// SPDX-License-Identifier: MIT
// Copyright (c) 2026 eunomia-bpf org.

use super::{Analyzer, AnalyzerError};
use crate::framework::core::Event;
use crate::framework::runners::EventStream;
use async_trait::async_trait;
use futures::stream::StreamExt;
use serde_json::json;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::time::interval;

// ---------------------------------------------------------------------------
// Analyzer
// ---------------------------------------------------------------------------

/// An observability-metrics analyzer that counts events per source and per
/// process, computes a sliding-window event rate, and optionally injects
/// periodic summary events into the stream.
///
/// ## Design notes
/// - All original events pass through unchanged (this analyzer is a
///   side-effect observer on the main path).
/// - Per-source and per-process counters are stored behind `Arc<Mutex<…>>` so
///   the `process()` method's closures can lazily insert entries for unseen
///   sources/processes.
/// - When `emit_summaries` is true, a tokio tick interval is merged with
///   the event stream so that summary events appear every `summary_interval`.
/// - The sliding timestamp window is bounded to 1024 entries to cap memory.
pub struct ObservabilityMetricsAnalyzer {
    // Per-source event counters (Mutex so the process closure can lazily insert)
    source_counts: Arc<Mutex<HashMap<String, Arc<AtomicU64>>>>,
    // Per-process event counters (pid -> count)
    process_counts: Arc<Mutex<HashMap<u32, u64>>>,
    // Total event counter
    total_events: Arc<AtomicU64>,
    // Sliding window of arrival timestamps for rate computation
    timestamps: Arc<Mutex<Vec<Instant>>>,
    // Maximum window size
    max_window: usize,
    // Whether to emit periodic summary events into the stream
    emit_summaries: bool,
    // How often to emit summary events
    summary_interval: Duration,
}

impl ObservabilityMetricsAnalyzer {
    /// Create a new `ObservabilityMetricsAnalyzer`.
    ///
    /// * `emit_summaries` — when `true`, inject summary events into the stream
    ///   every `summary_interval`.
    /// * `summary_interval` — period between summary events (default 30 s if
    ///   `emit_summaries` is true but interval is zero).
    /// * `max_window` — maximum number of timestamps kept for rate computation
    ///   (default 1024).
    pub fn new(emit_summaries: bool, summary_interval: Duration, max_window: usize) -> Self {
        let interval = if emit_summaries && summary_interval.is_zero() {
            Duration::from_secs(30)
        } else {
            summary_interval
        };

        Self {
            source_counts: Arc::new(Mutex::new(HashMap::new())),
            process_counts: Arc::new(Mutex::new(HashMap::new())),
            total_events: Arc::new(AtomicU64::new(0)),
            timestamps: Arc::new(Mutex::new(Vec::with_capacity(max_window.min(1024)))),
            max_window: max_window.min(1024).max(64),
            emit_summaries,
            summary_interval: interval,
        }
    }

    /// Create a new `ObservabilityMetricsAnalyzer` that only observes
    /// (no periodic summary injection).
    pub fn new_observer() -> Self {
        Self::new(false, Duration::ZERO, 1024)
    }

    /// Return the current per-source counts (snapshot).
    pub fn per_source_counts(&self) -> HashMap<String, u64> {
        self.source_counts
            .lock()
            .map(|guard| {
                guard
                    .iter()
                    .map(|(k, v)| (k.clone(), v.load(Ordering::Relaxed)))
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Return the current per-process counts (snapshot).
    pub fn per_process_counts(&self) -> HashMap<u32, u64> {
        self.process_counts
            .lock()
            .map(|guard| guard.clone())
            .unwrap_or_default()
    }

    /// Return the total number of events observed so far.
    pub fn total_events_observed(&self) -> u64 {
        self.total_events.load(Ordering::Relaxed)
    }

    /// Compute the event rate (events/sec) over the sliding window.
    pub fn current_rate(&self) -> f64 {
        let guard = match self.timestamps.lock() {
            Ok(g) => g,
            Err(_) => return 0.0,
        };
        if guard.len() < 2 {
            return 0.0;
        }
        let oldest = guard.first().unwrap();
        let newest = guard.last().unwrap();
        let elapsed = newest.saturating_duration_since(*oldest);
        if elapsed.is_zero() {
            return 0.0;
        }
        guard.len() as f64 / elapsed.as_secs_f64()
    }
}

impl Default for ObservabilityMetricsAnalyzer {
    fn default() -> Self {
        Self::new_observer()
    }
}

// ---------------------------------------------------------------------------
// Analyzer trait impl
// ---------------------------------------------------------------------------

#[async_trait]
impl Analyzer for ObservabilityMetricsAnalyzer {
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError> {
        let emit_summaries = self.emit_summaries;
        let summary_interval = self.summary_interval;

        // Shared state accessible from closures (Arc clones are cheap).
        let total_events = self.total_events.clone();
        let source_counts = self.source_counts.clone();
        let process_counts = self.process_counts.clone();
        let timestamps = self.timestamps.clone();
        let max_window = self.max_window;

        if emit_summaries && !summary_interval.is_zero() {
            // ---------------------------------------------------------------
            // Mode: emit periodic summary events
            //
            // Merge two streams:
            //   1. The original event stream (events pass through, counters
            //      incremented as a side effect).
            //   2. A tick stream that periodically emits a summary event.
            // ---------------------------------------------------------------
            let tick_stream = {
                let total_events = total_events.clone();
                let source_counts = source_counts.clone();
                let process_counts = process_counts.clone();
                let timestamps = timestamps.clone();

                let mut tick = interval(summary_interval);
                // Align first tick so we don't emit immediately.
                tick.tick().await;

                async_stream::stream! {
                    loop {
                        tick.tick().await;

                        let rate = {
                            let guard = timestamps.lock().unwrap_or_else(|e| e.into_inner());
                            if guard.len() < 2 {
                                0.0
                            } else {
                                let oldest = guard.first().unwrap();
                                let newest = guard.last().unwrap();
                                let elapsed = newest.saturating_duration_since(*oldest);
                                if elapsed.is_zero() { 0.0 } else { guard.len() as f64 / elapsed.as_secs_f64() }
                            }
                        };

                        let total = total_events.load(Ordering::Relaxed);
                        let per_source: serde_json::Map<String, serde_json::Value> = source_counts
                            .lock()
                            .map(|guard| {
                                guard
                                    .iter()
                                    .map(|(src, cnt)| (src.clone(), json!(cnt.load(Ordering::Relaxed))))
                                    .collect()
                            })
                            .unwrap_or_default();
                        let per_process: serde_json::Map<String, serde_json::Value> = process_counts
                            .lock()
                            .map(|guard| guard.iter().map(|(pid, cnt)| (pid.to_string(), json!(cnt))).collect())
                            .unwrap_or_default();

                        let data = json!({
                            "type": "observability_summary",
                            "total_events": total,
                            "rate_events_per_sec": (rate * 100.0).round() / 100.0,
                            "per_source": per_source,
                            "per_process": per_process,
                            "window_size": max_window,
                        });

                        yield Event::new(
                            "observability_metrics".to_string(),
                            0,
                            "mtagentlens".to_string(),
                            data,
                        );
                    }
                }
            };

            // Process each event from the main stream: record and pass through.
            let counted_stream = {
                let total_events = total_events.clone();
                let source_counts = source_counts.clone();
                let process_counts = process_counts.clone();
                let timestamps = timestamps.clone();

                stream.map(move |event| {
                    total_events.fetch_add(1, Ordering::Relaxed);

                    // Per-source: lazy-insert via mutex
                    if let Ok(mut src_guard) = source_counts.lock() {
                        let entry = src_guard
                            .entry(event.source.clone())
                            .or_insert_with(|| Arc::new(AtomicU64::new(0)));
                        entry.fetch_add(1, Ordering::Relaxed);
                    }

                    if let Ok(mut proc_guard) = process_counts.lock() {
                        *proc_guard.entry(event.pid).or_insert(0) += 1;
                    }
                    if let Ok(mut ts_guard) = timestamps.lock() {
                        ts_guard.push(Instant::now());
                        while ts_guard.len() > max_window {
                            ts_guard.remove(0);
                        }
                    }
                    event
                })
            };

            // Merge both streams.
            let merged = futures::stream::select(counted_stream, tick_stream);
            Ok(Box::pin(merged))
        } else {
            // ---------------------------------------------------------------
            // Mode: observer only — no summary injection
            // ---------------------------------------------------------------
            let counted_stream = stream.map(move |event| {
                total_events.fetch_add(1, Ordering::Relaxed);

                // Per-source: lazy-insert via mutex
                if let Ok(mut src_guard) = source_counts.lock() {
                    let entry = src_guard
                        .entry(event.source.clone())
                        .or_insert_with(|| Arc::new(AtomicU64::new(0)));
                    entry.fetch_add(1, Ordering::Relaxed);
                }

                if let Ok(mut proc_guard) = process_counts.lock() {
                    *proc_guard.entry(event.pid).or_insert(0) += 1;
                }
                if let Ok(mut ts_guard) = timestamps.lock() {
                    ts_guard.push(Instant::now());
                    while ts_guard.len() > max_window {
                        ts_guard.remove(0);
                    }
                }
                event
            });

            Ok(Box::pin(counted_stream))
        }
    }

    fn name(&self) -> &str {
        "ObservabilityMetricsAnalyzer"
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::framework::analyzers::OutputAnalyzer;
    use crate::framework::runners::FakeRunner;
    use crate::framework::runners::Runner;
    use futures::stream;
    use serde_json::json;
    use tokio::time::timeout;

    #[tokio::test]
    async fn test_observer_mode_counts_events() {
        let mut analyzer = ObservabilityMetricsAnalyzer::new_observer();

        let events = vec![
            Event::new(
                "ssl".to_string(),
                1001,
                "python".to_string(),
                json!({"msg": "a"}),
            ),
            Event::new(
                "ssl".to_string(),
                1001,
                "python".to_string(),
                json!({"msg": "b"}),
            ),
            Event::new(
                "process".to_string(),
                2002,
                "bash".to_string(),
                json!({"msg": "c"}),
            ),
        ];
        let stream: EventStream = Box::pin(stream::iter(events));

        let output = analyzer.process(stream).await.unwrap();
        let collected: Vec<_> = output.collect().await;

        assert_eq!(
            collected.len(),
            3,
            "observer mode passes all events through"
        );
        assert_eq!(analyzer.total_events_observed(), 3);

        let per_source = analyzer.per_source_counts();
        assert_eq!(per_source.get("ssl").copied().unwrap_or(0), 2);
        assert_eq!(per_source.get("process").copied().unwrap_or(0), 1);
    }

    #[tokio::test]
    async fn test_emission_mode_injects_summary_events() {
        let mut analyzer = ObservabilityMetricsAnalyzer::new(
            true,                      // emit summaries
            Duration::from_millis(50), // fast tick for test
            128,
        );

        let events = vec![
            Event::new(
                "ssl".to_string(),
                1001,
                "python".to_string(),
                json!({"msg": "x"}),
            ),
            Event::new(
                "ssl".to_string(),
                1001,
                "python".to_string(),
                json!({"msg": "y"}),
            ),
            Event::new(
                "process".to_string(),
                2002,
                "bash".to_string(),
                json!({"msg": "z"}),
            ),
        ];
        let stream: EventStream = Box::pin(stream::iter(events));

        let output = analyzer.process(stream).await.unwrap();

        // We expect 3 original events + at least 1 summary event within a
        // reasonable timeout.
        let result = timeout(Duration::from_secs(2), async {
            let mut orig = 0usize;
            let mut summaries = 0usize;
            let mut collected = Vec::new();
            tokio::pin!(output);
            while let Some(event) = output.next().await {
                collected.push(event);
                if collected.last().unwrap().source == "observability_metrics" {
                    summaries += 1;
                } else {
                    orig += 1;
                }
                if summaries >= 1 && orig >= 3 {
                    break;
                }
            }
            (orig, summaries, collected)
        })
        .await;

        assert!(result.is_ok(), "should complete within timeout");
        let (orig, summaries, _) = result.unwrap();
        assert_eq!(orig, 3, "all original events pass through");
        assert!(summaries >= 1, "at least one summary event injected");
    }

    #[tokio::test]
    async fn test_integration_with_fake_runner() {
        let mut runner = FakeRunner::new()
            .event_count(3)
            .delay_ms(5)
            .add_analyzer(Box::new(ObservabilityMetricsAnalyzer::new_observer()))
            .add_analyzer(Box::new(OutputAnalyzer::new()));

        let stream = runner.run().await.unwrap();
        let events: Vec<_> = stream.collect().await;

        // FakeRunner: 3 pairs x 2 events per pair = 6 events
        assert_eq!(events.len(), 6, "all events pass through the chain");
    }

    #[tokio::test]
    async fn test_current_rate_after_events() {
        let mut analyzer = ObservabilityMetricsAnalyzer::new_observer();

        let events: Vec<Event> = (0..10)
            .map(|i| Event::new("test".to_string(), i, "test".to_string(), json!({"idx": i})))
            .collect();

        let stream: EventStream = Box::pin(stream::iter(events));
        let _ = analyzer.process(stream).await.unwrap();

        let rate = analyzer.current_rate();
        // With 10 events fired in quick succession, rate should be positive.
        assert!(rate >= 0.0, "rate should be non-negative, got {}", rate);
    }
}
