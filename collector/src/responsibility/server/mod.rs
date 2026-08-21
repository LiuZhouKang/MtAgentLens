// SPDX-License-Identifier: MIT
// Responsibility architecture server module — HTTP API + SSE.
//
// ## Structure
//   api.rs      — ApiResponse<T>, ApiError uniform JSON format
//   cors.rs     — CORS headers + OPTIONS preflight
//   sse.rs      — GET /api/events SSE real-time stream
//   routes/     — REST endpoint handlers
//
// ## Endpoints
//   GET  /health                   — full health check (DB reachable)
//   GET  /live                     — liveness probe (always 200)
//   GET  /ready                    — readiness probe (DB must be alive)
//   GET  /api/events               — SSE real-time alert stream
//   GET  /api/runs                 — list all runs
//   GET  /api/runs/active          — active run (404 sentinel: no_active_run)
//   GET  /api/runs/{id}            — single run summary
//   GET  /api/runs/{id}/alerts     — responsibility alerts
//   GET  /api/runs/{id}/evidence   — correlation evidence + intents + actions
//   GET  /api/runs/{id}/graph      — full ResponsibilityGraph (nodes+edges)
//   GET  /api/runs/{id}/evaluation — aggregated evaluation metrics
//   GET  /api/runs/{id}/diff       — cross-run operation-level diff
//
// ## Architecture
//   The server wraps a composed request handler that owns:
//     - PersistenceReader  (Arc<>) — for querying historical data
//     - broadcast::Sender<ResponsibilityAlert> — for SSE subscription
//
//   Dependencies: types, persistence, replay (per architecture DAG).

pub mod api;
pub mod cors;
pub mod handoff_store;
pub mod routes;
pub mod sse;

use crate::responsibility::persistence::reader::PersistenceReader;
use crate::responsibility::server::handoff_store::SharedHandoffStore;
use crate::responsibility::types::alert_types::ResponsibilityAlert;
use http_body_util::{BodyExt, Full, combinators::BoxBody};
use hyper::{Request, Response, body::Bytes};
use std::sync::Arc;
use tokio::sync::broadcast;

// ═══════════════════════════════════════════════════════════════════════════
// Composed request handler
// ═══════════════════════════════════════════════════════════════════════════

/// Shared state accessible to every HTTP handler.
pub struct ServerState {
    /// Read-only persistence for queries.
    pub reader: Arc<PersistenceReader>,
    /// Broadcast sender for SSE — new handlers subscribe via `sender.subscribe()`.
    pub alert_tx: broadcast::Sender<ResponsibilityAlert>,
    /// Whether the DB connection is healthy (checked at startup and periodically).
    pub db_healthy: bool,
    /// Optional status string from the last DB health check.
    pub db_status: Option<String>,
    /// Shared in-memory handoff observability store.
    pub handoff_store: SharedHandoffStore,
    /// Broadcast sender for handoff SSE events (JSON payloads).
    pub handoff_event_tx: broadcast::Sender<serde_json::Value>,
}

/// Convert `Response<Bytes>` to `Response<BoxBody<Bytes, hyper::Error>>`.
fn to_boxed(resp: Response<Bytes>) -> Response<BoxBody<Bytes, hyper::Error>> {
    let (parts, body) = resp.into_parts();
    let full = Full::new(body).map_err(|never: std::convert::Infallible| match never {});
    let boxed = BodyExt::boxed(full);
    Response::from_parts(parts, boxed)
}

/// The composed service entry point — called on every incoming request.
///
/// Routes the request to the correct handler based on URI prefix:
///   /health, /live, /ready  → health routes
///   /api/events             → SSE stream (alerts + handoff events)
///   /api/handoff/*         → handoff observability routes
///   /api/runs/*             → run-scoped routes
pub async fn serve_request(
    req: Request<hyper::body::Incoming>,
    state: Arc<ServerState>,
) -> Result<Response<BoxBody<Bytes, hyper::Error>>, hyper::Error> {
    let uri_path = req.uri().path().to_string();
    let query = req.uri().query().map(|q| q.to_string());
    let method = req.method().clone();

    // Preflight: handle OPTIONS for any path.
    if cors::is_preflight(&req) {
        let resp = cors::handle_preflight(&req);
        return Ok(to_boxed(resp));
    }

    // Health probes (no DB dependency for /live).
    match uri_path.as_str() {
        "/health" => {
            let resp = routes::health::health(req, state.db_healthy, state.db_status.clone());
            return Ok(to_boxed(resp));
        }
        "/live" => {
            let resp = routes::health::live(req);
            return Ok(to_boxed(resp));
        }
        "/ready" => {
            let resp = routes::health::ready(req, state.db_healthy);
            return Ok(to_boxed(resp));
        }
        _ => {}
    }

    // SSE stream — long-lived streaming response.
    // Multiplexes both responsibility alerts and handoff events.
    if uri_path == "/api/events" {
        let alert_rx = state.alert_tx.subscribe();
        let handoff_rx = state.handoff_event_tx.subscribe();
        return Ok(handle_unified_sse(alert_rx, handoff_rx));
    }

    // All /api/handoff/* routes (REST).
    // NOTE: req.collect() consumes the request body, so this MUST be checked
    // before /api/runs/ which also requires the full request.
    if let Some(sub) = uri_path.strip_prefix("/api/handoff") {
        let body = req
            .collect()
            .await
            .map(|collected| collected.to_bytes())
            .unwrap_or_default();
        let resp = routes::handoff::route_handoff_request(
            &method,
            &body,
            state.handoff_store.clone(),
            sub,
            query.as_deref(),
        )
        .await;
        return Ok(to_boxed_bytes(resp));
    }

    // All /api/runs/* routes.
    if let Some(sub) = uri_path.strip_prefix("/api/runs") {
        let resp = routes::route_run_request(req, &state.reader, sub, query.as_deref());
        return Ok(to_boxed(resp));
    }

    // Fallback: 404 for unknown paths.
    let resp = api::ApiError::new("not_found", format!("No route matches {uri_path}"))
        .into_response(hyper::StatusCode::NOT_FOUND);
    Ok(to_boxed(resp))
}

// ═══════════════════════════════════════════════════════════════════════════
// SSE helper — convert an alert stream into an HTTP streaming response
// ═══════════════════════════════════════════════════════════════════════════

fn handle_sse(
    rx: broadcast::Receiver<ResponsibilityAlert>,
) -> Response<BoxBody<Bytes, hyper::Error>> {
    use futures::StreamExt;
    use http_body_util::StreamBody;
    use hyper::body::Frame;

    let event_stream = sse::alert_event_stream(rx);
    let body_stream = event_stream.map(|event| {
        let text = event.to_frame();
        let payload = format!("{text}\n");
        Ok::<Frame<Bytes>, hyper::Error>(Frame::data(Bytes::from(payload)))
    });

    let body = BodyExt::boxed(StreamBody::new(body_stream));

    let mut resp = Response::builder()
        .status(hyper::StatusCode::OK)
        .header(hyper::header::CONTENT_TYPE, "text/event-stream")
        .header("cache-control", "no-cache")
        .header("connection", "keep-alive")
        .header("x-accel-buffering", "no")
        .body(body)
        .unwrap();

    cors::with_cors_headers(&mut resp);
    resp
}

/// Unified SSE handler that merges responsibility alerts and handoff events
/// into a single SSE stream. This provides backward compatibility with the
/// existing `/api/events` endpoint while adding handoff event types.
///
/// Each event is formatted as:
///   event: message
///   data: <json>
///
/// Where the JSON envelope has `stream_type` to distinguish event sources:
///   {"stream_type":"alert", ...}          — ResponsibilityAlert
///   {"stream_type":"handoff_observability", ...} — Handoff observability event
///   {"stream_type":"delegation_tree", ...}        — Delegation tree update
fn handle_unified_sse(
    alert_rx: broadcast::Receiver<ResponsibilityAlert>,
    handoff_rx: broadcast::Receiver<serde_json::Value>,
) -> Response<BoxBody<Bytes, hyper::Error>> {
    use http_body_util::StreamBody;
    use hyper::body::Frame;

    use tokio_stream::wrappers::BroadcastStream;

    // Convert alert stream to handoff-style events (wrapped in stream_type envelope)
    let alert_stream = sse::alert_event_stream(alert_rx);

    // Convert handoff event stream to SseEvent items (sync filter_map)
    let handoff_stream = tokio_stream::StreamExt::filter_map(
        BroadcastStream::new(handoff_rx),
        |result| match result {
            Ok(value) => {
                let data = serde_json::to_string(&serde_json::json!({
                    "stream_type": "handoff_observability",
                    "data": value,
                }))
                .unwrap_or_else(|_| r#"{"stream_type":"handoff_observability","data":{}}"#.into());
                Some(sse::SseEvent::handoff(data))
            }
            Err(tokio_stream::wrappers::errors::BroadcastStreamRecvError::Lagged(n)) => {
                log::warn!("Handoff SSE channel lagged, dropped {n} events");
                let data = serde_json::to_string(&serde_json::json!({
                    "stream_type": "handoff_observability",
                    "data": {
                        "sub_type": "LAGGED",
                        "dropped": n,
                    }
                }))
                .unwrap_or_default();
                Some(sse::SseEvent::handoff(data))
            }
        },
    );

    // Merge both streams into one
    let merged = futures::stream::select(alert_stream, handoff_stream);

    let body_stream = futures::StreamExt::map(merged, |event| {
        let text = event.to_frame();
        let payload = format!("{text}\n");
        Ok::<Frame<Bytes>, hyper::Error>(Frame::data(Bytes::from(payload)))
    });

    let body = BodyExt::boxed(StreamBody::new(body_stream));

    let mut resp = Response::builder()
        .status(hyper::StatusCode::OK)
        .header(hyper::header::CONTENT_TYPE, "text/event-stream")
        .header("cache-control", "no-cache")
        .header("connection", "keep-alive")
        .header("x-accel-buffering", "no")
        .body(body)
        .unwrap();

    cors::with_cors_headers(&mut resp);
    resp
}

/// Convert a `Response<Bytes>` to `Response<BoxBody<Bytes, hyper::Error>>`.
fn to_boxed_bytes(resp: Response<Bytes>) -> Response<BoxBody<Bytes, hyper::Error>> {
    let (parts, body) = resp.into_parts();
    let full = Full::new(body).map_err(|never: std::convert::Infallible| match never {});
    let boxed = BodyExt::boxed(full);
    Response::from_parts(parts, boxed)
}

// ═══════════════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use hyper::Method;

    fn test_state() -> Arc<ServerState> {
        let (tx, _) = broadcast::channel(16);
        let (handoff_tx, _) = broadcast::channel(256);
        let conn =
            crate::responsibility::persistence::schema::open_responsibility_db(":memory:").unwrap();
        let reader = Arc::new(PersistenceReader::from_connection(conn));
        Arc::new(ServerState {
            reader,
            alert_tx: tx,
            db_healthy: true,
            db_status: Some("ok".into()),
            handoff_store: crate::responsibility::server::handoff_store::create_handoff_store(
                false,
            ),
            handoff_event_tx: handoff_tx,
        })
    }

    /// Build a test request with `Bytes` body (works with generic handlers).
    fn make_req(method: Method, path: &str) -> Request<hyper::body::Bytes> {
        Request::builder()
            .method(method)
            .uri(path)
            .body(hyper::body::Bytes::new())
            .unwrap()
    }

    #[test]
    fn test_cors_preflight() {
        let req = make_req(Method::OPTIONS, "/api/runs");
        let resp = cors::handle_preflight(&req);
        assert_eq!(resp.status(), hyper::StatusCode::OK);
        assert_eq!(
            resp.headers().get("access-control-allow-origin").unwrap(),
            "*"
        );
    }

    #[test]
    fn test_health_live() {
        let req = make_req(Method::GET, "/live");
        let resp = routes::health::live(req);
        assert_eq!(resp.status(), hyper::StatusCode::OK);
    }

    #[test]
    fn test_health_ready_ok() {
        let req = make_req(Method::GET, "/ready");
        let resp = routes::health::ready(req, true);
        assert_eq!(resp.status(), hyper::StatusCode::OK);
    }

    #[test]
    fn test_health_ready_unhealthy() {
        let req = make_req(Method::GET, "/ready");
        let resp = routes::health::ready(req, false);
        assert_eq!(resp.status(), hyper::StatusCode::SERVICE_UNAVAILABLE);
    }

    #[test]
    fn test_404_unknown_path() {
        let resp = api::ApiError::new("not_found", "No route matches /unknown")
            .into_response(hyper::StatusCode::NOT_FOUND);
        assert_eq!(resp.status(), hyper::StatusCode::NOT_FOUND);
    }

    #[test]
    fn test_no_active_run_sentinel() {
        let resp = api::ApiError::no_active_run().into_response(hyper::StatusCode::NOT_FOUND);
        assert_eq!(resp.status(), hyper::StatusCode::NOT_FOUND);
        let body = String::from_utf8(resp.body().to_vec()).unwrap();
        assert!(body.contains("no_active_run"));
    }

    #[test]
    fn test_list_runs_empty() {
        let state = test_state();
        let req = make_req(Method::GET, "/api/runs");
        let resp = routes::runs::list_runs(req, &state.reader);
        assert_eq!(resp.status(), hyper::StatusCode::OK);
        let body = String::from_utf8(resp.body().to_vec()).unwrap();
        assert!(body.contains("\"authority\":\"authoritative\""));
    }

    #[test]
    fn test_get_active_run_empty() {
        let state = test_state();
        let req = make_req(Method::GET, "/api/runs/active");
        let resp = routes::runs::get_active_run(req, &state.reader);
        assert_eq!(resp.status(), hyper::StatusCode::NOT_FOUND);
        let body = String::from_utf8(resp.body().to_vec()).unwrap();
        assert!(body.contains("no_active_run"));
    }

    #[test]
    fn test_get_run_not_found() {
        let state = test_state();
        let req = make_req(Method::GET, "/api/runs/nonexistent");
        let resp = routes::runs::get_run(req, &state.reader, "nonexistent");
        assert_eq!(resp.status(), hyper::StatusCode::NOT_FOUND);
    }

    #[test]
    fn test_get_alerts_empty() {
        let state = test_state();
        let req = make_req(Method::GET, "/api/runs/r1/alerts");
        let resp = routes::alerts::list_alerts(req, &state.reader, "r1");
        assert_eq!(resp.status(), hyper::StatusCode::OK);
    }

    #[test]
    fn test_get_graph_empty() {
        let state = test_state();
        let req = make_req(Method::GET, "/api/runs/r1/graph");
        let resp = routes::graph::get_graph(req, &state.reader, "r1");
        assert_eq!(resp.status(), hyper::StatusCode::OK);
        let body = String::from_utf8(resp.body().to_vec()).unwrap();
        assert!(body.contains("\"nodes\":[]"));
        assert!(body.contains("\"edges\":[]"));
    }

    #[test]
    fn test_get_evidence_empty() {
        let state = test_state();
        let req = make_req(Method::GET, "/api/runs/r1/evidence");
        let resp = routes::evidence::get_evidence(req, &state.reader, "r1");
        assert_eq!(resp.status(), hyper::StatusCode::OK);
        let body = String::from_utf8(resp.body().to_vec()).unwrap();
        assert!(body.contains("\"evidence\":[]"));
        assert!(body.contains("\"intents\":[]"));
        assert!(body.contains("\"actions\":[]"));
        assert!(body.contains("\"non_links\":[]"));
    }

    #[test]
    fn test_get_evaluation_empty() {
        let state = test_state();
        let req = make_req(Method::GET, "/api/runs/r1/evaluation");
        let resp = routes::evaluation::get_evaluation(req, &state.reader, "r1");
        assert_eq!(resp.status(), hyper::StatusCode::OK);
        let body = String::from_utf8(resp.body().to_vec()).unwrap();
        assert!(body.contains("\"alert_count\":0"));
    }

    #[test]
    fn test_diff_no_baseline() {
        // Use a fresh in-memory DB with a run inserted so get_run succeeds.
        // (test_state() uses a different connection, so we create one here.)
        // PersistenceReader wraps a Connection, so we need to go direct.
        // The reader's connection is behind a private field; use a fresh conn.
        let conn =
            crate::responsibility::persistence::schema::open_responsibility_db(":memory:").unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('r1', 100)",
            [],
        )
        .unwrap();
        let reader = Arc::new(PersistenceReader::from_connection(conn));
        let req = make_req(Method::GET, "/api/runs/r1/diff");
        let resp = routes::diff::get_diff(req, &reader, "r1", None);
        assert_eq!(resp.status(), hyper::StatusCode::BAD_REQUEST);
        let body = String::from_utf8(resp.body().to_vec()).unwrap();
        assert!(body.contains("no_baseline"));
    }
}
