// SPDX-License-Identifier: MIT
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
    Intent, IntentAuthoritySource, IntentClass, IntentId, IntentParseState, MessageId, NormalizedArg,
    ProcessId,
};
use regex_lite::Regex;

/// Maximum body length to attempt structured JSON parsing (2 MB).
const MAX_JSON_PARSE_BYTES: usize = 2 * 1024 * 1024;

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
    if raw.starts_with("HTTP/") {
        if let Some(pos) = raw.find("\r\n\r\n") {
            return &raw[pos + 4..];
        }
        // Malformed: no double-CRLF found — return as-is
    }

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

// ─── IntentClass helpers ──────────────────────────────────────────────

/// Tools that orchestrate work (agent coordination, task planning).
const ORCHESTRATION_TOOLS: &[&str] = &[
    "Bash",
    "Task",
    "TodoWrite",
    "TaskOutput",
    "ExitPlanMode",
    "EnterPlanMode",
];

/// Tools that produce observable side effects.
const SIDE_EFFECT_TOOLS: &[&str] = &[
    "Write",
    "Edit",
    "Read",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
    "LS",
];

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
    if ORCHESTRATION_TOOLS.contains(&tool_name) {
        IntentClass::Orchestration
    } else if SIDE_EFFECT_TOOLS.contains(&tool_name) {
        IntentClass::SideEffect
    } else {
        IntentClass::Unknown
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
        // Step 0: SSE fast-path — route SSE streams directly to the SSE
        // parser BEFORE the raw buffer scan (step 1).
        //
        // The raw buffer scan extracts individual JSON objects from the
        // stream and passes them to extract_generic_json.  SSE content_block_start
        // objects contain {type: "tool_use", name: "..."} WITHOUT input —
        // the arguments arrive later in content_block_delta events.  A
        // partial match here short-circuits the entire extraction pipeline
        // and prevents the SSE stream parser (step 6) from ever running.
        //
        // By routing SSE data to the dedicated stream parser first we
        // guarantee that content_block_start/delta/stop sequences are
        // correctly accumulated into complete tool_use intents.
        if looks_like_sse(body) {
            let sse = Self::extract_from_sse(body, source, process_id, message_id.clone(), timestamp_ns);
            if !sse.is_empty() {
                return sse;
            }
            let sse = Self::extract_from_openai_sse(body, source, process_id, message_id.clone(), timestamp_ns);
            if !sse.is_empty() {
                return sse;
            }
        }

        // Step 1: Raw buffer scan — handles buffers that start at arbitrary offsets.
        if body.len() > 64 {
            let intents = Self::extract_from_raw_buffer(body, source, process_id, message_id.clone(), timestamp_ns);
            if !intents.is_empty() {
                return intents;
            }
        }

        // Step 1: Strip HTTP framing + de-chunk transfer encoding
        let clean = strip_http_framing(body);
        let dechunked = dechunk_body(&clean);
        let payload: &str = &dechunked;

        // Step 1a: Request body with messages array — extract user messages
        // BEFORE tool_use extraction, because assistant messages in the
        // conversation history contain tool_use blocks that would return
        // early from Steps 2-6.
        if payload.len() <= MAX_JSON_PARSE_BYTES && looks_like_json(payload) {
            if let Ok(root) = serde_json::from_str::<serde_json::Value>(payload) {
                if let Some(messages) = root.get("messages").and_then(|m| m.as_array()).cloned() {
                    if !messages.is_empty() {
                        let messages_str = serde_json::to_string(&messages).unwrap_or_default();
                        let request_intents = Self::extract_from_request(
                            &messages_str, process_id, message_id.clone(), timestamp_ns,
                        );
                        if !request_intents.is_empty() {
                            return request_intents;
                        }
                    }
                }
            }
        }

        // Step 2: Anthropic structured JSON (content blocks)
        if payload.len() <= MAX_JSON_PARSE_BYTES && looks_like_json(payload) {
            let intents = Self::extract_from_json(payload, source, process_id, message_id.clone(), timestamp_ns);
            if !intents.is_empty() {
                return intents;
            }
        }

        // Step 3: OpenAI structured JSON
        if payload.len() <= MAX_JSON_PARSE_BYTES && looks_like_json(payload) {
            let intents = Self::extract_from_openai_json(payload, source, process_id, message_id.clone(), timestamp_ns);
            if !intents.is_empty() {
                return intents;
            }
        }

        // Step 4: Generic JSON tree scan (catch-all for unknown formats)
        if payload.len() <= MAX_JSON_PARSE_BYTES && looks_like_json(payload) {
            let intents = Self::extract_generic_json(payload, source, process_id, message_id.clone(), timestamp_ns);
            if !intents.is_empty() {
                return intents;
            }
        }

        // Step 5: Anthropic SSE + OpenAI SSE
        if looks_like_sse(payload) {
            let sse = Self::extract_from_sse(payload, source, process_id, message_id.clone(), timestamp_ns);
            if !sse.is_empty() {
                return sse;
            }
            let sse = Self::extract_from_openai_sse(payload, source, process_id, message_id.clone(), timestamp_ns);
            if !sse.is_empty() {
                return sse;
            }
        }

        // Step 6: If we haven't already tried the raw body with raw buffer scan,
        // and it's different from the payload version, try generic JSON on it.
        if payload.as_ptr() != body.as_ptr() && body.len() <= MAX_JSON_PARSE_BYTES {
            let intents = Self::extract_generic_json(body, source, process_id, message_id.clone(), timestamp_ns);
            if !intents.is_empty() {
                return intents;
            }
        }

        // Step 7: Regex fallback (always runs last, on de-chunked payload)
        let regex_intents = Self::extract_regex_fallback(payload, source, process_id, message_id.clone(), timestamp_ns);
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

        let mut intents = Vec::new();

        for msg in messages {
            let role = msg.get("role").and_then(|r| r.as_str()).unwrap_or("");

            if role == "user" {
                // Extract user message text as a content-bearing intent
                // so the ContentBoundary can detect injection/forgery
                // patterns in the initial task prompt.
                let user_text = match msg.get("content") {
                    Some(serde_json::Value::String(s)) => s.clone(),
                    Some(serde_json::Value::Array(arr)) => {
                        arr.iter()
                            .filter_map(|b| {
                                if b.get("type").and_then(|t| t.as_str()) == Some("text") {
                                    b.get("text").and_then(|t| t.as_str()).map(|s| s.to_string())
                                } else {
                                    None
                                }
                            })
                            .collect::<Vec<_>>()
                            .join("\n")
                    }
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
                    });
                }
                continue;
            }

            if role != "assistant" {
                continue;
            }

            let content = match msg.get("content") {
                Some(serde_json::Value::Array(arr)) => arr,
                _ => continue,
            };

            for block in content {
                if block.get("type").and_then(|t| t.as_str()) == Some("tool_use") {
                    if let Some(intent) = Self::block_to_intent(
                        block,
                        IntentAuthoritySource::RequestHistoryAssistantToolUse,
                        process_id,
                        message_id.clone(),
                        timestamp_ns,
                    ) {
                        intents.push(intent);
                    }
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
                if block.get("type").and_then(|t| t.as_str()) == Some("tool_use") {
                    if let Some(intent) = Self::block_to_intent(
                        block,
                        source,
                        process_id,
                        message_id.clone(),
                        timestamp_ns,
                    ) {
                        intents.push(intent);
                    }
                }
            }
            return intents;
        }

        // Case B: Single content block (e.g., from extracted delta)
        if value.get("type").and_then(|t| t.as_str()) == Some("tool_use") {
            if let Some(intent) = Self::block_to_intent(
                &value,
                source,
                process_id,
                message_id,
                timestamp_ns,
            ) {
                intents.push(intent);
            }
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
                let function = match tc.get("function") {
                    Some(f) => f,
                    None => continue,
                };
                let tool_name = match function.get("name").and_then(|n| n.as_str()) {
                    Some(n) => n.to_string(),
                    None => continue,
                };
                let args_str = function.get("arguments").and_then(|a| a.as_str()).unwrap_or("{}");
                let input: serde_json::Value = serde_json::from_str(args_str).unwrap_or(serde_json::Value::Null);

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
                    primary_matching_eligible: true,
                    authority_source: source,
                    timestamp_ns,
                });
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
        // Accumulate partial tool_calls: index → (name, arguments_string)
        let mut accum: std::collections::HashMap<i64, (String, String)> = std::collections::HashMap::new();

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

            for choice in choices {
                let delta = match choice.get("delta") {
                    Some(d) => d,
                    None => continue,
                };
                let tool_calls = match delta.get("tool_calls").and_then(|tc| tc.as_array()) {
                    Some(tc) => tc,
                    None => continue,
                };
                for tc in tool_calls {
                    let index = tc.get("index").and_then(|i| i.as_i64()).unwrap_or(0);
                    let function = match tc.get("function") {
                        Some(f) => f,
                        None => continue,
                    };

                    // Accumulate name if present
                    if let Some(name) = function.get("name").and_then(|n| n.as_str()) {
                        accum.entry(index)
                            .and_modify(|(n, _)| *n = name.to_string())
                            .or_insert((name.to_string(), String::new()));
                    }

                    // Accumulate arguments fragment
                    if let Some(args) = function.get("arguments").and_then(|a| a.as_str()) {
                        accum.entry(index)
                            .and_modify(|(_, a)| a.push_str(args))
                            .or_insert((String::new(), args.to_string()));
                    }
                }
            }
        }

        // Emit completed accumulations
        for (_index, (tool_name, args_str)) in &accum {
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
        Self::scan_json_for_tools(&value, source, process_id, &message_id, timestamp_ns, &mut intents);
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
                if obj.get("type").and_then(|t| t.as_str()) == Some("tool_use") {
                    if let Some(name) = obj.get("name").and_then(|n| n.as_str()) {
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
                        });
                        return; // Don't recurse into the tool_use block
                    }
                }

                // Pattern B: OpenAI function call → has function.name
                if let Some(function) = obj.get("function") {
                    if let Some(name) = function.get("name").and_then(|n| n.as_str()) {
                        let args_str = function.get("arguments")
                            .and_then(|a| a.as_str())
                            .unwrap_or("{}");
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
                        });
                    }
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
            let tries: [(&str, fn(&str, IntentAuthoritySource, ProcessId, MessageId, u64) -> Vec<Intent>); 3] = [
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
                    let sse = IntentExtractor::extract_from_sse(chunk, source, process_id, message_id.clone(), timestamp_ns);
                    if !sse.is_empty() {
                        intents.extend(sse);
                    }
                }
            }

            pos = end;
        }

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
        // Accumulate partial tool_use definitions across SSE events.
        // Keyed by content block index; stores (tool_name, accumulated_input_json).
        let mut accum: std::collections::HashMap<u64, (String, String)> = std::collections::HashMap::new();

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
                        accum.insert(index, (tool_name, String::new()));
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
                        entry.1.push_str(partial);
                    }
                }

                "content_block_stop" => {
                    let index = event.get("index").and_then(|i| i.as_u64()).unwrap_or(0);
                    if let Some((tool_name, input_json)) = accum.remove(&index) {
                        if let Ok(input) = serde_json::from_str::<serde_json::Value>(&input_json) {
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
                                primary_matching_eligible: true,
                                authority_source: source,
                                timestamp_ns,
                            });
                        }
                    }
                }

                _ => {}
            }
        }

        // Emit any incomplete accumulations as partial intents
        for (_index, (tool_name, input_json)) in accum {
            let input: serde_json::Value = serde_json::from_str(&input_json).unwrap_or(serde_json::Value::Null);
            let tool_args = extract_intent_args(&tool_name, &input);
            let intent_class = classify_intent_class(&tool_name);
            let parse_state = if input_json.is_empty() {
                IntentParseState::Partial
            } else {
                // We have partial input — mark as Partial
                IntentParseState::Partial
            };

            intents.push(Intent {
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
            });
        }

        intents
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
        source: IntentAuthoritySource,
        process_id: ProcessId,
        message_id: MessageId,
        timestamp_ns: u64,
    ) -> Vec<Intent> {
        let mut intents = Vec::new();

        // Pattern 1: JSON tool_use blocks inline
        let tool_use_re = Regex::new(
            r#""type"\s*:\s*"tool_use"[^}]*"name"\s*:\s*"([^"]+)"[^}]*"input"\s*:\s*(\{[^}]+\})"#
        ).unwrap();
        for caps in tool_use_re.captures_iter(body) {
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
                    primary_matching_eligible: true,
                    authority_source: IntentAuthoritySource::TextFallback,
                    timestamp_ns,
                });
            }
            break; // Only first match for now — prevent over-matching
        }

        // Pattern 2: Bash code fences
        if intents.is_empty() {
            let bash_re =
                Regex::new(r"```(?:bash|shell|sh)\s*\n([\s\S]*?)```").unwrap();
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
                });
            }
        }

        // Pattern 3: Write/Edit patterns (file_path + content)
        if intents.is_empty() {
            let write_re = Regex::new(
                r"(?i)(write|edit|create)\s+(?:file\s+)?(\S+\.\w+)"
            ).unwrap();
            for caps in write_re.captures_iter(body) {
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
                });
                break;
            }
        }

        intents
    }

    // ── Internal helpers ────────────────────────────────────────────

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
        assert_eq!(intents[0].intent_class, IntentClass::Orchestration);
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
            matches!(intent.parse_state, IntentParseState::Malformed | IntentParseState::Partial),
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

        let intents =
            IntentExtractor::extract_from_request(messages, pid(), msg_id(), 1000);

        // 1 UserPrompt (from user message) + 1 Bash (from assistant tool_use)
        assert_eq!(intents.len(), 2);
        assert!(intents.iter().any(|i| i.tool_name == "Bash"));
        assert!(intents.iter().any(|i| i.tool_name == "UserPrompt"));
        assert!(
            intents.iter().any(|i| i.authority_source == IntentAuthoritySource::RequestHistoryAssistantToolUse)
        );
    }

    #[test]
    fn test_extract_from_request_no_assistant() {
        let messages = r#"[
            {"role": "user", "content": "hello"}
        ]"#;

        let intents =
            IntentExtractor::extract_from_request(messages, pid(), msg_id(), 1000);
        // User message content is now extracted as a UserPrompt intent
        assert_eq!(intents.len(), 1);
        assert_eq!(intents[0].tool_name, "UserPrompt");
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

    #[test]
    fn test_intent_class_classification() {
        // Orchestration
        assert_eq!(
            classify_intent_class("Bash"),
            IntentClass::Orchestration
        );
        assert_eq!(
            classify_intent_class("Task"),
            IntentClass::Orchestration
        );

        // Side effect
        assert_eq!(
            classify_intent_class("Write"),
            IntentClass::SideEffect
        );
        assert_eq!(
            classify_intent_class("Read"),
            IntentClass::SideEffect
        );

        // Unknown
        assert_eq!(
            classify_intent_class("SomeRandomTool"),
            IntentClass::Unknown
        );
    }
}
