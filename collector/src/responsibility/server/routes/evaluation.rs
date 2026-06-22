// SPDX-License-Identifier: MIT
// Route handler: GET /api/runs/{id}/evaluation — return aggregated evaluation
// metrics for a run.

use crate::responsibility::persistence::reader::PersistenceReader;
use crate::responsibility::server::api::{ApiError, ApiResponse};
use hyper::{Request, Response, body::Bytes};
use serde::Serialize;
use std::sync::Arc;

/// Enriched evaluation response with the run record and evaluation data.
#[derive(Debug, Clone, Serialize)]
pub struct EvaluationResponse {
    pub run_id: String,
    pub alert_count: usize,
    pub claim_count: usize,
    pub evidence_count: usize,
    pub node_count: usize,
    pub edge_count: usize,
    pub identity_count: usize,
    /// Per-domain refinement counts.
    pub refinements: RefinementCountsResponse,
    /// Whether the run has been finalized (Phase 2 complete).
    pub is_finalized: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct RefinementCountsResponse {
    pub identity: usize,
    pub evidence: usize,
    pub graph: usize,
    pub classification: usize,
    pub claim: usize,
}

/// GET /api/runs/{id}/evaluation
pub fn get_evaluation<B>(
    _req: Request<B>,
    reader: &Arc<PersistenceReader>,
    run_id: &str,
) -> Response<Bytes> {
    let eval = match reader.get_evaluation_data(run_id) {
        Ok(v) => v,
        Err(e) => {
            return ApiError::internal(format!("Failed to get evaluation data: {e}"))
                .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR);
        }
    };

    // Determine if finalized: a run is finalized if the run record's
    // ended_at_ns is set.
    let is_finalized = reader
        .get_run(run_id)
        .ok()
        .flatten()
        .map(|r| r.ended_at_ns.is_some())
        .unwrap_or(false);

    ApiResponse::authoritative(EvaluationResponse {
        run_id: eval.run_id,
        alert_count: eval.alert_count,
        claim_count: eval.claim_count,
        evidence_count: eval.evidence_count,
        node_count: eval.node_count,
        edge_count: eval.edge_count,
        identity_count: eval.identity_count,
        refinements: RefinementCountsResponse {
            identity: eval.refinements.identity,
            evidence: eval.refinements.evidence,
            graph: eval.refinements.graph,
            classification: eval.refinements.classification,
            claim: eval.refinements.claim,
        },
        is_finalized,
    })
    .into_response()
}
