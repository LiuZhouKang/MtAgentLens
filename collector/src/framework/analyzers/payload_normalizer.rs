// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Reassemble complete HTTP responses and emit one decoded plaintext event.
//!
//! Why this must run before `SSLFilter`: the chunked terminator arrives as its
//! own `0\r\n\r\n` event, and the `record` SSL preset matches exactly that
//! event. An assembler placed after the filter never sees the terminator and
//! can never finish a chunked response.
//!
//! Raw fragments are yielded immediately but marked `partial=true`, so
//! `SSEProcessor`, `HTTPParser` and the responsibility routing skip them while
//! `FileLogger`/`OutputAnalyzer` still see every captured byte. Running the
//! message through the decoders exactly once avoids both double parsing and the
//! old "empty-body response plus compressed garbage" pair.

use std::collections::{HashMap, HashSet};

use async_trait::async_trait;
use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use futures::stream::StreamExt;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use super::{Analyzer, AnalyzerError};
use crate::framework::core::Event;
use crate::framework::normalize::{
    DecodeError, FrameKind, decode_message, framing_of, message_complete, payload_bytes,
    rebuild_plaintext, split_head,
};
use crate::framework::runners::EventStream;

const IDLE_TIMEOUT_NS: u64 = 30 * 1_000_000_000;
const MAX_CONNECTION_BYTES: usize = 64 * 1024 * 1024;
const MAX_TOTAL_BYTES: usize = 128 * 1024 * 1024;
const MAX_CONNECTIONS: usize = 64;

#[derive(Clone, Debug, Hash, PartialEq, Eq)]
struct ConnectionKey {
    pid: u32,
    ssl_ptr: u64,
    tid: u32,
}

struct Connection {
    buffer: Vec<u8>,
    pending: Vec<Event>,
    kind: Option<FrameKind>,
    truncated: bool,
    last_timestamp_ns: u64,
    pid: u32,
    tid: u32,
    ssl_ptr: u64,
    comm: String,
}

impl Connection {
    fn new(event: &Event, pid: u32, tid: u32, ssl_ptr: u64) -> Self {
        Self {
            buffer: Vec::new(),
            pending: Vec::new(),
            kind: None,
            truncated: false,
            last_timestamp_ns: event.timestamp,
            pid,
            tid,
            ssl_ptr,
            comm: event.comm.clone(),
        }
    }
}

/// Reassembles HTTPS responses and synthesizes one decoded event per message.
#[derive(Default)]
pub struct PayloadNormalizer {
    connections: HashMap<ConnectionKey, Connection>,
    poisoned: HashSet<ConnectionKey>,
    total_bytes: usize,
}

impl PayloadNormalizer {
    pub fn new() -> Self {
        Self::default()
    }

    /// Feed one event; returns everything to yield downstream.
    pub fn push_event(&mut self, event: Event) -> Vec<Event> {
        let mut out = self.expire_idle(event.timestamp);

        if event.source != "ssl" {
            out.push(event);
            return out;
        }

        let function = event
            .data
            .get("function")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let (key, pid, tid, ssl_ptr) = connection_key(&event);

        match function.as_str() {
            "WRITE/SEND" => {
                self.poisoned.remove(&key);
                if let Some(conn) = self.connections.remove(&key) {
                    self.total_bytes = self.total_bytes.saturating_sub(conn.buffer.len());
                    let diagnostic = diagnostic_for_conn(
                        &conn,
                        "incomplete",
                        "a new request started before this response completed",
                    );
                    out.extend(release(conn, diagnostic));
                }
                out.push(event);
                out
            }
            "READ/RECV" => self.push_read(event, key, pid, tid, ssl_ptr, out),
            _ => {
                out.push(event);
                out
            }
        }
    }

    /// Flush every unfinished connection (end of stream).
    pub fn flush_all(&mut self) -> Vec<Event> {
        let keys: Vec<_> = self.connections.keys().cloned().collect();
        let mut out = Vec::new();
        for key in keys {
            if let Some(conn) = self.connections.remove(&key) {
                self.total_bytes = self.total_bytes.saturating_sub(conn.buffer.len());
                let diagnostic =
                    diagnostic_for_conn(&conn, "incomplete", "stream ended mid-response");
                out.extend(release(conn, diagnostic));
            }
        }
        out
    }

    fn push_read(
        &mut self,
        event: Event,
        key: ConnectionKey,
        pid: u32,
        tid: u32,
        ssl_ptr: u64,
        mut out: Vec<Event>,
    ) -> Vec<Event> {
        if self.poisoned.contains(&key) {
            out.push(event);
            return out;
        }

        let payload = match payload_bytes(&event.data) {
            Ok(Some(payload)) => payload,
            Ok(None) => {
                out.push(event);
                return out;
            }
            Err(error) => {
                let diagnostic =
                    diagnostic_for_event(&event, "transport_error", &error.to_string());
                out.push(event);
                out.push(diagnostic);
                return out;
            }
        };

        if !self.connections.contains_key(&key)
            && self.connections.len() >= MAX_CONNECTIONS
            && let Some(oldest) = self
                .connections
                .iter()
                .min_by_key(|(_, connection)| connection.last_timestamp_ns)
                .map(|(key, _)| key.clone())
            && let Some(conn) = self.connections.remove(&oldest)
        {
            self.total_bytes = self.total_bytes.saturating_sub(conn.buffer.len());
            let diagnostic = diagnostic_for_conn(&conn, "dropped", "connection cap exceeded");
            out.extend(release(conn, diagnostic));
        }

        if payload.bytes.len() > MAX_CONNECTION_BYTES
            || self.total_bytes + payload.bytes.len() > MAX_TOTAL_BYTES
        {
            self.poisoned.insert(key);
            let diagnostic = diagnostic_for_event(&event, "dropped", "buffer cap exceeded");
            out.push(event);
            out.push(diagnostic);
            return out;
        }

        self.total_bytes += payload.bytes.len();
        let conn = self
            .connections
            .entry(key.clone())
            .or_insert_with(|| Connection::new(&event, pid, tid, ssl_ptr));
        conn.last_timestamp_ns = event.timestamp;
        conn.truncated |= payload.truncated;
        conn.buffer.extend_from_slice(&payload.bytes);
        conn.pending.push(event);

        self.advance(key, out)
    }

    fn advance(&mut self, key: ConnectionKey, mut out: Vec<Event>) -> Vec<Event> {
        let mut conn = match self.connections.remove(&key) {
            Some(conn) => conn,
            None => return out,
        };
        self.total_bytes = self.total_bytes.saturating_sub(conn.buffer.len());

        loop {
            if conn.kind.is_none() {
                match split_head(&conn.buffer) {
                    Err(DecodeError::IncompleteHead) => {
                        self.restore(key, conn);
                        return out;
                    }
                    Err(error) => {
                        self.poisoned.insert(key.clone());
                        let diagnostic =
                            diagnostic_for_conn(&conn, "malformed_head", &error.to_string());
                        out.extend(release(conn, diagnostic));
                        return out;
                    }
                    Ok(head) => match framing_of(&head) {
                        Ok(FrameKind::Unframed) => {
                            // No framing: completion is unknowable. Pass the raw
                            // events through exactly as before.
                            out.append(&mut conn.pending);
                            return out;
                        }
                        Ok(kind) => {
                            conn.kind = Some(kind);
                        }
                        Err(error) => {
                            self.poisoned.insert(key.clone());
                            let diagnostic =
                                diagnostic_for_conn(&conn, "malformed_head", &error.to_string());
                            out.extend(release(conn, diagnostic));
                            return out;
                        }
                    },
                }
            }

            // Framing is known from here on: every buffered raw fragment can be
            // released downstream, marked partial so the parsers skip it. This
            // must run on every push, not only when the head first completes.
            for mut pending in conn.pending.drain(..) {
                mark_partial(&mut pending);
                out.push(pending);
            }

            let kind = match conn.kind {
                Some(kind) => kind,
                None => continue,
            };
            match message_complete(&conn.buffer, kind) {
                Ok(None) => {
                    self.restore(key, conn);
                    return out;
                }
                Err(error) => {
                    self.poisoned.insert(key.clone());
                    let diagnostic = diagnostic_for_conn(&conn, "malformed", &error.to_string());
                    out.push(diagnostic);
                    return out;
                }
                Ok(Some(end)) => {
                    let raw = conn.buffer[..end].to_vec();
                    match decode_message(&raw) {
                        Ok(decoded) => {
                            let plaintext = rebuild_plaintext(&decoded);
                            let status = if conn.truncated { "lossy" } else { "ok" };
                            out.push(synthetic_event(
                                &conn, kind, &plaintext, &decoded, status, &raw,
                            ));
                            conn.buffer.drain(..end);
                            conn.truncated = false;
                            conn.kind = None;
                            if conn.buffer.is_empty() {
                                return out;
                            }
                            // Pipelined response: decide the next message's
                            // framing from its own head.
                            match split_head(&conn.buffer) {
                                Ok(head) => match framing_of(&head) {
                                    Ok(FrameKind::Unframed) => {
                                        self.poisoned.insert(key.clone());
                                        let diagnostic = diagnostic_for_conn(
                                            &conn,
                                            "unframed_pipeline",
                                            "trailing bytes have no framing",
                                        );
                                        out.push(diagnostic);
                                        return out;
                                    }
                                    Ok(kind) => conn.kind = Some(kind),
                                    Err(error) => {
                                        self.poisoned.insert(key.clone());
                                        let diagnostic = diagnostic_for_conn(
                                            &conn,
                                            "malformed_head",
                                            &error.to_string(),
                                        );
                                        out.push(diagnostic);
                                        return out;
                                    }
                                },
                                Err(DecodeError::IncompleteHead) => {
                                    self.restore(key, conn);
                                    return out;
                                }
                                Err(error) => {
                                    self.poisoned.insert(key.clone());
                                    let diagnostic = diagnostic_for_conn(
                                        &conn,
                                        "malformed_head",
                                        &error.to_string(),
                                    );
                                    out.push(diagnostic);
                                    return out;
                                }
                            }
                        }
                        Err(error) => {
                            self.poisoned.insert(key.clone());
                            let diagnostic =
                                diagnostic_for_conn(&conn, "malformed", &error.to_string());
                            out.push(diagnostic);
                            return out;
                        }
                    }
                }
            }
        }
    }

    fn expire_idle(&mut self, now_ns: u64) -> Vec<Event> {
        let expired: Vec<_> = self
            .connections
            .iter()
            .filter(|(_, connection)| {
                now_ns.saturating_sub(connection.last_timestamp_ns) > IDLE_TIMEOUT_NS
            })
            .map(|(key, _)| key.clone())
            .collect();
        let mut out = Vec::new();
        for key in expired {
            if let Some(conn) = self.connections.remove(&key) {
                self.total_bytes = self.total_bytes.saturating_sub(conn.buffer.len());
                let diagnostic = diagnostic_for_conn(&conn, "incomplete", "idle timeout");
                out.extend(release(conn, diagnostic));
            }
        }
        out
    }

    fn restore(&mut self, key: ConnectionKey, conn: Connection) {
        self.total_bytes += conn.buffer.len();
        self.connections.insert(key, conn);
    }
}

#[async_trait]
impl Analyzer for PayloadNormalizer {
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError> {
        let mut state = std::mem::take(self);
        let output = async_stream::stream! {
            let mut input = stream;
            while let Some(event) = input.next().await {
                for event in state.push_event(event) {
                    yield event;
                }
            }
            for event in state.flush_all() {
                yield event;
            }
        };
        Ok(Box::pin(output))
    }

    fn name(&self) -> &str {
        "payload_normalizer"
    }
}

fn connection_key(event: &Event) -> (ConnectionKey, u32, u32, u64) {
    let pid = event
        .data
        .get("pid")
        .and_then(Value::as_u64)
        .unwrap_or(event.pid as u64) as u32;
    let tid = event.data.get("tid").and_then(Value::as_u64).unwrap_or(0) as u32;
    let ssl_ptr = event
        .data
        .get("ssl_ptr")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let key = if ssl_ptr != 0 {
        ConnectionKey {
            pid,
            ssl_ptr,
            tid: 0,
        }
    } else {
        ConnectionKey {
            pid,
            ssl_ptr: 0,
            tid,
        }
    };
    (key, pid, tid, ssl_ptr)
}

/// Fingerprint the raw wire bytes instead of duplicating them.
///
/// The raw fragments already carry `data_b64`; copying the compressed message
/// into every synthetic/diagnostic event tripled the log volume (measured 3.1x).
/// A hash plus length keeps forensic linkage without a second copy.
fn source_fingerprint(bytes: &[u8]) -> (String, usize) {
    (hex::encode(Sha256::digest(bytes)), bytes.len())
}

fn mark_partial(event: &mut Event) {
    if let Some(object) = event.data.as_object_mut() {
        object.insert("partial".to_string(), Value::Bool(true));
    }
}

fn release(mut conn: Connection, diagnostic: Event) -> Vec<Event> {
    let mut out = Vec::with_capacity(conn.pending.len() + 1);
    for mut event in conn.pending.drain(..) {
        mark_partial(&mut event);
        out.push(event);
    }
    out.push(diagnostic);
    out
}

fn diagnostic_for_conn(conn: &Connection, status: &str, message: &str) -> Event {
    Event::new_with_timestamp(
        conn.last_timestamp_ns,
        "ssl".to_string(),
        conn.pid,
        conn.comm.clone(),
        {
            let (source_sha256, source_len) = source_fingerprint(&conn.buffer);
            json!({
                "function": "READ/RECV",
                "pid": conn.pid,
                "tid": conn.tid,
                "ssl_ptr": conn.ssl_ptr,
                "decode_status": status,
                "decode_error": message,
                "source_sha256": source_sha256,
                "source_len": source_len,
                "normalized_from": "payload_normalizer",
            })
        },
    )
}

fn diagnostic_for_event(event: &Event, status: &str, message: &str) -> Event {
    Event::new_with_timestamp(
        event.timestamp,
        "ssl".to_string(),
        event.pid,
        event.comm.clone(),
        json!({
            "function": "READ/RECV",
            "pid": event.pid,
            "tid": event.data.get("tid").cloned().unwrap_or(Value::from(0)),
            "ssl_ptr": event.data.get("ssl_ptr").cloned().unwrap_or(Value::from(0)),
            "decode_status": status,
            "decode_error": message,
            "normalized_from": "payload_normalizer",
        }),
    )
}

fn synthetic_event(
    conn: &Connection,
    kind: FrameKind,
    plaintext: &[u8],
    decoded: &crate::framework::normalize::DecodedMessage,
    status: &str,
    raw: &[u8],
) -> Event {
    let framing = match kind {
        FrameKind::Chunked => "chunked",
        FrameKind::ContentLength(_) => "content-length",
        FrameKind::Unframed => "unframed",
    };
    let (source_sha256, source_len) = source_fingerprint(raw);
    let mut data = json!({
        "function": "READ/RECV",
        "pid": conn.pid,
        "tid": conn.tid,
        "ssl_ptr": conn.ssl_ptr,
        "buf_size": plaintext.len(),
        "len": plaintext.len(),
        "truncated": status != "ok",
        "decode_status": status,
        "codings": decoded.codings.clone(),
        "framing": framing,
        "source_sha256": source_sha256,
        "source_len": source_len,
        "data_b64": BASE64.encode(plaintext),
        "normalized_from": "payload_normalizer",
    });
    match std::str::from_utf8(plaintext) {
        Ok(text) => {
            data["data"] = Value::String(text.to_string());
        }
        Err(_) => {
            // HTTPParser and SSEProcessor require a `data` string. Without one
            // they pass the event through, so the bytes remain visible via
            // data_b64 instead of being replaced by U+FFFD.
            data["decode_status"] = Value::String("binary_non_utf8".to_string());
            data["truncated"] = Value::Bool(true);
        }
    }
    Event::new_with_timestamp(
        conn.last_timestamp_ns,
        "ssl".to_string(),
        conn.pid,
        conn.comm.clone(),
        data,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::fs;
    use std::path::PathBuf;

    fn fixture(name: &str) -> Vec<u8> {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/wire/openclaw-deepseek-zstd-chunked")
            .join(name);
        fs::read(path).expect("fixture must exist")
    }

    fn read_event(bytes: &[u8], timestamp: u64) -> Event {
        let encoded = BASE64.encode(bytes);
        Event::new_with_timestamp(
            timestamp,
            "ssl".to_string(),
            1488,
            "MainThread".to_string(),
            json!({
                "function": "READ/RECV",
                "pid": 1488,
                "tid": 1488,
                "ssl_ptr": 77,
                "buf_size": bytes.len(),
                "len": bytes.len(),
                "data_b64": encoded,
                "truncated": false,
            }),
        )
    }

    fn write_event(timestamp: u64) -> Event {
        Event::new_with_timestamp(
            timestamp,
            "ssl".to_string(),
            1488,
            "MainThread".to_string(),
            json!({"function": "WRITE/SEND", "pid": 1488, "tid": 1488, "ssl_ptr": 77}),
        )
    }

    #[test]
    fn reassembles_the_fixture_at_every_split_point() {
        let raw = fixture("response.http");
        let golden = fixture("golden.sse");
        for split in (1..raw.len()).step_by(97) {
            let mut normalizer = PayloadNormalizer::new();
            let mut out = Vec::new();
            out.extend(normalizer.push_event(read_event(&raw[..split], 1)));
            out.extend(normalizer.push_event(read_event(&raw[split..], 2)));
            out.extend(normalizer.flush_all());
            let synthesized: Vec<_> = out
                .iter()
                .filter(|event| {
                    event.data.get("decode_status").and_then(Value::as_str) == Some("ok")
                })
                .collect();
            assert_eq!(synthesized.len(), 1, "split at {split}");
            assert_eq!(
                synthesized[0].data["source_sha256"].as_str().map(str::len),
                Some(64),
                "split at {split}: source fingerprint missing"
            );
            assert_eq!(synthesized[0].data["source_len"], raw.len());
            let text = synthesized[0].data["data"].as_str().unwrap();
            let body = &text.as_bytes()[text.find("\r\n\r\n").unwrap() + 4..];
            assert_eq!(body, golden.as_slice(), "split at {split}");
        }
    }

    #[test]
    fn raw_fragments_are_marked_partial_once_framing_is_known() {
        let raw = fixture("response.http");
        let mut normalizer = PayloadNormalizer::new();
        // The head is 577 bytes; enough to decide framing and release the
        // buffered raw event, but not enough to complete the body.
        let out = normalizer.push_event(read_event(&raw[..700], 1));
        assert!(!out.is_empty());
        for event in &out {
            assert_eq!(
                event.data.get("partial").and_then(Value::as_bool),
                Some(true),
                "buffered raw fragment must be marked partial"
            );
        }
        let _ = normalizer.flush_all();
    }

    #[test]
    fn unframed_response_is_not_synthesized() {
        let message = b"HTTP/1.1 200 OK\r\n\r\nbody";
        let mut normalizer = PayloadNormalizer::new();
        let out = normalizer.push_event(read_event(message, 1));
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].data.get("decode_status"), None);
        assert_eq!(out[0].data.get("partial"), None);
    }

    #[test]
    fn malformed_message_emits_one_diagnostic() {
        let message = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n";
        let mut normalizer = PayloadNormalizer::new();
        let mut out = normalizer.push_event(read_event(message, 1));
        out.extend(normalizer.flush_all());
        let diagnostics: Vec<_> = out
            .iter()
            .filter(|event| {
                event
                    .data
                    .get("decode_status")
                    .and_then(Value::as_str)
                    .is_some_and(|status| status != "ok")
            })
            .collect();
        assert_eq!(diagnostics.len(), 1);
        assert_eq!(diagnostics[0].data["decode_status"], "malformed");
    }

    #[test]
    fn a_new_request_flushes_the_previous_response_as_incomplete() {
        let raw = fixture("response.http");
        let mut normalizer = PayloadNormalizer::new();
        normalizer.push_event(read_event(&raw[..50], 1));
        let out = normalizer.push_event(write_event(2));
        assert!(out.iter().any(|event| {
            event.data.get("decode_status").and_then(Value::as_str) == Some("incomplete")
        }));
    }

    #[test]
    fn idle_connections_are_flushed_as_incomplete() {
        let raw = fixture("response.http");
        let mut normalizer = PayloadNormalizer::new();
        normalizer.push_event(read_event(&raw[..50], 1));
        let out = normalizer.expire_idle(1 + IDLE_TIMEOUT_NS + 1);
        assert!(out.iter().any(|event| {
            event.data.get("decode_status").and_then(Value::as_str) == Some("incomplete")
        }));
        assert!(
            out.iter()
                .filter(|event| event.data.get("partial").is_some())
                .all(|event| event.data["partial"] == true)
        );
    }

    #[test]
    fn data_b64_only_events_reassemble() {
        let raw = fixture("response.http");
        let golden = fixture("golden.sse");
        let event = read_event(&raw, 1);
        assert!(event.data.get("data").is_none());
        let mut normalizer = PayloadNormalizer::new();
        let mut out = normalizer.push_event(event);
        out.extend(normalizer.flush_all());
        let synthesized = out
            .iter()
            .find(|event| event.data.get("decode_status").and_then(Value::as_str) == Some("ok"))
            .expect("must synthesize from data_b64 alone");
        let text = synthesized.data["data"].as_str().unwrap();
        let body = &text.as_bytes()[text.find("\r\n\r\n").unwrap() + 4..];
        assert_eq!(body, golden.as_slice());
    }
    #[test]
    fn malformed_stream_is_poisoned_until_the_next_request() {
        let bad = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n";
        let mut normalizer = PayloadNormalizer::new();
        let first = normalizer.push_event(read_event(bad, 1));
        assert!(first.iter().any(|event| {
            event.data.get("decode_status").and_then(Value::as_str) == Some("malformed")
        }));

        // Further reads on the same connection pass through; they must not
        // produce a diagnostic storm or buffer half a stream.
        let second = normalizer.push_event(read_event(b"more bytes", 2));
        assert_eq!(second.len(), 1);
        assert!(second[0].data.get("decode_status").is_none());

        // A new request clears the poison.
        normalizer.push_event(write_event(3));
        let third = normalizer.push_event(read_event(
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
            4,
        ));
        assert!(third.iter().any(|event| {
            event.data.get("decode_status").and_then(Value::as_str) == Some("ok")
        }));
    }
}
