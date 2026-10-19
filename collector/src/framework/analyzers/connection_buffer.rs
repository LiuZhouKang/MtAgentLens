// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// connection_buffer.rs — Per-connection SSL_write reassembly.
//
// Problem: SSL_write data is captured in per-call eBPF uprobe events.
// Large request bodies (e.g., Anthropic API POST with system prompts +
// CLAUDE.md) span multiple SSL_write calls, producing fragments.
//
// Solution: Accumulate SSL_write data per TLS session (tid). When an
// SSL_read event arrives for that session, emit the assembled body as
// a synthetic SSL_write event, then pass the original read through.
// Uses an internal queue so both events reach the downstream pipeline.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use crate::framework::Event;
use crate::framework::analyzers::Analyzer;
use crate::framework::analyzers::AnalyzerError;
use crate::framework::runners::EventStream;
use futures::StreamExt;
use serde_json::json;

/// Identifies one TLS connection for reassembly purposes.
///
/// `tid` alone is NOT sufficient: Node runs a single-threaded event loop, so
/// one tid multiplexes many concurrent sockets. Accumulating by tid therefore
/// concatenates *different* requests' bodies (verified: 6 concurrent OpenClaw
/// calls produced 17 synthesized requests of which only 4 parsed as JSON).
///
/// `ssl_ptr` is the `SSL*` object captured by the tls_capture uprobe — stable
/// for the life of a connection and distinct across concurrent connections.
/// Older captures lack the field, so we fall back to tid to preserve the
/// previous behaviour exactly.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum ConnKey {
    SslPtr(u64),
    Tid(u64),
}

fn conn_key(data: &serde_json::Value, tid: u64) -> ConnKey {
    match data.get("ssl_ptr").and_then(|v| v.as_u64()) {
        Some(ptr) if ptr != 0 => ConnKey::SslPtr(ptr),
        _ => ConnKey::Tid(tid),
    }
}

/// Per-connection reassembly state.
///
/// `awaiting_request` is the crucial bit. Response body fragments (e.g. SSE
/// `data:` chunks, chunked-size lines) do NOT start with `HTTP/`, so a naive
/// "anything not starting with HTTP/ is a request fragment" rule folds the
/// tail of one response into the next request. Once a response begins we stop
/// accumulating until the next request line appears.
#[derive(Debug, Default)]
struct ConnBuf {
    data: Vec<u8>,
    awaiting_request: bool,
}

/// A single SSL_write larger than this is passed through instead of being
/// accumulated, bounding per-connection memory (matches the historical 64KB
/// threshold).
const MAX_ASSEMBLY_BYTES: usize = 64 * 1024;

/// True when the payload begins an HTTP request line, e.g. `POST /x HTTP/1.1`.
fn is_request_start(raw: &str) -> bool {
    const METHODS: [&str; 7] = [
        "GET ", "POST ", "PUT ", "DELETE ", "HEAD ", "OPTIONS ", "PATCH ",
    ];
    METHODS.iter().any(|m| raw.starts_with(m)) && raw.contains(" HTTP/1.")
}

/// True when the payload begins an HTTP response status line.
fn is_response_start(raw: &str) -> bool {
    raw.starts_with("HTTP/")
}

type SessionBuffers = Arc<Mutex<HashMap<ConnKey, ConnBuf>>>;

pub struct ConnectionBuffer {
    buffers: SessionBuffers,
    /// Queued events waiting to be emitted (avoids dropping SSL_read).
    pending: Arc<Mutex<Vec<Event>>>,
}

impl Default for ConnectionBuffer {
    fn default() -> Self {
        Self::new()
    }
}

impl ConnectionBuffer {
    pub fn new() -> Self {
        Self {
            buffers: Arc::new(Mutex::new(HashMap::new())),
            pending: Arc::new(Mutex::new(Vec::new())),
        }
    }
}

#[async_trait::async_trait]
impl Analyzer for ConnectionBuffer {
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError> {
        let buffers = Arc::clone(&self.buffers);
        let pending = Arc::clone(&self.pending);

        let processed = stream.filter_map(move |event| {
            let buffers = Arc::clone(&buffers);
            let pending = Arc::clone(&pending);
            async move {
                // Drain any queued events first
                {
                    let mut pq = pending.lock().unwrap();
                    if !pq.is_empty() {
                        let next = pq.remove(0);
                        pq.push(event);
                        return Some(next);
                    }
                }

                // Only process raw SSL events
                if event.source != "ssl" {
                    return Some(event);
                }

                // Response fragments held by PayloadNormalizer are not requests;
                // treating them as continued request bodies corrupts the state.
                if event.data.get("partial").and_then(|value| value.as_bool()) == Some(true) {
                    return Some(event);
                }

                let data = &event.data;
                let tid = match data.get("tid").and_then(|v| v.as_u64()) {
                    Some(t) => t,
                    None => return Some(event),
                };

                let raw_data = match data.get("data").and_then(|v| v.as_str()) {
                    Some(s) => s,
                    None => return Some(event),
                };
                if raw_data.is_empty() {
                    return Some(event);
                }

                // Direction is inferred from content: the tls_capture uprobe
                // does not populate a direction field. Key by connection, not
                // by tid — one tid multiplexes many concurrent sockets.
                let key = conn_key(data, tid);

                if is_response_start(raw_data) {
                    // A response starts here. Emit whatever request we
                    // accumulated, then ignore everything until the next
                    // request line (response bodies are not our concern).
                    let assembled = {
                        let mut bufs = buffers.lock().unwrap();
                        let entry = bufs.entry(key).or_default();
                        entry.awaiting_request = true;
                        std::mem::take(&mut entry.data)
                    };

                    if !assembled.is_empty() {
                        let body = String::from_utf8(assembled).unwrap_or_default();
                        if !body.trim().is_empty() {
                            // Emit the assembled request as a synthetic
                            // SSL event, then queue the original read
                            // event for the next iteration.
                            let mut synth_data = event.data.clone();
                            synth_data["data"] = json!(body);
                            // The rewritten body invalidates the transport
                            // bytes; leaving them behind would let a later
                            // reader pair `data_b64` with a different `data`.
                            if let Some(object) = synth_data.as_object_mut() {
                                object.remove("data_b64");
                                object.remove("data_b64_source");
                            }
                            let synth = Event::new_with_timestamp(
                                event.timestamp,
                                "ssl".to_string(),
                                event.pid,
                                event.comm.clone(),
                                synth_data,
                            );
                            pending.lock().unwrap().push(event); // original read follows
                            return Some(synth);
                        }
                    }

                    Some(event) // pass-through read
                } else if is_request_start(raw_data) {
                    // A new request begins. Start a fresh accumulation; any
                    // leftover data belongs to a request whose response never
                    // arrived (connection died mid-flight).
                    let mut bufs = buffers.lock().unwrap();
                    let entry = bufs.entry(key).or_default();
                    entry.awaiting_request = false;
                    entry.data.clear();
                    if raw_data.len() < MAX_ASSEMBLY_BYTES {
                        entry.data.extend_from_slice(raw_data.as_bytes());
                        None // emitted once its response arrives
                    } else {
                        // Very large single write — pass through directly
                        Some(event)
                    }
                } else {
                    // Continuation fragment. Only a request body qualifies:
                    // everything between a response and the next request is
                    // response payload and must stay untouched.
                    let mut bufs = buffers.lock().unwrap();
                    let entry = bufs.entry(key).or_default();
                    if entry.awaiting_request || raw_data.len() >= MAX_ASSEMBLY_BYTES {
                        Some(event) // response body — pass through unmodified
                    } else {
                        entry.data.extend_from_slice(raw_data.as_bytes());
                        None
                    }
                }
            }
        });

        Ok(Box::pin(processed))
    }

    fn name(&self) -> &str {
        "ConnectionBuffer"
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures::stream;

    fn ssl_event(tid: u64, ssl_ptr: u64, data: &str) -> Event {
        Event::new_with_timestamp(
            1,
            "ssl".to_string(),
            42,
            "node".to_string(),
            json!({ "tid": tid, "ssl_ptr": ssl_ptr, "data": data }),
        )
    }

    /// Two connections multiplexed onto ONE tid (Node's event loop) must not
    /// have their request bodies concatenated. Before the fix, both fragments
    /// accumulated into the same `tid` bucket.
    #[tokio::test]
    async fn concurrent_connections_do_not_mix() {
        let mut cb = ConnectionBuffer::new();
        let events = vec![
            ssl_event(1, 0xAAA, "POST /a HTTP/1.1\r\nHost: x\r\n\r\n{\"a\""),
            ssl_event(1, 0xBBB, "POST /b HTTP/1.1\r\nHost: y\r\n\r\n{\"b\""),
            ssl_event(1, 0xAAA, "HTTP/1.1 200 OK\r\n\r\n"),
        ];
        let out: Vec<Event> = cb
            .process(Box::pin(stream::iter(events)))
            .await
            .unwrap()
            .collect()
            .await;

        let emitted: Vec<String> = out
            .iter()
            .filter_map(|e| {
                e.data
                    .get("data")
                    .and_then(|v| v.as_str())
                    .map(String::from)
            })
            .filter(|d| d.contains("POST"))
            .collect();

        assert_eq!(emitted.len(), 1, "exactly one synthesized request expected");
        assert!(emitted[0].contains("/a"), "must be connection A's request");
        assert!(!emitted[0].contains("/b"), "connection B must not leak in");
    }

    /// Response body fragments (SSE `data:` chunks, chunked-size lines) do
    /// NOT start with `HTTP/`. Before the state machine, they were folded
    /// into the next request's body — this is what real OpenClaw captures
    /// showed (only 2 of 6 synthesized requests parsed as JSON).
    #[tokio::test]
    async fn response_body_fragments_do_not_pollute_next_request() {
        let mut cb = ConnectionBuffer::new();
        let events = vec![
            ssl_event(1, 0xAAA, "POST /one HTTP/1.1\r\nHost: x\r\n\r\n{\"n\":1}"),
            ssl_event(1, 0xAAA, "HTTP/1.1 200 OK\r\n\r\n"),
            ssl_event(1, 0xAAA, "data: {\"chunk\":1}\r\n\r\n"),
            ssl_event(1, 0xAAA, "14a\r\n\r\ndata: {\"chunk\":2}\r\n\r\n"),
            ssl_event(1, 0xAAA, "POST /two HTTP/1.1\r\nHost: x\r\n\r\n{\"n\":2}"),
            ssl_event(1, 0xAAA, "HTTP/1.1 200 OK\r\n\r\n"),
        ];
        let out: Vec<Event> = cb
            .process(Box::pin(stream::iter(events)))
            .await
            .unwrap()
            .collect()
            .await;

        let emitted: Vec<String> = out
            .iter()
            .filter_map(|e| {
                e.data
                    .get("data")
                    .and_then(|v| v.as_str())
                    .map(String::from)
            })
            .filter(|d| is_request_start(d))
            .collect();

        assert_eq!(emitted.len(), 2, "two requests should be synthesized");
        assert!(emitted[0].contains("/one"));
        assert!(
            !emitted[0].contains("chunk"),
            "response body must not leak into request 1"
        );
        assert!(emitted[1].contains("/two"));
        assert!(
            !emitted[1].contains("chunk"),
            "response body must not leak into request 2"
        );
    }

    /// Without ssl_ptr (e.g. older captures), fall back to tid so behaviour
    /// is byte-for-byte identical to before.
    #[tokio::test]
    async fn falls_back_to_tid_when_ssl_ptr_missing() {
        let mut cb = ConnectionBuffer::new();
        let ev = |data: &str| {
            Event::new_with_timestamp(
                1,
                "ssl".to_string(),
                42,
                "node".to_string(),
                json!({ "tid": 7, "data": data }),
            )
        };
        let events = vec![
            ev("POST /c HTTP/1.1\r\nHost: z\r\n\r\n{\"c\""),
            ev("HTTP/1.1 200 OK\r\n\r\n"),
        ];
        let out: Vec<Event> = cb
            .process(Box::pin(stream::iter(events)))
            .await
            .unwrap()
            .collect()
            .await;
        let emitted: Vec<String> = out
            .iter()
            .filter_map(|e| {
                e.data
                    .get("data")
                    .and_then(|v| v.as_str())
                    .map(String::from)
            })
            .filter(|d| d.contains("POST"))
            .collect();
        assert_eq!(emitted.len(), 1);
        assert!(emitted[0].contains("/c"));
    }
}
