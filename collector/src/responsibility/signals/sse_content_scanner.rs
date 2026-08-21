// SPDX-License-Identifier: MIT
//
// sse_content_scanner.rs — Scans SSE text events for indirect injection payloads
// that ContentBoundary cannot see (file contents, tool outputs, API responses).

use crate::responsibility::judgment::content::scan_text_layer1;
use crate::responsibility::judgment::ood_detector::{ANOMALY_THRESHOLD, OnlineOodDetector};
use crate::responsibility::types::ids::PolicyId;
use crate::responsibility::types::policy_types::{
    PolicyEvaluationResult, ResponsibilityClaimState,
};
use crate::responsibility::types::signal_types::{
    DetectedSignal, DetectionMethod, MatchLayer, SignalType,
};
use std::collections::HashSet;

/// Scans SSE stream text events for injection payloads.
/// Complements ContentBoundary (which only sees Intent.tool_args).
pub struct SseContentScanner {
    ood_detector: OnlineOodDetector,
    seen_events: HashSet<String>,
    events_scanned: u64,
    signals_produced: u64,
}

impl Default for SseContentScanner {
    fn default() -> Self {
        Self::new()
    }
}

impl SseContentScanner {
    pub fn new() -> Self {
        Self {
            ood_detector: OnlineOodDetector::new(0),
            seen_events: HashSet::new(),
            events_scanned: 0,
            signals_produced: 0,
        }
    }

    /// Scan an SSE text event for injection content.
    /// Returns detected signals to be fed into GraphBuilder.
    ///
    /// `event_type`: "tool_result", "text_delta", "content_block_delta"
    /// `event_id`:   unique SSE event ID for dedup
    /// `event_data`: the text content to scan
    /// `timestamp_ns`: event timestamp
    /// `tool_name`:  associated tool name for context (e.g. "Read", "Bash")
    pub fn scan_event(
        &mut self,
        event_type: &str,
        event_id: &str,
        event_data: &str,
        timestamp_ns: u64,
        tool_name: &str,
    ) -> Vec<DetectedSignal> {
        if !self.seen_events.insert(event_id.to_string()) {
            return vec![];
        }

        let should_scan = match event_type {
            "tool_result" => true,
            "content_block_delta" => true,
            "text_delta" => event_data.len() > 50,
            _ => false,
        };

        if !should_scan || event_data.is_empty() {
            return vec![];
        }

        self.events_scanned += 1;

        // Layer 1: Pattern matching (reuses content.rs)
        if let Some(result) = scan_text_layer1(event_data, tool_name) {
            let signal = build_signal(&result, event_data, timestamp_ns, event_type, tool_name);
            self.signals_produced += 1;
            return vec![signal];
        }

        // Layer 4: OOD detection
        if let Some(anomaly_score) = self
            .ood_detector
            .detect(event_data, tool_name, timestamp_ns)
            && anomaly_score > ANOMALY_THRESHOLD
        {
            let result = PolicyEvaluationResult {
                policy_id: Some(PolicyId::from("policy.content.ood")),
                boundary_id: Some(crate::responsibility::types::ids::BoundaryId::from(
                    "boundary.content.anomaly",
                )),
                claim_state: ResponsibilityClaimState::Observed,
                matched: true,
                reason: format!(
                    "SSE content: OOD anomaly in {} (score={:.3})",
                    tool_name, anomaly_score
                ),
                confidence: anomaly_score,
                matched_layer: MatchLayer::Layer4Ood {
                    score: anomaly_score as f64,
                },
            };
            let signal = build_signal(&result, event_data, timestamp_ns, event_type, tool_name);
            self.signals_produced += 1;
            return vec![signal];
        }

        vec![]
    }

    pub fn events_scanned(&self) -> u64 {
        self.events_scanned
    }
    pub fn signals_produced(&self) -> u64 {
        self.signals_produced
    }
}

fn build_signal(
    result: &PolicyEvaluationResult,
    text: &str,
    timestamp_ns: u64,
    event_type: &str,
    tool_name: &str,
) -> DetectedSignal {
    let snippet = if text.len() > 512 {
        format!("{}...", &text[..509])
    } else {
        text.to_string()
    };
    DetectedSignal {
        signal_type: SignalType::ContentInjection,
        method: match &result.matched_layer {
            MatchLayer::Layer1Pattern { pattern, .. } => DetectionMethod::Regex(pattern.clone()),
            MatchLayer::Layer4Ood { score } => DetectionMethod::Heuristic {
                score: *score,
                factors: vec![format!("ood={:.3}", score)],
            },
            _ => DetectionMethod::Regex("unknown".into()),
        },
        detection_confidence: result.confidence as f64,
        timestamp_ns,
        raw_snippet: Some(snippet),
        policy_id: result.policy_id.clone(),
        boundary_id: result.boundary_id.clone(),
        reason: format!(
            "[SSE scan:{}] {} in {}",
            event_type, result.reason, tool_name
        ),
        correlated_edges: vec![],
    }
}
