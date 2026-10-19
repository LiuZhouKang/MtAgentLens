// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Transport-line round-trip over a derived, first-hand payload.
//!
//! The legacy line was rendered with the same escaping rules as
//! `bpftools/tls_capture/tls_capture.c`; the base64 line is what the C producer
//! emits after P1. Both must decode to the exact bytes in `payload.bin`.

use std::fs;
use std::path::PathBuf;

use mtagentlens::framework::normalize::{
    PayloadSource, TransportError, ensure_data_b64, legacy_data_bytes, payload_bytes,
};
use serde_json::Value;

fn fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/wire/transport")
        .join(name)
}

fn read(name: &str) -> Vec<u8> {
    fs::read(fixture(name)).unwrap_or_else(|error| panic!("cannot read {name}: {error}"))
}

fn normalized_payload(line_name: &str) -> (Value, Vec<u8>, PayloadSource, u32) {
    let line = read(line_name);
    let mut value: Value = serde_json::from_slice(&line).expect("fixture line must be JSON");
    ensure_data_b64(&mut value, &line).expect("line must normalize");
    let payload = payload_bytes(&value)
        .expect("payload must decode")
        .expect("fixture declares a payload");
    (
        value,
        payload.bytes,
        payload.source,
        payload.declared_buf_size,
    )
}

#[test]
fn base64_and_legacy_lines_decode_to_identical_bytes() {
    let expected = read("payload.bin");
    let (_, from_base64, base64_source, base64_declared) =
        normalized_payload("capture-base64.jsonl");
    let (_, from_legacy, legacy_source, legacy_declared) =
        normalized_payload("capture-escaped.jsonl");

    assert_eq!(from_base64, expected, "base64 line changed the bytes");
    assert_eq!(from_legacy, expected, "legacy line changed the bytes");
    assert_eq!(from_base64, from_legacy);
    assert_eq!(base64_source, PayloadSource::Base64);
    assert_eq!(legacy_source, PayloadSource::LegacyUnescape);
    assert_eq!(base64_declared, expected.len() as u32);
    assert_eq!(legacy_declared, expected.len() as u32);
}

#[test]
fn decoded_length_equals_declared_buf_size() {
    let expected = read("payload.bin");
    for line_name in ["capture-base64.jsonl", "capture-escaped.jsonl"] {
        let (_, bytes, _, declared) = normalized_payload(line_name);
        assert_eq!(bytes.len(), declared as usize, "{line_name}");
        assert_eq!(bytes.len(), expected.len(), "{line_name}");
    }
}

#[test]
fn null_data_means_no_payload() {
    let line = br#"{"function":"READ/RECV","len":0,"buf_size":0,"data":null,"data_b64":null,"truncated":false}"#;
    let mut value: Value = serde_json::from_slice(line).unwrap();
    ensure_data_b64(&mut value, line).unwrap();
    assert_eq!(payload_bytes(&value).unwrap(), None);
}

#[test]
fn missing_data_b64_falls_back_to_legacy_bytes_and_says_so() {
    let line = read("capture-escaped.jsonl");
    let mut value: Value = serde_json::from_slice(&line).unwrap();
    assert!(value.get("data_b64").is_none(), "fixture starts as legacy");
    ensure_data_b64(&mut value, &line).unwrap();
    assert_eq!(value["data_b64_source"], "legacy_unescape");
    let payload = payload_bytes(&value).unwrap().unwrap();
    assert_eq!(payload.bytes, read("payload.bin"));
    assert_eq!(payload.source, PayloadSource::LegacyUnescape);
}

#[test]
fn base64_only_event_needs_no_legacy_field() {
    let payload = read("payload.bin");
    let encoded = base64_of(&payload);
    let line = format!(
        "{{\"buf_size\":{},\"data_b64\":\"{}\",\"truncated\":false}}",
        payload.len(),
        encoded
    )
    .into_bytes();
    let mut value: Value = serde_json::from_slice(&line).unwrap();
    ensure_data_b64(&mut value, &line).unwrap();
    assert_eq!(payload_bytes(&value).unwrap().unwrap().bytes, payload);
}

#[test]
fn malformed_base64_is_an_error_never_a_partial_payload() {
    let mut value: Value = serde_json::from_slice(br#"{"buf_size":3,"data_b64":"!!!"}"#).unwrap();
    assert!(matches!(
        ensure_data_b64(&mut value, b"{}"),
        Err(TransportError::InvalidBase64(_))
    ));
}

#[test]
fn truncated_line_keeps_raw_byte_accounting() {
    // Five captured bytes, ten declared: the shortfall is bytes_lost, and the
    // decoded payload length is the *captured* buf_size, never the declared one.
    let payload = b"abcde";
    let encoded = base64_of(payload);
    let line = format!(
        "{{\"buf_size\":5,\"len\":10,\"data_b64\":\"{}\",\"truncated\":true,\"bytes_lost\":5}}",
        encoded
    )
    .into_bytes();
    let mut value: Value = serde_json::from_slice(&line).unwrap();
    ensure_data_b64(&mut value, &line).unwrap();
    let decoded = payload_bytes(&value).unwrap().unwrap();
    assert_eq!(decoded.bytes, payload);
    assert!(decoded.truncated);
    assert_eq!(value["bytes_lost"], 5);
}

#[test]
fn legacy_line_recovers_the_exact_high_byte_values() {
    // The C producer's non-injective cases: 0xB5 and a raw C2 B5 sequence.
    let line = b"{\"buf_size\":3,\"data\":\"\\u00b5\\u00e9\\u00c2\"}";
    assert_eq!(
        legacy_data_bytes(line).unwrap(),
        Some(vec![0xB5, 0xE9, 0xC2])
    );
}

fn base64_of(payload: &[u8]) -> String {
    use base64::Engine as _;
    base64::engine::general_purpose::STANDARD.encode(payload)
}
