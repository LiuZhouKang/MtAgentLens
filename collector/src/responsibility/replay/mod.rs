// SPDX-License-Identifier: MIT
//
// replay/ — Offline audit, Phase 2 evaluation, and cross-run diff.
//
// Per spec §14:
//   - ReplayExecutor::reauthoritative() — full Phase 2 authoritative replay
//   - RunDiff/OpDiff — cross-run comparison of correlation evidence by op_type
//   - EvaluationMetrics — precision/recall/F1 from Phase 1 vs Phase 2 comparison
//   - AuditSummary — human-readable audit summary from replay results
//
// Backward-compatible re-exports from the existing modules are preserved
// at the bottom of this file.

// ── New modules (spec §14) ─────────────────────────────────────────────────
pub mod audit;
pub mod diff;
pub mod evaluation;
pub mod executor;

// ── Existing modules (backward compatibility) ─────────────────────────────
pub mod replay_executor;
pub mod run_diff;
pub mod traceback;

// ── New API exports ───────────────────────────────────────────────────────

pub use audit::AuditSummary;
pub use diff::{DiffChange, OpDiff, RunDiff, diff_runs, diff_runs_typed};
pub use evaluation::{
    ClaimRefinementBreakdown, ClassificationRefinementBreakdown, EvaluationMetrics,
    EvidenceRefinementBreakdown, GraphRefinementBreakdown, IdentityRefinementBreakdown,
};
pub use executor::{
    EvaluationMetrics as ReplayEvaluationMetrics, RefinementBundle, ReplayConfig, ReplayExecutor,
    ReplayResult, all_action_candidate_types,
};

// ── Existing re-exports (backward compatibility) ──────────────────────────

pub use replay_executor::{
    count_tracebacks_by_claim_state, load_audit_summary, load_traceback_replays,
    load_traceback_replays_for_agent, load_traceback_replays_in_range,
};
pub use run_diff::{
    DiffChange as LegacyDiffChange, OpDiff as LegacyOpDiff, RunDiff as LegacyRunDiff,
    diff_runs as legacy_diff_runs,
};
pub use traceback::{
    build_accountability_traceback_replay, build_accountable_chain_from_graphstore,
    build_minimal_accountable_chain,
};
