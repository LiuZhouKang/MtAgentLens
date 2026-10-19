// SPDX-License-Identifier: Apache-2.0 OR MIT
// Raw secret redaction for captured transport bytes.
//
// The byte-lossless contract (P1) keeps capture faithful: `data_b64` is what
// the process actually sent. That fidelity is why raw events contain
// `Authorization` / `Cookie` values verbatim. Exporting or logging them is a
// different question, and the answer is no.
//
// This analyzer runs downstream of the transport decoder and before any
// output. It redacts sensitive header *values* in the HTTP head contained in
// `data_b64`, leaves every other byte untouched, and records what it did in a
// `redaction` object. The original `buf_size` is preserved and the new length
// is reported separately, so a consumer can always tell that the bytes are no
// longer the captured ones.
//
// Known boundary: a header whose value is split across two TLS records is not
// redacted, because neither event contains a complete line. That is documented
// rather than guessed at — half-redacting a value would produce a corrupted
// credential that still leaks its visible prefix.

use crate::framework::analyzers::{Analyzer, AnalyzerError};
use crate::framework::core::Event;
use crate::framework::runners::EventStream;
use base64::Engine;
use futures::StreamExt;
use serde_json::{Value, json};

/// Header names whose values never leave the process.
const SENSITIVE_HEADERS: &[&str] = &[
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
];

#[derive(Debug, Default)]
pub struct RawSecretRedactor;

impl RawSecretRedactor {
    pub fn new() -> Self {
        Self
    }
}

#[async_trait::async_trait]
impl Analyzer for RawSecretRedactor {
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError> {
        Ok(Box::pin(stream.map(|mut event| {
            redact_event(&mut event);
            event
        })))
    }

    fn name(&self) -> &str {
        "raw_secret_redactor"
    }
}

/// Redact one event in place. Returns the header names redacted, if any.
pub fn redact_event(event: &mut Event) -> Option<Vec<String>> {
    if event.source != "ssl" {
        return None;
    }
    let encoded = event.data.get("data_b64")?.as_str()?.to_string();
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(encoded.as_bytes())
        .ok()?;
    let (redacted, headers) = redact_http_head(&bytes)?;
    let redacted_len = redacted.len();
    let encoded = base64::engine::general_purpose::STANDARD.encode(&redacted);

    let object = event.data.as_object_mut()?;
    object.insert("data_b64".to_string(), Value::String(encoded));
    object.insert(
        "redaction".to_string(),
        json!({
            "raw_headers": headers,
            "captured_buf_size": object.get("buf_size").cloned().unwrap_or(Value::Null),
            "redacted_buf_size": redacted_len,
            "note": "data_b64 holds redacted bytes; the capture itself was not modified",
        }),
    );
    // Redaction is not required to shrink the payload: replacing a short
    // folded continuation (` def`) with ` [REDACTED]` grows it. Only the
    // captured size and the resulting size are reported, never assumed.
    Some(headers)
}

/// Redact sensitive header values in an HTTP head.
///
/// Returns `None` when the bytes contain no complete sensitive header (or are
/// not an HTTP head at all), so callers can leave the event byte-identical.
pub fn redact_http_head(bytes: &[u8]) -> Option<(Vec<u8>, Vec<String>)> {
    if !looks_like_http_head(bytes) {
        return None;
    }

    let mut output = Vec::with_capacity(bytes.len());
    let mut redacted = Vec::new();
    let mut pos = 0usize;
    let mut in_head = true;
    // HTTP obs-fold: a header value may continue on following lines that start
    // with SP/HTAB. Redacting only the first line would leave the rest of a
    // credential in the continuation, so those lines are replaced too.
    let mut redacting_continuation = false;

    while pos < bytes.len() {
        let (line, next) = next_line(&bytes[pos..]);
        if !in_head {
            output.extend_from_slice(line);
            pos += next;
            continue;
        }
        // An empty line ends the head; everything after is body.
        let body = strip_line_ending(line);
        if body.is_empty() {
            in_head = false;
            redacting_continuation = false;
            output.extend_from_slice(line);
            pos += next;
            continue;
        }
        let is_continuation = body.first().is_some_and(|b| *b == b' ' || *b == b'\t');
        if redacting_continuation && is_continuation {
            let ending = &line[body.len()..];
            output.extend_from_slice(b" [REDACTED]");
            output.extend_from_slice(ending);
            pos += next;
            continue;
        }
        redacting_continuation = false;
        if let Some(name) = sensitive_header_name(body) {
            let ending = &line[body.len()..];
            output.extend_from_slice(name.as_bytes());
            output.extend_from_slice(b": [REDACTED]");
            output.extend_from_slice(ending);
            redacted.push(name);
            redacting_continuation = true;
        } else {
            output.extend_from_slice(line);
        }
        pos += next;
    }

    (!redacted.is_empty()).then_some((output, redacted))
}

fn looks_like_http_head(bytes: &[u8]) -> bool {
    // A request line (`GET / HTTP/1.1`) or a status line (`HTTP/1.1 200 OK`)
    // followed by at least one `Name: value` line, all inside the first
    // 8 KiB. Body-only TLS events are common and must stay untouched.
    let head = &bytes[..bytes.len().min(8192)];
    let has_start_line = head.starts_with(b"HTTP/")
        || head.split(|b| *b == b'\n').next().is_some_and(|line| {
            let line = strip_line_ending(line);
            line.ends_with(b" HTTP/1.1") || line.ends_with(b" HTTP/1.0") || line.contains(&b' ')
        });
    has_start_line && head.windows(2).any(|w| w == b": ")
}

/// Case-insensitive header match against [`SENSITIVE_HEADERS`].
fn sensitive_header_name(line: &[u8]) -> Option<String> {
    let colon = line.iter().position(|b| *b == b':')?;
    let name = std::str::from_utf8(&line[..colon]).ok()?.trim();
    if name.is_empty() {
        return None;
    }
    let lower = name.to_ascii_lowercase();
    SENSITIVE_HEADERS
        .iter()
        .any(|candidate| lower == *candidate)
        .then(|| name.to_string())
}

fn next_line(bytes: &[u8]) -> (&[u8], usize) {
    match bytes.iter().position(|b| *b == b'\n') {
        Some(index) => (&bytes[..=index], index + 1),
        None => (bytes, bytes.len()),
    }
}

fn strip_line_ending(line: &[u8]) -> &[u8] {
    let mut end = line.len();
    if end > 0 && line[end - 1] == b'\n' {
        end -= 1;
    }
    if end > 0 && line[end - 1] == b'\r' {
        end -= 1;
    }
    &line[..end]
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::framework::core::Event;
    use base64::Engine;
    use serde_json::json;

    fn ssl_event(bytes: &[u8]) -> Event {
        Event::new(
            "ssl".to_string(),
            42,
            "node".to_string(),
            json!({
                "data_b64": base64::engine::general_purpose::STANDARD.encode(bytes),
                "buf_size": bytes.len(),
            }),
        )
    }

    fn decoded(event: &Event) -> Vec<u8> {
        base64::engine::general_purpose::STANDARD
            .decode(event.data["data_b64"].as_str().unwrap())
            .unwrap()
    }

    const REQUEST: &[u8] = b"POST /v1/messages HTTP/1.1\r\nHost: api.anthropic.com\r\nAuthorization: Bearer sk-ant-secret-value\r\nCookie: session=abc123\r\nContent-Type: application/json\r\n\r\n{\"model\":\"x\"}";

    #[test]
    fn redacts_sensitive_header_values_and_keeps_everything_else() {
        let mut event = ssl_event(REQUEST);
        let headers = redact_event(&mut event).expect("must redact");

        assert_eq!(headers, vec!["Authorization", "Cookie"]);
        let bytes = decoded(&event);
        let text = String::from_utf8_lossy(&bytes);
        assert!(!text.contains("sk-ant-secret-value"));
        assert!(!text.contains("session=abc123"));
        assert!(text.contains("Host: api.anthropic.com"));
        assert!(text.contains("Content-Type: application/json"));
        // Body and framing untouched.
        assert!(text.ends_with("\r\n\r\n{\"model\":\"x\"}"));
        assert_eq!(
            event.data["redaction"]["raw_headers"],
            json!(["Authorization", "Cookie"])
        );
        assert_eq!(event.data["buf_size"], REQUEST.len());
        assert_eq!(event.data["redaction"]["redacted_buf_size"], bytes.len());
    }

    #[test]
    fn leaves_events_without_sensitive_headers_byte_identical() {
        let mut event = ssl_event(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n");
        let before = event.data.clone();
        assert!(redact_event(&mut event).is_none());
        assert_eq!(event.data, before);
    }

    #[test]
    fn ignores_non_http_and_invalid_payloads() {
        let mut binary = ssl_event(&[0x00, 0x01, 0x02, 0x03]);
        assert!(redact_event(&mut binary).is_none());

        let mut broken = Event::new(
            "ssl".to_string(),
            1,
            "node".to_string(),
            json!({"data_b64": "!!!not-base64!!!", "buf_size": 3}),
        );
        assert!(redact_event(&mut broken).is_none());
        assert_eq!(broken.data["data_b64"], "!!!not-base64!!!");
    }

    #[test]
    fn redaction_is_idempotent() {
        let mut event = ssl_event(REQUEST);
        redact_event(&mut event).unwrap();
        let once = event.data["data_b64"].clone();
        // A second pass finds `Authorization: [REDACTED]`, which is still a
        // sensitive header name, so it rewrites the same value.
        redact_event(&mut event);
        assert_eq!(event.data["data_b64"], once);
        assert_eq!(decoded(&event), decoded(&event));
    }

    #[test]
    fn folded_header_continuations_are_redacted_too() {
        let mut event = ssl_event(
            b"GET / HTTP/1.1\r\nAuthorization: Bearer abc\r\n def\r\n\tghi\r\nHost: x\r\n\r\n",
        );
        redact_event(&mut event).unwrap();
        let bytes = decoded(&event);
        let text = String::from_utf8_lossy(&bytes);
        assert!(!text.contains("abc"));
        assert!(!text.contains("def"));
        assert!(!text.contains("ghi"));
        assert_eq!(text.matches("[REDACTED]").count(), 3);
        assert!(text.contains("Host: x"));
    }

    #[test]
    fn set_cookie_is_redacted_in_responses() {
        let mut event = ssl_event(
            b"HTTP/1.1 200 OK\r\nSet-Cookie: sid=deadbeef; HttpOnly\r\nContent-Length: 2\r\n\r\nok",
        );
        let headers = redact_event(&mut event).unwrap();
        assert_eq!(headers, vec!["Set-Cookie"]);
        let bytes = decoded(&event);
        let text = String::from_utf8_lossy(&bytes);
        assert!(!text.contains("deadbeef"));
        assert!(text.contains("Set-Cookie: [REDACTED]"));
    }

    #[tokio::test]
    async fn analyzer_redacts_events_in_a_stream() {
        let mut analyzer = RawSecretRedactor::new();
        let stream: EventStream = Box::pin(futures::stream::iter(vec![
            ssl_event(REQUEST),
            ssl_event(b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n"),
        ]));
        let mut out = analyzer.process(stream).await.unwrap();
        let first = out.next().await.unwrap();
        let second = out.next().await.unwrap();
        assert!(first.data.get("redaction").is_some());
        assert!(second.data.get("redaction").is_none());
        assert!(out.next().await.is_none());
    }

    #[test]
    fn non_ssl_sources_are_untouched() {
        let mut event = Event::new(
            "process".to_string(),
            1,
            "rm".to_string(),
            json!({"event": "FILE_DELETE", "data_b64": "aGVsbG8="}),
        );
        let before = event.data.clone();
        assert!(redact_event(&mut event).is_none());
        assert_eq!(event.data, before);
    }
}
