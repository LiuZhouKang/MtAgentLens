// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Evidence pins for defects that P0.5 recorded and P1 is fixing.
//!
//! P0.5 wrote these as "current behavior" so the P1 justification stayed
//! executable. As each P1 change lands, the corresponding pin is inverted (the
//! names say so). The serde_json non-injectivity pin stays: it is a permanent
//! fact about the legacy `data` display field, which is why `data_b64` is the
//! authoritative byte source.

use std::fs;
use std::path::PathBuf;

use mtagentlens::framework::analyzers::common::detect_data_type;
use mtagentlens::framework::normalize::{decode_message, split_head};

fn response_raw(fixture: &str) -> Vec<u8> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/wire")
        .join(fixture)
        .join("response.http");
    fs::read(path).expect("wire fixture must exist")
}

#[test]
fn record_preset_no_longer_drops_compressed_responses() {
    // Inverted from P0.5: the record SSL preset used to contain
    // `data.type=binary`, and `ssl_filter.rs:249-254` still classifies a zstd
    // frame as binary. P1 removed the rule instead of teaching it about
    // decoding, because dropping bytes before they are decoded cannot be fixed
    // by a pre-decode heuristic.
    use mtagentlens::framework::analyzers::presets::record_ssl_filter_patterns;

    let patterns = record_ssl_filter_patterns();
    assert!(
        !patterns
            .iter()
            .any(|pattern| pattern.contains("data.type=binary")),
        "record preset reintroduced the pre-decode binary drop: {patterns:?}"
    );

    let raw = response_raw("openclaw-deepseek-zstd-chunked");
    let head = split_head(&raw).unwrap();
    let compressed = &raw[head.body_offset..];
    assert_eq!(
        detect_data_type(&String::from_utf8_lossy(compressed)),
        "binary",
        "the detector still calls compressed bytes binary; the preset must not use it"
    );

    let decoded = decode_message(&raw).unwrap();
    assert_eq!(
        detect_data_type(&String::from_utf8_lossy(&decoded.body)),
        "text"
    );
}

#[test]
fn serde_json_unicode_escapes_are_not_injective_at_the_byte_level() {
    // `tls_capture.c` emits an invalid byte 0xB5 as the six characters
    // backslash-u-0-0-b-5; valid UTF-8 for the same code point is emitted
    // verbatim as C2 B5. serde_json maps both to the same String, which is why
    // scripts/wire/extract_wire.py parses the C output byte-by-byte instead.
    let escape = format!("\\u{:04x}\\u{:04x}", 0x00b5, 0x00e9);
    let json = format!(r#"{{"data":"{escape}"}}"#);
    let value: serde_json::Value = serde_json::from_str(&json).unwrap();
    let text = value["data"].as_str().unwrap();
    assert_eq!(text.as_bytes(), &[0xC2, 0xB5, 0xC3, 0xA9]);
    assert_eq!(text.len(), 4, "two captured bytes became four UTF-8 bytes");
}

#[test]
fn stdout_reader_never_produces_replacement_characters() {
    // P0.5 pinned that `common.rs` called `String::from_utf8_lossy` on every
    // line. P1 replaced the whole path with byte reading. The behavior is
    // covered by `framework::runners::common::tests`; this test keeps the
    // source from silently reintroducing a lossy conversion.
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/framework/runners/common.rs");
    let source = fs::read_to_string(path).expect("common.rs must exist");
    assert!(
        !source.contains("from_utf8_lossy"),
        "stdout path reintroduced a lossy UTF-8 conversion"
    );
    assert!(
        source.contains("read_until(b'"),
        "stdout path no longer reads raw bytes"
    );
}
