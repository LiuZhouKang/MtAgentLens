use serde::{Deserialize, Serialize};
use super::ids::*;
use super::phase::PhaseIndicator;

// ═══ Intent and Action types ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Intent {
    pub intent_id: IntentId,
    pub process_id: ProcessId,
    pub message_id: MessageId,
    pub tool_name: String,
    pub tool_args: Vec<NormalizedArg>,
    pub intent_class: IntentClass,
    pub parse_state: IntentParseState,
    pub primary_matching_eligible: bool,
    pub authority_source: IntentAuthoritySource,
    pub timestamp_ns: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IntentArgs {
    pub path: Option<String>,
    pub command: Option<String>,
    pub url: Option<String>,
    pub host: Option<String>,
    pub port: Option<u16>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Action {
    pub action_id: ActionId,
    pub process_id: ProcessId,
    pub action_type: ActionCandidateType,
    pub normalized_args: Vec<NormalizedArg>,
    pub derivation_mode: CandidateDerivationMode,
    pub signal_class: SignalClass,
    pub timestamp_ns: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NormalizedArgs {
    pub canonical_path: Option<String>,
    pub host: Option<String>,
    pub port: Option<u16>,
    pub command: Option<String>,
    pub raw_args: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum NormalizedArg {
    Path(String),
    Command(String),
    Url { host: String, port: Option<u16> },
    /// Raw text content from tool input fields (command, content,
    /// old_string, new_string). Enables content-based boundary detection.
    Content(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum IntentClass { Orchestration, SideEffect, Unknown }

impl std::fmt::Display for IntentClass {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Orchestration => write!(f, "orchestration"),
            Self::SideEffect => write!(f, "side_effect"),
            Self::Unknown => write!(f, "unknown"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum IntentParseState { Complete, Partial, Malformed, Unsupported }

impl std::fmt::Display for IntentParseState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Complete => write!(f, "complete"),
            Self::Partial => write!(f, "partial"),
            Self::Malformed => write!(f, "malformed"),
            Self::Unsupported => write!(f, "unsupported"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum IntentAuthoritySource {
    RequestHistoryAssistantToolUse,
    SseResponseToolUse,
    HttpResponseToolUse,
    TextFallback,
}

impl std::fmt::Display for IntentAuthoritySource {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let value = match self {
            Self::RequestHistoryAssistantToolUse => "request_history_assistant_tool_use",
            Self::SseResponseToolUse => "sse_response_tool_use",
            Self::HttpResponseToolUse => "http_response_tool_use",
            Self::TextFallback => "text_fallback",
        };
        write!(f, "{}", value)
    }
}

// ═══ Action classification ═══

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum ActionCandidateType {
    FileRead, FileWrite, FileOverwrite, FileDelete,
    NetworkConnect, NetworkSend,
    ProcessExec, ProcessSpawn,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum CandidateDerivationMode { Direct, Aggregated, MultiEmit }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum SignalClass { HighSignal, LowSignal, InternalHousekeeping }

// ═══ Correlation evidence ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CorrelationEvidence {
    pub evidence_id: EvidenceId,
    pub intent_id: IntentId,
    pub action_id: ActionId,
    pub match_level: MatchLevel,
    pub confidence: f32,
    pub matched_fields: NormalizedArgs,
    pub derivation_mode: CandidateDerivationMode,
    pub phase: PhaseIndicator,
    pub timestamp_ns: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord)]
#[derive(Hash)]
pub enum MatchLevel {
    L1Exact = 5,       // 0.90
    L2Normalized = 4,   // 0.75
    L3Containment = 3,  // 0.65
    L4Basename = 2,     // 0.50
    L5Substring = 1,    // 0.35
}

// ═══ Non-link diagnostics ═══

/// Canonical NonLinkDiagnostic struct.
///
/// Used by both the legacy correlator and the new evidence pipeline.
/// Includes backward-compatible `::new()` constructor and `.with_*()` builders.
/// Field names and types match the old schema for backward compatibility.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct NonLinkDiagnostic {
    pub schema_version: u32,
    pub diagnostic_id: String,
    pub timestamp_ns: u64,
    pub agent_id: Option<String>,
    pub intent_id: Option<String>,
    /// Legacy name; new code should treat this as an action_id.
    pub candidate_id: Option<String>,
    pub reason: NonLinkReason,
    pub stage: String,
    /// Stored as String for backward compat with both old and new enum defs.
    pub intent_class: Option<String>,
    /// Stored as String for backward compat with both old and new enum defs.
    pub parse_state: Option<String>,
    pub match_level_considered: Option<String>,
    pub confidence_observed: Option<f32>,
    pub details: Option<serde_json::Value>,
}

pub const NON_LINK_DIAGNOSTIC_SCHEMA_VERSION: u32 = 1;

impl NonLinkDiagnostic {
    /// Legacy constructor — matches old calling convention.
    pub fn new(
        timestamp_ns: u64,
        agent_id: Option<String>,
        intent_id: Option<String>,
        candidate_id: Option<String>,
        reason: NonLinkReason,
        stage: String,
    ) -> Self {
        Self {
            schema_version: NON_LINK_DIAGNOSTIC_SCHEMA_VERSION,
            diagnostic_id: uuid::Uuid::new_v4().to_string(),
            timestamp_ns,
            agent_id,
            intent_id,
            candidate_id,
            reason,
            stage,
            intent_class: None,
            parse_state: None,
            match_level_considered: None,
            confidence_observed: None,
            details: None,
        }
    }

    /// Set the intent class. Accepts any displayable type for backward
    /// compatibility with both old and new `IntentClass` enums.
    pub fn with_intent_class(mut self, intent_class: impl std::fmt::Display) -> Self {
        self.intent_class = Some(intent_class.to_string());
        self
    }

    /// Set the parse state. Accepts any displayable type for backward
    /// compatibility with both old and new `IntentParseState` enums.
    pub fn with_parse_state(mut self, parse_state: impl std::fmt::Display) -> Self {
        self.parse_state = Some(parse_state.to_string());
        self
    }

    pub fn with_match_level_considered(mut self, match_level_considered: String) -> Self {
        self.match_level_considered = Some(match_level_considered);
        self
    }

    pub fn with_confidence_observed(mut self, confidence_observed: f32) -> Self {
        self.confidence_observed = Some(confidence_observed);
        self
    }

    pub fn with_details(mut self, details: serde_json::Value) -> Self {
        self.details = Some(details);
        self
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum NonLinkReason {
    NoArgumentMatch,
    CandidateNotAvailable,
    BelowConfidenceCutoff,
    OutsideTimeWindow,
    AmbiguousCompetition,
    IntentNotPrimaryEligible,
    ObserverPending,
}

impl std::fmt::Display for NonLinkReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NoArgumentMatch => write!(f, "no_argument_match"),
            Self::CandidateNotAvailable => write!(f, "candidate_not_available"),
            Self::BelowConfidenceCutoff => write!(f, "below_confidence_cutoff"),
            Self::OutsideTimeWindow => write!(f, "outside_time_window"),
            Self::AmbiguousCompetition => write!(f, "ambiguous_competition"),
            Self::IntentNotPrimaryEligible => write!(f, "intent_not_primary_eligible"),
            Self::ObserverPending => write!(f, "observer_pending"),
        }
    }
}

// ═══ Evidence record ═══

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum EvidenceType {
    IntentObservation, ActionCandidate, ActionCorrelation,
    CapabilityUse, ContextInheritanceClue, TaintPropagationClue, BlockedAttempt,
}
