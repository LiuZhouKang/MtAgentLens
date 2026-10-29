// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

pub mod claims;
pub mod classification;
pub mod evidence;
pub mod graph;
pub mod identity;
pub mod judgment;
pub mod observer;
pub mod outofband;
pub mod output;
pub mod persistence;
pub mod provenance;
pub mod replay;
pub mod runtime;
pub mod scoring;
pub mod signals;
pub mod types;
pub mod workspace;

pub use claims::{
    accountability_boundary_id, accountability_evidence_contract,
    accountability_evidence_shape_is_valid, build_accountability_evidence_payload,
};
pub use classification::{
    CacheTTL, ClassificationEngine, ObserverDecision, ObserverError, ObserverOutput,
    ObserverSignature, Route,
};
pub use evidence::{EvidenceCorrelator, NonLinkDiagnostic, NonLinkReason};
pub use graph::{
    GraphDiff, NewResponsibilityEdge, NewResponsibilityEdgeType, NewResponsibilityNode,
    NewResponsibilityNodeType, ResponsibilityEdgeType, ResponsibilityNodeType,
};
pub use identity::{
    AuthoritativeResolution, IdentityProvider, IdentityResolver, UnresolvedDiagnostic,
    UnresolvedReason,
};
pub use judgment::{
    DelegationObservation, DelegationTracker, evaluate_accountability_policy,
    evaluate_boundary_policy, evaluate_boundary_policy_with_resource,
};
pub use persistence::open_db_read_only;
pub use replay::{DiffChange, OpDiff, RunDiff, diff_runs};
pub use signals::SignalGraph;
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
