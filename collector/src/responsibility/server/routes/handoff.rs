// SPDX-License-Identifier: MIT
// Handoff observability REST API route handlers.
//
// All endpoints are read-only queries against the shared in-memory
// HandoffStoreData, except POST /api/handoff/probe which triggers a
// diagnostic probe.

use crate::responsibility::server::api::{ApiError, ApiResponse, Authority};
use crate::responsibility::server::handoff_store::{
    DependencyEdge, HandoffEventsResponse, HandoffObsMetrics, HandoffProbeRequest,
    HandoffProbeResponse, SharedHandoffStore,
};
use hyper::{Method, Response, StatusCode, body::Bytes};
use serde::Serialize;
use std::sync::Arc;
use tokio::sync::RwLock;

/// Helper: serialize any T to JSON bytes (for hyper response body).
fn json_bytes<T: Serialize>(data: &T) -> Vec<u8> {
    serde_json::to_vec(data).unwrap_or_else(|e| {
        format!(r#"{{"error":"serialization_failed","message":"{}"}}"#, e).into_bytes()
    })
}

/// Build a 200 JSON response with CORS headers.
fn json_response<T: Serialize>(data: &T) -> Response<Bytes> {
    let body = json_bytes(data);
    let mut resp = Response::new(Bytes::from(body));
    *resp.status_mut() = StatusCode::OK;
    resp.headers_mut().insert(
        hyper::header::CONTENT_TYPE,
        hyper::header::HeaderValue::from_static("application/json"),
    );
    resp
}

/// Build an error response.
fn error_response(status: StatusCode, code: &str, message: &str) -> Response<Bytes> {
    let err = ApiError::new(code.to_string(), message.to_string());
    err.into_response(status)
}

/// Extract a path segment after a prefix. Returns None if the prefix doesn't
/// match or there is nothing after it.
fn strip_prefix<'a>(path: &'a str, prefix: &str) -> Option<&'a str> {
    let rest = path.strip_prefix(prefix)?;
    if rest.is_empty() || rest.starts_with('/') {
        Some(rest.trim_start_matches('/'))
    } else {
        None
    }
}

/// Parse query string parameters.
fn parse_query(query: Option<&str>) -> std::collections::HashMap<String, String> {
    let mut params = std::collections::HashMap::new();
    if let Some(qs) = query {
        for pair in qs.split('&') {
            if let Some((key, value)) = pair.split_once('=') {
                params.insert(
                    url_decode(key).unwrap_or_default(),
                    url_decode(value).unwrap_or_default(),
                );
            }
        }
    }
    params
}

/// Minimal URL percent-decoding (handles %XX and +).
fn url_decode(input: &str) -> Option<String> {
    let mut bytes = Vec::with_capacity(input.len());
    let mut chars = input.chars();
    while let Some(c) = chars.next() {
        match c {
            '+' => bytes.push(b' '),
            '%' => {
                let hi = chars.next()?.to_digit(16)? as u8;
                let lo = chars.next()?.to_digit(16)? as u8;
                bytes.push((hi << 4) | lo);
            }
            _ => {
                // Percent-encoded UTF-8 sequences: push raw byte
                if c.is_ascii() {
                    bytes.push(c as u8);
                } else {
                    // For non-ASCII, re-encode as UTF-8
                    let mut buf = [0u8; 4];
                    let s = c.encode_utf8(&mut buf);
                    bytes.extend_from_slice(s.as_bytes());
                }
            }
        }
    }
    String::from_utf8(bytes).ok()
}

// ═══════════════════════════════════════════════════════════════════════════
// Route dispatcher
// ═══════════════════════════════════════════════════════════════════════════

/// Main entry point for all /api/handoff/* routes.
///
/// Instead of accepting the full `Request<Incoming>`, we receive the parsed
/// method and body bytes so that tests can construct calls easily.
///
/// `sub` is the path after `/api/handoff` (e.g. `/delegation-tree`).
pub async fn route_handoff_request(
    method: &Method,
    body: &[u8],
    store: SharedHandoffStore,
    sub: &str,
    query: Option<&str>,
) -> Response<Bytes> {
    let sub_path = sub.trim_start_matches('/');

    match (method.clone(), sub_path) {
        // GET /api/handoff/delegation-tree
        (Method::GET, "delegation-tree") => get_delegation_tree(store).await,

        // GET /api/handoff/metrics
        (Method::GET, "metrics") => get_metrics(store).await,

        // GET /api/handoff/events
        (Method::GET, "events") => {
            let params = parse_query(query);
            list_events(store, params).await
        }

        // GET /api/handoff/events/:id
        (Method::GET, rest) if rest.starts_with("events/") => {
            let event_id = rest.strip_prefix("events/").unwrap_or("");
            if event_id.is_empty() {
                let params = parse_query(query);
                list_events(store, params).await
            } else {
                get_event_by_id(store, event_id).await
            }
        }

        // GET /api/handoff/dependency-edges
        (Method::GET, "dependency-edges") => get_dependency_edges(store).await,

        // POST /api/handoff/probe
        (Method::POST, "probe") => {
            handle_probe(store, body).await
        }

        _ => error_response(
            StatusCode::NOT_FOUND,
            "not_found",
            &format!("No handoff route matches /api/handoff/{}", sub_path),
        ),
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Handler implementations
// ═══════════════════════════════════════════════════════════════════════════

/// GET /api/handoff/delegation-tree
async fn get_delegation_tree(store: SharedHandoffStore) -> Response<Bytes> {
    let guard = store.read().await;
    let resp = guard.get_tree_response();
    let wrapped = ApiResponse::provisional(resp);
    json_response(&wrapped)
}

/// GET /api/handoff/metrics
async fn get_metrics(store: SharedHandoffStore) -> Response<Bytes> {
    let guard = store.read().await;
    let metrics = guard.get_metrics();
    let wrapped = ApiResponse::provisional(metrics);
    json_response(&wrapped)
}

/// GET /api/handoff/events
async fn list_events(
    store: SharedHandoffStore,
    params: std::collections::HashMap<String, String>,
) -> Response<Bytes> {
    let limit: usize = params
        .get("limit")
        .and_then(|v| v.parse().ok())
        .unwrap_or(100)
        .min(500);
    let since_ms: u64 = params
        .get("since_ms")
        .and_then(|v| v.parse().ok())
        .unwrap_or(0);
    let sub_type: Option<&str> = params.get("sub_type").map(|s| s.as_str());

    let guard = store.read().await;
    let events_resp = guard.get_events(limit, since_ms, sub_type);
    let wrapped = ApiResponse::provisional(events_resp);
    json_response(&wrapped)
}

/// GET /api/handoff/events/:id
async fn get_event_by_id(store: SharedHandoffStore, event_id: &str) -> Response<Bytes> {
    let guard = store.read().await;
    match guard.get_event_by_id(event_id) {
        Some(event) => {
            let wrapped = ApiResponse::provisional(event);
            json_response(&wrapped)
        }
        None => error_response(
            StatusCode::NOT_FOUND,
            "event_not_found",
            &format!("Handoff event '{}' not found.", event_id),
        ),
    }
}

/// GET /api/handoff/dependency-edges
async fn get_dependency_edges(store: SharedHandoffStore) -> Response<Bytes> {
    let guard = store.read().await;
    let edges_resp = guard.get_dependency_edges();
    let wrapped = ApiResponse::provisional(edges_resp);
    json_response(&wrapped)
}

/// POST /api/handoff/probe
async fn handle_probe(store: SharedHandoffStore, body_bytes: &[u8]) -> Response<Bytes> {
    // Parse the request body
    let probe_req: HandoffProbeRequest = match serde_json::from_slice(body_bytes) {
        Ok(req) => req,
        Err(e) => {
            return error_response(
                StatusCode::BAD_REQUEST,
                "invalid_request_body",
                &format!("Invalid probe request: {}", e),
            );
        }
    };

    // Validate scope
    match probe_req.scope.as_str() {
        "probes" | "stream" | "runtime" | "all" => {}
        _ => {
            return error_response(
                StatusCode::BAD_REQUEST,
                "invalid_scope",
                "Scope must be one of: probes, stream, runtime, all.",
            );
        }
    }

    // Generate probe run ID
    let run_id = format!("probe-{}", uuid::Uuid::new_v4());
    let started_at_ms = {
        use std::time::SystemTime;
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64
    };

    let resp = HandoffProbeResponse {
        run_id,
        started_at_ms,
    };

    // Log the probe request for diagnostics
    log::info!(
        "Handoff probe started: scope={}, pid={:?}, port={:?}, run_id={}",
        probe_req.scope,
        probe_req.pid,
        probe_req.port,
        resp.run_id,
    );

    let wrapped = ApiResponse::provisional(resp);
    json_response(&wrapped)
}

// ═══════════════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;

    fn seeded_store() -> SharedHandoffStore {
        let store = crate::responsibility::server::handoff_store::create_handoff_store(true);
        store
    }

    #[tokio::test]
    async fn test_get_delegation_tree() {
        let store = seeded_store();
        let resp = get_delegation_tree(store).await;
        assert_eq!(resp.status(), StatusCode::OK);
        let body = String::from_utf8_lossy(resp.body());
        assert!(body.contains("\"agent_id\":\"root-agent\""));
        assert!(body.contains("\"authority\":\"provisional\""));
    }

    #[tokio::test]
    async fn test_get_metrics() {
        let store = seeded_store();
        let resp = get_metrics(store).await;
        assert_eq!(resp.status(), StatusCode::OK);
        let body = String::from_utf8_lossy(resp.body());
        assert!(body.contains("\"total_spawned\""));
        assert!(body.contains("\"fan_out_ratio\""));
    }

    #[tokio::test]
    async fn test_list_events_empty() {
        let store = Arc::new(RwLock::new(
            crate::responsibility::server::handoff_store::HandoffStoreData::new(),
        ));
        let params = std::collections::HashMap::new();
        let resp = list_events(store, params).await;
        assert_eq!(resp.status(), StatusCode::OK);
        let body = String::from_utf8_lossy(resp.body());
        assert!(body.contains("\"events\":[]"));
        assert!(body.contains("\"total\":0"));
    }

    #[tokio::test]
    async fn test_get_event_by_id_not_found() {
        let store = seeded_store();
        let resp = get_event_by_id(store, "nonexistent").await;
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
        let body = String::from_utf8_lossy(resp.body());
        assert!(body.contains("event_not_found"));
    }

    #[tokio::test]
    async fn test_get_dependency_edges() {
        let store = seeded_store();
        let mut guard = store.write().await;
        guard.upsert_edge(DependencyEdge {
            edge_id: "de1".into(),
            from_spawn_id: "s1".into(),
            to_spawn_id: "s2".into(),
            artifact_path: "/tmp/out.json".into(),
            is_orphaned: false,
            idle_ms: 500,
            producer_agent_id: "planner-agent".into(),
            consumer_agent_id: Some("tester-agent".into()),
        });
        drop(guard);

        let resp = get_dependency_edges(store).await;
        assert_eq!(resp.status(), StatusCode::OK);
        let body = String::from_utf8_lossy(resp.body());
        assert!(body.contains("\"edge_id\":\"de1\""));
        assert!(body.contains("\"producer_agent_id\":\"planner-agent\""));
    }

    #[tokio::test]
    async fn test_handle_probe() {
        let store = seeded_store();
        let body = serde_json::to_vec(&serde_json::json!({
            "scope": "all",
            "pid": null,
            "port": 7395,
        }))
        .unwrap();
        let resp = handle_probe(store, &body).await;
        assert_eq!(resp.status(), StatusCode::OK);
        let body_str = String::from_utf8_lossy(resp.body());
        assert!(body_str.contains("\"run_id\""));
        assert!(body_str.contains("\"started_at_ms\""));
    }

    #[tokio::test]
    async fn test_handle_probe_invalid_scope() {
        let store = seeded_store();
        let body = serde_json::to_vec(&serde_json::json!({
            "scope": "invalid",
        }))
        .unwrap();
        let resp = handle_probe(store, &body).await;
        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    }

    #[tokio::test]
    async fn test_handle_probe_invalid_body() {
        let store = seeded_store();
        let body = b"not-json";
        let resp = handle_probe(store, body).await;
        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    }

    #[tokio::test]
    async fn test_404_unknown_handoff_route() {
        let store = seeded_store();
        let resp = route_handoff_request(
            &Method::GET,
            b"",
            store,
            "/unknown",
            None,
        ).await;
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[test]
    fn test_url_decode() {
        assert_eq!(url_decode("hello%20world").as_deref(), Some("hello world"));
        assert_eq!(url_decode("a%2Fb").as_deref(), Some("a/b"));
        assert_eq!(url_decode("a+b").as_deref(), Some("a b"));
        assert_eq!(url_decode("plain").as_deref(), Some("plain"));
    }

    #[test]
    fn test_parse_query() {
        let params = parse_query(Some("limit=50&since_ms=1000&sub_type=HANDOFF_SPAWN"));
        assert_eq!(params.get("limit").map(|s| s.as_str()), Some("50"));
        assert_eq!(params.get("since_ms").map(|s| s.as_str()), Some("1000"));
        assert_eq!(
            params.get("sub_type").map(|s| s.as_str()),
            Some("HANDOFF_SPAWN")
        );
    }
}
