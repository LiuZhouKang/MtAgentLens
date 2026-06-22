// SPDX-License-Identifier: MIT
//
// claims/ — Claim pipeline: assembly, traceback, and projection.

// ── Legacy modules (backward compatibility) ────────────────────────────────
pub mod accountability_claim;
pub mod accountability_evidence;
pub mod responsibility_evidence;
pub mod alert;
pub mod persistence;

// ── Authoritative modules ──────────────────────────────────────────────────
pub mod alert_builder;
pub mod claim_builder;
pub mod projection;
pub mod text;
pub mod traceback;

// ── Legacy re-exports ──────────────────────────────────────────────────────
pub use accountability_claim::{
    AccountabilityClaim, AccountabilityClaimNewState, AccountabilityClaimType, ClaimSeverity,
    CompetingExplanation,
};
pub use accountability_evidence::{
    accountability_boundary_id, accountability_evidence_contract,
    accountability_evidence_shape_is_valid, build_accountability_evidence_payload,
};
pub use alert::{
    AccountabilityAlert, AgentRef, BehaviorTimeline, BehaviorTimelineEntry,
    CausalGraphSnapshot, EngineSection,
};
pub use persistence::ResponsibilityWriter;
// ResponsibilityTraceback is defined in types/; re-export for backward compat
pub use crate::responsibility::types::ResponsibilityTraceback;

// ── Authoritative re-exports ───────────────────────────────────────────────
pub use alert_builder::AlertBuilder;
pub use claim_builder::ClaimBuilder;
pub use projection::{ClaimProjector, ProjectedAlert};
pub use text::AlertBuilderText;
pub use traceback::TracebackBuilder;
