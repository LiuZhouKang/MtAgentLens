// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use super::ids::*;
use serde::{Deserialize, Serialize};

// ═══ Identity Refinement ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IdentityRefinement {
    pub phase1_agent_id: AgentId,
    pub phase2_agent_id: AgentId,
    pub refinement_type: IdentityRefinementType,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum IdentityRefinementType {
    Confirmed,
    Merged,
    Split,
    Reassigned,
    Dismissed,
}

// ═══ Evidence Refinement ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EvidenceRefinement {
    pub phase1_evidence_id: EvidenceId,
    pub phase2_evidence_id: Option<EvidenceId>,
    pub refinement_type: EvidenceRefinementType,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum EvidenceRefinementType {
    Confirmed,
    Upgraded,
    Downgraded,
    Dismissed,
    Discovered,
}

// ═══ Graph Refinement ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphRefinement {
    pub refinement_type: GraphRefinementType,
    pub affected_nodes: Vec<NodeId>,
    pub affected_edges: Vec<EdgeId>,
    pub description: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum GraphRefinementType {
    AgentMerged,
    AgentSplit,
    AgentReassigned,
    EdgeUpgraded,
    EdgeDowngraded,
    EdgeAdded,
    EdgeRemoved,
}

// ═══ Classification Refinement ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClassificationRefinement {
    pub phase1_alert_id: AlertId,
    pub phase2_alert_id: AlertId,
    pub refinement_type: ClassificationRefinementType,
    pub reason: String,
    pub score_delta: f32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ClassificationRefinementType {
    Confirmed,
    Changed,
    Dismissed,
    Discovered,
}

// ═══ Claim Refinement ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClaimRefinement {
    pub phase1_claim_id: ClaimId,
    pub phase2_claim_id: ClaimId,
    pub refinement_type: ClaimRefinementType,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ClaimRefinementType {
    Confirmed,
    SeverityChanged,
    Dismissed,
    Discovered,
}

// ═══ Classification scoring ═══

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ScoreBreakdown {
    pub rule_score: f32,
    pub pattern_score: f32,
    pub boundary_score: f32,
    pub evidence_score: f32,
    pub role_score: f32,
    pub repetition_score: f32,
    pub behavioral_score: f32,
    pub total: f32,
    /// Fused pre-Platt value. Audit fields: without them a persisted row
    /// cannot be checked against the scoring formula.
    #[serde(default)]
    pub raw_score: f32,
    /// Platt output before the evidence gate.
    #[serde(default)]
    pub calibrated_score: f32,
    /// Evidence-gate factor applied after calibration.
    #[serde(default)]
    pub gate_factor: f32,
    /// Which gate formula produced `gate_factor` (v1/v2/off).
    #[serde(default = "default_gate_version")]
    pub gate_version: String,
    #[serde(default)]
    pub platt_a: f32,
    #[serde(default)]
    pub platt_b: f32,
    /// True when an Observer verdict adjusted the final decision/score.
    #[serde(default)]
    pub observer_adjusted: bool,
}

fn default_gate_version() -> String {
    "legacy".to_string()
}

impl Default for ScoreBreakdown {
    fn default() -> Self {
        Self {
            rule_score: 0.0,
            pattern_score: 0.0,
            boundary_score: 0.0,
            evidence_score: 0.0,
            role_score: 0.0,
            repetition_score: 0.0,
            behavioral_score: 0.0,
            total: 0.0,
            raw_score: 0.0,
            calibrated_score: 0.0,
            gate_factor: 1.0,
            gate_version: default_gate_version(),
            platt_a: 0.0,
            platt_b: 0.0,
            observer_adjusted: false,
        }
    }
}
