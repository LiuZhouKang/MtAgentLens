use crate::responsibility::evidence::correlator_legacy::MatchLevel;
use crate::responsibility::types::observation_types::NormalizedResource;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceType {
    IntentObservation,
    ActionCandidate,
    ActionCorrelation,
    CapabilityUse,
    ContextInheritanceClue,
    TaintPropagationClue,
    BlockedAttempt,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceDirectnessNew {
    Direct,
    Inferred,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct MatchedField {
    pub field: String,
    pub observed: String,
    pub expected: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EvidenceDiagnostic {
    pub diagnostic_id: String,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct EvidenceRecordNew {
    pub evidence_id: String,
    pub evidence_type: EvidenceType,
    pub directness: EvidenceDirectnessNew,
    pub match_level: Option<MatchLevel>,
    pub observation_refs: Vec<String>,
    pub subject_hint: Option<String>,
    pub resource_ref: Option<NormalizedResource>,
    pub rule_id: String,
    pub matched_fields: Vec<MatchedField>,
    pub confidence: f32,
    pub diagnostics: Vec<EvidenceDiagnostic>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct EvidenceReconstruction {
    pub evidence: Vec<EvidenceRecordNew>,
    pub diagnostics: Vec<EvidenceDiagnostic>,
}
