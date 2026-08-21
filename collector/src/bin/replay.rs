#![allow(dead_code)]
#![allow(unused_imports)]
// SPDX-License-Identifier: MIT
// Ablation replay tool: re-run Correlator on existing SQLite DB with signals disabled.
// Usage: cargo run --bin replay -- --sqlite <path> [--disable-signal argument_matching|process_lineage|temporal_proximity]

use clap::Parser;
use futures::StreamExt;
use futures::stream;
use mtagentlens::framework::analyzers::{Analyzer, SSEProcessor};
use mtagentlens::framework::replay::load_replayable_trace_events;
use mtagentlens::framework::runners::EventStream;
use mtagentlens::responsibility::graph::graph_pattern_matcher::{
    PatternMatcher, default_templates,
};
use mtagentlens::responsibility::{
    SignalGraph,
    evidence::{
        ActionCandidate, ActionCandidateType, CandidateDerivationMode, CorrelationEvidence,
        EvidenceCorrelator, Lossiness, MatchLevel, NonLinkDiagnostic, NonLinkReason, SignalClass,
    },
    judgment::evaluate_accountability_policy,
    persistence::{SqliteWriter, open_causal_db, open_db, open_db_read_only},
    types::support_types::{
        ActionEvent, AgentInstance, AlertId, BoundaryId, ClaimState, EventId, EvidenceDirectness,
        EvidenceId, EvidenceRecord, IntentAuthoritySource, IntentClass, IntentEvent,
        IntentParseState, NodeId, PolicyEvaluationResult, ProcessId, ResourceId, SnapshotData,
        TracebackData, TracebackId, ViolationPatternMatchRecord,
    },
};
use rusqlite::{Connection, Row, types::Type};
use std::fs::File;
use std::io::BufReader;
use std::path::{Path, PathBuf};

use serde_json::json;
use std::collections::HashSet;

fn replay_resource_from_policy_fields(
    candidate_type: Option<&str>,
    target: Option<&str>,
) -> Option<ResourceId> {
    match candidate_type {
        Some("file_read") | Some("file_write") | Some("file_overwrite") => Some(ResourceId::File {
            canonical_path: target.unwrap_or("(unknown path)").to_string(),
        }),
        Some("network_connect") => {
            let target = target.unwrap_or("unknown:0");
            let (host, port) = target
                .rsplit_once(':')
                .and_then(|(host, port)| port.parse::<u16>().ok().map(|port| (host, port)))
                .unwrap_or((target, 0));
            Some(ResourceId::Network {
                host: host.to_string(),
                port,
                scheme: None,
            })
        }
        Some("process_exec") => Some(ResourceId::Process {
            process_id: ProcessId {
                pid: 0,
                start_time_ns: 0,
            },
        }),
        Some(other) => Some(ResourceId::ToolCall {
            tool_name: target.unwrap_or(other).to_string(),
            call_id: None,
        }),
        None => None,
    }
}

#[derive(Parser)]
#[command(about = "Replay causal trace from SQLite or raw trace artifacts")]
struct Args {
    #[arg(long, conflicts_with_all = ["trace_log", "run_dir"])]
    sqlite: Option<String>,

    #[arg(long, conflicts_with_all = ["sqlite", "run_dir"])]
    trace_log: Option<String>,

    #[arg(long, conflicts_with_all = ["sqlite", "trace_log"])]
    run_dir: Option<String>,

    #[arg(long, value_delimiter = ',')]
    disable_signal: Vec<String>,

    #[arg(long, default_value_t = false)]
    persisted_graph_replay: bool,

    #[arg(long, default_value_t = false)]
    force: bool,
}

#[tokio::main]
async fn main() {
    let args = Args::parse();

    if let Some(sqlite_path) = &args.sqlite {
        replay_sqlite(sqlite_path, &args.disable_signal);
        return;
    }

    if let Some(trace_log_path) = &args.trace_log {
        let summary = replay_raw_trace(trace_log_path)
            .await
            .unwrap_or_else(|err| panic!("raw trace replay failed: {err}"));
        println!("Raw trace replay requested from: {}", trace_log_path);
        println!("Disabled signals: {:?}", args.disable_signal);
        println!("total_lines={}", summary.total_lines);
        println!("parsed_lines={}", summary.parsed_lines);
        println!("replayable_lines={}", summary.replayable_lines);
        println!("filtered_lines={}", summary.filtered_lines);
        println!("filtered_derived_lines={}", summary.filtered_derived_lines);
        println!("malformed_lines={}", summary.malformed_lines);
        println!("truthfulness_status={}", summary.truthfulness_status);
        if let Some(limitation) = &summary.truthfulness_limitation {
            println!("truthfulness_limitation={}", limitation);
        }
        println!("enabled_analyzers={}", summary.enabled_analyzers);
        println!("emitted_events={}", summary.emitted_events);
        if let Some(path) = &summary.causal_db_out {
            println!("causal_db_out={}", path);
        }
        if let Some(path) = &summary.causal_jsonl_out {
            println!("causal_jsonl_out={}", path);
        }
        return;
    }

    if let Some(run_dir) = &args.run_dir {
        match replay_run_dir(
            run_dir,
            &args.disable_signal,
            args.force,
            args.persisted_graph_replay,
        )
        .await
        {
            Ok(report) => {
                println!("run_dir={}", report.run_dir);
                println!("mode={}", report.mode);
                println!("status={}", report.status);
                println!(
                    "legacy_rederived_candidates={}",
                    report.legacy_rederived_candidates
                );
                if let Some(path) = &report.causal_db_path {
                    println!("causal_db_path={}", path);
                }
                if let Some(path) = &report.alerts_db_path {
                    println!("alerts_db_path={}", path);
                }
                if let Some(path) = &report.trace_log_path {
                    println!("trace_log_path={}", path);
                }
                println!("report_path={}", report.report_path);
            }
            Err(err) => {
                eprintln!("replay error: {err}");
            }
        }
        return;
    }

    eprintln!("one of --sqlite, --trace-log, or --run-dir is required");
    std::process::exit(2);
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RunDirReplayReport {
    run_dir: String,
    mode: String,
    status: String,
    legacy_rederived_candidates: bool,
    causal_db_path: Option<String>,
    alerts_db_path: Option<String>,
    trace_log_path: Option<String>,
    report_path: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RawTraceReplaySummary {
    total_lines: usize,
    parsed_lines: usize,
    replayable_lines: usize,
    filtered_lines: usize,
    filtered_derived_lines: usize,
    malformed_lines: usize,
    enabled_analyzers: String,
    emitted_events: usize,
    truthfulness_status: String,
    truthfulness_limitation: Option<String>,
    causal_db_out: Option<String>,
    causal_jsonl_out: Option<String>,
}

async fn replay_raw_trace(trace_log_path: &str) -> Result<RawTraceReplaySummary, String> {
    let file = File::open(trace_log_path)
        .map_err(|err| format!("failed to open trace log {trace_log_path}: {err}"))?;
    let reader = BufReader::new(file);
    let (events, stats) = load_replayable_trace_events(reader)?;

    if stats.skipped_derived > 0 {
        return Err(
            "persisted trace contains filtered derived events (for example sse_processor) and is unsuitable for truthful raw-only intent/link parity"
                .to_string(),
        );
    }

    if stats.malformed_lines > 0 {
        return Err(
            "persisted trace contains malformed persisted trace lines and is unsuitable for truthful raw-only intent/link parity"
                .to_string(),
        );
    }

    let input_stream: EventStream = Box::pin(stream::iter(events));
    let mut sse_processor = SSEProcessor::new_with_timeout(30_000);
    let sse_stream = sse_processor
        .process(input_stream)
        .await
        .map_err(|err| format!("sse replay failed: {err}"))?;

    let emitted_events: Vec<_> = sse_stream.collect().await;

    Ok(RawTraceReplaySummary {
        total_lines: stats.total_lines,
        parsed_lines: stats.parsed_events + stats.skipped_non_raw,
        replayable_lines: stats.parsed_events,
        filtered_lines: stats.skipped_non_raw,
        filtered_derived_lines: stats.skipped_derived,
        malformed_lines: stats.malformed_lines,
        enabled_analyzers: "SSEProcessor".to_string(),
        emitted_events: emitted_events.len(),
        truthfulness_status: "raw_only_truthful_parity_possible".to_string(),
        truthfulness_limitation: None,
        causal_db_out: None,
        causal_jsonl_out: None,
    })
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
    canonical.exists().then_some(canonical)
}

fn resolve_alerts_db_path(run_dir: &Path) -> Option<PathBuf> {
    if let Some(path) = resolve_artifact_from_summary(run_dir, "alerts_db") {
        return Some(path);
    }
    // Find any .db file in the run directory (responsibility DB)
    let mut candidates: Vec<_> = match std::fs::read_dir(run_dir) {
        Ok(entries) => entries
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.extension().is_some_and(|ext| ext == "db"))
            .filter(|p| {
                !p.file_name()
                    .and_then(|n| n.to_str())
                    .is_none_or(|n| n.contains("alerts"))
            })
            .collect(),
        Err(_) => return None,
    };
    candidates.sort();
    candidates.into_iter().next()
}

fn resolve_causal_db_path(run_dir: &Path) -> Option<PathBuf> {
    if let Some(path) = resolve_artifact_from_summary(run_dir, "causal_db") {
        return Some(path);
    }
    // Try old name first, then new responsibility architecture name
    for name in &[
        "trace.log.causal.db",
        "trace.log.responsibility.db",
        "responsibility.db",
    ] {
        let path = run_dir.join(name);
        if path.exists() {
            return Some(path);
        }
    }
    // Fallback: any .db file ending with .responsibility.db or .causal.db
    let mut candidates = std::fs::read_dir(run_dir)
        .ok()?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.extension().is_some_and(|ext| ext == "db"))
        .filter(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| {
                    name.ends_with(".responsibility.db") || name.ends_with(".causal.db")
                })
        })
        .collect::<Vec<_>>();
    candidates.sort();
    candidates.into_iter().next()
}

fn replay_via_persistence_reader(
    db_path: &str,
    disable_signal: &[String],
    persisted_graph_replay: bool,
) -> Result<RunDirReplayReport, String> {
    use mtagentlens::responsibility::persistence::PersistenceReader;

    let reader = PersistenceReader::open(db_path)
        .map_err(|e| format!("failed to open persistence reader: {e}"))?;

    let runs = reader
        .list_runs()
        .map_err(|e| format!("failed to list runs: {e}"))?;
    let run = runs
        .first()
        .ok_or_else(|| "no runs found in database".to_string())?
        .clone();

    let config = replay_config_from_disable_signal(disable_signal);
    let result = if persisted_graph_replay {
        mtagentlens::responsibility::replay::ReplayExecutor::reauthoritative_from_persisted_graph_with_config(
            &reader,
            &run.run_id,
            &config,
        )
        .map_err(|e| format!("persisted graph replay failed: {e}"))?
    } else {
        mtagentlens::responsibility::replay::ReplayExecutor::reauthoritative_with_config(
            &reader,
            &run.run_id,
            &config,
        )
        .map_err(|e| format!("reauthoritative replay failed: {e}"))?
    };

    let eval = &result.evaluation;
    eprintln!("\n=== Replay via PersistenceReader ===");
    eprintln!("  Run:               {}", run.run_id);
    eprintln!("  Evidence produced:  {}", result.evidence.len());
    eprintln!("  Alerts:             {}", result.alerts.len());
    eprintln!("  Identities:         {}", result.identities.len());
    eprintln!("  Claims:             {}", result.claims.len());
    eprintln!("  Tracebacks:         {}", result.tracebacks.len());
    eprintln!("  --- Evaluation Metrics ---");
    eprintln!("  Phase1 alerts:      {}", eval.phase1_alert_count);
    eprintln!("  Phase2 alerts:      {}", eval.phase2_alert_count);
    eprintln!("  Confirmed:          {}", eval.confirmed_count);
    eprintln!("  Changed:            {}", eval.changed_count);
    eprintln!("  Dismissed:          {}", eval.dismissed_count);
    eprintln!("  Discovered:         {}", eval.discovered_count);
    eprintln!("  Precision:          {:.3}", eval.precision);
    eprintln!("  Recall:             {:.3}", eval.recall);
    eprintln!("  F1:                 {:.3}", eval.f1);
    eprintln!("  Graph nodes:        {}", eval.node_count);
    eprintln!("  Graph edges:        {}", eval.edge_count);

    Ok(RunDirReplayReport {
        run_dir: db_path.to_string(),
        mode: if persisted_graph_replay {
            "persisted_graph_replay".to_string()
        } else if disable_signal.is_empty() {
            "replay".to_string()
        } else {
            "controlled_replay".to_string()
        },
        status: "completed".to_string(),
        legacy_rederived_candidates: false,
        causal_db_path: Some(db_path.to_string()),
        alerts_db_path: None,
        trace_log_path: None,
        report_path: String::new(),
    })
}

fn replay_config_from_disable_signal(
    disable_signal: &[String],
) -> mtagentlens::responsibility::replay::ReplayConfig {
    use mtagentlens::responsibility::replay::{ReplayConfig, all_action_candidate_types};
    use mtagentlens::responsibility::types::{
        ActionCandidateType as ReplayActionCandidateType, MatchLevel as ReplayMatchLevel,
    };

    let mut config = ReplayConfig::all_enabled();
    let mut exact_match_only = false;
    let mut disabled_match_levels = HashSet::new();
    let mut disabled_action_types = HashSet::new();
    for signal in disable_signal {
        match signal.as_str() {
            "exact_match_only" => {
                exact_match_only = true;
            }
            "disable_l2_normalized" => {
                disabled_match_levels.insert(ReplayMatchLevel::L2Normalized);
            }
            "disable_l3_containment" => {
                disabled_match_levels.insert(ReplayMatchLevel::L3Containment);
            }
            "disable_process_exec_actions" | "disable_process_exec" => {
                disabled_action_types.extend([
                    ReplayActionCandidateType::ProcessExec,
                    ReplayActionCandidateType::ProcessSpawn,
                ]);
            }
            "disable_network_actions" => {
                disabled_action_types.extend([
                    ReplayActionCandidateType::NetworkConnect,
                    ReplayActionCandidateType::NetworkSend,
                ]);
            }
            "disable_file_read_actions" => {
                disabled_action_types.insert(ReplayActionCandidateType::FileRead);
            }
            "disable_file_write_actions" => {
                disabled_action_types.extend([
                    ReplayActionCandidateType::FileWrite,
                    ReplayActionCandidateType::FileOverwrite,
                    ReplayActionCandidateType::FileDelete,
                ]);
            }
            "disable_file_actions" => {
                disabled_action_types.extend([
                    ReplayActionCandidateType::FileRead,
                    ReplayActionCandidateType::FileWrite,
                    ReplayActionCandidateType::FileOverwrite,
                    ReplayActionCandidateType::FileDelete,
                ]);
            }
            // ── v2 ablation signals ──────────────────────────────────
            "disable_identity_resolution" => {
                config.skip_identity_resolution = true;
            }
            "disable_graph_attribution" => {
                config.skip_attribution = true;
            }
            "disable_ai_pipeline" => {
                config.skip_ai_pipeline = true;
            }
            _ => {}
        }
    }
    if exact_match_only {
        config.enabled_match_levels = Some([ReplayMatchLevel::L1Exact].into_iter().collect());
    } else if !disabled_match_levels.is_empty() {
        config.enabled_match_levels = Some(
            [
                ReplayMatchLevel::L1Exact,
                ReplayMatchLevel::L2Normalized,
                ReplayMatchLevel::L3Containment,
                ReplayMatchLevel::L4Basename,
                ReplayMatchLevel::L5Substring,
            ]
            .into_iter()
            .filter(|level| !disabled_match_levels.contains(level))
            .collect(),
        );
    }
    if !disabled_action_types.is_empty() {
        config.enabled_action_types = Some(
            all_action_candidate_types()
                .into_iter()
                .filter(|action_type| !disabled_action_types.contains(action_type))
                .collect(),
        );
    }
    config
}

fn replayed_boundary_id(candidate: &ActionCandidate) -> Option<BoundaryId> {
    match candidate.candidate_type.to_string().as_str() {
        "file_read" => Some(BoundaryId("boundary.file_read".to_string())),
        "file_write" | "file_overwrite" => Some(BoundaryId("boundary.file_write".to_string())),
        "network_connect" => Some(BoundaryId("boundary.network_connect".to_string())),
        "process_exec" => Some(BoundaryId("boundary.process_exec".to_string())),
        _ => None,
    }
}

fn replayed_resource_id(candidate: &ActionCandidate) -> Option<ResourceId> {
    match candidate.candidate_type.to_string().as_str() {
        "file_read" | "file_write" | "file_overwrite" => candidate
            .normalized_args
            .get("path")
            .and_then(|v| v.as_str())
            .map(|path| ResourceId::File {
                canonical_path: path.to_string(),
            }),
        "network_connect" => Some(ResourceId::Network {
            host: candidate
                .normalized_args
                .get("host")
                .and_then(|v| v.as_str())?
                .to_string(),
            port: u16::try_from(
                candidate
                    .normalized_args
                    .get("port")
                    .and_then(|v| v.as_u64())?,
            )
            .ok()?,
            scheme: None,
        }),
        "process_exec" => candidate
            .normalized_args
            .get("path")
            .and_then(|v| v.as_str())
            .map(|path| ResourceId::File {
                canonical_path: path.to_string(),
            }),
        _ => None,
    }
}

fn build_replayed_evidence_records(
    replayed_links: &[CorrelationEvidence],
    candidate_lookup: &std::collections::HashMap<String, &ActionCandidate>,
) -> Vec<EvidenceRecord> {
    replayed_links
        .iter()
        .filter_map(|link| {
            let candidate = candidate_lookup.get(&link.candidate_id).copied()?;
            Some(EvidenceRecord {
                evidence_id: EvidenceId(format!("evidence:{}", link.evidence_id)),
                source_event_id: EventId(
                    candidate
                        .raw_event_refs
                        .first()
                        .cloned()
                        .unwrap_or_default(),
                ),
                directness: if candidate.raw_event_refs.len() == 1 {
                    EvidenceDirectness::Direct
                } else {
                    EvidenceDirectness::Inferred
                },
                rule_or_inference_id: format!("causal_link:{}", link.match_level),
                resource_id: replayed_resource_id(candidate),
                boundary_id: replayed_boundary_id(candidate),
                confidence: link.confidence,
                redaction_status: "none".to_string(),
                payload_ref: Some(link.evidence_id.clone()),
            })
        })
        .collect()
}

#[derive(Default)]
struct OriginalAgentData {
    tls_sessions: std::collections::HashSet<String>,
    conversation_id: Option<String>,
    root_processes: std::collections::HashSet<ProcessId>,
}

fn load_original_agent_data(
    conn: &rusqlite::Connection,
) -> std::collections::HashMap<String, OriginalAgentData> {
    let mut map = std::collections::HashMap::new();
    let mut stmt = match conn.prepare(
        "SELECT agent_id, root_processes_json, tls_sessions_json, conversation_id FROM agent_instances",
    ) {
        Ok(s) => s,
        Err(_) => return map,
    };
    let rows = stmt.query_map([], |row| {
        let agent_id: String = row.get(0)?;
        let root_json: String = row.get(1).unwrap_or_default();
        let tls_json: String = row.get(2).unwrap_or_default();
        let conv_id: Option<String> = row.get(3).unwrap_or(None);
        Ok((agent_id, root_json, tls_json, conv_id))
    });
    if let Ok(rows) = rows {
        for row in rows.flatten() {
            let (agent_id, root_json, tls_json, conv_id) = row;
            let tls_sessions: std::collections::HashSet<String> =
                serde_json::from_str(&tls_json).unwrap_or_default();
            let root_processes: std::collections::HashSet<ProcessId> =
                serde_json::from_str(&root_json).unwrap_or_default();
            map.insert(
                agent_id,
                OriginalAgentData {
                    tls_sessions,
                    conversation_id: conv_id,
                    root_processes,
                },
            );
        }
    }
    map
}

async fn materialize_replayed_causal_artifacts(
    run_dir_path: &Path,
    intents: &[IntentEvent],
    replay_inputs: &ReplayInputs,
    replay_order_hints: &std::collections::HashMap<String, u64>,
    original_agent_data: &std::collections::HashMap<String, OriginalAgentData>,
    force: bool,
) -> Result<(PathBuf, PathBuf), String> {
    let causal_db_out = run_dir_path.join("trace.log.replayed.causal.db");
    let causal_jsonl_out = run_dir_path.join("trace.log.replayed.causal.jsonl");
    let alerts_db_out = run_dir_path.join("trace.log.replayed.alerts.db");
    if force {
        for path in &[&causal_db_out, &causal_jsonl_out, &alerts_db_out] {
            let _ = std::fs::remove_file(path);
        }
    }
    ensure_fresh_replay_outputs(&[&causal_db_out, &causal_jsonl_out, &alerts_db_out])?;

    let conn = open_causal_db(&causal_db_out.to_string_lossy()).map_err(|err| {
        format!("failed to open replayed causal db for artifact materialization: {err}")
    })?;
    let shared_conn = std::sync::Arc::new(std::sync::Mutex::new(conn));
    let writer = SqliteWriter::new(shared_conn.clone());

    let mut persisted_agents = std::collections::BTreeSet::new();
    for intent in intents {
        if persisted_agents.insert(intent.agent_id.clone()) {
            let root_process = replay_inputs
                .inputs
                .iter()
                .find_map(|input| match input {
                    ReplayInput::Candidate(candidate)
                        if candidate.agent_id.as_deref() == Some(intent.agent_id.as_str()) =>
                    {
                        Some(candidate.process_id)
                    }
                    _ => None,
                })
                .unwrap_or_else(|| ProcessId::new(0, 0));
            let mut agent =
                AgentInstance::new(intent.agent_id.clone(), root_process, intent.timestamp_ns);
            // Preserve original TLS sessions and conversation ID from the live run
            if let Some(orig) = original_agent_data.get(&intent.agent_id) {
                for session in &orig.tls_sessions {
                    agent.tls_sessions.insert(session.clone());
                }
                if orig.conversation_id.is_some() {
                    agent.set_conversation_id(orig.conversation_id.clone().unwrap());
                } else if let Some(conv_id) = intent.conversation_id.clone() {
                    agent.set_conversation_id(conv_id);
                }
            } else if let Some(conversation_id) = intent.conversation_id.clone() {
                agent.set_conversation_id(conversation_id);
            }
            writer
                .write_agent(&agent)
                .map_err(|err| format!("failed to persist replayed agent artifact: {err}"))?;
        }
        writer
            .write_intent(intent)
            .map_err(|err| format!("failed to persist replayed intent artifact: {err}"))?;
    }

    // Persist all original agents that had TLS sessions or conversation IDs,
    // even if they didn't produce intents (preserves three-signal union-find data).
    for (agent_id, orig_data) in original_agent_data {
        if !persisted_agents.contains(agent_id)
            && (!orig_data.tls_sessions.is_empty() || orig_data.conversation_id.is_some())
        {
            let root_process = orig_data
                .root_processes
                .iter()
                .next()
                .cloned()
                .unwrap_or_else(|| ProcessId::new(0, 0));
            let mut agent = AgentInstance::new(agent_id.clone(), root_process, 0);
            for session in &orig_data.tls_sessions {
                agent.tls_sessions.insert(session.clone());
            }
            if let Some(conv_id) = &orig_data.conversation_id {
                agent.set_conversation_id(conv_id.clone());
            }
            writer
                .write_agent(&agent)
                .map_err(|err| format!("failed to persist replayed agent artifact: {err}"))?;
        }
    }
    for input in &replay_inputs.inputs {
        match input {
            ReplayInput::Candidate(candidate) => writer
                .write_candidate(candidate)
                .map_err(|err| format!("failed to persist replayed candidate artifact: {err}"))?,
        }
    }

    let candidate_lookup = replay_inputs
        .inputs
        .iter()
        .map(|input| match input {
            ReplayInput::Candidate(candidate) => (candidate.candidate_id.clone(), candidate),
        })
        .collect::<std::collections::HashMap<_, _>>();

    let mut correlator = EvidenceCorrelator::new();
    let graph = SignalGraph::new();
    let mut ii = 0usize;
    let mut ri = 0usize;
    while ii < intents.len() || ri < replay_inputs.inputs.len() {
        let take_intent = match (intents.get(ii), replay_inputs.inputs.get(ri)) {
            (Some(i), Some(r)) => {
                effective_intent_timestamp(i, replay_order_hints) <= replay_input_timestamp(r)
            }
            (Some(_), None) => true,
            _ => false,
        };

        if take_intent {
            for link in correlator.on_intent(intents[ii].clone(), &graph) {
                writer.write_correlation_evidence(&link).map_err(|err| {
                    format!("failed to persist replayed causal link artifact: {err}")
                })?;
            }
            ii += 1;
        } else {
            match &replay_inputs.inputs[ri] {
                ReplayInput::Candidate(candidate) => {
                    for link in correlator.on_candidate(candidate.clone(), &graph) {
                        writer.write_correlation_evidence(&link).map_err(|err| {
                            format!("failed to persist replayed causal link artifact: {err}")
                        })?;
                    }
                }
            }
            ri += 1;
        }
    }

    let replayed_links = {
        let guard = shared_conn.lock().unwrap();
        load_links_strict(&guard).map_err(|err| {
            format!("failed to reload replayed causal links for evidence materialization: {err}")
        })?
    };
    let replayed_evidence_records =
        build_replayed_evidence_records(&replayed_links, &candidate_lookup);
    let evidence_link_ids: HashSet<String> = replayed_evidence_records
        .iter()
        .filter_map(|record| record.payload_ref.clone())
        .collect();

    // Phase I: write_evidence_record and write_policy_evaluation_with_traceback removed
    // from SqliteWriter. responsibility_evidence_records and policy evaluations will be
    // populated by ResponsibilityWriter in a future phase when the new pipeline is wired.
    let _ = &replayed_evidence_records;
    let _ = &evidence_link_ids;

    for diagnostic in correlator.take_non_link_diagnostics() {
        writer
            .write_non_link_diagnostic(&diagnostic)
            .map_err(|err| {
                format!("failed to persist replayed non-link diagnostic artifact: {err}")
            })?;
    }

    let mut pattern_matcher = PatternMatcher::new(default_templates());
    for link in &replayed_links {
        for matched in pattern_matcher.on_link(link.clone()) {
            let record = ViolationPatternMatchRecord {
                match_id: format!(
                    "{}:{}:{}",
                    matched.template_id, matched.agent_id, matched.end_ns
                ),
                template_id: matched.template_id,
                agent_id: matched.agent_id,
                matched_link_ids: matched.matched_evidence_ids,
                matched_policies: matched.matched_policies,
                matched_boundaries: matched.matched_boundaries,
                matched_targets: matched.matched_targets,
                start_ns: matched.start_ns,
                end_ns: matched.end_ns,
                severity: matched.severity,
                description: matched.description,
            };
            writer
                .write_violation_pattern_match(&record)
                .map_err(|err| {
                    format!("failed to persist replayed violation pattern artifact: {err}")
                })?;
        }
    }

    std::fs::write(&causal_jsonl_out, b"")
        .map_err(|err| format!("failed to write replayed causal jsonl placeholder: {err}"))?;

    Ok((causal_db_out, causal_jsonl_out))
}

async fn replay_run_dir(
    run_dir: &str,
    disable_signal: &[String],
    force: bool,
    persisted_graph_replay: bool,
) -> Result<RunDirReplayReport, String> {
    let run_dir_path = Path::new(run_dir);
    let causal_db_path = resolve_causal_db_path(run_dir_path)
        .ok_or_else(|| format!("no causal db artifact found in {}", run_dir_path.display()))?;
    let trace_log_path = resolve_events_artifact_path(run_dir_path);
    let alerts_db_path = resolve_alerts_db_path(run_dir_path);

    // Try PersistenceReader-based replay for new responsibility schema
    let db_path_str = causal_db_path.to_string_lossy().to_string();
    if db_path_str.contains("responsibility.db") {
        match replay_via_persistence_reader(&db_path_str, disable_signal, persisted_graph_replay) {
            Ok(report) => return Ok(report),
            Err(e) => eprintln!("PersistenceReader replay failed (falling back to SQL): {e}"),
        }
    }

    let conn = open_db_read_only(&db_path_str)
        .map_err(|err| format!("failed to open causal db read-only: {err}"))?;
    let replay_inputs = load_replay_inputs_strict(&conn).map_err(|err| {
        format!("authoritative SQLite replay failed to load replay inputs: {err}")
    })?;
    let intents = load_intents_strict(&conn).map_err(|err| {
        format!("authoritative SQLite replay failed to load intent_events: {err}")
    })?;
    let persisted_links = load_links_strict(&conn).map_err(|err| {
        format!("authoritative SQLite replay failed to load correlation_evidence: {err}")
    })?;
    let persisted_non_link_diagnostics =
        load_non_link_diagnostics_strict(&conn).map_err(|err| {
            format!("authoritative SQLite replay failed to load non_link_diagnostics: {err}")
        })?;
    let persisted_evidence_records = load_evidence_records(&conn);
    let persisted_violation_patterns = load_violation_pattern_matches(&conn);
    let persisted_snapshots = load_accountability_snapshots(&conn);

    let replay_order_hints = build_intent_replay_order_hints(&intents, &persisted_links);
    let mut correlator = EvidenceCorrelator::new();
    let graph = SignalGraph::new();
    let mut replayed_links = Vec::new();
    let mut ii = 0usize;
    let mut ri = 0usize;
    while ii < intents.len() || ri < replay_inputs.inputs.len() {
        let take_intent = match (intents.get(ii), replay_inputs.inputs.get(ri)) {
            (Some(i), Some(r)) => {
                effective_intent_timestamp(i, &replay_order_hints) <= replay_input_timestamp(r)
            }
            (Some(_), None) => true,
            _ => false,
        };

        if take_intent {
            let links = correlator.on_intent(intents[ii].clone(), &graph);
            replayed_links.extend(links);
            ii += 1;
        } else {
            match &replay_inputs.inputs[ri] {
                ReplayInput::Candidate(candidate) => {
                    let links = correlator.on_candidate(candidate.clone(), &graph);
                    replayed_links.extend(links);
                }
            }
            ri += 1;
        }
    }
    let replayed_non_link_diagnostics = correlator.take_non_link_diagnostics();
    let link_report = compare_links(&persisted_links, &replayed_links);
    let candidate_lookup = replay_inputs
        .inputs
        .iter()
        .map(|input| match input {
            ReplayInput::Candidate(candidate) => (candidate.candidate_id.clone(), candidate),
        })
        .collect::<std::collections::HashMap<_, _>>();
    let replayed_evidence_records =
        build_replayed_evidence_records(&replayed_links, &candidate_lookup);
    let replayed_violation_patterns = build_replayed_violation_pattern_records(&replayed_links);
    let evidence_report =
        compare_evidence_records(&persisted_evidence_records, &replayed_evidence_records);
    let violation_patterns_report = compare_violation_pattern_matches(
        &persisted_violation_patterns,
        &replayed_violation_patterns,
    );
    let semantic_diagnostic_report = semantic_non_link_diagnostics_report(
        &persisted_links,
        &persisted_non_link_diagnostics,
        &replayed_non_link_diagnostics,
    );
    let noise_summary = summarize_noise(
        &persisted_links,
        &persisted_non_link_diagnostics,
        &replayed_non_link_diagnostics,
    );

    let original_agent_data = load_original_agent_data(&conn);
    let (replayed_causal_db_path, replayed_causal_jsonl_path) =
        materialize_replayed_causal_artifacts(
            run_dir_path,
            &intents,
            &replay_inputs,
            &replay_order_hints,
            &original_agent_data,
            force,
        )
        .await?;
    let replayed_alerts_db_path = run_dir_path.join("trace.log.replayed.alerts.db");
    let replayed_conn = open_db_read_only(&replayed_causal_db_path.to_string_lossy())
        .map_err(|err| format!("failed to open replayed causal db read-only: {err}"))?;
    let replayed_snapshots = load_accountability_snapshots(&replayed_conn);
    let snapshot_report =
        compare_accountability_snapshots(&persisted_snapshots, &replayed_snapshots);

    let mut gap_sections = Vec::new();
    if has_parity_gap(&link_report) {
        gap_sections.push("causal_links_comparison");
    }
    if has_parity_gap(&semantic_diagnostic_report) {
        gap_sections.push("non_link_diagnostics_comparison");
    }
    if has_parity_gap(&violation_patterns_report) {
        gap_sections.push("violation_patterns_comparison");
    }
    if has_parity_gap(&evidence_report) {
        gap_sections.push("evidence_comparison");
    }
    if has_parity_gap(&snapshot_report) {
        gap_sections.push("snapshot_comparison");
    }
    let status = if gap_sections.is_empty() {
        "ok"
    } else {
        "parity_gap"
    };

    let report_path = run_dir_path.join("replay.report.json");
    let report_json = json!({
        "run_dir": run_dir_path,
        "mode": if disable_signal.is_empty() { "authoritative_sqlite" } else { "ablation_sqlite" },
        "status": status,
        "disable_signal": disable_signal,
        "legacy_rederived_candidates": replay_inputs.legacy_rederived_candidates,
        "gap_sections": gap_sections,
        "artifacts": {
            "causal_db": causal_db_path,
            "alerts_db": alerts_db_path,
            "replayed_alerts_db": replayed_alerts_db_path,
            "trace_log": trace_log_path,
            "replayed_causal_db": replayed_causal_db_path,
            "replayed_causal_jsonl": replayed_causal_jsonl_path,
        },
        "noise_summary": {
            "linked_intents_with_no_argument_match_count": noise_summary.linked_intents_with_no_argument_match_count,
            "persisted_non_link_diagnostics_count": noise_summary.persisted_non_link_diagnostics_count,
            "replayed_non_link_diagnostics_count": noise_summary.replayed_non_link_diagnostics_count,
            "reasons": noise_summary.reasons,
        },
        "causal_links_comparison": {
            "persisted_count": link_report.persisted_count,
            "replayed_count": link_report.replayed_count,
            "matched_count": link_report.matched_count,
            "missing_from_replay": link_report.missing_from_replay,
            "extra_in_replay": link_report.extra_in_replay,
        },
        "non_link_diagnostics_comparison": {
            "persisted_count": semantic_diagnostic_report.persisted_count,
            "replayed_count": semantic_diagnostic_report.replayed_count,
            "matched_count": semantic_diagnostic_report.matched_count,
            "missing_from_replay": semantic_diagnostic_report.missing_from_replay,
            "extra_in_replay": semantic_diagnostic_report.extra_in_replay,
        },
        "violation_patterns_comparison": {
            "persisted_count": violation_patterns_report.persisted_count,
            "replayed_count": violation_patterns_report.replayed_count,
            "matched_count": violation_patterns_report.matched_count,
            "missing_from_replay": violation_patterns_report.missing_from_replay,
            "extra_in_replay": violation_patterns_report.extra_in_replay,
        },
        "evidence_comparison": {
            "persisted_count": evidence_report.persisted_count,
            "replayed_count": evidence_report.replayed_count,
            "matched_count": evidence_report.matched_count,
            "missing_from_replay": evidence_report.missing_from_replay,
            "extra_in_replay": evidence_report.extra_in_replay,
        },
        "snapshot_comparison": {
            "persisted_count": snapshot_report.persisted_count,
            "replayed_count": snapshot_report.replayed_count,
            "matched_count": snapshot_report.matched_count,
            "missing_from_replay": snapshot_report.missing_from_replay,
            "extra_in_replay": snapshot_report.extra_in_replay,
        }
    });
    std::fs::write(
        &report_path,
        serde_json::to_vec_pretty(&report_json).map_err(|err| err.to_string())?,
    )
    .map_err(|err| format!("failed to write replay report: {err}"))?;

    Ok(RunDirReplayReport {
        run_dir: run_dir_path.to_string_lossy().into_owned(),
        mode: if disable_signal.is_empty() {
            "authoritative_sqlite".to_string()
        } else {
            "ablation_sqlite".to_string()
        },
        status: status.to_string(),
        legacy_rederived_candidates: replay_inputs.legacy_rederived_candidates,
        causal_db_path: Some(causal_db_path.to_string_lossy().into_owned()),
        alerts_db_path: alerts_db_path.map(|path| path.to_string_lossy().into_owned()),
        trace_log_path: trace_log_path.map(|path| path.to_string_lossy().into_owned()),
        report_path: report_path.to_string_lossy().into_owned(),
    })
}

fn ensure_fresh_replay_outputs(paths: &[&PathBuf]) -> Result<(), String> {
    let existing_paths: Vec<String> = paths
        .iter()
        .filter(|path| path.exists())
        .map(|path| path.to_string_lossy().into_owned())
        .collect();

    if existing_paths.is_empty() {
        return Ok(());
    }

    Err(format!(
        "refusing to replay into existing deterministic outputs: {} already exists",
        existing_paths.join(", ")
    ))
}

fn replay_sqlite(sqlite_path: &str, disable_signal: &[String]) {
    let conn = open_db_read_only(sqlite_path).expect("Failed to open SQLite DB read-only");

    let disable_arg_match = disable_signal.iter().any(|s| s == "argument_matching");
    let _disable_temporal = disable_signal.iter().any(|s| s == "temporal_proximity");

    println!("Replaying from: {}", sqlite_path);
    println!("Disabled signals: {:?}", disable_signal);

    let intents = load_intents_strict(&conn)
        .expect("authoritative SQLite replay failed to load intent_events");
    let replay_inputs = load_replay_inputs_strict(&conn)
        .expect("authoritative SQLite replay failed to load replay inputs");
    let persisted_links = load_links_strict(&conn)
        .expect("authoritative SQLite replay failed to load correlation_evidence");
    let persisted_non_link_diagnostics = load_non_link_diagnostics_strict(&conn)
        .expect("authoritative SQLite replay failed to load non_link_diagnostics");
    let persisted_evidence_records = load_evidence_records(&conn);

    println!(
        "legacy_rederived_candidates={}",
        replay_inputs.legacy_rederived_candidates
    );

    let replay_order_hints = build_intent_replay_order_hints(&intents, &persisted_links);
    let mut correlator = EvidenceCorrelator::new();
    let graph = SignalGraph::new();
    let mut replayed_links = Vec::new();
    let mut link_count = 0;

    let mut ii = 0;
    let mut ri = 0;
    while ii < intents.len() || ri < replay_inputs.inputs.len() {
        let take_intent = match (intents.get(ii), replay_inputs.inputs.get(ri)) {
            (Some(i), Some(r)) => {
                effective_intent_timestamp(i, &replay_order_hints) <= replay_input_timestamp(r)
            }
            (Some(_), None) => true,
            _ => false,
        };

        if take_intent {
            if !disable_arg_match {
                let links = correlator.on_intent(intents[ii].clone(), &graph);
                link_count += links.len();
                replayed_links.extend(links.iter().cloned());
                for link in &links {
                    println!(
                        "LINK intent={} candidate={} conf={:.2} level={:?}",
                        &link.intent_id[..8],
                        &link.candidate_id[..8],
                        link.confidence,
                        link.match_level
                    );
                }
            }
            ii += 1;
        } else {
            let links = match &replay_inputs.inputs[ri] {
                ReplayInput::Candidate(candidate) => {
                    correlator.on_candidate(candidate.clone(), &graph)
                }
            };
            link_count += links.len();
            replayed_links.extend(links.iter().cloned());
            for link in &links {
                println!(
                    "LINK intent={} candidate={} conf={:.2} level={:?}",
                    &link.intent_id[..8],
                    &link.candidate_id[..8],
                    link.confidence,
                    link.match_level
                );
            }
            ri += 1;
        }
    }

    let replayed_non_link_diagnostics = correlator.take_non_link_diagnostics();
    let replayed_violation_patterns = build_replayed_violation_pattern_records(&replayed_links);
    let persisted_violation_patterns = load_violation_pattern_matches(&conn);
    let persisted_link_report = compare_links(&persisted_links, &replayed_links);
    let persisted_diagnostic_report = semantic_non_link_diagnostics_report(
        &persisted_links,
        &persisted_non_link_diagnostics,
        &replayed_non_link_diagnostics,
    );
    let persisted_evidence_report = sqlite_evidence_comparison_report(
        &persisted_evidence_records,
        &replayed_links,
        &replay_inputs,
    );
    let persisted_violation_pattern_report = compare_violation_pattern_matches(
        &persisted_violation_patterns,
        &replayed_violation_patterns,
    );

    println!("\nTotal causal links: {}", link_count);
    println!("Intents processed: {}", intents.len());
    println!("Replay inputs processed: {}", replay_inputs.inputs.len());
    println!(
        "{}",
        format_artifact_report("links", &persisted_link_report)
    );
    println!(
        "{}",
        format_artifact_report("non_link_diagnostics", &persisted_diagnostic_report)
    );
    println!(
        "{}",
        format_artifact_report("violation_patterns", &persisted_violation_pattern_report)
    );
    println!(
        "{}",
        format_artifact_report("evidence_records", &persisted_evidence_report)
    );
}

#[derive(Debug, Clone)]
enum ReplayInput {
    Candidate(ActionCandidate),
}

#[derive(Debug, Clone)]
struct ReplayInputs {
    inputs: Vec<ReplayInput>,
    legacy_rederived_candidates: bool,
}

fn replay_input_timestamp(input: &ReplayInput) -> u64 {
    match input {
        ReplayInput::Candidate(candidate) => candidate.timestamp_ns,
    }
}

fn build_intent_replay_order_hints(
    intents: &[IntentEvent],
    persisted_links: &[CorrelationEvidence],
) -> std::collections::HashMap<String, u64> {
    let intent_by_id = intents
        .iter()
        .map(|intent| (intent.intent_id.as_str(), intent))
        .collect::<std::collections::HashMap<_, _>>();
    let mut links_by_candidate =
        std::collections::HashMap::<&str, Vec<&CorrelationEvidence>>::new();
    for link in persisted_links {
        links_by_candidate
            .entry(link.candidate_id.as_str())
            .or_default()
            .push(link);
    }

    let mut hints = std::collections::HashMap::new();
    for links in links_by_candidate.values() {
        let mut linked_intents = links
            .iter()
            .filter_map(|link| {
                let intent = intent_by_id.get(link.intent_id.as_str())?;
                Some((
                    intent.intent_id.clone(),
                    intent.timestamp_ns,
                    link.timestamp_ns,
                    link.authority_source.clone(),
                ))
            })
            .collect::<Vec<_>>();
        linked_intents.sort_by(|a, b| a.1.cmp(&b.1).then_with(|| a.0.cmp(&b.0)));

        if linked_intents.len() == 1 {
            let (intent_id, intent_ts, link_ts, authority_source) = &linked_intents[0];
            if *intent_ts > *link_ts
                && authority_source.as_deref() == Some("http_response_tool_use")
            {
                hints
                    .entry(intent_id.clone())
                    .and_modify(|existing: &mut u64| {
                        *existing = (*existing).min(link_ts.saturating_sub(1))
                    })
                    .or_insert_with(|| link_ts.saturating_sub(1));
            }
            continue;
        }

        if let Some((intent_id, intent_ts, link_ts, _)) = linked_intents.first()
            && *intent_ts > *link_ts
        {
            hints
                .entry(intent_id.clone())
                .and_modify(|existing: &mut u64| {
                    *existing = (*existing).min(link_ts.saturating_sub(1))
                })
                .or_insert_with(|| link_ts.saturating_sub(1));
        }

        for (offset, (intent_id, intent_ts, link_ts, _)) in
            linked_intents.iter().enumerate().skip(1)
        {
            if *intent_ts > *link_ts {
                let hinted_ts = link_ts.saturating_add(offset as u64);
                hints
                    .entry(intent_id.clone())
                    .and_modify(|existing: &mut u64| *existing = (*existing).min(hinted_ts))
                    .or_insert(hinted_ts);
            }
        }
    }
    hints
}

fn effective_intent_timestamp(
    intent: &IntentEvent,
    replay_order_hints: &std::collections::HashMap<String, u64>,
) -> u64 {
    replay_order_hints
        .get(&intent.intent_id)
        .copied()
        .unwrap_or(intent.timestamp_ns)
}

fn load_replay_inputs(conn: &Connection) -> ReplayInputs {
    let candidates = load_candidates(conn);
    if !candidates.is_empty() {
        return ReplayInputs {
            inputs: candidates.into_iter().map(ReplayInput::Candidate).collect(),
            legacy_rederived_candidates: false,
        };
    }

    ReplayInputs {
        inputs: load_actions(conn)
            .into_iter()
            .filter_map(|action| ActionCandidate::from_action_event(&action))
            .map(ReplayInput::Candidate)
            .collect(),
        legacy_rederived_candidates: true,
    }
}

fn load_replay_inputs_strict(conn: &Connection) -> rusqlite::Result<ReplayInputs> {
    let candidates = load_candidates_strict(conn)?;
    if !candidates.is_empty() {
        return Ok(ReplayInputs {
            inputs: candidates.into_iter().map(ReplayInput::Candidate).collect(),
            legacy_rederived_candidates: false,
        });
    }

    Ok(ReplayInputs {
        inputs: load_actions_strict(conn)?
            .into_iter()
            .filter_map(|action| ActionCandidate::from_action_event(&action))
            .map(ReplayInput::Candidate)
            .collect(),
        legacy_rederived_candidates: true,
    })
}

fn load_links(conn: &Connection) -> Vec<CorrelationEvidence> {
    let has_links_table = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='correlation_evidence'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count == 1)
        .unwrap_or(false);
    if !has_links_table {
        return Vec::new();
    }

    let mut stmt = conn
        .prepare(
            "SELECT schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence,
                    match_level, matched_fields_json, supporting_raw_event_refs_json, conversation_id,
                    authority_source, message_id, source_event_ref, debug_notes, derivation_mode
             FROM correlation_evidence ORDER BY timestamp_ns",
        )
        .unwrap();

    stmt.query_map([], load_link_row)
        .unwrap()
        .filter_map(|r| match r {
            Ok(link) => Some(link),
            Err(err) => {
                eprintln!("Skipping malformed causal link row during replay: {err}");
                None
            }
        })
        .collect()
}

fn load_links_strict(conn: &Connection) -> rusqlite::Result<Vec<CorrelationEvidence>> {
    let has_links_table = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='correlation_evidence'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count == 1)?;
    if !has_links_table {
        return Ok(Vec::new());
    }

    let mut stmt = conn.prepare(
        "SELECT schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence,
                match_level, matched_fields_json, supporting_raw_event_refs_json, conversation_id,
                authority_source, message_id, source_event_ref, debug_notes, derivation_mode
         FROM correlation_evidence ORDER BY timestamp_ns",
    )?;
    let rows = stmt.query_map([], load_link_row)?;
    rows.collect()
}

fn load_evidence_records(conn: &Connection) -> Vec<EvidenceRecord> {
    let has_table = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='evidence_records'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count == 1)
        .unwrap_or(false);
    if !has_table {
        return Vec::new();
    }

    let mut stmt = conn
        .prepare(
            "SELECT evidence_id, source_event_id, directness, rule_or_inference_id, resource_id, boundary_id, confidence, redaction_status, payload_ref
             FROM evidence_records ORDER BY evidence_id",
        )
        .unwrap();

    stmt.query_map([], load_evidence_record_row)
        .unwrap()
        .filter_map(|r| match r {
            Ok(record) => Some(record),
            Err(err) => {
                eprintln!("Skipping malformed evidence record row during replay: {err}");
                None
            }
        })
        .collect()
}

fn load_non_link_diagnostics(conn: &Connection) -> Vec<NonLinkDiagnostic> {
    let has_table = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='non_link_diagnostics'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count == 1)
        .unwrap_or(false);
    if !has_table {
        return Vec::new();
    }

    let mut stmt = conn
        .prepare(
            "SELECT schema_version, diagnostic_id, timestamp_ns, agent_id, intent_id, candidate_id, reason, stage,
                    intent_class, parse_state, match_level_considered, confidence_observed, details_json
             FROM non_link_diagnostics ORDER BY timestamp_ns",
        )
        .unwrap();

    stmt.query_map([], load_non_link_diagnostic_row)
        .unwrap()
        .filter_map(|r| match r {
            Ok(diagnostic) => Some(diagnostic),
            Err(err) => {
                eprintln!("Skipping malformed non-link diagnostic row during replay: {err}");
                None
            }
        })
        .collect()
}

fn load_non_link_diagnostics_strict(conn: &Connection) -> rusqlite::Result<Vec<NonLinkDiagnostic>> {
    let has_table = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='non_link_diagnostics'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count == 1)?;
    if !has_table {
        return Ok(Vec::new());
    }

    let mut stmt = conn.prepare(
        "SELECT schema_version, diagnostic_id, timestamp_ns, agent_id, intent_id, candidate_id, reason, stage,
                intent_class, parse_state, match_level_considered, confidence_observed, details_json
         FROM non_link_diagnostics ORDER BY timestamp_ns",
    )?;
    let rows = stmt.query_map([], load_non_link_diagnostic_row_strict)?;
    rows.collect()
}

fn load_non_link_diagnostic_row_strict(row: &Row<'_>) -> rusqlite::Result<NonLinkDiagnostic> {
    let schema_version = decode_u32_field(row, 0, "schema_version")?;
    let timestamp_ns = decode_u64_field(row, 2, "timestamp_ns")?;
    let reason = parse_non_link_reason(&row.get::<_, String>(6)?)?;
    let intent_class: Option<String> = row.get(8)?;
    let parse_state: Option<String> = row.get(9)?;
    let details = row
        .get::<_, Option<String>>(12)?
        .map(|value| {
            serde_json::from_str(&value).map_err(|err| {
                rusqlite::Error::FromSqlConversionFailure(
                    12,
                    Type::Text,
                    format!("invalid details_json for replay non_link_diagnostic row: {err}")
                        .into(),
                )
            })
        })
        .transpose()?;

    Ok(NonLinkDiagnostic {
        schema_version,
        diagnostic_id: row.get(1)?,
        timestamp_ns,
        agent_id: row.get(3)?,
        intent_id: row.get(4)?,
        candidate_id: row.get(5)?,
        reason,
        stage: row.get(7)?,
        intent_class,
        parse_state,
        match_level_considered: row.get(10)?,
        confidence_observed: row.get(11)?,
        details,
    })
}

fn load_intents(conn: &Connection) -> Vec<IntentEvent> {
    let select = select_intent_events_query(conn).unwrap_or(LEGACY_INTENT_EVENTS_SELECT);
    let mut stmt = conn.prepare(select).unwrap();

    stmt.query_map([], load_intent_row)
        .unwrap()
        .filter_map(|r| match r {
            Ok(intent) => Some(intent),
            Err(err) => {
                eprintln!("Skipping malformed intent row during replay: {err}");
                None
            }
        })
        .collect()
}

fn load_intents_strict(conn: &Connection) -> rusqlite::Result<Vec<IntentEvent>> {
    let select = select_intent_events_query(conn)?;
    let mut stmt = conn.prepare(select)?;

    let rows = stmt.query_map([], load_intent_row_strict)?;
    rows.collect()
}

fn load_actions(conn: &Connection) -> Vec<ActionEvent> {
    let select = if has_table(conn, "actions").unwrap_or(false) {
        RESPONSIBILITY_ACTIONS_SELECT
    } else {
        "SELECT action_id, agent_id, timestamp_ns, pid, process_start_time_ns, syscall, args_json, comm FROM action_events ORDER BY timestamp_ns"
    };
    let mut stmt = conn.prepare(select).unwrap();

    stmt.query_map([], load_action_row)
        .unwrap()
        .filter_map(|r| match r {
            Ok(action) => Some(action),
            Err(err) => {
                eprintln!("Skipping malformed action row during replay: {err}");
                None
            }
        })
        .collect()
}

fn load_actions_strict(conn: &Connection) -> rusqlite::Result<Vec<ActionEvent>> {
    let select = if has_table(conn, "actions").unwrap_or(false) {
        RESPONSIBILITY_ACTIONS_SELECT
    } else {
        "SELECT action_id, agent_id, timestamp_ns, pid, process_start_time_ns, syscall, args_json, comm FROM action_events ORDER BY timestamp_ns"
    };
    let mut stmt = conn.prepare(select)?;
    let rows = stmt.query_map([], load_action_row_strict)?;
    rows.collect()
}

fn load_candidates(conn: &Connection) -> Vec<ActionCandidate> {
    let has_candidates_table = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('action_candidates','actions')",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count > 0)
        .unwrap_or(false);
    if !has_candidates_table {
        return Vec::new();
    }

    let mut stmt = conn
        .prepare(
            "SELECT candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid,
                    process_start_time_ns, normalized_args_json, raw_event_refs_json,
                    derivation_mode, lossiness, aggregate_window_ns, aggregate_rule_name,
                    signal_class, debug_notes
             FROM action_candidates ORDER BY timestamp_ns",
        )
        .unwrap();

    stmt.query_map([], load_candidate_row)
        .unwrap()
        .filter_map(|r| match r {
            Ok(candidate) => Some(candidate),
            Err(err) => {
                eprintln!("Skipping malformed action candidate row during replay: {err}");
                None
            }
        })
        .collect()
}

fn load_candidates_strict(conn: &Connection) -> rusqlite::Result<Vec<ActionCandidate>> {
    let has_candidates_table = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='action_candidates'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count == 1)?;
    if !has_candidates_table {
        return Ok(Vec::new());
    }

    let select = if has_table(conn, "actions").unwrap_or(false) {
        // Map new schema actions to old action_candidates columns
        "SELECT action_id as candidate_id, 1 as schema_version, '' as agent_id, timestamp_ns, \
         CASE action_type \
           WHEN 'FileRead' THEN 'file_read' \
           WHEN 'FileWrite' THEN 'file_write' \
           WHEN 'FileOverwrite' THEN 'file_overwrite' \
           WHEN 'FileDelete' THEN 'file_delete' \
           WHEN 'NetworkConnect' THEN 'network_connect' \
           WHEN 'NetworkSend' THEN 'network_send' \
           WHEN 'ProcessExec' THEN 'process_exec' \
           WHEN 'ProcessSpawn' THEN 'process_spawn' \
           ELSE action_type \
         END as candidate_type, pid, process_start_time_ns, \
         normalized_args_json, '[]' as raw_event_refs_json, \
         derivation_mode, '' as lossiness, 0 as aggregate_window_ns, '' as aggregate_rule_name, \
         signal_class, '' as debug_notes \
         FROM actions ORDER BY timestamp_ns"
    } else {
        "SELECT candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid,
                process_start_time_ns, normalized_args_json, raw_event_refs_json,
                derivation_mode, lossiness, aggregate_window_ns, aggregate_rule_name,
                signal_class, debug_notes
         FROM action_candidates ORDER BY timestamp_ns"
    };
    let mut stmt = conn.prepare(select)?;

    let rows = stmt.query_map([], load_candidate_row)?;
    rows.collect()
}

const LEGACY_INTENT_EVENTS_SELECT: &str = "SELECT intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id FROM intent_events ORDER BY timestamp_ns";
const PROVENANCE_INTENT_EVENTS_SELECT: &str = "SELECT intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id, message_id, source_event_ref, authority_source FROM intent_events ORDER BY timestamp_ns";

/// New responsibility architecture schema (§13.1): intents table.
const RESPONSIBILITY_INTENTS_SELECT: &str = "\
SELECT intent_id, '' as agent_id, timestamp_ns, tool_name, tool_args_json, \
  CASE intent_class \
    WHEN 'SideEffect' THEN 'side_effect' \
    WHEN 'Orchestration' THEN 'orchestration' \
    WHEN 'Unknown' THEN 'unknown' \
    ELSE intent_class \
  END as intent_class, \
  CASE parse_state \
    WHEN 'Complete' THEN 'complete' \
    WHEN 'Partial' THEN 'partial' \
    WHEN 'Malformed' THEN 'malformed' \
    ELSE parse_state \
  END as parse_state, \
  primary_matching_eligible, \
  message_id as conversation_id, message_id, '' as source_event_ref, authority_source \
FROM intents ORDER BY timestamp_ns";

/// New responsibility architecture schema: actions table (mapped to action_candidates for replay).
const RESPONSIBILITY_ACTIONS_SELECT: &str = "\
SELECT action_id, '' as agent_id, timestamp_ns, pid, process_start_time_ns, \
  CASE action_type \
    WHEN 'FileRead' THEN 'file_read' \
    WHEN 'FileWrite' THEN 'file_write' \
    WHEN 'FileOverwrite' THEN 'file_overwrite' \
    WHEN 'FileDelete' THEN 'file_delete' \
    WHEN 'NetworkConnect' THEN 'network_connect' \
    WHEN 'NetworkSend' THEN 'network_send' \
    WHEN 'ProcessExec' THEN 'process_exec' \
    WHEN 'ProcessSpawn' THEN 'process_spawn' \
    ELSE action_type \
  END as syscall, \
  normalized_args_json as args_json, '' as comm \
FROM actions ORDER BY timestamp_ns";

fn select_intent_events_query(conn: &Connection) -> rusqlite::Result<&'static str> {
    // Try new responsibility schema first
    if has_table(conn, "intents")? {
        return Ok(RESPONSIBILITY_INTENTS_SELECT);
    }
    // Fall back to old causal schema
    let has_message_id = has_column(conn, "intent_events", "message_id")?;
    let has_source_event_ref = has_column(conn, "intent_events", "source_event_ref")?;
    let has_authority_source = has_column(conn, "intent_events", "authority_source")?;
    if has_message_id && has_source_event_ref && has_authority_source {
        Ok(PROVENANCE_INTENT_EVENTS_SELECT)
    } else {
        Ok(LEGACY_INTENT_EVENTS_SELECT)
    }
}

fn has_table(conn: &Connection, table: &str) -> rusqlite::Result<bool> {
    let count: i64 = conn.query_row(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?1",
        [table],
        |r| r.get(0),
    )?;
    Ok(count > 0)
}

fn has_column(conn: &Connection, table: &str, column: &str) -> rusqlite::Result<bool> {
    let pragma = format!("PRAGMA table_info({table})");
    let mut stmt = conn.prepare(&pragma)?;
    let mut rows = stmt.query([])?;
    while let Some(row) = rows.next()? {
        let name: String = row.get(1)?;
        if name == column {
            return Ok(true);
        }
    }
    Ok(false)
}

fn parse_intent_authority_source_strict(value: &str) -> rusqlite::Result<IntentAuthoritySource> {
    match value {
        "request_history_assistant_tool_use" => {
            Ok(IntentAuthoritySource::RequestHistoryAssistantToolUse)
        }
        "sse_response_tool_use" => Ok(IntentAuthoritySource::SseResponseToolUse),
        "http_response_tool_use" => Ok(IntentAuthoritySource::HttpResponseToolUse),
        "text_fallback" => Ok(IntentAuthoritySource::TextFallback),
        _ => Err(rusqlite::Error::FromSqlConversionFailure(
            11,
            Type::Text,
            format!("invalid authority_source for replay row: {value}").into(),
        )),
    }
}

fn parse_intent_authority_source(value: &str) -> IntentAuthoritySource {
    parse_intent_authority_source_strict(value)
        .unwrap_or(IntentAuthoritySource::HttpResponseToolUse)
}

fn build_intent_event(
    row: &Row<'_>,
    args: serde_json::Value,
    intent_class: IntentClass,
    parse_state: IntentParseState,
) -> rusqlite::Result<IntentEvent> {
    let persisted_primary_matching_eligible = row.get::<_, i64>(7)? != 0;

    let mut event = IntentEvent::new(
        row.get(0)?,
        row.get(1)?,
        decode_u64_field(row, 2, "timestamp_ns")?,
        row.get(3)?,
        args,
    )
    .with_intent_class(intent_class)
    .with_parse_state(parse_state);

    event.primary_matching_eligible =
        persisted_primary_matching_eligible && event.primary_matching_eligible;

    if let Ok(Some(conv_id)) = row.get::<_, Option<String>>(8) {
        event.set_conversation_id(conv_id);
    }
    if let Ok(Some(message_id)) = row.get::<_, Option<String>>(9) {
        event.set_message_id(message_id);
    }
    if let Ok(Some(source_event_ref)) = row.get::<_, Option<String>>(10) {
        event.set_source_event_ref(source_event_ref);
    }
    if let Ok(authority_source) = row.get::<_, String>(11) {
        event = event.with_authority_source(parse_intent_authority_source(&authority_source));
    }

    Ok(event)
}

fn load_intent_row(row: &Row<'_>) -> rusqlite::Result<IntentEvent> {
    let args_json: String = row.get(4)?;
    let args = match serde_json::from_str(&args_json) {
        Ok(value) => value,
        Err(err) => {
            eprintln!("Invalid tool_args_json for replay intent row: {err}");
            serde_json::Value::Null
        }
    };
    let intent_class = parse_intent_class(&row.get::<_, String>(5)?);
    let parse_state = parse_intent_parse_state(&row.get::<_, String>(6)?);
    build_intent_event(row, args, intent_class, parse_state)
}

fn load_intent_row_strict(row: &Row<'_>) -> rusqlite::Result<IntentEvent> {
    let args_json: String = row.get(4)?;
    let args = serde_json::from_str(&args_json).map_err(|err| {
        rusqlite::Error::FromSqlConversionFailure(
            4,
            Type::Text,
            format!("invalid tool_args_json for replay intent row: {err}").into(),
        )
    })?;
    let intent_class = parse_intent_class_strict(&row.get::<_, String>(5)?)?;
    let parse_state = parse_intent_parse_state_strict(&row.get::<_, String>(6)?)?;
    build_intent_event(row, args, intent_class, parse_state)
}

fn build_action_event(row: &Row<'_>, args: serde_json::Value) -> rusqlite::Result<ActionEvent> {
    let timestamp_ns = decode_u64_field(row, 2, "timestamp_ns")?;
    let pid = decode_u32_field(row, 3, "pid")?;
    let process_start_time_ns = decode_u64_field(row, 4, "process_start_time_ns")?;

    let mut action = ActionEvent::new(
        row.get(0)?,
        ProcessId::new(pid, process_start_time_ns),
        None,
        timestamp_ns,
        row.get(5)?,
        args,
        row.get(7)?,
    );
    if let Ok(Some(agent_id)) = row.get::<_, Option<String>>(1) {
        action.assign_agent(agent_id);
    }
    Ok(action)
}

fn load_action_row(row: &Row<'_>) -> rusqlite::Result<ActionEvent> {
    let args_json: String = row.get(6)?;
    let args = match serde_json::from_str(&args_json) {
        Ok(value) => value,
        Err(err) => {
            eprintln!("Invalid args_json for replay action row: {err}");
            serde_json::Value::Null
        }
    };

    build_action_event(row, args)
}

fn load_action_row_strict(row: &Row<'_>) -> rusqlite::Result<ActionEvent> {
    let args_json: String = row.get(6)?;
    let args = serde_json::from_str(&args_json).map_err(|err| {
        rusqlite::Error::FromSqlConversionFailure(
            6,
            Type::Text,
            format!("invalid args_json for replay action row: {err}").into(),
        )
    })?;

    build_action_event(row, args)
}

fn load_link_row(row: &Row<'_>) -> rusqlite::Result<CorrelationEvidence> {
    let schema_version = decode_u32_field(row, 0, "schema_version")?;
    let timestamp_ns = decode_u64_field(row, 5, "timestamp_ns")?;
    let match_level = parse_match_level(&row.get::<_, String>(7)?)?;

    let matched_fields_json = row.get::<_, Option<String>>(8)?;
    let matched_fields = matched_fields_json
        .map(|value| {
            serde_json::from_str(&value).map_err(|err| {
                rusqlite::Error::FromSqlConversionFailure(
                    8,
                    Type::Text,
                    format!("invalid matched_fields_json for replay link row: {err}").into(),
                )
            })
        })
        .transpose()?;

    let supporting_raw_event_refs_json = row.get::<_, Option<String>>(9)?;
    let supporting_raw_event_refs = supporting_raw_event_refs_json
        .map(|value| {
            serde_json::from_str(&value).map_err(|err| {
                rusqlite::Error::FromSqlConversionFailure(
                    9,
                    Type::Text,
                    format!("invalid supporting_raw_event_refs_json for replay link row: {err}")
                        .into(),
                )
            })
        })
        .transpose()?;

    Ok(CorrelationEvidence {
        schema_version,
        evidence_id: row.get(1)?,
        agent_id: row.get(2)?,
        intent_id: row.get(3)?,
        candidate_id: row.get(4)?,
        timestamp_ns,
        confidence: row.get(6)?,
        match_level,
        matched_fields,
        supporting_raw_event_refs,
        conversation_id: row.get(10)?,
        authority_source: row.get(11)?,
        message_id: row.get(12)?,
        source_event_ref: row.get(13)?,
        debug_notes: row.get(14)?,
        derivation_mode: row.get(15)?,
    })
}

fn load_candidate_row(row: &Row<'_>) -> rusqlite::Result<ActionCandidate> {
    let normalized_args_json: String = row.get(7)?;
    let normalized_args = serde_json::from_str(&normalized_args_json).map_err(|err| {
        rusqlite::Error::FromSqlConversionFailure(
            7,
            Type::Text,
            format!("invalid normalized_args_json for replay candidate row: {err}").into(),
        )
    })?;

    let raw_event_refs_json: String = row.get(8)?;
    let raw_event_refs = serde_json::from_str(&raw_event_refs_json).map_err(|err| {
        rusqlite::Error::FromSqlConversionFailure(
            8,
            Type::Text,
            format!("invalid raw_event_refs_json for replay candidate row: {err}").into(),
        )
    })?;

    let schema_version = decode_u32_field(row, 1, "schema_version")?;
    let timestamp_ns = decode_u64_field(row, 3, "timestamp_ns")?;
    let pid = decode_u32_field(row, 5, "pid")?;
    let process_start_time_ns = decode_u64_field(row, 6, "process_start_time_ns")?;

    let mut candidate = ActionCandidate::new(
        row.get(0)?,
        timestamp_ns,
        parse_candidate_type(&row.get::<_, String>(4)?)?,
        ProcessId::new(pid, process_start_time_ns),
        normalized_args,
        raw_event_refs,
        parse_derivation_mode(&row.get::<_, String>(9)?)?,
        parse_lossiness(&row.get::<_, String>(10)?)?,
        row.get(2)?,
        decode_optional_u64_field(row, 11, "aggregate_window_ns")?,
        row.get(12)?,
        parse_optional_signal_class(&row.get::<_, Option<String>>(13)?)?,
        row.get(14)?,
    )
    .map_err(|err| rusqlite::Error::FromSqlConversionFailure(0, Type::Text, err.into()))?;
    candidate.schema_version = schema_version;
    Ok(candidate)
}

fn parse_evidence_directness(value: &str) -> rusqlite::Result<EvidenceDirectness> {
    match value {
        "direct" => Ok(EvidenceDirectness::Direct),
        "inferred" => Ok(EvidenceDirectness::Inferred),
        _ => Err(rusqlite::Error::FromSqlConversionFailure(
            2,
            Type::Text,
            format!("invalid directness for replay evidence row: {value}").into(),
        )),
    }
}

fn load_evidence_record_row(row: &Row<'_>) -> rusqlite::Result<EvidenceRecord> {
    let resource_id = row
        .get::<_, Option<String>>(4)?
        .map(|value| {
            serde_json::from_str::<ResourceId>(&value).map_err(|err| {
                rusqlite::Error::FromSqlConversionFailure(
                    4,
                    Type::Text,
                    format!("invalid resource_id for replay evidence row: {err}").into(),
                )
            })
        })
        .transpose()?;

    Ok(EvidenceRecord {
        evidence_id: EvidenceId(row.get(0)?),
        source_event_id: EventId(row.get(1)?),
        directness: parse_evidence_directness(&row.get::<_, String>(2)?)?,
        rule_or_inference_id: row.get(3)?,
        resource_id,
        boundary_id: row.get::<_, Option<String>>(5)?.map(BoundaryId),
        confidence: row.get(6)?,
        redaction_status: row.get(7)?,
        payload_ref: row.get(8)?,
    })
}

fn load_non_link_diagnostic_row(row: &Row<'_>) -> rusqlite::Result<NonLinkDiagnostic> {
    let schema_version = decode_u32_field(row, 0, "schema_version")?;
    let timestamp_ns = decode_u64_field(row, 2, "timestamp_ns")?;
    let reason = parse_non_link_reason(&row.get::<_, String>(6)?)?;
    let intent_class: Option<String> = row.get(8)?;
    let parse_state: Option<String> = row.get(9)?;
    let details = row
        .get::<_, Option<String>>(12)?
        .map(|value| {
            serde_json::from_str(&value).map_err(|err| {
                rusqlite::Error::FromSqlConversionFailure(
                    12,
                    Type::Text,
                    format!("invalid details_json for replay non_link_diagnostic row: {err}")
                        .into(),
                )
            })
        })
        .transpose()?;

    Ok(NonLinkDiagnostic {
        schema_version,
        diagnostic_id: row.get(1)?,
        timestamp_ns,
        agent_id: row.get(3)?,
        intent_id: row.get(4)?,
        candidate_id: row.get(5)?,
        reason,
        stage: row.get(7)?,
        intent_class,
        parse_state,
        match_level_considered: row.get(10)?,
        confidence_observed: row.get(11)?,
        details,
    })
}

fn decode_u64_field(row: &Row<'_>, index: usize, field_name: &str) -> rusqlite::Result<u64> {
    let value = row.get::<_, i64>(index)?;
    u64::try_from(value).map_err(|_| conversion_error(index, field_name, value))
}

fn decode_optional_u64_field(
    row: &Row<'_>,
    index: usize,
    field_name: &str,
) -> rusqlite::Result<Option<u64>> {
    let value = row.get::<_, Option<i64>>(index)?;
    value
        .map(|v| u64::try_from(v).map_err(|_| conversion_error(index, field_name, v)))
        .transpose()
}

fn decode_u32_field(row: &Row<'_>, index: usize, field_name: &str) -> rusqlite::Result<u32> {
    let value = row.get::<_, i64>(index)?;
    u32::try_from(value).map_err(|_| conversion_error(index, field_name, value))
}

fn conversion_error(index: usize, field_name: &str, value: i64) -> rusqlite::Error {
    rusqlite::Error::FromSqlConversionFailure(
        index,
        Type::Integer,
        format!("invalid {field_name} value for replay row: {value}").into(),
    )
}

fn parse_intent_class(value: &str) -> IntentClass {
    match value {
        "orchestration" => IntentClass::Orchestration,
        "side_effect" => IntentClass::SideEffect,
        _ => IntentClass::Unknown,
    }
}

fn parse_intent_class_strict(value: &str) -> rusqlite::Result<IntentClass> {
    match value {
        "orchestration" => Ok(IntentClass::Orchestration),
        "side_effect" => Ok(IntentClass::SideEffect),
        "unknown" => Ok(IntentClass::Unknown),
        _ => Err(rusqlite::Error::FromSqlConversionFailure(
            5,
            Type::Text,
            format!("invalid intent_class for replay row: {value}").into(),
        )),
    }
}

fn parse_intent_parse_state(value: &str) -> IntentParseState {
    match value {
        "complete" => IntentParseState::Complete,
        "partial" => IntentParseState::Partial,
        "malformed" => IntentParseState::Malformed,
        _ => IntentParseState::Unsupported,
    }
}

fn parse_intent_parse_state_strict(value: &str) -> rusqlite::Result<IntentParseState> {
    match value {
        "complete" => Ok(IntentParseState::Complete),
        "partial" => Ok(IntentParseState::Partial),
        "malformed" => Ok(IntentParseState::Malformed),
        "unsupported" => Ok(IntentParseState::Unsupported),
        _ => Err(rusqlite::Error::FromSqlConversionFailure(
            6,
            Type::Text,
            format!("invalid parse_state for replay row: {value}").into(),
        )),
    }
}

fn parse_candidate_type(value: &str) -> rusqlite::Result<ActionCandidateType> {
    match value {
        "process_exec" => Ok(ActionCandidateType::ProcessExec),
        "file_read" => Ok(ActionCandidateType::FileRead),
        "file_write" => Ok(ActionCandidateType::FileWrite),
        "file_overwrite" => Ok(ActionCandidateType::FileOverwrite),
        "network_connect" => Ok(ActionCandidateType::NetworkConnect),
        "process_spawn" => Ok(ActionCandidateType::ProcessSpawn),
        _ => Err(rusqlite::Error::FromSqlConversionFailure(
            4,
            Type::Text,
            format!("invalid candidate_type for replay row: {value}").into(),
        )),
    }
}

fn parse_derivation_mode(value: &str) -> rusqlite::Result<CandidateDerivationMode> {
    match value {
        "direct" => Ok(CandidateDerivationMode::Direct),
        "aggregated" => Ok(CandidateDerivationMode::Aggregated),
        "multi_emit" => Ok(CandidateDerivationMode::MultiEmit),
        _ => Err(rusqlite::Error::FromSqlConversionFailure(
            9,
            Type::Text,
            format!("invalid derivation_mode for replay row: {value}").into(),
        )),
    }
}

fn parse_match_level(value: &str) -> rusqlite::Result<MatchLevel> {
    match value {
        "l1_exact" => Ok(MatchLevel::L1Exact),
        "l2_basename" => Ok(MatchLevel::L2Basename),
        "l3_host" => Ok(MatchLevel::L3Host),
        "l4_argv0" => Ok(MatchLevel::L4Argv0),
        "l5_substring" => Ok(MatchLevel::L5Substring),
        _ => Err(rusqlite::Error::FromSqlConversionFailure(
            7,
            Type::Text,
            format!("invalid match_level for replay row: {value}").into(),
        )),
    }
}

fn parse_lossiness(value: &str) -> rusqlite::Result<Lossiness> {
    match value {
        "lossless" => Ok(Lossiness::Lossless),
        "lossy" => Ok(Lossiness::Lossy),
        _ => Err(rusqlite::Error::FromSqlConversionFailure(
            10,
            Type::Text,
            format!("invalid lossiness for replay row: {value}").into(),
        )),
    }
}

fn parse_non_link_reason(value: &str) -> rusqlite::Result<NonLinkReason> {
    match value {
        "intent_not_primary_eligible" => Ok(NonLinkReason::IntentNotPrimaryEligible),
        "no_argument_match" => Ok(NonLinkReason::NoArgumentMatch),
        "candidate_not_available" => Ok(NonLinkReason::CandidateNotAvailable),
        "ambiguous_competition" => Ok(NonLinkReason::AmbiguousCompetition),
        "below_confidence_cutoff" => Ok(NonLinkReason::BelowConfidenceCutoff),
        "outside_time_window" => Ok(NonLinkReason::OutsideTimeWindow),
        "observer_pending" => Ok(NonLinkReason::ObserverPending),
        _ => Err(rusqlite::Error::FromSqlConversionFailure(
            6,
            Type::Text,
            format!("invalid non_link_reason for replay row: {value}").into(),
        )),
    }
}

fn parse_optional_signal_class(value: &Option<String>) -> rusqlite::Result<Option<SignalClass>> {
    match value.as_deref() {
        Some("high_signal") => Ok(Some(SignalClass::HighSignal)),
        Some("low_signal") => Ok(Some(SignalClass::LowSignal)),
        Some("internal_housekeeping") => Ok(Some(SignalClass::InternalHousekeeping)),
        Some(other) => Err(rusqlite::Error::FromSqlConversionFailure(
            13,
            Type::Text,
            format!("invalid signal_class for replay row: {other}").into(),
        )),
        None => Ok(None),
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ArtifactComparisonReport {
    persisted_count: usize,
    replayed_count: usize,
    matched_count: usize,
    missing_from_replay: Vec<String>,
    extra_in_replay: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct NoiseSummary {
    linked_intents_with_no_argument_match_count: usize,
    persisted_non_link_diagnostics_count: usize,
    replayed_non_link_diagnostics_count: usize,
    reasons: std::collections::BTreeMap<String, usize>,
}

fn format_artifact_report(label: &str, report: &ArtifactComparisonReport) -> String {
    let mut lines = vec![format!(
        "{label}_comparison persisted={} replayed={} matched={} missing={} extra={}",
        report.persisted_count,
        report.replayed_count,
        report.matched_count,
        report.missing_from_replay.len(),
        report.extra_in_replay.len()
    )];

    for (idx, item) in report.missing_from_replay.iter().take(5).enumerate() {
        lines.push(format!("{label}_missing_from_replay[{idx}]={item}"));
    }
    for (idx, item) in report.extra_in_replay.iter().take(5).enumerate() {
        lines.push(format!("{label}_extra_in_replay[{idx}]={item}"));
    }

    lines.join("\n")
}

fn compare_links(
    persisted: &[CorrelationEvidence],
    replayed: &[CorrelationEvidence],
) -> ArtifactComparisonReport {
    let persisted_keys: std::collections::BTreeSet<_> =
        persisted.iter().map(link_comparison_key).collect();
    let replayed_keys: std::collections::BTreeSet<_> =
        replayed.iter().map(link_comparison_key).collect();
    let matched_count = persisted_keys.intersection(&replayed_keys).count();
    let missing_from_replay = persisted_keys.difference(&replayed_keys).cloned().collect();
    let extra_in_replay = replayed_keys.difference(&persisted_keys).cloned().collect();

    ArtifactComparisonReport {
        persisted_count: persisted.len(),
        replayed_count: replayed.len(),
        matched_count,
        missing_from_replay,
        extra_in_replay,
    }
}

fn compare_non_link_diagnostics(
    persisted: &[NonLinkDiagnostic],
    replayed: &[NonLinkDiagnostic],
) -> ArtifactComparisonReport {
    let persisted_keys: std::collections::BTreeSet<_> = persisted
        .iter()
        .map(non_link_diagnostic_comparison_key)
        .collect();
    let replayed_keys: std::collections::BTreeSet<_> = replayed
        .iter()
        .map(non_link_diagnostic_comparison_key)
        .collect();
    let matched_count = persisted_keys.intersection(&replayed_keys).count();
    let missing_from_replay = persisted_keys.difference(&replayed_keys).cloned().collect();
    let extra_in_replay = replayed_keys.difference(&persisted_keys).cloned().collect();

    ArtifactComparisonReport {
        persisted_count: persisted.len(),
        replayed_count: replayed.len(),
        matched_count,
        missing_from_replay,
        extra_in_replay,
    }
}

fn semantic_non_link_diagnostics_report(
    persisted_links: &[CorrelationEvidence],
    persisted: &[NonLinkDiagnostic],
    replayed: &[NonLinkDiagnostic],
) -> ArtifactComparisonReport {
    let linked_intent_ids: std::collections::BTreeSet<&str> = persisted_links
        .iter()
        .map(|link| link.intent_id.as_str())
        .collect();

    let is_linked_no_argument_match_noise = |diagnostic: &NonLinkDiagnostic| {
        diagnostic.reason == NonLinkReason::NoArgumentMatch
            && diagnostic.stage == "argument_match"
            && diagnostic
                .intent_id
                .as_deref()
                .is_some_and(|intent_id| linked_intent_ids.contains(intent_id))
    };

    let filtered_persisted: Vec<_> = persisted
        .iter()
        .filter(|diagnostic| !is_linked_no_argument_match_noise(diagnostic))
        .filter(|diagnostic| diagnostic.reason != NonLinkReason::AmbiguousCompetition)
        .cloned()
        .collect();
    let filtered_replayed: Vec<_> = replayed
        .iter()
        .filter(|diagnostic| !is_linked_no_argument_match_noise(diagnostic))
        .filter(|diagnostic| diagnostic.reason != NonLinkReason::AmbiguousCompetition)
        .cloned()
        .collect();

    compare_non_link_diagnostics(&filtered_persisted, &filtered_replayed)
}

fn sqlite_evidence_comparison_report(
    persisted: &[EvidenceRecord],
    replayed_links: &[CorrelationEvidence],
    replay_inputs: &ReplayInputs,
) -> ArtifactComparisonReport {
    let candidate_lookup = replay_inputs
        .inputs
        .iter()
        .map(|input| match input {
            ReplayInput::Candidate(candidate) => (candidate.candidate_id.clone(), candidate),
        })
        .collect::<std::collections::HashMap<_, _>>();
    let replayed = build_replayed_evidence_records(replayed_links, &candidate_lookup);
    compare_evidence_records(persisted, &replayed)
}

fn violation_pattern_comparison_key(record: &ViolationPatternMatchRecord) -> String {
    format!(
        "template_id={}|agent_id={}|matched_policies={}|matched_boundaries={}|matched_targets={}|severity={}",
        record.template_id,
        record.agent_id,
        serde_json::to_string(&record.matched_policies).unwrap_or_default(),
        serde_json::to_string(&record.matched_boundaries).unwrap_or_default(),
        serde_json::to_string(&record.matched_targets).unwrap_or_default(),
        record.severity,
    )
}

fn compare_violation_pattern_matches(
    persisted: &[ViolationPatternMatchRecord],
    replayed: &[ViolationPatternMatchRecord],
) -> ArtifactComparisonReport {
    let persisted_keys: std::collections::BTreeSet<_> = persisted
        .iter()
        .map(violation_pattern_comparison_key)
        .collect();
    let replayed_keys: std::collections::BTreeSet<_> = replayed
        .iter()
        .map(violation_pattern_comparison_key)
        .collect();
    let matched_count = persisted_keys.intersection(&replayed_keys).count();
    let missing_from_replay = persisted_keys.difference(&replayed_keys).cloned().collect();
    let extra_in_replay = replayed_keys.difference(&persisted_keys).cloned().collect();

    ArtifactComparisonReport {
        persisted_count: persisted.len(),
        replayed_count: replayed.len(),
        matched_count,
        missing_from_replay,
        extra_in_replay,
    }
}

fn build_replayed_violation_pattern_records(
    replayed_links: &[CorrelationEvidence],
) -> Vec<ViolationPatternMatchRecord> {
    let mut matcher = PatternMatcher::new(default_templates());
    let mut records = Vec::new();
    for link in replayed_links {
        for matched in matcher.on_link(link.clone()) {
            records.push(ViolationPatternMatchRecord {
                match_id: format!(
                    "{}:{}:{}",
                    matched.template_id, matched.agent_id, matched.end_ns
                ),
                template_id: matched.template_id,
                agent_id: matched.agent_id,
                matched_link_ids: matched.matched_evidence_ids,
                matched_policies: matched.matched_policies,
                matched_boundaries: matched.matched_boundaries,
                matched_targets: matched.matched_targets,
                start_ns: matched.start_ns,
                end_ns: matched.end_ns,
                severity: matched.severity,
                description: matched.description,
            });
        }
    }
    records
}

fn load_accountability_snapshots(conn: &Connection) -> Vec<SnapshotData> {
    let has_table = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='causal_graph_snapshots'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count == 1)
        .unwrap_or(false);
    if !has_table {
        return Vec::new();
    }

    let mut stmt = match conn.prepare(
        "SELECT snapshot_id, timestamp_ns, agent_id, link_ids_json, evidence_ids_json, policy_ids_json,
                boundary_ids_json, claim_states_json, traceback_ids_json, violation_match_ids_json,
                node_count, edge_count, snapshot_kind
         FROM causal_graph_snapshots ORDER BY timestamp_ns, snapshot_id",
    ) {
        Ok(stmt) => stmt,
        Err(_) => return Vec::new(),
    };

    stmt.query_map([], |row| {
        Ok(SnapshotData {
            snapshot_id: row.get(0)?,
            timestamp_ns: row.get::<_, i64>(1)? as u64,
            agent_id: row.get(2)?,
            link_ids: serde_json::from_str(&row.get::<_, String>(3)?).unwrap_or_default(),
            evidence_ids: serde_json::from_str(&row.get::<_, String>(4)?).unwrap_or_default(),
            policy_ids: serde_json::from_str(&row.get::<_, String>(5)?).unwrap_or_default(),
            boundary_ids: serde_json::from_str(&row.get::<_, String>(6)?).unwrap_or_default(),
            claim_states: serde_json::from_str(&row.get::<_, String>(7)?).unwrap_or_default(),
            traceback_ids: serde_json::from_str(&row.get::<_, String>(8)?).unwrap_or_default(),
            violation_match_ids: serde_json::from_str(&row.get::<_, String>(9)?)
                .unwrap_or_default(),
            node_count: row.get::<_, i64>(10)? as usize,
            edge_count: row.get::<_, i64>(11)? as usize,
            snapshot_kind: row.get(12)?,
        })
    })
    .unwrap()
    .filter_map(Result::ok)
    .collect()
}

fn load_violation_pattern_matches(conn: &Connection) -> Vec<ViolationPatternMatchRecord> {
    let has_table = conn
        .query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='violation_pattern_matches'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .map(|count| count == 1)
        .unwrap_or(false);
    if !has_table {
        return Vec::new();
    }

    let mut stmt = conn
        .prepare(
            "SELECT match_id, template_id, agent_id, matched_evidence_ids_json, matched_policies_json, matched_boundaries_json, matched_targets_json, start_ns, end_ns, severity, description FROM violation_pattern_matches ORDER BY end_ns, match_id",
        )
        .unwrap();

    stmt.query_map([], |row| {
        Ok(ViolationPatternMatchRecord {
            match_id: row.get(0)?,
            template_id: row.get(1)?,
            agent_id: row.get(2)?,
            matched_link_ids: serde_json::from_str(&row.get::<_, String>(3)?).unwrap_or_default(),
            matched_policies: serde_json::from_str(&row.get::<_, String>(4)?).unwrap_or_default(),
            matched_boundaries: serde_json::from_str(&row.get::<_, String>(5)?).unwrap_or_default(),
            matched_targets: serde_json::from_str(&row.get::<_, String>(6)?).unwrap_or_default(),
            start_ns: row.get::<_, i64>(7)? as u64,
            end_ns: row.get::<_, i64>(8)? as u64,
            severity: row.get(9)?,
            description: row.get(10)?,
        })
    })
    .unwrap()
    .filter_map(Result::ok)
    .collect()
}

fn link_comparison_key(link: &CorrelationEvidence) -> String {
    format!(
        "{}->{}@{}",
        link.intent_id, link.candidate_id, link.match_level
    )
}

fn non_link_diagnostic_comparison_key(diagnostic: &NonLinkDiagnostic) -> String {
    format!(
        "intent={}|candidate={}|reason={}|stage={}",
        diagnostic.intent_id.as_deref().unwrap_or("-"),
        diagnostic.candidate_id.as_deref().unwrap_or("-"),
        diagnostic.reason,
        diagnostic.stage
    )
}

fn evidence_record_comparison_key(record: &EvidenceRecord) -> String {
    let boundary = record
        .boundary_id
        .as_ref()
        .map(|value| value.0.as_str())
        .unwrap_or("-");
    let resource =
        serde_json::to_string(&record.resource_id).unwrap_or_else(|_| "null".to_string());
    format!(
        "source_event_id={}|directness={}|rule_or_inference_id={}|boundary_id={}|resource_id={}",
        record.source_event_id.0,
        record.directness,
        record.rule_or_inference_id,
        boundary,
        resource,
    )
}

fn compare_evidence_records(
    persisted: &[EvidenceRecord],
    replayed: &[EvidenceRecord],
) -> ArtifactComparisonReport {
    let persisted_keys: std::collections::BTreeSet<_> = persisted
        .iter()
        .map(evidence_record_comparison_key)
        .collect();
    let replayed_keys: std::collections::BTreeSet<_> = replayed
        .iter()
        .map(evidence_record_comparison_key)
        .collect();
    let matched_count = persisted_keys.intersection(&replayed_keys).count();
    let missing_from_replay = persisted_keys.difference(&replayed_keys).cloned().collect();
    let extra_in_replay = replayed_keys.difference(&persisted_keys).cloned().collect();

    ArtifactComparisonReport {
        persisted_count: persisted_keys.len(),
        replayed_count: replayed_keys.len(),
        matched_count,
        missing_from_replay,
        extra_in_replay,
    }
}

fn accountability_snapshot_comparison_key(snapshot: &SnapshotData) -> String {
    // Compare by semantic content, not non-deterministic IDs.
    // link_ids, evidence_ids, traceback_ids, and violation_match_ids are
    // UUIDs regenerated during replay — they will never match live IDs.
    // Policy, boundary, claim state, and count signature form the fingerprint.
    let link_count = snapshot.evidence_ids.len();
    let evidence_count = snapshot.evidence_ids.len();
    let traceback_count = snapshot.traceback_ids.len();
    format!(
        "agent_id={}|policies={}|boundaries={}|claims={}|kind={}|link_count={}|evidence_count={}|traceback_count={}",
        snapshot.agent_id,
        serde_json::to_string(&snapshot.policy_ids).unwrap_or_default(),
        serde_json::to_string(&snapshot.boundary_ids).unwrap_or_default(),
        serde_json::to_string(&snapshot.claim_states).unwrap_or_default(),
        snapshot.snapshot_kind,
        link_count,
        evidence_count,
        traceback_count,
    )
}

fn compare_accountability_snapshots(
    persisted: &[SnapshotData],
    replayed: &[SnapshotData],
) -> ArtifactComparisonReport {
    let mut persisted_counts: std::collections::HashMap<String, usize> =
        std::collections::HashMap::new();
    let mut replayed_counts: std::collections::HashMap<String, usize> =
        std::collections::HashMap::new();

    for snap in persisted {
        *persisted_counts
            .entry(accountability_snapshot_comparison_key(snap))
            .or_insert(0) += 1;
    }
    for snap in replayed {
        *replayed_counts
            .entry(accountability_snapshot_comparison_key(snap))
            .or_insert(0) += 1;
    }

    let persisted_total: usize = persisted_counts.values().sum();
    let replayed_total: usize = replayed_counts.values().sum();

    let mut matched_count = 0usize;
    let mut missing_from_replay = Vec::new();
    let mut extra_in_replay = Vec::new();

    for (key, &p_count) in &persisted_counts {
        let r_count = replayed_counts.get(key).copied().unwrap_or(0);
        matched_count += p_count.min(r_count);
        if p_count > r_count {
            let shortfall = p_count - r_count;
            missing_from_replay.push(format!("{key}|shortfall={shortfall}"));
        }
    }
    for (key, &r_count) in &replayed_counts {
        let p_count = persisted_counts.get(key).copied().unwrap_or(0);
        if r_count > p_count {
            let excess = r_count - p_count;
            extra_in_replay.push(format!("{key}|excess={excess}"));
        }
    }

    ArtifactComparisonReport {
        persisted_count: persisted_total,
        replayed_count: replayed_total,
        matched_count,
        missing_from_replay,
        extra_in_replay,
    }
}

fn has_parity_gap(report: &ArtifactComparisonReport) -> bool {
    report.persisted_count != report.replayed_count
        || !report.missing_from_replay.is_empty()
        || !report.extra_in_replay.is_empty()
}

fn summarize_noise(
    persisted_links: &[CorrelationEvidence],
    persisted_non_link_diagnostics: &[NonLinkDiagnostic],
    replayed_non_link_diagnostics: &[NonLinkDiagnostic],
) -> NoiseSummary {
    let linked_intent_ids: std::collections::BTreeSet<&str> = persisted_links
        .iter()
        .map(|link| link.intent_id.as_str())
        .collect();

    let mut linked_intents_with_no_argument_match = std::collections::BTreeSet::new();
    let mut reasons = std::collections::BTreeMap::new();

    for diagnostic in persisted_non_link_diagnostics {
        *reasons.entry(diagnostic.reason.to_string()).or_insert(0) += 1;
        if diagnostic.reason == NonLinkReason::NoArgumentMatch
            && let Some(intent_id) = diagnostic.intent_id.as_deref()
            && linked_intent_ids.contains(intent_id)
        {
            linked_intents_with_no_argument_match.insert(intent_id.to_string());
        }
    }

    NoiseSummary {
        linked_intents_with_no_argument_match_count: linked_intents_with_no_argument_match.len(),
        persisted_non_link_diagnostics_count: persisted_non_link_diagnostics.len(),
        replayed_non_link_diagnostics_count: replayed_non_link_diagnostics.len(),
        reasons,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use mtagentlens::responsibility::{
        evidence::{MatchLevel, NonLinkDiagnostic, NonLinkReason, SignalClass},
        persistence::{SqliteWriter, open_db},
        types::support_types::{IntentClass, IntentParseState},
    };
    use rusqlite::params;
    use std::sync::{Arc, Mutex};

    fn sample_link(
        link_id: &str,
        intent_id: &str,
        candidate_id: &str,
        match_level: MatchLevel,
    ) -> CorrelationEvidence {
        CorrelationEvidence {
            schema_version: 1,
            evidence_id: link_id.to_string(),
            agent_id: "agent-1".to_string(),
            intent_id: intent_id.to_string(),
            candidate_id: candidate_id.to_string(),
            timestamp_ns: 1000,
            confidence: 0.9,
            match_level,
            matched_fields: None,
            supporting_raw_event_refs: None,
            conversation_id: None,
            authority_source: None,
            message_id: None,
            source_event_ref: None,
            debug_notes: None,
            derivation_mode: None,
        }
    }

    fn sample_diagnostic(
        diagnostic_id: &str,
        intent_id: Option<&str>,
        candidate_id: Option<&str>,
        reason: NonLinkReason,
        stage: &str,
    ) -> NonLinkDiagnostic {
        NonLinkDiagnostic {
            schema_version: 1,
            diagnostic_id: diagnostic_id.to_string(),
            timestamp_ns: 1000,
            agent_id: Some("agent-1".to_string()),
            intent_id: intent_id.map(|v| v.to_string()),
            candidate_id: candidate_id.map(|v| v.to_string()),
            reason,
            stage: stage.to_string(),
            intent_class: None,
            parse_state: None,
            match_level_considered: None,
            confidence_observed: None,
            details: None,
        }
    }

    #[test]
    fn test_load_intents_uses_persisted_ws_b_fields() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE intent_events (
                intent_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                tool_args_json TEXT NOT NULL,
                intent_class TEXT NOT NULL,
                parse_state TEXT NOT NULL,
                primary_matching_eligible INTEGER NOT NULL,
                conversation_id TEXT
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                "intent-1",
                "agent-1",
                1000i64,
                "WebSearch",
                "{\"query\":\"mtagentlens\"}",
                "unknown",
                "malformed",
                1i64,
                "msg_123"
            ],
        )
        .unwrap();

        let intents = load_intents(&conn);
        assert_eq!(intents.len(), 1);
        assert_eq!(intents[0].tool_name, "WebSearch");
        assert_eq!(intents[0].intent_class, IntentClass::Unknown);
        assert_eq!(intents[0].parse_state, IntentParseState::Malformed);
        assert!(!intents[0].primary_matching_eligible);
        assert_eq!(intents[0].conversation_id.as_deref(), Some("msg_123"));
    }

    #[test]
    fn test_load_intents_hydrates_provenance_when_columns_exist() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE intent_events (
                intent_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                tool_args_json TEXT NOT NULL,
                intent_class TEXT NOT NULL,
                parse_state TEXT NOT NULL,
                primary_matching_eligible INTEGER NOT NULL,
                conversation_id TEXT,
                message_id TEXT,
                source_event_ref TEXT,
                authority_source TEXT NOT NULL
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id, message_id, source_event_ref, authority_source)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                "intent-1",
                "agent-1",
                1000i64,
                "Read",
                "{\"file_path\":\"/tmp/x\"}",
                "side_effect",
                "complete",
                1i64,
                "conv-1",
                "msg-1",
                "content-block-1",
                "request_history_assistant_tool_use"
            ],
        )
        .unwrap();

        let intents = load_intents(&conn);
        assert_eq!(intents.len(), 1);
        assert_eq!(intents[0].conversation_id.as_deref(), Some("conv-1"));
        assert_eq!(intents[0].message_id.as_deref(), Some("msg-1"));
        assert_eq!(
            intents[0].source_event_ref.as_deref(),
            Some("content-block-1")
        );
        assert_eq!(
            intents[0].authority_source.to_string(),
            "request_history_assistant_tool_use"
        );

        let strict_intents = load_intents_strict(&conn).unwrap();
        assert_eq!(strict_intents.len(), 1);
        assert_eq!(strict_intents[0].message_id.as_deref(), Some("msg-1"));
        assert_eq!(
            strict_intents[0].source_event_ref.as_deref(),
            Some("content-block-1")
        );
        assert_eq!(
            strict_intents[0].authority_source.to_string(),
            "request_history_assistant_tool_use"
        );
    }

    #[tokio::test]
    async fn test_replay_run_dir_preserves_live_candidate_before_upgraded_request_history_intent_ordering()
     {
        let tempdir = tempfile::tempdir().unwrap();
        let run_dir = tempdir.path();
        let db_path = run_dir.join("trace.log.causal.db");
        let db_path_str = db_path.to_string_lossy().into_owned();
        let conn = open_db(&db_path_str).unwrap();
        conn.execute(
            "INSERT INTO agent_instances (agent_id, first_seen_ns, last_seen_ns, root_processes_json, tls_sessions_json, conversation_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params!["agent-1", 1000i64, 4000i64, "[]", "[]", Option::<String>::None],
        ).unwrap();
        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id, message_id, source_event_ref, authority_source)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                "intent-read", "agent-1", 1000i64, "Read", "{\"file_path\":\"/tmp/endpoints.txt\"}",
                "side_effect", "complete", 1i64, Option::<String>::None, "toolu_call_read", Option::<String>::None,
                "request_history_assistant_tool_use"
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id, message_id, source_event_ref, authority_source)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                "intent-curl", "agent-1", 3000i64, "Bash", "{\"command\":\"curl -v --max-time 5 -o /dev/null http://203.0.113.10:9000/beacon\"}",
                "side_effect", "complete", 1i64, Option::<String>::None, "toolu_call_curl", Option::<String>::None,
                "request_history_assistant_tool_use"
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                "candidate-read", 1i64, "agent-1", 1100i64, "file_read", 123i64, 456i64,
                "{\"path\":\"/tmp/endpoints.txt\"}", "[\"action-read\"]", "direct", "lossless",
                Option::<i64>::None, Option::<String>::None, "high_signal", Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                "candidate-exec", 1i64, "agent-1", 2000i64, "process_exec", 123i64, 456i64,
                "{\"path\":\"/usr/bin/curl\",\"argv\":[\"curl\"]}", "[\"action-exec\"]", "direct", "lossy",
                Option::<i64>::None, Option::<String>::None, "high_signal", Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                "candidate-net", 1i64, "agent-1", 2100i64, "network_connect", 123i64, 456i64,
                "{\"host\":\"203.0.113.10\",\"port\":9000}", "[\"action-net\"]", "direct", "lossy",
                Option::<i64>::None, Option::<String>::None, "high_signal", Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence
             (schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence, match_level, matched_fields_json, supporting_raw_event_refs_json, conversation_id, authority_source, message_id, source_event_ref, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                1i64, "link-read", "agent-1", "intent-read", "candidate-read", 1100i64, 0.9f64,
                "l1_exact", "{\"candidate_type\":\"file_read\",\"target\":\"/tmp/endpoints.txt\",\"policy_id\":\"policy.file.read\",\"boundary_id\":\"boundary.file_read\"}",
                "[\"action-read\"]", Option::<String>::None, "request_history_assistant_tool_use", "toolu_call_read", Option::<String>::None, Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence
             (schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence, match_level, matched_fields_json, supporting_raw_event_refs_json, conversation_id, authority_source, message_id, source_event_ref, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                1i64, "link-exec", "agent-1", "intent-curl", "candidate-exec", 2000i64, 0.9f64,
                "l4_argv0", "{\"candidate_type\":\"process_exec\",\"target\":\"/usr/bin/curl\",\"policy_id\":\"policy.process.exec\",\"boundary_id\":\"boundary.process_exec\"}",
                "[\"action-exec\"]", Option::<String>::None, "http_response_tool_use", "toolu_call_curl", Option::<String>::None, Option::<String>::None
            ],
        ).unwrap();
        drop(conn);

        std::fs::write(run_dir.join("trace.log"), "{}\n").unwrap();
        std::fs::write(run_dir.join("trace.log.alerts.db"), b"").unwrap();

        let _report = replay_run_dir(run_dir.to_str().unwrap(), &[], false, false)
            .await
            .unwrap();

        let replayed_conn = open_db_read_only(
            &run_dir
                .join("trace.log.replayed.causal.db")
                .to_string_lossy(),
        )
        .unwrap();
        let templates: Vec<String> = replayed_conn
            .prepare("SELECT template_id FROM violation_pattern_matches ORDER BY template_id")
            .unwrap()
            .query_map([], |row| row.get(0))
            .unwrap()
            .collect::<rusqlite::Result<Vec<_>>>()
            .unwrap();
        assert!(
            templates
                .iter()
                .any(|t| t == "shared_artifact_read_then_exec"),
            "replay should preserve the live-effective ordering where process_exec matched before the later upgraded request-history timestamp"
        );
        assert!(
            !templates
                .iter()
                .any(|t| t == "prompt_injection_then_network"),
            "replay should not flip the same intent to network-first solely because SQLite retained the upgraded later timestamp"
        );
    }

    #[tokio::test]
    async fn test_replay_run_dir_materializes_replayed_causal_links_with_intent_provenance() {
        let tempdir = tempfile::tempdir().unwrap();
        let run_dir = tempdir.path();
        let db_path = run_dir.join("trace.log.causal.db");
        let db_path_str = db_path.to_string_lossy().into_owned();
        let conn = open_db(&db_path_str).unwrap();
        conn.execute(
            "INSERT INTO agent_instances (agent_id, first_seen_ns, last_seen_ns, root_processes_json, tls_sessions_json, conversation_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params!["agent-1", 1000i64, 1200i64, "[]", "[]", Option::<String>::None],
        ).unwrap();
        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id, message_id, source_event_ref, authority_source)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                "intent-1", "agent-1", 1000i64, "Read", "{\"file_path\":\"/tmp/demo.txt\"}",
                "side_effect", "complete", 1i64, "conv-1", "msg-1", "content-block-1",
                "request_history_assistant_tool_use"
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                "candidate-1", 1i64, "agent-1", 1100i64, "file_read", 123i64, 456i64,
                "{\"path\":\"/tmp/demo.txt\"}", "[\"action-1\"]", "direct", "lossless",
                Option::<i64>::None, Option::<String>::None, "high_signal", Option::<String>::None
            ],
        ).unwrap();
        drop(conn);

        std::fs::write(run_dir.join("trace.log"), "{}\n").unwrap();
        let _report = replay_run_dir(run_dir.to_str().unwrap(), &[], false, false)
            .await
            .unwrap();

        let replayed_conn = open_db_read_only(
            &run_dir
                .join("trace.log.replayed.causal.db")
                .to_string_lossy(),
        )
        .unwrap();
        let row = replayed_conn.query_row(
            "SELECT authority_source, message_id, source_event_ref FROM correlation_evidence WHERE intent_id = ?1",
            ["intent-1"],
            |row| {
                Ok((
                    row.get::<_, Option<String>>(0)?,
                    row.get::<_, Option<String>>(1)?,
                    row.get::<_, Option<String>>(2)?,
                ))
            },
        ).unwrap();
        assert_eq!(row.0.as_deref(), Some("request_history_assistant_tool_use"));
        assert_eq!(row.1.as_deref(), Some("msg-1"));
        assert_eq!(row.2.as_deref(), Some("content-block-1"));
    }

    #[test]
    fn test_load_intents_skips_negative_timestamp() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE intent_events (
                intent_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                tool_args_json TEXT NOT NULL,
                intent_class TEXT NOT NULL,
                parse_state TEXT NOT NULL,
                primary_matching_eligible INTEGER NOT NULL,
                conversation_id TEXT
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                "intent-neg-ts",
                "agent-1",
                -1i64,
                "Read",
                "{\"file_path\":\"/tmp/x\"}",
                "side_effect",
                "complete",
                1i64,
                Option::<String>::None
            ],
        )
        .unwrap();

        let intents = load_intents(&conn);
        assert!(intents.is_empty());
    }

    #[test]
    fn test_load_actions_skips_negative_numeric_fields() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE action_events (
                action_id TEXT PRIMARY KEY,
                agent_id TEXT,
                timestamp_ns INTEGER NOT NULL,
                pid INTEGER NOT NULL,
                process_start_time_ns INTEGER NOT NULL,
                syscall TEXT NOT NULL,
                args_json TEXT NOT NULL,
                comm TEXT NOT NULL
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO action_events
             (action_id, agent_id, timestamp_ns, pid, process_start_time_ns, syscall, args_json, comm)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                "action-1",
                "agent-1",
                -1i64,
                123i64,
                456i64,
                "openat",
                "{}",
                "bash"
            ],
        )
        .unwrap();

        let actions = load_actions(&conn);
        assert!(actions.is_empty());
    }

    #[test]
    fn test_load_actions_preserves_row_with_invalid_json_as_null_args() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE action_events (
                action_id TEXT PRIMARY KEY,
                agent_id TEXT,
                timestamp_ns INTEGER NOT NULL,
                pid INTEGER NOT NULL,
                process_start_time_ns INTEGER NOT NULL,
                syscall TEXT NOT NULL,
                args_json TEXT NOT NULL,
                comm TEXT NOT NULL
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO action_events
             (action_id, agent_id, timestamp_ns, pid, process_start_time_ns, syscall, args_json, comm)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                "action-2",
                "agent-1",
                1000i64,
                123i64,
                456i64,
                "openat",
                "{not-json}",
                "bash"
            ],
        )
        .unwrap();

        let actions = load_actions(&conn);
        assert_eq!(actions.len(), 1);
        assert_eq!(actions[0].args, serde_json::Value::Null);
    }

    #[test]
    fn test_load_replay_inputs_prefers_persisted_candidates() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE action_events (
                action_id TEXT PRIMARY KEY,
                agent_id TEXT,
                timestamp_ns INTEGER NOT NULL,
                pid INTEGER NOT NULL,
                process_start_time_ns INTEGER NOT NULL,
                syscall TEXT NOT NULL,
                args_json TEXT NOT NULL,
                comm TEXT NOT NULL
            );
            CREATE TABLE action_candidates (
                candidate_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                agent_id TEXT,
                timestamp_ns INTEGER NOT NULL,
                candidate_type TEXT NOT NULL,
                pid INTEGER NOT NULL,
                process_start_time_ns INTEGER NOT NULL,
                normalized_args_json TEXT NOT NULL,
                raw_event_refs_json TEXT NOT NULL,
                derivation_mode TEXT NOT NULL,
                lossiness TEXT NOT NULL,
                aggregate_window_ns INTEGER,
                aggregate_rule_name TEXT,
                signal_class TEXT,
                debug_notes TEXT
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO action_events
             (action_id, agent_id, timestamp_ns, pid, process_start_time_ns, syscall, args_json, comm)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                "action-1",
                "agent-1",
                1000i64,
                123i64,
                456i64,
                "openat",
                "{\"path\":\"/tmp/from-action\",\"flags\":\"O_RDONLY\"}",
                "bash"
            ],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                "candidate-1",
                1i64,
                "agent-1",
                999i64,
                "file_read",
                123i64,
                456i64,
                "{\"path\":\"/tmp/from-candidate\",\"flags\":\"O_RDONLY\"}",
                "[\"action-1\"]",
                "direct",
                "lossless",
                Option::<i64>::None,
                Option::<String>::None,
                "high_signal",
                Option::<String>::None
            ],
        )
        .unwrap();

        let replay_inputs = load_replay_inputs(&conn);
        assert_eq!(replay_inputs.inputs.len(), 1);
        match &replay_inputs.inputs[0] {
            ReplayInput::Candidate(candidate) => {
                assert_eq!(candidate.candidate_id, "candidate-1");
                assert_eq!(candidate.normalized_args["path"], "/tmp/from-candidate");
            }
        }
    }

    #[test]
    fn test_load_replay_inputs_falls_back_to_action_derivation() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE action_events (
                action_id TEXT PRIMARY KEY,
                agent_id TEXT,
                timestamp_ns INTEGER NOT NULL,
                pid INTEGER NOT NULL,
                process_start_time_ns INTEGER NOT NULL,
                syscall TEXT NOT NULL,
                args_json TEXT NOT NULL,
                comm TEXT NOT NULL
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO action_events
             (action_id, agent_id, timestamp_ns, pid, process_start_time_ns, syscall, args_json, comm)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                "action-1",
                "agent-1",
                1000i64,
                123i64,
                456i64,
                "openat",
                "{\"path\":\"/tmp/from-action\",\"flags\":\"O_RDONLY\"}",
                "bash"
            ],
        )
        .unwrap();

        let replay_inputs = load_replay_inputs(&conn);
        assert_eq!(replay_inputs.inputs.len(), 1);
        match &replay_inputs.inputs[0] {
            ReplayInput::Candidate(candidate) => {
                assert_eq!(candidate.normalized_args["path"], "/tmp/from-action");
                assert_eq!(candidate.raw_event_refs, vec!["action-1"]);
            }
        }
    }

    #[test]
    fn test_load_replay_inputs_marks_legacy_rederived_candidates_on_action_fallback() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE action_events (
                action_id TEXT PRIMARY KEY,
                agent_id TEXT,
                timestamp_ns INTEGER NOT NULL,
                pid INTEGER NOT NULL,
                process_start_time_ns INTEGER NOT NULL,
                syscall TEXT NOT NULL,
                args_json TEXT NOT NULL,
                comm TEXT NOT NULL
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO action_events
             (action_id, agent_id, timestamp_ns, pid, process_start_time_ns, syscall, args_json, comm)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                "action-legacy",
                "agent-1",
                1000i64,
                123i64,
                456i64,
                "openat",
                "{\"path\":\"/tmp/from-action\",\"flags\":\"O_RDONLY\"}",
                "bash"
            ],
        )
        .unwrap();

        let replay_inputs = load_replay_inputs(&conn);
        assert!(replay_inputs.legacy_rederived_candidates);
    }

    #[test]
    fn test_load_candidates_skips_unknown_persisted_candidate_type() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE action_candidates (
                candidate_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                agent_id TEXT,
                timestamp_ns INTEGER NOT NULL,
                candidate_type TEXT NOT NULL,
                pid INTEGER NOT NULL,
                process_start_time_ns INTEGER NOT NULL,
                normalized_args_json TEXT NOT NULL,
                raw_event_refs_json TEXT NOT NULL,
                derivation_mode TEXT NOT NULL,
                lossiness TEXT NOT NULL,
                aggregate_window_ns INTEGER,
                aggregate_rule_name TEXT,
                signal_class TEXT,
                debug_notes TEXT
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                "candidate-bad",
                1i64,
                "agent-1",
                999i64,
                "unknown_candidate_type",
                123i64,
                456i64,
                "{\"path\":\"/tmp/from-candidate\",\"flags\":\"O_RDONLY\"}",
                "[\"action-1\"]",
                "direct",
                "lossless",
                Option::<i64>::None,
                Option::<String>::None,
                "high_signal",
                Option::<String>::None
            ],
        ).unwrap();

        let candidates = load_candidates(&conn);
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_load_candidates_skips_malformed_persisted_candidate_json() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE action_candidates (
                candidate_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                agent_id TEXT,
                timestamp_ns INTEGER NOT NULL,
                candidate_type TEXT NOT NULL,
                pid INTEGER NOT NULL,
                process_start_time_ns INTEGER NOT NULL,
                normalized_args_json TEXT NOT NULL,
                raw_event_refs_json TEXT NOT NULL,
                derivation_mode TEXT NOT NULL,
                lossiness TEXT NOT NULL,
                aggregate_window_ns INTEGER,
                aggregate_rule_name TEXT,
                signal_class TEXT,
                debug_notes TEXT
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                "candidate-bad-json",
                1i64,
                "agent-1",
                999i64,
                "file_read",
                123i64,
                456i64,
                "{not-json}",
                "[\"action-1\"]",
                "direct",
                "lossless",
                Option::<i64>::None,
                Option::<String>::None,
                "high_signal",
                Option::<String>::None
            ],
        ).unwrap();

        let candidates = load_candidates(&conn);
        assert!(candidates.is_empty());
    }

    #[test]
    fn test_load_links_round_trips_appendix_fields() {
        let conn = Connection::open_in_memory().unwrap();
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
                debug_notes TEXT,
                derivation_mode TEXT
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO correlation_evidence
             (schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence, match_level,
              matched_fields_json, supporting_raw_event_refs_json, conversation_id, authority_source, message_id,
              source_event_ref, debug_notes, derivation_mode)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)",
            params![
                1i64,
                "link-1",
                "agent-1",
                "intent-1",
                "candidate-1",
                1000i64,
                0.91f64,
                "l2_basename",
                "{\"intent_basename\":\"shadow\",\"candidate_basename\":\"shadow\"}",
                "[\"action-1\",\"action-2\"]",
                "conv-1",
                "request_history_assistant_tool_use",
                "msg-1",
                "content-block-1",
                "matched via basename",
                None::<String>,
            ],
        ).unwrap();

        let links = load_links(&conn);
        assert_eq!(links.len(), 1);
        let link = &links[0];
        assert_eq!(link.schema_version, 1);
        assert_eq!(link.match_level, MatchLevel::L2Basename);
        assert_eq!(link.conversation_id.as_deref(), Some("conv-1"));
        assert_eq!(
            link.authority_source.as_deref(),
            Some("request_history_assistant_tool_use")
        );
        assert_eq!(link.message_id.as_deref(), Some("msg-1"));
        assert_eq!(link.source_event_ref.as_deref(), Some("content-block-1"));
        assert_eq!(link.debug_notes.as_deref(), Some("matched via basename"));
        assert_eq!(
            link.supporting_raw_event_refs.as_deref(),
            Some(&["action-1".to_string(), "action-2".to_string()][..])
        );
        assert_eq!(
            link.matched_fields
                .as_ref()
                .and_then(|value| value.get("intent_basename"))
                .and_then(|value| value.as_str()),
            Some("shadow")
        );
    }

    #[test]
    fn test_load_links_strict_round_trips_provenance_columns() {
        let conn = Connection::open_in_memory().unwrap();
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
                debug_notes TEXT,
                derivation_mode TEXT
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO correlation_evidence
             (schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence, match_level,
              matched_fields_json, supporting_raw_event_refs_json, conversation_id, authority_source, message_id,
              source_event_ref, debug_notes, derivation_mode)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16)",
            params![
                1i64,
                "link-strict-1",
                "agent-1",
                "intent-1",
                "candidate-1",
                1000i64,
                0.91f64,
                "l1_exact",
                "{\"path\":\"/tmp/demo.txt\"}",
                "[\"action-1\"]",
                "conv-1",
                "request_history_assistant_tool_use",
                "msg-strict-1",
                "content-block-strict-1",
                "strict row",
                None::<String>,
            ],
        ).unwrap();

        let links = load_links_strict(&conn).unwrap();
        assert_eq!(links.len(), 1);
        let link = &links[0];
        assert_eq!(
            link.authority_source.as_deref(),
            Some("request_history_assistant_tool_use")
        );
        assert_eq!(link.message_id.as_deref(), Some("msg-strict-1"));
        assert_eq!(
            link.source_event_ref.as_deref(),
            Some("content-block-strict-1")
        );
    }

    #[test]
    fn test_load_evidence_records_round_trips_accountability_fields() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE responsibility_evidence_records (
                evidence_id TEXT PRIMARY KEY,
                evidence_type TEXT NOT NULL,
                directness TEXT NOT NULL,
                observation_refs_json TEXT NOT NULL,
                subject_hint TEXT,
                resource_ref_json TEXT,
                rule_id TEXT NOT NULL,
                matched_fields_json TEXT NOT NULL,
                confidence REAL NOT NULL,
                diagnostics_json TEXT NOT NULL
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO responsibility_evidence_records
             (evidence_id, evidence_type, directness, observation_refs_json, subject_hint, resource_ref_json, rule_id, matched_fields_json, confidence, diagnostics_json)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                "e-1",
                "action_correlation",
                "direct",
                "[\"evt-1\"]",
                "boundary.read",
                "{\"File\":{\"canonical_path\":\"/tmp/demo.txt\"}}",
                "rule.direct.read",
                "[]",
                0.9f64,
                "[]"
            ],
        ).unwrap();

        let records = load_evidence_records(&conn);
        assert_eq!(records.len(), 1);
        let record = &records[0];
        assert_eq!(record.evidence_id.0, "e-1");
        assert_eq!(record.source_event_id.0, "evt-1");
        assert_eq!(record.directness, EvidenceDirectness::Direct);
        assert_eq!(record.rule_or_inference_id, "rule.direct.read");
        match record.resource_id.as_ref() {
            Some(ResourceId::File { canonical_path }) => {
                assert_eq!(canonical_path, "/tmp/demo.txt")
            }
            _ => panic!("expected file resource id"),
        }
        assert_eq!(
            record.boundary_id.as_ref().map(|value| value.0.as_str()),
            Some("boundary.read")
        );
        assert_eq!(record.payload_ref.as_deref(), Some("link-1"));
    }

    #[test]
    fn test_load_non_link_diagnostics_round_trips_appendix_fields() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE non_link_diagnostics (
                schema_version INTEGER NOT NULL,
                diagnostic_id TEXT PRIMARY KEY,
                timestamp_ns INTEGER NOT NULL,
                agent_id TEXT,
                intent_id TEXT,
                candidate_id TEXT,
                reason TEXT NOT NULL,
                stage TEXT NOT NULL,
                intent_class TEXT,
                parse_state TEXT,
                match_level_considered TEXT,
                confidence_observed REAL,
                details_json TEXT
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO non_link_diagnostics
             (schema_version, diagnostic_id, timestamp_ns, agent_id, intent_id, candidate_id, reason, stage,
              intent_class, parse_state, match_level_considered, confidence_observed, details_json)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
            params![
                1i64,
                "diag-1",
                1000i64,
                "agent-1",
                "intent-1",
                Option::<String>::None,
                "no_argument_match",
                "argument_match",
                "side_effect",
                "complete",
                Option::<String>::None,
                Option::<f64>::None,
                "{\"checked_candidates\":1}"
            ],
        ).unwrap();

        let diagnostics = load_non_link_diagnostics(&conn);
        assert_eq!(diagnostics.len(), 1);
        let diagnostic = &diagnostics[0];
        assert_eq!(diagnostic.schema_version, 1);
        assert_eq!(diagnostic.reason.to_string(), "no_argument_match");
        assert_eq!(diagnostic.stage, "argument_match");
        assert_eq!(diagnostic.intent_id.as_deref(), Some("intent-1"));
        assert_eq!(diagnostic.intent_class.as_deref(), Some("side_effect"));
        assert_eq!(diagnostic.parse_state.as_deref(), Some("complete"));
        assert_eq!(
            diagnostic
                .details
                .as_ref()
                .and_then(|value| value.get("checked_candidates"))
                .and_then(|value| value.as_u64()),
            Some(1)
        );
    }

    #[test]
    fn test_non_link_diagnostic_writer_to_sqlite_to_replay_round_trips_none_and_some_details() {
        let conn = Arc::new(Mutex::new(Connection::open_in_memory().unwrap()));
        conn.lock()
            .unwrap()
            .execute_batch(
                "CREATE TABLE non_link_diagnostics (
                schema_version INTEGER NOT NULL,
                diagnostic_id TEXT PRIMARY KEY,
                timestamp_ns INTEGER NOT NULL,
                agent_id TEXT,
                intent_id TEXT,
                candidate_id TEXT,
                reason TEXT NOT NULL,
                stage TEXT NOT NULL,
                intent_class TEXT,
                parse_state TEXT,
                match_level_considered TEXT,
                confidence_observed REAL,
                details_json TEXT
            );",
            )
            .unwrap();
        let writer = SqliteWriter::new(conn.clone());

        let diagnostic_without_details = NonLinkDiagnostic::new(
            1000,
            Some("agent-1".to_string()),
            Some("intent-1".to_string()),
            Some("candidate-1".to_string()),
            NonLinkReason::NoArgumentMatch,
            "argument_match".to_string(),
        )
        .with_intent_class(IntentClass::SideEffect)
        .with_parse_state(IntentParseState::Complete);

        let diagnostic_with_details = NonLinkDiagnostic::new(
            1001,
            Some("agent-1".to_string()),
            Some("intent-2".to_string()),
            Some("candidate-2".to_string()),
            NonLinkReason::NoArgumentMatch,
            "argument_match".to_string(),
        )
        .with_details(serde_json::json!({"checked_candidates": 2}));

        writer
            .write_non_link_diagnostic(&diagnostic_without_details)
            .unwrap();
        writer
            .write_non_link_diagnostic(&diagnostic_with_details)
            .unwrap();

        let persisted_none: Option<String> = conn
            .lock()
            .unwrap()
            .query_row(
                "SELECT details_json FROM non_link_diagnostics WHERE diagnostic_id = ?1",
                [diagnostic_without_details.diagnostic_id.as_str()],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(persisted_none, None);

        let diagnostics = load_non_link_diagnostics(&conn.lock().unwrap());
        let none_round_trip = diagnostics
            .iter()
            .find(|diagnostic| diagnostic.diagnostic_id == diagnostic_without_details.diagnostic_id)
            .unwrap();
        assert_eq!(none_round_trip.details, None);

        let some_round_trip = diagnostics
            .iter()
            .find(|diagnostic| diagnostic.diagnostic_id == diagnostic_with_details.diagnostic_id)
            .unwrap();
        assert_eq!(
            some_round_trip
                .details
                .as_ref()
                .and_then(|value| value.get("checked_candidates"))
                .and_then(|value| value.as_u64()),
            Some(2)
        );
    }

    #[test]
    fn test_compare_links_reports_missing_and_extra_rows() {
        let persisted = vec![
            sample_link("link-1", "intent-1", "candidate-1", MatchLevel::L1Exact),
            sample_link("link-2", "intent-2", "candidate-2", MatchLevel::L2Basename),
        ];
        let replayed = vec![
            sample_link("new-link-a", "intent-1", "candidate-1", MatchLevel::L1Exact),
            sample_link("new-link-c", "intent-3", "candidate-3", MatchLevel::L4Argv0),
        ];

        let report = compare_links(&persisted, &replayed);
        assert_eq!(report.persisted_count, 2);
        assert_eq!(report.replayed_count, 2);
        assert_eq!(report.matched_count, 1);
        assert_eq!(
            report.missing_from_replay,
            vec!["intent-2->candidate-2@l2_basename".to_string()]
        );
        assert_eq!(
            report.extra_in_replay,
            vec!["intent-3->candidate-3@l4_argv0".to_string()]
        );
    }

    #[test]
    fn test_compare_non_link_diagnostics_reports_missing_and_extra_rows() {
        let persisted = vec![
            sample_diagnostic(
                "diag-1",
                Some("intent-1"),
                Some("candidate-1"),
                NonLinkReason::NoArgumentMatch,
                "argument_match",
            ),
            sample_diagnostic(
                "diag-2",
                Some("intent-2"),
                None,
                NonLinkReason::IntentNotPrimaryEligible,
                "eligibility_gate",
            ),
        ];
        let replayed = vec![
            sample_diagnostic(
                "diag-a",
                Some("intent-1"),
                Some("candidate-1"),
                NonLinkReason::NoArgumentMatch,
                "argument_match",
            ),
            sample_diagnostic(
                "diag-c",
                Some("intent-3"),
                None,
                NonLinkReason::CandidateNotAvailable,
                "candidate_filter",
            ),
        ];

        let report = compare_non_link_diagnostics(&persisted, &replayed);
        assert_eq!(report.persisted_count, 2);
        assert_eq!(report.replayed_count, 2);
        assert_eq!(report.matched_count, 1);
        assert_eq!(report.missing_from_replay, vec!["intent=intent-2|candidate=-|reason=intent_not_primary_eligible|stage=eligibility_gate".to_string()]);
        assert_eq!(
            report.extra_in_replay,
            vec![
                "intent=intent-3|candidate=-|reason=candidate_not_available|stage=candidate_filter"
                    .to_string()
            ]
        );
    }

    #[test]
    fn test_semantic_non_link_diagnostics_report_ignores_linked_no_argument_match_noise() {
        let persisted_links = vec![sample_link(
            "link-1",
            "intent-1",
            "candidate-linked",
            MatchLevel::L1Exact,
        )];
        let persisted = vec![
            sample_diagnostic(
                "diag-1",
                Some("intent-1"),
                Some("candidate-noise"),
                NonLinkReason::NoArgumentMatch,
                "argument_match",
            ),
            sample_diagnostic(
                "diag-2",
                Some("intent-2"),
                None,
                NonLinkReason::CandidateNotAvailable,
                "candidate_filter",
            ),
        ];
        let replayed = vec![sample_diagnostic(
            "diag-a",
            Some("intent-2"),
            None,
            NonLinkReason::CandidateNotAvailable,
            "candidate_filter",
        )];

        let report = semantic_non_link_diagnostics_report(&persisted_links, &persisted, &replayed);
        assert_eq!(report.persisted_count, 1);
        assert_eq!(report.replayed_count, 1);
        assert_eq!(report.matched_count, 1);
        assert!(report.missing_from_replay.is_empty());
        assert!(report.extra_in_replay.is_empty());
    }

    #[test]
    fn test_compare_violation_pattern_matches_ignores_unstable_link_ids() {
        let persisted = vec![ViolationPatternMatchRecord {
            match_id: "persisted-1".to_string(),
            template_id: "prompt_injection_then_network".to_string(),
            agent_id: "agent-1".to_string(),
            matched_link_ids: vec!["live-link-1".to_string(), "live-link-2".to_string()],
            matched_policies: vec![
                "policy.file.read".to_string(),
                "policy.network.connect".to_string(),
            ],
            matched_boundaries: vec![
                "boundary.file_read".to_string(),
                "boundary.network_connect".to_string(),
            ],
            matched_targets: vec![
                "/tmp/endpoints.txt".to_string(),
                "203.0.113.10:9000".to_string(),
            ],
            start_ns: 100,
            end_ns: 200,
            severity: "critical".to_string(),
            description: "desc".to_string(),
        }];
        let replayed = vec![ViolationPatternMatchRecord {
            match_id: "replayed-1".to_string(),
            template_id: "prompt_injection_then_network".to_string(),
            agent_id: "agent-1".to_string(),
            matched_link_ids: vec!["replay-link-a".to_string(), "replay-link-b".to_string()],
            matched_policies: vec![
                "policy.file.read".to_string(),
                "policy.network.connect".to_string(),
            ],
            matched_boundaries: vec![
                "boundary.file_read".to_string(),
                "boundary.network_connect".to_string(),
            ],
            matched_targets: vec![
                "/tmp/endpoints.txt".to_string(),
                "203.0.113.10:9000".to_string(),
            ],
            start_ns: 100,
            end_ns: 200,
            severity: "critical".to_string(),
            description: "desc".to_string(),
        }];

        let report = compare_violation_pattern_matches(&persisted, &replayed);
        assert_eq!(report.persisted_count, 1);
        assert_eq!(report.replayed_count, 1);
        assert_eq!(report.matched_count, 1);
        assert!(report.missing_from_replay.is_empty());
        assert!(report.extra_in_replay.is_empty());
    }

    #[test]
    fn test_format_artifact_report_includes_summary_and_examples() {
        let report = ArtifactComparisonReport {
            persisted_count: 2,
            replayed_count: 2,
            matched_count: 1,
            missing_from_replay: vec!["intent-2->candidate-2@l2_basename".to_string()],
            extra_in_replay: vec!["intent-3->candidate-3@l4_argv0".to_string()],
        };

        let rendered = format_artifact_report("links", &report);
        assert!(
            rendered
                .contains("links_comparison persisted=2 replayed=2 matched=1 missing=1 extra=1")
        );
        assert!(
            rendered.contains("links_missing_from_replay[0]=intent-2->candidate-2@l2_basename")
        );
        assert!(rendered.contains("links_extra_in_replay[0]=intent-3->candidate-3@l4_argv0"));
    }

    #[test]
    fn test_args_accepts_trace_log_replay_mode() {
        let parsed = Args::try_parse_from(["replay", "--trace-log", "/tmp/sample.trace.log"]);

        assert!(parsed.is_ok());
    }

    #[test]
    fn test_args_accepts_run_dir_replay_mode() {
        let parsed = Args::try_parse_from(["replay", "--run-dir", "/tmp/sample-run"]);

        assert!(parsed.is_ok());
    }

    #[test]
    fn test_resolve_causal_db_path_prefers_summary_artifact_metadata() {
        let tempdir = tempfile::tempdir().unwrap();
        let run_dir = tempdir.path();
        let preferred = run_dir.join("custom-output.causal.db");
        let fallback = run_dir.join("trace.log.causal.db");
        std::fs::write(&preferred, b"").unwrap();
        std::fs::write(&fallback, b"").unwrap();
        std::fs::write(
            run_dir.join("summary.json"),
            serde_json::json!({
                "artifacts": {
                    "causal_db": preferred,
                }
            })
            .to_string(),
        )
        .unwrap();

        let resolved = resolve_causal_db_path(run_dir).unwrap();
        assert_eq!(
            resolved,
            fallback.parent().unwrap().join("custom-output.causal.db")
        );
    }

    #[test]
    fn test_load_intents_strict_rejects_invalid_tool_args_json() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE intent_events (
                intent_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                tool_args_json TEXT NOT NULL,
                intent_class TEXT NOT NULL,
                parse_state TEXT NOT NULL,
                primary_matching_eligible INTEGER NOT NULL,
                conversation_id TEXT
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                "intent-bad-json",
                "agent-1",
                1000i64,
                "Read",
                "{not-json}",
                "side_effect",
                "complete",
                1i64,
                Option::<String>::None
            ],
        )
        .unwrap();

        let err = load_intents_strict(&conn).unwrap_err();
        assert!(err.to_string().contains("tool_args_json"));
    }

    #[test]
    fn test_load_candidates_strict_rejects_invalid_normalized_args_json() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE action_candidates (
                candidate_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                agent_id TEXT,
                timestamp_ns INTEGER NOT NULL,
                candidate_type TEXT NOT NULL,
                pid INTEGER NOT NULL,
                process_start_time_ns INTEGER NOT NULL,
                normalized_args_json TEXT NOT NULL,
                raw_event_refs_json TEXT NOT NULL,
                derivation_mode TEXT NOT NULL,
                lossiness TEXT NOT NULL,
                aggregate_window_ns INTEGER,
                aggregate_rule_name TEXT,
                signal_class TEXT,
                debug_notes TEXT
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                "candidate-bad-json",
                1i64,
                "agent-1",
                999i64,
                "file_read",
                123i64,
                456i64,
                "{not-json}",
                "[\"action-1\"]",
                "direct",
                "lossless",
                Option::<i64>::None,
                Option::<String>::None,
                "high_signal",
                Option::<String>::None
            ],
        )
        .unwrap();

        let err = load_candidates_strict(&conn).unwrap_err();
        assert!(err.to_string().contains("normalized_args_json"));
    }

    #[test]
    fn test_load_actions_strict_rejects_invalid_args_json() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE action_events (
                action_id TEXT PRIMARY KEY,
                agent_id TEXT,
                timestamp_ns INTEGER NOT NULL,
                pid INTEGER NOT NULL,
                process_start_time_ns INTEGER NOT NULL,
                syscall TEXT NOT NULL,
                args_json TEXT NOT NULL,
                comm TEXT NOT NULL
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO action_events
             (action_id, agent_id, timestamp_ns, pid, process_start_time_ns, syscall, args_json, comm)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                "action-bad-json",
                "agent-1",
                1000i64,
                123i64,
                456i64,
                "openat",
                "{not-json}",
                "bash"
            ],
        )
        .unwrap();

        let err = load_actions_strict(&conn).unwrap_err();
        assert!(err.to_string().contains("args_json"));
    }

    #[test]
    fn test_load_intents_strict_rejects_invalid_intent_class_enum() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE intent_events (
                intent_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                tool_args_json TEXT NOT NULL,
                intent_class TEXT NOT NULL,
                parse_state TEXT NOT NULL,
                primary_matching_eligible INTEGER NOT NULL,
                conversation_id TEXT
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                "intent-bad-enum",
                "agent-1",
                1000i64,
                "Read",
                "{\"file_path\":\"/tmp/x\"}",
                "nonsense_enum",
                "complete",
                1i64,
                Option::<String>::None
            ],
        )
        .unwrap();

        let err = load_intents_strict(&conn).unwrap_err();
        assert!(err.to_string().contains("intent_class"));
    }

    #[test]
    fn test_load_non_link_diagnostics_strict_rejects_invalid_parse_state_enum() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE non_link_diagnostics (
                schema_version INTEGER NOT NULL,
                diagnostic_id TEXT PRIMARY KEY,
                timestamp_ns INTEGER NOT NULL,
                agent_id TEXT,
                intent_id TEXT,
                candidate_id TEXT,
                reason TEXT NOT NULL,
                stage TEXT NOT NULL,
                intent_class TEXT,
                parse_state TEXT,
                match_level_considered TEXT,
                confidence_observed REAL,
                details_json TEXT
            );",
        )
        .unwrap();

        conn.execute(
            "INSERT INTO non_link_diagnostics
             (schema_version, diagnostic_id, timestamp_ns, agent_id, intent_id, candidate_id, reason, stage, intent_class, parse_state, match_level_considered, confidence_observed, details_json)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
            params![
                1i64,
                "diag-bad-enum",
                1000i64,
                "agent-1",
                "intent-1",
                Option::<String>::None,
                "no_argument_match",
                "argument_match",
                "side_effect",
                "not_a_real_state",
                Option::<String>::None,
                Option::<f64>::None,
                "{}"
            ],
        ).unwrap();

        let err = load_non_link_diagnostics_strict(&conn).unwrap_err();
        assert!(err.to_string().contains("parse_state"));
    }

    #[tokio::test]
    async fn test_replay_run_dir_reports_linked_intents_with_no_argument_match_noise() {
        let tempdir = tempfile::tempdir().unwrap();
        let run_dir = tempdir.path();
        let db_path = run_dir.join("trace.log.causal.db");
        let db_path_str = db_path.to_string_lossy().into_owned();
        let conn = open_db(&db_path_str).unwrap();
        conn.execute(
            "INSERT INTO agent_instances (agent_id, first_seen_ns, last_seen_ns, root_processes_json, tls_sessions_json, conversation_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params!["agent-1", 1000i64, 1200i64, "[]", "[]", Option::<String>::None],
        ).unwrap();
        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id, message_id, source_event_ref, authority_source)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                "intent-1", "agent-1", 1000i64, "Read", "{\"file_path\":\"/tmp/demo.txt\"}",
                "side_effect", "complete", 1i64, Option::<String>::None, Option::<String>::None,
                Option::<String>::None, "http_response_tool_use"
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                "candidate-1", 1i64, "agent-1", 1100i64, "file_read", 123i64, 456i64,
                "{\"path\":\"/tmp/demo.txt\"}", "[\"action-1\"]", "direct", "lossless",
                Option::<i64>::None, Option::<String>::None, "high_signal", Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence
             (schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence, match_level, matched_fields_json, supporting_raw_event_refs_json, conversation_id, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                1i64, "link-1", "agent-1", "intent-1", "candidate-1", 1100i64, 0.9f64,
                "l1_exact", "{\"intent_arg\":\"/tmp/demo.txt\",\"candidate_arg\":\"/tmp/demo.txt\"}",
                "[\"action-1\"]", Option::<String>::None, Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO non_link_diagnostics
             (schema_version, diagnostic_id, timestamp_ns, agent_id, intent_id, candidate_id, reason, stage, intent_class, parse_state, match_level_considered, confidence_observed, details_json)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
            params![
                1i64, "diag-noise", 1200i64, "agent-1", "intent-1", "candidate-9",
                "no_argument_match", "argument_match", "side_effect", "complete",
                Option::<String>::None, Option::<f64>::None, "{}"
            ],
        ).unwrap();
        drop(conn);

        std::fs::write(run_dir.join("trace.log"), "{}\n").unwrap();
        let report = replay_run_dir(run_dir.to_str().unwrap(), &[], false, false)
            .await
            .unwrap();
        let report_json: serde_json::Value =
            serde_json::from_slice(&std::fs::read(run_dir.join("replay.report.json")).unwrap())
                .unwrap();

        assert_eq!(report.status, "ok");
        assert_eq!(
            report_json["noise_summary"]["linked_intents_with_no_argument_match_count"],
            1
        );
        assert_eq!(
            report_json["noise_summary"]["reasons"]["no_argument_match"],
            1
        );
        assert_eq!(
            report_json["noise_summary"]["persisted_non_link_diagnostics_count"],
            1
        );
        assert_eq!(
            report_json["noise_summary"]["replayed_non_link_diagnostics_count"],
            0
        );
        assert_eq!(report_json["evidence_comparison"]["persisted_count"], 0);
        assert_eq!(
            report_json["non_link_diagnostics_comparison"]["persisted_count"], 0,
            "semantic comparison should filter linked no_argument_match noise out of replay parity gating"
        );
        assert_eq!(
            report_json["non_link_diagnostics_comparison"]["replayed_count"],
            0
        );
        assert_eq!(
            report_json["non_link_diagnostics_comparison"]["matched_count"],
            0
        );
        assert_eq!(
            report_json["violation_patterns_comparison"]["persisted_count"],
            0
        );
        assert_eq!(
            report_json["violation_patterns_comparison"]["replayed_count"],
            0
        );
        assert_eq!(
            report_json["violation_patterns_comparison"]["matched_count"],
            0
        );
    }

    #[tokio::test]
    async fn test_replay_run_dir_reports_link_comparison_section_and_parity_gap_when_links_differ()
    {
        let tempdir = tempfile::tempdir().unwrap();
        let run_dir = tempdir.path();
        let db_path = run_dir.join("trace.log.causal.db");
        let db_path_str = db_path.to_string_lossy().into_owned();
        let conn = open_db(&db_path_str).unwrap();
        conn.execute(
            "INSERT INTO agent_instances (agent_id, first_seen_ns, last_seen_ns, root_processes_json, tls_sessions_json, conversation_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                "agent-1",
                1000i64,
                1200i64,
                "[]",
                "[]",
                Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id, message_id, source_event_ref, authority_source)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                "intent-1",
                "agent-1",
                1000i64,
                "Read",
                "{\"file_path\":\"/tmp/demo.txt\"}",
                "side_effect",
                "complete",
                1i64,
                Option::<String>::None,
                Option::<String>::None,
                Option::<String>::None,
                "http_response_tool_use"
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                "candidate-1",
                1i64,
                "agent-1",
                1100i64,
                "file_read",
                123i64,
                456i64,
                "{\"path\":\"/tmp/other.txt\"}",
                "[\"action-1\"]",
                "direct",
                "lossless",
                Option::<i64>::None,
                Option::<String>::None,
                "high_signal",
                Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence
             (schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence, match_level, matched_fields_json, supporting_raw_event_refs_json, conversation_id, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                1i64,
                "link-1",
                "agent-1",
                "intent-1",
                "candidate-1",
                1100i64,
                0.9f64,
                "l1_exact",
                "{\"intent_arg\":\"/tmp/demo.txt\",\"candidate_arg\":\"/tmp/demo.txt\"}",
                "[\"action-1\"]",
                Option::<String>::None,
                Option::<String>::None
            ],
        ).unwrap();
        drop(conn);

        std::fs::write(run_dir.join("trace.log"), "{}\n").unwrap();
        std::fs::write(run_dir.join("trace.log.alerts.db"), b"").unwrap();

        let report = replay_run_dir(run_dir.to_str().unwrap(), &[], false, false)
            .await
            .unwrap();
        let report_json: serde_json::Value =
            serde_json::from_slice(&std::fs::read(run_dir.join("replay.report.json")).unwrap())
                .unwrap();

        assert_eq!(report.status, "parity_gap");
        assert_eq!(report_json["status"], "parity_gap");
        assert!(
            report_json.get("causal_links_comparison").is_some(),
            "replay report should expose causal-link parity section"
        );
        assert_eq!(report_json["causal_links_comparison"]["persisted_count"], 1);
        assert_eq!(report_json["causal_links_comparison"]["replayed_count"], 0);
        assert_eq!(report_json["causal_links_comparison"]["matched_count"], 0);
        let gap_sections = report_json["gap_sections"].as_array().unwrap();
        assert!(
            gap_sections
                .iter()
                .any(|value| value == "causal_links_comparison"),
            "link comparison should be called out as a parity gap section"
        );
    }

    #[tokio::test]
    async fn test_replay_run_dir_preserves_conversation_only_original_agents() {
        let tempdir = tempfile::tempdir().unwrap();
        let run_dir = tempdir.path();
        let db_path = run_dir.join("trace.log.causal.db");
        let db_path_str = db_path.to_string_lossy().into_owned();
        let conn = open_db(&db_path_str).unwrap();
        conn.execute(
            "INSERT INTO agent_instances (agent_id, first_seen_ns, last_seen_ns, root_processes_json, tls_sessions_json, conversation_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                "agent-1",
                1000i64,
                1200i64,
                "[]",
                "[]",
                Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO agent_instances (agent_id, first_seen_ns, last_seen_ns, root_processes_json, tls_sessions_json, conversation_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                "agent-conv-only",
                900i64,
                900i64,
                "[{\"pid\":321,\"start_time_ns\":654}]",
                "[]",
                "conv-only-1"
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id, message_id, source_event_ref, authority_source)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                "intent-1",
                "agent-1",
                1000i64,
                "Read",
                "{\"file_path\":\"/tmp/demo.txt\"}",
                "side_effect",
                "complete",
                1i64,
                Option::<String>::None,
                Option::<String>::None,
                Option::<String>::None,
                "http_response_tool_use"
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)
            ",
            params![
                "candidate-1",
                1i64,
                "agent-1",
                1100i64,
                "file_read",
                123i64,
                456i64,
                "{\"path\":\"/tmp/demo.txt\"}",
                "[\"action-1\"]",
                "direct",
                "lossless",
                Option::<i64>::None,
                Option::<String>::None,
                "high_signal",
                Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence
             (schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence, match_level, matched_fields_json, supporting_raw_event_refs_json, conversation_id, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                1i64,
                "link-1",
                "agent-1",
                "intent-1",
                "candidate-1",
                1100i64,
                0.9f64,
                "l1_exact",
                "{\"intent_arg\":\"/tmp/demo.txt\",\"candidate_arg\":\"/tmp/demo.txt\"}",
                "[\"action-1\"]",
                Option::<String>::None,
                Option::<String>::None
            ],
        ).unwrap();
        drop(conn);

        std::fs::write(run_dir.join("trace.log"), "{}\n").unwrap();
        std::fs::write(run_dir.join("trace.log.alerts.db"), b"").unwrap();

        let _report = replay_run_dir(run_dir.to_str().unwrap(), &[], false, false)
            .await
            .unwrap();

        let replayed_conn = open_db_read_only(
            &run_dir
                .join("trace.log.replayed.causal.db")
                .to_string_lossy(),
        )
        .unwrap();
        let preserved: (String, String, String) = replayed_conn
            .query_row(
                "SELECT agent_id, root_processes_json, conversation_id FROM agent_instances WHERE agent_id = ?1",
                ["agent-conv-only"],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .unwrap();
        assert_eq!(preserved.0, "agent-conv-only");
        assert_eq!(preserved.1, "[{\"pid\":321,\"start_time_ns\":654}]");
        assert_eq!(preserved.2, "conv-only-1");
    }

    #[tokio::test]
    async fn test_replay_run_dir_writes_authoritative_report_for_valid_causal_db() {
        let tempdir = tempfile::tempdir().unwrap();
        let run_dir = tempdir.path();
        let db_path = run_dir.join("trace.log.causal.db");
        let db_path_str = db_path.to_string_lossy().into_owned();
        let conn = open_db(&db_path_str).unwrap();
        conn.execute(
            "INSERT INTO agent_instances (agent_id, first_seen_ns, last_seen_ns, root_processes_json, tls_sessions_json, conversation_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                "agent-1",
                1000i64,
                1200i64,
                "[]",
                "[]",
                Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id, message_id, source_event_ref, authority_source)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                "intent-1",
                "agent-1",
                1000i64,
                "Read",
                "{\"file_path\":\"/tmp/demo.txt\"}",
                "side_effect",
                "complete",
                1i64,
                Option::<String>::None,
                Option::<String>::None,
                Option::<String>::None,
                "http_response_tool_use"
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                "candidate-1",
                1i64,
                "agent-1",
                1100i64,
                "file_read",
                123i64,
                456i64,
                "{\"path\":\"/tmp/demo.txt\"}",
                "[\"action-1\"]",
                "direct",
                "lossless",
                Option::<i64>::None,
                Option::<String>::None,
                "high_signal",
                Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence
             (schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence, match_level, matched_fields_json, supporting_raw_event_refs_json, conversation_id, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                1i64,
                "link-1",
                "agent-1",
                "intent-1",
                "candidate-1",
                1100i64,
                0.9f64,
                "l1_exact",
                "{\"intent_arg\":\"/tmp/demo.txt\",\"candidate_arg\":\"/tmp/demo.txt\"}",
                "[\"action-1\"]",
                Option::<String>::None,
                Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO responsibility_evidence_records
             (evidence_id, evidence_type, directness, observation_refs_json, subject_hint, resource_ref_json, rule_id, matched_fields_json, confidence, diagnostics_json)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                "evidence:link-1",
                "action_correlation",
                "direct",
                "[\"action-1\"]",
                "boundary.file_read",
                "{\"File\":{\"canonical_path\":\"/tmp/demo.txt\"}}",
                "causal_link:l1_exact",
                "[]",
                0.9f64,
                "[]"
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO non_link_diagnostics
             (schema_version, diagnostic_id, timestamp_ns, agent_id, intent_id, candidate_id, reason, stage, intent_class, parse_state, match_level_considered, confidence_observed, details_json)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
            params![
                1i64,
                "diag-1",
                1200i64,
                "agent-1",
                "intent-1",
                "candidate-1",
                "below_confidence_cutoff",
                "confidence_gate",
                "side_effect",
                "complete",
                "l5_substring",
                0.4f64,
                "{\"checked_candidates\":1}"
            ],
        ).unwrap();
        drop(conn);

        std::fs::write(run_dir.join("trace.log"), "{}\n").unwrap();
        std::fs::write(run_dir.join("trace.log.alerts.db"), b"").unwrap();

        let report = replay_run_dir(run_dir.to_str().unwrap(), &[], false, false)
            .await
            .unwrap();
        assert_eq!(report.mode, "authoritative_sqlite");
        assert_eq!(report.status, "ok");
        assert!(!report.legacy_rederived_candidates);

        let report_json: serde_json::Value =
            serde_json::from_slice(&std::fs::read(run_dir.join("replay.report.json")).unwrap())
                .unwrap();
        assert_eq!(report_json["mode"], "authoritative_sqlite");
        assert_eq!(report_json["status"], "ok");
        assert_eq!(report_json["legacy_rederived_candidates"], false);
        assert_eq!(report_json["artifacts"]["causal_db"], db_path_str);
        assert!(run_dir.join("trace.log.replayed.causal.db").exists());
        assert!(run_dir.join("trace.log.replayed.causal.jsonl").exists());
    }

    #[tokio::test]
    async fn test_replay_run_dir_materializes_replayed_authoritative_rows_for_policy_grounded_links()
     {
        let tempdir = tempfile::tempdir().unwrap();
        let run_dir = tempdir.path();
        let db_path = run_dir.join("trace.log.causal.db");
        let db_path_str = db_path.to_string_lossy().into_owned();
        let conn = open_db(&db_path_str).unwrap();
        conn.execute(
            "INSERT INTO agent_instances (agent_id, first_seen_ns, last_seen_ns, root_processes_json, tls_sessions_json, conversation_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params!["agent-1", 1000i64, 1200i64, "[]", "[]", Option::<String>::None],
        ).unwrap();
        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id, message_id, source_event_ref, authority_source)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                "intent-1",
                "agent-1",
                1000i64,
                "Bash",
                "{\"command\":\"curl -v --max-time 5 -o /dev/null http://203.0.113.10:9000/beacon\"}",
                "side_effect",
                "complete",
                1i64,
                Option::<String>::None,
                Option::<String>::None,
                Option::<String>::None,
                "http_response_tool_use"
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                "candidate-1", 1i64, "agent-1", 1100i64, "network_connect", 123i64, 456i64,
                "{\"host\":\"203.0.113.10\",\"port\":9000}", "[\"action-1\"]", "direct", "lossless",
                Option::<i64>::None, Option::<String>::None, "high_signal", Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence
             (schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence, match_level, matched_fields_json, supporting_raw_event_refs_json, conversation_id, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                1i64,
                "link-1",
                "agent-1",
                "intent-1",
                "candidate-1",
                1100i64,
                0.9f64,
                "l3_host",
                "{\"intent_arg\":\"http://203.0.113.10:9000/beacon\",\"candidate_arg\":\"203.0.113.10:9000\"}",
                "[\"action-1\"]",
                Option::<String>::None,
                Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO responsibility_evidence_records
             (evidence_id, evidence_type, directness, observation_refs_json, subject_hint, resource_ref_json, rule_id, matched_fields_json, confidence, diagnostics_json)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                "evidence:link-1",
                "action_correlation",
                "direct",
                "[\"action-1\"]",
                "boundary.network_connect",
                "{\"Network\":{\"host\":\"203.0.113.10\",\"port\":9000,\"scheme\":null}}",
                "causal_link:l3_host",
                "[]",
                0.9f64,
                "[]"
            ],
        ).unwrap();
        drop(conn);

        std::fs::write(run_dir.join("trace.log"), "{}\n").unwrap();
        std::fs::write(run_dir.join("trace.log.alerts.db"), b"").unwrap();

        let _report = replay_run_dir(run_dir.to_str().unwrap(), &[], false, false)
            .await
            .unwrap();

        let replayed_conn = open_db_read_only(
            &run_dir
                .join("trace.log.replayed.causal.db")
                .to_string_lossy(),
        )
        .unwrap();
        let policy_count: i64 = replayed_conn
            .query_row("SELECT COUNT(*) FROM policy_evaluations", [], |row| {
                row.get(0)
            })
            .unwrap();
        let traceback_count: i64 = replayed_conn
            .query_row(
                "SELECT COUNT(*) FROM accountability_tracebacks",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let evidence_count: i64 = replayed_conn
            .query_row("SELECT COUNT(*) FROM evidence_records", [], |row| {
                row.get(0)
            })
            .unwrap();
        let violation_count: i64 = replayed_conn
            .query_row(
                "SELECT COUNT(*) FROM violation_pattern_matches",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let evidence_row: (String, String) = replayed_conn
            .query_row(
                "SELECT boundary_id, payload_ref FROM evidence_records LIMIT 1",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(
            policy_count, 1,
            "replayed causal db should materialize policy evaluations for policy-grounded links"
        );
        assert_eq!(
            traceback_count, 1,
            "replayed causal db should materialize accountability tracebacks for policy-grounded links"
        );
        assert_eq!(
            evidence_count, 1,
            "replayed causal db should materialize evidence records for replayed links"
        );
        assert_eq!(
            violation_count, 0,
            "single network-connect policy-grounded replay sample should not invent unrelated violation pattern rows"
        );
        assert_eq!(evidence_row.0, "boundary.network_connect");
        assert!(
            !evidence_row.1.is_empty(),
            "replayed evidence record should reference a replayed causal link payload"
        );

        let report_json: serde_json::Value =
            serde_json::from_slice(&std::fs::read(run_dir.join("replay.report.json")).unwrap())
                .unwrap();
        assert_eq!(report_json["evidence_comparison"]["persisted_count"], 1);
        assert_eq!(report_json["evidence_comparison"]["replayed_count"], 1);
        assert_eq!(
            report_json["evidence_comparison"]["matched_count"], 1,
            "replay report should compare authoritative evidence semantics rather than replay-generated payload ids"
        );
        assert_eq!(
            report_json["evidence_comparison"]["missing_from_replay"],
            serde_json::json!([])
        );
        assert_eq!(
            report_json["evidence_comparison"]["extra_in_replay"],
            serde_json::json!([])
        );
    }

    #[test]
    fn test_replay_sqlite_evidence_comparison_uses_authoritative_evidence_semantics() {
        let persisted = vec![EvidenceRecord {
            evidence_id: EvidenceId("evidence:link-1".to_string()),
            source_event_id: EventId("action-1".to_string()),
            directness: EvidenceDirectness::Direct,
            rule_or_inference_id: "causal_link:l3_host".to_string(),
            resource_id: Some(ResourceId::Network {
                host: "203.0.113.10".to_string(),
                port: 9000,
                scheme: None,
            }),
            boundary_id: Some(BoundaryId("boundary.network_connect".to_string())),
            confidence: 0.9,
            redaction_status: "none".to_string(),
            payload_ref: Some("link-1".to_string()),
        }];
        let candidate = ActionCandidate::new(
            "candidate-1".to_string(),
            1_100,
            ActionCandidateType::NetworkConnect,
            ProcessId::new(123, 456),
            serde_json::json!({"host":"203.0.113.10","port":9000}),
            vec!["action-1".to_string()],
            CandidateDerivationMode::Direct,
            Lossiness::Lossless,
            Some("agent-1".to_string()),
            None,
            None,
            Some(SignalClass::HighSignal),
            None,
        )
        .unwrap();
        let link = sample_link(
            "replayed-link-1",
            "intent-1",
            "candidate-1",
            MatchLevel::L3Host,
        );
        let replay_inputs = ReplayInputs {
            inputs: vec![ReplayInput::Candidate(candidate)],
            legacy_rederived_candidates: false,
        };
        let report = sqlite_evidence_comparison_report(&persisted, &[link], &replay_inputs);
        assert_eq!(report.persisted_count, 1);
        assert_eq!(report.replayed_count, 1);
        assert_eq!(
            report.matched_count, 1,
            "sqlite replay parity should use the same authoritative evidence semantics as run-dir replay"
        );
        assert!(report.missing_from_replay.is_empty());
        assert!(report.extra_in_replay.is_empty());
    }

    #[test]
    fn test_replay_config_from_disable_signal_maps_supported_sqlite_flags() {
        let config = replay_config_from_disable_signal(&[
            "disable_l2_normalized".to_string(),
            "disable_l3_containment".to_string(),
            "disable_process_exec_actions".to_string(),
            "disable_network_actions".to_string(),
            "disable_file_read_actions".to_string(),
            "disable_file_write_actions".to_string(),
        ]);

        assert!(config.enabled_match_levels.as_ref().is_some_and(|levels| {
            levels.contains(&mtagentlens::responsibility::types::MatchLevel::L1Exact)
                && !levels.contains(&mtagentlens::responsibility::types::MatchLevel::L2Normalized)
                && !levels.contains(&mtagentlens::responsibility::types::MatchLevel::L3Containment)
                && levels.len() == 3
        }));
        assert!(
            config
                .enabled_action_types
                .as_ref()
                .is_some_and(|action_types| {
                    !action_types.contains(
                        &mtagentlens::responsibility::types::ActionCandidateType::ProcessExec,
                    ) && !action_types.contains(
                        &mtagentlens::responsibility::types::ActionCandidateType::ProcessSpawn,
                    ) && !action_types.contains(
                        &mtagentlens::responsibility::types::ActionCandidateType::NetworkConnect,
                    ) && !action_types.contains(
                        &mtagentlens::responsibility::types::ActionCandidateType::NetworkSend,
                    ) && !action_types.contains(
                        &mtagentlens::responsibility::types::ActionCandidateType::FileRead,
                    ) && !action_types.contains(
                        &mtagentlens::responsibility::types::ActionCandidateType::FileWrite,
                    ) && !action_types.contains(
                        &mtagentlens::responsibility::types::ActionCandidateType::FileOverwrite,
                    ) && !action_types.contains(
                        &mtagentlens::responsibility::types::ActionCandidateType::FileDelete,
                    ) && action_types.is_empty()
                })
        );

        let exact_only = replay_config_from_disable_signal(&[
            "exact_match_only".to_string(),
            "disable_l2_normalized".to_string(),
        ]);
        assert!(
            exact_only
                .enabled_match_levels
                .as_ref()
                .is_some_and(|levels| levels
                    .contains(&mtagentlens::responsibility::types::MatchLevel::L1Exact,)
                    && levels.len() == 1)
        );

        let file_actions = replay_config_from_disable_signal(&["disable_file_actions".to_string()]);
        assert!(
            file_actions
                .enabled_action_types
                .as_ref()
                .is_some_and(|action_types| {
                    !action_types.contains(
                        &mtagentlens::responsibility::types::ActionCandidateType::FileRead,
                    ) && !action_types.contains(
                        &mtagentlens::responsibility::types::ActionCandidateType::FileWrite,
                    ) && !action_types.contains(
                        &mtagentlens::responsibility::types::ActionCandidateType::FileOverwrite,
                    ) && !action_types.contains(
                        &mtagentlens::responsibility::types::ActionCandidateType::FileDelete,
                    ) && action_types.contains(
                        &mtagentlens::responsibility::types::ActionCandidateType::NetworkConnect,
                    )
                })
        );
    }

    #[tokio::test]
    async fn test_replay_raw_trace_refuses_malformed_persisted_lines_for_truthful_raw_only_parity()
    {
        let tempdir = tempfile::tempdir().unwrap();
        let trace_path = tempdir.path().join("sample.trace.log");
        std::fs::write(
            &trace_path,
            concat!(
                "{\"timestamp\":1,\"timestamp_ns\":1001,\"source\":\"process\",\"pid\":1,\"comm\":\"claude\",\"data\":{\"event\":\"EXEC\",\"pid\":1,\"ppid\":0,\"start_time_ns\":100,\"parent_start_time_ns\":0,\"filename\":\"claude\",\"full_command\":\"claude\"}}\n",
                "{\"timestamp\":2,\"timestamp_ns\":1002,\"source\":\"ssl\",\"pid\":1,\"comm\":\"HTTP Client\",\"data\":{\"function\":\"READ/RECV\",\"pid\":1,\"tid\":1,\"data\":\"event: message_start\\ndata: {\\\"type\\\":\\\"message_start\\\",\\\"message\\\":{\\\"id\\\":\\\"msg_replay_1\\\"}}\\n\\n\"}}\n",
                "not-json\n"
            ),
        )
        .unwrap();

        let expected_db_path = trace_path.with_file_name("sample.trace.log.replay.causal.db");
        let expected_jsonl_path = trace_path.with_file_name("sample.trace.log.replay.causal.jsonl");
        assert!(!expected_db_path.exists());
        assert!(!expected_jsonl_path.exists());

        let err = replay_raw_trace(trace_path.to_str().unwrap())
            .await
            .expect_err("malformed persisted raw lines should fail closed");

        assert!(err.contains("malformed persisted trace lines"));
        assert!(err.contains("unsuitable for truthful raw-only intent/link parity"));
        assert!(!expected_db_path.exists());
        assert!(!expected_jsonl_path.exists());
    }

    #[tokio::test]
    async fn test_replay_run_dir_reports_snapshot_comparison_section() {
        let tempdir = tempfile::tempdir().unwrap();
        let run_dir = tempdir.path();
        let db_path = run_dir.join("trace.log.causal.db");
        let db_path_str = db_path.to_string_lossy().into_owned();
        let conn = open_db(&db_path_str).unwrap();
        conn.execute(
            "INSERT INTO agent_instances (agent_id, first_seen_ns, last_seen_ns, root_processes_json, tls_sessions_json, conversation_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params!["agent-1", 1000i64, 1200i64, "[]", "[]", Option::<String>::None],
        ).unwrap();
        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id, message_id, source_event_ref, authority_source)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                "intent-1", "agent-1", 1000i64, "Read", "{\"file_path\":\"/tmp/demo.txt\"}",
                "side_effect", "complete", 1i64, Option::<String>::None, Option::<String>::None,
                Option::<String>::None, "http_response_tool_use"
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                "candidate-1", 1i64, "agent-1", 1100i64, "file_read", 123i64, 456i64,
                "{\"path\":\"/tmp/demo.txt\"}", "[\"action-1\"]", "direct", "lossless",
                Option::<i64>::None, Option::<String>::None, "high_signal", Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence
             (schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence, match_level, matched_fields_json, supporting_raw_event_refs_json, conversation_id, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                1i64, "link-1", "agent-1", "intent-1", "candidate-1", 1100i64, 0.9f64,
                "l1_exact", "{\"intent_arg\":\"/tmp/demo.txt\",\"candidate_arg\":\"/tmp/demo.txt\"}",
                "[\"action-1\"]", Option::<String>::None, Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO responsibility_graph_snapshots
             (snapshot_id, timestamp_ns, agent_id, nodes_json, edges_json, diagnostics_json)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                "snapshot-1",
                1100i64,
                "agent-1",
                "[\"evidence:link-1\"]",
                "[\"link-1\"]",
                "[]"
            ],
        )
        .unwrap();
        drop(conn);

        std::fs::write(run_dir.join("trace.log"), "{}\n").unwrap();
        std::fs::write(run_dir.join("trace.log.alerts.db"), b"").unwrap();

        let report = replay_run_dir(run_dir.to_str().unwrap(), &[], false, false)
            .await
            .unwrap();
        let report_json: serde_json::Value =
            serde_json::from_slice(&std::fs::read(run_dir.join("replay.report.json")).unwrap())
                .unwrap();
        assert!(
            report_json.get("snapshot_comparison").is_some(),
            "replay report should expose snapshot parity section"
        );
        assert_eq!(report.status, "parity_gap");
        assert_eq!(report_json["status"], "parity_gap");
        assert_eq!(report_json["snapshot_comparison"]["persisted_count"], 1);
        assert_eq!(report_json["snapshot_comparison"]["replayed_count"], 0);
        let gap_sections = report_json["gap_sections"].as_array().unwrap();
        assert!(
            gap_sections
                .iter()
                .any(|value| value == "snapshot_comparison"),
            "snapshot comparison should be called out as a parity gap section"
        );
    }

    #[tokio::test]
    async fn test_replay_raw_trace_refuses_mixed_post_analyzer_input_for_truthful_raw_only_parity()
    {
        let tempdir = tempfile::tempdir().unwrap();
        let trace_path = tempdir.path().join("sample.trace.log");
        std::fs::write(
            &trace_path,
            concat!(
                "{\"timestamp\":1,\"timestamp_ns\":1001,\"source\":\"process\",\"pid\":1,\"comm\":\"claude\",\"data\":{\"event\":\"EXEC\",\"pid\":1,\"ppid\":0,\"start_time_ns\":100,\"parent_start_time_ns\":0,\"filename\":\"claude\",\"full_command\":\"claude\"}}\n",
                "{\"timestamp\":2,\"timestamp_ns\":1002,\"source\":\"ssl\",\"pid\":1,\"comm\":\"HTTP Client\",\"data\":{\"function\":\"READ/RECV\",\"pid\":1,\"tid\":1,\"data\":\"event: message_start\\ndata: {\\\"type\\\":\\\"message_start\\\",\\\"message\\\":{\\\"id\\\":\\\"msg_replay_1\\\"}}\\n\\n\"}}\n",
                "{\"timestamp\":3,\"timestamp_ns\":1003,\"source\":\"ssl\",\"pid\":1,\"comm\":\"HTTP Client\",\"data\":{\"function\":\"READ/RECV\",\"pid\":1,\"tid\":1,\"data\":\"event: content_block_start\\ndata: {\\\"type\\\":\\\"content_block_start\\\",\\\"index\\\":0,\\\"content_block\\\":{\\\"type\\\":\\\"tool_use\\\",\\\"name\\\":\\\"Read\\\",\\\"input\\\":{}}}\\n\\n\"}}\n",
                "{\"timestamp\":4,\"timestamp_ns\":1004,\"source\":\"ssl\",\"pid\":1,\"comm\":\"HTTP Client\",\"data\":{\"function\":\"READ/RECV\",\"pid\":1,\"tid\":1,\"data\":\"event: content_block_delta\\ndata: {\\\"type\\\":\\\"content_block_delta\\\",\\\"index\\\":0,\\\"delta\\\":{\\\"type\\\":\\\"input_json_delta\\\",\\\"partial_json\\\":\\\"{\\\\\\\"file_path\\\\\\\":\\\\\\\"/tmp/demo.txt\\\\\\\"}\\\"}}\\n\\n\"}}\n",
                "{\"timestamp\":5,\"timestamp_ns\":1005,\"source\":\"ssl\",\"pid\":1,\"comm\":\"HTTP Client\",\"data\":{\"function\":\"READ/RECV\",\"pid\":1,\"tid\":1,\"data\":\"event: message_stop\\ndata: {\\\"type\\\":\\\"message_stop\\\"}\\n\\n\"}}\n",
                "{\"timestamp\":6,\"timestamp_ns\":1006,\"source\":\"sse_processor\",\"pid\":1,\"comm\":\"HTTP Client\",\"data\":{\"message_id\":\"msg_replay_1\"}}\n",
                "not-json\n"
            ),
        )
        .unwrap();

        let expected_db_path = trace_path.with_file_name("sample.trace.log.replay.causal.db");
        let expected_jsonl_path = trace_path.with_file_name("sample.trace.log.replay.causal.jsonl");
        assert!(!expected_db_path.exists());
        assert!(!expected_jsonl_path.exists());

        let err = replay_raw_trace(trace_path.to_str().unwrap())
            .await
            .expect_err("mixed post-analyzer input should fail closed");

        assert!(err.contains("persisted trace contains filtered derived events"));
        assert!(err.contains("sse_processor"));
        assert!(err.contains("truthful raw-only intent/link parity"));
        assert!(!expected_db_path.exists());
        assert!(!expected_jsonl_path.exists());
    }

    #[tokio::test]
    async fn test_replay_run_dir_materializes_violation_pattern_matches_for_shared_artifact_sequence()
     {
        let tempdir = tempfile::tempdir().unwrap();
        let run_dir = tempdir.path();
        let db_path = run_dir.join("trace.log.causal.db");
        let db_path_str = db_path.to_string_lossy().into_owned();
        let conn = open_db(&db_path_str).unwrap();
        conn.execute(
            "INSERT INTO agent_instances (agent_id, first_seen_ns, last_seen_ns, root_processes_json, tls_sessions_json, conversation_id)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params!["agent-1", 1000i64, 1200i64, "[]", "[]", Option::<String>::None],
        ).unwrap();
        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id, message_id, source_event_ref, authority_source)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                "intent-read", "agent-1", 1000i64, "Read", "{\"file_path\":\"/tmp/shared.md\"}",
                "side_effect", "complete", 1i64, Option::<String>::None, Option::<String>::None,
                Option::<String>::None, "http_response_tool_use"
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO intent_events
             (intent_id, agent_id, timestamp_ns, tool_name, tool_args_json, intent_class, parse_state, primary_matching_eligible, conversation_id, message_id, source_event_ref, authority_source)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                "intent-exec", "agent-1", 2000i64, "Bash", "{\"command\":\"/usr/bin/bash /tmp/run.sh\"}",
                "side_effect", "complete", 1i64, Option::<String>::None, Option::<String>::None,
                Option::<String>::None, "http_response_tool_use"
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                "candidate-read", 1i64, "agent-1", 1100i64, "file_read", 123i64, 456i64,
                "{\"path\":\"/tmp/shared.md\",\"flags\":\"O_RDONLY\"}", "[\"action-read\"]", "direct", "lossless",
                Option::<i64>::None, Option::<String>::None, "high_signal", Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO action_candidates
             (candidate_id, schema_version, agent_id, timestamp_ns, candidate_type, pid, process_start_time_ns,
              normalized_args_json, raw_event_refs_json, derivation_mode, lossiness, aggregate_window_ns,
              aggregate_rule_name, signal_class, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                "candidate-exec", 1i64, "agent-1", 2100i64, "process_exec", 123i64, 456i64,
                "{\"path\":\"/usr/bin/bash\",\"argv\":[\"/usr/bin/bash\",\"/tmp/run.sh\"]}", "[\"action-exec\"]", "direct", "lossless",
                Option::<i64>::None, Option::<String>::None, "high_signal", Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence
             (schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence, match_level, matched_fields_json, supporting_raw_event_refs_json, conversation_id, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                1i64, "link-read", "agent-1", "intent-read", "candidate-read", 1100i64, 0.9f64,
                "l1_exact", "{\"candidate_type\":\"legacy_irrelevant\",\"target\":\"/tmp/shared.md\",\"policy_id\":\"policy.file.read\",\"boundary_id\":\"boundary.file_read\"}",
                "[\"action-read\"]", Option::<String>::None, Option::<String>::None
            ],
        ).unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence
             (schema_version, evidence_id, agent_id, intent_id, candidate_id, timestamp_ns, confidence, match_level, matched_fields_json, supporting_raw_event_refs_json, conversation_id, debug_notes)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                1i64, "link-exec", "agent-1", "intent-exec", "candidate-exec", 2100i64, 0.9f64,
                "l1_exact", "{\"candidate_type\":\"legacy_irrelevant_exec\",\"target\":\"/usr/bin/bash\",\"policy_id\":\"policy.process.exec\",\"boundary_id\":\"boundary.process_exec\"}",
                "[\"action-exec\"]", Option::<String>::None, Option::<String>::None
            ],
        ).unwrap();
        drop(conn);

        std::fs::write(run_dir.join("trace.log"), "{}\n").unwrap();
        std::fs::write(run_dir.join("trace.log.alerts.db"), b"").unwrap();

        let _report = replay_run_dir(run_dir.to_str().unwrap(), &[], false, false)
            .await
            .unwrap();

        let replayed_conn = open_db_read_only(
            &run_dir
                .join("trace.log.replayed.causal.db")
                .to_string_lossy(),
        )
        .unwrap();
        let violation_count: i64 = replayed_conn
            .query_row(
                "SELECT COUNT(*) FROM violation_pattern_matches",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let violation_row: (String, String, String) = replayed_conn.query_row(
            "SELECT template_id, matched_policies_json, matched_boundaries_json FROM violation_pattern_matches LIMIT 1",
            [],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        ).unwrap();
        assert_eq!(
            violation_count, 1,
            "replayed causal db should materialize violation pattern matches for shared-artifact sequences"
        );
        assert_eq!(violation_row.0, "shared_artifact_read_then_exec");
        assert!(violation_row.1.contains("policy.file.read"));
        assert!(violation_row.2.contains("boundary.file_read"));
    }
}
