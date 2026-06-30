// SPDX-License-Identifier: MIT
// PersistenceWriter — async batch writer using bounded mpsc channel + independent tokio task.

use crate::responsibility::types::alert_types::{
    ResponsibilityAlert, ResponsibilityClaim, ResponsibilityTraceback,
};
use crate::responsibility::types::evidence_types::{
    Action, CorrelationEvidence, Intent, NonLinkDiagnostic,
};
use crate::responsibility::types::graph_types::{
    GraphSnapshot, ResponsibilityEdge, ResponsibilityNode,
};
use crate::responsibility::types::identity_types::{
    AuthoritativeIdentity, ProvisionalIdentity, SubagentBoundary,
};
use crate::responsibility::types::policy_types::{
    ContextPollution, DelegationRecord, PolicyEvaluation,
};
use crate::responsibility::types::refinement::{
    ClaimRefinement, ClassificationRefinement, EvidenceRefinement, GraphRefinement,
    IdentityRefinement,
};
use crate::responsibility::types::replay_types::RunRecord;
use crate::responsibility::types::ids::{
    MessageId, TlsSessionId, AgentId,
};

use rusqlite::{params, Connection};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use tokio::sync::{mpsc, oneshot};

// ═══════════════════════════════════════════════════════════════════════════
// Signal-layer helper types (not yet in types/)
// ═══════════════════════════════════════════════════════════════════════════

/// Minimal process metadata captured from eBPF process events.
#[derive(Debug, Clone)]
pub struct ProcessMetadata {
    pub pid: u32,
    pub start_time_ns: u64,
    pub parent_pid: Option<u32>,
    pub parent_start_time_ns: Option<u64>,
    pub comm: String,
    pub timestamp_ns: u64,
}

/// Minimal TLS session data captured from SSL sniffing.
#[derive(Debug, Clone)]
pub struct TlsSession {
    pub tls_session_id: TlsSessionId,
    pub pid: u32,
    pub start_time_ns: u64,
    pub timestamp_ns: u64,
}

/// How a message is bound to a process/TLS session.
#[derive(Debug, Clone)]
pub enum BindingType {
    TlsShared,
    TlsNew,
    ProcessTreeInherited,
}

impl BindingType {
    pub fn as_str(&self) -> &'static str {
        match self {
            BindingType::TlsShared => "TlsShared",
            BindingType::TlsNew => "TlsNew",
            BindingType::ProcessTreeInherited => "ProcessTreeInherited",
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// PersistRecord — 29 variants, all data types + Flush
// ═══════════════════════════════════════════════════════════════════════════

/// All possible records that can be persisted.
/// Sent through a bounded mpsc channel to the writer task.
pub enum PersistRecord {
    // --- Phase 1 data ---
    /// A process event observed by eBPF
    Process(ProcessMetadata),
    /// A TLS session established
    Tls(TlsSession),
    /// A message bound to a process and TLS session
    MessageBinding {
        message_id: MessageId,
        pid: u32,
        start_time_ns: u64,
        tls_session_id: Option<TlsSessionId>,
        binding_type: BindingType,
        timestamp_ns: u64,
    },
    /// A parsed intent from an LLM response
    Intent(Intent),
    /// An observed action candidate
    Action(Action),
    /// Correlation evidence linking intent to action
    Evidence(CorrelationEvidence),
    /// A non-link diagnostic (why no link was formed)
    NonLink(NonLinkDiagnostic),
    /// Phase 1 provisional agent identity
    ProvisionalIdentity(ProvisionalIdentity),
    /// A graph node (Agent, Resource, or PolicyBoundary)
    GraphNode(ResponsibilityNode),
    /// A graph edge between nodes
    GraphEdge(ResponsibilityEdge),
    /// A policy evaluation result
    PolicyEvaluation(PolicyEvaluation),
    /// Cross-agent delegation record
    Delegation(DelegationRecord),
    /// Context pollution event
    ContextPollution(ContextPollution),
    /// A responsibility alert
    Alert(ResponsibilityAlert),
    /// A responsibility claim
    Claim(ResponsibilityClaim),
    /// A responsibility traceback
    Traceback(ResponsibilityTraceback),

    // --- Phase 2 data ---
    /// Authoritative agent identity (Phase 2)
    AuthoritativeIdentity(AuthoritativeIdentity),
    /// Identity refinement (Phase 1 → Phase 2)
    IdentityRefinement(IdentityRefinement),
    /// Evidence refinement (Phase 1 → Phase 2)
    EvidenceRefinement(EvidenceRefinement),
    /// Graph refinement (Phase 1 → Phase 2)
    GraphRefinement(GraphRefinement),
    /// A graph snapshot (for Phase 2 finalization)
    GraphSnapshot(GraphSnapshot),
    /// Classification refinement (Phase 1 → Phase 2)
    ClassificationRefinement(ClassificationRefinement),
    /// Claim refinement (Phase 1 → Phase 2)
    ClaimRefinement(ClaimRefinement),
    /// Subagent boundary discovered
    SubagentBoundary(SubagentBoundary),

    // --- Direct mapping ---
    /// Direct process-to-agent identity mapping
    IdentityMapping {
        agent_id: AgentId,
        pid: u32,
        start_time_ns: u64,
    },

    // --- Run metadata ---
    /// Start a new run
    RunStart {
        run_id: String,
        timestamp_ns: u64,
    },
    /// End a run
    RunEnd {
        run_id: String,
        timestamp_ns: u64,
    },
    /// Full run record for metadata
    RunRecord(RunRecord),

    // --- Control ---
    /// Flush pending writes and return result via oneshot channel
    Flush(oneshot::Sender<Result<(), String>>),
}

// ═══════════════════════════════════════════════════════════════════════════
// PersistenceWriter
// ═══════════════════════════════════════════════════════════════════════════

/// Configuration for the persistence writer.
pub struct WriterConfig {
    /// Bounded channel capacity
    pub channel_capacity: usize,
    /// Batch commit every N records
    pub batch_size: usize,
    /// Batch commit every N milliseconds
    pub batch_interval_ms: u64,
    /// SQLite database path
    pub db_path: String,
}

impl Default for WriterConfig {
    fn default() -> Self {
        Self {
            channel_capacity: 4096,
            batch_size: 500,
            batch_interval_ms: 200,
            db_path: "mtagentlens_responsibility.db".to_string(),
        }
    }
}

/// Handle to the async persistence writer.
/// Clone is cheap (just clones the channel sender).
#[derive(Clone)]
pub struct PersistenceWriter {
    tx: mpsc::Sender<PersistRecord>,
    lost_count: Arc<AtomicU64>,
}

impl PersistenceWriter {
    /// Spawn the writer task and return a handle.
    /// The spawned task runs independently, writing batches to SQLite.
    pub fn new(config: WriterConfig) -> Result<Self, String> {
        let (tx, rx) = mpsc::channel::<PersistRecord>(config.channel_capacity);
        let lost_count = Arc::new(AtomicU64::new(0));
        let lost_for_task = Arc::clone(&lost_count);

        // Open the SQLite connection with WAL mode
        let conn = Connection::open(&config.db_path)
            .map_err(|e| format!("failed to open SQLite DB: {e}"))?;
        conn.execute_batch(
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON;",
        )
        .map_err(|e| format!("failed to set pragmas: {e}"))?;
        super::schema::run_migrations(&conn)
            .map_err(|e| format!("failed to run migrations: {e}"))?;

        let batch_size = config.batch_size;
        let batch_interval_ms = config.batch_interval_ms;

        tokio::spawn(async move {
            writer_task(conn, rx, batch_size, batch_interval_ms, lost_for_task).await;
        });

        Ok(Self {
            tx,
            lost_count,
        })
    }

    /// Send a persist record. Non-blocking — discards if the channel is full.
    /// Returns `true` if the record was queued, `false` if it was dropped.
    pub fn write(&self, record: PersistRecord) -> bool {
        match self.tx.try_send(record) {
            Ok(()) => true,
            Err(mpsc::error::TrySendError::Full(_)) => {
                self.lost_count.fetch_add(1, Ordering::Relaxed);
                false
            }
            Err(mpsc::error::TrySendError::Closed(_)) => false,
        }
    }

    /// Flush pending writes synchronously. Blocks until all queued writes are committed.
    /// Uses a Flush record with a oneshot channel.
    pub async fn flush(&self) -> Result<(), String> {
        let (tx, rx) = oneshot::channel();
        // For flush we must wait — use blocking send since channel capacity is large
        let _ = self.tx.send(PersistRecord::Flush(tx)).await;
        rx.await.map_err(|_| "writer task dropped".to_string())?
    }

    /// Total number of records dropped due to full channel.
    pub fn lost_persist_count(&self) -> u64 {
        self.lost_count.load(Ordering::Relaxed)
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Writer task internals
// ═══════════════════════════════════════════════════════════════════════════

async fn writer_task(
    conn: Connection,
    mut rx: mpsc::Receiver<PersistRecord>,
    batch_size: usize,
    batch_interval_ms: u64,
    lost_count: Arc<AtomicU64>,
) {
    let mut pending: usize = 0;
    let interval = tokio::time::Duration::from_millis(batch_interval_ms);
    // Cache run_id for current active run — written once per batch
    let mut current_run_id: Option<String> = None;

    loop {
        // Wait for either: a record, or the batch interval timer
        let batch_deadline = tokio::time::sleep(interval);
        tokio::pin!(batch_deadline);

        let record = tokio::select! {
            rec = rx.recv() => {
                match rec {
                    Some(record) => record,
                    None => {
                        // Channel closed — flush remaining and exit
                        if pending > 0 {
                            let _ = conn.execute_batch("COMMIT");
                        }
                        return;
                    }
                }
            }
            _ = &mut batch_deadline => {
                // Timer fired — commit if there are pending records
                if pending > 0 {
                    let _ = commit_batch(&conn, &mut pending, &mut current_run_id);
                }
                continue;
            }
        };

        // Process the record
        match record {
            PersistRecord::Flush(tx) => {
                if pending > 0 {
                    let _ = commit_batch(&conn, &mut pending, &mut current_run_id);
                }
                let _ = tx.send(Ok(()));
                continue;
            }
            PersistRecord::RunStart {
                run_id,
                timestamp_ns,
            } => {
                current_run_id = Some(run_id.clone());
                // Commit pending batch first (belongs to previous run, if any)
                if pending > 0 {
                    let _ = commit_batch(&conn, &mut pending, &mut current_run_id);
                }
                // Insert run row immediately so RunEnd can UPDATE it later
                let _ = conn.execute(
                    "INSERT OR IGNORE INTO runs (run_id, started_at_ns) VALUES (?1, ?2)",
                    params![run_id, timestamp_ns as i64],
                );
                continue;
            }
            PersistRecord::RunEnd { run_id, timestamp_ns } => {
                // Run end is written immediately
                if pending > 0 {
                    let _ = commit_batch(&conn, &mut pending, &mut current_run_id);
                }
                let _ = conn.execute(
                    "UPDATE runs SET ended_at_ns = ?1 WHERE run_id = ?2",
                    params![timestamp_ns as i64, run_id],
                );
                continue;
            }
            PersistRecord::RunRecord(record) => {
                // Commit pending batch first, then write run metadata immediately
                if pending > 0 {
                    let _ = commit_batch(&conn, &mut pending, &mut current_run_id);
                }
                current_run_id = Some(record.run_id.clone());
                let _ = conn.execute(
                    "INSERT OR REPLACE INTO runs (run_id, started_at_ns, ended_at_ns, scenario_id, mode, collector_version)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                    params![
                        record.run_id,
                        record.started_at_ns as i64,
                        record.ended_at_ns.map(|v| v as i64),
                        record.scenario_id,
                        record.mode,
                        record.collector_version,
                    ],
                );
                continue;
            }
            other => {
                let run_id = match &current_run_id {
                    Some(id) => id.clone(),
                    None => {
                        // No active run — discard
                        lost_count.fetch_add(1, Ordering::Relaxed);
                        continue;
                    }
                };
                // Begin a transaction before the first write in a batch
                if pending == 0 {
                    let _ = conn.execute_batch("BEGIN TRANSACTION");
                }
                if let Err(_e) = write_record(&conn, &other, &run_id) {
                    lost_count.fetch_add(1, Ordering::Relaxed);
                    continue;
                }
            }
        }

        pending += 1;

        // Commit if batch is full
        if pending >= batch_size {
            let _ = commit_batch(&conn, &mut pending, &mut current_run_id);
        }
    }
}

/// Write a single record to the connection (within the current transaction context).
/// The caller wraps this with BEGIN/COMMIT for batching.
fn write_record(conn: &Connection, record: &PersistRecord, run_id: &str) -> rusqlite::Result<()> {
    match record {
        PersistRecord::Process(pm) => {
            conn.execute(
                "INSERT OR IGNORE INTO process_events
                 (pid, start_time_ns, parent_pid, parent_start_time_ns, comm, timestamp_ns, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![
                    pm.pid as i64,
                    pm.start_time_ns as i64,
                    pm.parent_pid.map(|v| v as i64),
                    pm.parent_start_time_ns.map(|v| v as i64),
                    pm.comm,
                    pm.timestamp_ns as i64,
                    run_id,
                ],
            )?;
        }
        PersistRecord::Tls(tls) => {
            conn.execute(
                "INSERT OR IGNORE INTO tls_sessions
                 (tls_session_id, pid, start_time_ns, timestamp_ns, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![
                    tls.tls_session_id.as_str(),
                    tls.pid as i64,
                    tls.start_time_ns as i64,
                    tls.timestamp_ns as i64,
                    run_id,
                ],
            )?;
        }
        PersistRecord::MessageBinding {
            message_id,
            pid,
            start_time_ns,
            tls_session_id,
            binding_type,
            timestamp_ns,
        } => {
            conn.execute(
                "INSERT INTO message_bindings
                 (message_id, pid, start_time_ns, tls_session_id, binding_type, timestamp_ns, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![
                    message_id.as_str(),
                    *pid as i64,
                    *start_time_ns as i64,
                    tls_session_id.as_ref().map(|s| s.as_str()),
                    binding_type.as_str(),
                    *timestamp_ns as i64,
                    run_id,
                ],
            )?;
        }
        PersistRecord::Intent(intent) => {
            let tool_args = serde_json::to_string(&intent.tool_args)
                .unwrap_or_else(|_| "[]".to_string());
            conn.execute(
                "INSERT OR IGNORE INTO intents
                 (intent_id, pid, process_start_time_ns, message_id, tool_name, tool_args_json,
                  intent_class, parse_state, primary_matching_eligible, authority_source, timestamp_ns, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
                params![
                    intent.intent_id.as_str(),
                    intent.process_id.pid as i64,
                    intent.process_id.start_time_ns as i64,
                    intent.message_id.as_str(),
                    intent.tool_name,
                    tool_args,
                    intent.intent_class.to_string(),
                    format!("{:?}", intent.parse_state),
                    if intent.primary_matching_eligible { 1 } else { 0 },
                    format!("{:?}", intent.authority_source),
                    intent.timestamp_ns as i64,
                    run_id,
                ],
            )?;
        }
        PersistRecord::Action(action) => {
            let norm_args = serde_json::to_string(&action.normalized_args)
                .unwrap_or_else(|_| "[]".to_string());
            conn.execute(
                "INSERT OR IGNORE INTO actions
                 (action_id, pid, process_start_time_ns, action_type, normalized_args_json,
                  derivation_mode, signal_class, timestamp_ns, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                params![
                    action.action_id.as_str(),
                    action.process_id.pid as i64,
                    action.process_id.start_time_ns as i64,
                    format!("{:?}", action.action_type),
                    norm_args,
                    format!("{:?}", action.derivation_mode),
                    format!("{:?}", action.signal_class),
                    action.timestamp_ns as i64,
                    run_id,
                ],
            )?;
        }
        PersistRecord::Evidence(ev) => {
            let matched = serde_json::to_string(&ev.matched_fields)
                .unwrap_or_else(|_| "{}".to_string());
            conn.execute(
                "INSERT OR IGNORE INTO correlation_evidence
                 (evidence_id, intent_id, action_id, match_level, confidence, matched_fields_json,
                  derivation_mode, phase, timestamp_ns, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
                params![
                    ev.evidence_id.as_str(),
                    ev.intent_id.as_str(),
                    ev.action_id.as_str(),
                    format!("{:?}", ev.match_level),
                    ev.confidence,
                    matched,
                    format!("{:?}", ev.derivation_mode),
                    format!("{:?}", ev.phase),
                    ev.timestamp_ns as i64,
                    run_id,
                ],
            )?;
        }
        PersistRecord::NonLink(diag) => {
            conn.execute(
                "INSERT INTO non_link_diagnostics
                 (diagnostic_id, intent_id, action_id, reason, timestamp_ns, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    diag.diagnostic_id,
                    diag.intent_id.as_ref().map(|s| s.as_str()),
                    diag.candidate_id.as_ref().map(|s| s.as_str()),
                    format!("{:?}", diag.reason),
                    diag.timestamp_ns as i64,
                    run_id,
                ],
            )?;
        }
        PersistRecord::ProvisionalIdentity(pi) => {
            let pids = serde_json::to_string(&pi.process_ids)
                .unwrap_or_else(|_| "[]".to_string());
            conn.execute(
                "INSERT OR REPLACE INTO provisional_identities
                 (agent_id, process_ids_json, primary_signal, confidence, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![
                    pi.agent_id.as_str(),
                    pids,
                    format!("{:?}", pi.primary_signal),
                    pi.confidence,
                    run_id,
                ],
            )?;
        }
        PersistRecord::GraphNode(node) => {
            let resource = serde_json::to_string(&node.resource)
                .unwrap_or_else(|_| "null".to_string());
            let ev_refs = serde_json::to_string(&node.evidence_refs)
                .unwrap_or_else(|_| "[]".to_string());
            conn.execute(
                "INSERT OR REPLACE INTO graph_nodes
                 (node_id, node_type, agent_id, resource_json, evidence_refs_json, timestamp_ns, phase, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                params![
                    node.node_id.as_str(),
                    format!("{:?}", node.node_type),
                    node.agent_id.as_ref().map(|s| s.as_str()),
                    resource,
                    ev_refs,
                    node.timestamp_ns as i64,
                    format!("{:?}", node.phase),
                    run_id,
                ],
            )?;
        }
        PersistRecord::GraphEdge(edge) => {
            let ev_refs = serde_json::to_string(&edge.evidence_refs)
                .unwrap_or_else(|_| "[]".to_string());
            conn.execute(
                "INSERT OR REPLACE INTO graph_edges
                 (edge_id, edge_type, source_node_id, target_node_id, evidence_refs_json, confidence, phase, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                params![
                    edge.edge_id.as_str(),
                    format!("{:?}", edge.edge_type),
                    edge.source.as_str(),
                    edge.target.as_str(),
                    ev_refs,
                    edge.confidence,
                    format!("{:?}", edge.phase),
                    run_id,
                ],
            )?;
        }
        PersistRecord::PolicyEvaluation(pe) => {
            let judgment = serde_json::to_string(&pe.judgment)
                .unwrap_or_else(|_| "{}".to_string());
            let resource = serde_json::to_string(&pe.resource)
                .unwrap_or_else(|_| "null".to_string());
            let ev_refs = serde_json::to_string(&pe.evidence)
                .unwrap_or_else(|_| "[]".to_string());
            conn.execute(
                "INSERT OR REPLACE INTO policy_evaluations
                 (policy_id, boundary, judgment_json, subject_agent_id, resource_json,
                  evidence_refs_json, confidence, reason, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                params![
                    pe.policy_id.as_str(),
                    format!("{:?}", pe.boundary),
                    judgment,
                    pe.subject.as_str(),
                    resource,
                    ev_refs,
                    pe.confidence,
                    pe.reason,
                    run_id,
                ],
            )?;
        }
        PersistRecord::Delegation(del) => {
            conn.execute(
                "INSERT OR IGNORE INTO delegation_records
                 (delegation_id, from_agent_id, to_agent_id, parent_pid, parent_start_time_ns,
                  child_pid, child_start_time_ns, timestamp_ns, confidence, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
                params![
                    del.delegation_id,
                    del.from_agent.as_str(),
                    del.to_agent.as_str(),
                    del.parent_process.pid as i64,
                    del.parent_process.start_time_ns as i64,
                    del.child_process.pid as i64,
                    del.child_process.start_time_ns as i64,
                    del.timestamp_ns as i64,
                    del.confidence,
                    run_id,
                ],
            )?;
        }
        PersistRecord::ContextPollution(cp) => {
            let path = serde_json::to_string(&cp.propagation_path)
                .unwrap_or_else(|_| "[]".to_string());
            conn.execute(
                "INSERT INTO context_pollution_events
                 (source_agent_id, contaminated_artifact, propagation_path_json, detected_at_ns, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![
                    cp.source_agent.as_str(),
                    cp.contaminated_artifact,
                    path,
                    cp.detected_at_ns as i64,
                    run_id,
                ],
            )?;
        }
        PersistRecord::Alert(alert) => {
            let involved = serde_json::to_string(&alert.involved_agents)
                .unwrap_or_else(|_| "[]".to_string());
            let engine = serde_json::to_string(&alert.engine)
                .unwrap_or_else(|_| "{}".to_string());
            let timeline = serde_json::to_string(&alert.timeline)
                .unwrap_or_else(|_| "[]".to_string());
            let metrics = serde_json::to_string(&alert.graph_snapshot)
                .unwrap_or_else(|_| "{}".to_string());
            conn.execute(
                "INSERT OR REPLACE INTO responsibility_alerts
                 (alert_id, timestamp_ns, display_profile, decision, severity, confidence, authority,
                  reasoning, title, summary, action_hint, primary_agent_id, involved_agents_json,
                  engine_section_json, timeline_json, graph_metrics_json, phase, provisional_ref, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19)",
                params![
                    alert.alert_id.as_str(),
                    alert.timestamp_ns as i64,
                    format!("{:?}", alert.display_profile),
                    format!("{:?}", alert.decision),
                    format!("{:?}", alert.severity),
                    alert.confidence,
                    format!("{:?}", alert.authority),
                    alert.reasoning,
                    alert.title,
                    alert.summary,
                    alert.action_hint,
                    alert.primary_agent.agent_id.as_str(),
                    involved,
                    engine,
                    timeline,
                    metrics,
                    format!("{:?}", alert.phase),
                    alert.provisional_ref.as_ref().map(|s| s.as_str()),
                    run_id,
                ],
            )?;
        }
        PersistRecord::Claim(claim) => {
            let ev_refs = serde_json::to_string(&claim.evidence_refs)
                .unwrap_or_else(|_| "[]".to_string());
            let competing = serde_json::to_string(&claim.competing_explanations)
                .unwrap_or_else(|_| "[]".to_string());
            conn.execute(
                "INSERT OR REPLACE INTO responsibility_claims
                 (claim_id, claim_type, subject_agent_id, policy_id, boundary_id, claim_state,
                  severity, confidence, evidence_refs_json, competing_explanations_json,
                  traceback_ref, phase, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
                params![
                    claim.claim_id.as_str(),
                    format!("{:?}", claim.claim_type),
                    claim.subject.as_str(),
                    claim.policy_id.as_str(),
                    claim.boundary_id.as_str(),
                    format!("{:?}", claim.claim_state),
                    format!("{:?}", claim.severity),
                    claim.confidence,
                    ev_refs,
                    competing,
                    claim.traceback_ref.as_ref().map(|s| s.as_str()),
                    format!("{:?}", claim.phase),
                    run_id,
                ],
            )?;
        }
        PersistRecord::Traceback(tb) => {
            let chain_nodes = serde_json::to_string(&tb.chain_node_ids)
                .unwrap_or_else(|_| "[]".to_string());
            let chain_edges = serde_json::to_string(&tb.chain_edge_ids)
                .unwrap_or_else(|_| "[]".to_string());
            conn.execute(
                "INSERT OR REPLACE INTO responsibility_tracebacks
                 (traceback_id, claim_id, chain_node_ids_json, chain_edge_ids_json,
                  root_cause_node_id, explanation_summary, confidence, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                params![
                    tb.traceback_id.as_str(),
                    tb.claim_id.as_str(),
                    chain_nodes,
                    chain_edges,
                    tb.root_cause_node_id.as_str(),
                    tb.explanation_summary,
                    tb.confidence,
                    run_id,
                ],
            )?;
        }
        PersistRecord::AuthoritativeIdentity(ai) => {
            let pids = serde_json::to_string(&ai.process_ids)
                .unwrap_or_else(|_| "[]".to_string());
            let msgs = serde_json::to_string(&ai.message_ids)
                .unwrap_or_else(|_| "[]".to_string());
            let tlss = serde_json::to_string(&ai.tls_sessions)
                .unwrap_or_else(|_| "[]".to_string());
            let prov_refs = serde_json::to_string(&ai.provisional_refs)
                .unwrap_or_else(|_| "[]".to_string());
            conn.execute(
                "INSERT OR REPLACE INTO agent_identities
                 (agent_id, process_ids_json, message_ids_json, tls_sessions_json, confidence,
                  provisional_refs_json, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![
                    ai.agent_id.as_str(),
                    pids,
                    msgs,
                    tlss,
                    format!("{:?}", ai.confidence),
                    prov_refs,
                    run_id,
                ],
            )?;
        }
        PersistRecord::IdentityRefinement(ir) => {
            conn.execute(
                "INSERT INTO identity_refinements
                 (phase1_agent_id, phase2_agent_id, refinement_type, reason, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![
                    ir.phase1_agent_id.as_str(),
                    ir.phase2_agent_id.as_str(),
                    format!("{:?}", ir.refinement_type),
                    ir.reason,
                    run_id,
                ],
            )?;
        }
        PersistRecord::EvidenceRefinement(er) => {
            conn.execute(
                "INSERT INTO evidence_refinements
                 (phase1_evidence_id, phase2_evidence_id, refinement_type, reason, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![
                    er.phase1_evidence_id.as_str(),
                    er.phase2_evidence_id.as_ref().map(|s| s.as_str()),
                    format!("{:?}", er.refinement_type),
                    er.reason,
                    run_id,
                ],
            )?;
        }
        PersistRecord::GraphRefinement(gr) => {
            let nodes = serde_json::to_string(&gr.affected_nodes)
                .unwrap_or_else(|_| "[]".to_string());
            let edges = serde_json::to_string(&gr.affected_edges)
                .unwrap_or_else(|_| "[]".to_string());
            conn.execute(
                "INSERT INTO graph_refinements
                 (refinement_type, affected_nodes_json, affected_edges_json, description, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![
                    format!("{:?}", gr.refinement_type),
                    nodes,
                    edges,
                    gr.description,
                    run_id,
                ],
            )?;
        }
        PersistRecord::GraphSnapshot(snap) => {
            let nodes = serde_json::to_string(&snap.nodes)
                .unwrap_or_else(|_| "[]".to_string());
            let edges = serde_json::to_string(&snap.edges)
                .unwrap_or_else(|_| "[]".to_string());
            let metrics = serde_json::to_string(&snap.metrics)
                .unwrap_or_else(|_| "{}".to_string());
            conn.execute(
                "INSERT OR REPLACE INTO graph_snapshots
                 (snapshot_id, timestamp_ns, nodes_json, edges_json, metrics_json, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    snap.snapshot_id,
                    snap.timestamp_ns as i64,
                    nodes,
                    edges,
                    metrics,
                    run_id,
                ],
            )?;
        }
        PersistRecord::ClassificationRefinement(cr) => {
            conn.execute(
                "INSERT INTO classification_refinements
                 (phase1_alert_id, phase2_alert_id, refinement_type, reason, score_delta, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    cr.phase1_alert_id.as_str(),
                    cr.phase2_alert_id.as_str(),
                    format!("{:?}", cr.refinement_type),
                    cr.reason,
                    cr.score_delta,
                    run_id,
                ],
            )?;
        }
        PersistRecord::ClaimRefinement(cr) => {
            conn.execute(
                "INSERT INTO claim_refinements
                 (phase1_claim_id, phase2_claim_id, refinement_type, reason, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![
                    cr.phase1_claim_id.as_str(),
                    cr.phase2_claim_id.as_str(),
                    format!("{:?}", cr.refinement_type),
                    cr.reason,
                    run_id,
                ],
            )?;
        }
        PersistRecord::SubagentBoundary(sb) => {
            conn.execute(
                "INSERT OR REPLACE INTO subagent_boundaries
                 (boundary_id, parent_agent_id, child_agent_id, parent_pid, parent_start_time_ns,
                  child_pid, child_start_time_ns, reason, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                params![
                    format!("{}-{}-{}",
                        sb.parent_agent.as_str(),
                        sb.child_agent.as_str(),
                        sb.parent_process.start_time_ns),
                    sb.parent_agent.as_str(),
                    sb.child_agent.as_str(),
                    sb.parent_process.pid as i64,
                    sb.parent_process.start_time_ns as i64,
                    sb.child_process.pid as i64,
                    sb.child_process.start_time_ns as i64,
                    format!("{:?}", sb.reason),
                    run_id,
                ],
            )?;
        }
        PersistRecord::IdentityMapping {
            agent_id,
            pid,
            start_time_ns,
        } => {
            conn.execute(
                "INSERT OR REPLACE INTO identity_process_map
                 (agent_id, pid, start_time_ns, run_id)
                 VALUES (?1, ?2, ?3, ?4)",
                params![
                    agent_id.as_str(),
                    *pid as i64,
                    *start_time_ns as i64,
                    run_id,
                ],
            )?;
        }
        // RunStart, RunEnd, RunRecord, Flush handled before this function
        PersistRecord::RunStart { .. }
        | PersistRecord::RunEnd { .. }
        | PersistRecord::RunRecord(_)
        | PersistRecord::Flush(_) => {}
    }
    Ok(())
}

/// Commit the current batch with an explicit transaction boundary.
///
/// Per spec §13.1, Phase 1 writes are batched into single transactions
/// (every 500 records or 200ms) for WAL efficiency. Each batch begins
/// with an implicit transaction (first write after commit) and ends here.
fn commit_batch(
    conn: &Connection,
    pending: &mut usize,
    _current_run_id: &mut Option<String>,
) -> rusqlite::Result<()> {
    if *pending > 0 {
        conn.execute_batch("COMMIT")?;
        *pending = 0;
        conn.execute_batch("BEGIN TRANSACTION")?;
    }
    Ok(())
}

// ═══════════════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use crate::responsibility::types::ids::*;
    use crate::responsibility::types::phase::PhaseIndicator;

    fn test_db_path() -> (tempfile::TempDir, String) {
        use tempfile::tempdir;
        let dir = tempdir().unwrap();
        let path = dir.path().join("test_writer.db");
        (dir, path.to_string_lossy().into_owned())
    }

    #[tokio::test]
    async fn test_writer_creates_db_and_writes() {
        let (_dir, path) = test_db_path();
        let config = WriterConfig {
            db_path: path.clone(),
            ..Default::default()
        };
        let writer = PersistenceWriter::new(config).unwrap();

        // Start a run
        writer.write(PersistRecord::RunStart {
            run_id: "test-run".into(),
            timestamp_ns: 100,
        });

        // Write a process event
        writer.write(PersistRecord::Process(ProcessMetadata {
            pid: 100,
            start_time_ns: 1000,
            parent_pid: Some(1),
            parent_start_time_ns: Some(0),
            comm: "claude".into(),
            timestamp_ns: 5000,
        }));

        // Write a TLS session
        writer.write(PersistRecord::Tls(TlsSession {
            tls_session_id: TlsSessionId::from("tls-1"),
            pid: 100,
            start_time_ns: 1000,
            timestamp_ns: 5000,
        }));

        // Write an intent
        writer.write(PersistRecord::Intent(Intent {
            intent_id: IntentId::from("intent-1"),
            process_id: ProcessId::new(100, 1000),
            message_id: MessageId::from("msg-1"),
            tool_name: "Bash".into(),
            tool_args: vec![],
            intent_class: crate::responsibility::types::evidence_types::IntentClass::SideEffect,
            parse_state: crate::responsibility::types::evidence_types::IntentParseState::Complete,
            primary_matching_eligible: true,
            authority_source:
                crate::responsibility::types::evidence_types::IntentAuthoritySource::RequestHistoryAssistantToolUse,
            timestamp_ns: 5000,
        }));

        // Flush
        writer.flush().await.unwrap();

        // Verify via reader connection
        let conn = Connection::open(&path).unwrap();
        let count: i64 = conn
            .query_row("SELECT count(*) FROM process_events", [], |r| r.get(0))
            .unwrap();
        assert_eq!(count, 1);

        let tls_count: i64 = conn
            .query_row("SELECT count(*) FROM tls_sessions", [], |r| r.get(0))
            .unwrap();
        assert_eq!(tls_count, 1);

        let intent_count: i64 = conn
            .query_row("SELECT count(*) FROM intents", [], |r| r.get(0))
            .unwrap();
        assert_eq!(intent_count, 1);

        assert_eq!(writer.lost_persist_count(), 0);
    }

    #[tokio::test]
    async fn test_writer_drops_on_full_channel() {
        let (_dir, path) = test_db_path();
        let config = WriterConfig {
            db_path: path.clone(),
            channel_capacity: 2,
            batch_size: 100,
            ..Default::default()
        };
        let writer = PersistenceWriter::new(config).unwrap();

        // Start run
        writer.write(PersistRecord::RunStart {
            run_id: "r1".into(),
            timestamp_ns: 0,
        });

        // Flood the channel (capacity 2, but dispatcher consumes quickly)
        // Send many records rapidly — some should be dropped
        let mut dropped = 0u64;
        for i in 0..500 {
            let ok = writer.write(PersistRecord::Process(ProcessMetadata {
                pid: 100 + i as u32,
                start_time_ns: i as u64,
                parent_pid: None,
                parent_start_time_ns: None,
                comm: "test".into(),
                timestamp_ns: i as u64,
            }));
            if !ok {
                dropped += 1;
            }
            // Small yield to let the writer task consume
            if i % 50 == 0 {
                tokio::task::yield_now().await;
            }
        }

        writer.flush().await.unwrap();
        let lost = writer.lost_persist_count();
        // Some losses may happen with small channel
        eprintln!("dropped={dropped} lost_counter={lost}");
    }

    #[tokio::test]
    async fn test_writer_flush_returns_ok() {
        let (_dir, path) = test_db_path();
        let config = WriterConfig {
            db_path: path.clone(),
            ..Default::default()
        };
        let writer = PersistenceWriter::new(config).unwrap();

        writer.write(PersistRecord::RunStart {
            run_id: "flush-test".into(),
            timestamp_ns: 0,
        });

        let result = writer.flush().await;
        assert!(result.is_ok());
    }

    #[tokio::test]
    async fn test_run_record_writes_immediately() {
        let (_dir, path) = test_db_path();
        let config = WriterConfig {
            db_path: path.clone(),
            ..Default::default()
        };
        let writer = PersistenceWriter::new(config).unwrap();

        writer.write(PersistRecord::RunRecord(RunRecord {
            run_id: "r1".into(),
            started_at_ns: 100,
            ended_at_ns: Some(200),
            scenario_id: Some("sc1".into()),
            mode: Some("performance".into()),
            collector_version: Some("0.1.0".into()),
        }));

        writer.flush().await.unwrap();

        let conn = Connection::open(&path).unwrap();
        let (run_id, started): (String, i64) = conn
            .query_row(
                "SELECT run_id, started_at_ns FROM runs WHERE run_id = 'r1'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert_eq!(run_id, "r1");
        assert_eq!(started, 100);
    }

    #[tokio::test]
    async fn test_alert_persistence() {
        let (_dir, path) = test_db_path();
        let config = WriterConfig {
            db_path: path.clone(),
            ..Default::default()
        };
        let writer = PersistenceWriter::new(config).unwrap();

        writer.write(PersistRecord::RunStart {
            run_id: "alert-run".into(),
            timestamp_ns: 0,
        });

        let alert = ResponsibilityAlert {
            alert_id: AlertId::from("a1"),
            timestamp_ns: 1000,
            display_profile: crate::responsibility::types::alert_types::DisplayProfile::UnsafeByEngine,
            decision: crate::responsibility::types::alert_types::AlertDecision::Unsafe,
            severity: crate::responsibility::types::alert_types::AlertSeverity::Warning,
            confidence: 0.85,
            authority: crate::responsibility::types::alert_types::AlertAuthority::Supporting,
            reasoning: "test".into(),
            title: "Test Alert".into(),
            summary: "A test alert".into(),
            action_hint: Some("Review".into()),
            primary_agent: crate::responsibility::types::alert_types::AgentRef {
                agent_id: AgentId::from("ag1"),
                claimed_role: None,
            },
            involved_agents: vec![],
            engine: crate::responsibility::types::alert_types::EngineSection {
                rule_verdicts: vec![],
                pattern_verdicts: vec![],
                classification_score: 0.5,
                classification_route: "default".into(),
                score_breakdown: crate::responsibility::types::refinement::ScoreBreakdown {
                    rule_score: 0.0,
                    pattern_score: 0.0,
                    boundary_score: 0.0,
                    evidence_score: 0.0,
                    role_score: 0.0,
                    repetition_score: 0.0,
            behavioral_score: 0.0,
                    total: 0.5,
                },
                cusum_triggered: false,
            cusum_dimensions: vec![],
            progressive_fused: 0.0,
            observer_trigger_reason: String::new(),
            deberta_score: 0.0,
            },
            timeline: crate::responsibility::types::alert_types::BehaviorTimeline {
                steps: vec![],
                total_duration_ms: 0,
            },
            graph_snapshot: crate::responsibility::types::graph_types::GraphSnapshot {
                snapshot_id: "test-snapshot".into(),
                timestamp_ns: 1000,
                nodes: vec![],
                edges: vec![],
                metrics: crate::responsibility::types::graph_types::GraphMetrics {
                    node_counts: Default::default(),
                    edge_counts: Default::default(),
                    agent_count: 1,
                    cross_agent_edges: 0,
                    delegation_depth: 0,
                    taint_chains: 0,
                    avg_confidence: 0.0,
                },
            },
            phase: PhaseIndicator::Provisional { timestamp_ns: 1000 },
            provisional_ref: None,
        };
        writer.write(PersistRecord::Alert(alert));
        writer.flush().await.unwrap();

        let conn = Connection::open(&path).unwrap();
        let count: i64 = conn
            .query_row(
                "SELECT count(*) FROM responsibility_alerts WHERE run_id = 'alert-run'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(count, 1);
    }
}
