// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Cross-check our intent extraction against OpenClaw's own record.
//!
//! The response bytes came from the TLS uprobe. The `asserted-tools.json` file
//! came from OpenClaw's SQLite (`transcript_events`, full arguments; the
//! `trajectory_runtime_events` copy ellipsizes them). If our extractor and the
//! agent's own record disagree, one of them is wrong and the difference must be
//! explained — a passing build with an unexplained difference is exactly the
//! failure mode this repository is trying to eliminate.

use std::fs;
use std::path::PathBuf;

use mtagentlens::framework::normalize::decode_message;
use mtagentlens::responsibility::evidence::intent_extractor::IntentExtractor;
use mtagentlens::responsibility::types::{IntentAuthoritySource, MessageId, ProcessId};

fn fixture_path(relative: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/wire/openclaw-intent-crosscheck")
        .join(relative)
}

fn read(relative: &str) -> Vec<u8> {
    fs::read(fixture_path(relative))
        .unwrap_or_else(|error| panic!("cannot read {relative}: {error}"))
}

#[test]
fn our_extracted_intent_names_agree_with_openclaw_asserted_tool_calls() {
    let raw = read("response.http");
    let decoded = decode_message(&raw).expect("response must decode");
    let golden = read("golden.sse");
    assert_eq!(
        decoded.body, golden,
        "decoder must agree with the zstd golden"
    );

    let body = String::from_utf8(decoded.body).expect("SSE payload must be UTF-8");
    let intents = IntentExtractor::extract(
        &body,
        IntentAuthoritySource::SseResponseToolUse,
        ProcessId::new(1488, 0),
        MessageId::from("p05-crosscheck"),
        0,
    );

    let asserted: serde_json::Value =
        serde_json::from_slice(&read("asserted-tools.json")).expect("asserted tools must be JSON");
    let asserted = asserted
        .as_array()
        .expect("asserted tools must be an array");
    assert!(!asserted.is_empty(), "asserted side must not be empty");

    let ours: Vec<&str> = intents
        .iter()
        .map(|intent| intent.tool_name.as_str())
        .collect();
    let theirs: Vec<&str> = asserted
        .iter()
        .map(|call| call["name"].as_str().unwrap_or("<missing>"))
        .collect();

    // The claim under test: we see the call the agent says it made.
    assert_eq!(
        ours, theirs,
        "extracted tool names {ours:?} must equal openclaw's asserted {theirs:?}"
    );

    // Argument coverage is reported, not asserted, because this specific call
    // (`text("CROSSCHECK")`) carries no path or URL to extract. The general
    // case is covered by `evidence/args.rs`: unrecognised fields (OpenClaw's
    // `exec` puts JavaScript in `code`) are scanned for explicit literals, and
    // three unit tests pin that behaviour.
    for (intent, call) in intents.iter().zip(asserted.iter()) {
        println!(
            "tool={} ours_args={:?} asserted_args={}",
            intent.tool_name, intent.tool_args, call["args"]
        );
    }
}

#[test]
fn crosscheck_does_not_invent_calls_the_agent_did_not_make() {
    let raw = read("response.http");
    let decoded = decode_message(&raw).unwrap();
    let body = String::from_utf8(decoded.body).unwrap();
    let intents = IntentExtractor::extract(
        &body,
        IntentAuthoritySource::SseResponseToolUse,
        ProcessId::new(1488, 0),
        MessageId::from("p05-crosscheck"),
        0,
    );
    let asserted: serde_json::Value = serde_json::from_slice(&read("asserted-tools.json")).unwrap();
    let asserted_names: Vec<&str> = asserted
        .as_array()
        .unwrap()
        .iter()
        .map(|call| call["name"].as_str().unwrap_or(""))
        .collect();
    for intent in intents {
        assert!(
            asserted_names.contains(&intent.tool_name.as_str()),
            "extractor invented {:?}, which OpenClaw did not record",
            intent.tool_name
        );
    }
}
