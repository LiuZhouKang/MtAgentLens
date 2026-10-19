// SPDX-License-Identifier: Apache-2.0 OR MIT
// Route handler: GET /api/runs/{id}/graph — return the full ResponsibilityGraph
// for a run (nodes + edges) as a single JSON payload.

use crate::responsibility::persistence::reader::PersistenceReader;
use crate::responsibility::server::api::{ApiError, ApiResponse};
use hyper::{Request, Response, body::Bytes};
use serde::Serialize;
use std::sync::Arc;

/// Combined graph response (nodes + edges in one object).
#[derive(Debug, Clone, Serialize)]
pub struct GraphResponse {
    pub nodes: Vec<crate::responsibility::types::graph_types::ResponsibilityNode>,
    pub edges: Vec<crate::responsibility::types::graph_types::ResponsibilityEdge>,
}

/// GET /api/runs/{id}/graph
pub fn get_graph<B>(
    _req: Request<B>,
    reader: &Arc<PersistenceReader>,
    run_id: &str,
) -> Response<Bytes> {
    match reader.get_graph(run_id) {
        Ok((nodes, edges)) => {
            ApiResponse::authoritative(GraphResponse { nodes, edges }).into_response()
        }
        Err(e) => ApiError::internal(format!("Failed to get graph: {e}"))
            .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR),
    }
}
