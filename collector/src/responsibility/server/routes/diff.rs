// SPDX-License-Identifier: MIT
// Route handler: GET /api/runs/{id}/diff?against={other_run_id} — compute an
// operation-level diff between two runs.

use crate::responsibility::persistence::reader::PersistenceReader;
use crate::responsibility::replay::run_diff::{RunDiff, diff_runs};
use crate::responsibility::server::api::{ApiError, ApiResponse};
use hyper::{Request, Response, body::Bytes};
use serde::Serialize;
use std::collections::HashMap;
use std::sync::Arc;

/// Enriched diff response including the two run records being compared.
#[derive(Debug, Clone, Serialize)]
pub struct DiffResponse {
    pub before: crate::responsibility::types::replay_types::RunRecord,
    pub after: crate::responsibility::types::replay_types::RunRecord,
    pub diff: RunDiff,
}

/// GET /api/runs/{id}/diff?against={other_run_id}
///
/// If `against` is omitted, the most recent preceding run is used as the
/// baseline. If there is no preceding run, returns a 400.
pub fn get_diff<B>(
    _req: Request<B>,
    reader: &Arc<PersistenceReader>,
    run_id: &str,
    query: Option<&str>,
) -> Response<Bytes> {
    // Parse query for ?against=<other_run_id>
    let params = query
        .map(|q| {
            q.split('&')
                .filter_map(|p| p.split_once('='))
                .collect::<HashMap<&str, &str>>()
        })
        .unwrap_or_default();

    // Resolve the "after" run (the requested one).
    let after_run = match reader.get_run(run_id) {
        Ok(Some(r)) => r,
        Ok(None) => {
            return ApiError::not_found("run").into_response(hyper::StatusCode::NOT_FOUND);
        }
        Err(e) => {
            return ApiError::internal(format!("Failed to get run: {e}"))
                .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR);
        }
    };

    // Resolve the "before" run.
    let before_id = params.get("against").copied();
    let before_run = match before_id {
        Some(before_id) => match reader.get_run(before_id) {
            Ok(Some(r)) => r,
            Ok(None) => {
                return ApiError::new(
                    "before_run_not_found",
                    format!("Baseline run '{before_id}' not found"),
                )
                .into_response(hyper::StatusCode::NOT_FOUND);
            }
            Err(e) => {
                return ApiError::internal(format!("Failed to get baseline run: {e}"))
                    .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR);
            }
        },
        None => {
            // Auto-select: find the most recent run that ended before this
            // run started.
            let runs = match reader.list_runs() {
                Ok(runs) => runs,
                Err(e) => {
                    return ApiError::internal(format!("Failed to list runs: {e}"))
                        .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR);
                }
            };
            let candidate = runs
                .iter()
                .filter(|r| {
                    r.run_id != run_id
                        && r.ended_at_ns.is_some()
                        && r.ended_at_ns.unwrap() < after_run.started_at_ns
                })
                .max_by_key(|r| r.ended_at_ns);

            match candidate {
                Some(r) => r.clone(),
                None => {
                    return ApiError::new(
                        "no_baseline",
                        "No preceding run available. Use ?against=<run_id> to specify a baseline.",
                    )
                    .into_response(hyper::StatusCode::BAD_REQUEST);
                }
            }
        }
    };

    // Fetch evidence for both runs.
    let after_evidence = match reader.get_evidence(&after_run.run_id) {
        Ok(v) => v,
        Err(e) => {
            return ApiError::internal(format!("Failed to get evidence for after run: {e}"))
                .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR);
        }
    };
    let before_evidence = match reader.get_evidence(&before_run.run_id) {
        Ok(v) => v,
        Err(e) => {
            return ApiError::internal(format!("Failed to get evidence for before run: {e}"))
                .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR);
        }
    };

    // Convert to serde_json::Value slices for the diff function.
    let before_json: Vec<serde_json::Value> = before_evidence
        .iter()
        .map(|e| serde_json::to_value(e).unwrap_or_default())
        .collect();
    let after_json: Vec<serde_json::Value> = after_evidence
        .iter()
        .map(|e| serde_json::to_value(e).unwrap_or_default())
        .collect();

    let run_diff = diff_runs(&before_json, &after_json);

    ApiResponse::authoritative(DiffResponse {
        before: before_run,
        after: after_run,
        diff: run_diff,
    })
    .into_response()
}
