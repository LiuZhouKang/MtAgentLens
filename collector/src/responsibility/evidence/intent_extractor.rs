// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// intent_extractor.rs — Intent extraction from LLM API responses.
//
// Primary path: structured tool_use parsing from Anthropic content blocks
// (type="tool_use" with name + input fields).
//
// Fallback path: regex heuristics for text-only responses when the
// structured format is unavailable (e.g., non-Anthropic agents, streaming
// partial JSON, or legacy formats).

use crate::responsibility::evidence::args::extract_intent_args;
use crate::responsibility::types::{
    Intent, IntentAuthoritySource, IntentClass, IntentId, IntentParseState, MessageId,
    NativeToolCallFormat, NativeToolCallRef, NormalizedArg, ProcessId,
};
use regex_lite::Regex;
use std::sync::atomic::{AtomicU64, Ordering};

/// Maximum body length to attempt structured JSON parsing (2 MB).
pub const MAX_JSON_PARSE_BYTES: usize = 2 * 1024 * 1024;

/// Bodies skipped because they exceeded [`MAX_JSON_PARSE_BYTES`].
///
/// A large system prompt (for example OpenClaw's 47 KB request is fine, but
/// agent runtimes that inline whole files are not) silently produced zero
/// intents before this counter existed. It is surfaced at run finalization.
pub static DIAG_BODY_TOO_LARGE: AtomicU64 = AtomicU64::new(0);

// ─── HTTP body extraction ─────────────────────────────────────────────

/// Strip HTTP response/request framing from raw captured payload.
///
/// SSL_read/SSL_write captures the application-layer buffer which may
/// include HTTP headers when the HTTP library sends headers+body in a
/// single write. This function strips the leading HTTP status/headers
/// to recover the clean JSON body.
///
/// Returns a slice pointing into `raw` (zero-copy).
pub fn strip_http_framing(raw: &str) -> &str {
    // HTTP/1.1 200 OK\r\nHeader: value\r\n\r\n{json body}
    if raw.starts_with("HTTP/")
        && let Some(pos) = raw.find("\r\n\r\n")
    {
        return &raw[pos + 4..];
    }
    // Malformed: no double-CRLF found — return as-is

    // HTTP request: POST /v1/messages HTTP/1.1\r\n...\r\n\r\n{json body}
    if let Some(pos) = raw.find("\r\n\r\n") {
        let after = &raw[pos + 4..];
        // Only strip if the remainder looks like JSON or SSE
        let trimmed = after.trim_start();
        if trimmed.starts_with('{') || trimmed.starts_with('[') || trimmed.starts_with("data:") {
            return trimmed;
        }
    }

    raw
}

/// Decode HTTP chunked transfer encoding.
///
/// Chunked bodies have the format:
///   <hex-size>\r\n<data>\r\n<hex-size>\r\n<data>\r\n0\r\n\r\n
///
/// Returns the de-chunked body. If the input doesn't look chunked,
/// returns the original input unchanged (zero-copy).
pub fn dechunk_body(raw: &str) -> std::borrow::Cow<'_, str> {
    // Quick check: does the body start with a hex chunk size line?
    if !raw.starts_with(|c: char| c.is_ascii_hexdigit()) {
        return std::borrow::Cow::Borrowed(raw);
    }

    // Verify it's chunked: first line should be hex digits followed by \r\n
    let first_line_end = match raw.find("\r\n") {
        Some(p) => p,
        None => return std::borrow::Cow::Borrowed(raw),
    };
    let first_line = &raw[..first_line_end];
    if !first_line.chars().all(|c| c.is_ascii_hexdigit()) {
        return std::borrow::Cow::Borrowed(raw);
    }

    // De-chunk
    let mut result = String::with_capacity(raw.len());
    let mut pos = 0;
    let bytes = raw.as_bytes();

    while pos < bytes.len() {
        // Find end of chunk size line
        let crlf = match bytes[pos..].windows(2).position(|w| w == b"\r\n") {
            Some(p) => pos + p,
            None => break,
        };
        let size_str = std::str::from_utf8(&bytes[pos..crlf]).unwrap_or("");
        let chunk_size = match usize::from_str_radix(size_str.trim(), 16) {
            Ok(s) => s,
            Err(_) => break,
        };
        if chunk_size == 0 {
            break; // Final chunk
        }
        pos = crlf + 2; // Skip \r\n
        let chunk_end = pos.saturating_add(chunk_size).min(bytes.len());
        result.push_str(std::str::from_utf8(&bytes[pos..chunk_end]).unwrap_or(""));
        pos = chunk_end;
        // Skip trailing \r\n after chunk data
        if pos + 2 <= bytes.len() && &bytes[pos..pos + 2] == b"\r\n" {
            pos += 2;
        }
    }

    if result.is_empty() {
        std::borrow::Cow::Borrowed(raw)
    } else {
        std::borrow::Cow::Owned(result)
    }
}

/// Detect whether a string might be JSON (starts with { or [).
fn looks_like_json(s: &str) -> bool {
    let trimmed = s.trim_start();
    trimmed.starts_with('{') || trimmed.starts_with('[')
}

/// Detect whether a string might be SSE (contains "data:" lines).
fn looks_like_sse(s: &str) -> bool {
    s.contains("data:") && (s.contains("event:") || s.contains("type\""))
}

/// Detect an SSE payload that starts with its own frames. Leading blank
/// lines and comments are framing; arbitrary bytes before the first frame
/// are a raw-offset capture and stay on the weak recovery path.
fn starts_with_sse(s: &str) -> bool {
    for line in s.lines() {
        let line = line.trim_start();
        if line.is_empty() || line.starts_with(':') {
            continue;
        }
        return line.starts_with("data:") || line.starts_with("event:");
    }
    false
}

// ─── IntentClass helpers ──────────────────────────────────────────────
//
// The class table lives in `types::tool_vocabulary` so that every intent path
// shares one answer. It used to be duplicated here, and the copy disagreed
// with `types::support_types` on `Bash` and several other names.

/// Required input fields per tool name. Missing fields → Partial parse state.
const REQUIRED_FIELDS: &[(&str, &[&str])] = &[
    ("Bash", &["command"]),
    ("Write", &["file_path", "content"]),
    ("Edit", &["file_path", "old_string", "new_string"]),
    ("Read", &["file_path"]),
    ("Glob", &["pattern"]),
    ("Grep", &["pattern"]),
    ("WebFetch", &["url"]),
    ("WebSearch", &["query"]),
    ("NotebookEdit", &["notebook_path"]),
    ("Task", &["description", "prompt"]),
    ("TodoWrite", &["todos"]),
    ("LS", &["path"]),
];

fn classify_intent_class(tool_name: &str) -> IntentClass {
    crate::responsibility::types::tool_vocabulary::classify_intent_class(tool_name)
}

/// Preserve a captured structured tool-call id. Empty/absent values are not
/// references, and the collector never manufactures one.
fn captured_native_ref(
    value: Option<&serde_json::Value>,
    format: NativeToolCallFormat,
) -> Option<NativeToolCallRef> {
    let id = value?.as_str()?.trim();
    if id.is_empty() {
        return None;
    }
    Some(NativeToolCallRef {
        format,
        id: id.to_string(),
    })
}

/// Normalize raw/generic recovery results into unverified text hypotheses.
///
/// The parsed tool name, arguments, and parse state are retained so the
/// observation is not dropped; only its provenance is downgraded.
fn downgrade_to_text_fallback(intents: &mut [Intent]) {
    for intent in intents {
        intent.primary_matching_eligible = false;
        intent.authority_source = IntentAuthoritySource::TextFallback;
        intent.native_tool_call = None;
    }
}

fn classify_parse_state(tool_name: &str, input: &serde_json::Value) -> IntentParseState {
    let Some(fields) = REQUIRED_FIELDS
        .iter()
        .find(|(name, _)| *name == tool_name)
        .map(|(_, f)| *f)
    else {
        // Unknown tool — check if input is a valid JSON object
        if input.is_object() {
            return IntentParseState::Complete;
        }
        return IntentParseState::Partial;
    };

    if !input.is_object() {
        return IntentParseState::Malformed;
    }

    let obj = input.as_object().unwrap();
    let all_present = fields.iter().all(|f| obj.contains_key(*f));
    if all_present {
        IntentParseState::Complete
    } else {
        IntentParseState::Partial
    }
}

// ─── IntentExtractor ──────────────────────────────────────────────────

pub struct IntentExtractor;

impl IntentExtractor {
    /// Required fields for a given tool name.
    ///
    /// Returns the expected input keys used for parse state classification.
    /// Unknown tools return an empty slice.
    pub fn required_fields(tool_name: &str) -> &[&str] {
        REQUIRED_FIELDS
            .iter()
            .find(|(name, _)| *name == tool_name)
            .map(|(_, fields)| *fields)
            .unwrap_or(&[])
    }

    // ── Main extraction entry points ────────────────────────────────

    /// Extract intents from raw payload (any provider, any format, any offset).
    ///
    /// Handles payloads that start at arbitrary positions within the data
    /// stream (e.g., SSL buffers that don't align with message boundaries).
    ///
    /// # Pipeline (first non-empty result wins):
    ///
    /// Request sources: role-aware `root.messages` extraction only, no fallback.
    /// 0. Raw buffer scan — find and parse every JSON chunk in the buffer
    /// 1. Strip HTTP framing if present (headers → clean body)
    /// 2. Anthropic JSON — `content[]` with `type: "tool_use"`
    /// 3. OpenAI JSON — `choices[].message.tool_calls[]`
    /// 4. Generic JSON tree scan — walks any tree for tool_use patterns
    /// 5. Anthropic SSE — `content_block_start/delta/stop` events
    /// 6. OpenAI SSE — `choices[].delta.tool_calls[]`
    /// 7. Regex fallback — code fences, inline JSON tool_use
    pub fn extract(
        body: &str,
        source: IntentAuthoritySource,
        process_id: ProcessId,
        message_id: MessageId,
        timestamp_ns: u64,
    ) -> Vec<Intent> {
        if body.len() > MAX_JSON_PARSE_BYTES {
            DIAG_BODY_TOO_LARGE.fetch_add(1, Ordering::Relaxed);
        }

        // Request sources carry conversation history, not a response. Only
        // the role-aware `messages` walk may create intents: tool declarations
        // and retrieved payloads are data, never assistant calls. This must
        // run before every raw/SSE/generic fallback, which scan tool-shaped
        // objects anywhere in the tree. Non-parseable requests yield no
        // invented intents rather than falling through to those scanners.
        if source == IntentAuthoritySource::RequestHistoryAssistantToolUse {
            let clean = strip_http_framing(body);
            let dechunked = dechunk_body(clean);
            let payload: &str = &dechunked;
            if payload.len() <= MAX_JSON_PARSE_BYTES
                && looks_like_json(payload)
                && let Ok(root) = serde_json::from_str::<serde_json::Value>(payload)
                && let Some(messages) = root.get("messages").and_then(|m| m.as_array())
            {
                let messages_json = serde_json::to_string(messages).unwrap_or_default();
                return Self::extract_from_request(
                    &messages_json,
                    process_id,
                    message_id,
                    timestamp_ns,
                );
            }
            return Vec::new();
        }

        // Prefer known response formats on the framed/de-chunked payload
        // before any raw-buffer or generic recovery. A raw scan can match
        // function-shaped data inside an unknown wrapper, so it must not
        // pre-empt a structured parse of a known provider response.
        let clean = strip_http_framing(body);
        let dechunked = dechunk_body(clean);
        let payload: &str = &dechunked;

        // Step 1: Anthropic structured JSON (content blocks)
        if payload.len() <= MAX_JSON_PARSE_BYTES && looks_like_json(payload) {
            let intents = Self::extract_from_json(
                payload,
                source,
                process_id,
                message_id.clone(),
                timestamp_ns,
            );
            if !intents.is_empty() {
                return intents;
            }
        }

        // Step 2: OpenAI structured JSON
        if payload.len() <= MAX_JSON_PARSE_BYTES && looks_like_json(payload) {
            let intents = Self::extract_from_openai_json(
                payload,
                source,
                process_id,
                message_id.clone(),
                timestamp_ns,
            );
            if !intents.is_empty() {
                return intents;
            }
        }

        // Step 3: Known SSE response formats. The dedicated stream parsers
        // accumulate content_block_start/delta/stop before any raw scan.
        if starts_with_sse(payload) {
            let sse = Self::extract_from_sse(
                payload,
                source,
                process_id,
                message_id.clone(),
                timestamp_ns,
            );
            if !sse.is_empty() {
                return sse;
            }
            let sse = Self::extract_from_openai_sse(
                payload,
                source,
                process_id,
                message_id.clone(),
                timestamp_ns,
            );
            if !sse.is_empty() {
                return sse;
            }
        }

        // Step 4: Raw buffer scan — handles buffers that start at arbitrary
        // offsets. These are text hypotheses, never native structured calls.
        if body.len() > 64 {
            let intents = Self::extract_from_raw_buffer(
                body,
                source,
                process_id,
                message_id.clone(),
                timestamp_ns,
            );
            if !intents.is_empty() {
                return intents;
            }
        }

        // Step 5: Generic JSON tree scan (catch-all for unknown formats)
        if payload.len() <= MAX_JSON_PARSE_BYTES && looks_like_json(payload) {
            let intents = Self::extract_generic_json(
                payload,
                source,
                process_id,
                message_id.clone(),
                timestamp_ns,
            );
            if !intents.is_empty() {
                return intents;
            }
        }

        // Step 6: Generic JSON on the raw body when framing changed it.
        if payload.as_ptr() != body.as_ptr() && body.len() <= MAX_JSON_PARSE_BYTES {
            let intents = Self::extract_generic_json(
                body,
                source,
                process_id,
                message_id.clone(),
                timestamp_ns,
            );
            if !intents.is_empty() {
                return intents;
            }
        }

        // Step 7: Regex fallback (always runs last, on de-chunked payload)
        let regex_intents = Self::extract_regex_fallback(
            payload,
            source,
            process_id,
            message_id.clone(),
            timestamp_ns,
        );
        if !regex_intents.is_empty() {
            return regex_intents;
        }

        Vec::new()
    }

    /// Extract intents from the request messages JSON array.
    ///
    /// Walks each assistant message looking for content blocks of type `tool_use`.
    pub fn extract_from_request(
        messages_json: &str,
        process_id: ProcessId,
        message_id: MessageId,
        timestamp_ns: u64,
    ) -> Vec<Intent> {
        let messages: Vec<serde_json::Value> = match serde_json::from_str(messages_json) {
            Ok(v) => v,
            Err(_) => return Vec::new(),
        };
        Self::extract_from_request_messages(&messages, process_id, message_id, timestamp_ns)
    }

    /// Extract intents from already-parsed request messages.
    ///
    /// Borrowing the parsed history keeps the role-aware walk shared by the
    /// string path and in-memory callers; neither invents a second protocol
    /// parser.
    pub fn extract_from_request_messages(
        messages: &[serde_json::Value],
        process_id: ProcessId,
        message_id: MessageId,
        timestamp_ns: u64,
    ) -> Vec<Intent> {
        let mut intents = Vec::new();

        for msg in messages {
            let role = msg.get("role").and_then(|r| r.as_str()).unwrap_or("");

            if role == "user" {
                // Extract user message text as a content-bearing intent
                // so the ContentBoundary can detect injection/forgery
                // patterns in the initial task prompt.
                let user_text = match msg.get("content") {
                    Some(serde_json::Value::String(s)) => s.clone(),
                    Some(serde_json::Value::Array(arr)) => arr
                        .iter()
                        .filter_map(|b| {
                            if b.get("type").and_then(|t| t.as_str()) == Some("text") {
                                b.get("text")
                                    .and_then(|t| t.as_str())
                                    .map(|s| s.to_string())
                            } else {
                                None
                            }
                        })
                        .collect::<Vec<_>>()
                        .join("\n"),
                    _ => continue,
                };
                if !user_text.is_empty() {
                    intents.push(Intent {
                        intent_id: IntentId::new(),
                        process_id,
                        message_id: message_id.clone(),
                        tool_name: "UserPrompt".to_string(),
                        tool_args: vec![NormalizedArg::Content(user_text)],
                        intent_class: IntentClass::SideEffect,
                        parse_state: IntentParseState::Complete,
                        primary_matching_eligible: false,
                        authority_source: IntentAuthoritySource::RequestHistoryAssistantToolUse,
                        timestamp_ns,
                        native_tool_call: None,
                        declared_conversation_id: None,
                    });
                }
                continue;
            }

            if role != "assistant" {
                continue;
            }

            // OpenAI assistant calls: {id, type: "function", function: {name, arguments}}.
            if let Some(tool_calls) = msg.get("tool_calls").and_then(|tc| tc.as_array()) {
                for tool_call in tool_calls {
                    if let Some(intent) = Self::openai_tool_call_to_intent(
                        tool_call,
                        IntentAuthoritySource::RequestHistoryAssistantToolUse,
                        process_id,
                        message_id.clone(),
                        timestamp_ns,
                    ) {
                        intents.push(intent);
                    }
                }
            }

            // Anthropic assistant content blocks.
            let content = match msg.get("content") {
                Some(serde_json::Value::Array(arr)) => arr,
                _ => continue,
            };

            for block in content {
                if block.get("type").and_then(|t| t.as_str()) == Some("tool_use")
                    && let Some(intent) = Self::block_to_intent(
                        block,
                        IntentAuthoritySource::RequestHistoryAssistantToolUse,
                        process_id,
                        message_id.clone(),
                        timestamp_ns,
                    )
                {
                    intents.push(intent);
                }
            }
        }

        intents
    }

    // ── Structured JSON extraction ──────────────────────────────────

    /// Extract tool_use blocks from a JSON body.
    ///
    /// Handles both full message objects and individual content blocks.
    fn extract_from_json(
        body: &str,
        source: IntentAuthoritySource,
        process_id: ProcessId,
        message_id: MessageId,
        timestamp_ns: u64,
    ) -> Vec<Intent> {
        let value: serde_json::Value = match serde_json::from_str(body) {
            Ok(v) => v,
            Err(_) => return Vec::new(),
        };

        let mut intents = Vec::new();

        // Case A: Full response message with content array
        if let Some(content) = value.get("content").and_then(|c| c.as_array()) {
            for block in content {
                if block.get("type").and_then(|t| t.as_str()) == Some("tool_use")
                    && let Some(intent) = Self::block_to_intent(
                        block,
                        source,
                        process_id,
                        message_id.clone(),
                        timestamp_ns,
                    )
                {
                    intents.push(intent);
                }
            }
            return intents;
        }

        // Case B: Single content block (e.g., from extracted delta)
        if value.get("type").and_then(|t| t.as_str()) == Some("tool_use")
            && let Some(intent) =
                Self::block_to_intent(&value, source, process_id, message_id, timestamp_ns)
        {
            intents.push(intent);
        }

        intents
    }

    // ── OpenAI JSON extraction ────────────────────────────────────────

    /// Extract from OpenAI API response format.
    ///
    /// OpenAI uses:
    /// ```json
    /// {
    ///   "choices": [{
    ///     "message": {
    ///       "tool_calls": [{
    ///         "function": {"name": "...", "arguments": "{...}"}
    ///       }]
    ///     }
    ///   }]
    /// }
    /// ```
    fn extract_from_openai_json(
        body: &str,
        source: IntentAuthoritySource,
        process_id: ProcessId,
        message_id: MessageId,
        timestamp_ns: u64,
    ) -> Vec<Intent> {
        let value: serde_json::Value = match serde_json::from_str(body) {
            Ok(v) => v,
            Err(_) => return Vec::new(),
        };

        let mut intents = Vec::new();

        // Walk choices[].message.tool_calls[]
        let choices = match value.get("choices").and_then(|c| c.as_array()) {
            Some(c) => c,
            None => return Vec::new(),
        };

        for choice in choices {
            let message = match choice.get("message") {
                Some(m) => m,
                None => continue,
            };
            let tool_calls = match message.get("tool_calls").and_then(|tc| tc.as_array()) {
                Some(tc) => tc,
                None => continue,
            };
            for tc in tool_calls {
                if let Some(intent) = Self::openai_tool_call_to_intent(
                    tc,
                    source,
                    process_id,
                    message_id.clone(),
                    timestamp_ns,
                ) {
                    intents.push(intent);
                }
            }
        }

        intents
    }

    /// Extract from OpenAI streaming SSE format.
    ///
    /// OpenAI SSE delta format:
    /// ```json
    /// data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"Bash","arguments":"{\"command\":"}}]}}]}
    /// ```
    fn extract_from_openai_sse(
        body: &str,
        source: IntentAuthoritySource,
        process_id: ProcessId,
        message_id: MessageId,
        timestamp_ns: u64,
    ) -> Vec<Intent> {
        let mut intents = Vec::new();
        // A tool index is local to a choice; choices may stream concurrently.
        let mut accum: std::collections::BTreeMap<
            (i64, i64),
            (String, String, Option<NativeToolCallRef>),
        > = std::collections::BTreeMap::new();

        for line in body.lines() {
            let data = if let Some(d) = line.strip_prefix("data: ") {
                d.trim()
            } else if line.starts_with("data:") {
                line[5..].trim()
            } else {
                continue;
            };

            if data.is_empty() || data == "[DONE]" {
                continue;
            }

            let event: serde_json::Value = match serde_json::from_str(data) {
                Ok(v) => v,
                Err(_) => continue,
            };

            let choices = match event.get("choices").and_then(|c| c.as_array()) {
                Some(c) => c,
                None => continue,
            };

            for (choice_position, choice) in choices.iter().enumerate() {
                let choice_index = choice
                    .get("index")
                    .and_then(|index| index.as_i64())
                    .unwrap_or(choice_position as i64);
                let delta = match choice.get("delta") {
                    Some(d) => d,
                    None => continue,
                };
                let tool_calls = match delta.get("tool_calls").and_then(|tc| tc.as_array()) {
                    Some(tc) => tc,
                    None => continue,
                };
                for tc in tool_calls {
                    let index = (
                        choice_index,
                        tc.get("index").and_then(|i| i.as_i64()).unwrap_or(0),
                    );
                    // The id can arrive before or without the function
                    // fragment; preserve it independently of accumulation.
                    let native_tool_call =
                        captured_native_ref(tc.get("id"), NativeToolCallFormat::OpenAi);
                    if let Some(native) = native_tool_call.clone() {
                        accum
                            .entry(index)
                            .or_insert((String::new(), String::new(), None))
                            .2 = Some(native);
                    }
                    let function = match tc.get("function") {
                        Some(f) => f,
                        None => continue,
                    };

                    // Accumulate name if present
                    if let Some(name) = function.get("name").and_then(|n| n.as_str()) {
                        accum
                            .entry(index)
                            .and_modify(|(n, _, _)| *n = name.to_string())
                            .or_insert((name.to_string(), String::new(), None));
                    }

                    // Accumulate arguments fragment
                    if let Some(args) = function.get("arguments").and_then(|a| a.as_str()) {
                        accum
                            .entry(index)
                            .and_modify(|(_, a, _)| a.push_str(args))
                            .or_insert((String::new(), args.to_string(), None));
                    }
                }
            }
        }

        // Emit completed accumulations
        for (tool_name, args_str, native_tool_call) in accum.values() {
            if tool_name.is_empty() {
                continue;
            }
            let input: serde_json::Value =
                serde_json::from_str(args_str).unwrap_or(serde_json::Value::Null);
            let tool_args = extract_intent_args(tool_name, &input);
            let intent_class = classify_intent_class(tool_name);
            let parse_state = if args_str.is_empty() {
                IntentParseState::Partial
            } else {
                classify_parse_state(tool_name, &input)
            };

            intents.push(Intent {
                intent_id: IntentId::new(),
                process_id,
                message_id: message_id.clone(),
                tool_name: tool_name.clone(),
                tool_args,
                intent_class,
                parse_state,
                primary_matching_eligible: true,
                authority_source: source,
                timestamp_ns,
                native_tool_call: native_tool_call.clone(),
                declared_conversation_id: None,
            });
        }

        intents
    }

    // ── Generic JSON scanner ───────────────────────────────────────────

    /// Walk ANY JSON tree looking for tool-use-like structures.
    ///
    /// Scans recursively for:
    /// - `type: "tool_use"` + `name` (Anthropic content block)
    /// - `function.name` + `function.arguments` (OpenAI tool_call)
    /// - Objects with both `name` and `input` fields (generic tool call)
    ///
    /// This is the catch-all that handles unknown API formats.
    fn extract_generic_json(
        body: &str,
        source: IntentAuthoritySource,
        process_id: ProcessId,
        message_id: MessageId,
        timestamp_ns: u64,
    ) -> Vec<Intent> {
        let value: serde_json::Value = match serde_json::from_str(body) {
            Ok(v) => v,
            Err(_) => return Vec::new(),
        };

        let mut intents = Vec::new();
        Self::scan_json_for_tools(
            &value,
            source,
            process_id,
            &message_id,
            timestamp_ns,
            &mut intents,
        );
        downgrade_to_text_fallback(&mut intents);
        intents
    }

    /// Recursively scan a JSON value for tool-use patterns.
    fn scan_json_for_tools(
        value: &serde_json::Value,
        source: IntentAuthoritySource,
        process_id: ProcessId,
        message_id: &MessageId,
        timestamp_ns: u64,
        out: &mut Vec<Intent>,
    ) {
        match value {
            serde_json::Value::Object(obj) => {
                // Pattern A: Anthropic tool_use block → type="tool_use" + name
                if obj.get("type").and_then(|t| t.as_str()) == Some("tool_use")
                    && let Some(name) = obj.get("name").and_then(|n| n.as_str())
                {
                    let input = obj.get("input").unwrap_or(&serde_json::Value::Null);
                    let tool_name = name.to_string();
                    let tool_args = extract_intent_args(&tool_name, input);
                    let intent_class = classify_intent_class(&tool_name);
                    let parse_state = classify_parse_state(&tool_name, input);

                    out.push(Intent {
                        intent_id: IntentId::new(),
                        process_id,
                        message_id: message_id.clone(),
                        tool_name,
                        tool_args,
                        intent_class,
                        parse_state,
                        primary_matching_eligible: true,
                        authority_source: source,
                        timestamp_ns,
                        native_tool_call: captured_native_ref(
                            obj.get("id"),
                            NativeToolCallFormat::Anthropic,
                        ),
                        declared_conversation_id: None,
                    });
                    return; // Don't recurse into the tool_use block
                }

                // Pattern B: OpenAI function call → has function.name and an
                // actual arguments string. A schema declaration has
                // parameters, not arguments, and is not a call.
                if let Some(function) = obj.get("function")
                    && let Some(name) = function.get("name").and_then(|n| n.as_str())
                    && let Some(args_str) = function.get("arguments").and_then(|a| a.as_str())
                {
                    let input: serde_json::Value =
                        serde_json::from_str(args_str).unwrap_or(serde_json::Value::Null);
                    let tool_name = name.to_string();
                    let tool_args = extract_intent_args(&tool_name, &input);
                    let intent_class = classify_intent_class(&tool_name);
                    let parse_state = classify_parse_state(&tool_name, &input);

                    out.push(Intent {
                        intent_id: IntentId::new(),
                        process_id,
                        message_id: message_id.clone(),
                        tool_name,
                        tool_args,
                        intent_class,
                        parse_state,
                        primary_matching_eligible: true,
                        authority_source: source,
                        timestamp_ns,
                        native_tool_call: captured_native_ref(
                            obj.get("id"),
                            NativeToolCallFormat::OpenAi,
                        ),
                        declared_conversation_id: None,
                    });
                }

                // Recurse into all values
                for (_k, v) in obj {
                    Self::scan_json_for_tools(v, source, process_id, message_id, timestamp_ns, out);
                }
            }
            serde_json::Value::Array(arr) => {
                for v in arr {
                    Self::scan_json_for_tools(v, source, process_id, message_id, timestamp_ns, out);
                }
            }
            _ => {} // Scalar — nothing to scan
        }
    }

    // ── Raw buffer chunk extraction ──────────────────────────────────

    /// Scan a raw buffer for JSON objects and try to extract intents
    /// from every valid JSON chunk found.
    ///
    /// SSL event "data" buffers start at arbitrary offsets within the
    /// application data stream — not at JSON message boundaries.
    /// This function finds every `{...}` or `[...]` chunk in the buffer,
    /// tries to parse it, and extracts intents from successful parses.
    pub fn extract_from_raw_buffer(
        buf: &str,
        source: IntentAuthoritySource,
        process_id: ProcessId,
        message_id: MessageId,
        timestamp_ns: u64,
    ) -> Vec<Intent> {
        let mut intents = Vec::new();
        let bytes = buf.as_bytes();
        let mut pos = 0;

        // Scan through the buffer looking for JSON object/array starts
        while pos < bytes.len() {
            // Find next '{' or '['
            let start = match bytes[pos..].iter().position(|&b| b == b'{' || b == b'[') {
                Some(p) => pos + p,
                None => break,
            };

            // Try to find the matching close from this position
            let end_char = if bytes[start] == b'{' { b'}' } else { b']' };
            let mut depth: i32 = 0;
            let mut in_string = false;
            let mut escaped = false;
            let mut end = start;

            for (i, &b) in bytes[start..].iter().enumerate() {
                if escaped {
                    escaped = false;
                    continue;
                }
                if b == b'\\' && in_string {
                    escaped = true;
                    continue;
                }
                if b == b'"' {
                    in_string = !in_string;
                    continue;
                }
                if in_string {
                    continue;
                }
                if b == bytes[start] {
                    depth += 1;
                } else if b == end_char {
                    depth -= 1;
                    if depth == 0 {
                        end = start + i + 1;
                        break;
                    }
                }
            }

            if depth != 0 {
                // Unbalanced — skip this '{'  and continue
                pos = start + 1;
                continue;
            }

            let chunk = &buf[start..end];
            if chunk.len() > MAX_JSON_PARSE_BYTES {
                pos = end;
                continue;
            }

            // Try each parser on this chunk
            let tries: [(
                &str,
                fn(&str, IntentAuthoritySource, ProcessId, MessageId, u64) -> Vec<Intent>,
            ); 3] = [
                ("anthropic_json", IntentExtractor::extract_from_json),
                ("openai_json", IntentExtractor::extract_from_openai_json),
                ("generic_json", IntentExtractor::extract_generic_json),
            ];

            let mut found = false;
            for (_name, extractor) in &tries {
                let result = extractor(chunk, source, process_id, message_id.clone(), timestamp_ns);
                if !result.is_empty() {
                    intents.extend(result);
                    found = true;
                    break;
                }
            }

            if !found {
                // Also try SSE extraction if the chunk looks like SSE
                if looks_like_sse(chunk) {
                    let sse = IntentExtractor::extract_from_sse(
                        chunk,
                        source,
                        process_id,
                        message_id.clone(),
                        timestamp_ns,
                    );
                    if !sse.is_empty() {
                        intents.extend(sse);
                    }
                }
            }

            pos = end;
        }

        downgrade_to_text_fallback(&mut intents);
        intents
    }

    // ── SSE event extraction ────────────────────────────────────────

    /// Parse SSE (Server-Sent Events) body looking for tool_use blocks.
    ///
    /// Handles `content_block_start` events with type="tool_use" and
    /// `content_block_delta` events with input_json_delta.
    fn extract_from_sse(
        body: &str,
        source: IntentAuthoritySource,
        process_id: ProcessId,
        message_id: MessageId,
        timestamp_ns: u64,
    ) -> Vec<Intent> {
        let mut intents = Vec::new();
        // Accumulate tool_use definitions across SSE events. BTreeMap keeps
        // emission deterministic by content-block index. Each entry stores
        // (tool_name, start-event initial input, delta buffer, native reference).
        // Initial input stays separate so it is never prepended to delta
        // fragments; it is the whole input only when no deltas arrive.
        let mut accum: std::collections::BTreeMap<
            u64,
            (
                String,
                Option<serde_json::Value>,
                String,
                Option<NativeToolCallRef>,
            ),
        > = std::collections::BTreeMap::new();

        for line in body.lines() {
            let data = if let Some(d) = line.strip_prefix("data: ") {
                d.trim()
            } else if line.starts_with("data:") {
                line[5..].trim()
            } else {
                continue;
            };

            if data.is_empty() || data == "[DONE]" {
                continue;
            }

            let event: serde_json::Value = match serde_json::from_str(data) {
                Ok(v) => v,
                Err(_) => continue,
            };

            let event_type = event.get("type").and_then(|t| t.as_str()).unwrap_or("");

            match event_type {
                "content_block_start" => {
                    let content_block = match event.get("content_block") {
                        Some(cb) => cb,
                        None => continue,
                    };
                    if content_block.get("type").and_then(|t| t.as_str()) == Some("tool_use") {
                        let tool_name = content_block
                            .get("name")
                            .and_then(|n| n.as_str())
                            .unwrap_or("")
                            .to_string();
                        let index = event.get("index").and_then(|i| i.as_u64()).unwrap_or(0);
                        accum.insert(
                            index,
                            (
                                tool_name,
                                content_block.get("input").cloned(),
                                String::new(),
                                captured_native_ref(
                                    content_block.get("id"),
                                    NativeToolCallFormat::Anthropic,
                                ),
                            ),
                        );
                    }
                }

                "content_block_delta" => {
                    let delta = match event.get("delta") {
                        Some(d) => d,
                        None => continue,
                    };
                    if delta.get("type").and_then(|t| t.as_str()) != Some("input_json_delta") {
                        continue;
                    }
                    let partial = delta
                        .get("partial_json")
                        .and_then(|p| p.as_str())
                        .unwrap_or("");
                    let index = event.get("index").and_then(|i| i.as_u64()).unwrap_or(0);

                    if let Some(entry) = accum.get_mut(&index) {
                        entry.2.push_str(partial);
                    }
                }

                "content_block_stop" => {
                    let index = event.get("index").and_then(|i| i.as_u64()).unwrap_or(0);
                    if let Some(entry) = accum.remove(&index) {
                        intents.push(Self::finalize_sse_tool_use(
                            entry,
                            source,
                            process_id,
                            message_id.clone(),
                            timestamp_ns,
                            true,
                        ));
                    }
                }

                _ => {}
            }
        }

        // Emit any incomplete accumulations as partial intents
        for entry in accum.into_values() {
            intents.push(Self::finalize_sse_tool_use(
                entry,
                source,
                process_id,
                message_id.clone(),
                timestamp_ns,
                false,
            ));
        }

        intents
    }

    /// Finalize one accumulated Anthropic tool_use block.
    ///
    /// Deltas are the streamed JSON fragments; the start event's `input` is
    /// either a placeholder (`{}`) or the whole input when no deltas arrive.
    /// A failed parse still yields a `Partial` intent so an observed call and
    /// its native reference are not silently dropped. `Partial` records only
    /// that the arguments were not fully observed; it does not prove the call
    /// ran or that its arguments were complete.
    fn finalize_sse_tool_use(
        (tool_name, initial_input, input_json, native_tool_call): (
            String,
            Option<serde_json::Value>,
            String,
            Option<NativeToolCallRef>,
        ),
        source: IntentAuthoritySource,
        process_id: ProcessId,
        message_id: MessageId,
        timestamp_ns: u64,
        stopped: bool,
    ) -> Intent {
        let parsed = if input_json.is_empty() {
            initial_input
        } else {
            serde_json::from_str::<serde_json::Value>(&input_json).ok()
        };
        let (input, parse_state) = match parsed {
            Some(input) => {
                let state = if stopped {
                    classify_parse_state(&tool_name, &input)
                } else {
                    IntentParseState::Partial
                };
                (input, state)
            }
            None => (serde_json::Value::Null, IntentParseState::Partial),
        };
        let tool_args = extract_intent_args(&tool_name, &input);
        let intent_class = classify_intent_class(&tool_name);

        Intent {
            intent_id: IntentId::new(),
            process_id,
            message_id,
            tool_name,
            tool_args,
            intent_class,
            parse_state,
            primary_matching_eligible: true,
            authority_source: source,
            timestamp_ns,
            native_tool_call,
            declared_conversation_id: None,
        }
    }

    // ── Regex fallback ──────────────────────────────────────────────

    /// Regex-based extraction for text-only responses.
    ///
    /// Looks for patterns like:
    /// - Code fences with language identifiers: ```bash ... ```
    /// - JSON tool_use blocks inline
    /// - Tool invocation patterns: "Tool: Bash" followed by JSON args
    fn extract_regex_fallback(
        body: &str,
        _source: IntentAuthoritySource,
        process_id: ProcessId,
        message_id: MessageId,
        timestamp_ns: u64,
    ) -> Vec<Intent> {
        let mut intents = Vec::new();

        // Pattern 1: JSON tool_use blocks inline
        let tool_use_re = Regex::new(
            r#""type"\s*:\s*"tool_use"[^}]*"name"\s*:\s*"([^"]+)"[^}]*"input"\s*:\s*(\{[^}]+\})"#,
        )
        .unwrap();
        // captures() returns only the first match — same as captures_iter + break.
        if let Some(caps) = tool_use_re.captures(body) {
            let tool_name = caps.get(1).unwrap().as_str().to_string();
            let input_str = caps.get(2).unwrap().as_str();
            if let Ok(input) = serde_json::from_str::<serde_json::Value>(input_str) {
                let tool_args = extract_intent_args(&tool_name, &input);
                let intent_class = classify_intent_class(&tool_name);
                let parse_state = classify_parse_state(&tool_name, &input);
                intents.push(Intent {
                    intent_id: IntentId::new(),
                    process_id,
                    message_id: message_id.clone(),
                    tool_name,
                    tool_args,
                    intent_class,
                    parse_state,
                    primary_matching_eligible: false,
                    authority_source: IntentAuthoritySource::TextFallback,
                    timestamp_ns,
                    native_tool_call: None,
                    declared_conversation_id: None,
                });
            }
        }

        // Pattern 2: Bash code fences
        if intents.is_empty() {
            let bash_re = Regex::new(r"```(?:bash|shell|sh)\s*\n([\s\S]*?)```").unwrap();
            for caps in bash_re.captures_iter(body) {
                let command = caps.get(1).unwrap().as_str().trim().to_string();
                if command.len() < 3 {
                    continue;
                }
                let input = serde_json::json!({"command": command});
                intents.push(Intent {
                    intent_id: IntentId::new(),
                    process_id,
                    message_id: message_id.clone(),
                    tool_name: "Bash".to_string(),
                    tool_args: extract_intent_args("Bash", &input),
                    intent_class: IntentClass::Orchestration,
                    parse_state: IntentParseState::Complete,
                    primary_matching_eligible: false, // Lower confidence for regex matches
                    authority_source: IntentAuthoritySource::TextFallback,
                    timestamp_ns,
                    native_tool_call: None,
                    declared_conversation_id: None,
                });
            }
        }

        // Pattern 3: Write/Edit patterns (file_path + content)
        if intents.is_empty() {
            let write_re = Regex::new(r"(?i)(write|edit|create)\s+(?:file\s+)?(\S+\.\w+)").unwrap();
            // First match only, consistent with Pattern 1.
            if let Some(caps) = write_re.captures(body) {
                let file_path = caps.get(1).unwrap().as_str().to_string();
                let input = serde_json::json!({"file_path": file_path, "content": ""});
                let tool_name = "Write".to_string();
                intents.push(Intent {
                    intent_id: IntentId::new(),
                    process_id,
                    message_id: message_id.clone(),
                    tool_name: tool_name.clone(),
                    tool_args: extract_intent_args(&tool_name, &input),
                    intent_class: IntentClass::SideEffect,
                    parse_state: IntentParseState::Partial, // Content missing
                    primary_matching_eligible: false,
                    authority_source: IntentAuthoritySource::TextFallback,
                    timestamp_ns,
                    native_tool_call: None,
                    declared_conversation_id: None,
                });
            }
        }

        intents
    }

    // ── Internal helpers ────────────────────────────────────────────

    /// Convert an OpenAI `tool_calls[]` entry into an Intent.
    fn openai_tool_call_to_intent(
        tool_call: &serde_json::Value,
        source: IntentAuthoritySource,
        process_id: ProcessId,
        message_id: MessageId,
        timestamp_ns: u64,
    ) -> Option<Intent> {
        let function = tool_call.get("function")?;
        let tool_name = function.get("name")?.as_str()?.to_string();
        let args_str = function.get("arguments").and_then(|a| a.as_str());
        // A missing (or non-string) `arguments` field is unobserved input, not
        // an empty object. Keep it Null and Partial so no caller mistakes an
        // incomplete call for a complete one.
        let (input, args_observed) = match args_str {
            Some(args) => (
                serde_json::from_str(args).unwrap_or(serde_json::Value::Null),
                true,
            ),
            None => (serde_json::Value::Null, false),
        };

        let tool_args = extract_intent_args(&tool_name, &input);
        let intent_class = classify_intent_class(&tool_name);
        let parse_state = if args_observed {
            classify_parse_state(&tool_name, &input)
        } else {
            IntentParseState::Partial
        };

        Some(Intent {
            intent_id: IntentId::new(),
            process_id,
            message_id,
            tool_name,
            tool_args,
            intent_class,
            parse_state,
            primary_matching_eligible: true,
            authority_source: source,
            timestamp_ns,
            native_tool_call: captured_native_ref(
                tool_call.get("id"),
                NativeToolCallFormat::OpenAi,
            ),
            declared_conversation_id: None,
        })
    }

    /// Convert a tool_use JSON block into an Intent.
    fn block_to_intent(
        block: &serde_json::Value,
        source: IntentAuthoritySource,
        process_id: ProcessId,
        message_id: MessageId,
        timestamp_ns: u64,
    ) -> Option<Intent> {
        let tool_name = block.get("name")?.as_str()?.to_string();
        let input = block.get("input").unwrap_or(&serde_json::Value::Null);

        let tool_args = extract_intent_args(&tool_name, input);
        let intent_class = classify_intent_class(&tool_name);
        let parse_state = classify_parse_state(&tool_name, input);

        Some(Intent {
            intent_id: IntentId::new(),
            process_id,
            message_id,
            tool_name,
            tool_args,
            intent_class,
            parse_state,
            primary_matching_eligible: true,
            authority_source: source,
            timestamp_ns,
            native_tool_call: captured_native_ref(block.get("id"), NativeToolCallFormat::Anthropic),
            declared_conversation_id: None,
        })
    }
}

// ─── Tests ───────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn pid() -> ProcessId {
        ProcessId::new(100, 1)
    }

    fn msg_id() -> MessageId {
        MessageId::from("msg-001")
    }

    // ── Structured tool_use parsing ─────────────────────────────────

    #[test]
    fn test_extract_tool_use_bash() {
        let body = r#"{
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Bash", "id": "toolu_001", "input": {"command": "ls -la /etc"}}
            ]
        }"#;

        let intents = IntentExtractor::extract(
            body,
            IntentAuthoritySource::SseResponseToolUse,
            pid(),
            msg_id(),
            1000,
        );

        assert_eq!(intents.len(), 1);
        assert_eq!(intents[0].tool_name, "Bash");
        // `Bash` is a side effect: it performs an action rather than
        // coordinating agents, and the correlator must be able to link it to
        // the shell action it produces. See `types::tool_vocabulary`.
        assert_eq!(intents[0].intent_class, IntentClass::SideEffect);
        assert_eq!(intents[0].parse_state, IntentParseState::Complete);
        assert!(intents[0].primary_matching_eligible);
        assert!(!intents[0].tool_args.is_empty());
    }

    #[test]
    fn test_extract_tool_use_write() {
        let body = r#"{
            "content": [
                {"type": "tool_use", "name": "Write", "id": "toolu_002", "input": {"file_path": "/tmp/test.txt", "content": "hello"}}
            ]
        }"#;

        let intents = IntentExtractor::extract(
            body,
            IntentAuthoritySource::HttpResponseToolUse,
            pid(),
            msg_id(),
            2000,
        );

        assert_eq!(intents.len(), 1);
        assert_eq!(intents[0].tool_name, "Write");
        assert_eq!(intents[0].intent_class, IntentClass::SideEffect);
        assert_eq!(intents[0].parse_state, IntentParseState::Complete);
    }

    #[test]
    fn test_extract_tool_use_partial_input() {
        let body = r#"{
            "content": [
                {"type": "tool_use", "name": "Write", "id": "toolu_003", "input": {"file_path": "/tmp/x.txt"}}
            ]
        }"#;

        let intents = IntentExtractor::extract(
            body,
            IntentAuthoritySource::SseResponseToolUse,
            pid(),
            msg_id(),
            3000,
        );

        assert_eq!(intents.len(), 1);
        // Write requires file_path AND content → partial
        assert_eq!(intents[0].parse_state, IntentParseState::Partial);
    }

    #[test]
    fn test_extract_empty_body() {
        let intents = IntentExtractor::extract(
            "",
            IntentAuthoritySource::SseResponseToolUse,
            pid(),
            msg_id(),
            0,
        );
        assert!(intents.is_empty());
    }

    // ── SSE extraction ──────────────────────────────────────────────

    #[test]
    fn test_extract_sse_content_block() {
        let body = "\
data: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"tool_use\",\"name\":\"Bash\"}}
data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"input_json_delta\",\"partial_json\":\"{\\\"command\\\": \\\"ls\\\"}\"}}
data: {\"type\":\"content_block_stop\",\"index\":0}
";

        let intents = IntentExtractor::extract(
            body,
            IntentAuthoritySource::SseResponseToolUse,
            pid(),
            msg_id(),
            4000,
        );

        assert!(intents.len() >= 1);
        // Either the accumulated SSE produces an intent, or we get the partial
    }

    #[test]
    fn test_extract_sse_incomplete() {
        let body = "\
data: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"tool_use\",\"name\":\"Read\"}}
data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"input_json_delta\",\"partial_json\":\"{\\\"file_path\\\": \\\"\"}}
";

        let intents = IntentExtractor::extract(
            body,
            IntentAuthoritySource::SseResponseToolUse,
            pid(),
            msg_id(),
            5000,
        );

        // Incomplete accumulation without content_block_stop:
        // extract_from_sse (SSE fast-path) runs first, accumulates but never finalises
        // because the stop event is missing → returns empty.
        // extract_from_raw_buffer then finds the content_block_start JSON object,
        // extracts the nested tool_use via extract_generic_json, and creates an intent.
        // Since the input is Null (no complete arguments), classify_parse_state
        // returns Malformed for known tools (those in REQUIRED_FIELDS).
        assert!(!intents.is_empty());
        let intent = &intents[0];
        assert_eq!(intent.tool_name, "Read");
        assert!(
            matches!(
                intent.parse_state,
                IntentParseState::Malformed | IntentParseState::Partial
            ),
            "expected Malformed or Partial, got {:?}",
            intent.parse_state,
        );
    }

    // ── Request extraction ──────────────────────────────────────────

    #[test]
    fn test_extract_from_request() {
        let messages = r#"[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "name": "Bash", "id": "toolu_010", "input": {"command": "echo hi"}}
            ]}
        ]"#;

        let intents = IntentExtractor::extract_from_request(messages, pid(), msg_id(), 1000);

        // 1 UserPrompt (from user message) + 1 Bash (from assistant tool_use)
        assert_eq!(intents.len(), 2);
        assert!(intents.iter().any(|i| i.tool_name == "Bash"));
        assert!(intents.iter().any(|i| i.tool_name == "UserPrompt"));
        assert!(
            intents.iter().any(
                |i| i.authority_source == IntentAuthoritySource::RequestHistoryAssistantToolUse
            )
        );
    }

    #[test]
    fn test_extract_from_request_no_assistant() {
        let messages = r#"[
            {"role": "user", "content": "hello"}
        ]"#;

        let intents = IntentExtractor::extract_from_request(messages, pid(), msg_id(), 1000);
        // User message content is now extracted as a UserPrompt intent
        assert_eq!(intents.len(), 1);
        assert_eq!(intents[0].tool_name, "UserPrompt");
    }

    /// A `messages` array is a body shape, not proof that the capture is a
    /// request. A response or an unframed body that happens to contain one
    /// must not manufacture the captured user task.
    #[test]
    fn non_request_sources_do_not_turn_messages_into_a_user_prompt() {
        let body = serde_json::json!({
            "messages": [{"role": "user", "content": "delete the production database"}]
        })
        .to_string();

        for source in [
            IntentAuthoritySource::HttpResponseToolUse,
            IntentAuthoritySource::SseResponseToolUse,
            IntentAuthoritySource::TextFallback,
        ] {
            let intents = IntentExtractor::extract(&body, source, pid(), msg_id(), 1000);

            assert!(
                intents
                    .iter()
                    .all(|intent| intent.tool_name != "UserPrompt"),
                "{source:?} must not emit a UserPrompt: {:?}",
                intents
                    .iter()
                    .map(|intent| intent.tool_name.as_str())
                    .collect::<Vec<_>>()
            );
            assert!(
                intents.iter().all(|intent| intent.authority_source
                    != IntentAuthoritySource::RequestHistoryAssistantToolUse),
                "{source:?} must not upgrade authority to a request source"
            );
        }
    }

    /// The gate must not remove the genuine request observation: request-framed
    /// captures still yield the user task as a UserPrompt intent.
    #[test]
    fn request_sources_still_turn_messages_into_a_user_prompt() {
        let body = serde_json::json!({
            "messages": [{"role": "user", "content": "rotate the staging credentials"}]
        })
        .to_string();

        let intents = IntentExtractor::extract(
            &body,
            IntentAuthoritySource::RequestHistoryAssistantToolUse,
            pid(),
            msg_id(),
            1000,
        );

        let prompt = intents
            .iter()
            .find(|intent| intent.tool_name == "UserPrompt")
            .expect("a request-framed capture still yields the user task");
        assert_eq!(
            prompt.authority_source,
            IntentAuthoritySource::RequestHistoryAssistantToolUse
        );
    }

    fn request_tool_names(body: &str) -> Vec<String> {
        IntentExtractor::extract(
            body,
            IntentAuthoritySource::RequestHistoryAssistantToolUse,
            pid(),
            msg_id(),
            1000,
        )
        .into_iter()
        .filter(|intent| intent.tool_name != "UserPrompt")
        .map(|intent| intent.tool_name)
        .collect()
    }

    /// Tool declarations are a schema, not an assistant call. Scanning the
    /// generic tree finds `function.name` inside `tools` and used to promote
    /// Bash's parameter schema into an executable intent.
    #[test]
    fn request_tool_declarations_do_not_become_tool_calls() {
        let body = serde_json::json!({
            "messages": [{"role": "user", "content": "list the files"}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "Bash",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}}
                    }
                }
            }]
        })
        .to_string();

        let intents = IntentExtractor::extract(
            &body,
            IntentAuthoritySource::RequestHistoryAssistantToolUse,
            pid(),
            msg_id(),
            1000,
        );

        assert!(
            request_tool_names(&body).is_empty(),
            "tool declarations must not become calls: {intents:?}"
        );
        assert_eq!(intents.len(), 1);
        assert_eq!(intents[0].tool_name, "UserPrompt");
    }

    /// User text and tool-result payloads may contain tool-shaped data. Only
    /// assistant messages can supply calls.
    #[test]
    fn request_user_and_tool_payload_data_never_becomes_calls() {
        let body = serde_json::json!({
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "inspect this payload"},
                        {"type": "tool_use", "id": "toolu_data", "name": "Bash",
                         "input": {"command": "rm -rf /"}}
                    ]
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_data",
                    "content": [{
                        "type": "function",
                        "function": {"name": "Read", "arguments": "{\"file_path\":\"/etc/passwd\"}"}
                    }]
                }
            ]
        })
        .to_string();

        let intents = IntentExtractor::extract(
            &body,
            IntentAuthoritySource::RequestHistoryAssistantToolUse,
            pid(),
            msg_id(),
            1000,
        );

        assert!(
            request_tool_names(&body).is_empty(),
            "payload data must not become calls: {intents:?}"
        );
        assert_eq!(intents.len(), 1);
        assert_eq!(intents[0].tool_name, "UserPrompt");
    }

    /// Genuine assistant calls in either wire format survive the gate with
    /// their captured native references; declared schema names stay out.
    #[test]
    fn request_preserves_assistant_anthropic_and_openai_calls() {
        let body = serde_json::json!({
            "messages": [
                {"role": "user", "content": "do the thing"},
                {"role": "assistant", "content": [{
                    "type": "tool_use", "id": "toolu_real", "name": "Read",
                    "input": {"file_path": "/tmp/in"}
                }]},
                {"role": "assistant", "content": null, "tool_calls": [{
                    "id": "call_real", "type": "function",
                    "function": {"name": "Bash", "arguments": "{\"command\":\"echo hi\"}"}
                }]}
            ],
            "tools": [{
                "type": "function",
                "function": {"name": "Write", "parameters": {"type": "object"}}
            }]
        })
        .to_string();

        let intents = IntentExtractor::extract(
            &body,
            IntentAuthoritySource::RequestHistoryAssistantToolUse,
            pid(),
            msg_id(),
            1000,
        );

        assert_eq!(
            intents
                .iter()
                .filter(|i| i.tool_name == "UserPrompt")
                .count(),
            1
        );
        let calls: Vec<_> = intents
            .iter()
            .filter(|i| i.tool_name != "UserPrompt")
            .collect();
        assert_eq!(calls.len(), 2, "only actual assistant calls: {calls:?}");
        assert!(calls.iter().all(|i| i.tool_name != "Write"));

        let read = calls
            .iter()
            .find(|i| i.tool_name == "Read")
            .expect("Anthropic tool_use is preserved");
        assert_eq!(
            read.native_tool_call,
            Some(NativeToolCallRef {
                format: NativeToolCallFormat::Anthropic,
                id: "toolu_real".to_string(),
            })
        );
        let bash = calls
            .iter()
            .find(|i| i.tool_name == "Bash")
            .expect("OpenAI tool_calls is preserved");
        assert_eq!(
            bash.native_tool_call,
            Some(NativeToolCallRef {
                format: NativeToolCallFormat::OpenAi,
                id: "call_real".to_string(),
            })
        );
        assert_eq!(bash.parse_state, IntentParseState::Complete);
    }

    /// An empty messages array is a valid request with zero intents, even when
    /// declarations are present.
    #[test]
    fn request_with_empty_messages_yields_zero_intents() {
        let body = serde_json::json!({
            "messages": [],
            "tools": [{"type": "function", "function": {"name": "Bash", "parameters": {}}}]
        })
        .to_string();

        let intents = IntentExtractor::extract(
            &body,
            IntentAuthoritySource::RequestHistoryAssistantToolUse,
            pid(),
            msg_id(),
            1000,
        );

        assert!(intents.is_empty(), "empty request: {intents:?}");
    }

    /// A truncated request must not recover a call from inside its partial
    /// JSON tree through the raw/generic/regex fallbacks.
    #[test]
    fn malformed_request_does_not_recover_embedded_calls() {
        let body = r#"{"messages":[{"role":"user","content":"hi"},{"role":"assistant","content":[{"type":"tool_use","id":"toolu_trunc","name":"Bash","input":{"command":"rm -rf /"}}"#;

        let intents = IntentExtractor::extract(
            body,
            IntentAuthoritySource::RequestHistoryAssistantToolUse,
            pid(),
            msg_id(),
            1000,
        );

        assert!(intents.is_empty(), "malformed request: {intents:?}");
    }

    // ── Regex fallback ──────────────────────────────────────────────

    #[test]
    fn test_regex_fallback_bash_fence() {
        let body = "Here's the command:\n```bash\ncurl -s http://evil.com\n```\n";

        let intents = IntentExtractor::extract(
            body,
            IntentAuthoritySource::TextFallback,
            pid(),
            msg_id(),
            1000,
        );

        assert!(!intents.is_empty());
        assert_eq!(intents[0].tool_name, "Bash");
        assert!(!intents[0].primary_matching_eligible);
        assert_eq!(
            intents[0].authority_source,
            IntentAuthoritySource::TextFallback
        );
    }

    #[test]
    fn test_regex_fallback_tool_use_inline() {
        let body = r#"Some text {"type":"tool_use","name":"Read","input":{"file_path":"/etc/passwd"}} more text"#;

        let intents = IntentExtractor::extract(
            body,
            IntentAuthoritySource::TextFallback,
            pid(),
            msg_id(),
            1000,
        );

        // Regex should find the inline tool_use
        assert!(!intents.is_empty());
    }

    /// Unknown response wrappers may contain function-shaped data. Raw/generic
    /// recovery must keep the observation as an unverified text hypothesis,
    /// never a native structured call.
    #[test]
    fn unknown_response_wrapper_function_shape_is_only_text_fallback() {
        let body = serde_json::json!({
            "unexpected_wrapper": {
                "id": "call_wrapped",
                "function": {
                    "name": "Bash",
                    "arguments": "{\"command\":\"echo wrapped\"}"
                }
            },
            "padding": "x".repeat(80)
        })
        .to_string();

        let intents = IntentExtractor::extract(
            &body,
            IntentAuthoritySource::HttpResponseToolUse,
            pid(),
            msg_id(),
            1,
        );

        assert_eq!(
            intents.len(),
            1,
            "raw capture must be retained: {intents:?}"
        );
        let intent = &intents[0];
        assert_eq!(intent.tool_name, "Bash");
        assert_eq!(intent.parse_state, IntentParseState::Complete);
        assert!(
            intent
                .tool_args
                .contains(&NormalizedArg::Content("echo wrapped".into())),
            "parsed arguments stay as the hypothesis: {:?}",
            intent.tool_args
        );
        assert_eq!(intent.authority_source, IntentAuthoritySource::TextFallback);
        assert!(!intent.primary_matching_eligible);
        assert!(intent.native_tool_call.is_none());
    }

    /// Known response formats keep their structured provenance even when the
    /// body is large enough to have previously hit the raw-buffer scan first.
    #[test]
    fn response_echoed_history_is_not_a_new_native_call() {
        let body = serde_json::json!({"messages": [
            {"role":"assistant","content":[{"type":"tool_use","id":"old-call",
                "name":"Bash","input":{"command":"echo historical call"}}]}
        ]})
        .to_string();
        let intents = IntentExtractor::extract(
            &body,
            IntentAuthoritySource::HttpResponseToolUse,
            pid(),
            msg_id(),
            1,
        );
        assert!(
            !intents.is_empty(),
            "weak content recovery may remain available"
        );
        assert!(
            intents.iter().all(|intent| intent.authority_source
                == IntentAuthoritySource::TextFallback
                && !intent.primary_matching_eligible
                && intent.native_tool_call.is_none()),
            "an echoed messages array is not a supported new-call response envelope"
        );
    }

    #[test]
    fn known_response_formats_stay_structured_and_eligible() {
        let anthropic = serde_json::json!({
            "content": [{
                "type": "tool_use",
                "id": "toolu_known",
                "name": "Bash",
                "input": {"command": "echo known"}
            }],
            "padding": "x".repeat(80)
        })
        .to_string();
        let openai = serde_json::json!({
            "choices": [{"message": {"tool_calls": [{
                "id": "call_known",
                "function": {"name": "Bash", "arguments": "{\"command\":\"echo known\"}"}
            }]}}],
            "padding": "x".repeat(80)
        })
        .to_string();

        for (body, format, id) in [
            (anthropic, NativeToolCallFormat::Anthropic, "toolu_known"),
            (openai, NativeToolCallFormat::OpenAi, "call_known"),
        ] {
            assert!(body.len() > 64);
            let intents = IntentExtractor::extract(
                &body,
                IntentAuthoritySource::HttpResponseToolUse,
                pid(),
                msg_id(),
                1,
            );
            assert_eq!(intents.len(), 1, "{format:?}: {intents:?}");
            assert_eq!(
                intents[0].authority_source,
                IntentAuthoritySource::HttpResponseToolUse
            );
            assert!(intents[0].primary_matching_eligible);
            assert_eq!(
                intents[0].native_tool_call,
                Some(NativeToolCallRef {
                    format,
                    id: id.into(),
                })
            );
            assert_eq!(intents[0].parse_state, IntentParseState::Complete);
            assert!(!intents[0].tool_args.is_empty());
        }
    }

    /// A response `tools` declaration is a schema. Without an `arguments`
    /// string, its function shape must not become a call.
    #[test]
    fn response_tool_schema_without_arguments_is_not_a_call() {
        let body = serde_json::json!({
            "id": "decl-1",
            "tools": [{
                "type": "function",
                "function": {
                    "name": "Bash",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}}
                    }
                }
            }],
            "padding": "x".repeat(80)
        })
        .to_string();

        let intents = IntentExtractor::extract(
            &body,
            IntentAuthoritySource::HttpResponseToolUse,
            pid(),
            msg_id(),
            1,
        );

        assert!(
            intents.is_empty(),
            "schema declarations are not calls: {intents:?}"
        );
    }

    /// A structured OpenAI call with no `arguments` field keeps the native
    /// reference but must not synthesize `{}` and report Complete.
    #[test]
    fn openai_call_without_arguments_stays_partial() {
        for tool_name in ["Bash", "UnknownTool"] {
            let body = serde_json::json!({
                "choices": [{"message": {"tool_calls": [{
                    "id": "call_noargs",
                    "type": "function",
                    "function": {"name": tool_name}
                }]}}],
                "padding": "x".repeat(80)
            })
            .to_string();

            let intents = IntentExtractor::extract(
                &body,
                IntentAuthoritySource::HttpResponseToolUse,
                pid(),
                msg_id(),
                1,
            );

            assert_eq!(intents.len(), 1, "{tool_name}: {intents:?}");
            assert_eq!(
                intents[0].parse_state,
                IntentParseState::Partial,
                "{tool_name} missing arguments must not be Complete"
            );
            assert_eq!(
                intents[0].authority_source,
                IntentAuthoritySource::HttpResponseToolUse
            );
            assert!(intents[0].primary_matching_eligible);
        }
    }

    /// A structured response recovered from an arbitrary offset is still an
    /// observation, but only a weak text hypothesis.
    #[test]
    fn response_raw_offset_recovery_is_a_weak_candidate() {
        let body = format!(
            "prefix-before-json {}",
            r#"{"content":[{"type":"tool_use","id":"toolu_offset","name":"Bash","input":{"command":"echo offset"}}]}"#
        );

        let intents = IntentExtractor::extract(
            &body,
            IntentAuthoritySource::HttpResponseToolUse,
            pid(),
            msg_id(),
            1,
        );

        assert_eq!(
            intents.len(),
            1,
            "raw capture must be retained: {intents:?}"
        );
        assert_eq!(intents[0].parse_state, IntentParseState::Complete);
        assert_eq!(
            intents[0].authority_source,
            IntentAuthoritySource::TextFallback
        );
        assert!(!intents[0].primary_matching_eligible);
        assert!(intents[0].native_tool_call.is_none());
    }

    /// The regex recovery has no native provenance, including the inline
    /// `tool_use` branch that used to be marked primary.
    #[test]
    fn regex_inline_tool_use_is_not_primary() {
        let body = r#"text {"type":"tool_use","name":"Read","input":{"file_path":"/etc/passwd"}"#;

        let intents = IntentExtractor::extract(
            body,
            IntentAuthoritySource::TextFallback,
            pid(),
            msg_id(),
            1,
        );

        assert_eq!(
            intents.len(),
            1,
            "regex recovery must be retained: {intents:?}"
        );
        assert_eq!(
            intents[0].authority_source,
            IntentAuthoritySource::TextFallback
        );
        assert!(!intents[0].primary_matching_eligible);
        assert!(intents[0].native_tool_call.is_none());
    }

    // ── required_fields ─────────────────────────────────────────────

    #[test]
    fn test_required_fields_bash() {
        let fields = IntentExtractor::required_fields("Bash");
        assert!(fields.contains(&"command"));
    }

    #[test]
    fn test_required_fields_unknown() {
        let fields = IntentExtractor::required_fields("UnknownTool");
        assert!(fields.is_empty());
    }

    // ── Parse state classification ──────────────────────────────────

    #[test]
    fn test_parse_state_complete_vs_partial() {
        let full = serde_json::json!({"command": "ls", "description": "list"});
        let partial = serde_json::json!({});
        // Bash requires "command"
        assert_eq!(
            classify_parse_state("Bash", &full),
            IntentParseState::Complete
        );
        assert_eq!(
            classify_parse_state("Bash", &partial),
            IntentParseState::Partial
        );
    }

    /// A body above the structured-parse limit is observable, not silently
    /// ignored. The counter is what lets an operator tell "the runtime sent a
    /// 3 MB prompt, so we skipped JSON parsing" from "no LLM traffic".
    #[test]
    fn oversized_body_increments_diagnostic_counter() {
        let before = DIAG_BODY_TOO_LARGE.load(Ordering::Relaxed);
        let body = " ".repeat(MAX_JSON_PARSE_BYTES + 1);
        let _ = IntentExtractor::extract(
            &body,
            IntentAuthoritySource::HttpResponseToolUse,
            ProcessId {
                pid: 1,
                start_time_ns: 0,
            },
            MessageId::new(),
            0,
        );
        assert!(
            DIAG_BODY_TOO_LARGE.load(Ordering::Relaxed) > before,
            "oversized body must be counted"
        );
    }

    /// The table itself is covered by `types::tool_vocabulary`'s tests; this
    /// only pins that the wrapper used by the extraction path reaches it.
    #[test]
    fn test_intent_class_classification() {
        assert_eq!(classify_intent_class("Bash"), IntentClass::SideEffect);
        assert_eq!(classify_intent_class("Task"), IntentClass::Orchestration);
        assert_eq!(classify_intent_class("Write"), IntentClass::SideEffect);
        assert_eq!(classify_intent_class("Read"), IntentClass::SideEffect);
        assert_eq!(
            classify_intent_class("SomeRandomTool"),
            IntentClass::Unknown
        );
    }

    #[test]
    fn identical_anthropic_arguments_keep_distinct_native_ids() {
        let body = r#"{"content": [
            {"type": "tool_use", "id": "toolu_a", "name": "Bash", "input": {"command": "ls"}},
            {"type": "tool_use", "id": "toolu_b", "name": "Bash", "input": {"command": "ls"}}
        ]}"#;
        let intents = IntentExtractor::extract(
            body,
            IntentAuthoritySource::HttpResponseToolUse,
            pid(),
            msg_id(),
            1,
        );
        assert_eq!(intents.len(), 2);
        assert_eq!(
            intents[0].native_tool_call,
            Some(NativeToolCallRef {
                format: NativeToolCallFormat::Anthropic,
                id: "toolu_a".into(),
            })
        );
        assert_eq!(
            intents[1].native_tool_call,
            Some(NativeToolCallRef {
                format: NativeToolCallFormat::Anthropic,
                id: "toolu_b".into(),
            })
        );
    }

    #[test]
    fn anthropic_sse_completion_keeps_content_block_id() {
        let body = concat!(
            "data: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":",
            "{\"type\":\"tool_use\",\"id\":\"toolu_sse\",\"name\":\"Bash\"}}\n",
            "data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":",
            "{\"type\":\"input_json_delta\",\"partial_json\":\"{\\\"command\\\":\\\"ls\\\"}\"}}\n",
            "data: {\"type\":\"content_block_stop\",\"index\":0}\n"
        );
        let intents = IntentExtractor::extract(
            body,
            IntentAuthoritySource::SseResponseToolUse,
            pid(),
            msg_id(),
            1,
        );
        assert_eq!(intents.len(), 1);
        assert_eq!(
            intents[0].native_tool_call,
            Some(NativeToolCallRef {
                format: NativeToolCallFormat::Anthropic,
                id: "toolu_sse".into(),
            })
        );
    }

    #[test]
    fn anthropic_sse_start_with_empty_input_and_stop_keeps_native_call() {
        let body = concat!(
            "data: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":",
            "{\"type\":\"tool_use\",\"id\":\"toolu_empty\",\"name\":\"Bash\",\"input\":{}}}\n",
            "data: {\"type\":\"content_block_stop\",\"index\":0}\n"
        );
        let intents = IntentExtractor::extract_from_sse(
            body,
            IntentAuthoritySource::SseResponseToolUse,
            pid(),
            msg_id(),
            1,
        );
        assert_eq!(intents.len(), 1, "an observed call must not disappear");
        assert_eq!(intents[0].tool_name, "Bash");
        assert_eq!(
            intents[0].native_tool_call,
            Some(NativeToolCallRef {
                format: NativeToolCallFormat::Anthropic,
                id: "toolu_empty".into(),
            })
        );
        assert_eq!(intents[0].parse_state, IntentParseState::Partial);
    }

    #[test]
    fn anthropic_sse_start_with_full_input_and_stop_preserves_arguments() {
        let body = concat!(
            "data: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":",
            "{\"type\":\"tool_use\",\"id\":\"toolu_initial\",\"name\":\"Bash\",",
            "\"input\":{\"command\":\"echo initial\"}}}\n",
            "data: {\"type\":\"content_block_stop\",\"index\":0}\n"
        );
        let intents = IntentExtractor::extract_from_sse(
            body,
            IntentAuthoritySource::SseResponseToolUse,
            pid(),
            msg_id(),
            1,
        );
        assert_eq!(intents.len(), 1);
        assert_eq!(intents[0].tool_name, "Bash");
        assert_eq!(intents[0].parse_state, IntentParseState::Complete);
        assert!(
            intents[0]
                .tool_args
                .contains(&NormalizedArg::Content("echo initial".into())),
            "initial input arguments must survive: {:?}",
            intents[0].tool_args
        );
        assert_eq!(
            intents[0].native_tool_call,
            Some(NativeToolCallRef {
                format: NativeToolCallFormat::Anthropic,
                id: "toolu_initial".into(),
            })
        );
    }

    #[test]
    fn anthropic_sse_without_stop_keeps_partial_state_even_for_valid_input() {
        let start = serde_json::json!({"type":"content_block_start","index":0,
            "content_block":{"type":"tool_use","id":"tool-pending","name":"Bash","input":{"command":"echo pending"}}});
        let body = format!("data: {start}\n\n");
        let intents = IntentExtractor::extract_from_sse(
            &body,
            IntentAuthoritySource::SseResponseToolUse,
            pid(),
            msg_id(),
            1,
        );
        assert_eq!(intents.len(), 1);
        assert_eq!(intents[0].parse_state, IntentParseState::Partial);
        assert_eq!(
            intents[0].native_tool_call.as_ref().unwrap().id,
            "tool-pending"
        );
        assert!(!intents[0].tool_args.is_empty());
    }

    #[test]
    fn anthropic_sse_malformed_delta_with_stop_matches_stream_tail() {
        let start = concat!(
            "data: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":",
            "{\"type\":\"tool_use\",\"id\":\"toolu_bad\",\"name\":\"Bash\"}}\n",
            "data: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":",
            "{\"type\":\"input_json_delta\",\"partial_json\":\"{\\\"command\\\":\"}}\n"
        );
        for body in [
            format!("{start}data: {{\"type\":\"content_block_stop\",\"index\":0}}\n"),
            start.to_string(),
        ] {
            let intents = IntentExtractor::extract_from_sse(
                &body,
                IntentAuthoritySource::SseResponseToolUse,
                pid(),
                msg_id(),
                1,
            );
            assert_eq!(intents.len(), 1);
            assert_eq!(intents[0].tool_name, "Bash");
            assert_eq!(intents[0].parse_state, IntentParseState::Partial);
            assert_eq!(
                intents[0].native_tool_call,
                Some(NativeToolCallRef {
                    format: NativeToolCallFormat::Anthropic,
                    id: "toolu_bad".into(),
                })
            );
            // Partial does not prove the arguments were observed.
            assert!(intents[0].tool_args.is_empty());
        }
    }

    #[test]
    fn openai_json_and_sse_keep_native_ids() {
        let json = r#"{"choices": [{"message": {"tool_calls": [
            {"id": "call_json", "function": {"name": "Bash", "arguments": "{\"command\":\"ls\"}"}}
        ]}}]}"#;
        let from_json = IntentExtractor::extract(
            json,
            IntentAuthoritySource::HttpResponseToolUse,
            pid(),
            msg_id(),
            1,
        );
        assert_eq!(
            from_json[0].native_tool_call,
            Some(NativeToolCallRef {
                format: NativeToolCallFormat::OpenAi,
                id: "call_json".into(),
            })
        );

        let sse = concat!(
            "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,",
            "\"id\":\"call_sse\",\"function\":{\"name\":\"Bash\",",
            "\"arguments\":\"{\\\"command\\\":\"}}]}}]}\n",
            "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,",
            "\"function\":{\"arguments\":\"\\\"ls\\\"}\"}}]}}]}\n"
        );
        let from_sse = IntentExtractor::extract(
            sse,
            IntentAuthoritySource::SseResponseToolUse,
            pid(),
            msg_id(),
            1,
        );
        assert_eq!(from_sse.len(), 1);
        assert_eq!(
            from_sse[0].native_tool_call,
            Some(NativeToolCallRef {
                format: NativeToolCallFormat::OpenAi,
                id: "call_sse".into(),
            })
        );
    }

    #[test]
    fn openai_sse_choice_indices_keep_native_calls_separate() {
        let payload = serde_json::json!({"choices": [
            {"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call-a", "function": {"name": "Bash", "arguments": "{\"command\":\"echo a\"}"}}]}},
            {"index": 1, "delta": {"tool_calls": [{"index": 0, "id": "call-b", "function": {"name": "Bash", "arguments": "{\"command\":\"echo b\"}"}}]}}
        ]});
        let body = format!("data: {payload}\n\ndata: [DONE]\n\n");
        let intents = IntentExtractor::extract_from_openai_sse(
            &body,
            IntentAuthoritySource::SseResponseToolUse,
            pid(),
            msg_id(),
            1,
        );
        assert_eq!(intents.len(), 2, "tool indices are local to each choice");
        let ids: std::collections::HashSet<_> = intents
            .iter()
            .map(|intent| intent.native_tool_call.as_ref().unwrap().id.as_str())
            .collect();
        assert_eq!(ids, std::collections::HashSet::from(["call-a", "call-b"]));
    }

    #[test]
    fn absent_or_empty_native_ids_stay_none() {
        let body = r#"{"content": [
            {"type": "tool_use", "id": "", "name": "Bash", "input": {"command": "ls"}},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/x"}}
        ]}"#;
        let intents = IntentExtractor::extract(
            body,
            IntentAuthoritySource::HttpResponseToolUse,
            pid(),
            msg_id(),
            1,
        );
        assert_eq!(intents.len(), 2);
        assert!(intents.iter().all(|i| i.native_tool_call.is_none()));
    }
}
