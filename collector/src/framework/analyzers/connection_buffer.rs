// SPDX-License-Identifier: MIT
//
// connection_buffer.rs — Per-connection SSL_write reassembly.
//
// Problem: SSL_write data is captured in per-call eBPF uprobe events.
// Large request bodies (e.g., Anthropic API POST with system prompts +
// CLAUDE.md) span multiple SSL_write calls, producing fragments.
//
// Solution: Accumulate SSL_write data per TLS session (tid). When an
// SSL_read event arrives for that session, emit the assembled body as
// a synthetic SSL_write event, then pass the original read through.
// Uses an internal queue so both events reach the downstream pipeline.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use crate::framework::Event;
use crate::framework::analyzers::Analyzer;
use crate::framework::analyzers::AnalyzerError;
use crate::framework::runners::EventStream;
use futures::StreamExt;
use serde_json::json;

type SessionBuffers = Arc<Mutex<HashMap<u64, Vec<u8>>>>;

pub struct ConnectionBuffer {
    buffers: SessionBuffers,
    /// Queued events waiting to be emitted (avoids dropping SSL_read).
    pending: Arc<Mutex<Vec<Event>>>,
}

impl Default for ConnectionBuffer {
    fn default() -> Self {
        Self::new()
    }
}

impl ConnectionBuffer {
    pub fn new() -> Self {
        Self {
            buffers: Arc::new(Mutex::new(HashMap::new())),
            pending: Arc::new(Mutex::new(Vec::new())),
        }
    }
}

#[async_trait::async_trait]
impl Analyzer for ConnectionBuffer {
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError> {
        let buffers = Arc::clone(&self.buffers);
        let pending = Arc::clone(&self.pending);

        let processed = stream.filter_map(move |event| {
            let buffers = Arc::clone(&buffers);
            let pending = Arc::clone(&pending);
            async move {
                // Drain any queued events first
                {
                    let mut pq = pending.lock().unwrap();
                    if !pq.is_empty() {
                        let next = pq.remove(0);
                        pq.push(event);
                        return Some(next);
                    }
                }

                // Only process raw SSL events
                if event.source != "ssl" {
                    return Some(event);
                }

                let data = &event.data;
                let tid = match data.get("tid").and_then(|v| v.as_u64()) {
                    Some(t) => t,
                    None => return Some(event),
                };

                let raw_data = match data.get("data").and_then(|v| v.as_str()) {
                    Some(s) => s,
                    None => return Some(event),
                };
                if raw_data.is_empty() {
                    return Some(event);
                }

                // Determine write vs read by content: HTTP responses start
                // with "HTTP/", requests start with a method (POST/GET).
                // The eBPF tls_capture uprobe doesn't populate a direction field.
                let is_response = raw_data.starts_with("HTTP/");

                if !is_response {
                    let mut bufs = buffers.lock().unwrap();
                    if raw_data.len() < 64 * 1024 {
                        bufs.entry(tid)
                            .or_default()
                            .extend_from_slice(raw_data.as_bytes());
                        None // swallow request fragment
                    } else {
                        // Very large single write — pass through directly
                        Some(event)
                    }
                } else {
                    let assembled = {
                        let mut bufs = buffers.lock().unwrap();
                        bufs.remove(&tid)
                    };

                    if let Some(buf) = assembled
                        && !buf.is_empty()
                    {
                        let body = String::from_utf8(buf).unwrap_or_default();
                        if !body.trim().is_empty() {
                            // Emit the assembled request as a synthetic
                            // SSL event, then queue the original read
                            // event for the next iteration.
                            let mut synth_data = event.data.clone();
                            synth_data["data"] = json!(body);
                            let synth = Event::new_with_timestamp(
                                event.timestamp,
                                "ssl".to_string(),
                                event.pid,
                                event.comm.clone(),
                                synth_data,
                            );
                            pending.lock().unwrap().push(event); // original read follows
                            return Some(synth);
                        }
                    }

                    Some(event) // pass-through read
                }
            }
        });

        Ok(Box::pin(processed))
    }

    fn name(&self) -> &str {
        "ConnectionBuffer"
    }
}
