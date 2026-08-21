// SPDX-License-Identifier: MIT
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

    /// Extract message ID from SSE events - matches ssl_log_analyzer.py logic
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
        None
    }

    /// Check if SSE stream is complete - follows Claude API streaming docs
    fn is_sse_complete(accumulator: &SSEAccumulator) -> bool {
        // According to Claude docs, the proper completion sequence is:
        // 1. message_start
        // 2. content_block_start, content_block_delta(s), content_block_stop
        // 3. message_delta (with stop_reason)
        // 4. message_stop (final event)

        // The ONLY reliable completion indicator is message_stop
        // All other events can appear multiple times or be missing
        for event in &accumulator.events {
            if let Some(event_type) = &event.event {
                match event_type.as_str() {
                    "message_stop" => return true,
                    "error" => return true, // Immediate completion on error
                    _ => {}
                }
            }
        }

        // Do not finalize on size alone — only message_stop or error signals
        // completion. Size-based early finalization splits input_json_delta
        // fragments across batches and causes content_block_start loss,
        // producing unknown_tool / malformed intents downstream.
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

                // Evict stale accumulators on every SSL event (not just SSE)
                {
                    let mut buffers_lock = buffers.lock().unwrap();
                    let now = event.timestamp;
                    buffers_lock.retain(|_, acc| now.saturating_sub(acc.last_update) < timeout_ns);
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
