// SPDX-License-Identifier: MIT
// Copyright (c) 2026 MtAgentLens Project

//! Alert Enrichment Analyzer
//!
//! Correlates, deduplicates, and enriches detection events from upstream
//! analyzers into structured `alert` source events. Serves as the terminal
//! stage before output (Stage 4 in the pipeline contract).
//!
//! ## Consumed Event Sources
//!
//! | Source              | Consumed events                         |
//! |--------------------|------------------------------------------|
//! | `handoff_injection`| INJECTION_DETECTED, IDENTITY_CONFUSION   |
//! | `multi_agent_anomaly`| ANOMALY (all categories)               |
//! | `causal_link`      | causal_link events                       |
//! | `context_pollution`| CONTEXT_POLLUTION_DETECTED               |
//! | `credential_leak`  | credential_leak events                   |
//!
//! ## Emitted Event Sources
//!
//! | Source  | When                                      |
//! |---------|-------------------------------------------|
//! | `alert` | Correlated, deduplicated alert is ready   |
//!
//! ## Risk Notes
//!
//! 1. This analyzer is stateless per event — it does not maintain cross-event
//!    buffers. Correlation logic lives in upstream analyzers (ContextPollution,
//!    HandoffInjection). This stage enriches and deduplicates only.
//!
//! 2. Alert deduplication uses a simple timestamp-based cooldown keyed by a
//!    composite of (scenario_id, rule_id, agent_root_pid). Under high event
//!    throughput with multiple scenarios, the cooldown map grows linearly with
//!    distinct (scenario, rule) pairs. This is bounded by the number of scenarios
//!    (typically < 20).

use super::{Analyzer, AnalyzerError};
use crate::framework::core::Event;
use crate::framework::runners::EventStream;
use async_trait::async_trait;
use futures::stream::{self, StreamExt};
use serde_json::{Value, json};
use std::collections::HashMap;

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

/// Default cooldown between duplicate alerts for the same dedup key.
const DEFAULT_ALERT_COOLDOWN_NS: u64 = 15_000_000_000; // 15 seconds

/// Sources that this analyzer consumes for alert enrichment.
const CONSUMED_SOURCES: &[&str] = &[
    "handoff_injection",
    "multi_agent_anomaly",
    "causal_link",
    "context_pollution",
    "credential_leak",
];

// ─────────────────────────────────────────────────────────────────────────────
// Alert Classification
// ─────────────────────────────────────────────────────────────────────────────

/// Alert severity levels.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum AlertSeverity {
    Info,
    Warning,
    Medium,
    High,
    Critical,
}

impl AlertSeverity {
    fn as_str(&self) -> &'static str {
        match self {
            Self::Info => "info",
            Self::Warning => "warning",
            Self::Medium => "medium",
            Self::High => "high",
            Self::Critical => "critical",
        }
    }

    /// Returns a numeric score for comparison (higher = more severe).
    fn score(&self) -> u8 {
        match self {
            Self::Info => 0,
            Self::Warning => 1,
            Self::Medium => 2,
            Self::High => 3,
            Self::Critical => 4,
        }
    }

    fn max(a: Self, b: Self) -> Self {
        if a.score() >= b.score() { a } else { b }
    }
}

impl From<&str> for AlertSeverity {
    fn from(s: &str) -> Self {
        match s {
            "critical" => Self::Critical,
            "high" => Self::High,
            "medium" => Self::Medium,
            "warning" => Self::Warning,
            _ => Self::Info,
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Analyzer
// ─────────────────────────────────────────────────────────────────────────────

/// Analyzer that enriches and deduplicates detection events into structured alerts.
pub struct AlertEnrichmentAnalyzer {
    /// Last emission timestamps for dedup keys.
    last_alert_ts: HashMap<String, u64>,
    /// Alert cooldown period in nanoseconds.
    cooldown_ns: u64,
}

impl AlertEnrichmentAnalyzer {
    /// Create a new `AlertEnrichmentAnalyzer` with default cooldown.
    pub fn new() -> Self {
        Self {
            last_alert_ts: HashMap::new(),
            cooldown_ns: DEFAULT_ALERT_COOLDOWN_NS,
        }
    }

    /// Create a new `AlertEnrichmentAnalyzer` with custom cooldown.
    pub fn with_cooldown(cooldown_ns: u64) -> Self {
        Self {
            last_alert_ts: HashMap::new(),
            cooldown_ns,
        }
    }

    /// Check whether an alert should be emitted (dedup + cooldown).
    fn should_emit_alert(&mut self, dedup_key: &str, timestamp: u64) -> bool {
        match self.last_alert_ts.get(dedup_key) {
            Some(last) if timestamp.saturating_sub(*last) < self.cooldown_ns => false,
            _ => {
                self.last_alert_ts.insert(dedup_key.to_string(), timestamp);
                true
            }
        }
    }

    /// Build the dedup key from an incoming event.
    fn build_dedup_key(&self, event: &Event) -> String {
        let scenario_id = event
            .data
            .get("scenario_id")
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        let rule_id = event
            .data
            .get("rule_id")
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        let agent_root_pid = event
            .data
            .get("agent")
            .and_then(|a| a.get("root_pid"))
            .and_then(Value::as_u64)
            .unwrap_or(0);

        format!("{}:{}:{}", scenario_id, rule_id, agent_root_pid)
    }

    /// Extract the severity from an incoming event, defaulting to "info".
    fn extract_severity(&self, event: &Event) -> AlertSeverity {
        event
            .data
            .get("severity")
            .and_then(Value::as_str)
            .map(AlertSeverity::from)
            .unwrap_or(AlertSeverity::Info)
    }

    /// Build a summary description from the event data.
    fn build_description(&self, event: &Event) -> String {
        // Try direct description field first
        if let Some(desc) = event.data.get("description").and_then(Value::as_str) {
            return desc.to_string();
        }

        // Try rule_id + scenario_id combination
        let rule_id = event
            .data
            .get("rule_id")
            .and_then(Value::as_str)
            .unwrap_or("alert");
        let scenario_id = event
            .data
            .get("scenario_id")
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        let event_type = event
            .data
            .get("event")
            .and_then(Value::as_str)
            .unwrap_or("detection");

        format!("{}: {} ({})", event_type, rule_id, scenario_id)
    }

    /// Process a single event — if it's from a consumed source, potentially emit
    /// an enriched alert event. Otherwise pass through unchanged.
    fn process_event(&mut self, event: &Event) -> Vec<Event> {
        // Only process events from consumed sources
        if !CONSUMED_SOURCES.contains(&event.source.as_str()) {
            return vec![event.clone()];
        }

        let dedup_key = self.build_dedup_key(event);
        if !self.should_emit_alert(&dedup_key, event.timestamp) {
            return vec![event.clone()];
        }

        let severity = self.extract_severity(event);
        let description = self.build_description(event);

        // Gather agent information
        let agent_info = event
            .data
            .get("agent")
            .cloned()
            .unwrap_or(json!({
                "event_pid": event.pid,
                "event_comm": event.comm,
            }));

        // Build the enriched alert event
        let alert_event = Event::new_with_timestamp(
            event.timestamp,
            "alert".to_string(),
            event.pid,
            event.comm.clone(),
            json!({
                "event": "ALERT",
                "alert_type": event.source,
                "original_source": event.source,
                "severity": severity.as_str(),
                "description": description,
                "dedup_key": dedup_key,
                "detected_at": event.timestamp,
                "agent": agent_info,
                "evidence": event.data,
            }),
        );

        vec![event.clone(), alert_event]
    }
}

impl Default for AlertEnrichmentAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Analyzer for AlertEnrichmentAnalyzer {
    async fn process(&mut self, stream: EventStream) -> Result<EventStream, AnalyzerError> {
        // Collect the stream eagerly to avoid `&mut self` lifetime conflicts in closures.
        // The event batch size is bounded by the tokio channel capacity.
        let events: Vec<Event> = stream.collect().await;
        let results: Vec<Event> = events
            .into_iter()
            .flat_map(|event| self.process_event(&event))
            .collect();

        Ok(Box::pin(futures::stream::iter(results)))
    }

    fn name(&self) -> &str {
        "AlertEnrichmentAnalyzer"
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use futures::stream::StreamExt;

    fn mk_event(timestamp: u64, source: &str, pid: u32, comm: &str, data: Value) -> Event {
        Event::new_with_timestamp(timestamp, source.to_string(), pid, comm.to_string(), data)
    }

    #[tokio::test]
    async fn enriches_handoff_injection_to_alert() {
        let mut analyzer = AlertEnrichmentAnalyzer::new();

        let input = vec![mk_event(
            1_000,
            "handoff_injection",
            100,
            "claude",
            json!({
                "event": "INJECTION_DETECTED",
                "rule_id": "shared_artifact_acceptance",
                "scenario_id": "agent-teams-context-pollution",
                "severity": "critical",
                "agent": {
                    "root_pid": 100,
                    "agent_id": "agent-100",
                },
            }),
        )];

        let stream = analyzer
            .process(Box::pin(futures::stream::iter(input)))
            .await
            .expect("analyzer should run");

        let output: Vec<Event> = stream.collect().await;
        let alert = output.iter().find(|e| e.source == "alert");
        assert!(alert.is_some(), "expected alert event");

        let ev = alert.unwrap();
        assert_eq!(
            ev.data.get("alert_type").and_then(Value::as_str),
            Some("handoff_injection")
        );
        assert_eq!(
            ev.data.get("severity").and_then(Value::as_str),
            Some("critical")
        );
    }

    #[tokio::test]
    async fn enriches_context_pollution_to_alert() {
        let mut analyzer = AlertEnrichmentAnalyzer::new();

        let input = vec![mk_event(
            1_000,
            "context_pollution",
            200,
            "claude",
            json!({
                "event": "CONTEXT_POLLUTION_DETECTED",
                "scenario_id": "agent-teams-context-pollution",
                "phase": "AlertEnriched",
                "severity": "critical",
                "propagation_count": 4,
            }),
        )];

        let stream = analyzer
            .process(Box::pin(futures::stream::iter(input)))
            .await
            .expect("analyzer should run");

        let output: Vec<Event> = stream.collect().await;
        let alert = output.iter().find(|e| e.source == "alert");
        assert!(alert.is_some(), "expected alert event");
        assert_eq!(
            alert.unwrap().data.get("alert_type").and_then(Value::as_str),
            Some("context_pollution")
        );
    }

    #[tokio::test]
    async fn non_consumed_sources_pass_through() {
        let mut analyzer = AlertEnrichmentAnalyzer::new();

        let input = vec![mk_event(
            1_000,
            "process",
            300,
            "claude",
            json!({"event": "FILE_OPEN", "filepath": "/tmp/test"}),
        )];

        let stream = analyzer
            .process(Box::pin(futures::stream::iter(input)))
            .await
            .expect("analyzer should run");

        let output: Vec<Event> = stream.collect().await;
        assert_eq!(output.len(), 1);
        assert_eq!(output[0].source, "process");
    }

    #[tokio::test]
    async fn deduplicates_within_cooldown() {
        let mut analyzer = AlertEnrichmentAnalyzer::new();

        let input = vec![
            mk_event(
                1_000,
                "handoff_injection",
                400,
                "claude",
                json!({
                    "event": "INJECTION_DETECTED",
                    "rule_id": "shared_artifact_acceptance",
                    "scenario_id": "agent-teams-context-pollution",
                    "severity": "high",
                    "agent": {"root_pid": 400},
                }),
            ),
            // Duplicate (same dedup key) within cooldown
            mk_event(
                2_000,
                "handoff_injection",
                400,
                "claude",
                json!({
                    "event": "INJECTION_DETECTED",
                    "rule_id": "shared_artifact_acceptance",
                    "scenario_id": "agent-teams-context-pollution",
                    "severity": "high",
                    "agent": {"root_pid": 400},
                }),
            ),
        ];

        let stream = analyzer
            .process(Box::pin(futures::stream::iter(input)))
            .await
            .expect("analyzer should run");

        let output: Vec<Event> = stream.collect().await;
        let alert_count = output.iter().filter(|e| e.source == "alert").count();
        assert_eq!(alert_count, 1, "should deduplicate within cooldown");
    }

    #[tokio::test]
    async fn enriches_multi_agent_anomaly_to_alert() {
        let mut analyzer = AlertEnrichmentAnalyzer::new();

        let input = vec![mk_event(
            1_000,
            "multi_agent_anomaly",
            500,
            "claude",
            json!({
                "event": "ANOMALY",
                "rule_id": "team_scale_causal_propagation",
                "category": "trust_propagation",
                "severity": "high",
                "description": "Shared file read by 3 distinct agents",
                "agent": {"root_pid": 500},
            }),
        )];

        let stream = analyzer
            .process(Box::pin(futures::stream::iter(input)))
            .await
            .expect("analyzer should run");

        let output: Vec<Event> = stream.collect().await;
        let alert = output.iter().find(|e| e.source == "alert");
        assert!(alert.is_some(), "expected alert event");
        assert_eq!(
            alert.unwrap().data.get("description").and_then(Value::as_str),
            Some("Shared file read by 3 distinct agents")
        );
    }
}
