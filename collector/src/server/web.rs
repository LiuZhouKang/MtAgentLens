// SPDX-License-Identifier: MIT
// Copyright (c) 2026 eunomia-bpf org.

use crate::responsibility::api_contracts::{
    ACCOUNTABILITY_TRACEBACK_AVAILABLE, ACCOUNTABILITY_TRACEBACK_UNAVAILABLE,
    AUTHORITY_AUTHORITATIVE, AUTHORITY_SUPPORTING, CORRELATION_EVIDENCE_AUTHORITY,
    CORRELATION_EVIDENCE_KIND,
};
use crate::responsibility::claims::accountability_evidence::accountability_evidence_shape_is_valid;
use crate::responsibility::evidence::{CorrelationEvidence, root_cause};
use crate::responsibility::graph::graph_simplifier;
use crate::responsibility::persistence::schema::open_db_read_only;
use crate::framework::analyzers::pipeline_contract::{PipelineStage, StageMetrics};
use crate::framework::core::Event;
use crate::server::api_types::{
    HealthResponse, HealthStatus, StageMetricsSnapshot, HEALTH_SCHEMA_VERSION,
};
use crate::server::assets::FrontendAssets;
use http_body_util::Full;
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper::{Method, Request, Response, StatusCode, body::Bytes};
use hyper_util::rt::TokioIo;
use rusqlite::Connection;
use rusqlite::OptionalExtension;
use rusqlite::types::ValueRef;
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

pub struct WebServer {
    assets: Arc<FrontendAssets>,
    event_sender: broadcast::Sender<Event>,
    log_file: Option<String>,
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
        let assets = FrontendAssets::new()?;
        Ok(Self {
            assets: Arc::new(assets),
            event_sender,
            log_file: log_file.map(|s| s.to_string()),
            health_state: None,
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
        let assets = FrontendAssets::new()?;
        Ok(Self {
            assets: Arc::new(assets),
            event_sender,
            log_file: log_file.map(|s| s.to_string()),
            health_state: Some(health_state),
        })
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
        log::info!(
            "📦 Embedded {} assets from frontend/dist:",
            all_assets.len()
        );
        for asset in all_assets.iter().take(10) {
            log::info!("   - {}", asset);
        }
        if all_assets.len() > 10 {
            log::info!("   ... and {} more", all_assets.len() - 10);
        }

        let health_state = self.health_state.clone();

        loop {
            let (stream, _) = listener
                .accept()
                .await
                .map_err(|e| Box::new(e) as Box<dyn std::error::Error + Send + Sync>)?;
            let assets = Arc::clone(&self.assets);
            let event_sender = self.event_sender.clone();
            let log_file = self.log_file.clone();
            let hs = health_state.clone();

            tokio::spawn(async move {
                let io = TokioIo::new(stream);
                let service = service_fn(move |req| {
                    handle_request(req, assets.clone(), event_sender.clone(), log_file.clone(), hs.clone())
                });

                if let Err(err) = http1::Builder::new().serve_connection(io, service).await {
                    log::error!("❌ Error serving connection: {:?}", err);
                }
            });
        }
    }
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
                Ok(None) => Ok(not_found_json(
                    serde_json::json!({ "code": "no_active_run" }),
                )),
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
                    RunResource::CorrelationEvidences => {
                        serve_correlation_evidence_api(&run_dir).await
                    }
                    RunResource::Events => serve_run_events_api(&run_dir).await,
                    RunResource::Diff => serve_run_diff_api(&run_dir).await,
                    RunResource::GraphAnalysis => serve_run_graph_analysis_api(&run_dir).await,
                    RunResource::Snapshots => serve_run_snapshots_api(&run_dir).await,
                    RunResource::AgentSnapshots => {
                        serve_run_agent_snapshots_api(&run_dir, run_route.extra.unwrap_or("")).await
                    }
                    RunResource::SnapshotById => {
                        serve_run_snapshot_by_id_api(&run_dir, run_route.extra.unwrap_or("")).await
                    }
                    RunResource::SnapshotDiff => serve_run_snapshot_diff_api(&run_dir).await,
                    RunResource::SnapshotChain => serve_run_snapshot_chain_api(&run_dir).await,
                    RunResource::AgentSnapshotChain => {
                        serve_run_agent_snapshot_chain_api(&run_dir, run_route.extra.unwrap_or(""))
                            .await
                    }
                    RunResource::SnapshotCrossDiff => {
                        let other_run_id = run_route.extra.unwrap_or("");
                        serve_run_snapshot_cross_diff_api(&run_dir, other_run_id).await
                    }
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

async fn serve_events_api(
    _event_sender: broadcast::Sender<Event>,
    log_file: Option<String>,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let legacy_warning = "299 AgentSight \"/api/events is a legacy, non-authoritative compatibility endpoint; prefer run-bound artifacts when available\"";

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
                    .header("Access-Control-Allow-Origin", "*")
                    .header("X-AgentSight-Legacy-Api", "true")
                    .header("X-AgentSight-Authority", "non-authoritative")
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
                    .header("Access-Control-Allow-Origin", "*")
                    .header("X-AgentSight-Legacy-Api", "true")
                    .header("X-AgentSight-Authority", "non-authoritative")
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
            "authority": "non-authoritative",
        });
        Ok(Response::builder()
            .status(StatusCode::NOT_FOUND)
            .header("Content-Type", "application/json")
            .header("Access-Control-Allow-Origin", "*")
            .header("X-AgentSight-Legacy-Api", "true")
            .header("X-AgentSight-Authority", "non-authoritative")
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
            "label": label,
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
        .header("Access-Control-Allow-Origin", "*")
        .body(Full::new(Bytes::from(response.to_string())))
        .unwrap())
}

#[derive(Clone, Copy)]
enum RunResource {
    Summary,
    Evaluation,
    Alerts,
    CorrelationEvidences,
    Events,
    Diff,
    GraphAnalysis,
    Snapshots,
    AgentSnapshots,
    SnapshotById,
    SnapshotDiff,
    SnapshotChain,
    AgentSnapshotChain,
    SnapshotCrossDiff,
}

struct RunRoute<'a> {
    run_id: &'a str,
    resource: RunResource,
    /// Extra path segment for resources that need it (e.g., snapshot_id for SnapshotById)
    extra: Option<&'a str>,
}

fn match_run_route(path: &str) -> Option<RunRoute<'_>> {
    let trimmed = path.trim_matches('/');
    let segments: Vec<&str> = trimmed.split('/').collect();
    match segments.as_slice() {
        ["api", "runs", run_id] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::Summary,
            extra: None,
        }),
        ["api", "runs", run_id, "evaluation"] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::Evaluation,
            extra: None,
        }),
        ["api", "runs", run_id, "alerts"] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::Alerts,
            extra: None,
        }),
        ["api", "runs", run_id, "correlation-evidence"] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::CorrelationEvidences,
            extra: None,
        }),
        ["api", "runs", run_id, "events"] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::Events,
            extra: None,
        }),
        ["api", "runs", run_id, "diff"] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::Diff,
            extra: None,
        }),
        ["api", "runs", run_id, "graph-analysis"] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::GraphAnalysis,
            extra: None,
        }),
        ["api", "runs", run_id, "snapshots"] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::Snapshots,
            extra: None,
        }),
        ["api", "runs", run_id, "agents", agent_id, "snapshots"]
            if !run_id.is_empty() && !agent_id.is_empty() =>
        {
            Some(RunRoute {
                run_id,
                resource: RunResource::AgentSnapshots,
                extra: Some(agent_id),
            })
        }
        ["api", "runs", run_id, "snapshots", snapshot_id]
            if !run_id.is_empty() && !snapshot_id.is_empty() =>
        {
            Some(RunRoute {
                run_id,
                resource: RunResource::SnapshotById,
                extra: Some(snapshot_id),
            })
        }
        ["api", "runs", run_id, "snapshot-diff"] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::SnapshotDiff,
            extra: None,
        }),
        ["api", "runs", run_id, "snapshot-chain"] if !run_id.is_empty() => Some(RunRoute {
            run_id,
            resource: RunResource::SnapshotChain,
            extra: None,
        }),
        ["api", "runs", run_id, "agents", agent_id, "snapshot-chain"]
            if !run_id.is_empty() && !agent_id.is_empty() =>
        {
            Some(RunRoute {
                run_id,
                resource: RunResource::AgentSnapshotChain,
                extra: Some(agent_id),
            })
        }
        ["api", "runs", run_id, "snapshot-cross-diff", other_run_id]
            if !run_id.is_empty() && !other_run_id.is_empty() =>
        {
            Some(RunRoute {
                run_id,
                resource: RunResource::SnapshotCrossDiff,
                extra: Some(other_run_id),
            })
        }
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

    let alerts_db_path = resolve_alerts_db_path(run_dir);
    let causal_db_path = resolve_correlation_evidence_db_path(run_dir);
    if let Some(path) = causal_db_path.as_ref() {
        match load_accountability_audit_summary(path, run_id) {
            Ok(_) => {}
            Err(AccountabilityAuditSummaryError::InvalidRow { run_id }) => {
                return Ok(structured_error_json(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "invalid_accountability_summary",
                    "failed to decode accountability_audit_summaries row for replay".to_string(),
                    serde_json::json!({ "run_id": run_id }),
                ));
            }
            Err(AccountabilityAuditSummaryError::Sqlite(err)) => {
                return Ok(internal_error_json(format!(
                    "failed to read alerts DB: {err}"
                )));
            }
        }
    }

    Ok(json_response(run_summary_value(run_dir, run_id, &summary)))
}

fn load_correlation_evidence_keys_for_summary(
    db_path: &Path,
) -> Result<std::collections::BTreeSet<String>, CorrelationEvidencesReplayError> {
    let rows = load_correlation_evidence_rows(db_path)?;
    Ok(rows
        .into_iter()
        .filter_map(|row| {
            let evidence_id = row.get("evidence_id")?.as_str()?;
            let intent_id = row.get("intent_id")?.as_str()?;
            let candidate_id = row.get("candidate_id")?.as_str()?;
            let match_level = row.get("match_level")?.as_str()?;
            Some(format!(
                "{evidence_id}|{intent_id}|{candidate_id}|{match_level}"
            ))
        })
        .collect())
}

fn has_causal_artifact_divergence(persisted_path: &Path, replayed_path: &Path) -> bool {
    let persisted = load_correlation_evidence_keys_for_summary(persisted_path);
    let replayed = load_correlation_evidence_keys_for_summary(replayed_path);
    match (persisted, replayed) {
        (Ok(left), Ok(right)) => left != right,
        _ => true,
    }
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

    let replayed_causal_path = run_dir.join("trace.log.replayed.causal.db");
    let replayed_causal_exists = replayed_causal_path.exists();
    let persisted_causal_path = run_dir.join("trace.log.causal.db");
    let persisted_causal_exists = persisted_causal_path.exists();
    let alerts_db_path = resolve_alerts_db_path(run_dir);
    let correlation_evidence_db_path = resolve_correlation_evidence_db_path(run_dir);
    let correlation_evidence_available = correlation_evidence_db_path.as_ref().is_some_and(|p| {
        rusqlite::Connection::open(p)
            .ok()
            .and_then(|c| {
                c.query_row("SELECT COUNT(*) FROM correlation_evidence", [], |r| {
                    r.get::<_, i64>(0)
                })
                .ok()
            })
            .is_some_and(|_| true)
    });

    let warnings = if replayed_causal_exists
        && persisted_causal_exists
        && has_causal_artifact_divergence(&persisted_causal_path, &replayed_causal_path)
    {
        vec![serde_json::json!({
            "code": "stale_causal_artifact_divergence",
            "severity": "warning",
            "message": "Persisted causal artifact differs from authoritative replay.",
        })]
    } else {
        Vec::new()
    };

    let mut payload = serde_json::json!({
        "run_id": run_id,
        "scenario_ids": scenario_ids,
        "run_status": run_status,
        "artifact_availability": {
            "evaluation": run_dir.join("evaluation.json").exists(),
            "alerts": alerts_db_path.is_some(),
            "correlation_evidence": correlation_evidence_available,
            "events": resolve_events_artifact_path(run_dir).is_some(),
        },
        "artifact_sources": {
            "correlation_evidence": if !correlation_evidence_available {
                "missing"
            } else if replayed_causal_exists {
                "replayed"
            } else {
                "persisted"
            }
        },
        "warnings": warnings,
    });

    if let Some(causal_db_path) = correlation_evidence_db_path
        && let Ok(Some(accountability_summary)) =
            load_accountability_audit_summary(&causal_db_path, run_id)
        && let Ok(value) = serde_json::to_value(accountability_summary)
        && let Some(object) = payload.as_object_mut()
    {
        object.insert("accountability_summary".to_string(), value);
    }

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
                "message": message,
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

async fn serve_run_graph_analysis_api(
    run_dir: &Path,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let Some(db_path) = resolve_correlation_evidence_db_path(run_dir) else {
        return Ok(not_found_json(json_error(
            "artifact_unavailable",
            run_dir.file_name().and_then(|n| n.to_str()).unwrap_or("?"),
            "correlation_evidence",
        )));
    };
    let rows = match load_correlation_evidence_rows(&db_path) {
        Ok(rows) => rows,
        Err(e) => {
            return Ok(internal_error_json(format!(
                "failed to read causal links: {e:?}"
            )));
        }
    };
    let causal_links: Vec<CorrelationEvidence> = rows
        .iter()
        .filter_map(|row| serde_json::from_value(row.clone()).ok())
        .collect();
    let simplified = graph_simplifier::simplify(&causal_links);
    let legacy_correlation_ranking = root_cause::rank_legacy_correlation_evidence(&causal_links, 3);
    let violation_patterns = match load_violation_pattern_rows(&db_path) {
        Ok(rows) if !rows.is_empty() => rows,
        _ => {
            let mut pattern_matcher =
                crate::responsibility::graph::graph_pattern_matcher::PatternMatcher::new(
                    crate::responsibility::graph::graph_pattern_matcher::default_templates(),
                );
            let mut rows = Vec::new();
            for link in &causal_links {
                for matched in pattern_matcher.on_link(link.clone()) {
                    rows.push(serde_json::json!({
                        "template_id": matched.template_id,
                        "agent_id": matched.agent_id,
                        "matched_evidence_ids": matched.matched_evidence_ids,
                        "matched_policies": matched.matched_policies,
                        "matched_boundaries": matched.matched_boundaries,
                        "matched_targets": matched.matched_targets,
                        "severity": matched.severity,
                    }));
                }
            }
            rows
        }
    };
    let snapshot = load_latest_accountability_snapshot(&db_path);
    let authoritative_source = if snapshot.is_some() {
        "accountability_snapshot"
    } else {
        "legacy_links"
    };

    let mut response = serde_json::json!({
        "authoritative_source": authoritative_source,
        "evidence_kind": CORRELATION_EVIDENCE_KIND,
        "evidence_authority": CORRELATION_EVIDENCE_AUTHORITY,
        "snapshot": snapshot,
        "graph_summary": {
            "total_links": simplified.total_links,
            "compressed_ratio": simplified.compressed_ratio,
            "summary": simplified.summary,
            "nodes": simplified.nodes,
        },
        "violation_patterns": violation_patterns,
    });

    if authoritative_source == "accountability_snapshot" {
        response["authority"] = serde_json::json!(AUTHORITY_AUTHORITATIVE);
        response["accountability_traceback_status"] =
            serde_json::json!(ACCOUNTABILITY_TRACEBACK_AVAILABLE);
        response["legacy_correlation_ranking"] = serde_json::json!(legacy_correlation_ranking);
        response["warnings"] = serde_json::json!([]);
    } else {
        response["authority"] = serde_json::json!(AUTHORITY_SUPPORTING);
        response["accountability_traceback_status"] =
            serde_json::json!(ACCOUNTABILITY_TRACEBACK_UNAVAILABLE);
        response["legacy_correlation_ranking"] = serde_json::json!(legacy_correlation_ranking);
        response["warnings"] = serde_json::json!([
            "legacy causal-link analysis is supporting evidence, not authoritative accountability traceback"
        ]);
    }

    Ok(json_response(response))
}

fn snapshot_field_diff(
    before_val: Option<&serde_json::Value>,
    after_val: Option<&serde_json::Value>,
) -> serde_json::Value {
    let before_ids: Vec<String> = before_val
        .and_then(|v| serde_json::from_value(v.clone()).ok())
        .unwrap_or_default();
    let after_ids: Vec<String> = after_val
        .and_then(|v| serde_json::from_value(v.clone()).ok())
        .unwrap_or_default();
    let before_set: std::collections::BTreeSet<String> = before_ids.into_iter().collect();
    let after_set: std::collections::BTreeSet<String> = after_ids.into_iter().collect();
    let added: Vec<String> = after_set.difference(&before_set).cloned().collect();
    let removed: Vec<String> = before_set.difference(&after_set).cloned().collect();
    serde_json::json!({
        "added": added,
        "removed": removed,
        "added_count": added.len(),
        "removed_count": removed.len(),
    })
}

fn compute_snapshot_diff(
    before: &serde_json::Value,
    after: &serde_json::Value,
) -> serde_json::Value {
    let fields = [
        "evidence_ids",
        "evidence_ids",
        "policy_ids",
        "boundary_ids",
        "claim_states",
        "traceback_ids",
        "violation_match_ids",
    ];
    let mut field_diffs = serde_json::Map::new();
    for field in &fields {
        let diff = snapshot_field_diff(before.get(field), after.get(field));
        field_diffs.insert(field.to_string(), diff);
    }

    serde_json::json!({
        "field_diffs": field_diffs,
        "before_timestamp_ns": before.get("timestamp_ns"),
        "after_timestamp_ns": after.get("timestamp_ns"),
        "before_snapshot_id": before.get("snapshot_id"),
        "after_snapshot_id": after.get("snapshot_id"),
    })
}

async fn serve_run_snapshots_api(
    run_dir: &Path,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let Some(db_path) = resolve_correlation_evidence_db_path(run_dir) else {
        return Ok(not_found_json(json_error(
            "artifact_unavailable",
            run_dir.file_name().and_then(|n| n.to_str()).unwrap_or("?"),
            "correlation_evidence",
        )));
    };
    let db_path_str = db_path.to_string_lossy();
    let conn = match open_db_read_only(&db_path_str) {
        Ok(c) => c,
        Err(_) => {
            return Ok(internal_error_json(
                "failed to open DB read-only".to_string(),
            ));
        }
    };

    let has_snapshots_table: bool = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='causal_graph_snapshots'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count > 0)
        .unwrap_or(false);

    if !has_snapshots_table {
        return Ok(json_response(serde_json::json!({
            "snapshots": [],
            "total_count": 0,
        })));
    }

    let mut stmt = match conn.prepare(
        "SELECT snapshot_id, timestamp_ns, agent_id, evidence_ids_json, evidence_ids_json,
                policy_ids_json, boundary_ids_json, claim_states_json, traceback_ids_json,
                violation_match_ids_json, node_count, edge_count, snapshot_kind
         FROM causal_graph_snapshots
         ORDER BY timestamp_ns ASC, snapshot_id ASC",
    ) {
        Ok(s) => s,
        Err(e) => return Ok(internal_error_json(format!("query prepare: {e}"))),
    };

    let rows: Vec<serde_json::Value> = stmt
        .query_map([], |row| {
            let snapshot_id: String = row.get(0)?;
            let timestamp_ns: i64 = row.get(1)?;
            let agent_id: String = row.get(2)?;
            let evidence_ids_json: String = row.get(3)?;
            let evidence_ids_json: String = row.get(4)?;
            let policy_ids_json: String = row.get(5)?;
            let boundary_ids_json: String = row.get(6)?;
            let claim_states_json: String = row.get(7)?;
            let traceback_ids_json: String = row.get(8)?;
            let violation_match_ids_json: String = row.get(9)?;
            let node_count: i64 = row.get(10)?;
            let edge_count: i64 = row.get(11)?;
            let snapshot_kind: String = row.get(12)?;
            Ok(serde_json::json!({
                "snapshot_id": snapshot_id,
                "timestamp_ns": timestamp_ns,
                "agent_id": agent_id,
                "evidence_ids": serde_json::from_str::<Vec<String>>(&evidence_ids_json)
                    .unwrap_or_default(),
                "evidence_ids": serde_json::from_str::<Vec<String>>(&evidence_ids_json)
                    .unwrap_or_default(),
                "policy_ids": serde_json::from_str::<Vec<String>>(&policy_ids_json)
                    .unwrap_or_default(),
                "boundary_ids": serde_json::from_str::<Vec<String>>(&boundary_ids_json)
                    .unwrap_or_default(),
                "claim_states": serde_json::from_str::<Vec<String>>(&claim_states_json)
                    .unwrap_or_default(),
                "traceback_ids": serde_json::from_str::<Vec<String>>(&traceback_ids_json)
                    .unwrap_or_default(),
                "violation_match_ids": serde_json::from_str::<Vec<String>>(&violation_match_ids_json)
                    .unwrap_or_default(),
                "node_count": node_count,
                "edge_count": edge_count,
                "snapshot_kind": snapshot_kind,
            }))
        })
        .unwrap()
        .filter_map(Result::ok)
        .collect();

    Ok(json_response(serde_json::json!({
        "snapshots": rows,
        "total_count": rows.len(),
    })))
}

async fn serve_run_snapshot_by_id_api(
    run_dir: &Path,
    snapshot_id: &str,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let Some(db_path) = resolve_correlation_evidence_db_path(run_dir) else {
        return Ok(not_found_json(json_error(
            "artifact_unavailable",
            run_dir.file_name().and_then(|n| n.to_str()).unwrap_or("?"),
            "correlation_evidence",
        )));
    };
    let db_path_str = db_path.to_string_lossy();
    let conn = match open_db_read_only(&db_path_str) {
        Ok(c) => c,
        Err(_) => {
            return Ok(internal_error_json(
                "failed to open DB read-only".to_string(),
            ));
        }
    };

    let has_snapshots_table: bool = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='causal_graph_snapshots'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count > 0)
        .unwrap_or(false);

    if !has_snapshots_table {
        return Ok(not_found_json(json_error(
            "snapshots_unavailable",
            snapshot_id,
            "causal_graph_snapshots",
        )));
    }

    let snapshot_result = conn
        .query_row(
            "SELECT snapshot_id, timestamp_ns, agent_id, evidence_ids_json, evidence_ids_json,
                    policy_ids_json, boundary_ids_json, claim_states_json, traceback_ids_json,
                    violation_match_ids_json, node_count, edge_count, snapshot_kind
             FROM causal_graph_snapshots WHERE snapshot_id = ?1",
            [snapshot_id],
            |row| {
                let snapshot_id: String = row.get(0)?;
                let timestamp_ns: i64 = row.get(1)?;
                let agent_id: String = row.get(2)?;
                let evidence_ids: Vec<String> =
                    serde_json::from_str(&row.get::<_, String>(3)?).unwrap_or_default();
                let evidence_ids: Vec<String> =
                    serde_json::from_str(&row.get::<_, String>(4)?).unwrap_or_default();
                let policy_ids: Vec<String> =
                    serde_json::from_str(&row.get::<_, String>(5)?).unwrap_or_default();
                let boundary_ids: Vec<String> =
                    serde_json::from_str(&row.get::<_, String>(6)?).unwrap_or_default();
                let claim_states: Vec<String> =
                    serde_json::from_str(&row.get::<_, String>(7)?).unwrap_or_default();
                let traceback_ids: Vec<String> =
                    serde_json::from_str(&row.get::<_, String>(8)?).unwrap_or_default();
                let violation_match_ids: Vec<String> =
                    serde_json::from_str(&row.get::<_, String>(9)?).unwrap_or_default();
                let node_count: i64 = row.get(10)?;
                let edge_count: i64 = row.get(11)?;
                let snapshot_kind: String = row.get(12)?;
                Ok(serde_json::json!({
                    "snapshot_id": snapshot_id,
                    "timestamp_ns": timestamp_ns,
                    "agent_id": agent_id,
                    "evidence_ids": evidence_ids,
                    "evidence_ids": evidence_ids,
                    "policy_ids": policy_ids,
                    "boundary_ids": boundary_ids,
                    "claim_states": claim_states,
                    "traceback_ids": traceback_ids,
                    "violation_match_ids": violation_match_ids,
                    "node_count": node_count,
                    "edge_count": edge_count,
                    "snapshot_kind": snapshot_kind,
                }))
            },
        )
        .optional();

    match snapshot_result {
        Ok(Some(snapshot)) => Ok(json_response(snapshot)),
        Ok(None) => Ok(not_found_json(json_error(
            "snapshot_not_found",
            snapshot_id,
            "causal_graph_snapshots",
        ))),
        Err(_) => Ok(internal_error_json("failed to query snapshot".to_string())),
    }
}

async fn serve_run_snapshot_diff_api(
    run_dir: &Path,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let Some(db_path) = resolve_correlation_evidence_db_path(run_dir) else {
        return Ok(not_found_json(json_error(
            "artifact_unavailable",
            run_dir.file_name().and_then(|n| n.to_str()).unwrap_or("?"),
            "correlation_evidence",
        )));
    };
    let db_path_str = db_path.to_string_lossy();
    let conn = match open_db_read_only(&db_path_str) {
        Ok(c) => c,
        Err(_) => {
            return Ok(internal_error_json(
                "failed to open DB read-only".to_string(),
            ));
        }
    };

    let has_snapshots_table: bool = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='causal_graph_snapshots'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count > 0)
        .unwrap_or(false);

    if !has_snapshots_table {
        return Ok(not_found_json(json_error(
            "snapshots_unavailable",
            run_dir.file_name().and_then(|n| n.to_str()).unwrap_or("?"),
            "causal_graph_snapshots",
        )));
    }

    // Get the last two snapshots for automatic diff (most recent two by time)
    let snapshots: Vec<serde_json::Value> = conn
        .prepare(
            "SELECT snapshot_id, timestamp_ns, agent_id, evidence_ids_json, evidence_ids_json,
                    policy_ids_json, boundary_ids_json, claim_states_json, traceback_ids_json,
                    violation_match_ids_json, node_count, edge_count, snapshot_kind
             FROM causal_graph_snapshots
             ORDER BY timestamp_ns DESC LIMIT 2",
        )
        .unwrap()
        .query_map([], |row| {
            Ok(serde_json::json!({
                "snapshot_id": row.get::<_, String>(0)?,
                "timestamp_ns": row.get::<_, i64>(1)?,
                "agent_id": row.get::<_, String>(2)?,
                "evidence_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(3)?)
                    .unwrap_or_default(),
                "evidence_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(4)?)
                    .unwrap_or_default(),
                "policy_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(5)?)
                    .unwrap_or_default(),
                "boundary_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(6)?)
                    .unwrap_or_default(),
                "traceback_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(8)?)
                    .unwrap_or_default(),
                "violation_match_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(9)?)
                    .unwrap_or_default(),
                "node_count": row.get::<_, i64>(10)?,
                "edge_count": row.get::<_, i64>(11)?,
                "snapshot_kind": row.get::<_, String>(12)?,
            }))
        })
        .unwrap()
        .filter_map(Result::ok)
        .collect();

    if snapshots.len() < 2 {
        return Ok(json_response(serde_json::json!({
            "diff_available": false,
            "reason": if snapshots.is_empty() { "no snapshots found" } else { "only one snapshot, need at least 2 for diff" },
            "snapshot_count": snapshots.len(),
        })));
    }

    // snapshots[0] is the latest (DESC), snapshots[1] is the previous
    let full_diff = compute_snapshot_diff(&snapshots[1], &snapshots[0]);

    Ok(json_response(serde_json::json!({
        "diff_available": true,
        "before": {
            "snapshot_id": snapshots[1]["snapshot_id"],
            "timestamp_ns": snapshots[1]["timestamp_ns"],
            "snapshot_kind": snapshots[1]["snapshot_kind"],
        },
        "after": {
            "snapshot_id": snapshots[0]["snapshot_id"],
            "timestamp_ns": snapshots[0]["timestamp_ns"],
            "snapshot_kind": snapshots[0]["snapshot_kind"],
        },
        "field_diffs": full_diff["field_diffs"],
        "before_timestamp_ns": full_diff["before_timestamp_ns"],
        "after_timestamp_ns": full_diff["after_timestamp_ns"],
    })))
}

fn compute_snapshot_chain_hash(snapshot_data: &str, prev_hash: u64) -> u64 {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    snapshot_data.hash(&mut hasher);
    prev_hash.hash(&mut hasher);
    hasher.finish()
}

async fn serve_run_snapshot_chain_api(
    run_dir: &Path,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let Some(db_path) = resolve_correlation_evidence_db_path(run_dir) else {
        return Ok(not_found_json(json_error(
            "artifact_unavailable",
            run_dir.file_name().and_then(|n| n.to_str()).unwrap_or("?"),
            "correlation_evidence",
        )));
    };
    let db_path_str = db_path.to_string_lossy();
    let conn = match open_db_read_only(&db_path_str) {
        Ok(c) => c,
        Err(_) => {
            return Ok(internal_error_json(
                "failed to open DB read-only".to_string(),
            ));
        }
    };

    let has_snapshots_table: bool = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='causal_graph_snapshots'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count > 0)
        .unwrap_or(false);

    if !has_snapshots_table {
        return Ok(not_found_json(json_error(
            "snapshots_unavailable",
            run_dir.file_name().and_then(|n| n.to_str()).unwrap_or("?"),
            "causal_graph_snapshots",
        )));
    }

    let mut stmt = match conn.prepare(
        "SELECT snapshot_id, timestamp_ns, agent_id, evidence_ids_json, evidence_ids_json,
                policy_ids_json, boundary_ids_json, claim_states_json, traceback_ids_json,
                violation_match_ids_json, node_count, edge_count, snapshot_kind
         FROM causal_graph_snapshots
         ORDER BY timestamp_ns ASC, snapshot_id ASC",
    ) {
        Ok(s) => s,
        Err(e) => return Ok(internal_error_json(format!("query prepare: {e}"))),
    };

    let rows: Vec<serde_json::Value> = stmt
        .query_map([], |row| {
            Ok(serde_json::json!({
                "snapshot_id": row.get::<_, String>(0)?,
                "timestamp_ns": row.get::<_, i64>(1)?,
                "agent_id": row.get::<_, String>(2)?,
                "evidence_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(3)?).unwrap_or_default(),
                "evidence_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(4)?).unwrap_or_default(),
                "policy_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(5)?).unwrap_or_default(),
                "boundary_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(6)?).unwrap_or_default(),
                "claim_states": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(7)?).unwrap_or_default(),
                "traceback_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(8)?).unwrap_or_default(),
                "violation_match_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(9)?).unwrap_or_default(),
                "node_count": row.get::<_, i64>(10)?,
                "edge_count": row.get::<_, i64>(11)?,
                "snapshot_kind": row.get::<_, String>(12)?,
            }))
        })
        .unwrap()
        .filter_map(Result::ok)
        .collect();

    // Build hash chain: each snapshot's hash includes the previous hash
    let genesis_hash: u64 = 0;
    let mut chain_entries: Vec<serde_json::Value> = Vec::with_capacity(rows.len());
    let mut prev_hash = genesis_hash;

    for snapshot in &rows {
        let snapshot_data = serde_json::to_string(snapshot).unwrap_or_default();
        let current_hash = compute_snapshot_chain_hash(&snapshot_data, prev_hash);

        chain_entries.push(serde_json::json!({
            "snapshot_id": snapshot["snapshot_id"],
            "timestamp_ns": snapshot["timestamp_ns"],
            "agent_id": snapshot["agent_id"],
            "snapshot_kind": snapshot["snapshot_kind"],
            "prev_hash_hex": hex::encode(prev_hash.to_le_bytes()),
            "hash_hex": hex::encode(current_hash.to_le_bytes()),
            "chain_length": chain_entries.len() + 1,
        }));

        prev_hash = current_hash;
    }

    Ok(json_response(serde_json::json!({
        "chain_entries": chain_entries,
        "chain_length": chain_entries.len(),
        "root_hash_hex": hex::encode(prev_hash.to_le_bytes()),
        "algorithm": "SipHash-1-3",
        "integrity": if chain_entries.is_empty() { "empty" } else { "ok" },
    })))
}

fn load_latest_snapshot_from_run(run_dir: &Path) -> Option<serde_json::Value> {
    let db_path = resolve_correlation_evidence_db_path(run_dir)?;
    let db_path_str = db_path.to_string_lossy();
    let conn = open_db_read_only(&db_path_str).ok()?;
    let has_table: bool = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='causal_graph_snapshots'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count > 0)
        .unwrap_or(false);
    if !has_table {
        return None;
    }
    conn.query_row(
        "SELECT snapshot_id, timestamp_ns, agent_id, evidence_ids_json, evidence_ids_json,
                policy_ids_json, boundary_ids_json, claim_states_json, traceback_ids_json,
                violation_match_ids_json, node_count, edge_count, snapshot_kind
         FROM causal_graph_snapshots
         ORDER BY timestamp_ns DESC LIMIT 1",
        [],
        |row| {
            Ok(serde_json::json!({
                "snapshot_id": row.get::<_, String>(0)?,
                "timestamp_ns": row.get::<_, i64>(1)?,
                "agent_id": row.get::<_, String>(2)?,
                "evidence_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(3)?)
                    .unwrap_or_default(),
                "evidence_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(4)?)
                    .unwrap_or_default(),
                "policy_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(5)?)
                    .unwrap_or_default(),
                "boundary_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(6)?)
                    .unwrap_or_default(),
                "claim_states": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(7)?)
                    .unwrap_or_default(),
                "traceback_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(8)?)
                    .unwrap_or_default(),
                "violation_match_ids": serde_json::from_str::<Vec<String>>(&row.get::<_, String>(9)?)
                    .unwrap_or_default(),
                "snapshot_kind": row.get::<_, String>(12)?,
            }))
        },
    )
    .ok()
}

async fn serve_run_snapshot_cross_diff_api(
    run_dir: &Path,
    other_run_id: &str,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    // Resolve the other run directory relative to the current run's parent
    let other_run_dir = run_dir.parent().map(|p| p.join(other_run_id));
    let other_run_dir = match other_run_dir {
        Some(dir) if dir.is_dir() => dir,
        _ => {
            return Ok(not_found_json(json_error(
                "run_not_found",
                other_run_id,
                "run",
            )));
        }
    };

    let primary_snapshot = load_latest_snapshot_from_run(run_dir);
    let other_snapshot = load_latest_snapshot_from_run(&other_run_dir);

    match (primary_snapshot, other_snapshot) {
        (Some(ref primary), Some(ref other)) => {
            let diff = compute_snapshot_diff(primary, other);
            Ok(json_response(serde_json::json!({
                "diff_available": true,
                "primary_run": run_dir.file_name().and_then(|n| n.to_str()),
                "other_run": other_run_id,
                "primary_snapshot": {
                    "snapshot_id": primary["snapshot_id"],
                    "timestamp_ns": primary["timestamp_ns"],
                    "agent_id": primary["agent_id"],
                },
                "other_snapshot": {
                    "snapshot_id": other["snapshot_id"],
                    "timestamp_ns": other["timestamp_ns"],
                    "agent_id": other["agent_id"],
                },
                "field_diffs": diff["field_diffs"],
            })))
        }
        (None, Some(_)) => Ok(json_response(serde_json::json!({
            "diff_available": false,
            "reason": "primary run has no snapshots",
        }))),
        (Some(_), None) => Ok(json_response(serde_json::json!({
            "diff_available": false,
            "reason": "other run has no snapshots",
        }))),
        (None, None) => Ok(json_response(serde_json::json!({
            "diff_available": false,
            "reason": "neither run has snapshots",
        }))),
    }
}

fn sqlite_row_to_snapshot_json(row: &rusqlite::Row) -> rusqlite::Result<serde_json::Value> {
    let snapshot_id: String = row.get(0)?;
    let timestamp_ns: i64 = row.get(1)?;
    let agent_id: String = row.get(2)?;
    let evidence_ids_json: String = row.get(3)?;
    let evidence_ids_json: String = row.get(4)?;
    let policy_ids_json: String = row.get(5)?;
    let boundary_ids_json: String = row.get(6)?;
    let claim_states_json: String = row.get(7)?;
    let traceback_ids_json: String = row.get(8)?;
    let violation_match_ids_json: String = row.get(9)?;
    let node_count: i64 = row.get(10)?;
    let edge_count: i64 = row.get(11)?;
    let snapshot_kind: String = row.get(12)?;
    Ok(serde_json::json!({
        "snapshot_id": snapshot_id,
        "timestamp_ns": timestamp_ns,
        "agent_id": agent_id,
        "evidence_ids": serde_json::from_str::<Vec<String>>(&evidence_ids_json).unwrap_or_default(),
        "evidence_ids": serde_json::from_str::<Vec<String>>(&evidence_ids_json).unwrap_or_default(),
        "policy_ids": serde_json::from_str::<Vec<String>>(&policy_ids_json).unwrap_or_default(),
        "boundary_ids": serde_json::from_str::<Vec<String>>(&boundary_ids_json).unwrap_or_default(),
        "claim_states": serde_json::from_str::<Vec<String>>(&claim_states_json).unwrap_or_default(),
        "traceback_ids": serde_json::from_str::<Vec<String>>(&traceback_ids_json).unwrap_or_default(),
        "violation_match_ids": serde_json::from_str::<Vec<String>>(&violation_match_ids_json).unwrap_or_default(),
        "node_count": node_count,
        "edge_count": edge_count,
        "snapshot_kind": snapshot_kind,
    }))
}

fn build_snapshot_chain_response(
    rows: Vec<serde_json::Value>,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    if rows.is_empty() {
        return Ok(json_response(serde_json::json!({
            "chain_entries": [],
            "chain_length": 0,
            "integrity": "empty",
            "algorithm": "SipHash-1-3",
        })));
    }
    let genesis_hash: u64 = 0;
    let mut chain_entries: Vec<serde_json::Value> = Vec::with_capacity(rows.len());
    let mut prev_hash = genesis_hash;

    for snapshot in &rows {
        let snapshot_data = serde_json::to_string(snapshot).unwrap_or_default();
        let current_hash = compute_snapshot_chain_hash(&snapshot_data, prev_hash);
        chain_entries.push(serde_json::json!({
            "snapshot_id": snapshot["snapshot_id"],
            "timestamp_ns": snapshot["timestamp_ns"],
            "agent_id": snapshot["agent_id"],
            "snapshot_kind": snapshot["snapshot_kind"],
            "prev_hash_hex": hex::encode(prev_hash.to_le_bytes()),
            "hash_hex": hex::encode(current_hash.to_le_bytes()),
            "chain_length": chain_entries.len() + 1,
        }));
        prev_hash = current_hash;
    }

    Ok(json_response(serde_json::json!({
        "chain_entries": chain_entries,
        "chain_length": chain_entries.len(),
        "root_hash_hex": hex::encode(prev_hash.to_le_bytes()),
        "algorithm": "SipHash-1-3",
        "integrity": "ok",
    })))
}

async fn serve_run_agent_snapshots_api(
    run_dir: &Path,
    agent_id: &str,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let Some(db_path) = resolve_correlation_evidence_db_path(run_dir) else {
        return Ok(not_found_json(json_error(
            "artifact_unavailable",
            run_dir.file_name().and_then(|n| n.to_str()).unwrap_or("?"),
            "correlation_evidence",
        )));
    };
    let db_path_str = db_path.to_string_lossy();
    let conn = match open_db_read_only(&db_path_str) {
        Ok(c) => c,
        Err(_) => return Ok(internal_error_json("failed to open DB".to_string())),
    };

    let has_snapshots_table: bool = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='causal_graph_snapshots'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count > 0)
        .unwrap_or(false);
    if !has_snapshots_table {
        return Ok(json_response(
            serde_json::json!({"snapshots": [], "total_count": 0}),
        ));
    }

    let mut stmt = match conn.prepare(
        "SELECT snapshot_id, timestamp_ns, agent_id, evidence_ids_json, evidence_ids_json,
                policy_ids_json, boundary_ids_json, claim_states_json, traceback_ids_json,
                violation_match_ids_json, node_count, edge_count, snapshot_kind
         FROM causal_graph_snapshots WHERE agent_id = ?1
         ORDER BY timestamp_ns ASC, snapshot_id ASC",
    ) {
        Ok(s) => s,
        Err(e) => return Ok(internal_error_json(format!("query prepare: {e}"))),
    };

    let rows: Vec<serde_json::Value> = stmt
        .query_map([agent_id], |row| sqlite_row_to_snapshot_json(row))
        .unwrap()
        .filter_map(Result::ok)
        .collect();

    Ok(json_response(serde_json::json!({
        "snapshots": rows,
        "total_count": rows.len(),
        "agent_id": agent_id,
    })))
}

async fn serve_run_agent_snapshot_chain_api(
    run_dir: &Path,
    agent_id: &str,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let Some(db_path) = resolve_correlation_evidence_db_path(run_dir) else {
        return Ok(not_found_json(json_error(
            "artifact_unavailable",
            run_dir.file_name().and_then(|n| n.to_str()).unwrap_or("?"),
            "correlation_evidence",
        )));
    };
    let db_path_str = db_path.to_string_lossy();
    let conn = match open_db_read_only(&db_path_str) {
        Ok(c) => c,
        Err(_) => return Ok(internal_error_json("failed to open DB".to_string())),
    };

    let has_snapshots_table: bool = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='causal_graph_snapshots'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count > 0)
        .unwrap_or(false);
    if !has_snapshots_table {
        return Ok(not_found_json(json_error(
            "snapshots_unavailable",
            run_dir.file_name().and_then(|n| n.to_str()).unwrap_or("?"),
            "causal_graph_snapshots",
        )));
    }

    let mut stmt = match conn.prepare(
        "SELECT snapshot_id, timestamp_ns, agent_id, evidence_ids_json, evidence_ids_json,
                policy_ids_json, boundary_ids_json, claim_states_json, traceback_ids_json,
                violation_match_ids_json, node_count, edge_count, snapshot_kind
         FROM causal_graph_snapshots WHERE agent_id = ?1
         ORDER BY timestamp_ns ASC, snapshot_id ASC",
    ) {
        Ok(s) => s,
        Err(e) => return Ok(internal_error_json(format!("query prepare: {e}"))),
    };

    let rows: Vec<serde_json::Value> = stmt
        .query_map([agent_id], |row| sqlite_row_to_snapshot_json(row))
        .unwrap()
        .filter_map(Result::ok)
        .collect();

    build_snapshot_chain_response(rows)
}

async fn serve_run_diff_api(
    run_dir: &Path,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    // Compare this run's causal links against the previous run in the same env/runs dir
    let Some(db_path) = resolve_correlation_evidence_db_path(run_dir) else {
        return Ok(not_found_json(json_error(
            "artifact_unavailable",
            run_dir.file_name().and_then(|n| n.to_str()).unwrap_or("?"),
            "correlation_evidence",
        )));
    };
    let current_links = match load_correlation_evidence_rows(&db_path) {
        Ok(rows) => rows,
        Err(e) => {
            return Ok(internal_error_json(format!(
                "failed to read causal links: {e:?}"
            )));
        }
    };
    // Find previous run dir (sorted, take the one just before this one)
    let prev_links: Vec<serde_json::Value> = if let Some(runs_dir) = run_dir.parent() {
        let mut siblings: Vec<_> = std::fs::read_dir(runs_dir)
            .ok()
            .into_iter()
            .flatten()
            .filter_map(|e| e.ok())
            .filter(|e| e.path().is_dir())
            .map(|e| e.path())
            .collect();
        siblings.sort();
        let pos = siblings.iter().position(|p| p == run_dir);
        if let Some(i) = pos {
            if i > 0 {
                let prev = &siblings[i - 1];
                if let Some(prev_db) = resolve_correlation_evidence_db_path(prev) {
                    load_correlation_evidence_rows(&prev_db).unwrap_or_default()
                } else {
                    vec![]
                }
            } else {
                vec![]
            }
        } else {
            vec![]
        }
    } else {
        vec![]
    };

    let diff = crate::responsibility::replay::run_diff::diff_runs(&prev_links, &current_links);
    Ok(json_response(serde_json::json!({
        "diff": diff,
        "prev_link_count": prev_links.len(),
        "current_link_count": current_links.len(),
    })))
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
                        "artifact": events_path.file_name().and_then(|name| name.to_str()).unwrap_or("trace.log"),
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
                    "artifact": events_path.file_name().and_then(|name| name.to_str()).unwrap_or("trace.log"),
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

#[derive(Debug, Clone)]
struct EvidenceRowSummary {
    evidence_id: String,
    directness: String,
    boundary_id: Option<String>,
    payload_ref: Option<String>,
}

#[derive(Debug, Clone)]
struct PolicyEvaluationSummary {
    evidence_id: String,
    policy_id: Option<String>,
    boundary_id: Option<String>,
    claim_state: String,
    reason: String,
}

#[derive(Debug, Clone, serde::Serialize)]
struct AccountabilityTracebackReplaySummary {
    traceback_id: String,
    alert_id: String,
    chain_node_ids: Vec<String>,
    root_cause_node_id: String,
    supporting_evidence_ids: Vec<String>,
    explanation_summary: String,
}

#[derive(Debug, Clone, serde::Serialize)]
struct AccountabilityAuditSummaryPayload {
    run_id: String,
    alert_count: u64,
    traceback_count: u64,
    summary_text: String,
}

fn load_evidence_record_summaries(
    db_path: &Path,
) -> rusqlite::Result<std::collections::HashMap<String, EvidenceRowSummary>> {
    let conn = Connection::open(db_path)?;
    let has_table = table_column_exists(&conn, "evidence_records", "evidence_id").unwrap_or(false);
    if !has_table {
        return Ok(std::collections::HashMap::new());
    }
    let mut stmt = conn.prepare(
        "SELECT evidence_id, directness, boundary_id, payload_ref FROM evidence_records",
    )?;
    let rows = stmt.query_map([], |row| {
        let summary = EvidenceRowSummary {
            evidence_id: row.get(0)?,
            directness: row.get(1)?,
            boundary_id: row.get(2)?,
            payload_ref: row.get(3)?,
        };
        Ok((summary.evidence_id.clone(), summary))
    })?;
    rows.collect()
}

fn load_policy_evaluation_summaries(
    db_path: &Path,
) -> rusqlite::Result<std::collections::HashMap<String, PolicyEvaluationSummary>> {
    let conn = Connection::open(db_path)?;
    let has_table =
        table_column_exists(&conn, "policy_evaluations", "evidence_id").unwrap_or(false);
    if !has_table {
        return Ok(std::collections::HashMap::new());
    }
    let mut stmt = conn.prepare(
        "SELECT evidence_id, policy_id, boundary_id, claim_state, reason FROM policy_evaluations",
    )?;
    let rows = stmt.query_map([], |row| {
        let summary = PolicyEvaluationSummary {
            evidence_id: row.get(0)?,
            policy_id: row.get(1)?,
            boundary_id: row.get(2)?,
            claim_state: row.get(3)?,
            reason: row.get(4)?,
        };
        Ok((summary.evidence_id.clone(), summary))
    })?;
    rows.collect()
}

fn load_accountability_traceback_replay_summaries(
    db_path: &Path,
) -> Result<
    std::collections::HashMap<String, AccountabilityTracebackReplaySummary>,
    AccountabilityTracebackReplayError,
> {
    let conn = Connection::open(db_path)?;
    load_accountability_traceback_replay_summaries_from_conn(&conn)
}

fn load_accountability_traceback_replay_summaries_from_conn(
    conn: &Connection,
) -> Result<
    std::collections::HashMap<String, AccountabilityTracebackReplaySummary>,
    AccountabilityTracebackReplayError,
> {
    let has_table = table_column_exists(conn, "accountability_traceback_replays", "traceback_id")
        .unwrap_or(false);
    if !has_table {
        return Ok(std::collections::HashMap::new());
    }
    let mut stmt = conn.prepare(
        "SELECT traceback_id, alert_id, chain_node_ids_json, root_cause_node_id, supporting_evidence_ids_json, explanation_summary \
         FROM accountability_traceback_replays",
    )?;
    let rows = stmt.query_map([], |row| {
        let traceback_id: String = row.get(0)?;
        let alert_id: String = row.get(1)?;
        let chain_node_ids_json: String = row.get(2)?;
        let supporting_evidence_ids_json: String = row.get(4)?;
        let chain_node_ids_value: Value =
            serde_json::from_str(&chain_node_ids_json).map_err(|_| {
                rusqlite::Error::ToSqlConversionFailure(Box::new(
                    AccountabilityTracebackReplayError::InvalidJson {
                        alert_id: alert_id.clone(),
                    },
                ))
            })?;
        let supporting_evidence_ids_value: Value =
            serde_json::from_str(&supporting_evidence_ids_json).map_err(|_| {
                rusqlite::Error::ToSqlConversionFailure(Box::new(
                    AccountabilityTracebackReplayError::InvalidJson {
                        alert_id: alert_id.clone(),
                    },
                ))
            })?;
        let chain_node_ids = match chain_node_ids_value {
            Value::Array(entries) if entries.iter().all(Value::is_string) => entries
                .into_iter()
                .filter_map(|value| value.as_str().map(str::to_string))
                .collect::<Vec<_>>(),
            _ => {
                return Err(rusqlite::Error::ToSqlConversionFailure(Box::new(
                    AccountabilityTracebackReplayError::InvalidJson {
                        alert_id: alert_id.clone(),
                    },
                )));
            }
        };
        let supporting_evidence_ids = match supporting_evidence_ids_value {
            Value::Array(entries) if entries.iter().all(Value::is_string) => entries
                .into_iter()
                .filter_map(|value| value.as_str().map(str::to_string))
                .collect(),
            _ => {
                return Err(rusqlite::Error::ToSqlConversionFailure(Box::new(
                    AccountabilityTracebackReplayError::InvalidJson {
                        alert_id: alert_id.clone(),
                    },
                )));
            }
        };
        let explanation_summary = match row.get_ref(5)? {
            ValueRef::Text(text) => String::from_utf8_lossy(text).into_owned(),
            _ => {
                return Err(rusqlite::Error::ToSqlConversionFailure(Box::new(
                    AccountabilityTracebackReplayError::InvalidJson {
                        alert_id: alert_id.clone(),
                    },
                )));
            }
        };
        let root_cause_node_id: String = row.get(3)?;
        if chain_node_ids.is_empty()
            || !chain_node_ids
                .iter()
                .any(|node_id| node_id == &root_cause_node_id)
        {
            return Err(rusqlite::Error::ToSqlConversionFailure(Box::new(
                AccountabilityTracebackReplayError::InvalidRow {
                    alert_id: alert_id.clone(),
                },
            )));
        }
        let summary = AccountabilityTracebackReplaySummary {
            traceback_id,
            alert_id,
            chain_node_ids,
            root_cause_node_id,
            supporting_evidence_ids,
            explanation_summary,
        };
        Ok((summary.alert_id.clone(), summary))
    })?;
    rows.collect::<rusqlite::Result<std::collections::HashMap<_, _>>>()
        .map_err(map_accountability_traceback_replay_error)
}

fn load_accountability_audit_summary(
    db_path: &Path,
    run_id: &str,
) -> Result<Option<AccountabilityAuditSummaryPayload>, AccountabilityAuditSummaryError> {
    let conn = Connection::open(db_path)?;
    let has_table =
        table_column_exists(&conn, "accountability_audit_summaries", "run_id").unwrap_or(false);
    if !has_table {
        return Ok(None);
    }
    let summary = conn.query_row(
        "SELECT run_id, alert_count, traceback_count, summary_text FROM accountability_audit_summaries WHERE run_id = ?1",
        [run_id],
        |row| {
            let summary_text = match row.get_ref(3)? {
                ValueRef::Text(text) => String::from_utf8_lossy(text).into_owned(),
                _ => {
                    return Err(rusqlite::Error::ToSqlConversionFailure(Box::new(
                        AccountabilityAuditSummaryError::InvalidRow {
                            run_id: run_id.to_string(),
                        },
                    )))
                }
            };
            let alert_count_i64 = row.get::<_, i64>(1)?;
            let traceback_count_i64 = row.get::<_, i64>(2)?;
            let (Ok(alert_count), Ok(traceback_count)) = (
                u64::try_from(alert_count_i64),
                u64::try_from(traceback_count_i64),
            ) else {
                return Err(rusqlite::Error::ToSqlConversionFailure(Box::new(
                    AccountabilityAuditSummaryError::InvalidRow {
                        run_id: run_id.to_string(),
                    },
                )));
            };
            Ok(AccountabilityAuditSummaryPayload {
                run_id: row.get(0)?,
                alert_count,
                traceback_count,
                summary_text,
            })
        },
    );
    match summary {
        Ok(value) => Ok(Some(value)),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
        Err(err) => Err(map_accountability_audit_summary_error(err)),
    }
}

async fn serve_alerts_api(
    run_dir: &Path,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let db_path = resolve_db_for_alerts(run_dir);
    let Some(db_path) = db_path else {
        return Ok(not_found_json(json_error(
            "db_unavailable",
            run_dir.file_name().and_then(|s| s.to_str()).unwrap_or("unknown"),
            "database",
        )));
    };

    let conn = match Connection::open(&db_path) {
        Ok(c) => c,
        Err(e) => {
            return Ok(internal_error_json(format!("failed to open alerts DB: {e}")));
        }
    };

    let mut stmt = match conn.prepare(
        "SELECT alert_id, agent_id, timestamp_ns, display_profile, decision, severity,
                confidence, authority, reasoning, title, summary, action_hint
         FROM responsibility_alerts
         ORDER BY timestamp_ns DESC"
    ) {
        Ok(s) => s,
        Err(_) => {
            return Ok(json_response(serde_json::json!([])));
        }
    };

    let rows: Vec<serde_json::Value> = stmt
        .query_map([], |row| {
            Ok(serde_json::json!({
                "alert_id": row.get::<_, String>(0)?,
                "agent_id": row.get::<_, String>(1)?,
                "timestamp_ns": row.get::<_, i64>(2)?,
                "display_profile": row.get::<_, String>(3)?,
                "decision": row.get::<_, String>(4)?,
                "severity": row.get::<_, String>(5)?,
                "confidence": row.get::<_, f64>(6)?,
                "authority": row.get::<_, String>(7)?,
                "reasoning": row.get::<_, String>(8)?,
                "title": row.get::<_, String>(9)?,
                "summary": row.get::<_, String>(10)?,
                "action_hint": row.get::<_, String>(11)?,
            }))
        })
        .ok()
        .map(|iter| iter.filter_map(|r| r.ok()).collect())
        .unwrap_or_default();

    Ok(json_response(serde_json::json!(rows)))
}

fn resolve_db_for_alerts(run_dir: &Path) -> Option<PathBuf> {
    // First check summary-declared external alerts DB path
    if let Some(path) = resolve_artifact_from_summary(run_dir, "alerts_db") {
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
            .filter(|p| p.extension().map_or(false, |ext| ext == "db"))
            .filter(|p| {
                p.file_name()
                    .and_then(|n| n.to_str())
                    .map_or(false, |n| n.contains("alerts"))
            })
            .collect(),
        Err(_) => return None,
    };
    candidates.sort();
    candidates.into_iter().next()
}

async fn serve_correlation_evidence_api(
    run_dir: &Path,
) -> std::result::Result<Response<Full<Bytes>>, Infallible> {
    let run_id = run_dir
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("unknown-run");
    let Some(correlation_evidence_db_path) = resolve_correlation_evidence_db_path(run_dir) else {
        return Ok(not_found_json(json_error(
            "artifact_unavailable",
            run_id,
            "correlation_evidence",
        )));
    };

    let mut rows = match load_correlation_evidence_rows(&correlation_evidence_db_path) {
        Ok(rows) => rows,
        Err(CorrelationEvidencesReplayError::UnsupportedSchema { table }) => {
            return Ok(structured_error_json(
                StatusCode::INTERNAL_SERVER_ERROR,
                "unsupported_correlation_evidence_schema",
                "correlation_evidence schema is unsupported for replay: expected current correlation-evidence columns, found legacy persisted artifact".to_string(),
                serde_json::json!({ "table": table }),
            ));
        }
        Err(CorrelationEvidencesReplayError::InvalidJson {
            column,
            evidence_id,
        }) => {
            return Ok(structured_error_json(
                StatusCode::INTERNAL_SERVER_ERROR,
                "invalid_correlation_evidence_json",
                format!("failed to decode correlation_evidence.{column} for replay"),
                serde_json::json!({ "column": column, "evidence_id": evidence_id }),
            ));
        }
        Err(CorrelationEvidencesReplayError::InvalidJsonShape {
            column,
            evidence_id,
        }) => {
            return Ok(structured_error_json(
                StatusCode::INTERNAL_SERVER_ERROR,
                "invalid_correlation_evidence_json",
                format!("failed to decode correlation_evidence.{column} for replay"),
                serde_json::json!({ "column": column, "evidence_id": evidence_id }),
            ));
        }
        Err(CorrelationEvidencesReplayError::Sqlite(err)) => {
            return Ok(internal_error_json(format!(
                "failed to read correlation evidence DB: {err}"
            )));
        }
    };

    if let Some(causal_db_path) = resolve_correlation_evidence_db_path(run_dir) {
        match load_accountability_traceback_replay_summaries(&causal_db_path) {
            Ok(accountability_tracebacks) => {
                let authoritative_evidence_rows =
                    load_evidence_record_summaries(&causal_db_path).unwrap_or_default();
                attach_correlation_evidence_tracebacks(
                    &mut rows,
                    &accountability_tracebacks,
                    &authoritative_evidence_rows,
                );
            }
            Err(AccountabilityTracebackReplayError::InvalidJson { alert_id })
            | Err(AccountabilityTracebackReplayError::InvalidRow { alert_id }) => {
                return Ok(structured_error_json(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "invalid_accountability_traceback_replay",
                    "failed to decode accountability_traceback_replays row for replay".to_string(),
                    serde_json::json!({ "alert_id": alert_id }),
                ));
            }
            Err(AccountabilityTracebackReplayError::Sqlite(err)) => {
                return Ok(internal_error_json(format!(
                    "failed to read causal DB: {err}"
                )));
            }
        }
    }

    Ok(json_response(serde_json::json!({
        "authority": AUTHORITY_SUPPORTING,
        "correlation_evidence": rows,
    })))
}

fn alert_accountability_evidence<'a>(evidence: &'a Value) -> &'a Value {
    evidence.get("accountability_evidence").unwrap_or(evidence)
}

fn accountability_payload_evidence_id(evidence: &Value) -> Option<&str> {
    alert_accountability_evidence(evidence)
        .get("correlation_evidence")
        .and_then(Value::as_object)
        .and_then(|link| link.get("evidence_id"))
        .and_then(Value::as_str)
}

fn accountability_payload_agent_id(evidence: &Value) -> Option<&str> {
    alert_accountability_evidence(evidence)
        .get("correlation_evidence")
        .and_then(Value::as_object)
        .and_then(|link| link.get("agent_id"))
        .and_then(Value::as_str)
}

fn override_alert_evidence_with_policy_evaluation(
    evidence: &mut Value,
    policy_evaluation: &PolicyEvaluationSummary,
) {
    let Some(obj) = evidence.as_object_mut() else {
        return;
    };
    obj.insert(
        "claim_state".to_string(),
        Value::String(policy_evaluation.claim_state.clone()),
    );
    obj.insert(
        "reason".to_string(),
        Value::String(policy_evaluation.reason.clone()),
    );
    obj.insert(
        "policy_id".to_string(),
        policy_evaluation
            .policy_id
            .as_ref()
            .map(|value| Value::String(value.clone()))
            .unwrap_or(Value::Null),
    );
    obj.insert(
        "boundary_id".to_string(),
        policy_evaluation
            .boundary_id
            .as_ref()
            .map(|value| Value::String(value.clone()))
            .unwrap_or(Value::Null),
    );
}

fn validate_accountability_evidence_payload(
    evidence: &Value,
    authoritative_evidence_rows: &std::collections::HashMap<String, EvidenceRowSummary>,
) -> Vec<serde_json::Value> {
    let mut warnings = Vec::new();
    let accountability_evidence = alert_accountability_evidence(evidence);
    let Some(obj) = accountability_evidence.as_object() else {
        warnings.push(
            serde_json::json!({"code": "accountability_evidence_missing", "severity": "warning"}),
        );
        return warnings;
    };

    if obj.get("kind").and_then(Value::as_str) != Some("accountability_evidence") {
        warnings.push(serde_json::json!({"code": "accountability_evidence_kind_invalid", "severity": "warning"}));
    }

    match obj.get("evidence_refs") {
        Some(Value::Array(refs)) if !refs.is_empty() && refs.iter().all(Value::is_string) => {
            let ref_ids: Vec<String> = refs.iter().filter_map(Value::as_str).map(str::to_string).collect();
            let missing: Vec<String> = ref_ids
                .iter()
                .filter(|evidence_id| !authoritative_evidence_rows.is_empty() && !authoritative_evidence_rows.contains_key(*evidence_id))
                .cloned()
                .collect();
            if !missing.is_empty() {
                warnings.push(serde_json::json!({
                    "code": "accountability_evidence_refs_missing",
                    "severity": "warning",
                    "evidence_refs": missing,
                }));
            }
            if let Some(first_ref) = ref_ids.first()
                && let Some(summary) = authoritative_evidence_rows.get(first_ref)
            {
                if obj.get("directness").and_then(Value::as_str) != Some(summary.directness.as_str()) {
                    warnings.push(serde_json::json!({"code": "accountability_evidence_directness_mismatch", "severity": "warning", "evidence_id": first_ref}));
                }
                let payload_boundary = obj.get("boundary_id").and_then(Value::as_str);
                if payload_boundary != summary.boundary_id.as_deref() {
                    warnings.push(serde_json::json!({"code": "accountability_evidence_boundary_mismatch", "severity": "warning", "evidence_id": first_ref}));
                }
                if let Some(evidence_id) = accountability_payload_evidence_id(evidence)
                    && Some(evidence_id) != summary.payload_ref.as_deref()
                {
                    warnings.push(serde_json::json!({"code": "accountability_evidence_payload_ref_mismatch", "severity": "warning", "evidence_id": first_ref}));
                }
            }
        }
        _ => warnings.push(serde_json::json!({"code": "accountability_evidence_refs_empty", "severity": "warning"})),
    }

    match obj.get("directness").and_then(Value::as_str) {
        Some("direct") | Some("inferred") => {}
        _ => warnings.push(serde_json::json!({"code": "accountability_evidence_directness_invalid", "severity": "warning"})),
    }

    match obj.get("boundary_id") {
        Some(Value::Null) | None | Some(Value::String(_)) => {}
        _ => warnings.push(serde_json::json!({"code": "accountability_evidence_boundary_invalid", "severity": "warning"})),
    }

    if !matches!(obj.get("correlation_evidence"), Some(Value::Object(_))) {
        warnings.push(serde_json::json!({"code": "accountability_evidence_correlation_evidence_missing", "severity": "warning"}));
    }

    warnings
}

fn attach_alert_consistency_warnings(
    rows: &mut Vec<serde_json::Value>,
    correlation_evidence_by_id: &std::collections::HashMap<String, serde_json::Value>,
    authoritative_evidence_rows: &std::collections::HashMap<String, EvidenceRowSummary>,
    authoritative_policy_evaluations: &std::collections::HashMap<String, PolicyEvaluationSummary>,
    accountability_traceback_replays: &std::collections::HashMap<
        String,
        AccountabilityTracebackReplaySummary,
    >,
) {
    rows.retain_mut(|row| {
        let Some(obj) = row.as_object_mut() else {
            return true;
        };
        let evidence_source = obj.get("evidence_source").and_then(Value::as_str);
        let agent_id = obj.get("agent_id").and_then(Value::as_str);
        let causal_evidence_ids = obj.get("causal_evidence_ids").and_then(Value::as_array);
        let mut evidence = obj.get("evidence").cloned().unwrap_or(Value::Null);
        let mut warnings = Vec::new();

        if evidence_source == Some("causal") {
            warnings.extend(validate_accountability_evidence_payload(&evidence, authoritative_evidence_rows));
            if !accountability_evidence_shape_is_valid(alert_accountability_evidence(&evidence)) {
                return false;
            }
            if let Some(evidence_id) = accountability_payload_evidence_id(&evidence)
                && let Some(policy_evaluation) = authoritative_policy_evaluations.get(evidence_id)
            {
                override_alert_evidence_with_policy_evaluation(&mut evidence, policy_evaluation);
            }
            if let Some(payload_agent_id) = accountability_payload_agent_id(&evidence)
                && agent_id.is_some()
                && Some(payload_agent_id) != agent_id
            {
                warnings.push(serde_json::json!({"code": "accountability_evidence_agent_mismatch", "severity": "warning"}));
            }
            if let Some(payload_evidence_id) = accountability_payload_evidence_id(&evidence) {
                if let Some(ids) = causal_evidence_ids {
                    if !ids.iter().filter_map(Value::as_str).any(|id| id == payload_evidence_id) {
                        warnings.push(serde_json::json!({"code": "accountability_evidence_correlation_evidence_mismatch", "severity": "warning", "evidence_id": payload_evidence_id}));
                    }
                }
            }
            match causal_evidence_ids {
                Some(ids) if ids.is_empty() => {
                    warnings.push(serde_json::json!({"code": "correlation_evidence_empty", "severity": "warning"}));
                }
                Some(ids) => {
                    let mut missing = Vec::new();
                    let mut mismatched = Vec::new();
                    for id in ids.iter().filter_map(Value::as_str) {
                        match correlation_evidence_by_id.get(id) {
                            None => missing.push(id.to_string()),
                            Some(link)
                                if agent_id.is_some()
                                    && link.get("agent_id").and_then(Value::as_str) != agent_id =>
                            {
                                mismatched.push(id.to_string());
                            }
                            _ => {}
                        }
                    }
                    if !missing.is_empty() {
                        warnings.push(serde_json::json!({"code": "correlation_evidence_missing", "severity": "warning", "evidence_ids": missing}));
                    }
                    if !mismatched.is_empty() {
                        warnings.push(serde_json::json!({"code": "correlation_evidence_agent_mismatch", "severity": "warning", "evidence_ids": mismatched}));
                    }
                    if let Some(payload_evidence_id) = accountability_payload_evidence_id(&evidence)
                        && let Some(link) = correlation_evidence_by_id.get(payload_evidence_id)
                        && agent_id.is_some()
                        && link.get("agent_id").and_then(Value::as_str) != agent_id
                    {
                        warnings.push(serde_json::json!({"code": "accountability_evidence_agent_mismatch", "severity": "warning"}));
                    }
                }
                None => {
                    warnings.push(serde_json::json!({"code": "correlation_evidence_empty", "severity": "warning"}));
                }
            }
        }

        obj.insert("evidence".to_string(), evidence);
        if let Some(traceback) = accountability_traceback_replays.get(
            obj.get("alert_id").and_then(Value::as_str).unwrap_or_default(),
        ) {
            obj.insert(
                "accountability_traceback".to_string(),
                serde_json::json!({
                    "traceback_id": traceback.traceback_id,
                    "alert_id": traceback.alert_id,
                    "chain_node_ids": traceback.chain_node_ids,
                    "root_cause_node_id": traceback.root_cause_node_id,
                    "supporting_evidence_ids": traceback.supporting_evidence_ids,
                    "explanation_summary": traceback.explanation_summary,
                }),
            );
        }
        obj.insert("consistency_warnings".to_string(), serde_json::Value::Array(warnings));
        true
    });
}

fn attach_correlation_evidence_tracebacks(
    rows: &mut Vec<serde_json::Value>,
    accountability_traceback_replays: &std::collections::HashMap<
        String,
        AccountabilityTracebackReplaySummary,
    >,
    authoritative_evidence_rows: &std::collections::HashMap<String, EvidenceRowSummary>,
) {
    if accountability_traceback_replays.is_empty() || authoritative_evidence_rows.is_empty() {
        return;
    }

    let mut tracebacks_by_evidence_id = std::collections::HashMap::new();
    for traceback in accountability_traceback_replays.values() {
        let mut matched_evidence_ids = traceback
            .supporting_evidence_ids
            .iter()
            .filter_map(|evidence_id| authoritative_evidence_rows.get(evidence_id))
            .filter_map(|summary| summary.payload_ref.as_ref())
            .cloned()
            .collect::<std::collections::BTreeSet<_>>();

        if matched_evidence_ids.len() != 1 {
            continue;
        }

        let Some(evidence_id) = matched_evidence_ids.pop_first() else {
            continue;
        };
        tracebacks_by_evidence_id.insert(evidence_id, traceback.clone());
    }

    for row in rows.iter_mut() {
        let Some(obj) = row.as_object_mut() else {
            continue;
        };
        let Some(evidence_id) = obj.get("evidence_id").and_then(Value::as_str) else {
            continue;
        };
        let Some(traceback) = tracebacks_by_evidence_id.get(evidence_id) else {
            continue;
        };
        obj.insert(
            "accountability_traceback".to_string(),
            serde_json::json!({
                "traceback_id": traceback.traceback_id,
                "alert_id": traceback.alert_id,
                "chain_node_ids": traceback.chain_node_ids,
                "root_cause_node_id": traceback.root_cause_node_id,
                "supporting_evidence_ids": traceback.supporting_evidence_ids,
                "explanation_summary": traceback.explanation_summary,
            }),
        );
    }
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

fn resolve_alerts_db_path(run_dir: &Path) -> Option<PathBuf> {
    if let Some(path) = resolve_artifact_from_summary(run_dir, "alerts_db") {
        return Some(path);
    }
    let canonical = run_dir.join("trace.log.alerts.db");
    if canonical.exists() {
        return Some(canonical);
    }

    let mut candidates = match std::fs::read_dir(run_dir) {
        Ok(entries) => entries
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|path| path.extension().is_some_and(|ext| ext == "db"))
            .filter(|path| {
                path.file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.ends_with(".alerts.db"))
            })
            .collect::<Vec<_>>(),
        Err(_) => return None,
    };
    candidates.sort();
    candidates.into_iter().next()
}

fn load_alert_rows(db_path: &Path) -> Result<Vec<serde_json::Value>, AlertsReplayError> {
    let conn = Connection::open(db_path)?;
    let has_evidence_source = alert_column_exists(&conn, "evidence_source")?;
    let query = if has_evidence_source {
        "SELECT alert_id, agent_id, timestamp_ns, severity, confidence, category, rule_id, description, evidence_source, causal_evidence_ids_json, evidence_json FROM alerts ORDER BY timestamp_ns, alert_id"
    } else {
        "SELECT alert_id, agent_id, timestamp_ns, severity, confidence, category, rule_id, description, causal_evidence_ids_json, evidence_json FROM alerts ORDER BY timestamp_ns, alert_id"
    };
    let mut stmt = conn.prepare(query)?;
    let rows = stmt.query_map([], |row| {
        let alert_id: String = row.get(0)?;
        let (evidence_source, causal_idx, evidence_idx) = if has_evidence_source {
            (row.get::<_, String>(8)?, 9, 10)
        } else {
            ("anomaly".to_string(), 8, 9)
        };
        let causal_evidence_ids_json: String = row.get(causal_idx)?;
        let evidence_json: String = row.get(evidence_idx)?;
        let causal_evidence_ids =
            parse_alert_causal_evidence_ids_json(&causal_evidence_ids_json, &alert_id)?;
        let evidence = parse_alert_evidence_json(&evidence_json, &alert_id)?;
        Ok(serde_json::json!({
            "alert_id": alert_id,
            "agent_id": row.get::<_, String>(1)?,
            "timestamp_ns": row.get::<_, i64>(2)?,
            "severity": row.get::<_, String>(3)?,
            "confidence": row.get::<_, f64>(4)?,
            "category": row.get::<_, String>(5)?,
            "rule_id": row.get::<_, String>(6)?,
            "description": row.get::<_, String>(7)?,
            "evidence_source": evidence_source,
            "causal_evidence_ids": causal_evidence_ids,
            "evidence": evidence,
        }))
    })?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .map_err(map_alerts_row_error)
}

fn parse_alert_json_column(
    json: &str,
    column: &'static str,
    alert_id: &str,
) -> Result<Value, rusqlite::Error> {
    serde_json::from_str(json).map_err(|_| {
        rusqlite::Error::ToSqlConversionFailure(Box::new(AlertsReplayError::InvalidJson {
            column,
            alert_id: alert_id.to_string(),
        }))
    })
}

fn parse_alert_causal_evidence_ids_json(
    json: &str,
    alert_id: &str,
) -> Result<Value, rusqlite::Error> {
    let value = parse_alert_json_column(json, "causal_evidence_ids_json", alert_id)?;
    match value {
        Value::Array(entries) if entries.iter().all(Value::is_string) => Ok(Value::Array(entries)),
        _ => Err(rusqlite::Error::ToSqlConversionFailure(Box::new(
            AlertsReplayError::InvalidJsonShape {
                column: "causal_evidence_ids_json",
                alert_id: alert_id.to_string(),
            },
        ))),
    }
}

fn parse_alert_evidence_json(json: &str, alert_id: &str) -> Result<Value, rusqlite::Error> {
    parse_alert_json_column(json, "evidence_json", alert_id)
}

fn resolve_correlation_evidence_db_path(run_dir: &Path) -> Option<PathBuf> {
    let replayed = run_dir.join("trace.log.replayed.causal.db");
    if replayed.exists() {
        return Some(replayed);
    }

    if let Some(path) = resolve_artifact_from_summary(run_dir, "causal_db") {
        return Some(path);
    }

    let canonical = run_dir.join("trace.log.causal.db");
    if canonical.exists() {
        return Some(canonical);
    }

    let mut candidates = match std::fs::read_dir(run_dir) {
        Ok(entries) => entries
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|path| path.extension().is_some_and(|ext| ext == "db"))
            .filter(|path| {
                path.file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.ends_with(".causal.db"))
            })
            .collect::<Vec<_>>(),
        Err(_) => return None,
    };
    candidates.sort();
    candidates.into_iter().next()
}

fn load_correlation_evidence_rows(
    db_path: &Path,
) -> Result<Vec<serde_json::Value>, CorrelationEvidencesReplayError> {
    let conn = match open_db_read_only(&db_path.to_string_lossy()) {
        Ok(conn) => conn,
        Err(err) => {
            let fallback_conn = Connection::open(db_path)?;
            let has_legacy_causal_links =
                table_column_exists(&fallback_conn, "causal_links", "evidence_id").unwrap_or(false);
            if has_legacy_causal_links {
                let row_count: i64 =
                    fallback_conn
                        .query_row("SELECT COUNT(*) FROM causal_links", [], |row| row.get(0))?;
                if row_count == 0 {
                    return Ok(Vec::new());
                }
                return Err(CorrelationEvidencesReplayError::UnsupportedSchema {
                    table: "correlation_evidence",
                });
            }
            if !correlation_evidence_schema_is_current_shape(&fallback_conn)? {
                return Err(CorrelationEvidencesReplayError::UnsupportedSchema {
                    table: "correlation_evidence",
                });
            }
            return Err(CorrelationEvidencesReplayError::Sqlite(err));
        }
    };
    if !correlation_evidence_schema_is_current_shape(&conn)? {
        let has_legacy_causal_links =
            table_column_exists(&conn, "causal_links", "evidence_id").unwrap_or(false);
        if has_legacy_causal_links {
            let row_count: i64 =
                conn.query_row("SELECT COUNT(*) FROM causal_links", [], |row| row.get(0))?;
            if row_count == 0 {
                return Ok(Vec::new());
            }
        } else {
            let row_count: i64 =
                conn.query_row("SELECT COUNT(*) FROM correlation_evidence", [], |row| {
                    row.get(0)
                })?;
            if row_count == 0 {
                return Ok(Vec::new());
            }
        }
        return Err(CorrelationEvidencesReplayError::UnsupportedSchema {
            table: "correlation_evidence",
        });
    }
    let has_authority_source =
        table_column_exists(&conn, "correlation_evidence", "authority_source")?;
    let has_message_id = table_column_exists(&conn, "correlation_evidence", "message_id")?;
    let has_source_event_ref =
        table_column_exists(&conn, "correlation_evidence", "source_event_ref")?;
    let authority_source_sql = if has_authority_source {
        "authority_source"
    } else {
        "NULL AS authority_source"
    };
    let message_id_sql = if has_message_id {
        "message_id"
    } else {
        "NULL AS message_id"
    };
    let source_event_ref_sql = if has_source_event_ref {
        "source_event_ref"
    } else {
        "NULL AS source_event_ref"
    };
    let query = format!(
        "SELECT schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence, \
                match_level, matched_fields_json, supporting_raw_event_refs_json, conversation_id, {authority_source_sql}, {message_id_sql}, {source_event_ref_sql}, debug_notes \
         FROM correlation_evidence ORDER BY timestamp_ns, evidence_id"
    );
    let mut stmt = conn.prepare(&query)?;
    let rows = stmt.query_map([], |row| {
        let evidence_id: String = row.get(1)?;
        let matched_fields =
            parse_matched_fields_json(row.get::<_, Option<String>>(8)?, &evidence_id)?;
        let supporting_raw_event_refs =
            parse_supporting_raw_event_refs_json(row.get::<_, Option<String>>(9)?, &evidence_id)?;
        let authority_source =
            parse_authority_source_column(row.get::<_, Option<String>>(11)?, &evidence_id)?;
        let message_id =
            parse_optional_string_value_ref(row.get_ref(12)?, "message_id", &evidence_id)?;
        let source_event_ref =
            parse_optional_string_value_ref(row.get_ref(13)?, "source_event_ref", &evidence_id)?;
        Ok(serde_json::json!({
            "schema_version": row.get::<_, i64>(0)?,
            "evidence_id": evidence_id,
            "agent_id": row.get::<_, String>(2)?,
            "intent_id": row.get::<_, String>(3)?,
            "candidate_id": row.get::<_, String>(4)?,
            "timestamp_ns": row.get::<_, i64>(5)?,
            "confidence": row.get::<_, f64>(6)?,
            "match_level": row.get::<_, String>(7)?,
            "matched_fields": matched_fields,
            "supporting_raw_event_refs": supporting_raw_event_refs,
            "conversation_id": row.get::<_, Option<String>>(10)?,
            "authority_source": authority_source,
            "message_id": message_id,
            "source_event_ref": source_event_ref,
            "debug_notes": row.get::<_, Option<String>>(14)?,
        }))
    })?;
    rows.collect::<rusqlite::Result<Vec<_>>>()
        .map_err(map_correlation_evidence_row_error)
}

fn map_correlation_evidence_row_error(err: rusqlite::Error) -> CorrelationEvidencesReplayError {
    if let rusqlite::Error::ToSqlConversionFailure(inner) = err {
        match inner.downcast::<CorrelationEvidencesReplayError>() {
            Ok(replay_err) => *replay_err,
            Err(inner) => CorrelationEvidencesReplayError::Sqlite(
                rusqlite::Error::ToSqlConversionFailure(inner),
            ),
        }
    } else {
        CorrelationEvidencesReplayError::Sqlite(err)
    }
}

fn map_alerts_row_error(err: rusqlite::Error) -> AlertsReplayError {
    if let rusqlite::Error::ToSqlConversionFailure(inner) = err {
        match inner.downcast::<AlertsReplayError>() {
            Ok(replay_err) => *replay_err,
            Err(inner) => AlertsReplayError::Sqlite(rusqlite::Error::ToSqlConversionFailure(inner)),
        }
    } else {
        AlertsReplayError::Sqlite(err)
    }
}

fn map_accountability_traceback_replay_error(
    err: rusqlite::Error,
) -> AccountabilityTracebackReplayError {
    if let rusqlite::Error::ToSqlConversionFailure(inner) = err {
        match inner.downcast::<AccountabilityTracebackReplayError>() {
            Ok(replay_err) => *replay_err,
            Err(inner) => AccountabilityTracebackReplayError::Sqlite(
                rusqlite::Error::ToSqlConversionFailure(inner),
            ),
        }
    } else {
        AccountabilityTracebackReplayError::Sqlite(err)
    }
}

fn map_accountability_audit_summary_error(err: rusqlite::Error) -> AccountabilityAuditSummaryError {
    if let rusqlite::Error::ToSqlConversionFailure(inner) = err {
        match inner.downcast::<AccountabilityAuditSummaryError>() {
            Ok(replay_err) => *replay_err,
            Err(inner) => AccountabilityAuditSummaryError::Sqlite(
                rusqlite::Error::ToSqlConversionFailure(inner),
            ),
        }
    } else {
        AccountabilityAuditSummaryError::Sqlite(err)
    }
}

fn load_latest_accountability_snapshot(db_path: &Path) -> Option<serde_json::Value> {
    let db_path_str = db_path.to_string_lossy();
    let conn = open_db_read_only(&db_path_str).ok()?;
    let has_snapshots_table: bool = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='causal_graph_snapshots'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count > 0)
        .unwrap_or(false);
    if !has_snapshots_table {
        return None;
    }
    let has_expanded_columns: bool = conn
        .query_row(
            "SELECT count(*) FROM pragma_table_info('causal_graph_snapshots') WHERE name='snapshot_kind'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count > 0)
        .unwrap_or(false);
    if !has_expanded_columns {
        let row: Option<(String, i64, String, String)> = conn
            .query_row(
                "SELECT snapshot_id, timestamp_ns, agent_id, evidence_ids_json
                 FROM causal_graph_snapshots
                 ORDER BY timestamp_ns DESC LIMIT 1",
                [],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .optional()
            .ok()?;
        return row.map(|(snapshot_id, timestamp_ns, agent_id, evidence_ids_json)| {
            serde_json::json!({
                "snapshot_id": snapshot_id,
                "timestamp_ns": timestamp_ns,
                "agent_id": agent_id,
                "evidence_ids": serde_json::from_str::<Vec<String>>(&evidence_ids_json).unwrap_or_default(),
                "snapshot_kind": "legacy_link_window",
            })
        });
    }
    let snapshot: Option<serde_json::Value> = conn
        .query_row(
            "SELECT snapshot_id, timestamp_ns, agent_id, evidence_ids_json, evidence_ids_json,
                    policy_ids_json, boundary_ids_json, claim_states_json, traceback_ids_json,
                    violation_match_ids_json, node_count, edge_count, snapshot_kind
             FROM causal_graph_snapshots
             ORDER BY timestamp_ns DESC LIMIT 1",
            [],
            |row| {
                let evidence_ids: Vec<String> =
                    serde_json::from_str(&row.get::<_, String>(3)?).unwrap_or_default();
                let evidence_ids: Vec<String> =
                    serde_json::from_str(&row.get::<_, String>(4)?).unwrap_or_default();
                let policy_ids: Vec<String> =
                    serde_json::from_str(&row.get::<_, String>(5)?).unwrap_or_default();
                let boundary_ids: Vec<String> =
                    serde_json::from_str(&row.get::<_, String>(6)?).unwrap_or_default();
                let claim_states: Vec<String> =
                    serde_json::from_str(&row.get::<_, String>(7)?).unwrap_or_default();
                let traceback_ids: Vec<String> =
                    serde_json::from_str(&row.get::<_, String>(8)?).unwrap_or_default();
                let violation_match_ids: Vec<String> =
                    serde_json::from_str(&row.get::<_, String>(9)?).unwrap_or_default();
                Ok(serde_json::json!({
                    "snapshot_id": row.get::<_, String>(0)?,
                    "timestamp_ns": row.get::<_, i64>(1)?,
                    "agent_id": row.get::<_, String>(2)?,
                    "evidence_ids": evidence_ids,
                    "evidence_ids": evidence_ids,
                    "policy_ids": policy_ids,
                    "boundary_ids": boundary_ids,
                    "claim_states": claim_states,
                    "traceback_ids": traceback_ids,
                    "violation_match_ids": violation_match_ids,
                    "node_count": row.get::<_, i64>(10)?,
                    "edge_count": row.get::<_, i64>(11)?,
                    "snapshot_kind": row.get::<_, String>(12)?,
                }))
            },
        )
        .optional()
        .ok()?;
    snapshot
}

fn load_violation_pattern_rows(db_path: &Path) -> Result<Vec<serde_json::Value>, rusqlite::Error> {
    let conn = open_db_read_only(&db_path.to_string_lossy())?;
    let exists: i64 = conn.query_row(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='violation_pattern_matches'",
        [],
        |row| row.get(0),
    )?;
    if exists == 0 {
        return Ok(Vec::new());
    }
    let mut stmt = conn.prepare(
        "SELECT match_id, template_id, agent_id, matched_evidence_ids_json, matched_policies_json, matched_boundaries_json, matched_targets_json, start_ns, end_ns, severity, description FROM violation_pattern_matches ORDER BY end_ns, match_id"
    )?;
    let rows = stmt.query_map([], |row| {
        let matched_evidence_ids: Value =
            serde_json::from_str(&row.get::<_, String>(3)?).unwrap_or(Value::Null);
        let matched_policies: Value =
            serde_json::from_str(&row.get::<_, String>(4)?).unwrap_or(Value::Null);
        let matched_boundaries: Value =
            serde_json::from_str(&row.get::<_, String>(5)?).unwrap_or(Value::Null);
        let matched_targets: Value =
            serde_json::from_str(&row.get::<_, String>(6)?).unwrap_or(Value::Null);
        Ok(serde_json::json!({
            "match_id": row.get::<_, String>(0)?,
            "template_id": row.get::<_, String>(1)?,
            "agent_id": row.get::<_, String>(2)?,
            "matched_evidence_ids": matched_evidence_ids,
            "matched_policies": matched_policies,
            "matched_boundaries": matched_boundaries,
            "matched_targets": matched_targets,
            "start_ns": row.get::<_, i64>(7)?,
            "end_ns": row.get::<_, i64>(8)?,
            "severity": row.get::<_, String>(9)?,
            "description": row.get::<_, String>(10)?,
        }))
    })?;
    rows.collect()
}

#[derive(Debug)]
enum AlertsReplayError {
    Sqlite(rusqlite::Error),
    InvalidJson {
        column: &'static str,
        alert_id: String,
    },
    InvalidJsonShape {
        column: &'static str,
        alert_id: String,
    },
}

impl std::fmt::Display for AlertsReplayError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Sqlite(err) => write!(f, "{err}"),
            Self::InvalidJson { column, alert_id } => write!(
                f,
                "failed to decode alerts.{column} for replay (alert_id={alert_id})"
            ),
            Self::InvalidJsonShape { column, alert_id } => write!(
                f,
                "failed to decode alerts.{column} for replay (alert_id={alert_id})"
            ),
        }
    }
}

impl std::error::Error for AlertsReplayError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Sqlite(err) => Some(err),
            Self::InvalidJson { .. } | Self::InvalidJsonShape { .. } => None,
        }
    }
}

impl From<rusqlite::Error> for AlertsReplayError {
    fn from(err: rusqlite::Error) -> Self {
        Self::Sqlite(err)
    }
}

#[derive(Debug)]
enum AccountabilityAuditSummaryError {
    Sqlite(rusqlite::Error),
    InvalidRow { run_id: String },
}

impl std::fmt::Display for AccountabilityAuditSummaryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Sqlite(err) => write!(f, "{err}"),
            Self::InvalidRow { run_id } => write!(
                f,
                "failed to decode accountability_audit_summaries row for replay (run_id={run_id})"
            ),
        }
    }
}

impl std::error::Error for AccountabilityAuditSummaryError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Sqlite(err) => Some(err),
            Self::InvalidRow { .. } => None,
        }
    }
}

impl From<rusqlite::Error> for AccountabilityAuditSummaryError {
    fn from(err: rusqlite::Error) -> Self {
        Self::Sqlite(err)
    }
}

#[derive(Debug)]
enum AccountabilityTracebackReplayError {
    Sqlite(rusqlite::Error),
    InvalidJson { alert_id: String },
    InvalidRow { alert_id: String },
}

impl std::fmt::Display for AccountabilityTracebackReplayError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Sqlite(err) => write!(f, "{err}"),
            Self::InvalidJson { alert_id } | Self::InvalidRow { alert_id } => write!(
                f,
                "failed to decode accountability_traceback_replays row for replay (alert_id={alert_id})"
            ),
        }
    }
}

impl std::error::Error for AccountabilityTracebackReplayError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Sqlite(err) => Some(err),
            Self::InvalidJson { .. } | Self::InvalidRow { .. } => None,
        }
    }
}

impl From<rusqlite::Error> for AccountabilityTracebackReplayError {
    fn from(err: rusqlite::Error) -> Self {
        Self::Sqlite(err)
    }
}

#[derive(Debug)]
enum CorrelationEvidencesReplayError {
    Sqlite(rusqlite::Error),
    UnsupportedSchema {
        table: &'static str,
    },
    InvalidJson {
        column: &'static str,
        evidence_id: String,
    },
    InvalidJsonShape {
        column: &'static str,
        evidence_id: String,
    },
}

impl std::fmt::Display for CorrelationEvidencesReplayError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Sqlite(err) => write!(f, "{err}"),
            Self::UnsupportedSchema { table } => write!(
                f,
                "{table} schema is unsupported for replay: expected appendix causal-links columns, found legacy persisted artifact"
            ),
            Self::InvalidJson {
                column,
                evidence_id,
            } => write!(
                f,
                "failed to decode correlation_evidence.{column} for replay (evidence_id={evidence_id})"
            ),
            Self::InvalidJsonShape {
                column,
                evidence_id,
            } => write!(
                f,
                "failed to decode correlation_evidence.{column} for replay (evidence_id={evidence_id})"
            ),
        }
    }
}

impl std::error::Error for CorrelationEvidencesReplayError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Sqlite(err) => Some(err),
            Self::UnsupportedSchema { .. }
            | Self::InvalidJson { .. }
            | Self::InvalidJsonShape { .. } => None,
        }
    }
}

impl From<rusqlite::Error> for CorrelationEvidencesReplayError {
    fn from(err: rusqlite::Error) -> Self {
        Self::Sqlite(err)
    }
}

fn correlation_evidence_schema_is_current_shape(
    conn: &Connection,
) -> Result<bool, CorrelationEvidencesReplayError> {
    let required_columns = [
        "schema_version",
        "evidence_id",
        "agent_id",
        "intent_id",
        "candidate_id",
        "timestamp_ns",
        "confidence",
        "match_level",
        "matched_fields_json",
        "supporting_raw_event_refs_json",
        "conversation_id",
        "debug_notes",
    ];

    for column in required_columns {
        if !table_column_exists(conn, "correlation_evidence", column)? {
            return Ok(false);
        }
    }
    Ok(true)
}

fn validate_correlation_evidence_schema(
    conn: &Connection,
) -> Result<(), CorrelationEvidencesReplayError> {
    if correlation_evidence_schema_is_current_shape(conn)? {
        Ok(())
    } else {
        Err(CorrelationEvidencesReplayError::UnsupportedSchema {
            table: "correlation_evidence",
        })
    }
}

fn parse_optional_json_column(
    json: Option<String>,
    column: &'static str,
    evidence_id: &str,
) -> Result<Value, rusqlite::Error> {
    match json {
        Some(json) => serde_json::from_str(&json).map_err(|_| {
            rusqlite::Error::ToSqlConversionFailure(Box::new(
                CorrelationEvidencesReplayError::InvalidJson {
                    column,
                    evidence_id: evidence_id.to_string(),
                },
            ))
        }),
        None => Ok(Value::Null),
    }
}

fn parse_matched_fields_json(
    json: Option<String>,
    evidence_id: &str,
) -> Result<Value, rusqlite::Error> {
    let value = parse_optional_json_column(json, "matched_fields_json", evidence_id)?;
    if value.is_null() || value.is_object() {
        Ok(value)
    } else {
        Err(rusqlite::Error::ToSqlConversionFailure(Box::new(
            CorrelationEvidencesReplayError::InvalidJsonShape {
                column: "matched_fields_json",
                evidence_id: evidence_id.to_string(),
            },
        )))
    }
}

fn parse_supporting_raw_event_refs_json(
    json: Option<String>,
    evidence_id: &str,
) -> Result<Value, rusqlite::Error> {
    let value = parse_optional_json_column(json, "supporting_raw_event_refs_json", evidence_id)?;
    match value {
        Value::Null => Ok(Value::Null),
        Value::Array(entries) if entries.iter().all(Value::is_string) => Ok(Value::Array(entries)),
        _ => Err(rusqlite::Error::ToSqlConversionFailure(Box::new(
            CorrelationEvidencesReplayError::InvalidJsonShape {
                column: "supporting_raw_event_refs_json",
                evidence_id: evidence_id.to_string(),
            },
        ))),
    }
}

fn parse_authority_source_column(
    value: Option<String>,
    evidence_id: &str,
) -> Result<Value, rusqlite::Error> {
    match value {
        None => Ok(Value::Null),
        Some(source)
            if matches!(
                source.as_str(),
                "request_history_assistant_tool_use"
                    | "sse_response_tool_use"
                    | "http_response_tool_use"
                    | "text_fallback"
            ) =>
        {
            Ok(Value::String(source))
        }
        Some(_) => Err(rusqlite::Error::ToSqlConversionFailure(Box::new(
            CorrelationEvidencesReplayError::InvalidJsonShape {
                column: "authority_source",
                evidence_id: evidence_id.to_string(),
            },
        ))),
    }
}

fn parse_optional_string_value_ref(
    value: ValueRef<'_>,
    column: &'static str,
    evidence_id: &str,
) -> Result<Value, rusqlite::Error> {
    match value {
        ValueRef::Null => Ok(Value::Null),
        ValueRef::Text(text) => Ok(Value::String(String::from_utf8_lossy(text).into_owned())),
        _ => Err(rusqlite::Error::ToSqlConversionFailure(Box::new(
            CorrelationEvidencesReplayError::InvalidJsonShape {
                column,
                evidence_id: evidence_id.to_string(),
            },
        ))),
    }
}

fn alert_column_exists(conn: &Connection, column: &str) -> rusqlite::Result<bool> {
    table_column_exists(conn, "alerts", column)
}

fn table_column_exists(conn: &Connection, table: &str, column: &str) -> rusqlite::Result<bool> {
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})"))?;
    let rows = stmt.query_map([], |row| row.get::<_, String>(1))?;
    for row in rows {
        if row? == column {
            return Ok(true);
        }
    }
    Ok(false)
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
                stages_map.insert(stage_name, StageMetricsSnapshot::from_stage_metrics(metrics));
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
        .header("Access-Control-Allow-Origin", "*")
        .body(Full::new(Bytes::from(value.to_string())))
        .unwrap()
}

fn not_found_json(value: serde_json::Value) -> Response<Full<Bytes>> {
    Response::builder()
        .status(StatusCode::NOT_FOUND)
        .header("Content-Type", "application/json")
        .header("Access-Control-Allow-Origin", "*")
        .body(Full::new(Bytes::from(value.to_string())))
        .unwrap()
}

fn internal_error_json(message: String) -> Response<Full<Bytes>> {
    Response::builder()
        .status(StatusCode::INTERNAL_SERVER_ERROR)
        .header("Content-Type", "application/json")
        .header("Access-Control-Allow-Origin", "*")
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
        .header("Access-Control-Allow-Origin", "*")
        .body(Full::new(Bytes::from(
            serde_json::json!({
                "error": error,
                "message": message,
                "details": details,
            })
            .to_string(),
        )))
        .unwrap()
}

fn json_error(error: &str, run_id: &str, artifact: &str) -> serde_json::Value {
    serde_json::json!({
        "error": error,
        "run_id": run_id,
        "artifact": artifact,
    })
}

#[cfg(test)]
mod tests {
    use super::{
        handle_request, match_run_route, resolve_run_dir, serve_alerts_api,
        serve_correlation_evidence_api, serve_events_api, serve_run_agent_snapshot_chain_api,
        serve_run_agent_snapshots_api, serve_run_evaluation_api, serve_run_events_api,
        serve_run_graph_analysis_api, serve_run_snapshot_by_id_api, serve_run_snapshot_chain_api,
        serve_run_snapshot_cross_diff_api, serve_run_snapshot_diff_api, serve_run_snapshots_api,
        serve_run_summary_api,
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
            response.headers().get("x-agentsight-legacy-api").unwrap(),
            "true"
        );
        assert_eq!(
            response.headers().get("x-agentsight-authority").unwrap(),
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
            response.headers().get("x-agentsight-legacy-api").unwrap(),
            "true"
        );
        assert_eq!(
            response.headers().get("x-agentsight-authority").unwrap(),
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
                        "events": false
                    },
                    "artifact_sources": {
                        "correlation_evidence": "persisted"
                    },
                    "warnings": []
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
                        "events": false
                    },
                    "artifact_sources": {
                        "correlation_evidence": "missing"
                    },
                    "warnings": []
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
                    "correlation_evidence": false,
                    "events": false
                },
                "artifact_sources": {
                    "correlation_evidence": "missing"
                },
                "warnings": []
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
                    "correlation_evidence": false,
                    "events": false
                },
                "artifact_sources": {
                    "correlation_evidence": "missing"
                },
                "warnings": []
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
        assert_eq!(response.status(), StatusCode::NOT_FOUND);
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/json"
        );
        assert_eq!(
            read_json_body(response).await,
            json!({
                "code": "no_active_run"
            })
        );
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
                    "events": false
                },
                "artifact_sources": {
                    "correlation_evidence": "missing"
                },
                "warnings": []
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
        assert_eq!(
            read_json_body(response).await,
            json!([
                {
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
                },
                {
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
                }
            ])
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
        assert_eq!(
            body,
            json!([
                {
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
                }
            ])
        );
        assert_run_bound_payload_is_not_legacy_sample_data(&body);
    }

    #[tokio::test]
    #[ignore]
    async fn web_alerts_surface_claim_state_and_policy_fields_without_frontend_invention() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_alerts_db(&[json!({
            "alert_id": "alert-policy-1",
            "agent_id": "agent-1",
            "timestamp_ns": 123,
            "severity": "WARNING",
            "confidence": 0.8,
            "category": "policy",
            "rule_id": "rule-policy-1",
            "description": "policy-grounded alert",
            "evidence_source": "causal",
            "causal_evidence_ids": ["link-1"],
            "evidence": {
                "claim_state": "wrong-claim-state",
                "policy_id": "policy.wrong",
                "boundary_id": "boundary.wrong",
                "reason": "wrong alert payload reason",
                "accountability_evidence": {
                    "kind": "accountability_evidence",
                    "evidence_refs": ["evidence:link-1"],
                    "directness": "direct",
                    "boundary_id": "boundary.file_read",
                    "correlation_evidence": {"evidence_id": "link-1", "agent_id": "agent-1"}
                }
            }
        })]);
        fixture.write_evidence_records_db(&[json!({
            "evidence_id": "evidence:link-1",
            "source_event_id": "action-1",
            "directness": "direct",
            "rule_or_inference_id": "causal_link:l1_exact",
            "resource_id": {"File": {"canonical_path": "/tmp/demo.txt"}},
            "boundary_id": "boundary.file_read",
            "confidence": 0.8,
            "redaction_status": "none",
            "payload_ref": "link-1"
        })]);
        fixture.write_policy_evaluations_db(&[json!({
            "evidence_id": "link-1",
            "policy_id": "policy.file.read",
            "boundary_id": "boundary.file_read",
            "claim_state": "observed",
            "reason": "file-read boundary match"
        })]);
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1,
            "evidence_id": "link-1",
            "agent_id": "agent-1",
            "intent_id": "intent-1",
            "candidate_id": "candidate-1",
            "timestamp_ns": 123,
            "confidence": 0.8,
            "match_level": "l1_exact",
            "matched_fields": {"path": "/tmp/demo.txt"},
            "supporting_raw_event_refs": ["action-1"],
            "conversation_id": null,
            "authority_source": "request_history_assistant_tool_use",
            "message_id": "msg-1",
            "source_event_ref": "content-block-1",
            "debug_notes": null
        })]);

        let response = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!([
                {
                    "alert_id": "alert-policy-1",
                    "agent_id": "agent-1",
                    "timestamp_ns": 123,
                    "severity": "WARNING",
                    "confidence": 0.8,
                    "category": "policy",
                    "rule_id": "rule-policy-1",
                    "description": "policy-grounded alert",
                    "evidence_source": "causal",
                    "causal_evidence_ids": ["link-1"],
                    "evidence": {
                        "claim_state": "observed",
                        "policy_id": "policy.file.read",
                        "boundary_id": "boundary.file_read",
                        "reason": "file-read boundary match",
                        "accountability_evidence": {
                            "kind": "accountability_evidence",
                            "evidence_refs": ["evidence:link-1"],
                            "directness": "direct",
                            "boundary_id": "boundary.file_read",
                            "correlation_evidence": {"evidence_id": "link-1", "agent_id": "agent-1"}
                        }
                    },
                    "consistency_warnings": []
                }
            ])
        );
    }

    #[tokio::test]
    #[ignore]
    async fn web_alerts_load_authoritative_policy_and_traceback_rows_from_causal_db() {
        let fixture = RunFixture::new("test-run-split-db-authoritative");
        fixture.write_summary(json!({
            "run_id": "test-run-split-db-authoritative",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_alerts_db(&[json!({
            "alert_id": "alert-policy-1",
            "agent_id": "agent-1",
            "timestamp_ns": 123,
            "severity": "WARNING",
            "confidence": 0.8,
            "category": "policy",
            "rule_id": "rule-policy-1",
            "description": "policy-grounded alert",
            "evidence_source": "causal",
            "causal_evidence_ids": ["link-1"],
            "evidence": {
                "claim_state": "wrong-claim-state",
                "policy_id": "policy.wrong",
                "boundary_id": "boundary.wrong",
                "reason": "wrong alert payload reason",
                "accountability_evidence": {
                    "kind": "accountability_evidence",
                    "evidence_refs": ["evidence:link-1"],
                    "directness": "direct",
                    "boundary_id": "boundary.file_read",
                    "correlation_evidence": {"evidence_id": "link-1", "agent_id": "agent-1"}
                }
            }
        })]);
        fixture.write_evidence_records_to_causal_db(&[json!({
            "evidence_id": "evidence:link-1",
            "source_event_id": "action-1",
            "directness": "direct",
            "rule_or_inference_id": "causal_link:l1_exact",
            "resource_id": {"File": {"canonical_path": "/tmp/demo.txt"}},
            "boundary_id": "boundary.file_read",
            "confidence": 0.8,
            "redaction_status": "none",
            "payload_ref": "link-1"
        })]);
        fixture.write_policy_evaluations_to_causal_db(&[json!({
            "evidence_id": "link-1",
            "policy_id": "policy.file.read",
            "boundary_id": "boundary.file_read",
            "claim_state": "observed",
            "reason": "file-read boundary match"
        })]);
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1,
            "evidence_id": "link-1",
            "agent_id": "agent-1",
            "intent_id": "intent-1",
            "candidate_id": "candidate-1",
            "timestamp_ns": 123,
            "confidence": 0.8,
            "match_level": "l1_exact",
            "matched_fields": {"path": "/tmp/demo.txt"},
            "supporting_raw_event_refs": ["action-1"],
            "conversation_id": null,
            "authority_source": "request_history_assistant_tool_use",
            "message_id": "msg-1",
            "source_event_ref": "content-block-1",
            "debug_notes": null
        })]);
        fixture.write_accountability_traceback_replay_to_causal_db(json!({
            "traceback_id": "tb-1",
            "alert_id": "alert-policy-1",
            "chain_node_ids": ["node-root", "node-leaf"],
            "root_cause_node_id": "node-root",
            "supporting_evidence_ids": ["evidence:link-1"],
            "explanation_summary": "minimal accountable chain"
        }));

        let response = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!([
                {
                    "alert_id": "alert-policy-1",
                    "agent_id": "agent-1",
                    "timestamp_ns": 123,
                    "severity": "WARNING",
                    "confidence": 0.8,
                    "category": "policy",
                    "rule_id": "rule-policy-1",
                    "description": "policy-grounded alert",
                    "evidence_source": "causal",
                    "causal_evidence_ids": ["link-1"],
                    "evidence": {
                        "claim_state": "observed",
                        "policy_id": "policy.file.read",
                        "boundary_id": "boundary.file_read",
                        "reason": "file-read boundary match",
                        "accountability_evidence": {
                            "kind": "accountability_evidence",
                            "evidence_refs": ["evidence:link-1"],
                            "directness": "direct",
                            "boundary_id": "boundary.file_read",
                            "correlation_evidence": {"evidence_id": "link-1", "agent_id": "agent-1"}
                        }
                    },
                    "accountability_traceback": {
                        "traceback_id": "tb-1",
                        "alert_id": "alert-policy-1",
                        "chain_node_ids": ["node-root", "node-leaf"],
                        "root_cause_node_id": "node-root",
                        "supporting_evidence_ids": ["evidence:link-1"],
                        "explanation_summary": "minimal accountable chain"
                    },
                    "consistency_warnings": []
                }
            ])
        );
    }

    #[tokio::test]
    #[ignore]
    async fn get_run_alerts_filters_malformed_causal_evidence_fail_closed() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_alerts_db(&[
            json!({
                "alert_id": "alert-empty",
                "agent_id": "agent-1",
                "timestamp_ns": 100,
                "severity": "WARNING",
                "confidence": 0.8,
                "category": "policy",
                "rule_id": "rule-empty",
                "description": "empty refs",
                "evidence_source": "causal",
                "causal_evidence_ids": [],
                "evidence": {"kind": "trace"}
            }),
            json!({
                "alert_id": "alert-missing",
                "agent_id": "agent-1",
                "timestamp_ns": 101,
                "severity": "WARNING",
                "confidence": 0.8,
                "category": "policy",
                "rule_id": "rule-missing",
                "description": "missing refs",
                "evidence_source": "causal",
                "causal_evidence_ids": ["link-missing"],
                "evidence": {"kind": "trace"}
            }),
            json!({
                "alert_id": "alert-mismatch",
                "agent_id": "agent-1",
                "timestamp_ns": 102,
                "severity": "WARNING",
                "confidence": 0.8,
                "category": "policy",
                "rule_id": "rule-mismatch",
                "description": "agent mismatch",
                "evidence_source": "causal",
                "causal_evidence_ids": ["link-foreign"],
                "evidence": {"kind": "trace"}
            }),
        ]);
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1,
            "evidence_id": "link-foreign",
            "agent_id": "other-agent",
            "intent_id": "intent-foreign",
            "candidate_id": "candidate-foreign",
            "timestamp_ns": 102,
            "confidence": 0.9,
            "match_level": "l1_exact",
            "matched_fields": {"path": "/tmp/b"},
            "supporting_raw_event_refs": ["event-2"],
            "conversation_id": null,
            "authority_source": "request_history_assistant_tool_use",
            "message_id": "msg-2",
            "source_event_ref": "content-block-2",
            "debug_notes": null
        })]);

        let response = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(read_json_body(response).await, json!([]));
    }

    #[tokio::test]
    #[ignore]
    async fn get_run_alerts_returns_explicit_error_for_malformed_causal_evidence_ids_json() {
        let fixture = RunFixture::new("test-run-alerts-bad-causal-json");
        fixture.write_summary(json!({
            "run_id": "test-run-alerts-bad-causal-json",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        let conn =
            rusqlite::Connection::open(fixture.run_dir().join("trace.log.alerts.db")).unwrap();
        conn.execute_batch(
            "CREATE TABLE alerts (
                alert_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                severity TEXT NOT NULL,
                confidence REAL NOT NULL,
                category TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                description TEXT NOT NULL,
                evidence_source TEXT NOT NULL,
                causal_evidence_ids_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL
            );",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO alerts (
                alert_id, agent_id, timestamp_ns, severity, confidence,
                category, rule_id, description, evidence_source, causal_evidence_ids_json, evidence_json
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
            rusqlite::params![
                "alert-bad-causal-json",
                "agent-1",
                123i64,
                "WARNING",
                0.8f64,
                "policy",
                "rule-1",
                "bad causal link ids json",
                "causal",
                "{not-json",
                serde_json::to_string(&json!({
                    "kind": "accountability_evidence",
                    "evidence_refs": ["evidence:link-1"],
                    "directness": "direct",
                    "boundary_id": "boundary.file_read",
                    "correlation_evidence": {"evidence_id": "link-1", "agent_id": "agent-1"}
                }))
                .unwrap(),
            ],
        )
        .unwrap();

        let response = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_alerts_json",
                "message": "failed to decode alerts.causal_evidence_ids_json for replay",
                "details": {
                    "column": "causal_evidence_ids_json",
                    "alert_id": "alert-bad-causal-json"
                }
            })
        );
    }

    #[tokio::test]
    #[ignore]
    async fn get_run_alerts_returns_explicit_error_for_malformed_authoritative_causal_links_json() {
        let fixture = RunFixture::new("test-run-alerts-bad-authoritative-causal-json");
        fixture.write_summary(json!({
            "run_id": "test-run-alerts-bad-authoritative-causal-json",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_alerts_db(&[json!({
            "alert_id": "alert-1",
            "agent_id": "agent-1",
            "timestamp_ns": 123,
            "severity": "WARNING",
            "confidence": 0.8,
            "category": "policy",
            "rule_id": "rule-1",
            "description": "alert with malformed authoritative causal artifact",
            "evidence_source": "causal",
            "causal_evidence_ids": ["bad-json-link"],
            "evidence": {
                "kind": "accountability_evidence",
                "evidence_refs": ["evidence:bad-json-link"],
                "directness": "direct",
                "boundary_id": "boundary.file_read",
                "correlation_evidence": {"evidence_id": "bad-json-link", "agent_id": "agent-1"}
            }
        })]);
        let conn =
            rusqlite::Connection::open(fixture.run_dir().join("trace.log.causal.db")).unwrap();
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
        conn.execute(
            "INSERT INTO correlation_evidence (
                schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns,
                confidence, match_level, matched_fields_json, supporting_raw_event_refs_json,
                conversation_id, authority_source, message_id, source_event_ref, debug_notes
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            rusqlite::params![
                1i64,
                "bad-json-link",
                "agent-1",
                "intent-1",
                "candidate-1",
                123i64,
                0.8f64,
                "l1_exact",
                "{not-json",
                serde_json::to_string(&json!(["event-1"])).unwrap(),
                rusqlite::types::Null,
                "request_history_assistant_tool_use",
                "msg-1",
                "content-block-1",
                rusqlite::types::Null,
            ],
        )
        .unwrap();

        let response = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_correlation_evidence_json",
                "message": "failed to decode correlation_evidence.matched_fields_json for replay",
                "details": {
                    "column": "matched_fields_json",
                    "evidence_id": "bad-json-link"
                }
            })
        );
    }

    #[tokio::test]
    #[ignore]
    async fn get_run_alerts_returns_explicit_error_for_malformed_evidence_json() {
        let fixture = RunFixture::new("test-run-alerts-bad-evidence-json");
        fixture.write_summary(json!({
            "run_id": "test-run-alerts-bad-evidence-json",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        let conn =
            rusqlite::Connection::open(fixture.run_dir().join("trace.log.alerts.db")).unwrap();
        conn.execute_batch(
            "CREATE TABLE alerts (
                alert_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                severity TEXT NOT NULL,
                confidence REAL NOT NULL,
                category TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                description TEXT NOT NULL,
                evidence_source TEXT NOT NULL,
                causal_evidence_ids_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL
            );",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO alerts (
                alert_id, agent_id, timestamp_ns, severity, confidence,
                category, rule_id, description, evidence_source, causal_evidence_ids_json, evidence_json
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
            rusqlite::params![
                "alert-bad-evidence-json",
                "agent-1",
                123i64,
                "WARNING",
                0.8f64,
                "policy",
                "rule-1",
                "bad evidence json",
                "causal",
                serde_json::to_string(&json!(["link-1"])).unwrap(),
                "{not-json",
            ],
        )
        .unwrap();

        let response = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_alerts_json",
                "message": "failed to decode alerts.evidence_json for replay",
                "details": {
                    "column": "evidence_json",
                    "alert_id": "alert-bad-evidence-json"
                }
            })
        );
    }

    #[tokio::test]
    async fn run_summary_reports_replayed_correlation_evidence_source_when_replayed_artifact_present()
     {
        let fixture = RunFixture::new("test-run-summary-replayed-source");
        fixture.write_summary(json!({
            "run_id": "test-run-summary-replayed-source",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_replayed_correlation_evidence_db(&[]);

        let response = serve_run_summary_api(fixture.run_dir(), "test-run-summary-replayed-source")
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "run_id": "test-run-summary-replayed-source",
                "scenario_ids": ["scenario-a"],
                "run_status": "completed",
                "artifact_availability": {
                    "evaluation": false,
                    "alerts": false,
                    "correlation_evidence": true,
                    "events": false
                },
                "artifact_sources": {
                    "correlation_evidence": "replayed"
                },
                "warnings": []
            })
        );
    }

    #[tokio::test]
    async fn run_summary_surfaces_accountability_audit_summary_when_present() {
        let fixture = RunFixture::new("test-run-summary-accountability");
        fixture.write_summary(json!({
            "run_id": "test-run-summary-accountability",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_alerts_db(&[]);
        fixture.write_accountability_audit_summary(json!({
            "run_id": "test-run-summary-accountability",
            "alert_count": 2,
            "traceback_count": 1,
            "summary_text": "1 of 2 alerts have replayable accountability tracebacks"
        }));

        let response = serve_run_summary_api(fixture.run_dir(), "test-run-summary-accountability")
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "run_id": "test-run-summary-accountability",
                "scenario_ids": ["scenario-a"],
                "run_status": "completed",
                "artifact_availability": {
                    "evaluation": false,
                    "alerts": true,
                    "correlation_evidence": false,
                    "events": false
                },
                "artifact_sources": {
                    "correlation_evidence": "missing"
                },
                "accountability_summary": {
                    "run_id": "test-run-summary-accountability",
                    "alert_count": 2,
                    "traceback_count": 1,
                    "summary_text": "1 of 2 alerts have replayable accountability tracebacks"
                },
                "warnings": []
            })
        );
    }

    #[tokio::test]
    async fn run_summary_ignores_mismatched_accountability_audit_summary_rows() {
        let fixture = RunFixture::new("test-run-summary-accountability-mismatch");
        fixture.write_summary(json!({
            "run_id": "test-run-summary-accountability-mismatch",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_alerts_db(&[]);
        fixture.write_raw_accountability_audit_summary_table(&[json!({
            "run_id": "some-other-run",
            "alert_count": 9,
            "traceback_count": 8,
            "summary_text": "wrong run summary"
        })]);

        let response = serve_run_summary_api(
            fixture.run_dir(),
            "test-run-summary-accountability-mismatch",
        )
        .await
        .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "run_id": "test-run-summary-accountability-mismatch",
                "scenario_ids": ["scenario-a"],
                "run_status": "completed",
                "artifact_availability": {
                    "evaluation": false,
                    "alerts": true,
                    "correlation_evidence": false,
                    "events": false
                },
                "artifact_sources": {
                    "correlation_evidence": "missing"
                },
                "warnings": []
            })
        );
    }

    #[tokio::test]
    async fn run_summary_returns_explicit_error_for_negative_accountability_audit_counts() {
        let fixture = RunFixture::new("test-run-summary-accountability-negative-counts");
        fixture.write_summary(json!({
            "run_id": "test-run-summary-accountability-negative-counts",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_alerts_db(&[]);
        let conn =
            rusqlite::Connection::open(fixture.run_dir().join("trace.log.causal.db")).unwrap();
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS accountability_audit_summaries (
                run_id TEXT PRIMARY KEY,
                alert_count INTEGER NOT NULL,
                traceback_count INTEGER NOT NULL,
                summary_text TEXT NOT NULL
            );",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO accountability_audit_summaries (run_id, alert_count, traceback_count, summary_text) VALUES (?1, ?2, ?3, ?4)",
            rusqlite::params![
                "test-run-summary-accountability-negative-counts",
                -1i64,
                1i64,
                "bad negative count",
            ],
        )
        .unwrap();

        let response = serve_run_summary_api(
            fixture.run_dir(),
            "test-run-summary-accountability-negative-counts",
        )
        .await
        .unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_accountability_summary",
                "message": "failed to decode accountability_audit_summaries row for replay",
                "details": {
                    "run_id": "test-run-summary-accountability-negative-counts"
                }
            })
        );
    }

    #[tokio::test]
    async fn run_summary_returns_explicit_error_for_malformed_accountability_audit_summary_row() {
        let fixture = RunFixture::new("test-run-summary-accountability-malformed");
        fixture.write_summary(json!({
            "run_id": "test-run-summary-accountability-malformed",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_alerts_db(&[]);
        let conn =
            rusqlite::Connection::open(fixture.run_dir().join("trace.log.causal.db")).unwrap();
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS accountability_audit_summaries (
                run_id TEXT PRIMARY KEY,
                alert_count INTEGER NOT NULL,
                traceback_count INTEGER NOT NULL,
                summary_text BLOB NOT NULL
            );",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO accountability_audit_summaries (run_id, alert_count, traceback_count, summary_text) VALUES (?1, ?2, ?3, ?4)",
            rusqlite::params![
                "test-run-summary-accountability-malformed",
                1i64,
                1i64,
                rusqlite::types::Value::Blob(vec![0, 159, 146, 150]),
            ],
        )
        .unwrap();

        let response = serve_run_summary_api(
            fixture.run_dir(),
            "test-run-summary-accountability-malformed",
        )
        .await
        .unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_accountability_summary",
                "message": "failed to decode accountability_audit_summaries row for replay",
                "details": {
                    "run_id": "test-run-summary-accountability-malformed"
                }
            })
        );
    }

    #[tokio::test]
    async fn run_summary_does_not_emit_divergence_warning_when_persisted_and_replayed_causal_artifacts_match()
     {
        let fixture = RunFixture::new("test-run-summary-no-divergence");
        fixture.write_summary(json!({
            "run_id": "test-run-summary-no-divergence",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        let shared = json!({
            "schema_version": 1,
            "evidence_id": "shared-link",
            "agent_id": "agent-shared",
            "intent_id": "intent-shared",
            "candidate_id": "candidate-shared",
            "timestamp_ns": 100,
            "confidence": 0.9,
            "match_level": "l1_exact",
            "matched_fields": {"path": "/tmp/shared"},
            "supporting_raw_event_refs": ["event-shared"],
            "conversation_id": null,
            "authority_source": null,
            "message_id": null,
            "source_event_ref": null,
            "debug_notes": null
        });
        fixture.write_correlation_evidence_db(&[shared.clone()]);
        fixture.write_replayed_correlation_evidence_db(&[shared]);

        let response = serve_run_summary_api(fixture.run_dir(), "test-run-summary-no-divergence")
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "run_id": "test-run-summary-no-divergence",
                "scenario_ids": ["scenario-a"],
                "run_status": "completed",
                "artifact_availability": {
                    "evaluation": false,
                    "alerts": false,
                    "correlation_evidence": true,
                    "events": false
                },
                "artifact_sources": {
                    "correlation_evidence": "replayed"
                },
                "warnings": []
            })
        );
    }

    #[tokio::test]
    async fn run_summary_reports_stale_causal_artifact_divergence_warning_when_persisted_and_replayed_causal_artifacts_differ()
     {
        let fixture = RunFixture::new("test-run-summary-stale-warning");
        fixture.write_summary(json!({
            "run_id": "test-run-summary-stale-warning",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1,
            "evidence_id": "legacy-link",
            "agent_id": "agent-legacy",
            "intent_id": "intent-legacy",
            "candidate_id": "candidate-legacy",
            "timestamp_ns": 100,
            "confidence": 0.5,
            "match_level": "l1_exact",
            "matched_fields": {"path": "/tmp/legacy"},
            "supporting_raw_event_refs": ["event-legacy"],
            "conversation_id": null,
            "authority_source": null,
            "message_id": null,
            "source_event_ref": null,
            "debug_notes": null
        })]);
        fixture.write_replayed_correlation_evidence_db(&[json!({
            "schema_version": 1,
            "evidence_id": "replayed-link",
            "agent_id": "agent-replayed",
            "intent_id": "intent-replayed",
            "candidate_id": "candidate-replayed",
            "timestamp_ns": 200,
            "confidence": 0.95,
            "match_level": "l1_exact",
            "matched_fields": {"path": "/tmp/replayed"},
            "supporting_raw_event_refs": ["event-replayed"],
            "conversation_id": null,
            "authority_source": null,
            "message_id": null,
            "source_event_ref": null,
            "debug_notes": null
        })]);

        let response = serve_run_summary_api(fixture.run_dir(), "test-run-summary-stale-warning")
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "run_id": "test-run-summary-stale-warning",
                "scenario_ids": ["scenario-a"],
                "run_status": "completed",
                "artifact_availability": {
                    "evaluation": false,
                    "alerts": false,
                    "correlation_evidence": true,
                    "events": false
                },
                "artifact_sources": {
                    "correlation_evidence": "replayed"
                },
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
    async fn get_run_correlation_evidence_prefers_replayed_causal_sqlite_db_when_present() {
        let fixture = RunFixture::new("test-run-replayed-preferred");
        fixture.write_summary(json!({
            "run_id": "test-run-replayed-preferred",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1,
            "evidence_id": "legacy-link",
            "agent_id": "agent-legacy",
            "intent_id": "intent-legacy",
            "candidate_id": "candidate-legacy",
            "timestamp_ns": 100,
            "confidence": 0.5,
            "match_level": "l1_exact",
            "matched_fields": {"path": "/tmp/legacy"},
            "supporting_raw_event_refs": ["event-legacy"],
            "conversation_id": null,
            "authority_source": null,
            "message_id": null,
            "source_event_ref": null,
            "debug_notes": null
        })]);
        fixture.write_replayed_correlation_evidence_db(&[json!({
            "schema_version": 1,
            "evidence_id": "replayed-link",
            "agent_id": "agent-replayed",
            "intent_id": "intent-replayed",
            "candidate_id": "candidate-replayed",
            "timestamp_ns": 200,
            "confidence": 0.95,
            "match_level": "l1_exact",
            "matched_fields": {"path": "/tmp/replayed"},
            "supporting_raw_event_refs": ["event-replayed"],
            "conversation_id": "conv-r",
            "authority_source": "request_history_assistant_tool_use",
            "message_id": "msg-r",
            "source_event_ref": "source-r",
            "debug_notes": "replayed"
        })]);

        let response = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        assert_eq!(body["authority"], "supporting");
        assert_eq!(
            body["correlation_evidence"],
            json!([
                {
                    "schema_version": 1,
                    "evidence_id": "replayed-link",
                    "agent_id": "agent-replayed",
                    "intent_id": "intent-replayed",
                    "candidate_id": "candidate-replayed",
                    "timestamp_ns": 200,
                    "confidence": 0.95,
                    "match_level": "l1_exact",
                    "matched_fields": {"path": "/tmp/replayed"},
                    "supporting_raw_event_refs": ["event-replayed"],
                    "conversation_id": "conv-r",
                    "authority_source": "request_history_assistant_tool_use",
                    "message_id": "msg-r",
                    "source_event_ref": "source-r",
                    "debug_notes": "replayed"
                }
            ])
        );
    }

    #[tokio::test]
    async fn get_run_correlation_evidence_returns_json_rows_from_causal_sqlite_db() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a", "scenario-b"],
            "status": "completed"
        }));
        fixture.write_correlation_evidence_db(&[
            json!({
                "schema_version": 1,
                "evidence_id": "link-1",
                "agent_id": "agent-1",
                "intent_id": "intent-1",
                "candidate_id": "candidate-1",
                "timestamp_ns": 123,
                "confidence": 0.8,
                "match_level": "l1_exact",
                "matched_fields": {"path": "/tmp/a"},
                "supporting_raw_event_refs": ["event-1"],
                "conversation_id": "conv-1",
                "authority_source": "request_history_assistant_tool_use",
                "message_id": "msg-1",
                "source_event_ref": "content-block-1",
                "debug_notes": "note-1"
            }),
            json!({
                "schema_version": 1,
                "evidence_id": "link-2",
                "agent_id": "agent-2",
                "intent_id": "intent-2",
                "candidate_id": "candidate-2",
                "timestamp_ns": 456,
                "confidence": 0.95,
                "match_level": "l2_basename",
                "matched_fields": null,
                "supporting_raw_event_refs": null,
                "conversation_id": null,
                "authority_source": null,
                "message_id": null,
                "source_event_ref": null,
                "debug_notes": null
            }),
        ]);

        let response = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/json"
        );
        let body = read_json_body(response).await;
        assert_eq!(body["authority"], "supporting");
        assert!(body.get("correlation_evidence").is_some());
        assert!(body.get("causal_links").is_none());
        assert_eq!(
            body["correlation_evidence"],
            json!([
                {
                    "schema_version": 1,
                    "evidence_id": "link-1",
                    "agent_id": "agent-1",
                    "intent_id": "intent-1",
                    "candidate_id": "candidate-1",
                    "timestamp_ns": 123,
                    "confidence": 0.8,
                    "match_level": "l1_exact",
                    "matched_fields": {"path": "/tmp/a"},
                    "supporting_raw_event_refs": ["event-1"],
                    "conversation_id": "conv-1",
                    "authority_source": "request_history_assistant_tool_use",
                    "message_id": "msg-1",
                    "source_event_ref": "content-block-1",
                    "debug_notes": "note-1"
                },
                {
                    "schema_version": 1,
                    "evidence_id": "link-2",
                    "agent_id": "agent-2",
                    "intent_id": "intent-2",
                    "candidate_id": "candidate-2",
                    "timestamp_ns": 456,
                    "confidence": 0.95,
                    "match_level": "l2_basename",
                    "matched_fields": null,
                    "supporting_raw_event_refs": null,
                    "conversation_id": null,
                    "authority_source": null,
                    "message_id": null,
                    "source_event_ref": null,
                    "debug_notes": null
                }
            ])
        );
    }

    #[tokio::test]
    async fn run_graph_analysis_includes_violation_pattern_artifacts() {
        let fixture = RunFixture::new("test-run-graph-patterns");
        fixture.write_summary(json!({
            "run_id": "test-run-graph-patterns",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_correlation_evidence_db(&[
            json!({
                "schema_version": 1,
                "evidence_id": "link-read",
                "agent_id": "agent-1",
                "intent_id": "intent-1",
                "candidate_id": "candidate-1",
                "timestamp_ns": 100,
                "confidence": 0.9,
                "match_level": "l1_exact",
                "matched_fields": {
                    "candidate_type": "legacy_irrelevant",
                    "target": "/tmp/shared.md",
                    "policy_id": "policy.file.read",
                    "boundary_id": "boundary.file_read"
                },
                "supporting_raw_event_refs": ["event-1"],
                "conversation_id": null,
                "authority_source": null,
                "message_id": null,
                "source_event_ref": null,
                "debug_notes": null
            }),
            json!({
                "schema_version": 1,
                "evidence_id": "link-exec",
                "agent_id": "agent-1",
                "intent_id": "intent-2",
                "candidate_id": "candidate-2",
                "timestamp_ns": 200,
                "confidence": 0.9,
                "match_level": "l1_exact",
                "matched_fields": {
                    "candidate_type": "legacy_irrelevant_exec",
                    "target": "/usr/bin/bash",
                    "policy_id": "policy.process.exec",
                    "boundary_id": "boundary.process_exec"
                },
                "supporting_raw_event_refs": ["event-2"],
                "conversation_id": null,
                "authority_source": null,
                "message_id": null,
                "source_event_ref": null,
                "debug_notes": null
            }),
        ]);

        let response = serve_run_graph_analysis_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "authoritative_source": "legacy_links",
                "evidence_kind": "correlation_evidence",
                "evidence_authority": "supporting",
                "authority": "supporting",
                "accountability_traceback_status": "unavailable",
                "warnings": ["legacy causal-link analysis is supporting evidence, not authoritative accountability traceback"],
                "snapshot": null,
                "graph_summary": {
                    "total_links": 2,
                    "compressed_ratio": 1.0,
                    "summary": "1 agent(s) performed 2 distinct operation type(s) across 2 causal link(s).\n  [agent-1] 1 × legacy_irrelevant (targets: /tmp/shared.md)\n  [agent-1] 1 × legacy_irrelevant_exec (targets: /usr/bin/bash)",
                    "nodes": [
                        {
                            "agent_id": "agent-1",
                            "operation_type": "legacy_irrelevant",
                            "count": 1,
                            "first_ns": 100,
                            "last_ns": 100,
                            "representative_evidence_id": "link-read",
                            "targets": ["/tmp/shared.md"]
                        },
                        {
                            "agent_id": "agent-1",
                            "operation_type": "legacy_irrelevant_exec",
                            "count": 1,
                            "first_ns": 200,
                            "last_ns": 200,
                            "representative_evidence_id": "link-exec",
                            "targets": ["/usr/bin/bash"]
                        }
                    ]
                },
                "legacy_correlation_ranking": [
                    {
                        "agent_id": "agent-1",
                        "root_evidence_id": "link-read",
                        "score": 0.949999988079071,
                        "downstream_count": 1,
                        "explanation": "Root cause: legacy_irrelevant operation on '/tmp/shared.md' (confidence=0.90, 1 downstream links within 30s)"
                    },
                    {
                        "agent_id": "agent-1",
                        "root_evidence_id": "link-exec",
                        "score": 0.44999998807907104,
                        "downstream_count": 0,
                        "explanation": "Root cause: legacy_irrelevant_exec operation on '/usr/bin/bash' (confidence=0.90, 0 downstream links within 30s)"
                    }
                ],
                "violation_patterns": [
                    {
                        "template_id": "shared_artifact_read_then_exec",
                        "agent_id": "agent-1",
                        "matched_evidence_ids": ["link-read", "link-exec"],
                        "matched_policies": ["policy.file.read", "policy.process.exec"],
                        "matched_boundaries": ["boundary.file_read", "boundary.process_exec"],
                        "matched_targets": ["/tmp/shared.md", "/usr/bin/bash"],
                        "severity": "warning"
                    }
                ]
            })
        );
    }

    #[tokio::test]
    async fn test_graph_analysis_includes_snapshot_when_available() {
        let fixture = RunFixture::new("test-run-graph-snapshot");
        fixture.write_summary(json!({
            "run_id": "test-run-graph-snapshot",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1,
            "evidence_id": "link-1",
            "agent_id": "agent-1",
            "intent_id": "intent-1",
            "candidate_id": "candidate-1",
            "timestamp_ns": 100,
            "confidence": 0.9,
            "match_level": "l1_exact",
            "matched_fields": {"target": "/etc/shadow"},
            "supporting_raw_event_refs": ["event-1"],
            "conversation_id": null,
            "authority_source": null,
            "message_id": null,
            "source_event_ref": null,
            "debug_notes": null
        })]);
        fixture.write_causal_graph_snapshot(json!({
            "snapshot_id": "snap-1",
            "timestamp_ns": 100,
            "agent_id": "agent-1",
            "link_ids": ["link-1"],
            "evidence_ids": ["evidence:link-1"],
            "policy_ids": ["policy.file.read"],
            "boundary_ids": ["boundary.file_read"],
            "claim_states": ["observed"],
            "traceback_ids": ["tb-1"],
            "violation_match_ids": [],
            "node_count": 1,
            "edge_count": 1,
            "snapshot_kind": "accountability",
        }));

        let response = serve_run_graph_analysis_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        assert_eq!(body["authoritative_source"], "accountability_snapshot");
        assert!(
            body.get("snapshot").is_some(),
            "response must include snapshot section"
        );
        assert_eq!(body["snapshot"]["snapshot_id"], "snap-1");
        assert_eq!(body["snapshot"]["snapshot_kind"], "accountability");
        assert_eq!(body["snapshot"]["agent_id"], "agent-1");
        assert_eq!(body["snapshot"]["evidence_ids"], json!(["evidence:link-1"]));
        assert!(
            body.get("graph_summary").is_some(),
            "response must still include graph_summary"
        );
        assert_eq!(body["authority"], "authoritative");
        assert_eq!(body["accountability_traceback_status"], "available");
        assert_eq!(body["warnings"], json!([]));
        assert!(
            body.get("legacy_correlation_ranking").is_some(),
            "correlation ranking remains supporting evidence, even when a snapshot exists"
        );
        assert!(
            body.get("root_causes").is_none(),
            "snapshot-mode graph response must not expose legacy ranking as root_causes"
        );
    }

    #[tokio::test]
    async fn test_graph_analysis_degrades_truthfully_when_no_snapshot() {
        let fixture = RunFixture::new("test-run-graph-legacy");
        fixture.write_summary(json!({
            "run_id": "test-run-graph-legacy",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1,
            "evidence_id": "link-1",
            "agent_id": "agent-1",
            "intent_id": "intent-1",
            "candidate_id": "candidate-1",
            "timestamp_ns": 100,
            "confidence": 0.9,
            "match_level": "l1_exact",
            "matched_fields": {"target": "/etc/shadow"},
            "supporting_raw_event_refs": ["event-1"],
            "conversation_id": null,
            "authority_source": null,
            "message_id": null,
            "source_event_ref": null,
            "debug_notes": null
        })]);
        // No snapshot written — legacy-only data

        let response = serve_run_graph_analysis_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        assert_eq!(body["authoritative_source"], "legacy_links");
        assert_eq!(body["authority"], "supporting");
        assert_eq!(body["evidence_kind"], "correlation_evidence");
        assert_eq!(body["evidence_authority"], "supporting");
        assert_eq!(body["accountability_traceback_status"], "unavailable");
        assert!(body["warnings"].as_array().unwrap().iter().any(|warning| {
            warning
                .as_str()
                .unwrap_or_default()
                .contains("legacy causal-link analysis is supporting evidence, not authoritative accountability traceback")
        }));
        assert!(
            body["snapshot"].is_null(),
            "snapshot must be null for legacy-only runs"
        );
        assert!(
            body.get("graph_summary").is_some(),
            "graph_summary still present in legacy mode"
        );
        assert!(
            body.get("legacy_correlation_ranking").is_some(),
            "legacy ranking must be renamed away from root_causes in legacy mode"
        );
        assert!(
            body.get("root_causes").is_none(),
            "legacy-only response must not expose root_causes as authoritative"
        );
    }

    #[tokio::test]
    async fn test_snapshots_api_returns_snapshot_list() {
        let fixture = RunFixture::new("test-run-snapshots-api");
        fixture.write_summary(
            json!({"run_id": "test-run-snapshots-api", "scenario_ids": [], "status": "completed"}),
        );
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1, "evidence_id": "link-1", "agent_id": "agent-1",
            "intent_id": "intent-1", "candidate_id": "candidate-1", "timestamp_ns": 100,
            "confidence": 0.9, "match_level": "l1_exact",
            "matched_fields": {"target": "/etc/shadow"},
            "supporting_raw_event_refs": ["event-1"],
            "conversation_id": null, "authority_source": null, "message_id": null,
            "source_event_ref": null, "debug_notes": null
        })]);
        fixture.write_causal_graph_snapshot(json!({
            "snapshot_id": "snap-api-1", "timestamp_ns": 100, "agent_id": "agent-1",
            "link_ids": ["link-1"], "evidence_ids": [], "policy_ids": [], "boundary_ids": [],
            "claim_states": [], "traceback_ids": [], "violation_match_ids": [],
            "node_count": 1, "edge_count": 1, "snapshot_kind": "accountability",
        }));
        fixture.write_causal_graph_snapshot(json!({
            "snapshot_id": "snap-api-2", "timestamp_ns": 200, "agent_id": "agent-1",
            "link_ids": ["link-1", "link-2"], "evidence_ids": [], "policy_ids": [],
            "boundary_ids": [], "claim_states": [], "traceback_ids": [],
            "violation_match_ids": [], "node_count": 2, "edge_count": 1,
            "snapshot_kind": "accountability",
        }));

        let response = serve_run_snapshots_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        assert_eq!(body["total_count"], 2);
        assert_eq!(body["snapshots"].as_array().unwrap().len(), 2);
        assert_eq!(body["snapshots"][0]["snapshot_id"], "snap-api-1");
        assert_eq!(body["snapshots"][1]["snapshot_id"], "snap-api-2");
        assert!(body["snapshots"][0]["evidence_ids"].is_array());
    }

    #[tokio::test]
    async fn test_snapshots_api_returns_empty_when_no_snapshots() {
        let fixture = RunFixture::new("test-run-no-snap");
        fixture.write_summary(
            json!({"run_id": "test-run-no-snap", "scenario_ids": [], "status": "completed"}),
        );
        fixture.write_correlation_evidence_db(&[]);

        let response = serve_run_snapshots_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        assert_eq!(body["total_count"], 0);
        assert!(body["snapshots"].as_array().unwrap().is_empty());
    }

    #[tokio::test]
    async fn test_snapshot_by_id_api_returns_full_snapshot() {
        let fixture = RunFixture::new("test-run-snap-by-id");
        fixture.write_summary(
            json!({"run_id": "test-run-snap-by-id", "scenario_ids": [], "status": "completed"}),
        );
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1, "evidence_id": "link-1", "agent_id": "agent-1",
            "intent_id": "intent-1", "candidate_id": "candidate-1", "timestamp_ns": 100,
            "confidence": 0.9, "match_level": "l1_exact",
            "matched_fields": {"target": "/etc/shadow"},
            "supporting_raw_event_refs": ["event-1"],
            "conversation_id": null, "authority_source": null, "message_id": null,
            "source_event_ref": null, "debug_notes": null
        })]);
        fixture.write_causal_graph_snapshot(json!({
            "snapshot_id": "snap-by-id-1", "timestamp_ns": 100, "agent_id": "agent-1",
            "link_ids": ["link-1"], "evidence_ids": ["ev-1"], "policy_ids": ["pol-1"],
            "boundary_ids": ["bound-1"], "claim_states": ["observed"],
            "traceback_ids": ["tb-1"], "violation_match_ids": ["vm-1"],
            "node_count": 2, "edge_count": 1, "snapshot_kind": "accountability",
        }));

        let response = serve_run_snapshot_by_id_api(fixture.run_dir(), "snap-by-id-1")
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        assert_eq!(body["snapshot_id"], "snap-by-id-1");
        assert_eq!(body["agent_id"], "agent-1");
        assert_eq!(body["evidence_ids"], json!(["ev-1"]));
        assert_eq!(body["policy_ids"], json!(["pol-1"]));
        assert_eq!(body["boundary_ids"], json!(["bound-1"]));
        assert_eq!(body["claim_states"], json!(["observed"]));
        assert_eq!(body["traceback_ids"], json!(["tb-1"]));
        assert_eq!(body["violation_match_ids"], json!(["vm-1"]));
        assert_eq!(body["node_count"], 2);
        assert_eq!(body["edge_count"], 1);
        assert_eq!(body["snapshot_kind"], "accountability");
    }

    #[tokio::test]
    async fn test_snapshot_by_id_api_returns_not_found_for_missing_snapshot() {
        let fixture = RunFixture::new("test-run-snap-by-id-miss");
        fixture.write_summary(json!({"run_id": "test-run-snap-by-id-miss", "scenario_ids": [], "status": "completed"}));
        fixture.write_correlation_evidence_db(&[]);

        let response = serve_run_snapshot_by_id_api(fixture.run_dir(), "nonexistent-snap")
            .await
            .unwrap();
        let status = response.status();
        let body = read_json_body(response).await;
        assert_eq!(status, StatusCode::NOT_FOUND);
        assert!(
            body["error"] == "snapshots_unavailable" || body["error"] == "snapshot_not_found",
            "expected snapshots_unavailable or snapshot_not_found, got error={}",
            body["error"]
        );
    }

    #[tokio::test]
    async fn test_snapshot_diff_api_returns_diff_between_consecutive_snapshots() {
        let fixture = RunFixture::new("test-run-snapshot-diff");
        fixture.write_summary(
            json!({"run_id": "test-run-snapshot-diff", "scenario_ids": [], "status": "completed"}),
        );
        fixture.write_correlation_evidence_db(&[
            json!({
                "schema_version": 1, "evidence_id": "link-1", "agent_id": "agent-1",
                "intent_id": "intent-1", "candidate_id": "candidate-1", "timestamp_ns": 100,
                "confidence": 0.9, "match_level": "l1_exact",
                "matched_fields": {"target": "/file-a"},
                "supporting_raw_event_refs": ["event-1"],
                "conversation_id": null, "authority_source": null, "message_id": null,
                "source_event_ref": null, "debug_notes": null
            }),
            json!({
                "schema_version": 1, "evidence_id": "link-2", "agent_id": "agent-1",
                "intent_id": "intent-2", "candidate_id": "candidate-2", "timestamp_ns": 200,
                "confidence": 0.9, "match_level": "l1_exact",
                "matched_fields": {"target": "/file-b"},
                "supporting_raw_event_refs": ["event-2"],
                "conversation_id": null, "authority_source": null, "message_id": null,
                "source_event_ref": null, "debug_notes": null
            }),
        ]);
        // First snapshot: only link-1
        fixture.write_causal_graph_snapshot(json!({
            "snapshot_id": "snap-diff-1", "timestamp_ns": 100, "agent_id": "agent-1",
            "link_ids": ["link-1"], "evidence_ids": [], "policy_ids": [], "boundary_ids": [],
            "claim_states": [], "traceback_ids": [], "violation_match_ids": [],
            "node_count": 1, "edge_count": 1, "snapshot_kind": "accountability",
        }));
        // Second snapshot: link-1 and link-2 (the diff should show link-2 as added)
        fixture.write_causal_graph_snapshot(json!({
            "snapshot_id": "snap-diff-2", "timestamp_ns": 200, "agent_id": "agent-1",
            "link_ids": ["link-1", "link-2"], "evidence_ids": [], "policy_ids": [],
            "boundary_ids": [], "claim_states": [], "traceback_ids": [],
            "violation_match_ids": [], "node_count": 2, "edge_count": 1,
            "snapshot_kind": "accountability",
        }));

        let response = serve_run_snapshot_diff_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        assert_eq!(body["diff_available"], true);
        assert_eq!(body["before"]["snapshot_id"], "snap-diff-1");
        assert_eq!(body["after"]["snapshot_id"], "snap-diff-2");
        // Full-field diff: evidence ids remain empty; link ids are stored separately in fixtures
        assert!(
            body["field_diffs"]["evidence_ids"]["added"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        assert!(
            body["field_diffs"]["evidence_ids"]["removed"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        assert_eq!(body["field_diffs"]["evidence_ids"]["added_count"], 0);
        assert_eq!(body["field_diffs"]["evidence_ids"]["removed_count"], 0);
        // Full-field diff: other fields are empty for both
        assert_eq!(body["field_diffs"]["policy_ids"]["added_count"], 0);
        assert_eq!(body["field_diffs"]["boundary_ids"]["added_count"], 0);
        assert_eq!(body["field_diffs"]["traceback_ids"]["added_count"], 0);
        assert_eq!(body["field_diffs"]["violation_match_ids"]["added_count"], 0);
    }

    #[tokio::test]
    async fn test_snapshot_diff_api_returns_unavailable_when_insufficient_snapshots() {
        let fixture = RunFixture::new("test-run-diff-unavail");
        fixture.write_summary(
            json!({"run_id": "test-run-diff-unavail", "scenario_ids": [], "status": "completed"}),
        );
        fixture.write_correlation_evidence_db(&[]);

        let response = serve_run_snapshot_diff_api(fixture.run_dir())
            .await
            .unwrap();
        let status = response.status();
        let body = read_json_body(response).await;
        assert!(
            status == StatusCode::OK || status == StatusCode::NOT_FOUND,
            "expected OK or NOT_FOUND, got {}",
            status
        );
        if status == StatusCode::OK {
            assert_eq!(body["diff_available"], false);
        }
    }

    #[tokio::test]
    async fn test_snapshot_chain_api_returns_hash_chain() {
        let fixture = RunFixture::new("test-run-snap-chain");
        fixture.write_summary(
            json!({"run_id": "test-run-snap-chain", "scenario_ids": [], "status": "completed"}),
        );
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1, "evidence_id": "link-1", "agent_id": "agent-1",
            "intent_id": "intent-1", "candidate_id": "candidate-1", "timestamp_ns": 100,
            "confidence": 0.9, "match_level": "l1_exact",
            "matched_fields": {"target": "/file-a"},
            "supporting_raw_event_refs": ["event-1"],
            "conversation_id": null, "authority_source": null, "message_id": null,
            "source_event_ref": null, "debug_notes": null
        })]);
        // Two snapshots to form a chain
        fixture.write_causal_graph_snapshot(json!({
            "snapshot_id": "chain-1", "timestamp_ns": 100, "agent_id": "agent-1",
            "link_ids": ["link-1"], "evidence_ids": [], "policy_ids": [], "boundary_ids": [],
            "claim_states": [], "traceback_ids": [], "violation_match_ids": [],
            "node_count": 1, "edge_count": 1, "snapshot_kind": "accountability",
        }));
        fixture.write_causal_graph_snapshot(json!({
            "snapshot_id": "chain-2", "timestamp_ns": 200, "agent_id": "agent-1",
            "link_ids": ["link-1", "link-2"], "evidence_ids": [], "policy_ids": [],
            "boundary_ids": [], "claim_states": [], "traceback_ids": [],
            "violation_match_ids": [], "node_count": 2, "edge_count": 1,
            "snapshot_kind": "accountability",
        }));

        let response = serve_run_snapshot_chain_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        assert_eq!(body["chain_length"], 2);
        assert_eq!(body["integrity"], "ok");
        assert!(body.get("root_hash_hex").is_some(), "must have root hash");
        assert_eq!(body["algorithm"], "SipHash-1-3");

        // Each chain entry must have a hash
        let entries = body["chain_entries"].as_array().unwrap();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0]["snapshot_id"], "chain-1");
        assert_eq!(entries[1]["snapshot_id"], "chain-2");
        // First entry prev_hash is all zeros (genesis)
        assert_eq!(entries[0]["prev_hash_hex"], "0000000000000000");
        // Hashes must be 16 hex chars (8 bytes)
        assert_eq!(entries[0]["hash_hex"].as_str().unwrap().len(), 16);
        // Second entry's prev_hash should match first entry's hash
        assert_eq!(entries[1]["prev_hash_hex"], entries[0]["hash_hex"]);
        // Chain lengths increase monotonically
        assert_eq!(entries[0]["chain_length"], 1);
        assert_eq!(entries[1]["chain_length"], 2);

        // Root hash must match the last entry's hash
        assert_eq!(body["root_hash_hex"], entries[1]["hash_hex"]);
    }

    #[tokio::test]
    async fn test_snapshot_chain_api_returns_empty_for_no_snapshots() {
        let fixture = RunFixture::new("test-run-chain-empty");
        fixture.write_summary(
            json!({"run_id": "test-run-chain-empty", "scenario_ids": [], "status": "completed"}),
        );
        fixture.write_correlation_evidence_db(&[]);

        let response = serve_run_snapshot_chain_api(fixture.run_dir())
            .await
            .unwrap();
        let status = response.status();
        let body = read_json_body(response).await;
        // Either 404 (no snapshots table) or OK with empty chain
        if status == StatusCode::OK {
            assert_eq!(body["chain_length"], 0);
            assert_eq!(body["integrity"], "empty");
        } else {
            assert_eq!(status, StatusCode::NOT_FOUND);
            assert_eq!(body["error"], "snapshots_unavailable");
        }
    }

    #[tokio::test]
    async fn test_snapshot_cross_diff_api_compares_snapshots_across_runs() {
        let primary = RunFixture::new("test-run-primary");
        primary.write_summary(
            json!({"run_id": "test-run-primary", "scenario_ids": [], "status": "completed"}),
        );
        primary.write_correlation_evidence_db(&[
            json!({"schema_version": 1, "evidence_id": "link-a", "agent_id": "agent-1",
                "intent_id": "intent-1", "candidate_id": "candidate-1", "timestamp_ns": 100,
                "confidence": 0.9, "match_level": "l1_exact",
                "matched_fields": {"target": "/file-a"}, "supporting_raw_event_refs": ["ev-1"],
                "conversation_id": null, "authority_source": null, "message_id": null,
                "source_event_ref": null, "debug_notes": null}),
        ]);
        primary.write_causal_graph_snapshot(json!({
            "snapshot_id": "snap-p1", "timestamp_ns": 100, "agent_id": "agent-1",
            "link_ids": ["link-a"], "evidence_ids": ["ev-a"], "policy_ids": ["pol-read"],
            "boundary_ids": ["bound-read"], "claim_states": ["observed"],
            "traceback_ids": ["tb-a"], "violation_match_ids": [],
            "node_count": 1, "edge_count": 1, "snapshot_kind": "accountability",
        }));

        let other = RunFixture::new_in_repo(&primary.repo_root, "test-run-other");
        other.write_summary(
            json!({"run_id": "test-run-other", "scenario_ids": [], "status": "completed"}),
        );
        other.write_correlation_evidence_db(&[
            json!({"schema_version": 1, "evidence_id": "link-a", "agent_id": "agent-1",
                "intent_id": "intent-1", "candidate_id": "candidate-1", "timestamp_ns": 100,
                "confidence": 0.9, "match_level": "l1_exact",
                "matched_fields": {"target": "/file-a"}, "supporting_raw_event_refs": ["ev-1"],
                "conversation_id": null, "authority_source": null, "message_id": null,
                "source_event_ref": null, "debug_notes": null}),
            json!({"schema_version": 1, "evidence_id": "link-b", "agent_id": "agent-1",
                "intent_id": "intent-2", "candidate_id": "candidate-2", "timestamp_ns": 200,
                "confidence": 0.9, "match_level": "l1_exact",
                "matched_fields": {"target": "/file-b"}, "supporting_raw_event_refs": ["ev-2"],
                "conversation_id": null, "authority_source": null, "message_id": null,
                "source_event_ref": null, "debug_notes": null}),
        ]);
        other.write_causal_graph_snapshot(json!({
            "snapshot_id": "snap-o1", "timestamp_ns": 200, "agent_id": "agent-1",
            "link_ids": ["link-a", "link-b"], "evidence_ids": ["ev-a", "ev-b"],
            "policy_ids": ["pol-read", "pol-exec"], "boundary_ids": ["bound-read", "bound-exec"],
            "claim_states": ["observed", "inferred"], "traceback_ids": ["tb-a", "tb-b"],
            "violation_match_ids": ["match-1"],
            "node_count": 2, "edge_count": 1, "snapshot_kind": "accountability",
        }));

        let response = serve_run_snapshot_cross_diff_api(primary.run_dir(), "test-run-other")
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        assert_eq!(
            body["diff_available"], true,
            "cross-diff should be available, got {body}"
        );
        assert_eq!(
            body["primary_snapshot"]["snapshot_id"], "snap-p1",
            "primary snap_id wrong, got {body}"
        );
        assert_eq!(
            body["other_snapshot"]["snapshot_id"], "snap-o1",
            "other snap_id wrong, got {body}"
        );
        // Cross-run diff is computed over evidence_ids, not legacy link ids
        let evidence_added = &body["field_diffs"]["evidence_ids"]["added"];
        let evidence_added_count = &body["field_diffs"]["evidence_ids"]["added_count"];
        assert_eq!(
            *evidence_added_count, 1,
            "evidence_ids.added_count should be 1, got field_diffs: {}",
            body["field_diffs"]
        );
        assert!(
            evidence_added.as_array().unwrap().contains(&json!("ev-b")),
            "evidence_ids.added should contain 'ev-b', got {:?}",
            evidence_added
        );
        // Other fields should also reflect cross-run differences
        assert_eq!(
            body["field_diffs"]["evidence_ids"]["added_count"], 1,
            "evidence_ids diff wrong"
        );
        assert_eq!(
            body["field_diffs"]["policy_ids"]["added_count"], 1,
            "policy_ids diff wrong"
        );
        assert_eq!(
            body["field_diffs"]["violation_match_ids"]["added_count"], 1,
            "violation_match_ids diff wrong"
        );
    }

    #[tokio::test]
    async fn test_snapshot_cross_diff_api_returns_unavailable_when_missing_snapshots() {
        let primary = RunFixture::new("test-run-cross-empty");
        primary.write_summary(
            json!({"run_id": "test-run-cross-empty", "scenario_ids": [], "status": "completed"}),
        );
        primary.write_correlation_evidence_db(&[]);
        // No snapshots in primary

        let other = RunFixture::new_in_repo(&primary.repo_root, "test-run-cross-other");
        other.write_summary(
            json!({"run_id": "test-run-cross-other", "scenario_ids": [], "status": "completed"}),
        );
        other.write_correlation_evidence_db(&[]);
        other.write_causal_graph_snapshot(json!({
            "snapshot_id": "snap-o", "timestamp_ns": 100, "agent_id": "agent-1",
            "link_ids": ["link-1"], "evidence_ids": [], "policy_ids": [], "boundary_ids": [],
            "claim_states": [], "traceback_ids": [], "violation_match_ids": [],
            "node_count": 1, "edge_count": 1, "snapshot_kind": "accountability",
        }));

        // Primary has no snapshots → should report diff_available: false
        let response = serve_run_snapshot_cross_diff_api(primary.run_dir(), "test-run-cross-other")
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        assert_eq!(body["diff_available"], false);
        assert_eq!(body["reason"], "primary run has no snapshots");
    }

    #[tokio::test]
    async fn test_agent_snapshots_api_filters_by_agent() {
        let fixture = RunFixture::new("test-run-agent-snaps");
        fixture.write_summary(
            json!({"run_id": "test-run-agent-snaps", "scenario_ids": [], "status": "completed"}),
        );
        fixture.write_correlation_evidence_db(&[]);
        // Two snapshots for different agents
        fixture.write_causal_graph_snapshot(json!({
            "snapshot_id": "snap-a1", "timestamp_ns": 100, "agent_id": "agent-alpha",
            "link_ids": ["link-a1"], "evidence_ids": [], "policy_ids": [], "boundary_ids": [],
            "claim_states": [], "traceback_ids": [], "violation_match_ids": [],
            "node_count": 1, "edge_count": 1, "snapshot_kind": "accountability",
        }));
        fixture.write_causal_graph_snapshot(json!({
            "snapshot_id": "snap-b1", "timestamp_ns": 200, "agent_id": "agent-beta",
            "link_ids": ["link-b1"], "evidence_ids": ["ev-b1"], "policy_ids": ["pol-b1"],
            "boundary_ids": [], "claim_states": [], "traceback_ids": [], "violation_match_ids": [],
            "node_count": 1, "edge_count": 1, "snapshot_kind": "accountability",
        }));

        let response = serve_run_agent_snapshots_api(fixture.run_dir(), "agent-beta")
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        assert_eq!(
            body["total_count"], 1,
            "should only find agent-beta snapshots"
        );
        assert_eq!(body["snapshots"][0]["snapshot_id"], "snap-b1");
        assert_eq!(body["agent_id"], "agent-beta");
        // Must return full fields, not just a subset
        assert_eq!(body["snapshots"][0]["evidence_ids"], json!(["ev-b1"]));
        assert_eq!(body["snapshots"][0]["policy_ids"], json!(["pol-b1"]));
    }

    #[tokio::test]
    async fn test_agent_snapshot_chain_api_filters_by_agent() {
        let fixture = RunFixture::new("test-run-agent-chain");
        fixture.write_summary(
            json!({"run_id": "test-run-agent-chain", "scenario_ids": [], "status": "completed"}),
        );
        fixture.write_correlation_evidence_db(&[]);
        // Snapshots for two different agents at the same timestamps
        for agent in &["agent-alpha", "agent-beta"] {
            let snap1_id = format!("snap-{agent}-1");
            let snap2_id = format!("snap-{agent}-2");
            fixture.write_causal_graph_snapshot(json!({
                "snapshot_id": snap1_id.clone(),
                "timestamp_ns": 100, "agent_id": agent,
                "link_ids": ["link-1"], "evidence_ids": [], "policy_ids": [], "boundary_ids": [],
                "claim_states": [], "traceback_ids": [], "violation_match_ids": [],
                "node_count": 1, "edge_count": 1, "snapshot_kind": "accountability",
            }));
            fixture.write_causal_graph_snapshot(json!({
                "snapshot_id": snap2_id,
                "timestamp_ns": 200, "agent_id": agent,
                "link_ids": ["link-1", "link-2"], "evidence_ids": [], "policy_ids": [],
                "boundary_ids": [], "claim_states": [], "traceback_ids": [],
                "violation_match_ids": [], "node_count": 2, "edge_count": 1,
                "snapshot_kind": "accountability",
            }));
        }

        let response = serve_run_agent_snapshot_chain_api(fixture.run_dir(), "agent-alpha")
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        assert_eq!(
            body["chain_length"], 2,
            "agent-alpha should have 2-chain, got: {}",
            body["chain_length"]
        );
        assert_eq!(body["integrity"], "ok");
        assert_eq!(body["chain_entries"][0]["agent_id"], "agent-alpha");
        assert_eq!(body["chain_entries"][1]["agent_id"], "agent-alpha");
        // Verify chain linking: first entry hash = second entry prev_hash
        assert_eq!(
            body["chain_entries"][1]["prev_hash_hex"],
            body["chain_entries"][0]["hash_hex"]
        );
    }

    #[tokio::test]
    async fn get_run_correlation_evidence_migrates_empty_legacy_schema_shape_to_empty_array() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        let conn =
            rusqlite::Connection::open(fixture.run_dir().join("trace.log.causal.db")).unwrap();
        conn.execute_batch(
            "CREATE TABLE agent_instances (
                agent_id TEXT PRIMARY KEY,
                first_seen_ns INTEGER NOT NULL,
                last_seen_ns INTEGER NOT NULL,
                root_processes_json TEXT NOT NULL DEFAULT '[]',
                tls_sessions_json TEXT NOT NULL DEFAULT '[]',
                conversation_id TEXT
            );
            CREATE TABLE intent_events (
                intent_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                tool_args_json TEXT NOT NULL,
                conversation_id TEXT
            );
            CREATE TABLE action_events (
                action_id TEXT PRIMARY KEY,
                agent_id TEXT,
                timestamp_ns INTEGER NOT NULL,
                pid INTEGER NOT NULL,
                process_start_time_ns INTEGER NOT NULL,
                parent_pid INTEGER,
                parent_start_time_ns INTEGER,
                syscall TEXT NOT NULL,
                args_json TEXT NOT NULL,
                comm TEXT NOT NULL
            );
            CREATE TABLE correlation_evidence (
                evidence_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                intent_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                confidence REAL NOT NULL,
                signals_json TEXT NOT NULL,
                match_detail TEXT NOT NULL
            );
            CREATE TABLE alerts (
                alert_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                severity TEXT NOT NULL,
                confidence REAL NOT NULL,
                category TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                description TEXT NOT NULL,
                causal_evidence_ids_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL
            );
            CREATE TABLE schema_version (
                version INTEGER PRIMARY KEY
            );
            INSERT INTO schema_version(version) VALUES (1);",
        )
        .unwrap();

        let response = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "authority": "supporting",
                "correlation_evidence": []
            })
        );
    }

    #[tokio::test]
    async fn get_run_correlation_evidence_returns_structured_error_for_non_empty_legacy_schema_shape()
     {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        let conn =
            rusqlite::Connection::open(fixture.run_dir().join("trace.log.causal.db")).unwrap();
        conn.execute_batch(
            "CREATE TABLE correlation_evidence (
                evidence_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                intent_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                confidence REAL NOT NULL,
                signals_json TEXT NOT NULL,
                match_detail TEXT NOT NULL
            );
            CREATE TABLE schema_version (
                version INTEGER PRIMARY KEY
            );
            INSERT INTO schema_version(version) VALUES (1);",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence (
                evidence_id, agent_id, intent_id, action_id, timestamp_ns, confidence, signals_json, match_detail
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            rusqlite::params![
                "legacy-link",
                "agent-legacy",
                "intent-legacy",
                "action-legacy",
                999i64,
                0.7f64,
                "[]",
                "L1Exact",
            ],
        )
        .unwrap();

        let response = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "unsupported_correlation_evidence_schema",
                "message": "correlation_evidence schema is unsupported for replay: expected current correlation-evidence columns, found legacy persisted artifact",
                "details": {
                    "table": "correlation_evidence"
                }
            })
        );
    }

    #[tokio::test]
    async fn get_run_correlation_evidence_returns_structured_error_for_malformed_json_columns() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        let conn =
            rusqlite::Connection::open(fixture.run_dir().join("trace.log.causal.db")).unwrap();
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
                debug_notes TEXT
            );",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence (
                schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns,
                confidence, match_level, matched_fields_json, supporting_raw_event_refs_json,
                conversation_id, debug_notes
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            rusqlite::params![
                1i64,
                "bad-json-link",
                "agent-1",
                "intent-1",
                "candidate-1",
                123i64,
                0.8f64,
                "l1_exact",
                "{not-json",
                serde_json::to_string(&json!(["event-1"])).unwrap(),
                rusqlite::types::Null,
                rusqlite::types::Null,
            ],
        )
        .unwrap();

        let response = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_correlation_evidence_json",
                "message": "failed to decode correlation_evidence.matched_fields_json for replay",
                "details": {
                    "column": "matched_fields_json",
                    "evidence_id": "bad-json-link"
                }
            })
        );
    }

    #[tokio::test]
    async fn get_run_correlation_evidence_rejects_matched_fields_json_with_non_object_shape() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        let conn =
            rusqlite::Connection::open(fixture.run_dir().join("trace.log.causal.db")).unwrap();
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
                debug_notes TEXT
            );",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence (
                schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns,
                confidence, match_level, matched_fields_json, supporting_raw_event_refs_json,
                conversation_id, debug_notes
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            rusqlite::params![
                1i64,
                "bad-matched-fields-link",
                "agent-1",
                "intent-1",
                "candidate-1",
                123i64,
                0.8f64,
                "l1_exact",
                serde_json::to_string(&json!(["not-an-object"])).unwrap(),
                serde_json::to_string(&json!(["event-1"])).unwrap(),
                rusqlite::types::Null,
                rusqlite::types::Null,
            ],
        )
        .unwrap();

        let response = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_correlation_evidence_json",
                "message": "failed to decode correlation_evidence.matched_fields_json for replay",
                "details": {
                    "column": "matched_fields_json",
                    "evidence_id": "bad-matched-fields-link"
                }
            })
        );
    }

    #[tokio::test]
    async fn get_run_correlation_evidence_rejects_supporting_raw_event_refs_json_with_non_string_entries()
     {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        let conn =
            rusqlite::Connection::open(fixture.run_dir().join("trace.log.causal.db")).unwrap();
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
        conn.execute(
            "INSERT INTO correlation_evidence (
                schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns,
                confidence, match_level, matched_fields_json, supporting_raw_event_refs_json,
                conversation_id, authority_source, message_id, source_event_ref, debug_notes
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            rusqlite::params![
                1i64,
                "bad-supporting-refs-link",
                "agent-1",
                "intent-1",
                "candidate-1",
                123i64,
                0.8f64,
                "l1_exact",
                serde_json::to_string(&json!({"path": "/tmp/a"})).unwrap(),
                serde_json::to_string(&json!(["event-1", 2])).unwrap(),
                rusqlite::types::Null,
                "request_history_assistant_tool_use",
                "msg-1",
                "block-1",
                rusqlite::types::Null,
            ],
        )
        .unwrap();

        let response = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_correlation_evidence_json",
                "message": "failed to decode correlation_evidence.supporting_raw_event_refs_json for replay",
                "details": {
                    "column": "supporting_raw_event_refs_json",
                    "evidence_id": "bad-supporting-refs-link"
                }
            })
        );
    }

    #[tokio::test]
    async fn get_run_correlation_evidence_rejects_invalid_authority_source() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        let conn =
            rusqlite::Connection::open(fixture.run_dir().join("trace.log.causal.db")).unwrap();
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
        conn.execute(
            "INSERT INTO correlation_evidence (
                schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns,
                confidence, match_level, matched_fields_json, supporting_raw_event_refs_json,
                conversation_id, authority_source, message_id, source_event_ref, debug_notes
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            rusqlite::params![
                1i64,
                "bad-authority-link",
                "agent-1",
                "intent-1",
                "candidate-1",
                123i64,
                0.8f64,
                "l1_exact",
                serde_json::to_string(&json!({"path": "/tmp/a"})).unwrap(),
                serde_json::to_string(&json!(["event-1"])).unwrap(),
                rusqlite::types::Null,
                "bogus_authority",
                "msg-1",
                "block-1",
                rusqlite::types::Null,
            ],
        )
        .unwrap();

        let response = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_correlation_evidence_json",
                "message": "failed to decode correlation_evidence.authority_source for replay",
                "details": {
                    "column": "authority_source",
                    "evidence_id": "bad-authority-link"
                }
            })
        );
    }

    #[tokio::test]
    async fn get_run_correlation_evidence_rejects_non_string_message_id_or_source_event_ref() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        let conn =
            rusqlite::Connection::open(fixture.run_dir().join("trace.log.causal.db")).unwrap();
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
                message_id BLOB,
                source_event_ref BLOB,
                debug_notes TEXT
            );",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence (
                schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns,
                confidence, match_level, matched_fields_json, supporting_raw_event_refs_json,
                conversation_id, authority_source, message_id, source_event_ref, debug_notes
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            rusqlite::params![
                1i64,
                "bad-message-link",
                "agent-1",
                "intent-1",
                "candidate-1",
                123i64,
                0.8f64,
                "l1_exact",
                serde_json::to_string(&json!({"path": "/tmp/a"})).unwrap(),
                serde_json::to_string(&json!(["event-1"])).unwrap(),
                rusqlite::types::Null,
                "request_history_assistant_tool_use",
                rusqlite::types::Value::Blob(vec![0, 159, 146, 150]),
                rusqlite::types::Value::Blob(vec![0, 159, 146, 150]),
                rusqlite::types::Null,
            ],
        )
        .unwrap();

        let response = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_correlation_evidence_json",
                "message": "failed to decode correlation_evidence.message_id for replay",
                "details": {
                    "column": "message_id",
                    "evidence_id": "bad-message-link"
                }
            })
        );
    }

    #[tokio::test]
    async fn handle_request_routes_run_correlation_evidence_through_get_path() {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1,
            "evidence_id": "link-1",
            "agent_id": "agent-1",
            "intent_id": "intent-1",
            "candidate_id": "candidate-1",
            "timestamp_ns": 123,
            "confidence": 0.8,
            "match_level": "l1_exact",
            "matched_fields": {"path": "/tmp/a"},
            "supporting_raw_event_refs": ["event-1"],
            "conversation_id": "conv-1",
            "authority_source": "request_history_assistant_tool_use",
            "message_id": "msg-1",
            "source_event_ref": "content-block-1",
            "debug_notes": "note-1"
        })]);
        let _test_lock = test_env_lock();
        let _guard = fixture.enter_repo_root();

        let response = issue_get("/api/runs/test-run/correlation-evidence").await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get("content-type").unwrap(),
            "application/json"
        );
        let body = read_json_body(response).await;
        assert_eq!(body["authority"], "supporting");
        assert!(body.get("correlation_evidence").is_some());
        assert!(body.get("causal_links").is_none());
        assert_eq!(
            body["correlation_evidence"],
            json!([
                {
                    "schema_version": 1,
                    "evidence_id": "link-1",
                    "agent_id": "agent-1",
                    "intent_id": "intent-1",
                    "candidate_id": "candidate-1",
                    "timestamp_ns": 123,
                    "confidence": 0.8,
                    "match_level": "l1_exact",
                    "matched_fields": {"path": "/tmp/a"},
                    "supporting_raw_event_refs": ["event-1"],
                    "conversation_id": "conv-1",
                    "authority_source": "request_history_assistant_tool_use",
                    "message_id": "msg-1",
                    "source_event_ref": "content-block-1",
                    "debug_notes": "note-1"
                }
            ])
        );
        assert_run_bound_payload_is_not_legacy_sample_data(&body["correlation_evidence"]);
    }

    #[tokio::test]
    #[ignore]
    async fn run_alerts_surface_replayable_accountability_traceback_when_present() {
        let fixture = RunFixture::new("test-run-traceback-replay");
        fixture.write_summary(json!({
            "run_id": "test-run-traceback-replay",
            "scenario_ids": ["scenario-a"],
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

        fixture.write_accountability_traceback_replay(json!({
            "traceback_id": "tb-1",
            "alert_id": "alert-1",
            "chain_node_ids": ["node-root", "node-leaf"],
            "root_cause_node_id": "node-root",
            "supporting_evidence_ids": ["evidence:link-1"],
            "explanation_summary": "minimal accountable chain"
        }));

        let response = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!([
                {
                    "alert_id": "alert-1",
                    "agent_id": "agent-1",
                    "timestamp_ns": 123,
                    "severity": "WARNING",
                    "confidence": 0.8,
                    "category": "policy",
                    "rule_id": "rule-1",
                    "description": "first alert",
                    "evidence_source": "causal",
                    "causal_evidence_ids": ["link-1"],
                    "evidence": {
                        "kind": "accountability_evidence",
                        "evidence_refs": ["evidence:link-1"],
                        "directness": "direct",
                        "boundary_id": "boundary.file_read",
                        "correlation_evidence": {"evidence_id": "link-1", "agent_id": "agent-1"}
                    },
                    "accountability_traceback": {
                        "traceback_id": "tb-1",
                        "alert_id": "alert-1",
                        "chain_node_ids": ["node-root", "node-leaf"],
                        "root_cause_node_id": "node-root",
                        "supporting_evidence_ids": ["evidence:link-1"],
                        "explanation_summary": "minimal accountable chain"
                    },
                    "consistency_warnings": []
                }
            ])
        );
    }

    #[tokio::test]
    #[ignore]
    async fn run_alerts_fail_closed_when_accountability_traceback_replay_is_malformed() {
        let fixture = RunFixture::new("test-run-traceback-malformed");
        fixture.write_summary(json!({
            "run_id": "test-run-traceback-malformed",
            "scenario_ids": ["scenario-a"],
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

        fixture.write_raw_accountability_traceback_replay(
            "INSERT INTO accountability_traceback_replays (traceback_id, alert_id, chain_node_ids_json, root_cause_node_id, supporting_evidence_ids_json, explanation_summary) VALUES ('tb-1', 'alert-1', '{not-json', 'node-root', '[\"evidence:link-1\"]', 'minimal accountable chain')",
        );

        let response = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_accountability_traceback_replay",
                "message": "failed to decode accountability_traceback_replays row for replay",
                "details": {
                    "alert_id": "alert-1"
                }
            })
        );
    }

    #[tokio::test]
    #[ignore]
    async fn run_alerts_fail_closed_when_accountability_traceback_replay_has_empty_chain_node_ids()
    {
        let fixture = RunFixture::new("test-run-traceback-empty-chain");
        fixture.write_summary(json!({
            "run_id": "test-run-traceback-empty-chain",
            "scenario_ids": ["scenario-a"],
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
        fixture.write_raw_accountability_traceback_replay(
            "INSERT INTO accountability_traceback_replays (traceback_id, alert_id, chain_node_ids_json, root_cause_node_id, supporting_evidence_ids_json, explanation_summary) VALUES ('tb-1', 'alert-1', '[]', 'node-root', '[\"evidence:link-1\"]', 'minimal accountable chain')",
        );

        let response = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_accountability_traceback_replay",
                "message": "failed to decode accountability_traceback_replays row for replay",
                "details": {
                    "alert_id": "alert-1"
                }
            })
        );
    }

    #[tokio::test]
    #[ignore]
    async fn run_alerts_fail_closed_when_accountability_traceback_replay_root_cause_is_outside_chain()
     {
        let fixture = RunFixture::new("test-run-traceback-root-outside-chain");
        fixture.write_summary(json!({
            "run_id": "test-run-traceback-root-outside-chain",
            "scenario_ids": ["scenario-a"],
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
        fixture.write_raw_accountability_traceback_replay(
            "INSERT INTO accountability_traceback_replays (traceback_id, alert_id, chain_node_ids_json, root_cause_node_id, supporting_evidence_ids_json, explanation_summary) VALUES ('tb-1', 'alert-1', '[\"node-1\",\"node-2\"]', 'node-root', '[\"evidence:link-1\"]', 'minimal accountable chain')",
        );

        let response = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_accountability_traceback_replay",
                "message": "failed to decode accountability_traceback_replays row for replay",
                "details": {
                    "alert_id": "alert-1"
                }
            })
        );
    }

    #[tokio::test]
    async fn run_correlation_evidence_do_not_attach_traceback_when_only_chain_node_ids_match_evidence_id()
     {
        let fixture = RunFixture::new("test-run-causal-traceback-node-only-match");
        fixture.write_summary(json!({
            "run_id": "test-run-causal-traceback-node-only-match",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_alerts_db(&[]);
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1,
            "evidence_id": "link-1",
            "agent_id": "agent-1",
            "intent_id": "intent-1",
            "candidate_id": "candidate-1",
            "timestamp_ns": 123,
            "confidence": 0.8,
            "match_level": "l1_exact",
            "matched_fields": {"path": "/tmp/demo.txt"},
            "supporting_raw_event_refs": ["action-1"],
            "conversation_id": null,
            "authority_source": "request_history_assistant_tool_use",
            "message_id": "msg-1",
            "source_event_ref": "content-block-1",
            "debug_notes": null
        })]);
        fixture.write_accountability_traceback_replay(json!({
            "traceback_id": "tb-1",
            "alert_id": "alert-1",
            "chain_node_ids": ["node-root", "link-1", "node-leaf"],
            "root_cause_node_id": "node-root",
            "supporting_evidence_ids": ["evidence:unrelated"],
            "explanation_summary": "node id text happens to equal link id"
        }));

        let response = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        assert_eq!(body["authority"], "supporting");
        assert_eq!(
            body["correlation_evidence"],
            json!([
                {
                    "schema_version": 1,
                    "evidence_id": "link-1",
                    "agent_id": "agent-1",
                    "intent_id": "intent-1",
                    "candidate_id": "candidate-1",
                    "timestamp_ns": 123,
                    "confidence": 0.8,
                    "match_level": "l1_exact",
                    "matched_fields": {"path": "/tmp/demo.txt"},
                    "supporting_raw_event_refs": ["action-1"],
                    "conversation_id": null,
                    "authority_source": "request_history_assistant_tool_use",
                    "message_id": "msg-1",
                    "source_event_ref": "content-block-1",
                    "debug_notes": null
                }
            ])
        );
    }

    #[tokio::test]
    async fn run_correlation_evidence_fail_closed_when_accountability_traceback_replay_is_malformed()
     {
        let fixture = RunFixture::new("test-run-causal-traceback-malformed");
        fixture.write_summary(json!({
            "run_id": "test-run-causal-traceback-malformed",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_alerts_db(&[]);
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1,
            "evidence_id": "link-1",
            "agent_id": "agent-1",
            "intent_id": "intent-1",
            "candidate_id": "candidate-1",
            "timestamp_ns": 123,
            "confidence": 0.8,
            "match_level": "l1_exact",
            "matched_fields": {"path": "/tmp/demo.txt"},
            "supporting_raw_event_refs": ["action-1"],
            "conversation_id": null,
            "authority_source": "request_history_assistant_tool_use",
            "message_id": "msg-1",
            "source_event_ref": "content-block-1",
            "debug_notes": null
        })]);
        fixture.write_raw_accountability_traceback_replay(
            "INSERT INTO accountability_traceback_replays (traceback_id, alert_id, chain_node_ids_json, root_cause_node_id, supporting_evidence_ids_json, explanation_summary) VALUES ('tb-1', 'alert-1', '{not-json', 'node-root', '[\"evidence:link-1\"]', 'minimal accountable chain')",
        );

        let response = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_accountability_traceback_replay",
                "message": "failed to decode accountability_traceback_replays row for replay",
                "details": {
                    "alert_id": "alert-1"
                }
            })
        );
    }

    #[tokio::test]
    async fn run_correlation_evidence_fail_closed_when_accountability_traceback_replay_has_non_text_summary()
     {
        let fixture = RunFixture::new("test-run-causal-traceback-bad-summary-type");
        fixture.write_summary(json!({
            "run_id": "test-run-causal-traceback-bad-summary-type",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_alerts_db(&[]);
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1,
            "evidence_id": "link-1",
            "agent_id": "agent-1",
            "intent_id": "intent-1",
            "candidate_id": "candidate-1",
            "timestamp_ns": 123,
            "confidence": 0.8,
            "match_level": "l1_exact",
            "matched_fields": {"path": "/tmp/demo.txt"},
            "supporting_raw_event_refs": ["action-1"],
            "conversation_id": null,
            "authority_source": "request_history_assistant_tool_use",
            "message_id": "msg-1",
            "source_event_ref": "content-block-1",
            "debug_notes": null
        })]);
        let conn =
            rusqlite::Connection::open(fixture.run_dir().join("trace.log.causal.db")).unwrap();
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS accountability_traceback_replays (
                traceback_id TEXT PRIMARY KEY,
                alert_id TEXT NOT NULL,
                chain_node_ids_json TEXT NOT NULL,
                root_cause_node_id TEXT NOT NULL,
                supporting_evidence_ids_json TEXT NOT NULL,
                explanation_summary BLOB NOT NULL
            );",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO accountability_traceback_replays (
                traceback_id, alert_id, chain_node_ids_json, root_cause_node_id,
                supporting_evidence_ids_json, explanation_summary
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            rusqlite::params![
                "tb-1",
                "alert-1",
                serde_json::to_string(&json!(["node-root", "node-leaf"])).unwrap(),
                "node-root",
                serde_json::to_string(&json!(["evidence:link-1"])).unwrap(),
                rusqlite::types::Value::Blob(vec![0, 159, 146, 150]),
            ],
        )
        .unwrap();

        let response = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_accountability_traceback_replay",
                "message": "failed to decode accountability_traceback_replays row for replay",
                "details": {
                    "alert_id": "alert-1"
                }
            })
        );
    }

    #[tokio::test]
    async fn run_correlation_evidence_fail_closed_when_accountability_traceback_replay_has_empty_chain_node_ids()
     {
        let fixture = RunFixture::new("test-run-causal-traceback-empty-chain");
        fixture.write_summary(json!({
            "run_id": "test-run-causal-traceback-empty-chain",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_alerts_db(&[]);
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1,
            "evidence_id": "link-1",
            "agent_id": "agent-1",
            "intent_id": "intent-1",
            "candidate_id": "candidate-1",
            "timestamp_ns": 123,
            "confidence": 0.8,
            "match_level": "l1_exact",
            "matched_fields": {"path": "/tmp/demo.txt"},
            "supporting_raw_event_refs": ["action-1"],
            "conversation_id": null,
            "authority_source": "request_history_assistant_tool_use",
            "message_id": "msg-1",
            "source_event_ref": "content-block-1",
            "debug_notes": null
        })]);
        fixture.write_raw_accountability_traceback_replay(
            "INSERT INTO accountability_traceback_replays (traceback_id, alert_id, chain_node_ids_json, root_cause_node_id, supporting_evidence_ids_json, explanation_summary) VALUES ('tb-1', 'alert-1', '[]', 'node-root', '[\"evidence:link-1\"]', 'minimal accountable chain')",
        );

        let response = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_accountability_traceback_replay",
                "message": "failed to decode accountability_traceback_replays row for replay",
                "details": {
                    "alert_id": "alert-1"
                }
            })
        );
    }

    #[tokio::test]
    async fn run_correlation_evidence_fail_closed_when_accountability_traceback_replay_root_cause_is_outside_chain()
     {
        let fixture = RunFixture::new("test-run-causal-traceback-root-outside-chain");
        fixture.write_summary(json!({
            "run_id": "test-run-causal-traceback-root-outside-chain",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));
        fixture.write_alerts_db(&[]);
        fixture.write_correlation_evidence_db(&[json!({
            "schema_version": 1,
            "evidence_id": "link-1",
            "agent_id": "agent-1",
            "intent_id": "intent-1",
            "candidate_id": "candidate-1",
            "timestamp_ns": 123,
            "confidence": 0.8,
            "match_level": "l1_exact",
            "matched_fields": {"path": "/tmp/demo.txt"},
            "supporting_raw_event_refs": ["action-1"],
            "conversation_id": null,
            "authority_source": "request_history_assistant_tool_use",
            "message_id": "msg-1",
            "source_event_ref": "content-block-1",
            "debug_notes": null
        })]);
        fixture.write_raw_accountability_traceback_replay(
            "INSERT INTO accountability_traceback_replays (traceback_id, alert_id, chain_node_ids_json, root_cause_node_id, supporting_evidence_ids_json, explanation_summary) VALUES ('tb-1', 'alert-1', '[\"node-1\",\"node-2\"]', 'node-root', '[\"evidence:link-1\"]', 'minimal accountable chain')",
        );

        let response = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(
            read_json_body(response).await,
            json!({
                "error": "invalid_accountability_traceback_replay",
                "message": "failed to decode accountability_traceback_replays row for replay",
                "details": {
                    "alert_id": "alert-1"
                }
            })
        );
    }

    async fn missing_evaluation_alerts_or_correlation_evidence_artifacts_return_explicit_unavailable_response()
     {
        let fixture = RunFixture::new("test-run");
        fixture.write_summary(json!({
            "run_id": "test-run",
            "scenario_ids": ["scenario-a"],
            "status": "completed"
        }));

        let evaluation = serve_run_evaluation_api(fixture.run_dir()).await.unwrap();
        assert_eq!(evaluation.status(), StatusCode::NOT_FOUND);
        assert_eq!(
            read_json_body(evaluation).await,
            json!({
                "error": "artifact_unavailable",
                "run_id": "test-run",
                "artifact": "evaluation"
            })
        );

        let alerts = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(alerts.status(), StatusCode::NOT_FOUND);
        assert_eq!(
            read_json_body(alerts).await,
            json!({
                "error": "artifact_unavailable",
                "run_id": "test-run",
                "artifact": "alerts"
            })
        );

        let causal_links = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(causal_links.status(), StatusCode::NOT_FOUND);
        assert_eq!(
            read_json_body(causal_links).await,
            json!({
                "error": "artifact_unavailable",
                "run_id": "test-run",
                "artifact": "correlation_evidence"
            })
        );
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
                    "events": true
                },
                "artifact_sources": {
                    "correlation_evidence": "missing"
                },
                "warnings": []
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
                    "events": false
                },
                "artifact_sources": {
                    "correlation_evidence": "missing"
                },
                "warnings": []
            })
        );

        let response = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!([
                {
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
                }
            ])
        );
    }

    #[tokio::test]
    async fn get_run_correlation_evidence_reads_summary_declared_causal_db_artifact() {
        let fixture = RunFixture::new("test-run-summary-causal-db");
        let external_causal_db = fixture.repo_root.join("external.causal.db");
        fixture.write_summary(json!({
            "run_id": "test-run-summary-causal-db",
            "scenario_ids": ["scenario-a"],
            "status": "completed",
            "artifacts": {
                "causal_db": external_causal_db.to_string_lossy()
            }
        }));
        fixture.write_named_causal_links_db_path(
            &external_causal_db,
            &[json!({
                "schema_version": 1,
                "evidence_id": "external-link-1",
                "agent_id": "agent-external",
                "intent_id": "intent-external",
                "candidate_id": "candidate-external",
                "timestamp_ns": 654,
                "confidence": 0.88,
                "match_level": "l1_exact",
                "matched_fields": {"path": "/tmp/external"},
                "supporting_raw_event_refs": ["event-external"],
                "conversation_id": null,
                "authority_source": "request_history_assistant_tool_use",
                "message_id": "msg-external",
                "source_event_ref": "content-block-external",
                "debug_notes": "external"
            })],
        );

        let summary = serve_run_summary_api(fixture.run_dir(), "test-run-summary-causal-db")
            .await
            .unwrap();
        assert_eq!(summary.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(summary).await,
            json!({
                "run_id": "test-run-summary-causal-db",
                "scenario_ids": ["scenario-a"],
                "run_status": "completed",
                "artifact_availability": {
                    "evaluation": false,
                    "alerts": false,
                    "correlation_evidence": true,
                    "events": false
                },
                "artifact_sources": {
                    "correlation_evidence": "persisted"
                },
                "warnings": []
            })
        );

        let response = serve_correlation_evidence_api(fixture.run_dir())
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        let body = read_json_body(response).await;
        assert_eq!(body["authority"], "supporting");
        assert_eq!(
            body["correlation_evidence"],
            json!([
                {
                    "schema_version": 1,
                    "evidence_id": "external-link-1",
                    "agent_id": "agent-external",
                    "intent_id": "intent-external",
                    "candidate_id": "candidate-external",
                    "timestamp_ns": 654,
                    "confidence": 0.88,
                    "match_level": "l1_exact",
                    "matched_fields": {"path": "/tmp/external"},
                    "supporting_raw_event_refs": ["event-external"],
                    "conversation_id": null,
                    "authority_source": "request_history_assistant_tool_use",
                    "message_id": "msg-external",
                    "source_event_ref": "content-block-external",
                    "debug_notes": "external"
                }
            ])
        );
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
    #[ignore]
    async fn get_run_alerts_reads_legacy_rows_without_evidence_source_column() {
        let fixture = RunFixture::new("test-run");
        let conn =
            rusqlite::Connection::open(fixture.run_dir().join("trace.log.alerts.db")).unwrap();
        conn.execute_batch(
            "CREATE TABLE alerts (
                alert_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                severity TEXT NOT NULL,
                confidence REAL NOT NULL,
                category TEXT NOT NULL,
                rule_id TEXT NOT NULL,
                description TEXT NOT NULL,
                causal_evidence_ids_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL
            );",
        )
        .unwrap();
        conn.execute(
            "INSERT INTO alerts (
                alert_id, agent_id, timestamp_ns, severity, confidence,
                category, rule_id, description, causal_evidence_ids_json, evidence_json
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            rusqlite::params![
                "legacy-alert",
                "agent-legacy",
                999i64,
                "WARNING",
                0.7f64,
                "logic",
                "rule-legacy",
                "legacy row",
                "[]",
                "{}",
            ],
        )
        .unwrap();

        let response = serve_alerts_api(fixture.run_dir()).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            read_json_body(response).await,
            json!([
                {
                    "alert_id": "legacy-alert",
                    "agent_id": "agent-legacy",
                    "timestamp_ns": 999,
                    "severity": "WARNING",
                    "confidence": 0.7,
                    "category": "logic",
                    "rule_id": "rule-legacy",
                    "description": "legacy row",
                    "evidence_source": "anomaly",
                    "causal_evidence_ids": [],
                    "evidence": {},
                    "consistency_warnings": []
                }
            ])
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
                    "correlation_evidence": false,
                    "events": false
                },
                "artifact_sources": {
                    "correlation_evidence": "missing"
                },
                "warnings": []
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
            self.write_named_alerts_db(&self.run_dir.join("trace.log.alerts.db"), alerts);
        }

        fn write_named_alerts_db(&self, path: &std::path::Path, alerts: &[Value]) {
            let conn = rusqlite::Connection::open(path).unwrap();
            conn.execute_batch(
                "CREATE TABLE responsibility_alerts (
                    alert_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    timestamp_ns INTEGER NOT NULL,
                    display_profile TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    authority TEXT NOT NULL,
                    reasoning TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    action_hint TEXT NOT NULL
                );",
            )
            .unwrap();

            for alert in alerts {
                conn.execute(
                    "INSERT INTO responsibility_alerts (
                        alert_id, agent_id, timestamp_ns, display_profile, decision, severity,
                        confidence, authority, reasoning, title, summary, action_hint
                    ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
                    rusqlite::params![
                        alert.get("alert_id").unwrap().as_str().unwrap(),
                        alert.get("agent_id").unwrap().as_str().unwrap(),
                        alert.get("timestamp_ns").unwrap().as_i64().unwrap(),
                        alert.get("display_profile").unwrap().as_str().unwrap(),
                        alert.get("decision").unwrap().as_str().unwrap(),
                        alert.get("severity").unwrap().as_str().unwrap(),
                        alert.get("confidence").unwrap().as_f64().unwrap(),
                        alert.get("authority").unwrap().as_str().unwrap(),
                        alert.get("reasoning").unwrap().as_str().unwrap(),
                        alert.get("title").unwrap().as_str().unwrap(),
                        alert.get("summary").unwrap().as_str().unwrap(),
                        alert.get("action_hint").unwrap().as_str().unwrap(),
                    ],
                )
                .unwrap();
            }
        }

        fn write_evidence_records_db(&self, records: &[Value]) {
            let conn =
                rusqlite::Connection::open(self.run_dir.join("trace.log.causal.db")).unwrap();
            conn.execute_batch(
                "CREATE TABLE IF NOT EXISTS evidence_records (
                    evidence_id TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL,
                    directness TEXT NOT NULL,
                    rule_or_inference_id TEXT NOT NULL,
                    resource_id TEXT,
                    boundary_id TEXT,
                    confidence REAL NOT NULL,
                    redaction_status TEXT NOT NULL,
                    payload_ref TEXT
                );",
            )
            .unwrap();

            for record in records {
                conn.execute(
                    "INSERT INTO evidence_records (
                        evidence_id, source_event_id, directness, rule_or_inference_id,
                        resource_id, boundary_id, confidence, redaction_status, payload_ref
                    ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                    rusqlite::params![
                        record.get("evidence_id").unwrap().as_str().unwrap(),
                        record.get("source_event_id").unwrap().as_str().unwrap(),
                        record.get("directness").unwrap().as_str().unwrap(),
                        record
                            .get("rule_or_inference_id")
                            .unwrap()
                            .as_str()
                            .unwrap(),
                        record
                            .get("resource_id")
                            .map(serde_json::to_string)
                            .transpose()
                            .unwrap(),
                        record.get("boundary_id").and_then(Value::as_str),
                        record.get("confidence").unwrap().as_f64().unwrap(),
                        record.get("redaction_status").unwrap().as_str().unwrap(),
                        record.get("payload_ref").and_then(Value::as_str),
                    ],
                )
                .unwrap();
            }
        }

        fn write_policy_evaluations_db(&self, evaluations: &[Value]) {
            let conn =
                rusqlite::Connection::open(self.run_dir.join("trace.log.causal.db")).unwrap();
            conn.execute_batch(
                "CREATE TABLE IF NOT EXISTS policy_evaluations (
                    evidence_id TEXT PRIMARY KEY,
                    policy_id TEXT,
                    boundary_id TEXT,
                    claim_state TEXT NOT NULL,
                    matched INTEGER NOT NULL,
                    reason TEXT NOT NULL
                );",
            )
            .unwrap();

            for evaluation in evaluations {
                conn.execute(
                    "INSERT INTO policy_evaluations (
                        evidence_id, policy_id, boundary_id, claim_state, matched, reason
                    ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    rusqlite::params![
                        evaluation.get("evidence_id").unwrap().as_str().unwrap(),
                        evaluation.get("policy_id").and_then(Value::as_str),
                        evaluation.get("boundary_id").and_then(Value::as_str),
                        evaluation.get("claim_state").unwrap().as_str().unwrap(),
                        1i64,
                        evaluation.get("reason").unwrap().as_str().unwrap(),
                    ],
                )
                .unwrap();
            }
        }

        fn write_accountability_traceback_replay(&self, replay: Value) {
            let conn =
                rusqlite::Connection::open(self.run_dir.join("trace.log.causal.db")).unwrap();
            conn.execute_batch(
                "CREATE TABLE IF NOT EXISTS accountability_traceback_replays (
                    traceback_id TEXT PRIMARY KEY,
                    alert_id TEXT NOT NULL,
                    chain_node_ids_json TEXT NOT NULL,
                    root_cause_node_id TEXT NOT NULL,
                    supporting_evidence_ids_json TEXT NOT NULL,
                    explanation_summary TEXT NOT NULL
                );",
            )
            .unwrap();
            conn.execute(
                "INSERT INTO accountability_traceback_replays (
                    traceback_id, alert_id, chain_node_ids_json, root_cause_node_id,
                    supporting_evidence_ids_json, explanation_summary
                ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                rusqlite::params![
                    replay.get("traceback_id").unwrap().as_str().unwrap(),
                    replay.get("alert_id").unwrap().as_str().unwrap(),
                    serde_json::to_string(replay.get("chain_node_ids").unwrap()).unwrap(),
                    replay.get("root_cause_node_id").unwrap().as_str().unwrap(),
                    serde_json::to_string(replay.get("supporting_evidence_ids").unwrap()).unwrap(),
                    replay.get("explanation_summary").unwrap().as_str().unwrap(),
                ],
            )
            .unwrap();
        }

        fn write_raw_accountability_traceback_replay(&self, insert_sql: &str) {
            let conn =
                rusqlite::Connection::open(self.run_dir.join("trace.log.causal.db")).unwrap();
            conn.execute_batch(
                "CREATE TABLE IF NOT EXISTS accountability_traceback_replays (
                    traceback_id TEXT PRIMARY KEY,
                    alert_id TEXT NOT NULL,
                    chain_node_ids_json TEXT NOT NULL,
                    root_cause_node_id TEXT NOT NULL,
                    supporting_evidence_ids_json TEXT NOT NULL,
                    explanation_summary TEXT NOT NULL
                );",
            )
            .unwrap();
            conn.execute_batch(insert_sql).unwrap();
        }

        fn write_accountability_audit_summary(&self, summary: Value) {
            let conn =
                rusqlite::Connection::open(self.run_dir.join("trace.log.causal.db")).unwrap();
            conn.execute_batch(
                "CREATE TABLE IF NOT EXISTS accountability_audit_summaries (
                    run_id TEXT PRIMARY KEY,
                    alert_count INTEGER NOT NULL,
                    traceback_count INTEGER NOT NULL,
                    summary_text TEXT NOT NULL
                );",
            )
            .unwrap();
            conn.execute(
                "INSERT INTO accountability_audit_summaries (
                    run_id, alert_count, traceback_count, summary_text
                ) VALUES (?1, ?2, ?3, ?4)",
                rusqlite::params![
                    summary.get("run_id").unwrap().as_str().unwrap(),
                    summary.get("alert_count").unwrap().as_i64().unwrap(),
                    summary.get("traceback_count").unwrap().as_i64().unwrap(),
                    summary.get("summary_text").unwrap().as_str().unwrap(),
                ],
            )
            .unwrap();
        }

        fn write_raw_accountability_audit_summary_table(&self, summaries: &[Value]) {
            let conn =
                rusqlite::Connection::open(self.run_dir.join("trace.log.causal.db")).unwrap();
            conn.execute_batch(
                "CREATE TABLE IF NOT EXISTS accountability_audit_summaries (
                    run_id TEXT PRIMARY KEY,
                    alert_count INTEGER NOT NULL,
                    traceback_count INTEGER NOT NULL,
                    summary_text TEXT NOT NULL
                );",
            )
            .unwrap();
            for summary in summaries {
                conn.execute(
                    "INSERT INTO accountability_audit_summaries (
                        run_id, alert_count, traceback_count, summary_text
                    ) VALUES (?1, ?2, ?3, ?4)",
                    rusqlite::params![
                        summary.get("run_id").unwrap().as_str().unwrap(),
                        summary.get("alert_count").unwrap().as_i64().unwrap(),
                        summary.get("traceback_count").unwrap().as_i64().unwrap(),
                        summary.get("summary_text").unwrap().as_str().unwrap(),
                    ],
                )
                .unwrap();
            }
        }

        fn write_evidence_records_to_causal_db(&self, records: &[Value]) {
            let conn =
                rusqlite::Connection::open(self.run_dir.join("trace.log.causal.db")).unwrap();
            conn.execute_batch(
                "CREATE TABLE IF NOT EXISTS evidence_records (
                    evidence_id TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL,
                    directness TEXT NOT NULL,
                    rule_or_inference_id TEXT NOT NULL,
                    resource_id TEXT,
                    boundary_id TEXT,
                    confidence REAL NOT NULL,
                    redaction_status TEXT NOT NULL,
                    payload_ref TEXT
                );",
            )
            .unwrap();

            for record in records {
                conn.execute(
                    "INSERT INTO evidence_records (
                        evidence_id, source_event_id, directness, rule_or_inference_id,
                        resource_id, boundary_id, confidence, redaction_status, payload_ref
                    ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                    rusqlite::params![
                        record.get("evidence_id").unwrap().as_str().unwrap(),
                        record.get("source_event_id").unwrap().as_str().unwrap(),
                        record.get("directness").unwrap().as_str().unwrap(),
                        record
                            .get("rule_or_inference_id")
                            .unwrap()
                            .as_str()
                            .unwrap(),
                        record
                            .get("resource_id")
                            .map(serde_json::to_string)
                            .transpose()
                            .unwrap(),
                        record.get("boundary_id").and_then(Value::as_str),
                        record.get("confidence").unwrap().as_f64().unwrap(),
                        record.get("redaction_status").unwrap().as_str().unwrap(),
                        record.get("payload_ref").and_then(Value::as_str),
                    ],
                )
                .unwrap();
            }
        }

        fn write_policy_evaluations_to_causal_db(&self, evaluations: &[Value]) {
            let conn =
                rusqlite::Connection::open(self.run_dir.join("trace.log.causal.db")).unwrap();
            conn.execute_batch(
                "CREATE TABLE IF NOT EXISTS policy_evaluations (
                    evidence_id TEXT PRIMARY KEY,
                    policy_id TEXT,
                    boundary_id TEXT,
                    claim_state TEXT NOT NULL,
                    matched INTEGER NOT NULL,
                    reason TEXT NOT NULL
                );",
            )
            .unwrap();

            for evaluation in evaluations {
                conn.execute(
                    "INSERT INTO policy_evaluations (
                        evidence_id, policy_id, boundary_id, claim_state, matched, reason
                    ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    rusqlite::params![
                        evaluation.get("evidence_id").unwrap().as_str().unwrap(),
                        evaluation.get("policy_id").and_then(Value::as_str),
                        evaluation.get("boundary_id").and_then(Value::as_str),
                        evaluation.get("claim_state").unwrap().as_str().unwrap(),
                        1i64,
                        evaluation.get("reason").unwrap().as_str().unwrap(),
                    ],
                )
                .unwrap();
            }
        }

        fn write_accountability_traceback_replay_to_causal_db(&self, replay: Value) {
            let conn =
                rusqlite::Connection::open(self.run_dir.join("trace.log.causal.db")).unwrap();
            conn.execute_batch(
                "CREATE TABLE IF NOT EXISTS accountability_traceback_replays (
                    traceback_id TEXT PRIMARY KEY,
                    alert_id TEXT NOT NULL,
                    chain_node_ids_json TEXT NOT NULL,
                    root_cause_node_id TEXT NOT NULL,
                    supporting_evidence_ids_json TEXT NOT NULL,
                    explanation_summary TEXT NOT NULL
                );",
            )
            .unwrap();
            conn.execute(
                "INSERT INTO accountability_traceback_replays (
                    traceback_id, alert_id, chain_node_ids_json, root_cause_node_id,
                    supporting_evidence_ids_json, explanation_summary
                ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                rusqlite::params![
                    replay.get("traceback_id").unwrap().as_str().unwrap(),
                    replay.get("alert_id").unwrap().as_str().unwrap(),
                    serde_json::to_string(replay.get("chain_node_ids").unwrap()).unwrap(),
                    replay.get("root_cause_node_id").unwrap().as_str().unwrap(),
                    serde_json::to_string(replay.get("supporting_evidence_ids").unwrap()).unwrap(),
                    replay.get("explanation_summary").unwrap().as_str().unwrap(),
                ],
            )
            .unwrap();
        }

        fn write_correlation_evidence_db(&self, links: &[Value]) {
            self.write_named_causal_links_db("trace.log.causal.db", links);
        }

        fn write_causal_graph_snapshot(&self, snapshot: Value) {
            let db_path = self.run_dir.join("trace.log.causal.db");
            let conn = rusqlite::Connection::open(&db_path).unwrap();
            conn.execute_batch(
                "CREATE TABLE IF NOT EXISTS causal_graph_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    timestamp_ns INTEGER NOT NULL,
                    agent_id TEXT NOT NULL,
                    link_ids_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    policy_ids_json TEXT NOT NULL,
                    boundary_ids_json TEXT NOT NULL,
                    claim_states_json TEXT NOT NULL,
                    traceback_ids_json TEXT NOT NULL,
                    violation_match_ids_json TEXT NOT NULL,
                    node_count INTEGER NOT NULL,
                    edge_count INTEGER NOT NULL,
                    snapshot_kind TEXT NOT NULL
                );",
            )
            .unwrap();
            conn.execute(
                "INSERT OR REPLACE INTO causal_graph_snapshots (
                    snapshot_id, timestamp_ns, agent_id, link_ids_json, evidence_ids_json,
                    policy_ids_json, boundary_ids_json, claim_states_json, traceback_ids_json,
                    violation_match_ids_json, node_count, edge_count, snapshot_kind
                ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
                rusqlite::params![
                    snapshot
                        .get("snapshot_id")
                        .and_then(Value::as_str)
                        .unwrap_or("snap-1"),
                    snapshot
                        .get("timestamp_ns")
                        .and_then(Value::as_i64)
                        .unwrap_or(100),
                    snapshot
                        .get("agent_id")
                        .and_then(Value::as_str)
                        .unwrap_or("agent-1"),
                    &serde_json::to_string(
                        &snapshot
                            .get("evidence_ids")
                            .and_then(Value::as_array)
                            .cloned()
                            .unwrap_or_default()
                    )
                    .unwrap(),
                    &serde_json::to_string(
                        &snapshot
                            .get("evidence_ids")
                            .and_then(Value::as_array)
                            .cloned()
                            .unwrap_or_default()
                    )
                    .unwrap(),
                    &serde_json::to_string(
                        &snapshot
                            .get("policy_ids")
                            .and_then(Value::as_array)
                            .cloned()
                            .unwrap_or_default()
                    )
                    .unwrap(),
                    &serde_json::to_string(
                        &snapshot
                            .get("boundary_ids")
                            .and_then(Value::as_array)
                            .cloned()
                            .unwrap_or_default()
                    )
                    .unwrap(),
                    &serde_json::to_string(
                        &snapshot
                            .get("claim_states")
                            .and_then(Value::as_array)
                            .cloned()
                            .unwrap_or_default()
                    )
                    .unwrap(),
                    &serde_json::to_string(
                        &snapshot
                            .get("traceback_ids")
                            .and_then(Value::as_array)
                            .cloned()
                            .unwrap_or_default()
                    )
                    .unwrap(),
                    &serde_json::to_string(
                        &snapshot
                            .get("violation_match_ids")
                            .and_then(Value::as_array)
                            .cloned()
                            .unwrap_or_default()
                    )
                    .unwrap(),
                    snapshot
                        .get("node_count")
                        .and_then(Value::as_i64)
                        .unwrap_or(1),
                    snapshot
                        .get("edge_count")
                        .and_then(Value::as_i64)
                        .unwrap_or(1),
                    snapshot
                        .get("snapshot_kind")
                        .and_then(Value::as_str)
                        .unwrap_or("accountability"),
                ],
            )
            .unwrap();
        }

        fn write_replayed_correlation_evidence_db(&self, links: &[Value]) {
            self.write_named_causal_links_db("trace.log.replayed.causal.db", links);
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
                        handle_request(req, server_assets.clone(), server_sender.clone(), None, None)
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
