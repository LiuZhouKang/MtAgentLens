// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! The filters the `record` subcommand installs.
//!
//! These are in the library, not in `main.rs`, so tests can construct the real
//! filter objects and assert what they do to a real event shape. Two entries
//! were removed in P1 because they silently deleted the response chain:
//!
//!   * `response.body=` — an empty matcher; `"".contains("")` is always true,
//!     so every parsed response was dropped.
//!   * `data.type=binary` — a zstd frame is full of control bytes, so the
//!     entire compressed response was classified as binary and dropped before
//!     `HTTPParser`/`SSEProcessor` could see it.
//!
//! Both facts are pinned by tests below.

/// HTTP-side filters for `record`.
///
/// Deliberately excludes any empty matcher. The remaining rules suppress
/// telemetry noise (`/v1/rgstr`, 202, HEAD) without touching agent responses.
pub fn record_http_filter_patterns() -> Vec<String> {
    vec![
        "request.path_prefix=/v1/rgstr | response.status_code=202 | request.method=HEAD"
            .to_string(),
    ]
}

/// SSL-side filters for `record`.
///
/// Keeps the chunk-terminator de-duplication rule (`0\r\n\r\n` is a standalone
/// event) and nothing else. In particular there is no pre-decode binary rule:
/// dropping bytes before they are decoded is what P1 fixes.
pub fn record_ssl_filter_patterns() -> Vec<String> {
    vec![r"data=0\r\n\r\n".to_string()]
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::framework::analyzers::http_filter::FilterExpression as HttpExpression;
    use crate::framework::analyzers::ssl_filter::FilterExpression as SslExpression;
    use serde_json::json;

    fn ssl_data_with_control_bytes() -> String {
        [0u8, 4, 1, 0x28, 0x04, 0x00]
            .iter()
            .map(|b| *b as char)
            .collect()
    }

    #[test]
    fn record_http_presets_do_not_drop_a_parsed_response() {
        let response = json!({
            "message_type": "response",
            "status_code": 200,
            "body": "data: {\"type\":\"content_block_delta\"}\n\n",
        });
        for pattern in record_http_filter_patterns() {
            assert!(
                !HttpExpression::parse(&pattern).evaluate(&response),
                "record preset still drops a parsed response: {pattern}"
            );
        }
    }

    #[test]
    fn record_http_presets_keep_request_filters() {
        let telemetry = json!({
            "message_type": "request",
            "method": "POST",
            "path": "/v1/rgstr?token=x",
        });
        let matched = record_http_filter_patterns()
            .iter()
            .any(|pattern| HttpExpression::parse(pattern).evaluate(&telemetry));
        assert!(matched, "telemetry request filter no longer matches");
    }

    #[test]
    fn record_ssl_presets_keep_a_zstd_response_event() {
        let response = json!({
            "function": "READ/RECV",
            "data": ssl_data_with_control_bytes(),
        });
        for pattern in record_ssl_filter_patterns() {
            assert!(
                !SslExpression::parse(&pattern).evaluate(&response),
                "record preset drops a compressed response: {pattern}"
            );
        }
    }

    #[test]
    fn record_ssl_presets_still_match_the_chunk_terminator() {
        let terminator = json!({"function": "READ/RECV", "data": "0\r\n\r\n"});
        let matched = record_ssl_filter_patterns()
            .iter()
            .any(|pattern| SslExpression::parse(pattern).evaluate(&terminator));
        assert!(matched, "chunk terminator de-duplication rule was lost");
    }

    #[test]
    fn record_presets_contain_no_empty_matcher() {
        for pattern in record_http_filter_patterns()
            .into_iter()
            .chain(record_ssl_filter_patterns())
        {
            assert!(
                !pattern.trim_end().ends_with('='),
                "empty matcher in preset: {pattern}"
            );
        }
    }
}
