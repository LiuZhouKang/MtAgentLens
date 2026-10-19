// SPDX-License-Identifier: Apache-2.0 OR MIT
// Route handler: GET /api/runs/{id}/alerts — list all responsibility alerts for a run.

use crate::responsibility::persistence::reader::PersistenceReader;
use crate::responsibility::server::api::{ApiError, ApiResponse};
use hyper::{Request, Response, body::Bytes};
use std::sync::Arc;

/// GET /api/runs/{id}/alerts
pub fn list_alerts<B>(
    _req: Request<B>,
    reader: &Arc<PersistenceReader>,
    run_id: &str,
) -> Response<Bytes> {
    match reader.get_alerts(run_id) {
        Ok(alerts) => ApiResponse::authoritative(alerts).into_response(),
        Err(e) => ApiError::internal(format!("Failed to get alerts: {e}"))
            .into_response(hyper::StatusCode::INTERNAL_SERVER_ERROR),
    }
}
