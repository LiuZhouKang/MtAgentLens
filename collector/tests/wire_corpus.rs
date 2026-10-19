// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Acceptance tests over the frozen wire corpus.
//!
//! The corpus is what the SSL uprobe actually saw (see
//! `tests/fixtures/wire/MANIFEST.json` for provenance and SHA256). The golden
//! files were produced by the system `zstd` binary, not by this crate, so the
//! assertions below compare our decoder against an independent implementation.

use std::fs;
use std::path::PathBuf;

use mtagentlens::framework::normalize::{DecodeError, dechunk, decode_message};

const FIXTURES: [&str; 2] = [
    "openclaw-deepseek-zstd-chunked",
    "claude-code-deepseek-zstd-chunked",
];

fn fixture_path(relative: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/wire")
        .join(relative)
}

fn read_fixture(relative: &str) -> Vec<u8> {
    fs::read(fixture_path(relative))
        .unwrap_or_else(|error| panic!("cannot read fixture {relative}: {error}"))
}

/// Re-frame `payload` into HTTP chunks of the given sizes (last one gets the
/// remainder), terminated with `0\r\n\r\n`.
fn reframe(payload: &[u8], sizes: &[usize]) -> Vec<u8> {
    let mut out = Vec::with_capacity(payload.len() + sizes.len() * 8 + 5);
    let mut offset = 0;
    for &size in sizes {
        // A zero-size entry would look like the last-chunk; skip it so that a
        // split point at offset 0 simply produces one chunk.
        if size == 0 || offset >= payload.len() {
            continue;
        }
        let end = (offset + size).min(payload.len());
        let chunk = &payload[offset..end];
        out.extend_from_slice(format!("{:x}", chunk.len()).as_bytes());
        out.extend_from_slice(b"\r\n");
        out.extend_from_slice(chunk);
        out.extend_from_slice(b"\r\n");
        offset = end;
        if offset >= payload.len() {
            break;
        }
    }
    if offset < payload.len() {
        let chunk = &payload[offset..];
        out.extend_from_slice(format!("{:x}", chunk.len()).as_bytes());
        out.extend_from_slice(b"\r\n");
        out.extend_from_slice(chunk);
        out.extend_from_slice(b"\r\n");
    }
    out.extend_from_slice(b"0\r\n\r\n");
    out
}

fn response_payload(fixture: &str) -> Vec<u8> {
    let raw = read_fixture(&format!("{fixture}/response.http"));
    let decoded = decode_message(&raw).expect("fixture must decode");
    // Recover the compressed payload by de-chunking the body region.
    let head = mtagentlens::framework::normalize::split_head(&raw).unwrap();
    let dechunked = dechunk(&raw[head.body_offset..], head.body_offset).unwrap();
    assert_eq!(decoded.consumed, raw.len());
    dechunked.body
}

#[test]
fn golden_decode_matches_independent_zstd_for_every_fixture() {
    for fixture in FIXTURES {
        let raw = read_fixture(&format!("{fixture}/response.http"));
        let golden = read_fixture(&format!("{fixture}/golden.sse"));
        assert!(!golden.is_empty(), "{fixture}: golden must not be empty");
        let decoded = decode_message(&raw).expect("response must decode");
        assert_eq!(
            decoded.consumed,
            raw.len(),
            "{fixture}: decoder left unconsumed bytes"
        );
        assert_eq!(
            decoded.body, golden,
            "{fixture}: decoded bytes differ from system zstd"
        );
        assert_eq!(decoded.codings, vec!["zstd".to_string()]);
    }
}

#[test]
fn decode_is_deterministic() {
    for fixture in FIXTURES {
        let raw = read_fixture(&format!("{fixture}/response.http"));
        let first = decode_message(&raw).unwrap();
        let second = decode_message(&raw).unwrap();
        assert_eq!(
            first.body, second.body,
            "{fixture}: body is not deterministic"
        );
        assert_eq!(
            first.head.headers, second.head.headers,
            "{fixture}: headers are not deterministic"
        );
        assert_eq!(first.codings, second.codings);
        assert_eq!(first.consumed, second.consumed);
    }
}

#[test]
fn every_chunk_size_decodes_to_the_same_payload() {
    for fixture in FIXTURES {
        let payload = response_payload(fixture);
        for size in 1..=payload.len() {
            let framed = reframe(&payload, &[size; 4096]);
            let decoded = dechunk(&framed, 0)
                .unwrap_or_else(|error| panic!("{fixture}: chunk size {size} failed: {error}"));
            assert_eq!(
                decoded.body, payload,
                "{fixture}: chunk size {size} changed the payload"
            );
            assert_eq!(decoded.consumed, framed.len());
        }
    }
}

#[test]
fn every_two_chunk_split_point_decodes_to_the_same_payload() {
    for fixture in FIXTURES {
        let payload = response_payload(fixture);
        for split in 0..=payload.len() {
            let framed = reframe(&payload, &[split, payload.len()]);
            let decoded = dechunk(&framed, 0).unwrap();
            assert_eq!(
                decoded.body, payload,
                "{fixture}: split at {split} changed the payload"
            );
        }
    }
}

#[test]
fn chunk_extensions_and_trailers_do_not_change_the_payload() {
    for fixture in FIXTURES {
        let payload = response_payload(fixture);
        // Single chunk with an extension, then a trailer section.
        let mut framed = format!("{:x};source=p05\r\n", payload.len()).into_bytes();
        framed.extend_from_slice(&payload);
        framed.extend_from_slice(b"\r\n0\r\nX-Checksum: none\r\n\r\n");
        let decoded = dechunk(&framed, 0).unwrap();
        assert_eq!(
            decoded.body, payload,
            "{fixture}: extension changed payload"
        );
        assert_eq!(decoded.trailers.len(), 1);
    }
}

#[test]
fn reordered_headers_do_not_change_the_body() {
    for fixture in FIXTURES {
        let raw = read_fixture(&format!("{fixture}/response.http"));
        let golden = read_fixture(&format!("{fixture}/golden.sse"));
        let head = mtagentlens::framework::normalize::split_head(&raw).unwrap();
        let body = &raw[head.body_offset..];

        let mut rebuilt = format!("{}\r\n", head.start_line).into_bytes();
        for (name, value) in head.headers.iter().rev() {
            // Also exercise case-insensitive field-name matching.
            let name = if name.eq_ignore_ascii_case("content-encoding")
                || name.eq_ignore_ascii_case("transfer-encoding")
            {
                name.to_ascii_uppercase()
            } else {
                name.clone()
            };
            rebuilt.extend_from_slice(name.as_bytes());
            rebuilt.extend_from_slice(b": ");
            rebuilt.extend_from_slice(value);
            rebuilt.extend_from_slice(b"\r\n");
        }
        rebuilt.extend_from_slice(b"\r\n");
        rebuilt.extend_from_slice(body);

        let decoded = decode_message(&rebuilt).unwrap();
        assert_eq!(
            decoded.body, golden,
            "{fixture}: header order or casing changed the decoded body"
        );
    }
}

#[test]
fn malformed_responses_are_rejected() {
    for fixture in FIXTURES {
        let raw = read_fixture(&format!("{fixture}/response.http"));
        let head = mtagentlens::framework::normalize::split_head(&raw).unwrap();
        let body = &raw[head.body_offset..];
        let payload = response_payload(fixture);

        // 1. Truncated chunk data.
        let mut framed = format!("{:x}\r\n", payload.len()).into_bytes();
        framed.extend_from_slice(&payload[..payload.len() / 2]);
        assert!(matches!(
            dechunk(&framed, 0),
            Err(DecodeError::TruncatedChunk { .. })
        ));

        // 2. Bad chunk-size line.
        let mut framed = b"zz\r\n".to_vec();
        framed.extend_from_slice(&payload);
        assert!(matches!(
            dechunk(&framed, 0),
            Err(DecodeError::BadChunkSize { .. })
        ));

        // 3. Missing terminator: drop the final CRLF of `0\r\n\r\n`.
        let mut missing = body.to_vec();
        missing.truncate(missing.len().saturating_sub(2));
        assert!(matches!(
            dechunk(&missing, 0),
            Err(DecodeError::MissingTerminator)
        ));

        // 4. Corrupt the compressed frame but keep valid framing.
        let mut corrupt = payload.clone();
        corrupt[payload.len() / 2] ^= 0xff;
        let reframed = reframe(&corrupt, &[corrupt.len()]);
        let mut message = format!("{}\r\n", head.start_line).into_bytes();
        for (name, value) in &head.headers {
            message.extend_from_slice(name.as_bytes());
            message.extend_from_slice(b": ");
            message.extend_from_slice(value);
            message.extend_from_slice(b"\r\n");
        }
        message.extend_from_slice(b"\r\n");
        message.extend_from_slice(&reframed);
        assert!(
            matches!(
                decode_message(&message),
                Err(DecodeError::Decompress { .. })
            ),
            "{fixture}: corrupt zstd frame must not decode"
        );

        // 5. Unknown content coding.
        let mut unknown = format!("{}\r\n", head.start_line).into_bytes();
        for (name, value) in &head.headers {
            if name.eq_ignore_ascii_case("content-encoding") {
                unknown.extend_from_slice(b"Content-Encoding: snappy\r\n");
            } else {
                unknown.extend_from_slice(name.as_bytes());
                unknown.extend_from_slice(b": ");
                unknown.extend_from_slice(value);
                unknown.extend_from_slice(b"\r\n");
            }
        }
        unknown.extend_from_slice(b"\r\n");
        unknown.extend_from_slice(body);
        assert!(matches!(
            decode_message(&unknown),
            Err(DecodeError::UnsupportedContentEncoding(_))
        ));

        // 6. Content-Length promises more than the buffer holds.
        let short = b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabc";
        assert!(matches!(
            decode_message(short),
            Err(DecodeError::IncompleteBody { .. })
        ));
    }
}

#[test]
fn requests_are_byte_exact_and_still_valid_json() {
    for fixture in FIXTURES {
        let raw = read_fixture(&format!("{fixture}/request.http"));
        let decoded = decode_message(&raw).expect("request must decode");
        assert_eq!(decoded.consumed, raw.len());
        assert!(
            decoded.codings.is_empty(),
            "request must be identity-encoded"
        );

        let declared: usize = decoded
            .head
            .header("content-length")
            .expect("request must declare Content-Length")
            .parse()
            .unwrap();
        assert_eq!(declared, decoded.body.len());

        let json: serde_json::Value =
            serde_json::from_slice(&decoded.body).expect("request body must be JSON");
        assert!(json.get("model").is_some());

        // The sanitizer must have removed every identifying value. Do not put
        // the captured token itself in this file; scan for the shape instead.
        let text = String::from_utf8_lossy(&raw);
        assert!(!text.contains("lzk"), "{fixture}: home path leaked");
        assert!(
            text.to_ascii_lowercase()
                .contains("authorization: bearer redacted"),
            "{fixture}: bearer token was not redacted"
        );
        let leaked_token_shape = text
            .split("sk-")
            .skip(1)
            .any(|tail| tail.len() >= 20 && tail.bytes().take(20).all(|b| b.is_ascii_hexdigit()));
        assert!(!leaked_token_shape, "{fixture}: captured token leaked");
    }
}
