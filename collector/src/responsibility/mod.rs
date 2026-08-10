pub mod api_contracts;
pub mod claims;
pub mod classification;
pub mod cross_layer_bus;
pub mod evidence;
pub mod graph;
pub mod identity;
pub mod judgment;
pub mod observer;
pub mod output;
pub mod persistence;
pub mod replay;
pub mod runtime;
pub mod scoring;
pub mod server;
pub mod signals;
pub mod types;

pub use claims::{
    AccountabilityAlert, AgentRef, BehaviorTimelineEntry, CausalGraphSnapshot, EngineSection,
    accountability_boundary_id, accountability_evidence_contract,
    accountability_evidence_shape_is_valid, build_accountability_evidence_payload,
};
pub use classification::{
    CacheTTL, ClassificationEngine, ObserverDecision, ObserverError, ObserverOutput,
    ObserverSignature, Route,
};
pub use evidence::{
    ActionCandidate, ActionCandidateType, CandidateDerivationMode, CorrelationEvidence,
    EvidenceCorrelator, EvidenceDiagnostic, EvidenceDirectnessNew, EvidenceRecordNew, EvidenceType,
    Lossiness, MatchLevel, MatchedField, NonLinkDiagnostic, NonLinkReason, PendingArbitration,
    SignalClass, rank_legacy_correlation_evidence,
};
pub use graph::{
    GraphDiff, GraphStore, NewResponsibilityEdge, NewResponsibilityEdgeType, NewResponsibilityNode,
    NewResponsibilityNodeType, ResponsibilityEdgeType, ResponsibilityNodeType,
};
pub use identity::{
    AuthoritativeResolution, IdentityProvider, IdentityResolver, UnresolvedDiagnostic,
    UnresolvedReason,
};
pub use judgment::{
    DelegationRecord, DelegationTracker, evaluate_accountability_policy, evaluate_boundary_policy,
    evaluate_boundary_policy_with_resource,
};
pub use persistence::{SqliteWriter, open_db, open_db_read_only};
pub use replay::{
    DiffChange, OpDiff, RunDiff, build_accountability_traceback_replay,
    build_accountable_chain_from_graphstore, build_minimal_accountable_chain,
    count_tracebacks_by_claim_state, diff_runs, load_audit_summary, load_traceback_replays,
    load_traceback_replays_for_agent, load_traceback_replays_in_range,
};
pub use signals::SignalGraph;
pub use types::observation_types::{
    NormalizedResource, ObservationRecord, ObservationSource, ObservationType, ProcessIdentity,
    RedactionStatus,
};
// Legacy types from support/ — migrated to types/support_types.rs (§17.4).
// Only types actually in support_types.rs are re-exported. Functional modules
// (pattern_matcher, observer_cache, etc.) are re-exported from their new
// canonical locations (graph/, observer/, evidence/).
pub use types::support_types::{
    AccountabilityAuditSummary, AccountabilitySnapshot, AccountabilityTraceback,
    AccountabilityTracebackReplay, ActionEvent, AgentInstance, CausalEdge, CausalNode,
    CausalNodeType, CausalityType, EvidenceDirectness, EvidenceRecord, EvidenceRef,
    HandoffInjectionRecord, IntentAuthoritySource, IntentClass, IntentEvent, IntentParseState,
    PolicyEvaluationResult, ResourceId, ResponsibilityClaimState, ViolationPatternMatchRecord,
    classify_intent_class, is_primary_matching_tool,
};
