// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// claims/ — Claim pipeline: assembly, traceback, and text rendering.

// ── Legacy modules (backward compatibility) ────────────────────────────────
pub mod accountability_evidence;

// ── Authoritative modules ──────────────────────────────────────────────────
pub mod alert_builder;
pub mod attribution;
pub mod authorization;
pub mod claim_builder;
pub mod severity;
pub mod text;
pub mod traceback;

pub use severity::ClaimSeverity;

// ── Legacy re-exports ──────────────────────────────────────────────────────
pub use accountability_evidence::{
    accountability_boundary_id, accountability_evidence_contract,
    accountability_evidence_shape_is_valid, build_accountability_evidence_payload,
};
// ResponsibilityTraceback is defined in types/; re-export for backward compat
pub use crate::responsibility::types::ResponsibilityTraceback;

// ── Authoritative re-exports ───────────────────────────────────────────────
pub use alert_builder::AlertBuilder;
pub use attribution::{AttributionBuilder, AttributionResults};
pub use claim_builder::ClaimBuilder;
pub use text::AlertBuilderText;
pub use traceback::TracebackBuilder;
