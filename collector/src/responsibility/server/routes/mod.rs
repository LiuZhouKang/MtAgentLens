// SPDX-License-Identifier: Apache-2.0 OR MIT
// Route sub-modules for the server API.
//
// Each module handles a single URL subtree:
//   runs.rs       — /api/runs, /api/runs/active, /api/runs/{id}
//   alerts.rs     — /api/runs/{id}/alerts
//   graph.rs      — /api/runs/{id}/graph
//   evidence.rs   — /api/runs/{id}/evidence
//   evaluation.rs — /api/runs/{id}/evaluation
//   diff.rs       — /api/runs/{id}/diff
//   health.rs     — /health, /live, /ready

pub mod alerts;
pub mod diff;
pub mod evaluation;
pub mod evidence;
pub mod graph;
pub mod handoff;
pub mod health;
pub mod runs;

// Re-export the main routing functions.
pub use runs::route_run_request;
