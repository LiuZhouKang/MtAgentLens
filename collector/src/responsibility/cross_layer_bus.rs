// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// cross_layer_bus.rs — Unified cross-layer event bus (§17).
//
// Connects eBPF signals → runtime → scoring → claims → alerts through a
// single typed event bus. Each layer publishes structured events; downstream
// subscribers consume them without needing direct module coupling.
//
// Architecture:
//
//   eBPF Raw Events  ──→ route_event() ──→ CrossLayerBus.publish(EventEnvelope)
//                                              │
//                    ┌─────────────────────────┼─────────────────────────┐
//                    │                         │                         │
//              runtime::trigger()    scoring::ProgressiveScorer    claims::ClaimBuilder
//                    │                         │                         │
//              ClassificationResult    ProgressiveScore           ResponsibilityClaim
//                    │                         │                         │
//                    └─────────────────────────┼─────────────────────────┘
//                                              │
//                                    AlertBuilder::compose()
//                                              │
//                                    broadcast::Sender<ResponsibilityAlert> → SSE → Frontend

use crate::responsibility::types::alert_types::ResponsibilityAlert;
use crate::responsibility::types::ids::{AgentId, ClaimId, EdgeId};
use std::sync::Arc;
use tokio::sync::broadcast;

// ── Event Envelope ───────────────────────────────────────────────────────

/// Unified event envelope that flows through the cross-layer bus.
/// Each layer wraps its output in this envelope for downstream consumers.
#[derive(Debug, Clone)]
pub struct LayerEvent {
    /// Event origin layer.
    pub layer: Layer,
    /// Event timestamp (epoch ns).
    pub timestamp_ns: u64,
    /// Layer-specific payload serialized as JSON.
    pub payload: serde_json::Value,
}

/// Which layer produced this event.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Layer {
    /// eBPF probe — raw kernel events
    Probe,
    /// Runtime routing — intent/action extraction
    Runtime,
    /// Scoring engine — progressive dimension scores
    Scoring,
    /// Classification engine — rule/pattern evaluation
    Classification,
    /// Claims — boundary crossing claims
    Claims,
    /// Alerts — composed alert for frontend
    Alert,
}

impl Layer {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Probe => "probe",
            Self::Runtime => "runtime",
            Self::Scoring => "scoring",
            Self::Classification => "classification",
            Self::Claims => "claims",
            Self::Alert => "alert",
        }
    }
}

// ── Scoring Signal ───────────────────────────────────────────────────────

/// Scoring layer event payload: progressive score update for an agent.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ScoringSignal {
    pub agent_id: AgentId,
    /// Fused (OWA+GeoMean) progressive score.
    pub fused_score: f64,
    /// Individual dimension scores.
    pub dimension_scores: [f64; 6],
    /// Whether any CUSUM alarm is active.
    pub cusum_triggered: bool,
    /// Whether evidence conflict is detected.
    pub evidence_conflict: bool,
}

// ── Classification Signal ────────────────────────────────────────────────

/// Classification layer event payload.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ClassificationSignal {
    pub score: f32,
    pub route: String,
    /// Whether the Observer LLM was invoked.
    pub observer_called: bool,
    /// Trigger reason for the observer call (empty if not called).
    pub observer_trigger_reason: String,
}

// ── Claim Signal ─────────────────────────────────────────────────────────

/// Claims layer event payload.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ClaimSignal {
    pub claim_id: ClaimId,
    pub agent_id: AgentId,
    pub edge_id: Option<EdgeId>,
    pub confidence: f32,
    pub is_new: bool, // false if already claimed in prior cycle
}

// ── Cross-Layer Bus ──────────────────────────────────────────────────────

/// Unified cross-layer event bus.
///
/// Provides per-layer broadcast channels so subscribers can filter by layer.
/// The main alert channel delivers composed alerts to SSE / frontend.
pub struct CrossLayerBus {
    /// Scoring layer events (progressive scores, CUSUM alarms).
    pub scoring_tx: broadcast::Sender<ScoringSignal>,
    /// Classification layer events.
    pub classification_tx: broadcast::Sender<ClassificationSignal>,
    /// Claims layer events.
    pub claims_tx: broadcast::Sender<ClaimSignal>,
    /// Alert layer events (composed ResponsibilityAlert).
    pub alert_tx: broadcast::Sender<ResponsibilityAlert>,
}

impl CrossLayerBus {
    /// Create a new cross-layer bus with default channel capacity (256).
    pub fn new() -> Self {
        let (scoring_tx, _) = broadcast::channel(256);
        let (classification_tx, _) = broadcast::channel(256);
        let (claims_tx, _) = broadcast::channel(256);
        let (alert_tx, _) = broadcast::channel(256);
        Self {
            scoring_tx,
            classification_tx,
            claims_tx,
            alert_tx,
        }
    }

    /// Create a bus wrapping a pre-existing alert channel.
    ///
    /// Used when the runtime receives an alert_tx from main.rs
    /// (which already has SSE subscribers attached).
    pub fn with_alert_tx(alert_tx: broadcast::Sender<ResponsibilityAlert>) -> Self {
        let (scoring_tx, _) = broadcast::channel(256);
        let (classification_tx, _) = broadcast::channel(256);
        let (claims_tx, _) = broadcast::channel(256);
        Self {
            scoring_tx,
            classification_tx,
            claims_tx,
            alert_tx,
        }
    }

    /// Publish a scoring signal to all scoring subscribers.
    pub fn emit_scoring(&self, signal: ScoringSignal) {
        let _ = self.scoring_tx.send(signal);
    }

    /// Publish a classification signal.
    pub fn emit_classification(&self, signal: ClassificationSignal) {
        let _ = self.classification_tx.send(signal);
    }

    /// Publish a claim signal.
    pub fn emit_claim(&self, signal: ClaimSignal) {
        let _ = self.claims_tx.send(signal);
    }

    /// Publish a composed alert to all alert subscribers (SSE, frontend).
    pub fn emit_alert(&self, alert: ResponsibilityAlert) {
        let _ = self.alert_tx.send(alert);
    }

    /// Subscribe to scoring layer events.
    pub fn subscribe_scoring(&self) -> broadcast::Receiver<ScoringSignal> {
        self.scoring_tx.subscribe()
    }

    /// Subscribe to classification layer events.
    pub fn subscribe_classification(&self) -> broadcast::Receiver<ClassificationSignal> {
        self.classification_tx.subscribe()
    }

    /// Subscribe to claims layer events.
    pub fn subscribe_claims(&self) -> broadcast::Receiver<ClaimSignal> {
        self.claims_tx.subscribe()
    }

    /// Subscribe to alert layer events.
    pub fn subscribe_alerts(&self) -> broadcast::Receiver<ResponsibilityAlert> {
        self.alert_tx.subscribe()
    }
}

impl Default for CrossLayerBus {
    fn default() -> Self {
        Self::new()
    }
}

/// Shared cross-layer bus reference (Arc for multi-threaded access).
pub type SharedBus = Arc<CrossLayerBus>;
