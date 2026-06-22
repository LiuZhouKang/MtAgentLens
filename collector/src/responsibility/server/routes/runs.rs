// SPDX-License-Identifier: MIT
// Route handlers for run endpoints: GET /api/runs, /api/runs/active, /api/runs/{id}.

use crate::responsibility::persistence::reader::PersistenceReader;
use crate::responsibility::server::api::{ApiError, ApiResponse};
use hyper::{Request, Response, body::Bytes};
use std::sync::Arc;

/// GET /api/runs — list all runs in descending start-time order.
pub fn list_runs<B>(
    _req: Request<B>,
    reader: &Arc<PersistenceReader>,
) -> Response<Bytes> {
    match reader.list_runs() {
        Ok(runs) => ApiResponse::authoritative(runs).into_response(),
        Err(e) => ApiError::internal(format!("Failed to list runs: {e}")).into_response(
            hyper::StatusCode::INTERNAL_SERVER_ERROR,
        ),
    }
}

/// GET /api/runs/active — return the currently active run (ended_at_ns IS NULL).
///
/// Returns the sentinel `404 {code: "no_active_run"}` when no run is active.
pub fn get_active_run<B>(
    _req: Request<B>,
    reader: &Arc<PersistenceReader>,
) -> Response<Bytes> {
    match reader.list_runs() {
        Ok(runs) => {
            // The active run is the one with no ended_at_ns. If multiple, pick
            // the most recent by started_at_ns.
            let active = runs.iter().find(|r| r.ended_at_ns.is_none());
            match active {
                Some(run) => ApiResponse::provisional(run.clone()).into_response(),
                None => {
                    ApiError::no_active_run().into_response(hyper::StatusCode::NOT_FOUND)
                }
            }
        }
        Err(e) => ApiError::internal(format!("Failed to query active run: {e}")).into_response(
            hyper::StatusCode::INTERNAL_SERVER_ERROR,
        ),
    }
}

/// GET /api/runs/{id} — return a single run by ID.
pub fn get_run<B>(
    _req: Request<B>,
    reader: &Arc<PersistenceReader>,
    run_id: &str,
) -> Response<Bytes> {
    match reader.get_run(run_id) {
        Ok(Some(run)) => ApiResponse::authoritative(run).into_response(),
        Ok(None) => {
            ApiError::not_found("run").into_response(hyper::StatusCode::NOT_FOUND)
        }
        Err(e) => ApiError::internal(format!("Failed to get run: {e}")).into_response(
            hyper::StatusCode::INTERNAL_SERVER_ERROR,
        ),
    }
}

/// Helper: extract a path segment at position `idx` (0-indexed).
pub(crate) fn path_segment<'a>(path: &'a str, idx: usize) -> Option<&'a str> {
    path.split('/').filter(|s| !s.is_empty()).nth(idx)
}

/// Route a run-scoped API request to the correct handler based on path.
///
/// Path patterns:
/// - `/api/runs`                         -> list_runs
/// - `/api/runs/active`                  -> get_active_run
/// - `/api/runs/{id}`                    -> get_run
/// - `/api/runs/{id}/alerts`             -> alerts::list_alerts
/// - `/api/runs/{id}/graph`              -> graph::get_graph
/// - `/api/runs/{id}/evidence`           -> evidence::get_evidence
/// - `/api/runs/{id}/evaluation`         -> evaluation::get_evaluation
/// - `/api/runs/{id}/diff`               -> diff::get_diff
pub fn route_run_request<B>(
    req: Request<B>,
    reader: &Arc<PersistenceReader>,
    sub_path: &str,
    query: Option<&str>,
) -> Response<Bytes> {
    // sub_path is the part after "/api/runs" (e.g., "", "/active", "/{id}/alerts")
    let trimmed = sub_path.trim_start_matches('/');

    if trimmed.is_empty() {
        return list_runs(req, reader);
    }

    if trimmed == "active" {
        return get_active_run(req, reader);
    }

    // Split into segments: first is run_id, optional second is sub-resource
    let segments: Vec<&str> = trimmed.split('/').collect();
    let run_id = segments[0];

    match segments.get(1).copied() {
        None => get_run(req, reader, run_id),
        Some("alerts") => super::alerts::list_alerts(req, reader, run_id),
        Some("graph") => super::graph::get_graph(req, reader, run_id),
        Some("evidence") => super::evidence::get_evidence(req, reader, run_id),
        Some("evaluation") => super::evaluation::get_evaluation(req, reader, run_id),
        Some("diff") => super::diff::get_diff(req, reader, run_id, query),
        Some(other) => ApiError::not_found(&format!("resource {other}")).into_response(
            hyper::StatusCode::NOT_FOUND,
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_path_segment() {
        assert_eq!(path_segment("/api/runs", 0), Some("api"));
        assert_eq!(path_segment("/api/runs", 1), Some("runs"));
        assert_eq!(path_segment("/api/runs/active", 2), Some("active"));
        assert_eq!(path_segment("/api/runs/r1/alerts", 2), Some("r1"));
        assert_eq!(path_segment("/api/runs/r1/alerts", 3), Some("alerts"));
    }
}
