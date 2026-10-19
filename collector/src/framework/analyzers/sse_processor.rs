// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 eunomia-bpf org.

use super::{Analyzer, AnalyzerError};
use crate::framework::core::Event;
use crate::framework::runners::EventStream;
use async_trait::async_trait;
use futures::stream::StreamExt;
use serde_json::{Value, json};
use std::collections::HashMap;
use std::io::Write;
use std::sync::{Arc, Mutex};

use super::event::SSEProcessorEvent;

/// SSE Event Processor that merges Server-Sent Events content fragments
pub struct SSEProcessor {
    /// Store accumulated SSE content by connection + message ID
    pub(crate) sse_buffers: Arc<Mutex<HashMap<String, SSEAccumulator>>>,
    /// Timeout for incomplete SSE streams (in milliseconds)
    timeout_ms: u64,
    /// Enable debug output (matches Python quiet flag)
    debug: bool,
}

/// Accumulator for SSE events belonging to the same message
pub(crate) struct SSEAccumulator {
    pub(crate) message_id: Option<String>,
    pub(crate) accumulated_text: String,
    /// Per-block-index accumulated JSON (fix: was single String concatenating all blocks)
    pub(crate) accumulated_json: HashMap<usize, String>,
    /// Current content block index (from content_block_start)
    pub(crate) current_block_index: Option<usize>,
    pub(crate) events: Vec<SSEEvent>,
    pub(crate) is_complete: bool,
    pub(crate) last_update: u64,
    pub(crate) has_message_start: bool,
    pub(crate) start_time: u64,
    pub(crate) end_time: u64,
}

/// Parsed SSE event - matches ssl_log_analyzer.py structure
#[derive(Clone, Debug)]
pub(crate) struct SSEEvent {
    pub(crate) event: Option<String>,
    pub(crate) data: Option<String>,
    pub(crate) id: Option<String>,
    pub(crate) parsed_data: Option<Value>,
    pub(crate) raw_data: Option<String>,
}

impl SSEProcessor {
    /// Create a new SSEProcessor with default timeout (30 seconds)
    #[allow(dead_code)]
    pub fn new() -> Self {
        Self::new_with_timeout(30_000)
    }

    /// Create a new SSEProcessor with custom timeout
    pub fn new_with_timeout(timeout_ms: u64) -> Self {
        SSEProcessor {
            sse_buffers: Arc::new(Mutex::new(HashMap::new())),
            timeout_ms,
            debug: false,
        }
    }

    /// Debug print function - only prints if debug is enabled (matches Python debug_print)
    fn debug_print(&self, message: &str) {
        if self.debug {
            eprintln!("{}", message);
            std::io::stdout().flush().unwrap();
        }
    }

    /// Check if SSL data contains SSE events - enhanced detection
    pub fn is_sse_data(data: &str) -> bool {
        // Look for SSE patterns in the data
        let has_sse_patterns = data.contains("event:") && data.contains("data:");

        // Also check for Content-Type: text/event-stream
        let has_sse_content_type = data.contains("text/event-stream");

        // Check for chunked encoding with SSE events
        let has_chunked_sse = data.contains("Transfer-Encoding: chunked")
            && (data.contains("event:") || data.contains("data:"));

        // Check for standalone data: field (SSE can have just data: without event:)
        let has_sse_data_only =
            data.contains("data:") && (data.contains("\r\n\r\n") || data.contains("\n\n"));

        has_sse_patterns || has_sse_content_type || has_chunked_sse || has_sse_data_only
    }

    /// Parse SSE events from a single chunk - matches ssl_log_analyzer.py parse_sse_events_from_chunk
    pub(crate) fn parse_sse_events_from_chunk(chunk_content: &str) -> Vec<SSEEvent> {
        let mut events = Vec::new();

        // Split by double newlines to separate events - matches Python: re.split(r'\n\s*\n', chunk_content)
        let event_blocks: Vec<&str> = chunk_content.split("\n\n").collect();

        for block in event_blocks {
            if block.trim().is_empty() {
                continue;
            }

            let mut event = SSEEvent {
                event: None,
                data: None,
                id: None,
                parsed_data: None,
                raw_data: None,
            };
            let mut data_lines = Vec::new();

            for line in block.split('\n') {
                let line = line.trim();
                if line.starts_with("event:") {
                    event.event = Some(line[6..].trim().to_string());
                } else if line.starts_with("data:") {
                    data_lines.push(line[5..].trim());
                } else if line.starts_with("id:") {
                    event.id = Some(line[3..].trim().to_string());
                }
            }

            if !data_lines.is_empty() {
                let combined_data = data_lines.join("\n");
                event.data = Some(combined_data.clone());

                // Try to parse as JSON
                match serde_json::from_str::<Value>(&combined_data) {
                    Ok(parsed_json) => {
                        event.parsed_data = Some(parsed_json);
                    }
                    Err(_) => {
                        event.raw_data = Some(combined_data);
                    }
                }
            }

            if event.event.is_some() || event.data.is_some() {
                events.push(event);
            }
        }

        events
    }

    /// Parse SSE events from raw SSL data
    pub(crate) fn parse_sse_events(data: &str) -> Vec<SSEEvent> {
        // Clean up chunked encoding first
        let clean_data = Self::clean_chunked_content(data);

        // Use the chunk parser
        Self::parse_sse_events_from_chunk(&clean_data)
    }

    /// Clean HTTP chunked encoding artifacts from content - matches ssl_log_analyzer.py logic
    pub fn clean_chunked_content(content: &str) -> String {
        let mut content_parts = Vec::new();
        let lines: Vec<&str> = content.split("\r\n").collect();

        // If no \r\n delimiter found, there is no HTTP chunked encoding to clean.
        // Return content as-is to preserve SSE data that uses only \n delimiters.
        if lines.len() <= 1 {
            return content.to_string();
        }

        let mut i = 0;
        while i < lines.len() {
            let line = lines[i].trim();

            // Check if this is a chunk size (hex number) - matches Python regex r'^[0-9a-fA-F]+$'
            if !line.is_empty() && line.chars().all(|c| c.is_ascii_hexdigit()) {
                let chunk_size = u32::from_str_radix(line, 16).unwrap_or(0);
                if chunk_size == 0 {
                    break;
                }

                // Get the chunk content (next line)
                i += 1;
                if i < lines.len() {
                    content_parts.push(lines[i]);
                }
            }
            i += 1;
        }

        // If no hex chunk sizes were found, return original content as-is
        if content_parts.is_empty() {
            return content.to_string();
        }

        // Join all content and return - matches Python: '\n'.join(content_parts)
        content_parts.join("\n")
    }

    /// Generate a connection ID from event data and SSE events
    fn generate_connection_id(event: &Event, sse_events: &[SSEEvent]) -> String {
        let pid = event.data.get("pid").and_then(|v| v.as_u64()).unwrap_or(0);
        let tid = event.data.get("tid").and_then(|v| v.as_u64()).unwrap_or(0);

        // First, try to extract message ID from the SSE events
        if let Some(message_id) = Self::extract_message_id(sse_events) {
            return format!("{}:{}:{}", pid, tid, message_id);
        }

        // If no message ID, use a persistent connection identifier
        // Use a much larger time window (10 minutes) to keep long SSE streams together
        // This ensures that streaming responses don't get fragmented
        let timestamp = event.timestamp;
        let window = timestamp / 600_000_000_000; // Convert to 10-minute windows
        format!("{}:{}:{}", pid, tid, window)
    }

    /// Extract the message ID from SSE events.
    ///
    /// Anthropic puts it in `message_start.message.id`. OpenAI Chat
    /// Completions carries it as a top-level `id` on every chunk and has no
    /// `message_start` at all, so without the second pass an OpenAI stream
    /// would fall back to the coarse `{pid}:{tid}:{10-minute window}` key —
    /// two responses multiplexed onto one tid would then share a single
    /// accumulator and be merged into one another.
    fn extract_message_id(events: &[SSEEvent]) -> Option<String> {
        for event in events {
            if let Some(event_type) = &event.event
                && event_type == "message_start"
                && let Some(parsed_data) = &event.parsed_data
                && let Some(message) = parsed_data.get("message")
                && let Some(id) = message.get("id")
                && let Some(id_str) = id.as_str()
            {
                return Some(id_str.to_string());
            }
        }

        // OpenAI Chat Completions chunk: top-level string `id` alongside
        // `choices`. (There is no `event:` line to match on.)
        for event in events {
            if let Some(parsed_data) = &event.parsed_data
                && parsed_data.get("choices").is_some()
                && let Some(id_str) = parsed_data.get("id").and_then(|v| v.as_str())
            {
                return Some(id_str.to_string());
            }
        }

        None
    }

    /// Check whether the SSE stream has reached its provider's terminator.
    ///
    /// The terminator is provider-specific, and looking for only one of them
    /// silently disabled finalization for every other provider:
    ///
    /// | Protocol | Terminator |
    /// |---|---|
    /// | Anthropic Messages | `event: message_stop` (or `error`) |
    /// | OpenAI Chat Completions | a bare `data: [DONE]` sentinel — there is no `event:` line at all |
    /// | OpenAI Responses | `type: response.completed` in the payload |
    ///
    /// For Anthropic the sequence is message_start → content_block_* →
    /// message_delta → message_stop, and only `message_stop` is reliable:
    /// every other event can repeat or be absent. Do not finalize on size
    /// alone — size-based early finalization splits `input_json_delta`
    /// fragments across batches and loses `content_block_start`, producing
    /// unknown_tool / malformed intents downstream.
    fn is_sse_complete(accumulator: &SSEAccumulator) -> bool {
        for event in &accumulator.events {
            if let Some(event_type) = &event.event {
                match event_type.as_str() {
                    // `message_stop` and `error` are Anthropic's; the Responses
                    // API names its terminator explicitly.
                    "message_stop" | "error" | "response.completed" => return true,
                    _ => {}
                }
            }

            // OpenAI Chat Completions terminates with a sentinel that is not
            // JSON and carries no event name.
            if let Some(data) = &event.data
                && data.trim() == "[DONE]"
            {
                return true;
            }

            // OpenAI Responses puts the type inside the payload.
            if let Some(parsed) = &event.parsed_data
                && parsed.get("type").and_then(|v| v.as_str()) == Some("response.completed")
            {
                return true;
            }
        }

        false
    }

    /// Check if SSE stream contains meaningful content worth creating an event for
    fn has_meaningful_content(accumulator: &SSEAccumulator) -> bool {
        // Content is meaningful if:
        // 1. We have accumulated text content
        // 2. We have accumulated JSON content
        // 3. We have content_block_delta events (indicates content stream)
        // 4. We have a substantial number of events (suggests real content stream)

        if !accumulator.accumulated_text.is_empty() || !accumulator.accumulated_json.is_empty() {
            // Still need to check per-index JSON, is_empty() on HashMap works
            return true;
        }

        // Check if we have content_block_delta events (indicates content stream)
        let mut has_content_deltas = false;
        let mut has_message_start = false;
        let mut metadata_only_count = 0;

        for event in &accumulator.events {
            if let Some(event_type) = &event.event {
                match event_type.as_str() {
                    "content_block_delta" => has_content_deltas = true,
                    "message_start" => has_message_start = true,
                    // These are metadata-only events
                    "message_stop"
                    | "message_delta"
                    | "ping"
                    | "content_block_stop"
                    | "content_block_start" => {
                        metadata_only_count += 1;
                    }
                    _ => {}
                }
            }
        }

        // Stream is meaningful if:
        // - It has content_block_delta events, OR
        // - It has message_start and is not just a few metadata events
        has_content_deltas
            || (has_message_start
                && accumulator.events.len() > 3
                && metadata_only_count < accumulator.events.len())
    }

    /// Accumulate content from content_block_delta events - matches ssl_log_analyzer.py logic
    fn accumulate_content(accumulator: &mut SSEAccumulator, events: &[SSEEvent], debug: bool) {
        let mut chunk_text_parts = Vec::new();

        for event in events {
            accumulator.events.push(event.clone());

            // Determine event type: prefer event field, fall back to parsed_data.type
            let event_type = event
                .event
                .as_deref()
                .filter(|s| !s.is_empty())
                .or_else(|| {
                    event
                        .parsed_data
                        .as_ref()
                        .and_then(|pd| pd.get("type"))
                        .and_then(|v| v.as_str())
                });

            if let Some(event_type) = event_type {
                if debug {
                    eprintln!("[DEBUG]   Processing event type: {}", event_type);
                }

                match event_type {
                    "message_start" => {
                        accumulator.has_message_start = true;
                        // Extract message ID
                        if accumulator.message_id.is_none() {
                            accumulator.message_id =
                                Self::extract_message_id(std::slice::from_ref(event));
                        }
                        if debug {
                            eprintln!("[DEBUG]     Found message_start, has_message_start=true");
                        }
                    }
                    "content_block_start" => {
                        // Track the content block index for per-block JSON accumulation
                        if let Some(parsed_data) = &event.parsed_data
                            && let Some(index) = parsed_data.get("index").and_then(|v| v.as_u64())
                        {
                            accumulator.current_block_index = Some(index as usize);
                            if debug {
                                eprintln!("[DEBUG]     content_block_start index={}", index);
                            }
                        }
                    }
                    "content_block_delta" => {
                        // Handle deltas - matches ssl_log_analyzer.py logic
                        if let Some(parsed_data) = &event.parsed_data {
                            // Prefer the delta's own index, fall back to current_block_index
                            let block_index = parsed_data
                                .get("index")
                                .and_then(|v| v.as_u64())
                                .map(|v| v as usize)
                                .or(accumulator.current_block_index)
                                .unwrap_or(0);

                            if let Some(delta) = parsed_data.get("delta") {
                                let mut text = String::new();

                                // Handle text delta
                                if delta.get("type").and_then(|v| v.as_str()) == Some("text_delta")
                                {
                                    if let Some(text_value) =
                                        delta.get("text").and_then(|v| v.as_str())
                                    {
                                        text = text_value.to_string();
                                        if debug {
                                            eprintln!(
                                                "[DEBUG]     Extracted text_delta: '{}'",
                                                text
                                            );
                                        }
                                    }
                                }
                                // Handle thinking delta
                                else if delta.get("type").and_then(|v| v.as_str())
                                    == Some("thinking_delta")
                                    && let Some(thinking_value) =
                                        delta.get("thinking").and_then(|v| v.as_str())
                                {
                                    text = thinking_value.to_string();
                                    if debug {
                                        eprintln!(
                                            "[DEBUG]     Extracted thinking_delta: '{}'",
                                            text
                                        );
                                    }
                                }

                                if !text.is_empty() {
                                    chunk_text_parts.push(text.clone());
                                    accumulator.accumulated_text.push_str(&text);
                                }

                                // Handle JSON delta (partial_json) — per block index
                                if let Some(partial_json) =
                                    delta.get("partial_json").and_then(|v| v.as_str())
                                {
                                    accumulator
                                        .accumulated_json
                                        .entry(block_index)
                                        .or_default()
                                        .push_str(partial_json);
                                    if debug {
                                        eprintln!(
                                            "[DEBUG]     Extracted partial_json for block[{}]: '{}'",
                                            block_index, partial_json
                                        );
                                    }
                                }
                            }
                        }
                    }
                    _ => {
                        if debug {
                            eprintln!("[DEBUG]     Skipping event type: {}", event_type);
                        }
                    }
                }
            } else if let Some(parsed_data) = &event.parsed_data {
                // Neither an `event:` name nor a payload `type` — this is the
                // shape of an OpenAI Chat Completions chunk, whose payload is
                // `{"choices":[{"delta":{...}}]}`. Nothing above matches it, so
                // an OpenAI stream accumulated zero content and its finalize
                // was then discarded as "no meaningful content".
                Self::accumulate_openai_delta(accumulator, parsed_data, debug);
            } else if debug {
                eprintln!("[DEBUG]   Event with no type field");
            }
        }

        if debug && !chunk_text_parts.is_empty() {
            eprintln!(
                "[DEBUG]   Accumulated {} text parts: {:?}",
                chunk_text_parts.len(),
                chunk_text_parts
            );
        }
    }

    /// Accumulate one OpenAI Chat Completions chunk.
    ///
    /// The payload shape is `{"choices":[{"delta":{…}}]}`. Two kinds of delta
    /// matter:
    ///
    /// * `delta.content` — assistant text. `delta.reasoning_content` is the
    ///   same thing for reasoning models (DeepSeek streams it separately), so
    ///   it is appended to the same buffer rather than dropped.
    /// * `delta.tool_calls[]` — tool calls arrive fragmented: `function.name`
    ///   once, then `function.arguments` spread over many chunks. Arguments are
    ///   keyed by `index`, the same keying `content_block_delta` uses for
    ///   `partial_json`, so a multi-tool response keeps its blocks apart.
    ///
    /// Anthropic's `content_block_*` events never reach here: they carry an
    /// `event:` name or a payload `type`, so the caller routes them above.
    fn accumulate_openai_delta(accumulator: &mut SSEAccumulator, parsed_data: &Value, debug: bool) {
        let Some(choices) = parsed_data.get("choices").and_then(|v| v.as_array()) else {
            return;
        };

        // Record the stream id so the merged event carries it. Anthropic sets
        // this from its `message_start` event, which OpenAI streams do not
        // have, so it has to be picked up from any chunk instead.
        if accumulator.message_id.is_none()
            && let Some(id) = parsed_data.get("id").and_then(|v| v.as_str())
        {
            accumulator.message_id = Some(id.to_string());
        }

        for choice in choices {
            let Some(delta) = choice.get("delta") else {
                continue;
            };

            for key in ["content", "reasoning_content"] {
                if let Some(text) = delta.get(key).and_then(|v| v.as_str())
                    && !text.is_empty()
                {
                    accumulator.accumulated_text.push_str(text);
                    if debug {
                        eprintln!("[DEBUG]     OpenAI delta.{}: '{}'", key, text);
                    }
                }
            }

            let Some(tool_calls) = delta.get("tool_calls").and_then(|v| v.as_array()) else {
                continue;
            };

            for call in tool_calls {
                let block_index = call
                    .get("index")
                    .and_then(|v| v.as_u64())
                    .map(|v| v as usize)
                    .or(accumulator.current_block_index)
                    .unwrap_or(0);
                accumulator.current_block_index = Some(block_index);

                let Some(function) = call.get("function") else {
                    continue;
                };

                if debug && let Some(name) = function.get("name").and_then(|v| v.as_str()) {
                    eprintln!(
                        "[DEBUG]     OpenAI tool_calls[{}].name = {}",
                        block_index, name
                    );
                }

                if let Some(args) = function.get("arguments").and_then(|v| v.as_str()) {
                    accumulator
                        .accumulated_json
                        .entry(block_index)
                        .or_default()
                        .push_str(args);
                }
            }
        }
    }

    /// Create merged event from accumulated SSE content
    fn create_merged_event(
        connection_id: String,
        accumulator: &SSEAccumulator,
        original_event: &Event,
    ) -> Event {
        // Process JSON content from per-block-index accumulated JSON
        let json_content = if !accumulator.accumulated_json.is_empty() {
            // Build per-block JSON in index order
            let mut indices: Vec<usize> = accumulator.accumulated_json.keys().copied().collect();
            indices.sort();
            let combined: String = indices
                .iter()
                .filter_map(|idx| accumulator.accumulated_json.get(idx))
                .fold(String::new(), |mut acc, s| {
                    acc.push_str(s);
                    acc
                });
            match serde_json::from_str::<Value>(&combined) {
                Ok(parsed_json) => serde_json::to_string_pretty(&parsed_json).unwrap_or(combined),
                Err(_) => combined,
            }
        } else {
            String::new()
        };

        // Text content is always available
        let text_content = accumulator.accumulated_text.clone();

        // Convert SSE events to JSON format
        let sse_events_json: Vec<Value> = accumulator
            .events
            .iter()
            .map(|e| {
                json!({
                    "event": e.event,
                    "data": e.data,
                    "id": e.id,
                    "parsed_data": e.parsed_data,
                    "raw_data": e.raw_data
                })
            })
            .collect();

        // Calculate total size from both content types
        let total_size = json_content.len() + text_content.len();

        // Create SSE processor event with timing information
        let sse_processor_event = SSEProcessorEvent::new(
            connection_id,
            accumulator.message_id.clone(),
            accumulator.start_time,
            accumulator.end_time,
            "ssl".to_string(),
            original_event
                .data
                .get("function")
                .unwrap_or(&json!("unknown"))
                .as_str()
                .unwrap_or("unknown")
                .to_string(),
            original_event
                .data
                .get("tid")
                .unwrap_or(&json!(0))
                .as_u64()
                .unwrap_or(0),
            json_content,
            text_content,
            total_size,
            accumulator.events.len(),
            accumulator.has_message_start,
            sse_events_json,
        );

        // Convert to framework Event
        sse_processor_event.to_event(original_event)
    }
}

#[async_trait]
impl Analyzer for SSEProcessor {
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError> {
        let sse_buffers = Arc::clone(&self.sse_buffers);

        self.debug_print("[DEBUG] SSEProcessor: Starting SSE event processing");

        let debug = self.debug;
        let timeout_ns = self.timeout_ms * 1_000_000; // ms → ns, captured for eviction
        let processed_stream = stream.filter_map(move |event| {
            let buffers = Arc::clone(&sse_buffers);

            async move {
                // Only process SSL events with data
                if event.source != "ssl" {
                    return Some(event);
                }

                // Raw fragments buffered by PayloadNormalizer will be replaced
                // by one decoded event when the message completes. Parsing them
                // here as well would emit the same content twice (and an empty
                // response for the header-only fragment).
                if event.data.get("partial").and_then(|value| value.as_bool()) == Some(true) {
                    return Some(event);
                }

                // Evict stale accumulators on every SSL event (not just SSE).
                //
                // Eviction discards whatever was accumulated, so it must be
                // visible. Silently dropping it is indistinguishable from "this
                // stream produced nothing", which is exactly how an
                // unterminated stream stayed invisible: before OpenAI's
                // `[DONE]` sentinel was recognised, every OpenAI response was
                // evicted here and nothing said so.
                {
                    let mut buffers_lock = buffers.lock().unwrap();
                    let now = event.timestamp;
                    let mut evicted_with_content = 0usize;
                    buffers_lock.retain(|_, acc| {
                        let keep = now.saturating_sub(acc.last_update) < timeout_ns;
                        if !keep
                            && (!acc.accumulated_text.is_empty()
                                || !acc.accumulated_json.is_empty())
                        {
                            evicted_with_content += 1;
                        }
                        keep
                    });
                    if evicted_with_content > 0 {
                        eprintln!(
                            "sse_processor: evicted {evicted_with_content} incomplete stream(s) \
                             idle for more than {} ms, discarding accumulated content",
                            timeout_ns / 1_000_000
                        );
                    }
                }

                let data_str = match event.data.get("data").and_then(|v| v.as_str()) {
                    Some(s) => s,
                    None => return Some(event),
                };

                // Check if this is SSE data
                if !Self::is_sse_data(data_str) {
                    return Some(event);
                }

                // Parse SSE events from this data
                let sse_events = Self::parse_sse_events(data_str);
                if sse_events.is_empty() {
                    return Some(event); // Pass through if no SSE events found
                }

                // Check if this chunk contains only metadata events (no content potential)
                let has_content_potential = sse_events.iter().any(|sse_event| {
                    if let Some(event_type) = &sse_event.event {
                        match event_type.as_str() {
                            // These events can contain or lead to content
                            "message_start" | "content_block_start" | "content_block_delta" => true,
                            // These are completion/control events but still important
                            "message_stop" | "content_block_stop" => true,
                            // These are pure metadata
                            "message_delta" | "ping" => false,
                            // Unknown events might have content
                            _ => true,
                        }
                    } else {
                        // Events without type might have content
                        true
                    }
                });

                // Be more conservative about skipping chunks - only skip pure ping/metadata
                // Always process message_stop events even if they seem like "metadata"
                let should_skip_chunk = !has_content_potential && sse_events.iter().all(|e| {
                    if let Some(event_type) = &e.event {
                        matches!(event_type.as_str(), "ping" | "message_delta")
                    } else {
                        false
                    }
                });

                if should_skip_chunk {
                    let connection_id = Self::generate_connection_id(&event, &sse_events);
                    let buffers_lock = buffers.lock().unwrap();
                    let has_existing_accumulator = buffers_lock.contains_key(&connection_id);
                    drop(buffers_lock);

                    if !has_existing_accumulator {
                        if debug {
                            eprintln!("[DEBUG] Skipping metadata-only chunk with no existing accumulator: {:?}",
                                     sse_events.iter().map(|e| e.event.as_deref().unwrap_or("none")).collect::<Vec<_>>());
                        }
                        return None;
                    }
                }

                if debug {
                    eprintln!("[DEBUG] Processing SSE chunk at timestamp {} - found {} events",
                             event.timestamp, sse_events.len());
                    // Log event types for each SSE event
                    for (i, sse_event) in sse_events.iter().enumerate() {
                        let event_type = sse_event.event.as_deref().unwrap_or("none");
                        eprintln!("[DEBUG]   Event {}: type={}", i + 1, event_type);
                    }
                    std::io::stdout().flush().unwrap();
                }

                let connection_id = Self::generate_connection_id(&event, &sse_events);

                // Store/accumulate SSE events for this connection
                let mut buffers_lock = buffers.lock().unwrap();

                // Improve message ID matching - use the first available message ID as connection ID
                let mut final_connection_id = connection_id.clone();

                // If we have a message_start event, use its message ID as the definitive connection ID
                if let Some(message_id) = Self::extract_message_id(&sse_events) {
                    let pid = event.data.get("pid").and_then(|v| v.as_u64()).unwrap_or(0);
                    let tid = event.data.get("tid").and_then(|v| v.as_u64()).unwrap_or(0);
                    final_connection_id = format!("{}:{}:{}", pid, tid, message_id);
                } else {
                    // For events without message_start, try to find an existing accumulator
                    // with the same pid/tid that doesn't have a message_stop yet
                    let pid = event.data.get("pid").and_then(|v| v.as_u64()).unwrap_or(0);
                    let tid = event.data.get("tid").and_then(|v| v.as_u64()).unwrap_or(0);
                    let conn_prefix = format!("{}:{}:", pid, tid);

                    for (existing_id, accumulator) in buffers_lock.iter() {
                        if existing_id.starts_with(&conn_prefix) && !accumulator.is_complete {
                            // Check if this accumulator doesn't have message_stop yet
                            let has_message_stop = accumulator.events.iter().any(|e| {
                                e.event.as_deref() == Some("message_stop")
                            });
                            if !has_message_stop {
                                final_connection_id = existing_id.clone();
                                break;
                            }
                        }
                    }
                }

                let accumulator = buffers_lock.entry(final_connection_id.clone()).or_insert_with(|| SSEAccumulator {
                    message_id: None,
                    accumulated_text: String::new(),
                    accumulated_json: HashMap::new(),
                    current_block_index: None,
                    events: Vec::new(),
                    is_complete: false,
                    last_update: event.timestamp,
                    has_message_start: false,
                    start_time: event.timestamp,
                    end_time: event.timestamp,
                });

                // Update last update time and end time
                accumulator.last_update = event.timestamp;
                accumulator.end_time = event.timestamp;

                // Accumulate content from SSE events
                Self::accumulate_content(accumulator, &sse_events, debug);

                // Check if stream is complete
                if Self::is_sse_complete(accumulator) {
                    // Add detailed debug output like ssl_log_analyzer.py _finalize_sse_response
                    if debug {
                        let json_total: usize = accumulator.accumulated_json.values().map(|s| s.len()).sum();
                        eprintln!("[DEBUG] Finalizing SSE response:");
                        eprintln!("  - Text parts: {:?}", accumulator.accumulated_text);
                        eprintln!("  - JSON blocks: {:?}", accumulator.accumulated_json);
                        eprintln!("  - Merged text: '{}'", accumulator.accumulated_text);
                        eprintln!("  - Merged JSON blocks: {:?}", accumulator.accumulated_json);
                        eprintln!("  - Event count: {}", accumulator.events.len());
                        eprintln!("[DEBUG] SSEProcessor: Completed SSE stream for connection {} - {} text chars, {} json chars, {} events",
                                final_connection_id,
                                accumulator.accumulated_text.len(),
                                json_total,
                                accumulator.events.len());
                        std::io::stdout().flush().unwrap();
                    }

                    // Only create merged event if stream has meaningful content
                    let result_event = if Self::has_meaningful_content(accumulator) {
                        let merged_event = Self::create_merged_event(
                            final_connection_id.clone(),
                            accumulator,
                            &event,
                        );
                        Some(merged_event)
                    } else {
                        if debug {
                            eprintln!("[DEBUG] SSE stream {} contains no meaningful content - skipping event creation", final_connection_id);
                        }
                        None
                    };

                    // Clear this accumulator
                    buffers_lock.remove(&final_connection_id);
                    drop(buffers_lock);

                    result_event
                } else {
                    // Stream not complete yet, don't emit event
                    None
                }
            }
        });

        Ok(Box::pin(processed_stream))
    }

    fn name(&self) -> &str {
        "SSEProcessor"
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures::stream;

    /// One SSL read carrying `body`, as the SSL runner delivers it.
    fn ssl_event(tid: u64, body: &str) -> Event {
        Event::new_with_timestamp(
            1,
            "ssl".to_string(),
            42,
            "node".to_string(),
            json!({ "pid": 4242, "tid": tid, "function": "SSL_read", "data": body }),
        )
    }

    async fn run(chunks: Vec<Event>) -> Vec<Event> {
        let mut processor = SSEProcessor::new_with_timeout(30_000);
        processor
            .process(Box::pin(stream::iter(chunks)))
            .await
            .unwrap()
            .collect()
            .await
    }

    fn merged(out: &[Event]) -> Vec<&Event> {
        out.iter().filter(|e| e.source == "sse_processor").collect()
    }

    fn text_of(event: &Event) -> &str {
        event
            .data
            .get("text_content")
            .and_then(|v| v.as_str())
            .unwrap_or("")
    }

    // ── OpenAI Chat Completions (DeepSeek, OpenClaw) ────────────────────

    /// OpenAI streams carry no `event:` line at all and terminate with a bare
    /// `data: [DONE]` sentinel. A finalize rule that only inspects the `event`
    /// field can therefore never fire, and the stream used to sit in the
    /// buffer until the idle timeout discarded it.
    #[tokio::test]
    async fn openai_stream_finalizes_on_done_sentinel() {
        let chunks = vec![
            ssl_event(
                7,
                "data: {\"id\":\"chatcmpl-1\",\"object\":\"chat.completion.chunk\",\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"Hel\"}}]}\n\n",
            ),
            ssl_event(
                7,
                "data: {\"id\":\"chatcmpl-1\",\"object\":\"chat.completion.chunk\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"lo\"}}]}\n\n",
            ),
            ssl_event(7, "data: [DONE]\n\n"),
        ];

        let out = run(chunks).await;
        let merged = merged(&out);

        assert_eq!(
            merged.len(),
            1,
            "an OpenAI stream must produce exactly one merged event"
        );
        assert_eq!(text_of(merged[0]), "Hello");
    }

    /// `delta.tool_calls[]` arrives fragmented: `function.name` once, then
    /// `function.arguments` split across many chunks, keyed by `index`.
    /// Nothing accumulated these before, so an OpenAI tool call produced an
    /// empty `json_content`.
    #[tokio::test]
    async fn openai_tool_call_arguments_accumulate_by_index() {
        let chunks = vec![
            ssl_event(
                8,
                "data: {\"id\":\"chatcmpl-2\",\"choices\":[{\"index\":0,\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call_1\",\"function\":{\"name\":\"exec\",\"arguments\":\"{\\\"code\\\":\"}}]}}]}\n\n",
            ),
            ssl_event(
                8,
                "data: {\"id\":\"chatcmpl-2\",\"choices\":[{\"index\":0,\"delta\":{\"tool_calls\":[{\"index\":0,\"function\":{\"arguments\":\"\\\"ls /tmp\\\"}\"}}]}}]}\n\n",
            ),
            ssl_event(8, "data: [DONE]\n\n"),
        ];

        let out = run(chunks).await;
        let merged = merged(&out);
        assert_eq!(merged.len(), 1, "tool-call stream must finalize");

        let json_content = merged[0]
            .data
            .get("json_content")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        assert!(
            json_content.contains("ls /tmp"),
            "accumulated tool arguments must be present, got {json_content:?}"
        );
    }

    /// The stream id must drive the accumulator key, otherwise two responses
    /// multiplexed onto one tid merge into a single buffer.
    #[tokio::test]
    async fn openai_message_id_becomes_connection_id() {
        let chunks = vec![
            ssl_event(
                9,
                "data: {\"id\":\"chatcmpl-xyz\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"hi\"}}]}\n\n",
            ),
            ssl_event(9, "data: [DONE]\n\n"),
        ];

        let out = run(chunks).await;
        let merged = merged(&out);
        assert_eq!(merged.len(), 1);
        assert_eq!(
            merged[0].data.get("message_id").and_then(|v| v.as_str()),
            Some("chatcmpl-xyz")
        );
    }

    // ── Anthropic Messages (Claude Code) — must not regress ─────────────

    #[tokio::test]
    async fn anthropic_message_stop_still_finalizes() {
        let chunks = vec![
            ssl_event(
                1,
                "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_1\"}}\n\n",
            ),
            ssl_event(
                1,
                "event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"Hello\"}}\n\n",
            ),
            ssl_event(
                1,
                "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n",
            ),
        ];

        let out = run(chunks).await;
        let merged = merged(&out);

        assert_eq!(merged.len(), 1, "Anthropic stream must still finalize");
        assert_eq!(text_of(merged[0]), "Hello");
        assert_eq!(
            merged[0].data.get("message_id").and_then(|v| v.as_str()),
            Some("msg_1")
        );
        assert_eq!(
            merged[0]
                .data
                .get("has_message_start")
                .and_then(|v| v.as_bool()),
            Some(true)
        );
    }

    #[tokio::test]
    async fn anthropic_stream_without_message_stop_is_not_finalized() {
        let chunks = vec![
            ssl_event(
                2,
                "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_2\"}}\n\n",
            ),
            ssl_event(
                2,
                "event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"partial\"}}\n\n",
            ),
        ];

        let out = run(chunks).await;
        assert!(
            merged(&out).is_empty(),
            "an unterminated Anthropic stream must not emit a merged event"
        );
    }
}
