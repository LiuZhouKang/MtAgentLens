// SPDX-License-Identifier: Apache-2.0 OR MIT
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
use crate::responsibility::types::ids::{AgentId, MessageId, TlsSessionId};
use crate::responsibility::types::policy_types::{
    ContextPollution, DelegationRecord, PolicyEvaluation,
};
use crate::responsibility::types::refinement::{
    ClaimRefinement, ClassificationRefinement, EvidenceRefinement, GraphRefinement,
    IdentityRefinement,
};
use crate::responsibility::types::replay_types::RunRecord;

use rusqlite::{Connection, OptionalExtension, params};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
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
    /// One observed process-to-agent belief. Append-only: the writer inserts a
    /// row only when it differs from the process's current belief, so the
    /// table is a history, not a mutable map.
    IdentityMapping {
        agent_id: AgentId,
        pid: u32,
        start_time_ns: u64,
        belief_level: IdentityBeliefLevel,
        observed_at_ns: u64,
        source: &'static str,
    },

    // --- Run metadata ---
    /// Start a new run
    RunStart { run_id: String, timestamp_ns: u64 },
    /// End a run
    RunEnd { run_id: String, timestamp_ns: u64 },
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

/// How strongly a process-to-agent belief is held.
///
/// Ordered: `Unknown` < `Provisional` < `Inferred` < `Authoritative`. The
/// compatibility views resolve a process to its strongest belief, so a late
/// weak observation can never erase an authoritative resolution.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum IdentityBeliefLevel {
    Unknown,
    Provisional,
    Inferred,
    Authoritative,
}

impl IdentityBeliefLevel {
    pub fn as_str(self) -> &'static str {
        match self {
            IdentityBeliefLevel::Unknown => "Unknown",
            IdentityBeliefLevel::Provisional => "Provisional",
            IdentityBeliefLevel::Inferred => "Inferred",
            IdentityBeliefLevel::Authoritative => "Authoritative",
        }
    }
}

/// Handle to the async persistence writer.
/// Clone is cheap (just clones the channel sender).
#[derive(Clone)]
pub struct PersistenceWriter {
    tx: mpsc::Sender<PersistRecord>,
    /// Records dropped because the channel was full or had no active run.
    lost_count: Arc<AtomicU64>,
    /// Write-path failures (commit / transaction / row writes).
    ///
    /// Separate from `lost_count`: dropped records never reached SQLite,
    /// whereas these failed *at* SQLite. Surfaced so callers can tell a
    /// healthy run from one silently degrading.
    write_failures: Arc<AtomicU64>,
    /// Records rejected because the writer task is gone (`TrySendError::Closed`).
    ///
    /// This is not "channel full": nothing will ever drain the queue again, so
    /// every subsequent write is lost too. Counted separately so a run whose
    /// writer died cannot report "flushed cleanly".
    closed_count: Arc<AtomicU64>,
}

impl PersistenceWriter {
    /// Spawn the writer task and return a handle.
    /// The spawned task runs independently, writing batches to SQLite.
    pub fn new(config: WriterConfig) -> Result<Self, String> {
        let (tx, rx) = mpsc::channel::<PersistRecord>(config.channel_capacity);
        let lost_count = Arc::new(AtomicU64::new(0));
        let lost_for_task = Arc::clone(&lost_count);
        let write_failures = Arc::new(AtomicU64::new(0));
        let failures_for_task = Arc::clone(&write_failures);
        let closed_count = Arc::new(AtomicU64::new(0));

        // Open through the shared schema gate: classification, version gate,
        // pragmas and migrations happen in that order, in one place.
        let conn = super::schema::open_responsibility_db(&config.db_path)
            .map_err(|e| format!("failed to open SQLite DB: {e}"))?;

        let batch_size = config.batch_size;
        let batch_interval_ms = config.batch_interval_ms;

        tokio::spawn(async move {
            writer_task(
                conn,
                rx,
                batch_size,
                batch_interval_ms,
                lost_for_task,
                failures_for_task,
            )
            .await;
        });

        Ok(Self {
            tx,
            lost_count,
            write_failures,
            closed_count,
        })
    }

    /// Send a persist record. Non-blocking — discards if the channel is full.
    /// Returns `true` if the record was queued, `false` if it was dropped.
    pub fn write(&self, record: PersistRecord) -> bool {
        let kind = record_kind(&record);
        match self.tx.try_send(record) {
            Ok(()) => true,
            Err(mpsc::error::TrySendError::Full(_)) => {
                let n = self.lost_count.fetch_add(1, Ordering::Relaxed) + 1;
                // Log the first few drops with their kind: an aggregate counter
                // cannot tell a stuck writer from one flooded by a single
                // quadratic producer. Capped so a saturated run stays readable.
                if n <= 20 {
                    eprintln!(
                        "persistence: dropped {} record (channel full, total {n})",
                        kind
                    );
                }
                false
            }
            Err(mpsc::error::TrySendError::Closed(_)) => {
                self.closed_count.fetch_add(1, Ordering::Relaxed);
                false
            }
        }
    }

    /// Queue a record, waiting for channel capacity instead of dropping it.
    ///
    /// Use this for batch/finalize paths and for parent records whose children
    /// reference them by foreign key. `try_send` dropping a node while its edge
    /// is already queued turns one drop into a cascade of FOREIGN KEY failures.
    /// The live per-event path deliberately stays non-blocking: stalling the
    /// event loop there would drop kernel events instead of database rows.
    pub async fn write_blocking(&self, record: PersistRecord) -> bool {
        match self.tx.send(record).await {
            Ok(()) => true,
            Err(_) => {
                self.closed_count.fetch_add(1, Ordering::Relaxed);
                false
            }
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

    /// Records rejected because the writer task had already exited.
    ///
    /// Non-zero means the run lost data after the writer died; unlike a full
    /// channel, it will not recover on its own.
    pub fn channel_closed_count(&self) -> u64 {
        self.closed_count.load(Ordering::Relaxed)
    }

    /// Number of write-path failures (commit / transaction / row writes).
    ///
    /// Non-zero means data reached SQLite but was rejected — a materially
    /// different (and more serious) condition than `lost_persist_count`,
    /// which counts records that never made it to the writer at all.
    pub fn write_failure_count(&self) -> u64 {
        self.write_failures.load(Ordering::Relaxed)
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
    write_failures: Arc<AtomicU64>,
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
                        // Channel closed — flush remaining and exit.
                        //
                        // Ask SQLite whether a transaction is open instead of
                        // trusting `pending`: a failed row write leaves the
                        // transaction open with `pending == 0`, and skipping
                        // the COMMIT there would roll back rows that *did*
                        // make it into the transaction.
                        if !conn.is_autocommit()
                            && let Err(e) = conn.execute_batch("COMMIT")
                        {
                            record_write_failure(&write_failures, "final commit", &e);
                        }
                        return;
                    }
                }
            }
            _ = &mut batch_deadline => {
                // Timer fired — commit whatever the open transaction holds.
                if let Err(e) = commit_batch(&conn, &mut pending) {
                    record_write_failure(&write_failures, "batch commit", &e);
                }
                continue;
            }
        };

        // Process the record
        match record {
            PersistRecord::Flush(tx) => {
                // A flush must report failure honestly: replying Ok after a
                // failed commit told the caller its data was durable when it
                // was not. The oneshot send itself stays best-effort (the
                // caller may have gone away), but the payload now tells truth.
                if let Err(e) = commit_batch(&conn, &mut pending) {
                    record_write_failure(&write_failures, "flush commit", &e);
                    let _ = tx.send(Err(format!("flush failed: {e}")));
                    continue;
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
                if let Err(e) = commit_batch(&conn, &mut pending) {
                    record_write_failure(&write_failures, "batch commit", &e);
                }
                // Insert run row immediately so RunEnd can UPDATE it later
                if let Err(e) = conn.execute(
                    "INSERT OR IGNORE INTO runs (run_id, started_at_ns) VALUES (?1, ?2)",
                    params![run_id, timestamp_ns as i64],
                ) {
                    record_write_failure(&write_failures, "insert run", &e);
                }
                continue;
            }
            PersistRecord::RunEnd {
                run_id,
                timestamp_ns,
            } => {
                // Run end is written immediately
                if let Err(e) = commit_batch(&conn, &mut pending) {
                    record_write_failure(&write_failures, "batch commit", &e);
                }
                if let Err(e) = conn.execute(
                    "UPDATE runs SET ended_at_ns = ?1 WHERE run_id = ?2",
                    params![timestamp_ns as i64, run_id],
                ) {
                    record_write_failure(&write_failures, "update run end", &e);
                }
                continue;
            }
            PersistRecord::RunRecord(record) => {
                // Commit pending batch first, then write run metadata immediately
                if let Err(e) = commit_batch(&conn, &mut pending) {
                    record_write_failure(&write_failures, "batch commit", &e);
                }
                current_run_id = Some(record.run_id.clone());
                if let Err(e) = conn.execute(
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
                ) {
                    record_write_failure(&write_failures, "insert run record", &e);
                }
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
                // Open a transaction before the first write in a batch.
                //
                // Guard on SQLite's own transaction state, not on `pending`.
                // `pending` is a *trigger* counter (it decides when a batch is
                // full), and it deliberately does not increment when a row
                // write fails — so after one failed write it reads 0 while the
                // transaction is still open. Issuing BEGIN there is rejected
                // with "cannot start a transaction within a transaction",
                // which used to be swallowed and is now counted as a write
                // failure, turning one real failure into a standing false
                // alarm. `is_autocommit()` cannot drift out of sync.
                if conn.is_autocommit()
                    && let Err(e) = conn.execute_batch("BEGIN TRANSACTION")
                {
                    record_write_failure(&write_failures, "begin transaction", &e);
                }
                if let Err(e) = write_record(&conn, &other, &run_id) {
                    record_write_failure(&write_failures, record_kind(&other), &e);
                    continue;
                }
            }
        }

        pending += 1;

        // Commit if batch is full
        if pending >= batch_size
            && let Err(e) = commit_batch(&conn, &mut pending)
        {
            record_write_failure(&write_failures, "batch commit", &e);
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
            let tool_args =
                serde_json::to_string(&intent.tool_args).unwrap_or_else(|_| "[]".to_string());
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
            let norm_args =
                serde_json::to_string(&action.normalized_args).unwrap_or_else(|_| "[]".to_string());
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
            let matched =
                serde_json::to_string(&ev.matched_fields).unwrap_or_else(|_| "{}".to_string());
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
                    diag.intent_id.as_deref(),
                    diag.candidate_id.as_deref(),
                    format!("{:?}", diag.reason),
                    diag.timestamp_ns as i64,
                    run_id,
                ],
            )?;
        }
        PersistRecord::ProvisionalIdentity(pi) => {
            let pids = serde_json::to_string(&pi.process_ids).unwrap_or_else(|_| "[]".to_string());
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
            let resource =
                serde_json::to_string(&node.resource).unwrap_or_else(|_| "null".to_string());
            let ev_refs =
                serde_json::to_string(&node.evidence_refs).unwrap_or_else(|_| "[]".to_string());
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
            let ev_refs =
                serde_json::to_string(&edge.evidence_refs).unwrap_or_else(|_| "[]".to_string());
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
            let judgment = serde_json::to_string(&pe.judgment).unwrap_or_else(|_| "{}".to_string());
            let resource =
                serde_json::to_string(&pe.resource).unwrap_or_else(|_| "null".to_string());
            let ev_refs = serde_json::to_string(&pe.evidence).unwrap_or_else(|_| "[]".to_string());
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
            let path =
                serde_json::to_string(&cp.propagation_path).unwrap_or_else(|_| "[]".to_string());
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
            let involved =
                serde_json::to_string(&alert.involved_agents).unwrap_or_else(|_| "[]".to_string());
            let engine = serde_json::to_string(&alert.engine).unwrap_or_else(|_| "{}".to_string());
            let timeline =
                serde_json::to_string(&alert.timeline).unwrap_or_else(|_| "[]".to_string());
            let metrics =
                serde_json::to_string(&alert.graph_snapshot).unwrap_or_else(|_| "{}".to_string());
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
            let ev_refs =
                serde_json::to_string(&claim.evidence_refs).unwrap_or_else(|_| "[]".to_string());
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
            let chain_nodes =
                serde_json::to_string(&tb.chain_node_ids).unwrap_or_else(|_| "[]".to_string());
            let chain_edges =
                serde_json::to_string(&tb.chain_edge_ids).unwrap_or_else(|_| "[]".to_string());
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
            let pids = serde_json::to_string(&ai.process_ids).unwrap_or_else(|_| "[]".to_string());
            let msgs = serde_json::to_string(&ai.message_ids).unwrap_or_else(|_| "[]".to_string());
            let tlss = serde_json::to_string(&ai.tls_sessions).unwrap_or_else(|_| "[]".to_string());
            let prov_refs =
                serde_json::to_string(&ai.provisional_refs).unwrap_or_else(|_| "[]".to_string());
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
            let nodes =
                serde_json::to_string(&gr.affected_nodes).unwrap_or_else(|_| "[]".to_string());
            let edges =
                serde_json::to_string(&gr.affected_edges).unwrap_or_else(|_| "[]".to_string());
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
            let nodes = serde_json::to_string(&snap.nodes).unwrap_or_else(|_| "[]".to_string());
            let edges = serde_json::to_string(&snap.edges).unwrap_or_else(|_| "[]".to_string());
            let metrics = serde_json::to_string(&snap.metrics).unwrap_or_else(|_| "{}".to_string());
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
                    format!(
                        "{}-{}-{}",
                        sb.parent_agent.as_str(),
                        sb.child_agent.as_str(),
                        sb.parent_process.start_time_ns
                    ),
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
            belief_level,
            observed_at_ns,
            source,
        } => {
            append_identity_belief(
                conn,
                agent_id,
                *pid,
                *start_time_ns,
                run_id,
                *belief_level,
                *observed_at_ns,
                source,
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

/// Append one identity belief unless it repeats the process's current belief.
///
/// Returns `true` when a row was inserted. The table is a history: repeating
/// the same provisional observation on every tick would grow it without adding
/// information, and a weaker late observation must not rewrite history.
pub(crate) fn append_identity_belief(
    conn: &Connection,
    agent_id: &AgentId,
    pid: u32,
    start_time_ns: u64,
    run_id: &str,
    belief_level: IdentityBeliefLevel,
    observed_at_ns: u64,
    source: &str,
) -> rusqlite::Result<bool> {
    let current: Option<(String, String)> = conn
        .query_row(
            "SELECT agent_id, belief_level FROM identity_process_beliefs
             WHERE pid = ?1 AND start_time_ns = ?2 AND run_id = ?3
             ORDER BY CASE belief_level
                          WHEN 'Authoritative' THEN 3
                          WHEN 'Inferred' THEN 2
                          WHEN 'Provisional' THEN 1
                          ELSE 0
                      END DESC,
                      observed_at_ns DESC,
                      belief_id DESC
             LIMIT 1",
            params![pid as i64, start_time_ns as i64, run_id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()?;
    let unchanged = current
        .as_ref()
        .is_some_and(|(current_agent, current_level)| {
            current_agent == agent_id.as_str() && current_level == belief_level.as_str()
        });
    if unchanged {
        return Ok(false);
    }
    conn.execute(
        "INSERT INTO identity_process_beliefs
         (agent_id, pid, start_time_ns, run_id, belief_level, observed_at_ns, source)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
        params![
            agent_id.as_str(),
            pid as i64,
            start_time_ns as i64,
            run_id,
            belief_level.as_str(),
            observed_at_ns as i64,
            source,
        ],
    )?;
    Ok(true)
}

/// Record a write-path failure: bump the counter and log it.
///
/// Every failure here was previously discarded with `let _ =`, which is how a
/// permanently-stalling COMMIT loop stayed invisible.
/// Short record kind used in failure logs. A "FOREIGN KEY constraint failed"
/// without the record type forced a log-only diagnosis; with it, the exact
/// producer (evidence vs edge vs claim) is visible.
fn record_kind(record: &PersistRecord) -> &'static str {
    match record {
        PersistRecord::Process(_) => "process",
        PersistRecord::Tls(_) => "tls",
        PersistRecord::MessageBinding { .. } => "message_binding",
        PersistRecord::Intent(_) => "intent",
        PersistRecord::Action(_) => "action",
        PersistRecord::Evidence(_) => "evidence",
        PersistRecord::NonLink(_) => "non_link",
        PersistRecord::ProvisionalIdentity(_) => "provisional_identity",
        PersistRecord::GraphNode(_) => "graph_node",
        PersistRecord::GraphEdge(_) => "graph_edge",
        PersistRecord::PolicyEvaluation(_) => "policy_evaluation",
        PersistRecord::Delegation(_) => "delegation",
        PersistRecord::ContextPollution(_) => "context_pollution",
        PersistRecord::Alert(_) => "alert",
        PersistRecord::Claim(_) => "claim",
        PersistRecord::Traceback(_) => "traceback",
        PersistRecord::AuthoritativeIdentity(_) => "authoritative_identity",
        PersistRecord::IdentityRefinement(_) => "identity_refinement",
        PersistRecord::EvidenceRefinement(_) => "evidence_refinement",
        PersistRecord::GraphRefinement(_) => "graph_refinement",
        PersistRecord::GraphSnapshot(_) => "graph_snapshot",
        PersistRecord::ClassificationRefinement(_) => "classification_refinement",
        PersistRecord::ClaimRefinement(_) => "claim_refinement",
        PersistRecord::SubagentBoundary(_) => "subagent_boundary",
        PersistRecord::IdentityMapping { .. } => "identity_mapping",
        PersistRecord::RunStart { .. } => "run_start",
        PersistRecord::RunEnd { .. } => "run_end",
        PersistRecord::RunRecord(_) => "run_record",
        PersistRecord::Flush(_) => "flush",
    }
}

fn record_write_failure(counter: &AtomicU64, what: &str, err: &dyn std::fmt::Display) {
    let n = counter.fetch_add(1, Ordering::Relaxed) + 1;
    eprintln!("persistence: {what} failed (total {n}): {err}");
}

/// Commit the current batch, closing whatever transaction is open.
///
/// Phase 1 writes are batched into single transactions for WAL efficiency.
/// The batch trigger is `pending >= batch_size` (or the interval timer), but
/// the *transaction boundary* is owned by SQLite: this function just asks
/// whether a transaction is open and closes it. Callers must not infer the
/// transaction state from `pending` — after a failed row write the counter
/// reads 0 while the transaction is still open.
///
/// This function never opens a transaction. Opening is done lazily by the
/// record path on the next write (`is_autocommit()` guard), which keeps the
/// two operations from racing each other — the previous version re-opened a
/// transaction here *and* in the record path, so every batch after the first
/// produced a spurious "cannot start a transaction within a transaction".
fn commit_batch(conn: &Connection, pending: &mut usize) -> rusqlite::Result<()> {
    // Reset BEFORE attempting the commit. A batch that has been attempted must
    // never be retried: if `COMMIT` fails and `pending` stays non-zero, every
    // subsequent batch re-issues COMMIT against a transaction that no longer
    // exists, turning a transient failure into a permanent stall.
    *pending = 0;

    // Nothing open → nothing to commit. Not an error: the flush and
    // run-boundary paths call this unconditionally.
    if conn.is_autocommit() {
        return Ok(());
    }

    if let Err(e) = conn.execute_batch("COMMIT") {
        // A failed COMMIT can leave the transaction open. Roll it back so the
        // connection returns to a known state; SQLite reports "no transaction
        // is active" if there was nothing to roll back, which is harmless.
        let _ = conn.execute_batch("ROLLBACK");
        return Err(e);
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

    /// Regression: every batch after the first used to be rejected with
    /// "cannot start a transaction within a transaction".
    ///
    /// `commit_batch` re-opened a transaction after its `COMMIT`, while the
    /// record path also opened one whenever `pending == 0` — so the second
    /// batch's `BEGIN` was always refused. The error was discarded with
    /// `let _ =` and stayed invisible; now that it is counted, a healthy run
    /// must report exactly zero write-path failures.
    #[tokio::test]
    async fn test_writer_reports_no_failures_across_many_batches() {
        let (_dir, path) = test_db_path();
        let config = WriterConfig {
            db_path: path.clone(),
            // Force a commit every 2 records so the test crosses many batch
            // boundaries — the defect only appears from the *second* batch on.
            batch_size: 2,
            ..Default::default()
        };
        let writer = PersistenceWriter::new(config).unwrap();

        writer.write(PersistRecord::RunStart {
            run_id: "r1".into(),
            timestamp_ns: 0,
        });

        for i in 0..10u32 {
            writer.write(PersistRecord::Process(ProcessMetadata {
                pid: 200 + i,
                start_time_ns: i as u64,
                parent_pid: None,
                parent_start_time_ns: None,
                comm: "test".into(),
                timestamp_ns: i as u64,
            }));
        }

        writer.flush().await.unwrap();
        writer.write(PersistRecord::RunEnd {
            run_id: "r1".into(),
            timestamp_ns: 100,
        });
        writer.flush().await.unwrap();

        assert_eq!(
            writer.write_failure_count(),
            0,
            "a healthy run must not report write-path failures"
        );

        // Assert the rows really landed: this is not only about the counter.
        let conn = Connection::open(&path).unwrap();
        let n: i64 = conn
            .query_row("SELECT count(*) FROM process_events", [], |r| r.get(0))
            .unwrap();
        assert_eq!(n, 10);
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
            display_profile:
                crate::responsibility::types::alert_types::DisplayProfile::UnsafeByEngine,
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

    /// Regression: a failed COMMIT must not leave `pending` non-zero, and the
    /// connection must stay usable afterwards.
    ///
    /// Previously `commit_batch` did `COMMIT?; *pending = 0;` — the `?`
    /// returned early, so `pending` kept its value. Every later batch then
    /// retried COMMIT against a transaction that no longer existed, failing
    /// forever, while the caller discarded the error via `let _ =`.
    ///
    /// The failure is produced for real: a deferred foreign-key violation makes
    /// the INSERT succeed and `COMMIT` fail, which is exactly the transient
    /// failure this path has to survive. (The earlier version of this test used
    /// "COMMIT with no active transaction" to force an error — that is now a
    /// deliberate no-op, so it can no longer stand in for a real failure.)
    #[test]
    fn commit_batch_resets_pending_when_commit_fails() {
        let (_dir, path) = test_db_path();
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(crate::responsibility::persistence::schema::SCHEMA_DDL)
            .unwrap();
        // `foreign_keys` must be set outside a transaction to take effect;
        // `defer_foreign_keys` pushes the violation to COMMIT time.
        conn.execute_batch("PRAGMA foreign_keys=ON; PRAGMA defer_foreign_keys=ON;")
            .unwrap();
        conn.execute_batch("BEGIN TRANSACTION").unwrap();
        conn.execute_batch(
            "INSERT INTO correlation_evidence
               (evidence_id, intent_id, action_id, match_level, confidence, phase, timestamp_ns, run_id)
             VALUES ('e1', 'missing-intent', 'missing-action', 'L1Exact', 0.5, 'Phase1', 1, 'r1')",
        )
        .expect("the FK violation is deferred, so this INSERT must succeed");

        let mut pending = 5usize;
        let res = commit_batch(&conn, &mut pending);

        assert!(
            res.is_err(),
            "COMMIT must surface the deferred foreign-key violation"
        );
        assert_eq!(
            pending, 0,
            "pending must be reset even on failure, otherwise every later batch retries forever"
        );
        assert!(
            conn.is_autocommit(),
            "a failed COMMIT must be rolled back so the connection is usable again"
        );
    }

    /// Committing with no transaction open is a no-op, not a failure.
    ///
    /// The record path opens transactions lazily, and the flush / run-boundary
    /// paths call `commit_batch` unconditionally, so "nothing to commit" is a
    /// normal state. Treating it as an error is what produced the 488
    /// "cannot start a transaction within a transaction" false alarms observed
    /// in a real run.
    #[test]
    fn commit_batch_with_nothing_open_is_ok() {
        let (_dir, path) = test_db_path();
        let conn = Connection::open(&path).unwrap();
        let mut pending = 7usize;

        assert!(commit_batch(&conn, &mut pending).is_ok());
        assert_eq!(pending, 0);
    }

    // ── Identity beliefs (v2) ──────────────────────────────────────────────

    fn belief_conn() -> (tempfile::TempDir, Connection) {
        let (dir, path) = test_db_path();
        let conn =
            crate::responsibility::persistence::schema::open_responsibility_db(&path).unwrap();
        (dir, conn)
    }

    fn belief_count(conn: &Connection) -> i64 {
        conn.query_row("SELECT count(*) FROM identity_process_beliefs", [], |row| {
            row.get(0)
        })
        .unwrap()
    }

    #[test]
    fn append_identity_belief_is_idempotent_for_the_same_belief() {
        let (_dir, conn) = belief_conn();
        let agent = AgentId::from("agent-1");

        for observed_at in [100, 200, 300] {
            let inserted = append_identity_belief(
                &conn,
                &agent,
                7,
                70,
                "run-1",
                IdentityBeliefLevel::Provisional,
                observed_at,
                "provisional_identity",
            )
            .unwrap();
            assert_eq!(inserted, observed_at == 100, "only the first is new");
        }
        assert_eq!(belief_count(&conn), 1);
    }

    #[test]
    fn append_identity_belief_records_stronger_and_different_beliefs() {
        let (_dir, conn) = belief_conn();
        let provisional = AgentId::from("agent-provisional");
        let authoritative = AgentId::from("agent-authoritative");

        append_identity_belief(
            &conn,
            &provisional,
            7,
            70,
            "run-1",
            IdentityBeliefLevel::Provisional,
            100,
            "provisional_identity",
        )
        .unwrap();
        append_identity_belief(
            &conn,
            &authoritative,
            7,
            70,
            "run-1",
            IdentityBeliefLevel::Authoritative,
            200,
            "authoritative_identity_resolution",
        )
        .unwrap();
        // Repeating the authoritative observation is not a new belief.
        assert!(
            !append_identity_belief(
                &conn,
                &authoritative,
                7,
                70,
                "run-1",
                IdentityBeliefLevel::Authoritative,
                300,
                "authoritative_identity_resolution",
            )
            .unwrap()
        );

        assert_eq!(belief_count(&conn), 2);
        let current: String = conn
            .query_row(
                "SELECT agent_id FROM identity_process_map WHERE pid = 7 AND start_time_ns = 70",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(current, "agent-authoritative");
    }

    /// `write_blocking` must count a closed channel too: otherwise finalize
    /// would report a clean flush after every awaited write was rejected.
    #[tokio::test]
    async fn blocking_write_counts_closed_channel() {
        let (tx, rx) = mpsc::channel::<PersistRecord>(1);
        drop(rx);
        let writer = PersistenceWriter {
            tx,
            lost_count: Arc::new(AtomicU64::new(0)),
            write_failures: Arc::new(AtomicU64::new(0)),
            closed_count: Arc::new(AtomicU64::new(0)),
        };

        assert!(
            !writer
                .write_blocking(PersistRecord::RunEnd {
                    run_id: "r1".to_string(),
                    timestamp_ns: 1,
                })
                .await
        );
        assert_eq!(writer.channel_closed_count(), 1);
    }

    /// A closed channel means the writer task is gone: nothing will drain the
    /// queue, so every write is lost. It must be counted separately from a
    /// full channel, otherwise finalize reports "flushed cleanly".
    #[test]
    fn closed_channel_writes_are_counted() {
        let (tx, rx) = mpsc::channel::<PersistRecord>(1);
        drop(rx);
        let writer = PersistenceWriter {
            tx,
            lost_count: Arc::new(AtomicU64::new(0)),
            write_failures: Arc::new(AtomicU64::new(0)),
            closed_count: Arc::new(AtomicU64::new(0)),
        };

        let queued = writer.write(PersistRecord::RunEnd {
            run_id: "r1".to_string(),
            timestamp_ns: 1,
        });

        assert!(!queued);
        assert_eq!(writer.channel_closed_count(), 1);
        assert_eq!(writer.lost_persist_count(), 0);
        assert_eq!(writer.write_failure_count(), 0);
    }
}
