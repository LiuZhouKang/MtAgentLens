// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Credential-shaped redaction for stdio (MCP) payloads.
//!
//! `stdiocap` copies the bytes a process read from or wrote to stdin/stdout.
//! For an MCP server that traffic is JSON-RPC, and tool arguments routinely
//! carry API keys, bearer tokens and passwords. Those bytes are captured
//! faithfully — that is the point of the probe — but they must not reach an
//! output surface verbatim.
//!
//! This analyzer sits downstream of `TimestampNormalizer` and before every
//! output (console, log file, SSE, persistence). It applies the shape-based
//! redactor from [`crate::export::genai::redact`] to the event's `data`
//! string and records the rewrite in a `redaction` object. The capture itself
//! is not modified: what remains on the event is the redacted text plus the
//! evidence that redaction happened.
//!
//! Known boundaries: redaction is shape-based and per event. A credential that
//! does not look like one (an opaque random string with no key prefix or
//! label, a key written with JSON unicode escapes) passes through; a
//! credential split across two read(2)/write(2) events is not reassembled, so
//! neither fragment matches. `docs/capabilities.json` records these residuals.

use crate::export::genai::redact;
use crate::framework::analyzers::{Analyzer, AnalyzerError};
use crate::framework::core::Event;
use crate::framework::runners::EventStream;
use futures::StreamExt;
use serde_json::{Value, json};

/// Source identifier of events produced by [`crate::framework::runners::StdioRunner`].
const SOURCE: &str = "stdio";

/// Redacts credential-shaped substrings in stdio payloads.
#[derive(Debug, Default)]
pub struct StdioSecretRedactor;

impl StdioSecretRedactor {
    pub fn new() -> Self {
        Self
    }
}

#[async_trait::async_trait]
impl Analyzer for StdioSecretRedactor {
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError> {
        Ok(Box::pin(stream.map(|mut event| {
            redact_event(&mut event);
            event
        })))
    }

    fn name(&self) -> &str {
        "stdio_secret_redactor"
    }
}

/// Redact one stdio event in place. Returns `true` when the payload changed.
///
/// Events from other sources, events without a string `data` payload (the
/// `STATS` line and zero-length reads), and payloads with no credential shape
/// are left byte-identical.
pub fn redact_event(event: &mut Event) -> bool {
    if event.source != SOURCE {
        return false;
    }

    let Some(payload) = event.data.get("data").and_then(Value::as_str) else {
        return false;
    };
    let redacted = redact(payload);
    if redacted == payload {
        return false;
    }

    let captured_bytes = payload.len();
    let redacted_bytes = redacted.len();
    let Some(object) = event.data.as_object_mut() else {
        return false;
    };
    object.insert("data".to_string(), Value::String(redacted));
    object.insert(
        "redaction".to_string(),
        json!({
            "kind": "credential_shape",
            "rule": "export::genai::redact",
            "captured_data_bytes": captured_bytes,
            "redacted_data_bytes": redacted_bytes,
            "note": "data holds redacted text; the capture itself was not modified",
        }),
    );
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn stdio_event(data: &str) -> Event {
        Event::new(
            SOURCE.to_string(),
            4242,
            "mcp-server".to_string(),
            json!({
                "direction": "READ",
                "timestamp_ns": 1_000,
                "pid": 4242,
                "fd": 0,
                "fd_role": "stdin",
                "len": data.len(),
                "buf_size": data.len(),
                "copy_failed": false,
                "data": data,
                "truncated": false,
            }),
        )
    }

    #[test]
    fn redacts_a_bearer_token_in_a_json_rpc_payload() {
        let secret = "sk-ant-api03-REAL-LEAK-abcdefghijklmnopqrstuvwxyz";
        let payload = format!(
            r#"{{"jsonrpc":"2.0","method":"tools/call","params":{{"headers":{{"Authorization":"Bearer {secret}"}}}}}}"#
        );
        let mut event = stdio_event(&payload);

        assert!(redact_event(&mut event));

        let data = event.data["data"].as_str().unwrap();
        assert!(!data.contains(secret), "the token must not survive");
        assert!(data.contains("[REDACTED]"));
        // Everything that is not credential-shaped is untouched.
        assert!(data.contains(r#""method":"tools/call""#));
        assert_eq!(event.data["redaction"]["kind"], json!("credential_shape"));
        assert_eq!(
            event.data["redaction"]["captured_data_bytes"],
            json!(payload.len())
        );
        assert_eq!(
            event.data["redaction"]["redacted_data_bytes"],
            json!(data.len())
        );
    }

    #[test]
    fn redacts_key_value_secrets_in_plain_text() {
        let mut event = stdio_event("connecting with api_key=abc123def456 and password=hunter2");
        assert!(redact_event(&mut event));
        let data = event.data["data"].as_str().unwrap();
        assert!(!data.contains("abc123def456"));
        assert!(!data.contains("hunter2"));
        assert!(data.contains("connecting with"));
    }

    /// The actual MCP wire shape: a labelled secret is a quoted JSON value.
    /// The plain-text rules never fired on this, so it used to pass through
    /// with no `redaction` marker at all.
    #[test]
    fn redacts_labelled_json_rpc_arguments() {
        let mut event = stdio_event(
            r#"{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"arguments":{"password":"hunter2","safe":"keep"}}}"#,
        );
        assert!(redact_event(&mut event));
        let data = event.data["data"].as_str().unwrap();
        assert!(
            !data.contains("hunter2"),
            "labelled JSON secret leaked: {data}"
        );
        assert!(data.contains("[REDACTED]"));
        assert!(data.contains("\"safe\":\"keep\""));
        // Still valid JSON.
        let parsed: serde_json::Value = serde_json::from_str(data).expect("json must stay valid");
        assert!(parsed.is_object());
    }

    #[test]
    fn leaves_clean_payloads_byte_identical() {
        let mut event = stdio_event(r#"{"jsonrpc":"2.0","id":1,"result":{"ok":true}}"#);
        let before = event.data.clone();
        assert!(!redact_event(&mut event));
        assert_eq!(event.data, before);
    }

    #[test]
    fn leaves_events_without_a_string_payload_untouched() {
        // STATS lines and zero-length reads carry `data: null`.
        let mut event = Event::new(
            SOURCE.to_string(),
            1,
            "stdiocap".to_string(),
            json!({"direction": "STATS", "data": null}),
        );
        let before = event.data.clone();
        assert!(!redact_event(&mut event));
        assert_eq!(event.data, before);
    }

    #[test]
    fn ignores_other_sources() {
        let mut event = Event::new(
            "process".to_string(),
            1,
            "claude".to_string(),
            json!({"data": "Authorization: Bearer sk-ant-secret"}),
        );
        let before = event.data.clone();
        assert!(!redact_event(&mut event));
        assert_eq!(event.data, before);
    }

    #[test]
    fn redaction_is_idempotent() {
        let mut event = stdio_event(r#"{"token":"sk-ant-api03-REAL-LEAK-abcdefghijklmnop"}"#);
        assert!(redact_event(&mut event));
        let once = event.data["data"].clone();
        assert!(!redact_event(&mut event));
        assert_eq!(event.data["data"], once);
    }

    #[tokio::test]
    async fn analyzer_redacts_only_dirty_events_in_a_stream() {
        let mut analyzer = StdioSecretRedactor::new();
        let stream: EventStream = Box::pin(futures::stream::iter(vec![
            stdio_event(r#"{"api_key":"sk-ant-api03-REAL-LEAK-abcdefghijklmnop"}"#),
            stdio_event(r#"{"jsonrpc":"2.0","id":1}"#),
        ]));
        let mut out = analyzer.process(stream).await.unwrap();
        let dirty = out.next().await.unwrap();
        let clean = out.next().await.unwrap();
        assert!(dirty.data.get("redaction").is_some());
        assert!(clean.data.get("redaction").is_none());
        assert!(out.next().await.is_none());
    }
}
