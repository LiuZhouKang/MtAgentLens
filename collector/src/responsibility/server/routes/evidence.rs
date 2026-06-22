// SPDX-License-Identifier: MIT
// Route handler: GET /api/runs/{id}/evidence — return all correlation evidence,
// intents, actions, and non-link diagnostics for a run.

use crate::responsibility::persistence::reader::PersistenceReader;
use crate::responsibility::server::api::{ApiError, ApiResponse};
use hyper::{Request, Response, body::Bytes};
use serde::Serialize;
use std::sync::Arc;

/// Full evidence payload for a single run.
#[derive(Debug, Clone, Serialize)]
pub struct EvidenceResponse {
    pub evidence: Vec<crate::responsibility::types::evidence_types::CorrelationEvidence>,
    pub intents: Vec<crate::responsibility::types::evidence_types::Intent>,
    pub actions: Vec<crate::responsibility::types::evidence_types::Action>,
    pub non_links: Vec<crate::responsibility::types::evidence_types::NonLinkDiagnostic>,
}

/// GET /api/runs/{id}/evidence
pub fn get_evidence<B>(
    _req: Request<B>,
    reader: &Arc<PersistenceReader>,
    run_id: &str,
) -> Response<Bytes> {
    // Run each query independently so partial failures can be reported clearly.
    let evidence = match reader.get_evidence(run_id) {
        Ok(v) => v,
        Err(e) => {
            return ApiError::internal(format!("Failed to get evidence: {e}"))
                .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR);
        }
    };
    let intents = match reader.get_intents(run_id) {
        Ok(v) => v,
        Err(e) => {
            return ApiError::internal(format!("Failed to get intents: {e}"))
                .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR);
        }
    };
    let actions = match reader.get_actions(run_id) {
        Ok(v) => v,
        Err(e) => {
            return ApiError::internal(format!("Failed to get actions: {e}"))
                .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR);
        }
    };
    let non_links = match reader.get_non_link_diagnostics(run_id) {
        Ok(v) => v,
        Err(e) => {
            return ApiError::internal(format!("Failed to get non-link diagnostics: {e}"))
                .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR);
        }
    };

    ApiResponse::authoritative(EvidenceResponse {
        evidence,
        intents,
        actions,
        non_links,
    })
    .into_response()
}
