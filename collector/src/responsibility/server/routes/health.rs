// SPDX-License-Identifier: Apache-2.0 OR MIT
// Health-check endpoints: GET /health, GET /live, GET /ready.

use hyper::{Request, Response, body::Bytes};
use serde::Serialize;

/// Response shape for health checks.
#[derive(Debug, Clone, Serialize)]
pub struct HealthResponse {
    pub healthy: bool,
    pub ready: bool,
    /// Optional DB status string ("ok" or an error message).
    pub db: Option<String>,
}

/// GET /health — full health check including DB connectivity.
pub fn health<B>(_req: Request<B>, db_healthy: bool, db_status: Option<String>) -> Response<Bytes> {
    let body = serde_json::to_vec(&HealthResponse {
        healthy: db_healthy,
        ready: db_healthy,
        db: db_status,
    })
    .unwrap_or_default();

    let mut resp = Response::builder()
        .status(if db_healthy {
            hyper::StatusCode::OK
        } else {
            hyper::StatusCode::SERVICE_UNAVAILABLE
        })
        .header(hyper::header::CONTENT_TYPE, "application/json")
        .body(hyper::body::Bytes::from(body))
        .unwrap();

    super::super::cors::with_cors_headers(&mut resp);
    resp
}

/// GET /live — liveness probe (always returns 200 when the server is running).
pub fn live<B>(_req: Request<B>) -> Response<Bytes> {
    let body = serde_json::to_vec(&serde_json::json!({"alive": true})).unwrap_or_default();
    let mut resp = Response::builder()
        .status(hyper::StatusCode::OK)
        .header(hyper::header::CONTENT_TYPE, "application/json")
        .body(hyper::body::Bytes::from(body))
        .unwrap();

    super::super::cors::with_cors_headers(&mut resp);
    resp
}

/// GET /ready — readiness probe (DB connection must be alive).
pub fn ready<B>(_req: Request<B>, db_healthy: bool) -> Response<Bytes> {
    let body = serde_json::to_vec(&serde_json::json!({"ready": db_healthy})).unwrap_or_default();
    let mut resp = Response::builder()
        .status(if db_healthy {
            hyper::StatusCode::OK
        } else {
            hyper::StatusCode::SERVICE_UNAVAILABLE
        })
        .header(hyper::header::CONTENT_TYPE, "application/json")
        .body(hyper::body::Bytes::from(body))
        .unwrap();

    super::super::cors::with_cors_headers(&mut resp);
    resp
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_live_always_200() {
        let req = Request::builder()
            .uri("/live")
            .body(hyper::body::Bytes::new())
            .unwrap();
        let resp = live(req);
        assert_eq!(resp.status(), hyper::StatusCode::OK);
    }

    #[test]
    fn test_ready_unhealthy() {
        let req = Request::builder()
            .uri("/ready")
            .body(hyper::body::Bytes::new())
            .unwrap();
        let resp = ready(req, false);
        assert_eq!(resp.status(), hyper::StatusCode::SERVICE_UNAVAILABLE);
    }
}
