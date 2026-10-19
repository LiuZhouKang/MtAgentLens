// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! End-to-end response chain over the frozen wire corpus.
//!
//! The chain mirrors `main.rs`: PayloadNormalizer runs *before* SSLFilter, then
//! SSEProcessor and HTTPParser consume the single decoded event. The record
//! presets are the real ones, so a preset regression fails here too.

use std::fs;
use std::path::PathBuf;

use futures::StreamExt;
use mtagentlens::framework::analyzers::payload_normalizer::PayloadNormalizer;
use mtagentlens::framework::analyzers::presets::{
    record_http_filter_patterns, record_ssl_filter_patterns,
};
use mtagentlens::framework::analyzers::{
    Analyzer, HTTPFilter, HTTPParser, SSEProcessor, SSLFilter,
};
use mtagentlens::framework::core::Event;
use mtagentlens::framework::normalize::split_head;
use mtagentlens::framework::runners::common::AnalyzerProcessor;
use serde_json::{Value, json};

fn fixture(name: &str) -> Vec<u8> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/wire/openclaw-deepseek-zstd-chunked")
        .join(name);
    fs::read(path).expect("fixture must exist")
}

fn raw_event(bytes: &[u8], timestamp: u64) -> Event {
    use base64::Engine as _;
    Event::new_with_timestamp(
        timestamp,
        "ssl".to_string(),
        1488,
        "MainThread".to_string(),
        json!({
            "function": "READ/RECV",
            "pid": 1488,
            "tid": 1488,
            "ssl_ptr": 99,
            "buf_size": bytes.len(),
            "len": bytes.len(),
            // The producer still emits the display field; keep it so SSLFilter
            // sees exactly what it sees in production (including the standalone
            // terminator event).
            "data": String::from_utf8_lossy(bytes),
            "data_b64": base64::engine::general_purpose::STANDARD.encode(bytes),
            "truncated": false,
        }),
    )
}

fn fragments() -> Vec<Event> {
    let raw = fixture("response.http");
    let head = split_head(&raw).unwrap();
    let head_len = head.body_offset;
    let body_len = raw.len() - head_len;
    let first_body = body_len / 2;
    let terminator_start = raw.len() - 5; // "0\r\n\r\n"
    let cuts = [
        (0, head_len),
        (head_len, head_len + first_body),
        (head_len + first_body, terminator_start),
        (terminator_start, raw.len()),
    ];
    cuts.iter()
        .enumerate()
        .map(|(index, (start, end))| raw_event(&raw[*start..*end], index as u64 + 1))
        .collect()
}

async fn run_chain() -> Vec<Event> {
    let mut analyzers: Vec<Box<dyn Analyzer>> = vec![
        Box::new(PayloadNormalizer::new()),
        Box::new(SSLFilter::with_patterns(record_ssl_filter_patterns())),
        Box::new(SSEProcessor::new_with_timeout(30_000)),
        Box::new(HTTPParser::new()),
        Box::new(HTTPFilter::with_patterns(record_http_filter_patterns())),
    ];
    let stream = futures::stream::iter(fragments());
    let processed = AnalyzerProcessor::process_through_analyzers(Box::pin(stream), &mut analyzers)
        .await
        .expect("chain must not fail");
    processed.collect().await
}

#[tokio::test]
async fn record_chain_finalizes_a_zstd_chunked_sse_response() {
    let out = run_chain().await;
    let sse: Vec<_> = out
        .iter()
        .filter(|event| event.source == "sse_processor")
        .collect();
    assert!(
        !sse.is_empty(),
        "no sse_processor event was produced; sources: {:?}",
        out.iter()
            .map(|event| event.source.as_str())
            .collect::<Vec<_>>()
    );

    // The fixture's response contains assistant deltas ending in PONG and
    // [DONE]; SSEProcessor must have accumulated them.
    let has_content = sse.iter().any(|event| {
        event
            .data
            .get("text_content")
            .and_then(Value::as_str)
            .is_some_and(|text| !text.trim().is_empty())
            || event
                .data
                .get("json_content")
                .and_then(Value::as_str)
                .is_some_and(|text| !text.trim().is_empty())
    });
    assert!(
        has_content,
        "sse_processor events carry no content: {sse:#?}"
    );
    assert!(
        sse.iter().any(|event| {
            event
                .data
                .get("event_count")
                .and_then(Value::as_u64)
                .is_some_and(|count| count > 0)
        }),
        "SSE stream produced zero events"
    );
}

#[tokio::test]
async fn raw_ssl_events_still_reach_the_output() {
    let out = run_chain().await;
    let partial: Vec<_> = out
        .iter()
        .filter(|event| event.data.get("partial").and_then(Value::as_bool) == Some(true))
        .collect();
    // Four captured fragments enter the chain; the standalone terminator
    // (`0\r\n\r\n`) is consumed by the assembler and then de-duplicated by
    // the record SSL preset, so three partial fragments remain visible.
    assert_eq!(
        partial.len(),
        3,
        "captured fragments must remain visible (marked partial)"
    );
}

#[tokio::test]
async fn exactly_one_sse_response_reaches_the_output() {
    let out = run_chain().await;
    let sse: Vec<_> = out
        .iter()
        .filter(|event| event.source == "sse_processor")
        .collect();
    assert_eq!(sse.len(), 1, "expected exactly one finalized SSE response");
    let text = sse[0].data["text_content"]
        .as_str()
        .expect("SSE text content");
    assert!(text.contains("PONG"), "decoded SSE content lost: {text:?}");
}

#[tokio::test]
async fn no_empty_response_is_parsed_from_the_raw_fragments() {
    let out = run_chain().await;
    for event in out.iter().filter(|event| event.source == "http_parser") {
        let body = event.data.get("body").and_then(Value::as_str).unwrap_or("");
        assert!(
            !body.is_empty()
                || event.data.get("message_type") != Some(&Value::String("response".into())),
            "an empty response was parsed from a raw fragment: {event:?}"
        );
    }
}
