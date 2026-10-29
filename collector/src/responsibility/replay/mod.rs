// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// replay/ — Live-only Phase 2 replay and cross-run diff.
//
// Per spec §14:
//   - ReplayExecutor::reauthoritative() — full Phase 2 authoritative replay
//   - RunDiff/OpDiff — cross-run comparison of correlation evidence by op_type

pub mod ablation;
pub mod checkpoint;
pub mod executor;
pub mod run_diff;
pub mod run_metrics;
pub mod sensitivity;

pub use executor::{
    EvaluationMetrics as ReplayEvaluationMetrics, RefinementBundle, ReplayExecutor, ReplayResult,
};
pub use run_diff::{DiffChange, OpDiff, RunDiff, diff_runs};
pub use run_metrics::{
    ActionOutcomeCounts, IdentityCoverage, KindCoverage, RUN_METRICS_KIND, RUN_METRICS_VERSION,
    RunMetrics,
};
