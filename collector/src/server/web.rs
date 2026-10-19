// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 eunomia-bpf org.

use crate::framework::analyzers::pipeline_contract::{PipelineStage, StageMetrics};
use crate::framework::core::Event;
use crate::responsibility::persistence::schema::open_db_read_only;
use crate::responsibility::types::alert_types::ResponsibilityAlert;
use crate::server::api_types::{
    HEALTH_SCHEMA_VERSION, HealthResponse, HealthStatus, StageMetricsSnapshot,
};
use crate::server::assets::FrontendAssets;
use http_body_util::combinators::BoxBody;
use http_body_util::{BodyExt, Full, StreamBody};
use hyper::body::{Body as _, Frame};
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper::{Method, Request, Response, StatusCode, body::Bytes};
use hyper_util::rt::TokioIo;
use serde_json::Value;
use std::collections::HashMap;
use std::convert::Infallible;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Instant;
use tokio::net::TcpListener;
use tokio::sync::broadcast;

/// Shared health state accessible to HTTP handlers.
///
/// Contains a snapshot of per-stage metrics and the pipeline start time.
/// Updated periodically by the pipeline orchestrator or through a background
/// aggregation task. Thread-safe via `Arc<Mutex<>>`.
pub struct HealthState {
    /// Per-stage metrics snapshot keyed by PipelineStage.
    pub stage_metrics: HashMap<PipelineStage, StageMetrics>,
    /// Timestamp when the pipeline (or collector) started.
    pub started_at: Instant,
    /// Whether the pipeline has processed at least one event.
    pub ready: bool,
}

impl HealthState {
    pub fn new(stage_metrics: HashMap<PipelineStage, StageMetrics>) -> Self {
        Self {
            stage_metrics,
            started_at: Instant::now(),
            ready: false,
        }
    }
}

/// 把 `Full<Bytes>` 响应包装为 `BoxBody<Bytes, hyper::Error>`。
///
/// 统一 service_fn 的响应体类型，使常规 REST（Full）与实时 SSE（流式）共存于同一连接。
// The library target never constructs this type; the `mtagentlens` binary does.
// Clippy analyses each target separately, so the lib half reports it as dead.
#[allow(dead_code)]
pub struct WebServer {
    assets: Arc<FrontendAssets>,
    event_sender: broadcast::Sender<Event>,
    log_file: Option<String>,
    /// 责任告警广播发送端（record/trace 实时流接入）；None 时 `/api/live/events`
    /// 只推原始事件。每个 SSE 连接独立 subscribe。
    alert_sender: Option<broadcast::Sender<ResponsibilityAlert>>,
    /// Shared health state for GET /health, /live, /ready probes.
    /// In Phase A/B, this is populated with stage metrics but the pipeline
    /// orchestrator integration comes in Phase C.
    health_state: Option<Arc<Mutex<HealthState>>>,
}

impl WebServer {
    pub fn new(
        event_sender: broadcast::Sender<Event>,
        log_file: Option<&str>,
    ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        Self::with_channels(event_sender, log_file, None, None)
    }

    /// 创建带责任告警通道的 WebServer（record/trace 实时流）。
    pub fn with_channels(
        event_sender: broadcast::Sender<Event>,
        log_file: Option<&str>,
        alert_sender: Option<broadcast::Sender<ResponsibilityAlert>>,
        health_state: Option<Arc<Mutex<HealthState>>>,
    ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let assets = FrontendAssets::new()?;
        Ok(Self {
            assets: Arc::new(assets),
            event_sender,
            log_file: log_file.map(|s| s.to_string()),
            alert_sender,
            health_state,
        })
    }

    /// Create a new WebServer with an optional shared health state.
    ///
    /// When `health_state` is provided, the server will serve `GET /health`,
    /// `GET /live`, and `GET /ready` endpoints backed by live pipeline metrics.
    /// When `None`, the health endpoints return baseline/empty data.
    pub fn with_health_state(
        event_sender: broadcast::Sender<Event>,
        log_file: Option<&str>,
        health_state: Arc<Mutex<HealthState>>,
    ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        Self::with_channels(event_sender, log_file, None, Some(health_state))
    }

    pub async fn start(
        &self,
        addr: SocketAddr,
    ) -> std::result::Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let listener = TcpListener::bind(addr)
            .await
            .map_err(|e| Box::new(e) as Box<dyn std::error::Error + Send + Sync>)?;
        log::info!("🚀 Frontend server running on http://{}", addr);

        // List embedded assets for debugging
        let all_assets = self.assets.list_all_assets();
        log::info!("📦 Embedded {} assets from frontend/out:", all_assets.len());
        for asset in all_assets.iter().take(10) {
            log::info!("   - {}", asset);
        }
        if all_assets.len() > 10 {
            log::info!("   ... and {} more", all_assets.len() - 10);
        }

        let health_state = self.health_state.clone();
        let alert_sender = self.alert_sender.clone();

        loop {
            let (stream, _) = listener
                .accept()
                .await
                .map_err(|e| Box::new(e) as Box<dyn std::error::Error + Send + Sync>)?;
            let assets = Arc::clone(&self.assets);
            let event_sender = self.event_sender.clone();
            let log_file = self.log_file.clone();
            let hs = health_state.clone();
            let alert_sender = alert_sender.clone();

            tokio::spawn(async move {
                let io = TokioIo::new(stream);
                let service = service_fn(move |req| {
                    // 实时 SSE 走独立的流式服务；其余请求保持原样（Full body 收口 boxed）。
                    // service_fn 是 FnMut，必须在闭包内 clone 捕获值，再移入 async 块。
                    let is_live_events = req.uri().path() == "/api/live/events";
                    let assets = assets.clone();
                    let event_sender = event_sender.clone();
                    let log_file = log_file.clone();
                    let hs = hs.clone();
                    let alert_sender = alert_sender.clone();
                    async move {
                        if is_live_events {
                            serve_live_events_api(event_sender, alert_sender).await
                        } else {
                            handle_request(req, assets, event_sender, log_file, hs)
                                .await
                                .map(boxed_full)
                        }
                    }
                });

                if let Err(err) = http1::Builder::new().serve_connection(io, service).await {
                    log::error!("❌ Error serving connection: {:?}", err);
                }
            });
        }
    }
}

fn boxed_full(resp: Response<Full<Bytes>>) -> Response<BoxBody<Bytes, hyper::Error>> {
    let (parts, body) = resp.into_parts();
    let boxed = BodyExt::boxed(body.map_err(|never: std::convert::Infallible| match never {}));
    Response::from_parts(parts, boxed)
}

/// GET /api/live/events — 统一实时 SSE：原始事件 + 可选责任告警 + 30s 心跳。
///
/// 直播（record/trace）中后端把 alert_tx（责任告警，含图快照）与 event_sender
/// （原始框架事件）实时推送前端。legacy `/api/events` 保持日志回放语义不动。
async fn serve_live_events_api(
    event_sender: broadcast::Sender<Event>,
    alert_sender: Option<broadcast::Sender<ResponsibilityAlert>>,
) -> std::result::Result<Response<BoxBody<Bytes, hyper::Error>>, Infallible> {
    use tokio_stream::StreamExt as _;

    let stream = crate::server::live_sse::live_event_stream(
        event_sender.subscribe(),
        alert_sender.map(|s| s.subscribe()),
    )
    .map(|e| {
        let text = e.to_frame();
        let payload = format!("{text}\n");
        Ok::<Frame<Bytes>, hyper::Error>(Frame::data(Bytes::from(payload)))
    });

    let body = BodyExt::boxed(StreamBody::new(stream));

    let resp = Response::builder()
        .status(hyper::StatusCode::OK)
        .header(hyper::header::CONTENT_TYPE, "text/event-stream")
        .header("cache-control", "no-cache")
        .header("x-accel-buffering", "no")
        .body(body)
        .unwrap();
    // 安全：实时流含解密明文，绝不加 `Access-Control-Allow-Origin: *`——
    // 前端同源嵌入访问无需 CORS；`*` 会让任意恶意网页经 CORS 读取明文（审查 HIGH-2）。
    Ok(resp)
}

async fn handle_request(
    req: Request<hyper::body::Incoming>,
    assets: Arc<FrontendAssets>,
    event_sender: broadcast::Sender<Event>,
    log_file: Option<String>,
    health_state: Option<Arc<Mutex<HealthState>>>,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let path = req.uri().path();

    // Health probe endpoints — fastest path, no DB/asset I/O
    if req.method() == Method::GET {
        match path {
            "/health" | "/api/health" => {
                return Ok(serve_health(health_state.as_ref()));
            }
            "/live" | "/api/live" => {
                return Ok(serve_live());
            }
            "/ready" | "/api/ready" => {
                return Ok(serve_ready(health_state.as_ref()));
            }
            _ => {}
        }
    }

    log::info!("📨 {} {}", req.method(), path);

    if req.method() == Method::GET {
        if path == "/api/runs/active" {
            let response = match resolve_active_run_dir() {
                Ok(Some((run_id, run_dir))) => serve_run_summary_api(&run_dir, &run_id).await,
                // 无活跃 run 返回 200 {data:null}（而非 404）——前端 fetchJson 解析为
                // null，LiveSession 据此判定「run 结束」转回放。404 会被 fetchJson 抛错，
                // 导致生命周期死路径（审查 HIGH-1）。
                Ok(None) => Ok(json_response(serde_json::json!({ "data": null }))),
                Err(err) => Ok(internal_error_json(format!(
                    "failed to resolve active run: {err}"
                ))),
            };
            return response;
        }

        if let Some(run_route) = match_run_route(path) {
            let response = match resolve_run_dir(run_route.run_id) {
                Ok(run_dir) => match run_route.resource {
                    RunResource::Summary => serve_run_summary_api(&run_dir, run_route.run_id).await,
                    RunResource::Evaluation => serve_run_evaluation_api(&run_dir).await,
                    RunResource::Alerts => serve_alerts_api(&run_dir).await,
                    RunResource::Events => serve_run_events_api(&run_dir).await,
                    RunResource::Snapshots => serve_run_snapshots_api(&run_dir).await,
                    RunResource::Db => serve_run_db_api(&run_dir).await,
                },
                Err(_) => Ok(not_found_json(json_error(
                    "run_not_found",
                    run_route.run_id,
                    "run",
                ))),
            };
            return response;
        }
    }

    match (req.method(), path) {
        // API endpoints first
        (&Method::GET, "/api/events") => serve_events_api(event_sender, log_file).await,
        (&Method::GET, "/api/runs") => serve_runs_index_api().await,
        (&Method::GET, "/api/scenarios") => serve_scenarios_index_api().await,
        (&Method::GET, "/api/assets") => serve_assets_list(assets).await,

        // Serve static assets (catch-all for GET requests)
        (&Method::GET, _) => serve_asset(assets, path).await,

        // 404 for non-GET methods
        _ => {
            log::info!("❌ 404 Not Found: {} {}", req.method(), path);
            Ok(Response::builder()
                .status(StatusCode::NOT_FOUND)
                .header("Content-Type", "text/plain")
                .body(Full::new(Bytes::from("Not Found")))
                .unwrap())
        }
    }
}

async fn serve_asset(
    assets: Arc<FrontendAssets>,
    path: &str,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    if let Some(content) = assets.get(path) {
        let content_type = assets.get_content_type(path);
        log::info!("✅ Serving asset: {} ({})", path, content_type);
        Ok(Response::builder()
            .header("Content-Type", content_type)
            .header("Cache-Control", "public, max-age=31536000")
            .body(Full::new(Bytes::from(content.to_vec())))
            .unwrap())
    } else {
        log::info!("❌ Asset not found: {}", path);
        Ok(Response::builder()
            .status(StatusCode::NOT_FOUND)
            .header("Content-Type", "text/plain")
            .body(Full::new(Bytes::from("Asset not found")))
            .unwrap())
    }
}

// Legacy `/api/events` response. Deliberately no CORS: the payload is raw
// captured traffic, and `Access-Control-Allow-Origin: *` would let any web page
// the operator visits read it from loopback. The embedded frontend is
// same-origin and needs no CORS header.
async fn serve_events_api(
    _event_sender: broadcast::Sender<Event>,
    log_file: Option<String>,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let legacy_warning = "299 MtAgentLens \"/api/events is a legacy, non-authoritative compatibility endpoint; prefer run-bound artifacts when available\"";

    // If log file is specified, read and return its contents
    if let Some(log_path) = log_file {
        match tokio::fs::read_to_string(&log_path).await {
            Ok(content) => {
                log::info!(
                    "📊 Serving legacy log-backed /api/events response: {} ({} bytes)",
                    log_path,
                    content.len()
                );
                Ok(Response::builder()
                    .header("Content-Type", "text/plain")
                    .header("X-MtAgentLens-Legacy-Api", "true")
                    .header("X-MtAgentLens-Authority", "non-authoritative")
                    .header("Warning", legacy_warning)
                    .body(Full::new(Bytes::from(content)))
                    .unwrap())
            }
            Err(e) => {
                log::error!(
                    "❌ Failed to read legacy /api/events log file {}: {}",
                    log_path,
                    e
                );
                Ok(Response::builder()
                    .status(StatusCode::INTERNAL_SERVER_ERROR)
                    .header("Content-Type", "text/plain")
                    .header("X-MtAgentLens-Legacy-Api", "true")
                    .header("X-MtAgentLens-Authority", "non-authoritative")
                    .header("Warning", legacy_warning)
                    .body(Full::new(Bytes::from(format!(
                        "Failed to read log file: {}",
                        e
                    ))))
                    .unwrap())
            }
        }
    } else {
        log::info!(
            "📊 Legacy /api/events requested without log file; returning unavailable instead of sample data"
        );
        let body = serde_json::json!({
            "code": "artifact_unavailable",
            "artifact": "legacy_events_log",
            "message": "/api/events is a legacy compatibility endpoint and no log file is configured",
            "authority": "non-authoritative"
        });
        Ok(Response::builder()
            .status(StatusCode::NOT_FOUND)
            .header("Content-Type", "application/json")
            .header("X-MtAgentLens-Legacy-Api", "true")
            .header("X-MtAgentLens-Authority", "non-authoritative")
            .header("Warning", legacy_warning)
            .body(Full::new(Bytes::from(body.to_string())))
            .unwrap())
    }
}

async fn serve_runs_index_api() -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let runs_dir = match std::env::current_dir() {
        Ok(dir) => dir.join("env").join("runs"),
        Err(err) => {
            return Ok(internal_error_json(format!(
                "failed to resolve current directory: {err}"
            )));
        }
    };

    let mut entries = Vec::new();
    let read_dir = match std::fs::read_dir(&runs_dir) {
        Ok(read_dir) => read_dir,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            return Ok(json_response(Value::Array(entries)));
        }
        Err(err) => {
            return Ok(internal_error_json(format!(
                "failed to read runs directory: {err}"
            )));
        }
    };

    for entry in read_dir.filter_map(Result::ok) {
        let run_dir = entry.path();
        if !run_dir.is_dir() {
            continue;
        }
        let Some(run_id) = run_dir.file_name().and_then(|name| name.to_str()) else {
            continue;
        };

        let summary_path = run_dir.join("summary.json");
        let summary_bytes = match std::fs::read(&summary_path) {
            Ok(bytes) => bytes,
            Err(_) => continue,
        };
        let summary: Value = match serde_json::from_slice(&summary_bytes) {
            Ok(value) => value,
            Err(_) => continue,
        };

        entries.push(run_summary_value(&run_dir, run_id, &summary));
    }

    entries.sort_by(|left, right| {
        left.get("run_id")
            .and_then(Value::as_str)
            .cmp(&right.get("run_id").and_then(Value::as_str))
    });

    Ok(json_response(Value::Array(entries)))
}

async fn serve_scenarios_index_api() -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let scenarios_dir = match std::env::current_dir() {
        Ok(dir) => dir.join("env").join("scenarios"),
        Err(err) => {
            return Ok(internal_error_json(format!(
                "failed to resolve current directory: {err}"
            )));
        }
    };

    let mut entries = Vec::new();
    let read_dir = match std::fs::read_dir(&scenarios_dir) {
        Ok(read_dir) => read_dir,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            return Ok(json_response(Value::Array(entries)));
        }
        Err(err) => {
            return Ok(internal_error_json(format!(
                "failed to read scenarios directory: {err}"
            )));
        }
    };

    for entry in read_dir.filter_map(Result::ok) {
        let path = entry.path();
        if !path.is_file() || path.extension().is_none_or(|ext| ext != "json") {
            continue;
        }
        let bytes = match std::fs::read(&path) {
            Ok(bytes) => bytes,
            Err(_) => continue,
        };
        let metadata: Value = match serde_json::from_slice(&bytes) {
            Ok(value) => value,
            Err(_) => continue,
        };
        let Some(scenario_id) = metadata.get("id").and_then(Value::as_str) else {
            continue;
        };
        let label = metadata
            .get("description")
            .and_then(Value::as_str)
            .unwrap_or(scenario_id);
        entries.push(serde_json::json!({
            "scenario_id": scenario_id,
            "label": label
        }));
    }

    entries.sort_by(|left, right| {
        left.get("scenario_id")
            .and_then(Value::as_str)
            .cmp(&right.get("scenario_id").and_then(Value::as_str))
    });

    Ok(json_response(Value::Array(entries)))
}

async fn serve_assets_list(
    assets: Arc<FrontendAssets>,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let all_assets = assets.list_all_assets();
    let response = serde_json::json!({
        "assets": all_assets,
        "total_count": all_assets.len()
    });

    log::info!("📋 Serving assets list ({} assets)", all_assets.len());
    Ok(Response::builder()
        .header("Content-Type", "application/json")
        .body(Full::new(Bytes::from(response.to_string())))
        .unwrap())
}

#[derive(Clone, Copy)]
enum RunResource {
    Summary,
    Evaluation,
    Alerts,
    Events,
    Snapshots,
    /// Raw SQLite artifact (collector.responsibility.db) — the frontend loads
    /// it directly with sql.js, so the backend just streams the file.
    Db,
}

struct RunRoute<'a> {
    run_id: &'a str,
    resource: RunResource,
}

fn match_run_route(path: &str) -> Option<RunRoute<'_>> {
    let trimmed = path.trim_matches('/');
    let segments: Vec<&str> = trimmed.split('/').collect();
    match segments.as_slice() {
        ["api", "runs", run_id] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::Summary,
        }),
        ["api", "runs", run_id, "evaluation"] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::Evaluation,
        }),
        ["api", "runs", run_id, "alerts"] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::Alerts,
        }),
        ["api", "runs", run_id, "events"] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::Events,
        }),
        ["api", "runs", run_id, "db"] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::Db,
        }),
        ["api", "runs", run_id, "snapshots"] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::Snapshots,
        }),
        _ => None,
    }
}

fn resolve_run_dir(run_id: &str) -> std::io::Result<PathBuf> {
    let run_dir = std::env::current_dir()?
        .join("env")
        .join("runs")
        .join(run_id);
    if run_dir.is_dir() {
        Ok(run_dir)
    } else {
        Err(std::io::Error::new(
            std::io::ErrorKind::NotFound,
            format!("run directory not found for {run_id}"),
        ))
    }
}

fn resolve_active_run_dir() -> std::io::Result<Option<(String, PathBuf)>> {
    let runs_dir = std::env::current_dir()?.join("env").join("runs");
    let read_dir = match std::fs::read_dir(&runs_dir) {
        Ok(read_dir) => read_dir,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(err) => return Err(err),
    };

    let mut candidate: Option<(String, PathBuf)> = None;
    for entry in read_dir.filter_map(Result::ok) {
        let run_dir = entry.path();
        if !run_dir.is_dir() {
            continue;
        }
        let Some(run_id) = run_dir
            .file_name()
            .and_then(|name| name.to_str())
            .map(str::to_string)
        else {
            continue;
        };

        let summary_path = run_dir.join("summary.json");
        let summary_bytes = match std::fs::read(&summary_path) {
            Ok(bytes) => bytes,
            Err(_) => continue,
        };
        if serde_json::from_slice::<Value>(&summary_bytes).is_err() {
            continue;
        }

        if candidate
            .as_ref()
            .is_none_or(|(best_run_id, _)| run_id > *best_run_id)
        {
            candidate = Some((run_id, run_dir));
        }
    }

    Ok(candidate)
}

async fn serve_run_summary_api(
    run_dir: &Path,
    run_id: &str,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let summary_path = run_dir.join("summary.json");
    let summary_bytes = match tokio::fs::read(&summary_path).await {
        Ok(bytes) => bytes,
        Err(_) => {
            return Ok(not_found_json(json_error(
                "artifact_unavailable",
                run_id,
                "summary",
            )));
        }
    };

    let summary: serde_json::Value = match serde_json::from_slice(&summary_bytes) {
        Ok(value) => value,
        Err(err) => {
            return Ok(internal_error_json(format!(
                "failed to parse summary.json: {err}"
            )));
        }
    };

    Ok(json_response(run_summary_value(run_dir, run_id, &summary)))
}

fn run_summary_value(run_dir: &Path, run_id: &str, summary: &Value) -> Value {
    let scenario_ids = summary
        .get("scenario_ids")
        .cloned()
        .unwrap_or_else(|| Value::Array(Vec::new()));
    let run_status = summary
        .get("status")
        .cloned()
        .or_else(|| summary.get("run_status").cloned())
        .unwrap_or_else(|| Value::String("unknown".to_string()));

    // Availability must describe what the endpoints can actually serve. The
    // causal artifacts are no longer produced by the collector, so they do not
    // count as availability even when a stale file is present.
    let alerts_db_path = resolve_db_for_alerts(run_dir);
    let responsibility_db_path = resolve_responsibility_db_path(run_dir);
    let table_is_queryable = |path: &Path, table: &str| {
        let path = path.to_string_lossy();
        crate::responsibility::persistence::schema::open_responsibility_db_read_only(&path)
            .ok()
            .and_then(|c| {
                c.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |r| {
                    r.get::<_, i64>(0)
                })
                .ok()
            })
            .is_some()
    };
    let live_alerts_available = alerts_db_path
        .as_ref()
        .is_some_and(|path| table_is_queryable(path, "responsibility_alerts"));
    let live_correlation_available = responsibility_db_path
        .as_ref()
        .is_some_and(|path| table_is_queryable(path, "correlation_evidence"));

    let payload = serde_json::json!({
        "run_id": run_id,
        "scenario_ids": scenario_ids,
        "run_status": run_status,
        "artifact_availability": {
            "evaluation": run_dir.join("evaluation.json").exists(),
            "alerts": live_alerts_available,
            "correlation_evidence": live_correlation_available,
            "events": resolve_events_artifact_path(run_dir).is_some(),
            "database": responsibility_db_path.is_some()
        }
    });

    payload
}

fn load_replay_report_warnings(run_dir: &Path) -> Vec<serde_json::Value> {
    let replay_report = run_dir.join("replay.report.json");
    let Ok(bytes) = std::fs::read(&replay_report) else {
        return Vec::new();
    };
    let Ok(value) = serde_json::from_slice::<serde_json::Value>(&bytes) else {
        return Vec::new();
    };
    let Some(warnings) = value
        .get("warnings")
        .and_then(|warnings| warnings.as_array())
    else {
        return Vec::new();
    };
    warnings
        .iter()
        .filter_map(|warning| {
            let code = warning.get("code")?.as_str()?;
            let severity = warning.get("severity")?.as_str()?;
            let message = warning.get("message")?.as_str()?;
            Some(serde_json::json!({
                "code": code,
                "severity": severity,
                "message": message
            }))
        })
        .collect()
}

async fn serve_run_evaluation_api(
    run_dir: &Path,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let run_id = run_dir
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("unknown-run");
    let evaluation_path = run_dir.join("evaluation.json");
    match tokio::fs::read(&evaluation_path).await {
        Ok(bytes) => {
            let mut parsed: serde_json::Value = match serde_json::from_slice(&bytes) {
                Ok(value) => value,
                Err(err) => {
                    return Ok(internal_error_json(format!(
                        "failed to parse evaluation.json: {err}"
                    )));
                }
            };
            if let Some(object) = parsed.as_object_mut() {
                object.insert(
                    "warnings".to_string(),
                    serde_json::Value::Array(load_replay_report_warnings(run_dir)),
                );
            }
            Ok(json_response(parsed))
        }
        Err(_) => Ok(not_found_json(json_error(
            "artifact_unavailable",
            run_id,
            "evaluation",
        ))),
    }
}

async fn serve_run_snapshots_api(
    run_dir: &Path,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let run_id = run_dir.file_name().and_then(|n| n.to_str()).unwrap_or("?");
    let Some(db_path) = resolve_responsibility_db_path(run_dir) else {
        return Ok(not_found_json(json_error(
            "artifact_unavailable",
            run_id,
            "responsibility_db",
        )));
    };
    let conn = match open_db_read_only(&db_path.to_string_lossy()) {
        Ok(conn) => conn,
        Err(err) => {
            return Ok(internal_error_json(format!(
                "snapshots_schema_error: {err}"
            )));
        }
    };

    // One run per database: all 301 archived collector databases carry exactly
    // one `runs` row, so "all snapshots in this file" is "this run's snapshots".
    let mut stmt = match conn.prepare(
        "SELECT snapshot_id, timestamp_ns, nodes_json, edges_json, metrics_json
         FROM graph_snapshots
         ORDER BY timestamp_ns ASC, snapshot_id ASC",
    ) {
        Ok(stmt) => stmt,
        Err(err) => {
            return Ok(internal_error_json(format!(
                "snapshots_schema_error: {err}"
            )));
        }
    };

    let rows = match stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, i64>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, String>(3)?,
            row.get::<_, String>(4)?,
        ))
    }) {
        Ok(rows) => rows,
        Err(err) => {
            return Ok(internal_error_json(format!(
                "snapshots_schema_error: {err}"
            )));
        }
    };

    let mut snapshots = Vec::new();
    for row in rows {
        let (snapshot_id, timestamp_ns, nodes_json, edges_json, metrics_json) = match row {
            Ok(row) => row,
            Err(err) => {
                return Ok(internal_error_json(format!(
                    "snapshots_schema_error: {err}"
                )));
            }
        };
        // Malformed JSON in our own artifact is corruption, not "no data".
        let parse = |raw: &str, what: &str| {
            serde_json::from_str::<serde_json::Value>(raw)
                .map_err(|err| format!("snapshots_schema_error: {snapshot_id} {what}: {err}"))
        };
        let nodes = match parse(&nodes_json, "nodes_json") {
            Ok(value) => value,
            Err(err) => return Ok(internal_error_json(err)),
        };
        let edges = match parse(&edges_json, "edges_json") {
            Ok(value) => value,
            Err(err) => return Ok(internal_error_json(err)),
        };
        let metrics = match parse(&metrics_json, "metrics_json") {
            Ok(value) => value,
            Err(err) => return Ok(internal_error_json(err)),
        };
        snapshots.push(serde_json::json!({
            "snapshot_id": snapshot_id,
            "timestamp_ns": timestamp_ns,
            "nodes": nodes,
            "edges": edges,
            "metrics": metrics,
        }));
    }

    // The frontend's GraphSnapshot contract is an array, not a wrapper object.
    Ok(json_response(serde_json::Value::Array(snapshots)))
}

async fn serve_run_events_api(
    run_dir: &Path,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let run_id = run_dir
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("unknown-run");
    let Some(events_path) = resolve_events_artifact_path(run_dir) else {
        return Ok(not_found_json(json_error(
            "artifact_unavailable",
            run_id,
            "events",
        )));
    };

    let content = match tokio::fs::read_to_string(&events_path).await {
        Ok(content) => content,
        Err(err) => {
            return Ok(internal_error_json(format!(
                "failed to read trace.log: {err}"
            )));
        }
    };

    let mut events = Vec::new();
    for (line_index, line) in content.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let mut parsed: Value = match serde_json::from_str(line) {
            Ok(value) => value,
            Err(_) => {
                return Ok(structured_error_json(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "invalid_events_artifact",
                    "failed to parse trace.log for replay".to_string(),
                    serde_json::json!({
                        "line": line_index + 1,
                        "artifact": events_path.file_name().and_then(|name| name.to_str()).unwrap_or("trace.log")
                    }),
                ));
            }
        };
        let Some(event) = parsed.as_object_mut() else {
            return Ok(structured_error_json(
                StatusCode::INTERNAL_SERVER_ERROR,
                "invalid_events_artifact",
                "failed to parse trace.log for replay".to_string(),
                serde_json::json!({
                    "line": line_index + 1,
                    "artifact": events_path.file_name().and_then(|name| name.to_str()).unwrap_or("trace.log")
                }),
            ));
        };
        event.insert(
            "id".to_string(),
            Value::String(format!("{run_id}:{}", line_index + 1)),
        );
        events.push(parsed);
    }

    Ok(json_response(Value::Array(events)))
}

async fn serve_alerts_api(
    run_dir: &Path,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let db_path = resolve_db_for_alerts(run_dir);
    let Some(db_path) = db_path else {
        return Ok(not_found_json(json_error(
            "db_unavailable",
            run_dir
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("unknown"),
            "database",
        )));
    };

    let reader = match crate::responsibility::persistence::PersistenceReader::open(
        &db_path.to_string_lossy(),
    ) {
        Ok(reader) => reader,
        Err(error) => {
            // A schema mismatch must be visible: returning 200 [] made a real
            // broken artifact look like a run with no alerts.
            return Ok(internal_error_json(format!("alerts_schema_error: {error}")));
        }
    };

    match reader.get_alerts_all() {
        Ok(alerts) => match serde_json::to_value(alerts) {
            Ok(value) => Ok(json_response(value)),
            Err(error) => Ok(internal_error_json(format!(
                "alerts_serialization_error: {error}"
            ))),
        },
        Err(error) => Ok(internal_error_json(format!("alerts_schema_error: {error}"))),
    }
}

/// Locate the run's authoritative SQLite artifact (`collector.responsibility.db`),
/// which holds the `graph_snapshots` / `responsibility_alerts` /
/// `correlation_evidence` tables the frontend's sql.js consumption layer reads
/// directly. The collector writes it at `<run>/collector/collector.responsibility.db`.
fn resolve_responsibility_db_path(run_dir: &Path) -> Option<PathBuf> {
    if let Some(path) = resolve_artifact_from_summary(run_dir, "responsibility_db") {
        return Some(path);
    }
    let nested = run_dir
        .join("collector")
        .join("collector.responsibility.db");
    if nested.is_file() {
        return Some(nested);
    }
    let flat = run_dir.join("collector.responsibility.db");
    if flat.is_file() {
        return Some(flat);
    }
    // Shallow search for collector.responsibility.db under any subdirectory.
    let mut candidates = std::fs::read_dir(run_dir)
        .ok()?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.is_dir())
        .filter_map(|dir| {
            let db = dir.join("collector.responsibility.db");
            db.is_file().then_some(db)
        })
        .collect::<Vec<_>>();
    candidates.sort();
    candidates.into_iter().next()
}

/// Stream the raw run SQLite artifact. The frontend consumes it in-browser
/// with sql.js, so no schema knowledge lives on this endpoint.
async fn serve_run_db_api(
    run_dir: &Path,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let run_id = run_dir
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("unknown");
    let Some(db_path) = resolve_responsibility_db_path(run_dir) else {
        return Ok(not_found_json(json_error(
            "db_unavailable",
            run_id,
            "responsibility_db",
        )));
    };

    match tokio::fs::read(&db_path).await {
        Ok(bytes) => Ok(Response::builder()
            .status(StatusCode::OK)
            .header("Content-Type", "application/x-sqlite3")
            .header("Content-Length", bytes.len().to_string())
            .header("X-MtAgentLens-Artifact", "collector.responsibility.db")
            .body(Full::new(Bytes::from(bytes)))
            .unwrap()),
        Err(err) => Ok(internal_error_json(format!(
            "failed to read responsibility db: {err}"
        ))),
    }
}

fn resolve_db_for_alerts(run_dir: &Path) -> Option<PathBuf> {
    // Archived runs may declare an external alerts DB in summary.json; prefer
    // that explicit artifact, then the collector's live responsibility DB.
    if let Some(path) = resolve_artifact_from_summary(run_dir, "alerts_db") {
        return Some(path);
    }
    if let Some(path) = resolve_responsibility_db_path(run_dir) {
        return Some(path);
    }
    // New pipeline: alerts live in the main responsibility DB (trace.log.db)
    let canonical = run_dir.join("trace.log.db");
    if canonical.exists() {
        return Some(canonical);
    }
    // Legacy: alerts DB had "alerts" in the filename
    let mut candidates: Vec<_> = match std::fs::read_dir(run_dir) {
        Ok(entries) => entries
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.extension().is_some_and(|ext| ext == "db"))
            .filter(|p| {
                p.file_name()
                    .and_then(|n| n.to_str())
                    .is_some_and(|n| n.contains("alerts"))
            })
            .collect(),
        Err(_) => return None,
    };
    candidates.sort();
    candidates.into_iter().next()
}

fn resolve_artifact_from_summary(run_dir: &Path, key: &str) -> Option<PathBuf> {
    let summary_path = run_dir.join("summary.json");
    let summary = std::fs::read_to_string(summary_path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&summary).ok()?;
    let artifact = value.get("artifacts")?.get(key)?.as_str()?;
    let path = PathBuf::from(artifact);
    if path.exists() { Some(path) } else { None }
}

fn resolve_events_artifact_path(run_dir: &Path) -> Option<PathBuf> {
    if let Some(path) = resolve_artifact_from_summary(run_dir, "trace_log") {
        return Some(path);
    }
    let canonical = run_dir.join("trace.log");
    if canonical.exists() {
        Some(canonical)
    } else {
        None
    }
}

// ---------------------------------------------------------------------------
// Health Probe Handlers
// ---------------------------------------------------------------------------
// Covers three endpoints defined by PSH-001 Phase C contract:
//   GET /health  — full pipeline status with per-stage metrics
//   GET /live    — process alive? (returns 200, no metrics computation)
//   GET /ready   — pipeline ready? (has processed at least one event)
//
// Design notes:
// - /live must return within 1ms (no locks, no DB queries).
// - /ready uses cached readiness flag (updated on stage transitions).
// - /health returns full HealthResponse schema per v1.0.0.
//   If no health_state is configured, returns baseline with all stages idle.
// ---------------------------------------------------------------------------

/// Serve GET /live: process alive check.
///
/// Returns HTTP 200 immediately without acquiring any locks.
/// This is the "is the process running?" probe for orchestrators/load balancers.
fn serve_live() -> Response<Full<Bytes>> {
    Response::builder()
        .status(StatusCode::OK)
        .header("Content-Type", "application/json")
        .header("Cache-Control", "no-cache, no-store, must-revalidate")
        .body(Full::new(Bytes::from(
            serde_json::json!({"status": "alive"}).to_string(),
        )))
        .unwrap()
}

/// Serve GET /ready: pipeline readiness check.
///
/// Returns HTTP 200 when the pipeline has processed at least one event.
/// Returns HTTP 503 (Service Unavailable) during initial startup or drain.
fn serve_ready(health_state: Option<&Arc<Mutex<HealthState>>>) -> Response<Full<Bytes>> {
    let ready = health_state
        .and_then(|hs| hs.lock().ok())
        .map(|guard| guard.ready)
        .unwrap_or(false);

    if ready {
        Response::builder()
            .status(StatusCode::OK)
            .header("Content-Type", "application/json")
            .header("Cache-Control", "no-cache, no-store, must-revalidate")
            .body(Full::new(Bytes::from(
                serde_json::json!({"status": "ready"}).to_string(),
            )))
            .unwrap()
    } else {
        Response::builder()
            .status(StatusCode::SERVICE_UNAVAILABLE)
            .header("Content-Type", "application/json")
            .header("Cache-Control", "no-cache, no-store, must-revalidate")
            .body(Full::new(Bytes::from(
                serde_json::json!({"status": "not_ready", "reason": "pipeline has not processed any events yet"})
                    .to_string(),
            )))
            .unwrap()
    }
}

/// Serve GET /health: full pipeline health status.
///
/// Returns the canonical HealthResponse schema (v1.0.0).
/// When no health_state is configured, returns a baseline response with
/// all stages showing idle/zero metrics. This allows frontend development
/// to proceed before backend integration is complete.
fn serve_health(health_state: Option<&Arc<Mutex<HealthState>>>) -> Response<Full<Bytes>> {
    let build_version = format!(
        "{}-{}",
        env!("CARGO_PKG_VERSION"),
        option_env!("GIT_COMMIT_HASH").unwrap_or("dev")
    );

    let response = match health_state.and_then(|hs| hs.lock().ok()) {
        Some(guard) => {
            // Live pipeline metrics available
            let status = compute_overall_status(&guard);
            let mut stages_map = HashMap::new();

            for (stage, metrics) in &guard.stage_metrics {
                let stage_name = format!("{:?}", stage);
                stages_map.insert(
                    stage_name,
                    StageMetricsSnapshot::from_stage_metrics(metrics),
                );
            }

            let uptime = guard.started_at.elapsed().as_secs();

            HealthResponse {
                schema_version: HEALTH_SCHEMA_VERSION.to_string(),
                status,
                uptime_seconds: uptime,
                version: build_version,
                degraded_stages: None, // Phase D will populate this
                warnings: None,
                stages: stages_map,
            }
        }
        None => {
            // No health state configured — return baseline (all stages idle)
            let mut stages_map = HashMap::new();
            for stage in &[
                "Preprocessing",
                "CausalLinking",
                "InjectionDetection",
                "AnomalyDetection",
                "AlertEnrichment",
                "Output",
            ] {
                stages_map.insert(
                    stage.to_string(),
                    StageMetricsSnapshot {
                        events_processed: 0,
                        events_emitted: 0,
                        errors: 0,
                        avg_latency_us: 0,
                        p99_latency_us: 0,
                        buffer_size: 0,
                        status: "idle".to_string(),
                    },
                );
            }

            HealthResponse {
                schema_version: HEALTH_SCHEMA_VERSION.to_string(),
                status: HealthStatus::Healthy,
                uptime_seconds: 0,
                version: build_version,
                degraded_stages: None,
                warnings: None,
                stages: stages_map,
            }
        }
    };

    json_response(serde_json::to_value(&response).unwrap_or_else(|_| {
        serde_json::json!({"error": "serialization_failed", "schema_version": HEALTH_SCHEMA_VERSION})
    }))
}

/// Compute overall pipeline health status from per-stage metrics.
///
/// - `unhealthy` if Preprocessing or Output stage is degraded/error.
/// - `degraded` if any other stage is degraded/error.
/// - `healthy` otherwise.
fn compute_overall_status(state: &HealthState) -> HealthStatus {
    use crate::framework::analyzers::pipeline_contract::StageStatus;

    let mut any_degraded = false;

    for (stage, metrics) in &state.stage_metrics {
        match metrics.status {
            StageStatus::Error => {
                // Critical stages (Preprocessing, Output) make the whole pipeline unhealthy
                if *stage == PipelineStage::Preprocessing || *stage == PipelineStage::Output {
                    return HealthStatus::Unhealthy;
                }
                any_degraded = true;
            }
            StageStatus::Degraded => {
                any_degraded = true;
            }
            _ => {}
        }
    }

    if any_degraded {
        HealthStatus::Degraded
    } else {
        HealthStatus::Healthy
    }
}

fn json_response(value: serde_json::Value) -> Response<Full<Bytes>> {
    Response::builder()
        .status(StatusCode::OK)
        .header("Content-Type", "application/json")
        .body(Full::new(Bytes::from(value.to_string())))
        .unwrap()
}

fn not_found_json(value: serde_json::Value) -> Response<Full<Bytes>> {
    Response::builder()
        .status(StatusCode::NOT_FOUND)
        .header("Content-Type", "application/json")
        .body(Full::new(Bytes::from(value.to_string())))
        .unwrap()
}

fn internal_error_json(message: String) -> Response<Full<Bytes>> {
    Response::builder()
        .status(StatusCode::INTERNAL_SERVER_ERROR)
        .header("Content-Type", "application/json")
        .body(Full::new(Bytes::from(
            serde_json::json!({ "error": "internal_error", "message": message }).to_string(),
        )))
        .unwrap()
}

fn structured_error_json(
    status: StatusCode,
    error: &str,
    message: String,
    details: serde_json::Value,
) -> Response<Full<Bytes>> {
    Response::builder()
        .status(status)
        .header("Content-Type", "application/json")
        .body(Full::new(Bytes::from(
            serde_json::json!({
                "error": error,
                "message": message,
                "details": details
            })
            .to_string(),
        )))
        .unwrap()
}

fn json_error(error: &str, run_id: &str, artifact: &str) -> serde_json::Value {
    serde_json::json!({
        "error": error,
        "run_id": run_id,
        "artifact": artifact
    })
}

#[cfg(test)]
mod tests {
    use super::{
        handle_request, match_run_route, resolve_responsibility_db_path, resolve_run_dir,
        serve_alerts_api, serve_events_api, serve_live_events_api, serve_run_db_api,
        serve_run_evaluation_api, serve_run_events_api, serve_run_snapshots_api,
        serve_run_summary_api, serve_runs_index_api,
    };
    use crate::server::assets::FrontendAssets;
    use http_body_util::{BodyExt, Empty};
    use hyper::client::conn::http1 as client_http1;
    use hyper::server::conn::http1;
    use hyper::service::service_fn;
    use hyper::{Method, Request, StatusCode};
    use hyper_util::rt::TokioIo;
    use serde_json::{Value, json};
    use std::sync::{Arc, Mutex, MutexGuard, OnceLock};
    use tempfile::{NamedTempFile, TempDir};
    use tokio::io::duplex;
    use tokio::sync::broadcast;
    #[tokio::test]
    async fn serve_live_events_api_streams_raw_event_frames() {
        use crate::framework::core::Event;
        // http_body::Body 经 hyper::body::Body re-export
        use hyper::body::Body as _;

        let (event_tx, _event_rx) = broadcast::channel::<Event>(16);
        let (alert_tx, _alert_rx) = broadcast::channel::<
            crate::responsibility::types::alert_types::ResponsibilityAlert,
        >(16);
        let resp = serve_live_events_api(event_tx.clone(), Some(alert_tx))
            .await
            .unwrap();

        // SSE 响应头：text/event-stream + 禁缓冲；绝不带 `*` CORS（明文泄漏向量）
        assert_eq!(
            resp.headers().get(hyper::header::CONTENT_TYPE).unwrap(),
            "text/event-stream"
        );
        assert_eq!(resp.headers().get("x-accel-buffering").unwrap(), "no");
        assert!(resp.headers().get("Access-Control-Allow-Origin").is_none());

        // subscribe 之后发送原始事件 → 流应承载 `event: event` 帧
        // （心跳首 tick 立即触发，故读若干帧直到命中原始事件）
        event_tx
            .send(Event::new_with_timestamp(
                1,
                "tls_capture".into(),
                8120,
                "claude".into(),
                serde_json::json!({ "len": 1200 }),
            ))
            .unwrap();

        let mut body = resp.into_body();
        let mut found_raw = false;
        for _ in 0..10 {
            if let Some(Ok(frame)) = body.frame().await {
                if let Ok(bytes) = frame.into_data() {
                    let text = String::from_utf8(bytes.to_vec()).unwrap_or_default();
                    if text.contains("event: event") {
                        assert!(text.contains("\"source\":\"tls_capture\""));
                        found_raw = true;
                        break;
                    }
                }
            }
        }
        assert!(found_raw, "应能从 /api/live/events 读到原始事件帧");
    }
    #[tokio::test]
    async fn serve_events_api_marks_log_backed_responses_as_legacy_and_non_authoritative() {
        let temp = NamedTempFile::new().unwrap();
        tokio::fs::write(
            temp.path(),
            "{\"timestamp\":1,\"source\":\"test\",\"data\":{}}\n",
        )
        .await
        .unwrap();
        let (sender, _) = broadcast::channel(1);

        let response = serve_events_api(sender, Some(temp.path().display().to_string()))
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get("x-mtagentlens-legacy-api").unwrap(),
            "true"
        );
        assert_eq!(
            response.headers().get("x-mtagentlens-authority").unwrap(),
            "non-authoritative"
        );
        assert!(
            response
                .headers()
                .get("warning")
                .unwrap()
                .to_str()
                .unwrap()
                .contains("legacy")
        );

        let body = response.into_body().collect().await.unwrap().to_bytes();
        assert!(
            std::str::from_utf8(&body)
                .unwrap()
                .contains("\"timestamp\":1")
        );
    }
    #[tokio::test]
    async fn serve_events_api_returns_unavailable_without_log_file() {
        let (sender, _) = broadcast::channel(1);

        let response = serve_events_api(sender, None).await.unwrap();

        assert_eq!(response.status(), StatusCode::NOT_FOUND);
        assert_eq!(
            response.headers().get("x-mtagentlens-legacy-api").unwrap(),
            "true"
        );
        assert_eq!(
            response.headers().get("x-mtagentlens-authority").unwrap(),
            "non-authoritative"
        );
        assert!(
            response
                .headers()
                .get("warning")
                .unwrap()
                .to_str()
                .unwrap()
                .contains("legacy")
        );

        let body = response.into_body().collect().await.unwrap().to_bytes();
        let body = std::str::from_utf8(&body).unwrap();
        assert!(body.contains("artifact_unavailable"));
        assert!(!body.contains("SSL handshake completed"));
        assert!(!body.contains("https://api.example.com"));
        assert!(!body.contains("Process started"));
        assert!(!body.contains("api.example.com"));
    }
    #[tokio::test]
    async fn get_run_evaluation_returns_parsed_json_from_evaluation_artifact() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_evaluation(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "score": 0.75
        }));

        let response = serve_run_evaluation_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/json"
        );
        assert_eq!(
            read_json_body(response).await,
            json!({
                "run_id": "test-run",
                "scenario_ids": ["scenario-a"],
                "score": 0.75,
                "warnings": []
            })
        );
    }
    #[tokio::test]
    async fn run_evaluation_api_merges_replay_report_warnings_into_response() {
        let fixture = RunFixture::new("test-run-eval-warning");
        fixture.write_summary(json!({
            "run_id": "test-run-eval-warning",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_evaluation(json!({
            "run_id": "test-run-eval-warning",
            "scenario_ids": ["scenario-a"],
            "score": 0.75
        }));
        fixture.write_raw_file(
            "replay.report.json",
            json!({
                "warnings": [
                    {
                        "code": "stale_causal_artifact_divergence",
                        "severity": "warning",
                        "message": "Persisted causal artifact differs from authoritative replay."
                    }
                ]
            })
            .to_string(),
        );

        let response = serve_run_evaluation_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "run_id": "test-run-eval-warning",
                "scenario_ids": ["scenario-a"],
                "score": 0.75,
                "warnings": [
                    {
                        "code": "stale_causal_artifact_divergence",
                        "severity": "warning",
                        "message": "Persisted causal artifact differs from authoritative replay."
                    }
                ]
            })
        );
    }
    #[tokio::test]
    async fn handle_request_routes_runs_index_through_get_path() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a", "scenario-b"],
            "status": "completed"
        }));
        fixture.write_evaluation(json!({"ok": true}));
        fixture.write_alerts_db(&[]);
        fixture.write_correlation_evidence_db(&[]);
        let _test_lock = test_env_lock();
        let _guard = fixture.enter_repo_root();

        let response = issue_get("/api/runs").await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/json"
        );
        let body = read_json_body(response).await;
        assert_eq!(
            body,
            json!([
                {
                    "run_id": "test-run",
                    "scenario_ids": ["scenario-a", "scenario-b"],
                    "run_status": "completed",
                    "artifact_availability": {
                        "evaluation": true,
                        "alerts": true,
                        "correlation_evidence": true,
                        "events": false,
                        "database": true
                    }
                }
            ])
        );
        assert_run_bound_payload_is_not_legacy_sample_data(&body);
    }
    #[tokio::test]
    async fn handle_request_routes_scenarios_index_through_get_path() {
        let fixture = RunFixture::new("test-run");
        fixture.write_scenario_metadata(
            "scenario-z",
            json!({
                "id": "scenario-z",
                "description": "Zulu scenario"
            }),
        );
        fixture.write_scenario_metadata(
            "scenario-a",
            json!({
                "id": "scenario-a",
                "description": "Alpha scenario"
            }),
        );
        let _test_lock = test_env_lock();
        let _guard = fixture.enter_repo_root();

        let response = issue_get("/api/scenarios").await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/json"
        );
        let body = read_json_body(response).await;
        assert_eq!(
            body,
            json!([
                {
                    "scenario_id": "scenario-a",
                    "label": "Alpha scenario"
                },
                {
                    "scenario_id": "scenario-z",
                    "label": "Zulu scenario"
                }
            ])
        );
        assert_run_bound_payload_is_not_legacy_sample_data(&body);
    }
    #[tokio::test]
    async fn handle_request_routes_runs_index_skips_malformed_run_summaries() {
        let valid = RunFixture::new("valid-run");
        valid.write_summary(json!({
            "run_id": "valid-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        let malformed = RunFixture::new_in_repo(&valid.repo_root, "broken-run");
        malformed.write_raw_summary("{not-json");
        let _test_lock = test_env_lock();
        let _guard = valid.enter_repo_root();

        let response = issue_get("/api/runs").await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!([
                {
                    "run_id": "valid-run",
                    "scenario_ids": ["scenario-a"],
                    "run_status": "completed",
                    "artifact_availability": {
                        "evaluation": false,
                        "alerts": false,
                        "correlation_evidence": false,
                        "events": false,
                        "database": false
                    }
                }
            ])
        );
    }
    #[tokio::test]
    async fn handle_request_routes_scenarios_index_skips_malformed_metadata_files() {
        let fixture = RunFixture::new("test-run");
        fixture.write_scenario_metadata(
            "scenario-z",
            json!({
                "id": "scenario-z",
                "description": "Zulu scenario"
            }),
        );
        fixture.write_raw_scenario_metadata("scenario-bad", "{not-json");
        fixture.write_scenario_metadata(
            "scenario-a",
            json!({
                "id": "scenario-a",
                "description": "Alpha scenario"
            }),
        );
        let _test_lock = test_env_lock();
        let _guard = fixture.enter_repo_root();

        let response = issue_get("/api/scenarios").await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!([
                {
                    "scenario_id": "scenario-a",
                    "label": "Alpha scenario"
                },
                {
                    "scenario_id": "scenario-z",
                    "label": "Zulu scenario"
                }
            ])
        );
    }
    #[test]
    fn collection_routes_remain_distinct_from_run_detail_routes() {
        assert!(match_run_route("/api/runs").is_none());
        assert!(match_run_route("/api/scenarios").is_none());
        assert!(match_run_route("/api/events").is_none());
        assert!(match_run_route("/api/runs/test-run").is_some());
        assert!(match_run_route("/api/runs/test-run/events").is_some());
    }
    #[test]
    fn db_route_is_distinct_from_collection_routes() {
        assert!(match_run_route("/api/runs").is_none());
        assert!(match_run_route("/api/runs/test-run/db").is_some());
        assert!(match_run_route("/api/runs/test-run/db/extra").is_none());
    }
    #[test]
    fn resolve_responsibility_db_path_finds_nested_collector_layout() {
        let fixture = RunFixture::new("run-db-layout");
        let collector_dir = fixture.run_dir().join("collector");
        std::fs::create_dir_all(&collector_dir).unwrap();
        std::fs::write(
            collector_dir.join("collector.responsibility.db"),
            b"SQLite format 3\x00fixture-bytes",
        )
        .unwrap();

        let resolved = resolve_responsibility_db_path(fixture.run_dir());
        assert_eq!(
            resolved,
            Some(collector_dir.join("collector.responsibility.db"))
        );
    }
    #[test]
    fn resolve_responsibility_db_path_returns_none_when_absent() {
        let fixture = RunFixture::new("run-no-db");
        assert!(resolve_responsibility_db_path(fixture.run_dir()).is_none());
    }
    #[tokio::test]
    async fn serve_run_db_api_streams_the_raw_responsibility_db() {
        let fixture = RunFixture::new("run-db-api");
        let collector_dir = fixture.run_dir().join("collector");
        std::fs::create_dir_all(&collector_dir).unwrap();
        let db_bytes = b"SQLite format 3\x00fake-db-content-abc123";
        std::fs::write(collector_dir.join("collector.responsibility.db"), db_bytes).unwrap();

        let response = serve_run_db_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/x-sqlite3"
        );
        assert_eq!(
            response.headers().get("x-mtagentlens-artifact").unwrap(),
            "collector.responsibility.db"
        );
        let body = response.into_body().collect().await.unwrap().to_bytes();
        assert_eq!(&body[..], db_bytes);
    }
    #[tokio::test]
    async fn serve_run_db_api_404s_when_no_db_is_present() {
        let fixture = RunFixture::new("run-db-missing");
        let response = serve_run_db_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
    }
    #[tokio::test]
    async fn runs_index_reports_database_availability_for_real_layout() {
        let fixture = RunFixture::new("run-with-responsibility-db");
        fixture.write_summary(json!({
            "run_id": "run-with-responsibility-db",
            "scenario_ids": [],
            "status": "completed"
        }));
        let collector_dir = fixture.run_dir().join("collector");
        std::fs::create_dir_all(&collector_dir).unwrap();
        std::fs::write(
            collector_dir.join("collector.responsibility.db"),
            b"SQLite format 3\x00x",
        )
        .unwrap();

        let _guard = fixture.enter_repo_root();
        let response = serve_runs_index_api().await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = response.into_body().collect().await.unwrap().to_bytes();
        let value: Value = serde_json::from_slice(&body).unwrap();
        let run = value
            .as_array()
            .unwrap()
            .iter()
            .find(|entry| entry["run_id"] == "run-with-responsibility-db")
            .expect("run must appear in the index");
        assert_eq!(run["artifact_availability"]["database"], true);
    }
    #[tokio::test]
    async fn handle_request_routes_run_summary_through_get_path() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a", "scenario-b"],
            "status": "completed"
        }));
        fixture.write_evaluation(json!({"ok": true}));
        fixture.write_alerts_db(&[]);
        let _test_lock = test_env_lock();
        let _guard = fixture.enter_repo_root();

        let response = issue_get("/api/runs/test-run").await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/json"
        );
        let body = read_json_body(response).await;
        assert_eq!(
            body,
            json!({
                "run_id": "test-run",
                "scenario_ids": ["scenario-a", "scenario-b"],
                "run_status": "completed",
                "artifact_availability": {
                    "evaluation": true,
                    "alerts": true,
                    "correlation_evidence": true,
                    "events": false,
                    "database": true
                }
            })
        );
        assert_run_bound_payload_is_not_legacy_sample_data(&body);
    }
    #[tokio::test]
    async fn handle_request_routes_active_run_to_lexicographically_greatest_valid_summary() {
        let fixture = RunFixture::new("run-2026-04-28-b");
        fixture.write_summary(json!({
            "run_id": "run-2026-04-28-b",
            "scenario_ids": ["scenario-b"],
            "status": "completed"
        }));
        fixture.write_evaluation(json!({"ok": true}));
        fixture.write_alerts_db(&[]);

        let smaller = RunFixture::new_in_repo(&fixture.repo_root, "run-2026-04-28-a");
        smaller.write_summary(json!({
            "run_id": "run-2026-04-28-a",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));

        let malformed = RunFixture::new_in_repo(&fixture.repo_root, "run-2026-04-28-z");
        malformed.write_raw_summary("{not-json");

        let _test_lock = test_env_lock();
        let _guard = fixture.enter_repo_root();

        let response = issue_get("/api/runs/active").await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/json"
        );
        let body = read_json_body(response).await;
        assert_eq!(
            body,
            json!({
                "run_id": "run-2026-04-28-b",
                "scenario_ids": ["scenario-b"],
                "run_status": "completed",
                "artifact_availability": {
                    "evaluation": true,
                    "alerts": true,
                    "correlation_evidence": true,
                    "events": false,
                    "database": true
                }
            })
        );
        assert_run_bound_payload_is_not_legacy_sample_data(&body);
    }
    #[tokio::test]
    async fn handle_request_routes_active_run_returns_no_active_run_when_no_valid_runs_exist() {
        let fixture = RunFixture::new("broken-run");
        fixture.write_raw_summary("{not-json");
        let _test_lock = test_env_lock();
        let _guard = fixture.enter_repo_root();

        let response = issue_get("/api/runs/active").await;
        // 契约：无活跃 run → 200 {data:null}（前端据此判定 run 结束，转回放）
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(read_json_body(response).await, json!({ "data": null }));
    }
    #[tokio::test]
    async fn handle_request_routes_active_run_does_not_collide_with_generic_run_id_routing() {
        let fixture = RunFixture::new("real-run");
        fixture.write_summary(json!({
            "run_id": "real-run",
            "scenario_ids": ["scenario-real"],
            "status": "completed"
        }));

        let reserved = RunFixture::new_in_repo(&fixture.repo_root, "active");
        reserved.write_summary(json!({
            "run_id": "active",
            "scenario_ids": ["scenario-reserved"],
            "status": "failed"
        }));

        let _test_lock = test_env_lock();
        let _guard = fixture.enter_repo_root();

        let response = issue_get("/api/runs/active").await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "run_id": "real-run",
                "scenario_ids": ["scenario-real"],
                "run_status": "completed",
                "artifact_availability": {
                    "evaluation": false,
                    "alerts": false,
                    "correlation_evidence": false,
                    "events": false,
                    "database": false
                }
            })
        );
    }
    #[tokio::test]
    async fn handle_request_routes_run_evaluation_through_get_path() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_evaluation(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "score": 0.75
        }));
        let _test_lock = test_env_lock();
        let _guard = fixture.enter_repo_root();

        let response = issue_get("/api/runs/test-run/evaluation").await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/json"
        );
        let body = read_json_body(response).await;
        assert_eq!(
            body,
            json!({
                "run_id": "test-run",
                "scenario_ids": ["scenario-a"],
                "score": 0.75,
                "warnings": []
            })
        );
        assert_run_bound_payload_is_not_legacy_sample_data(&body);
    }
    #[tokio::test]
    async fn get_run_alerts_returns_json_rows_from_alerts_sqlite_db() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a", "scenario-b"],
            "status": "completed"
        }));
        fixture.write_alerts_db(&[
            json!({
                "alert_id": "alert-1",
                "agent_id": "agent-1",
                "timestamp_ns": 123,
                "display_profile": "observer_judged",
                "decision": "unsafe",
                "severity": "warning",
                "confidence": 0.8,
                "authority": "authoritative",
                "reasoning": "agent behavior matches known attack pattern",
                "title": "Suspicious file access detected",
                "summary": "agent-1 accessed /etc/shadow without explicit authorization",
                "action_hint": "review the policy violation and linked evidence"
            }),
            json!({
                "alert_id": "alert-2",
                "agent_id": "agent-2",
                "timestamp_ns": 456,
                "display_profile": "unsafe_by_engine",
                "decision": "unsafe",
                "severity": "critical",
                "confidence": 0.95,
                "authority": "authoritative",
                "reasoning": "engine score 0.85 exceeds threshold",
                "title": "Privilege escalation detected",
                "summary": "agent-2 executed commands with elevated permissions",
                "action_hint": "investigate the privilege escalation immediately"
            }),
        ]);

        let response = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/json"
        );
        let body = read_json_body(response).await;
        let alerts = body.as_array().expect("alerts response must be an array");
        assert_eq!(alerts.len(), 2);
        // Newest first.
        assert_eq!(alerts[0]["alert_id"], "alert-2");
        assert_eq!(alerts[1]["alert_id"], "alert-1");
        // The REST payload must match the SSE ResponsibilityAlert shape:
        // primary_agent (not a flat agent_id), typed enums, and phase.
        assert_eq!(alerts[0]["primary_agent"]["agent_id"], "agent-2");
        assert_eq!(alerts[0]["display_profile"], "UnsafeByEngine");
        assert_eq!(alerts[0]["decision"], "Unsafe");
        assert_eq!(alerts[0]["severity"], "Critical");
        assert_eq!(alerts[0]["authority"], "Authoritative");
        assert!(alerts[0].get("phase").is_some());
        assert!(alerts[0].get("involved_agents").is_some());
    }
    #[tokio::test]
    async fn get_run_alerts_returns_500_on_live_schema_mismatch() {
        let fixture = RunFixture::new("test-run-bad-alerts");
        fixture.write_summary(json!({
            "run_id": "test-run-bad-alerts",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        let collector_dir = fixture.run_dir().join("collector");
        std::fs::create_dir_all(&collector_dir).unwrap();
        let conn =
            rusqlite::Connection::open(collector_dir.join("collector.responsibility.db")).unwrap();
        // A table that exists but lacks primary_agent_id: the old handler
        // returned 200 [] here, making a broken artifact look like "no alerts".
        conn.execute_batch("CREATE TABLE responsibility_alerts (alert_id TEXT PRIMARY KEY);")
            .unwrap();
        conn.execute(
            "INSERT INTO responsibility_alerts (alert_id) VALUES ('a')",
            [],
        )
        .unwrap();
        drop(conn);

        let response = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        let body = read_json_body(response).await;
        assert!(
            body.get("message")
                .and_then(|value| value.as_str())
                .is_some_and(|message| message.contains("alerts_schema_error")),
            "schema mismatch must be explicit: {body}"
        );
    }
    #[tokio::test]
    async fn handle_request_routes_run_alerts_through_get_path() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a", "scenario-b"],
            "status": "completed"
        }));
        fixture.write_alerts_db(&[json!({
            "alert_id": "alert-1",
            "agent_id": "agent-1",
            "timestamp_ns": 123,
            "display_profile": "observer_judged",
            "decision": "unsafe",
            "severity": "warning",
            "confidence": 0.8,
            "authority": "authoritative",
            "reasoning": "test reasoning",
            "title": "test title",
            "summary": "test summary",
            "action_hint": "test action"
        })]);

        let _test_lock = test_env_lock();
        let _guard = fixture.enter_repo_root();

        let response = issue_get("/api/runs/test-run/alerts").await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/json"
        );
        let body = read_json_body(response).await;
        let alerts = body.as_array().expect("alerts response must be an array");
        assert_eq!(alerts.len(), 1);
        assert_eq!(alerts[0]["alert_id"], "alert-1");
        // Same ResponsibilityAlert shape as SSE: primary_agent + typed enums.
        assert_eq!(alerts[0]["primary_agent"]["agent_id"], "agent-1");
        assert_eq!(alerts[0]["display_profile"], "ObserverJudged");
        assert_eq!(alerts[0]["decision"], "Unsafe");
        assert_eq!(alerts[0]["severity"], "Warning");
        assert!(alerts[0].get("phase").is_some());
        assert_run_bound_payload_is_not_legacy_sample_data(&body);
    }

    #[tokio::test]
    async fn test_snapshots_api_returns_snapshot_list_from_graph_snapshots() {
        let fixture = RunFixture::new("test-run-snapshots-api");
        fixture.write_summary(
            json!({"run_id": "test-run-snapshots-api", "scenario_ids": [], "status": "completed"}),
        );
        fixture.write_live_snapshots(&[
            (
                "snap-api-1",
                100,
                json!([]),
                json!([]),
                json!({"node_counts": {}, "edge_counts": {}, "agent_count": 0,
                       "cross_agent_edges": 0, "delegation_depth": 0,
                       "taint_chains": 0, "avg_confidence": 0.0}),
            ),
            (
                "snap-api-2",
                200,
                json!([{"node_id": "n1", "node_type": "Agent"}]),
                json!([]),
                json!({"node_counts": {"Agent": 1}, "edge_counts": {}, "agent_count": 1,
                       "cross_agent_edges": 0, "delegation_depth": 0,
                       "taint_chains": 0, "avg_confidence": 1.0}),
            ),
        ]);

        let response = serve_run_snapshots_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        // The frontend contract is a bare array of GraphSnapshot.
        let frames = body.as_array().expect("snapshots must be an array");
        assert_eq!(frames.len(), 2);
        assert_eq!(frames[0]["snapshot_id"], "snap-api-1");
        assert_eq!(frames[1]["snapshot_id"], "snap-api-2");
        assert!(frames[1]["nodes"].is_array());
        assert!(frames[1]["edges"].is_array());
        assert_eq!(frames[1]["metrics"]["agent_count"], 1);
    }
    #[tokio::test]
    async fn test_snapshots_api_returns_empty_array_when_the_live_table_is_empty() {
        let fixture = RunFixture::new("test-run-no-snap");
        fixture.write_summary(
            json!({"run_id": "test-run-no-snap", "scenario_ids": [], "status": "completed"}),
        );
        fixture.write_live_snapshots(&[]);

        let response = serve_run_snapshots_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        assert_eq!(body, json!([]));
    }
    #[tokio::test]
    async fn handle_request_returns_404_for_the_retired_correlation_evidence_route() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        let _test_lock = test_env_lock();
        let _guard = fixture.enter_repo_root();

        // The route served the retired causal schema; it is gone rather than
        // silently answering from a file the collector no longer writes.
        let response = issue_get("/api/runs/test-run/correlation-evidence").await;
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
    }
    #[tokio::test]
    async fn get_run_events_returns_structured_json_from_trace_log_artifact() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_trace_log_lines(&[
            json!({
                "timestamp": 1777226074360i64,
                "source": "system",
                "pid": 108862,
                "comm": "claude",
                "data": {
                    "type": "system_metrics",
                    "timestamp": 14922360000000i64,
                    "alert": false
                }
            }),
            json!({
                "timestamp": 1777226074558i64,
                "source": "process",
                "pid": 0,
                "comm": "process:CLOCK_SYNC",
                "data": {
                    "event": "CLOCK_SYNC",
                    "timestamp": 14922558979468i64,
                    "phase": "start"
                }
            }),
        ]);

        let response = serve_run_events_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/json"
        );
        assert_eq!(
            read_json_body(response).await,
            json!([
                {
                    "id": "test-run:1",
                    "timestamp": 1777226074360i64,
                    "source": "system",
                    "pid": 108862,
                    "comm": "claude",
                    "data": {
                        "type": "system_metrics",
                        "timestamp": 14922360000000i64,
                        "alert": false
                    }
                },
                {
                    "id": "test-run:2",
                    "timestamp": 1777226074558i64,
                    "source": "process",
                    "pid": 0,
                    "comm": "process:CLOCK_SYNC",
                    "data": {
                        "event": "CLOCK_SYNC",
                        "timestamp": 14922558979468i64,
                        "phase": "start"
                    }
                }
            ])
        );
    }
    #[tokio::test]
    async fn handle_request_routes_run_events_distinctly_from_legacy_api_events() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_trace_log_lines(&[json!({
            "timestamp": 1,
            "source": "process",
            "pid": 42,
            "comm": "runner",
            "data": {"event": "EXEC"}
        })]);
        let _test_lock = test_env_lock();
        let _guard = fixture.enter_repo_root();

        let response = issue_get("/api/runs/test-run/events").await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/json"
        );
        let body = read_json_body(response).await;
        assert_eq!(
            body,
            json!([
                {
                    "id": "test-run:1",
                    "timestamp": 1,
                    "source": "process",
                    "pid": 42,
                    "comm": "runner",
                    "data": {"event": "EXEC"}
                }
            ])
        );
        assert_run_bound_payload_is_not_legacy_sample_data(&body);
    }
    #[tokio::test]
    async fn get_run_events_reads_summary_declared_trace_log_artifact() {
        let fixture = RunFixture::new("test-run-summary-trace-log");
        let external_trace_log = fixture.repo_root.join("external-trace.log");
        std::fs::write(
            &external_trace_log,
            format!(
                "{}\n",
                serde_json::to_string(&json!({
                    "timestamp": 7,
                    "source": "process",
                    "pid": 9,
                    "comm": "external-runner",
                    "data": {"event": "EXEC"}
                }))
                .unwrap()
            ),
        )
        .unwrap();
        fixture.write_summary(json!({
            "run_id": "test-run-summary-trace-log",
            "scenario_ids": ["scenario-a"],
            "status": "completed",
            "artifacts": {
                "trace_log": external_trace_log.to_string_lossy()
            }
        }));

        let summary = serve_run_summary_api(fixture.run_dir(), "test-run-summary-trace-log")
            .await
            .unwrap();
        assert_eq!(summary.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(summary).await,
            json!({
                "run_id": "test-run-summary-trace-log",
                "scenario_ids": ["scenario-a"],
                "run_status": "completed",
                "artifact_availability": {
                    "evaluation": false,
                    "alerts": false,
                    "correlation_evidence": false,
                    "events": true,
                    "database": false
                }
            })
        );

        let response = serve_run_events_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!([
                {
                    "id": "test-run-summary-trace-log:1",
                    "timestamp": 7,
                    "source": "process",
                    "pid": 9,
                    "comm": "external-runner",
                    "data": {"event": "EXEC"}
                }
            ])
        );
    }
    #[tokio::test]
    async fn get_run_alerts_reads_summary_declared_alerts_db_artifact() {
        let fixture = RunFixture::new("test-run-summary-alerts-db");
        let external_alerts_db = fixture.repo_root.join("external.alerts.db");
        fixture.write_summary(json!({
            "run_id": "test-run-summary-alerts-db",
            "scenario_ids": ["scenario-a"],
            "status": "completed",
            "artifacts": {
                "alerts_db": external_alerts_db.to_string_lossy()
            }
        }));
        fixture.write_named_alerts_db(
            &external_alerts_db,
            &[json!({
                "alert_id": "external-alert-1",
                "agent_id": "agent-external",
                "timestamp_ns": 321,
                "display_profile": "observer_judged",
                "decision": "unsafe",
                "severity": "warning",
                "confidence": 0.66,
                "authority": "authoritative",
                "reasoning": "external alert reasoning",
                "title": "External alert title",
                "summary": "External alert summary",
                "action_hint": "review external alert"
            })],
        );

        let summary = serve_run_summary_api(fixture.run_dir(), "test-run-summary-alerts-db")
            .await
            .unwrap();
        assert_eq!(summary.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(summary).await,
            json!({
                "run_id": "test-run-summary-alerts-db",
                "scenario_ids": ["scenario-a"],
                "run_status": "completed",
                "artifact_availability": {
                    "evaluation": false,
                    "alerts": true,
                    "correlation_evidence": false,
                    "events": false,
                    "database": false
                }
            })
        );

        let response = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        let alerts = body.as_array().expect("alerts response must be an array");
        assert_eq!(alerts.len(), 1);
        assert_eq!(alerts[0]["alert_id"], "external-alert-1");
        assert_eq!(alerts[0]["primary_agent"]["agent_id"], "agent-external");
        assert_eq!(alerts[0]["display_profile"], "ObserverJudged");
        assert_eq!(alerts[0]["decision"], "Unsafe");
        assert_eq!(alerts[0]["severity"], "Warning");
        assert!(alerts[0].get("phase").is_some());
    }
    #[tokio::test]
    async fn missing_run_events_artifact_returns_explicit_unavailable_response() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));

        let response = serve_run_events_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "artifact_unavailable",
                "run_id": "test-run",
                "artifact": "events"
            })
        );
    }
    #[tokio::test]
    async fn malformed_run_events_artifact_returns_explicit_error_without_partial_success() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_raw_trace_log("{\"timestamp\":1,\"source\":\"process\"}\n{not-json}\n");

        let response = serve_run_events_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_events_artifact",
                "message": "failed to parse trace.log for replay",
                "details": {
                    "line": 2,
                    "artifact": "trace.log"
                }
            })
        );
    }
    #[tokio::test]
    async fn get_run_summary_returns_minimal_summary_payload_from_summary_json() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a", "scenario-b"],
            "status": "completed"
        }));
        fixture.write_evaluation(json!({"ok": true}));
        fixture.write_alerts_db(&[]);

        let response = serve_run_summary_api(fixture.run_dir(), "test-run")
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "run_id": "test-run",
                "scenario_ids": ["scenario-a", "scenario-b"],
                "run_status": "completed",
                "artifact_availability": {
                    "evaluation": true,
                    "alerts": true,
                    "correlation_evidence": true,
                    "events": false,
                    "database": true
                }
            })
        );
    }
    #[test]
    fn resolve_run_dir_maps_run_id_to_env_runs_directory() {
        let fixture = RunFixture::new("test-run");
        let _test_lock = test_env_lock();
        let _guard = fixture.enter_repo_root();

        let run_dir = resolve_run_dir("test-run").unwrap();
        assert_eq!(run_dir, fixture.run_dir());
    }

    struct RunFixture {
        _tempdir: TempDir,
        repo_root: std::path::PathBuf,
        run_dir: std::path::PathBuf,
    }

    impl RunFixture {
        fn new(run_id: &str) -> Self {
            let tempdir = TempDir::new().unwrap();
            let repo_root = tempdir.path().to_path_buf();
            Self::new_with_tempdir(tempdir, repo_root, run_id)
        }

        fn new_in_repo(repo_root: &std::path::Path, run_id: &str) -> Self {
            let tempdir = TempDir::new().unwrap();
            Self::new_with_tempdir(tempdir, repo_root.to_path_buf(), run_id)
        }

        fn new_with_tempdir(tempdir: TempDir, repo_root: std::path::PathBuf, run_id: &str) -> Self {
            let run_dir = repo_root.join("env").join("runs").join(run_id);
            std::fs::create_dir_all(&run_dir).unwrap();
            Self {
                _tempdir: tempdir,
                repo_root,
                run_dir,
            }
        }

        fn run_dir(&self) -> &std::path::Path {
            &self.run_dir
        }

        fn write_scenario_metadata(&self, scenario_id: &str, metadata: Value) {
            let scenarios_dir = self.repo_root.join("env").join("scenarios");
            std::fs::create_dir_all(&scenarios_dir).unwrap();
            std::fs::write(
                scenarios_dir.join(format!("{scenario_id}.json")),
                serde_json::to_vec(&metadata).unwrap(),
            )
            .unwrap();
        }

        fn write_raw_scenario_metadata(&self, scenario_id: &str, raw: &str) {
            let scenarios_dir = self.repo_root.join("env").join("scenarios");
            std::fs::create_dir_all(&scenarios_dir).unwrap();
            std::fs::write(scenarios_dir.join(format!("{scenario_id}.json")), raw).unwrap();
        }

        fn write_summary(&self, summary: Value) {
            std::fs::write(
                self.run_dir.join("summary.json"),
                serde_json::to_vec(&summary).unwrap(),
            )
            .unwrap();
        }

        fn write_raw_summary(&self, raw: &str) {
            std::fs::write(self.run_dir.join("summary.json"), raw).unwrap();
        }

        fn write_evaluation(&self, evaluation: Value) {
            std::fs::write(
                self.run_dir.join("evaluation.json"),
                serde_json::to_vec(&evaluation).unwrap(),
            )
            .unwrap();
        }

        fn write_raw_file(&self, name: &str, content: String) {
            std::fs::write(self.run_dir.join(name), content).unwrap();
        }

        fn write_trace_log_lines(&self, events: &[Value]) {
            let mut content = String::new();
            for event in events {
                content.push_str(&serde_json::to_string(event).unwrap());
                content.push('\n');
            }
            std::fs::write(self.run_dir.join("trace.log"), content).unwrap();
        }

        fn write_raw_trace_log(&self, raw: &str) {
            std::fs::write(self.run_dir.join("trace.log"), raw).unwrap();
        }

        fn write_alerts_db(&self, alerts: &[Value]) {
            self.write_named_alerts_db(
                &self
                    .run_dir
                    .join("collector")
                    .join("collector.responsibility.db"),
                alerts,
            );
        }

        /// Write alerts into the live schema, which is what the collector emits.
        fn write_named_alerts_db(&self, path: &std::path::Path, alerts: &[Value]) {
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent).unwrap();
            }
            let conn =
                crate::responsibility::persistence::open_responsibility_db(&path.to_string_lossy())
                    .unwrap();

            for alert in alerts {
                let enum_name = |key: &str| {
                    let raw = alert.get(key).and_then(|v| v.as_str()).unwrap_or("");
                    match raw {
                        "observer_judged" => "ObserverJudged",
                        "unsafe_by_engine" => "UnsafeByEngine",
                        "safe_baseline" => "SafeBaseline",
                        "unsafe" => "Unsafe",
                        "uncertain" => "Uncertain",
                        "safe" => "Safe",
                        "warning" => "Warning",
                        "critical" => "Critical",
                        "info" => "Info",
                        "authoritative" => "Authoritative",
                        "supporting" => "Supporting",
                        "unavailable" => "Unavailable",
                        other => other,
                    }
                };
                conn.execute(
                    "INSERT INTO responsibility_alerts (
                        alert_id, timestamp_ns, display_profile, decision, severity, confidence,
                        authority, reasoning, title, summary, action_hint, primary_agent_id,
                        involved_agents_json, engine_section_json, timeline_json,
                        graph_metrics_json, phase, provisional_ref, run_id
                    ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19)",
                    rusqlite::params![
                        alert.get("alert_id").unwrap().as_str().unwrap(),
                        alert.get("timestamp_ns").unwrap().as_i64().unwrap(),
                        enum_name("display_profile"),
                        enum_name("decision"),
                        enum_name("severity"),
                        alert.get("confidence").unwrap().as_f64().unwrap(),
                        enum_name("authority"),
                        alert.get("reasoning").and_then(|v| v.as_str()).unwrap_or(""),
                        alert.get("title").and_then(|v| v.as_str()).unwrap_or(""),
                        alert.get("summary").and_then(|v| v.as_str()).unwrap_or(""),
                        alert.get("action_hint").and_then(|v| v.as_str()),
                        alert.get("agent_id").and_then(|v| v.as_str()).unwrap_or("agent-unknown"),
                        "[]",
                        "{}",
                        "{\"steps\":[],\"total_duration_ms\":0}",
                        "{}",
                        "Provisional { timestamp_ns: 0 }",
                        Option::<String>::None,
                        "test-run",
                    ],
                )
                .unwrap();
            }
        }

        fn write_correlation_evidence_db(&self, links: &[Value]) {
            self.write_named_causal_links_db("trace.log.causal.db", links);
        }

        /// Seed the live `graph_snapshots` table the way the collector writes it.
        fn write_live_snapshots(&self, frames: &[(&str, i64, Value, Value, Value)]) {
            let collector_dir = self.run_dir.join("collector");
            std::fs::create_dir_all(&collector_dir).unwrap();
            let db_path = collector_dir.join("collector.responsibility.db");
            let conn = crate::responsibility::persistence::schema::open_responsibility_db(
                &db_path.to_string_lossy(),
            )
            .unwrap();
            for (snapshot_id, timestamp_ns, nodes, edges, metrics) in frames {
                conn.execute(
                    "INSERT OR REPLACE INTO graph_snapshots
                     (snapshot_id, timestamp_ns, nodes_json, edges_json, metrics_json, run_id)
                     VALUES (?1, ?2, ?3, ?4, ?5, 'run')",
                    rusqlite::params![
                        snapshot_id,
                        timestamp_ns,
                        nodes.to_string(),
                        edges.to_string(),
                        metrics.to_string(),
                    ],
                )
                .unwrap();
            }
        }

        fn write_named_causal_links_db(&self, filename: &str, links: &[Value]) {
            self.write_named_causal_links_db_path(&self.run_dir.join(filename), links);
        }

        fn write_named_causal_links_db_path(&self, path: &std::path::Path, links: &[Value]) {
            let conn = rusqlite::Connection::open(path).unwrap();
            conn.execute_batch(
                "CREATE TABLE correlation_evidence (
                    schema_version INTEGER NOT NULL,
                    evidence_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    timestamp_ns INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    match_level TEXT NOT NULL,
                    matched_fields_json TEXT,
                    supporting_raw_event_refs_json TEXT,
                    conversation_id TEXT,
                    authority_source TEXT,
                    message_id TEXT,
                    source_event_ref TEXT,
                    debug_notes TEXT
                );",
            )
            .unwrap();

            for link in links {
                conn.execute(
                    "INSERT INTO correlation_evidence (
                        schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns,
                        confidence, match_level, matched_fields_json, supporting_raw_event_refs_json,
                        conversation_id, authority_source, message_id, source_event_ref, debug_notes
                    ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
                    rusqlite::params![
                        link.get("schema_version").unwrap().as_i64().unwrap(),
                        link.get("evidence_id").unwrap().as_str().unwrap(),
                        link.get("agent_id").unwrap().as_str().unwrap(),
                        link.get("intent_id").unwrap().as_str().unwrap(),
                        link.get("candidate_id").unwrap().as_str().unwrap(),
                        link.get("timestamp_ns").unwrap().as_i64().unwrap(),
                        link.get("confidence").unwrap().as_f64().unwrap(),
                        link.get("match_level").unwrap().as_str().unwrap(),
                        link.get("matched_fields").map(serde_json::to_string).transpose().unwrap(),
                        link.get("supporting_raw_event_refs").map(serde_json::to_string).transpose().unwrap(),
                        link.get("conversation_id").and_then(Value::as_str),
                        link.get("authority_source").and_then(Value::as_str),
                        link.get("message_id").and_then(Value::as_str),
                        link.get("source_event_ref").and_then(Value::as_str),
                        link.get("debug_notes").and_then(Value::as_str),
                    ],
                )
                .unwrap();
            }
        }

        fn enter_repo_root(&self) -> RepoRootGuard {
            RepoRootGuard::set(&self.repo_root)
        }
    }

    struct RepoRootGuard {
        previous: std::path::PathBuf,
    }

    impl RepoRootGuard {
        fn set(path: &std::path::Path) -> Self {
            let previous = std::env::current_dir().unwrap();
            std::env::set_current_dir(path).unwrap();
            Self { previous }
        }
    }

    impl Drop for RepoRootGuard {
        fn drop(&mut self) {
            std::env::set_current_dir(&self.previous).unwrap();
        }
    }

    async fn issue_get(path: &str) -> hyper::Response<hyper::body::Incoming> {
        let assets = Arc::new(FrontendAssets::new().unwrap());
        let (sender, _) = broadcast::channel(1);

        let (client_io, server_io) = duplex(4096);
        let server_assets = assets.clone();
        let server_sender = sender.clone();
        let server = tokio::spawn(async move {
            http1::Builder::new()
                .serve_connection(
                    TokioIo::new(server_io),
                    service_fn(move |req| {
                        handle_request(
                            req,
                            server_assets.clone(),
                            server_sender.clone(),
                            None,
                            None,
                        )
                    }),
                )
                .await
                .unwrap();
        });

        let (mut request_sender, connection) = client_http1::handshake(TokioIo::new(client_io))
            .await
            .unwrap();
        let client = tokio::spawn(async move {
            connection.await.unwrap();
        });

        let response = request_sender
            .send_request(
                Request::builder()
                    .method(Method::GET)
                    .uri(path)
                    .body(Empty::<hyper::body::Bytes>::new())
                    .unwrap(),
            )
            .await
            .unwrap();

        drop(request_sender);
        client.await.unwrap();
        server.await.unwrap();
        response
    }

    fn assert_run_bound_payload_is_not_legacy_sample_data(body: &Value) {
        let body_text = body.to_string();
        assert!(!body_text.contains("SSL handshake completed"));
        assert!(!body_text.contains("Process started"));
        assert!(!body_text.contains("HTTP request"));
        assert!(body.get("source").is_none());
        assert!(body.get("comm").is_none());
    }

    fn test_env_lock() -> MutexGuard<'static, ()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        match LOCK.get_or_init(|| Mutex::new(())).lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        }
    }

    async fn read_json_body<B>(response: hyper::Response<B>) -> Value
    where
        B: hyper::body::Body,
        B::Error: std::fmt::Debug,
    {
        let body = response.into_body().collect().await.unwrap().to_bytes();
        serde_json::from_slice(&body).unwrap()
    }
}
