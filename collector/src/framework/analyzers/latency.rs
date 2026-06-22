// SPDX-License-Identifier: GPL-3.0-or-later

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use futures::stream::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::json;
use tokio::sync::Mutex;

use super::{Analyzer, AnalyzerError};
use crate::framework::core::Event;
use crate::framework::runners::EventStream;

/// A single latency measurement produced by the LatencyAnalyzer.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LatencyMeasurement {
    pub request_timestamp_ns: u64,
    pub response_timestamp_ns: u64,
    pub latency_us: u64,
    pub pid: u32,
    pub comm: String,
    pub ssl_func: String,
    pub payload_size: usize,
}

impl LatencyMeasurement {
    fn to_event(&self) -> Event {
        Event::new_with_timestamp(
            self.response_timestamp_ns,
            "latency".to_string(),
            self.pid,
            self.comm.clone(),
            json!({
                "request_timestamp_ns": self.request_timestamp_ns,
                "response_timestamp_ns": self.response_timestamp_ns,
                "latency_us": self.latency_us,
                "ssl_func": self.ssl_func,
                "payload_size": self.payload_size,
            }),
        )
    }
}

#[derive(Debug, Clone)]
struct PendingRequest {
    timestamp_ns: u64,
    pid: u32,
    comm: String,
}

/// Analyzer that measures request-response latency by pairing SSL_write
/// (outgoing request) events with SSL_read (incoming response) events.
///
/// Events with source "ssl" whose `data.operation` is "SSL_write" or "SSL_read"
/// are paired: the first SSL_write after an SSL_read response creates a new
/// pending request; the next SSL_read marks the response arrival and a latency
/// measurement is emitted.
pub struct LatencyAnalyzer {
    /// Per-(pid) pending write — awaiting the next read
    pending: Arc<Mutex<HashMap<u32, PendingRequest>>>,
}

impl LatencyAnalyzer {
    pub fn new() -> Self {
        Self {
            pending: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    fn extract_operation(data: &serde_json::Value) -> Option<&str> {
        data.get("operation")?.as_str()
    }

    fn extract_payload_size(data: &serde_json::Value) -> usize {
        data.get("len")
            .and_then(|v| v.as_u64())
            .map(|n| n as usize)
            .unwrap_or(0)
    }
}

#[async_trait]
impl Analyzer for LatencyAnalyzer {
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError> {
        let pending = Arc::clone(&self.pending);

        let out = stream.filter_map(move |event| {
            let pending = Arc::clone(&pending);
            async move {
                if event.source != "ssl" {
                    return Some(event);
                }

                let op = Self::extract_operation(&event.data)?;

                match op {
                    "SSL_write" => {
                        let mut map = pending.lock().await;
                        map.insert(
                            event.pid,
                            PendingRequest {
                                timestamp_ns: event.timestamp,
                                pid: event.pid,
                                comm: event.comm.clone(),
                            },
                        );
                        Some(event)
                    }
                    "SSL_read" => {
                        let pending_req = {
                            let mut map = pending.lock().await;
                            map.remove(&event.pid)
                        };

                        if let Some(req) = pending_req {
                            let latency_ns = event.timestamp.saturating_sub(req.timestamp_ns);
                            let measurement = LatencyMeasurement {
                                request_timestamp_ns: req.timestamp_ns,
                                response_timestamp_ns: event.timestamp,
                                latency_us: latency_ns / 1_000,
                                pid: event.pid,
                                comm: event.comm.clone(),
                                ssl_func: "SSL_read".into(),
                                payload_size: Self::extract_payload_size(&event.data),
                            };
                            // Emit original read event + the derived latency event
                            let mut events = Vec::with_capacity(2);
                            events.push(event);
                            events.push(measurement.to_event());
                            Some(Event::new_with_timestamp(
                                measurement.response_timestamp_ns,
                                "latency".to_string(),
                                measurement.pid,
                                measurement.comm.clone(),
                                json!(measurement),
                            ))
                        } else {
                            Some(event)
                        }
                    }
                    _ => Some(event),
                }
            }
        });

        Ok(Box::pin(out))
    }

    fn name(&self) -> &str {
        "latency"
    }
}

impl Default for LatencyAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures::stream;

    fn make_ssl_event(pid: u32, timestamp: u64, operation: &str, len: u64) -> Event {
        Event::new_with_timestamp(
            timestamp,
            "ssl".to_string(),
            pid,
            "test_proc".to_string(),
            json!({"operation": operation, "len": len, "data": "dummy"}),
        )
    }

    #[tokio::test]
    async fn write_then_read_produces_latency_event() {
        // timestamps in nanoseconds; 1_500_000 ns = 1500 us
        let events = vec![
            make_ssl_event(100, 1_000_000, "SSL_write", 256),
            make_ssl_event(100, 2_500_000, "SSL_read", 512),
        ];
        let stream = Box::pin(stream::iter(events));

        let mut analyzer = LatencyAnalyzer::new();
        let result = analyzer.process(stream).await.unwrap();

        let collected: Vec<Event> = result.collect().await;
        let latency_events: Vec<&Event> =
            collected.iter().filter(|e| e.source == "latency").collect();
        assert_eq!(latency_events.len(), 1);
        let latency_us = latency_events[0].data["latency_us"].as_u64().unwrap();
        assert_eq!(latency_us, 1500);
    }

    #[tokio::test]
    async fn read_without_write_passthrough() {
        let events = vec![make_ssl_event(100, 5_000_000, "SSL_read", 128)];
        let stream = Box::pin(stream::iter(events));

        let mut analyzer = LatencyAnalyzer::new();
        let result = analyzer.process(stream).await.unwrap();

        let collected: Vec<Event> = result.collect().await;
        assert_eq!(collected.len(), 1);
        assert_eq!(collected[0].source, "ssl");
    }

    #[tokio::test]
    async fn non_ssl_events_passthrough() {
        let events = vec![Event::new(
            "process".to_string(),
            200,
            "bash".to_string(),
            json!({"action": "exec"}),
        )];
        let stream = Box::pin(stream::iter(events));

        let mut analyzer = LatencyAnalyzer::new();
        let result = analyzer.process(stream).await.unwrap();

        let collected: Vec<Event> = result.collect().await;
        assert_eq!(collected.len(), 1);
        assert_eq!(collected[0].source, "process");
    }

    #[tokio::test]
    async fn multiple_requests_different_pids() {
        let events = vec![
            make_ssl_event(100, 1_000_000, "SSL_write", 64),
            make_ssl_event(200, 1_500_000, "SSL_write", 128),
            make_ssl_event(100, 3_000_000, "SSL_read", 256),
            make_ssl_event(200, 4_000_000, "SSL_read", 512),
        ];
        let stream = Box::pin(stream::iter(events));

        let mut analyzer = LatencyAnalyzer::new();
        let result = analyzer.process(stream).await.unwrap();

        let collected: Vec<Event> = result.collect().await;
        let latency_events: Vec<&Event> =
            collected.iter().filter(|e| e.source == "latency").collect();
        assert_eq!(latency_events.len(), 2);

        let latencies: Vec<u64> = latency_events
            .iter()
            .map(|e| e.data["latency_us"].as_u64().unwrap())
            .collect();
        // pid 100: 3_000_000 - 1_000_000 = 2_000_000 ns → 2000 us
        // pid 200: 4_000_000 - 1_500_000 = 2_500_000 ns → 2500 us
        assert_eq!(latencies, vec![2000, 2500]);
    }
}
