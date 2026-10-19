// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// replay/ — Live-only Phase 2 replay and cross-run diff.
//
// Per spec §14:
//   - ReplayExecutor::reauthoritative() — full Phase 2 authoritative replay
//   - RunDiff/OpDiff — cross-run comparison of correlation evidence by op_type

pub mod executor;
pub mod run_diff;

pub use executor::{
    EvaluationMetrics as ReplayEvaluationMetrics, RefinementBundle, ReplayExecutor, ReplayResult,
};
pub use run_diff::{DiffChange, OpDiff, RunDiff, diff_runs};
