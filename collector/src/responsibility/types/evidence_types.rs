// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use super::ids::*;
use super::phase::PhaseIndicator;
use serde::{Deserialize, Serialize};

// ═══ Intent and Action types ═══

/// Wire format that supplied a captured native tool-call id.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum NativeToolCallFormat {
    Anthropic,
    OpenAi,
}

impl NativeToolCallFormat {
    /// Stable ordering for canonical snapshots.
    pub fn rank(self) -> u8 {
        match self {
            Self::Anthropic => 0,
            Self::OpenAi => 1,
        }
    }
}

/// A tool-call id observed in captured structured content.
///
/// This is a correlation reference, not authentication: it is preserved
/// byte-for-byte and never synthesised by the collector.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct NativeToolCallRef {
    pub format: NativeToolCallFormat,
    pub id: String,
}

/// One structured native tool-call reference observed at PRE-resolver time.
///
/// The reference is a protocol correlation key, not an authenticated identity.
/// `declared_conversation_id` is filled only when the same captured request
/// body declared it; a response normally carries `None`.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct NativeToolCallObservation {
    pub process_id: ProcessId,
    pub message_id: MessageId,
    pub native_tool_call: NativeToolCallRef,
    #[serde(default)]
    pub declared_conversation_id: Option<String>,
}

impl NativeToolCallObservation {
    /// Canonical order shared by the frozen snapshot and the identity DTO.
    pub fn cmp_canonical(&self, other: &Self) -> std::cmp::Ordering {
        (
            self.process_id.pid,
            self.process_id.start_time_ns,
            self.native_tool_call.format.rank(),
            self.native_tool_call.id.as_str(),
            self.declared_conversation_id.as_deref(),
        )
            .cmp(&(
                other.process_id.pid,
                other.process_id.start_time_ns,
                other.native_tool_call.format.rank(),
                other.native_tool_call.id.as_str(),
                other.declared_conversation_id.as_deref(),
            ))
            .then_with(|| self.message_id.as_str().cmp(other.message_id.as_str()))
    }
}

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
    #[serde(default)]
    pub native_tool_call: Option<NativeToolCallRef>,
    #[serde(default)]
    pub declared_conversation_id: Option<String>,
}

/// Completion of the observed operation, independent of its suspiciousness.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ActionOutcome {
    #[default]
    Unknown,
    Attempted,
    Pending,
    Succeeded,
    Failed,
}
impl ActionOutcome {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Unknown => "unknown",
            Self::Attempted => "attempted",
            Self::Pending => "pending",
            Self::Succeeded => "succeeded",
            Self::Failed => "failed",
        }
    }
    pub fn is_success(self) -> bool {
        self == Self::Succeeded
    }

    /// Whether the observation proves the operation was *initiated* even though
    /// it did not complete in the captured window.
    ///
    /// A non-blocking `connect(2)` returns `EINPROGRESS`: the kernel accepted the
    /// request and will finish it asynchronously, so the destination is a fact
    /// even though no completion was observed. Treating that as "nothing
    /// happened" hid every egress made through a non-blocking HTTP client.
    pub fn is_initiated(self) -> bool {
        matches!(self, Self::Attempted | Self::Pending)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Action {
    #[serde(default)]
    pub outcome: ActionOutcome,
    #[serde(default)]
    pub syscall_ret: Option<i64>,
    #[serde(default)]
    pub syscall_name: Option<String>,
    pub action_id: ActionId,
    pub process_id: ProcessId,
    pub action_type: ActionCandidateType,
    pub normalized_args: Vec<NormalizedArg>,
    pub derivation_mode: CandidateDerivationMode,
    pub signal_class: SignalClass,
    pub timestamp_ns: u64,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct NormalizedArgs {
    pub canonical_path: Option<String>,
    pub host: Option<String>,
    pub port: Option<u16>,
    pub command: Option<String>,
    pub raw_args: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum NormalizedArg {
    Path(String),
    /// Capture completeness, not another syscall operand or matchable path.
    PathCaptureIncomplete {
        normalized_path: String,
        raw_byte_len: usize,
    },
    OpenFlags(i32),
    Command(String),
    Url {
        host: String,
        port: Option<u16>,
    },
    /// Raw text content from tool input fields (command, content,
    /// old_string, new_string). Enables content-based boundary detection.
    Content(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum IntentClass {
    Orchestration,
    SideEffect,
    Unknown,
}

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
pub enum IntentParseState {
    Complete,
    Partial,
    Malformed,
    Unsupported,
}

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
    /// Read from the agent's own on-disk trajectory (out-of-band). This is an
    /// *asserted* source: the agent wrote it, we did not observe it.
    OutOfBandAsserted,
}

impl std::fmt::Display for IntentAuthoritySource {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let value = match self {
            Self::RequestHistoryAssistantToolUse => "request_history_assistant_tool_use",
            Self::SseResponseToolUse => "sse_response_tool_use",
            Self::HttpResponseToolUse => "http_response_tool_use",
            Self::TextFallback => "text_fallback",
            Self::OutOfBandAsserted => "out_of_band_asserted",
        };
        write!(f, "{}", value)
    }
}

// ═══ Action classification ═══

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum ActionCandidateType {
    /// Opening a descriptor does not establish bytes read or written.
    FileOpen,
    FileRead,
    FileWrite,
    FileOverwrite,
    FileDelete,
    /// A rename moves a directory entry: the source disappears and the
    /// destination appears. It is not an overwrite of either path, and the
    /// destination is the fact an accountability trail needs.
    FileRename,
    NetworkConnect,
    NetworkSend,
    ProcessExec,
    ProcessSpawn,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum CandidateDerivationMode {
    Direct,
    Aggregated,
    MultiEmit,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum SignalClass {
    HighSignal,
    LowSignal,
    InternalHousekeeping,
}

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

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum MatchLevel {
    L1Exact = 5,       // 0.90
    L2Normalized = 4,  // 0.75
    L3Containment = 3, // 0.65
    L4Basename = 2,    // 0.50
    L5Substring = 1,   // 0.35
    /// The action was performed by a descendant process of the process the
    /// intent spawned (a build script, package manager hook, or installer).
    /// No argument is shared, because the agent never typed the command that
    /// produced the action — the program it ran did. This is the OS-level
    /// reconciliation that prompt-level tools cannot perform.
    ProcessLineage = 0,
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
    IntentObservation,
    ActionCandidate,
    ActionCorrelation,
    CapabilityUse,
    ContextInheritanceClue,
    TaintPropagationClue,
    BlockedAttempt,
}
