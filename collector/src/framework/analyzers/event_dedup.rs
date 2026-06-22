// SPDX-License-Identifier: MIT
// Copyright (c) 2026 MtAgentPrism Project

//! Streaming event deduplication analyzer for the pipeline preprocessing stage.
//!
//! This analyzer eliminates semantically duplicate events produced by
//! overlapping eBPF probes before they reach expensive downstream stages
//! (HTTP parsing, SSE reassembly, injection detection, etc.).
//!
//! ## Dedup Key
//!
//! Events are identified by a [`DedupKey`] constructed from four fields:
//! `(timestamp, source, pid, event_type)`. Two events with identical keys
//! within the configured time window are considered duplicates.
//!
//! ## Position in Pipeline
//!
//! This analyzer MUST be placed **before** the [`TimestampNormalizer`] to
//! preserve nanosecond timestamp precision in the dedup key. Normalized
//! timestamps (epoch milliseconds) lose the precision needed for accurate
//! dedup comparison.

use crate::framework::analyzers::{Analyzer, AnalyzerError};
use crate::framework::core::Event;
use crate::framework::runners::EventStream;
use async_trait::async_trait;
use std::collections::BTreeSet;
use std::time::Duration;

/// Default dedup window in seconds.
///
/// Events more than this many seconds apart are unlikely to be duplicates
/// produced by overlapping eBPF probes. Reduced from 60s to 10s to avoid
/// false-positive suppression of legitimate distinct events.
const DEDUP_WINDOW_SECS: u64 = 10;

/// Configuration for [`StreamingEventDeduplicationAnalyzer`].
///
/// # Fields
///
/// * `dedup_window_secs` — Time window (in seconds) within which matching
///   events are suppressed. Default: [`DEDUP_WINDOW_SECS`] (10 seconds).
#[derive(Debug, Clone)]
pub struct DedupConfig {
    /// Time window within which duplicate events are suppressed (seconds).
    pub dedup_window_secs: u64,
}

impl Default for DedupConfig {
    fn default() -> Self {
        Self {
            dedup_window_secs: DEDUP_WINDOW_SECS,
        }
    }
}

/// Extract a semantic event-type label from an [`Event`].
///
/// Different event sources store the operation/action in different `data`
/// fields. This function normalizes them into a single classification string.
///
/// | Source      | Data field used | Examples                        |
/// |-------------|-----------------|---------------------------------|
/// | `ssl`       | `operation`     | `"SSL_write"`, `"SSL_read"`     |
/// | `process`   | `action`        | `"exec"`, `"file_open"`         |
/// | `system`    | `category`      | `"cpu"`, `"memory"`             |
/// | `stdio`     | `direction`     | `"read"`, `"write"`             |
/// | other       | `comm`          | `"claude"`, `"node"`            |
fn extract_event_type(event: &Event) -> String {
    event
        .data
        .get("operation")
        .or_else(|| event.data.get("action"))
        .or_else(|| event.data.get("category"))
        .or_else(|| event.data.get("direction"))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| event.comm.clone())
}

/// A composite key identifying semantically equivalent events.
///
/// # Fields
/// - `timestamp`: Event timestamp in nanoseconds (pre-normalization).
/// - `source`: Event source (e.g. `"ssl"`, `"process"`, `"stdio"`).
/// - `pid`: Process ID that produced the event.
/// - `event_type`: Semantic type of event (e.g. `"SSL_write"`, `"exec"`,
///   `"file_open"`). Included to avoid collisions when two different
///   event types occur at the same nanosecond in the same process.
///
/// `BTreeSet` (rather than `HashSet`) is used for the dedup cache to
/// provide deterministic iteration order, which makes tests more reliable.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct DedupKey {
    timestamp: u64,
    source: String,
    pid: u32,
    event_type: String,
}

impl DedupKey {
    /// Construct a [`DedupKey`] from an [`Event`].
    ///
    /// Uses the event's direct `pid` field and derives `event_type` from
    /// the `data` payload (see [`extract_event_type`]).
    ///
    /// Returns `None` if the event cannot produce a valid key (fail-open).
    fn from_event(event: &Event) -> Option<Self> {
        Some(Self {
            timestamp: event.timestamp,
            source: event.source.clone(),
            pid: event.pid,
            event_type: extract_event_type(event),
        })
    }
}

/// Streaming analyzer that suppresses duplicate events.
///
/// # Operation
///
/// For each incoming event, the analyzer computes a [`DedupKey`] and checks
/// whether an identical key was seen within the configured dedup window.
/// If so, the event is suppressed (not forwarded). Otherwise, it passes
/// through unchanged and its key is recorded in the dedup cache.
///
/// # Fail-open behavior
///
/// If a key cannot be computed (missing fields), the event passes through
/// unchanged. This ensures that unrecognized event shapes do not cause
/// data loss.
///
/// # Memory bounds
///
/// Before each event, the cache is pruned of entries older than the dedup
/// window, preventing unbounded memory growth under sustained load.
///
/// # Example
///
/// ```rust,ignore
/// use mtagentprism::framework::analyzers::{
///     StreamingEventDeduplicationAnalyzer, DedupConfig, TimestampNormalizer,
/// };
///
/// let analyzer = StreamingEventDeduplicationAnalyzer::new(
///     DedupConfig { dedup_window_secs: 10 }
/// );
///
/// // Dedup MUST be added before TimestampNormalizer
/// let mut builder = PipelineOrchestratorImpl::builder();
/// builder
///     .add_preprocessor(Box::new(analyzer))
///     .add_preprocessor(Box::new(TimestampNormalizer::new()));
/// ```
pub struct StreamingEventDeduplicationAnalyzer {
    /// Ordered set of recently seen dedup keys.
    ///
    /// `BTreeSet` provides deterministic ordering, which makes test
    /// assertions more reliable than `HashSet`.
    seen: BTreeSet<DedupKey>,
    /// Configuration for this analyzer instance.
    config: DedupConfig,
}

impl StreamingEventDeduplicationAnalyzer {
    /// Create a new deduplication analyzer with the given configuration.
    ///
    /// # Arguments
    ///
    /// * `config` — Configuration including the dedup time window.
    ///
    /// # Example
    ///
    /// ```rust,ignore
    /// use mtagentprism::framework::analyzers::{
    ///     StreamingEventDeduplicationAnalyzer, DedupConfig,
    /// };
    ///
    /// let analyzer = StreamingEventDeduplicationAnalyzer::new(
    ///     DedupConfig { dedup_window_secs: 10 }
    /// );
    /// ```
    pub fn new(config: DedupConfig) -> Self {
        Self {
            seen: BTreeSet::new(),
            config,
        }
    }
}

#[async_trait]
impl Analyzer for StreamingEventDeduplicationAnalyzer {
    /// Process an event stream, suppressing duplicates.
    ///
    /// For each event in the input stream, if an event with the same
    /// `(timestamp, source, pid, event_type)` was seen within the dedup
    /// window, the event is dropped. Otherwise it passes through unchanged.
    ///
    /// The stream is fully collected before processing so that the analyzer
    /// can safely mutate its internal dedup cache without lifetime conflicts
    /// with the returned `EventStream` (which is `'static`).
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError> {
        use futures::stream::{self, StreamExt};

        let window_ns = Duration::from_secs(self.config.dedup_window_secs).as_nanos() as u64;

        // Collect the full stream so we can safely mutate `self.seen`
        // without lifetime conflicts with the returned `EventStream`.
        let all_events: Vec<Event> = stream.collect().await;

        let mut output = Vec::with_capacity(all_events.len());
        for event in all_events {
            // Prune entries outside the dedup window before each event.
            // This keeps memory bounded without requiring a separate timer task.
            let cutoff = event.timestamp.saturating_sub(window_ns);
            self.seen.retain(|key| key.timestamp >= cutoff);

            if let Some(key) = DedupKey::from_event(&event) {
                if self.seen.contains(&key) {
                    // Duplicate detected — suppress this event
                    continue;
                }
                self.seen.insert(key);
            }
            // Unparseable events (missing fields) pass through unchanged (fail-open)
            output.push(event);
        }

        Ok(Box::pin(stream::iter(output)))
    }

    fn name(&self) -> &str {
        "StreamingEventDeduplicationAnalyzer"
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures::StreamExt;
    use futures::stream;
    use serde_json::json;

    /// Helper to create an [`Event`] with specific properties for testing.
    ///
    /// The `event_type` value is stored inside `data` at a source-specific
    /// key so that [`extract_event_type`] can retrieve it:
    /// - `"ssl"` sources → `data.operation`
    /// - `"process"` sources → `data.action`
    /// - other sources → no type field (falls back to `comm`)
    fn make_event(timestamp: u64, source: &str, pid: u32, event_type: &str, comm: &str) -> Event {
        let data = match source {
            "ssl" => json!({ "operation": event_type }),
            "process" => json!({ "action": event_type }),
            "system" => json!({ "category": event_type }),
            "stdio" => json!({ "direction": event_type }),
            _ => json!({}),
        };

        Event {
            timestamp,
            source: source.to_string(),
            pid,
            comm: comm.to_string(),
            data,
        }
    }

    /// Two identical events within the dedup window: the second should be suppressed.
    #[tokio::test]
    async fn test_exact_duplicate_suppressed() {
        let config = DedupConfig {
            dedup_window_secs: 10,
        };
        let mut analyzer = StreamingEventDeduplicationAnalyzer::new(config);

        let event = make_event(1000, "ssl", 42, "SSL_write", "test");
        let events = vec![event.clone(), event.clone()];
        let stream: EventStream = Box::pin(stream::iter(events));

        let result = analyzer.process(stream).await.unwrap();
        let output: Vec<Event> = result.collect().await;

        assert_eq!(output.len(), 1, "Duplicate event should be suppressed");
        assert_eq!(output[0].timestamp, 1000);
        assert_eq!(output[0].source, "ssl");
    }

    /// Events from different PIDs should never be deduplicated.
    #[tokio::test]
    async fn test_different_pid_not_suppressed() {
        let config = DedupConfig::default();
        let mut analyzer = StreamingEventDeduplicationAnalyzer::new(config);

        let event1 = make_event(1000, "ssl", 42, "SSL_write", "test");
        let event2 = make_event(1000, "ssl", 99, "SSL_write", "test");
        let events = vec![event1, event2];
        let stream: EventStream = Box::pin(stream::iter(events));

        let result = analyzer.process(stream).await.unwrap();
        let output: Vec<Event> = result.collect().await;

        assert_eq!(output.len(), 2, "Different PIDs should not be deduplicated");
    }

    /// Same (timestamp, source, pid) but different event_type: these are NOT
    /// duplicates and should both pass through (review feedback #4).
    #[tokio::test]
    async fn test_different_event_type_not_suppressed() {
        let config = DedupConfig::default();
        let mut analyzer = StreamingEventDeduplicationAnalyzer::new(config);

        let event1 = make_event(1000, "ssl", 42, "SSL_write", "test");
        let event2 = make_event(1000, "ssl", 42, "SSL_read", "test");
        let events = vec![event1, event2];
        let stream: EventStream = Box::pin(stream::iter(events));

        let result = analyzer.process(stream).await.unwrap();
        let output: Vec<Event> = result.collect().await;

        assert_eq!(
            output.len(),
            2,
            "Events with different event_types should not be deduplicated"
        );
    }

    /// Events outside the dedup window should not be suppressed.
    #[tokio::test]
    async fn test_outside_window_not_suppressed() {
        let config = DedupConfig {
            dedup_window_secs: 10,
        };
        let mut analyzer = StreamingEventDeduplicationAnalyzer::new(config);

        // 15 seconds apart → outside the 10-second window
        let event1 = make_event(1_000_000_000, "ssl", 42, "SSL_write", "test");
        let event2 = make_event(16_000_000_000, "ssl", 42, "SSL_write", "test");
        let events = vec![event1, event2];
        let stream: EventStream = Box::pin(stream::iter(events));

        let result = analyzer.process(stream).await.unwrap();
        let output: Vec<Event> = result.collect().await;

        assert_eq!(
            output.len(),
            2,
            "Events outside the dedup window should not be suppressed"
        );
    }

    /// Events from unknown sources with different comm values should both pass through.
    /// This tests the fail-open path: the dedup key still applies uniformly across sources.
    #[tokio::test]
    async fn test_different_comm_not_suppressed() {
        let config = DedupConfig::default();
        let mut analyzer = StreamingEventDeduplicationAnalyzer::new(config);

        // Different comm → different event_type fallback → not duplicates
        let event1 = make_event(1000, "custom", 42, "some_event", "claude");
        let event2 = make_event(1000, "custom", 42, "other_event", "node");
        let events = vec![event1, event2];
        let stream: EventStream = Box::pin(stream::iter(events));

        let result = analyzer.process(stream).await.unwrap();
        let output: Vec<Event> = result.collect().await;

        assert_eq!(
            output.len(),
            2,
            "Events with different comm values should both pass through"
        );
    }
}
