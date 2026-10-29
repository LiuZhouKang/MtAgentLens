// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 eunomia-bpf org.

use crate::framework::core::Event;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;

/// Exact TLS-only fields observed on the original raw SSL/TLS event.
///
/// HTTP/SSE analyzers replace the wire `connection_id` with a display stream
/// id and drop `ssl_ptr`. Keeping only these bounded source fields lets routing
/// derive the same producer key without re-importing responsibility logic.
fn captured_tls_metadata(original_event: &Event) -> Option<Value> {
    if !(original_event.source.contains("ssl") || original_event.source.contains("tls")) {
        return None;
    }

    let data = &original_event.data;
    let mut captured = serde_json::Map::new();
    for field in ["connection_id", "tls_session"] {
        if let Some(value) = data.get(field).and_then(Value::as_str) {
            captured.insert(field.to_string(), Value::String(value.to_string()));
        }
    }
    if let Some(value) = data
        .get("ssl_ptr")
        .and_then(Value::as_u64)
        .filter(|p| *p != 0)
    {
        captured.insert("ssl_ptr".to_string(), Value::from(value));
    }
    if let Some(value) = data.get("pid").and_then(Value::as_u64).filter(|p| *p != 0) {
        captured.insert("pid".to_string(), Value::from(value));
    }
    if let Some(value) = data
        .get("start_time_ns")
        .and_then(Value::as_u64)
        .filter(|start| *start != 0)
    {
        captured.insert("start_time_ns".to_string(), Value::from(value));
    }

    (!captured.is_empty()).then_some(Value::Object(captured))
}

fn with_captured_tls(mut data: Value, original_event: &Event) -> Value {
    if let Some(captured) = captured_tls_metadata(original_event)
        && let Some(object) = data.as_object_mut()
    {
        object.insert("captured_tls".to_string(), captured);
    }
    data
}

/// SSE Processor Event - represents a complete SSE interaction with timing information
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SSEProcessorEvent {
    pub connection_id: String,
    pub message_id: Option<String>,
    pub start_time: u64,
    pub end_time: u64,
    pub duration_ns: u64,
    pub original_source: String,
    pub function: String,
    pub tid: u64,
    pub json_content: String,
    pub text_content: String,
    pub total_size: usize,
    pub event_count: usize,
    pub has_message_start: bool,
    pub sse_events: Vec<Value>,
}

impl SSEProcessorEvent {
    pub fn new(
        connection_id: String,
        message_id: Option<String>,
        start_time: u64,
        end_time: u64,
        original_source: String,
        function: String,
        tid: u64,
        json_content: String,
        text_content: String,
        total_size: usize,
        event_count: usize,
        has_message_start: bool,
        sse_events: Vec<Value>,
    ) -> Self {
        let duration_ns = end_time.saturating_sub(start_time);

        SSEProcessorEvent {
            connection_id,
            message_id,
            start_time,
            end_time,
            duration_ns,
            original_source,
            function,
            tid,
            json_content,
            text_content,
            total_size,
            event_count,
            has_message_start,
            sse_events,
        }
    }

    pub fn to_event(&self, original_event: &Event) -> Event {
        // Serialize struct to JSON Value to ensure exact match with struct fields
        let data = with_captured_tls(
            serde_json::to_value(self).unwrap_or_else(|_| serde_json::json!({})),
            original_event,
        );

        // Use merged end_time if events were merged, otherwise use original timestamp
        let timestamp = if self.event_count > 1 {
            self.end_time
        } else {
            original_event.timestamp
        };

        Event::new_with_timestamp(
            timestamp,
            "sse_processor".to_string(),
            original_event.pid,
            original_event.comm.clone(),
            data,
        )
    }
}

/// HTTP Event - represents a parsed HTTP request or response
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HTTPEvent {
    pub tid: u64,
    pub message_type: String,
    pub first_line: String,
    pub method: Option<String>,
    pub path: Option<String>,
    pub protocol: Option<String>,
    pub status_code: Option<u16>,
    pub status_text: Option<String>,
    pub headers: HashMap<String, String>,
    pub body: Option<String>,
    pub total_size: usize,
    pub has_body: bool,
    pub is_chunked: bool,
    pub content_length: Option<usize>,
    pub original_source: String,
    pub raw_data: Option<String>,
}

impl HTTPEvent {
    pub fn new(
        tid: u64,
        message_type: String,
        first_line: String,
        method: Option<String>,
        path: Option<String>,
        protocol: Option<String>,
        status_code: Option<u16>,
        status_text: Option<String>,
        headers: HashMap<String, String>,
        body: Option<String>,
        total_size: usize,
        has_body: bool,
        is_chunked: bool,
        content_length: Option<usize>,
        original_source: String,
    ) -> Self {
        HTTPEvent {
            tid,
            message_type,
            first_line,
            method,
            path,
            protocol,
            status_code,
            status_text,
            headers,
            body,
            total_size,
            has_body,
            is_chunked,
            content_length,
            original_source,
            raw_data: None,
        }
    }

    pub fn with_raw_data(mut self, raw_data: String) -> Self {
        self.raw_data = Some(raw_data);
        self
    }

    pub fn to_event(&self, original_event: &Event) -> Event {
        // Serialize struct to JSON Value to ensure exact match with struct fields
        let data = with_captured_tls(
            serde_json::to_value(self).unwrap_or_else(|_| serde_json::json!({})),
            original_event,
        );

        Event::new_with_timestamp(
            original_event.timestamp, // Use original event timestamp directly
            "http_parser".to_string(),
            original_event.pid,
            original_event.comm.clone(),
            data,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn raw_ssl(data: Value) -> Event {
        Event::new_with_timestamp(7, "ssl".into(), 42, "curl".into(), data)
    }

    fn http_event(body: &str) -> HTTPEvent {
        HTTPEvent::new(
            7,
            "request".into(),
            "POST /v1/messages HTTP/1.1".into(),
            Some("POST".into()),
            Some("/v1/messages".into()),
            Some("HTTP/1.1".into()),
            None,
            None,
            HashMap::new(),
            Some(body.into()),
            body.len(),
            true,
            false,
            None,
            "ssl".into(),
        )
    }

    fn sse_event(stream_id: &str) -> SSEProcessorEvent {
        SSEProcessorEvent::new(
            stream_id.into(),
            Some("msg-1".into()),
            7,
            8,
            "ssl".into(),
            "READ/RECV".into(),
            7,
            "{}".into(),
            "{}".into(),
            2,
            1,
            true,
            vec![],
        )
    }

    #[test]
    fn projections_preserve_only_the_raw_tls_source_fields() {
        let raw = raw_ssl(json!({
            "connection_id": "raw-conn",
            "tls_session": "raw-session",
            "ssl_ptr": 4096,
            "pid": 42,
            "start_time_ns": 99,
            "data": "GET /secret HTTP/1.1",
        }));
        let expected = json!({
            "connection_id": "raw-conn",
            "tls_session": "raw-session",
            "ssl_ptr": 4096,
            "pid": 42,
            "start_time_ns": 99,
        });

        let http = http_event("{}").to_event(&raw);
        assert_eq!(http.data["captured_tls"], expected);
        assert!(http.data["captured_tls"].get("data").is_none());
        assert_eq!(http.data["connection_id"], Value::Null);

        let sse = sse_event("42:7:display-stream").to_event(&raw);
        assert_eq!(sse.data["connection_id"], "42:7:display-stream");
        assert_eq!(sse.data["captured_tls"], expected);
        assert!(sse.data["captured_tls"].get("data").is_none());
    }

    #[test]
    fn non_tls_or_context_free_source_does_not_invent_captured_tls() {
        let process = Event::new_with_timestamp(
            7,
            "process".into(),
            42,
            "curl".into(),
            json!({"ssl_ptr": 4096}),
        );
        assert!(
            http_event("{}")
                .to_event(&process)
                .data
                .get("captured_tls")
                .is_none()
        );

        let bare_ssl = raw_ssl(json!({"data": "GET / HTTP/1.1"}));
        assert!(
            sse_event("42:7:stream")
                .to_event(&bare_ssl)
                .data
                .get("captured_tls")
                .is_none()
        );
    }
}
