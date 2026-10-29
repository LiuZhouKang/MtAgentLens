// SPDX-License-Identifier: Apache-2.0 OR MIT
// Production persistence contract: PersistRecord -> current schema-v5 SQLite -> reader.
//
// Two runs deliberately reuse the same alert/claim/traceback ids. Schema v4
// scopes those primary keys by run_id, so the reader must still return one
// distinct payload per run instead of letting the second insert replace the
// first. The optional MTAGENTLENS_CONTRACT_DB export feeds the browser e2e
// test with the same fully flushed database; no source fixture is generated.

use mtagentlens::responsibility::persistence::{
    PersistRecord, PersistenceReader, PersistenceWriter, WriterConfig,
};
use mtagentlens::responsibility::types::{
    AgentId, AgentRef, AlertAuthority, AlertDecision, AlertId, AlertSeverity, BehaviorTimeline,
    BoundaryId, ClaimId, CompetingExplanation, DisplayProfile, EdgeId, EngineSection, EvidenceId,
    GraphSnapshot, NodeId, PhaseIndicator, PolicyId, ResponsibilityAlert, ResponsibilityClaim,
    ResponsibilityClaimState, ResponsibilityClaimType, ResponsibilityTraceback, ScoreBreakdown,
    TracebackId,
};
use rusqlite::{Connection, params};
use std::path::Path;

const SHARED_ALERT_ID: &str = "shared-alert";
const SHARED_CLAIM_ID: &str = "shared-claim";
const SHARED_TRACEBACK_ID: &str = "shared-traceback";
const SHARED_ROOT_NODE_ID: &str = "shared-root";

struct RunSpec {
    run_id: &'static str,
    tag: &'static str,
    timestamp_ns: u64,
}

const RUNS: [RunSpec; 2] = [
    RunSpec {
        run_id: "persistence-run-a",
        tag: "run-a",
        timestamp_ns: 1_000,
    },
    RunSpec {
        run_id: "persistence-run-b",
        tag: "run-b",
        timestamp_ns: 2_000,
    },
];

fn evidence_id(tag: &str) -> String {
    format!("evidence-{tag}")
}

fn claim(spec: &RunSpec) -> ResponsibilityClaim {
    ResponsibilityClaim {
        claim_id: ClaimId::from(SHARED_CLAIM_ID),
        claim_type: ResponsibilityClaimType::PolicyViolation,
        subject: AgentId::from(format!("agent-{}", spec.tag)),
        policy_id: PolicyId::from(format!("policy-{}", spec.tag)),
        boundary_id: BoundaryId::from(format!("boundary-{}", spec.tag)),
        claim_state: ResponsibilityClaimState::Confirmed,
        severity: AlertSeverity::Critical,
        confidence: 0.91,
        evidence_refs: vec![
            EvidenceId::from(evidence_id(spec.tag)),
            EvidenceId::from("shared-evidence"),
        ],
        competing_explanations: vec![CompetingExplanation {
            label: format!("{}-alternative", spec.tag),
            evidence_refs: vec![EvidenceId::from(format!("evidence-{}-alt", spec.tag))],
            rule_id: Some(format!("rule-{}", spec.tag)),
            rationale: format!("{} competing rationale", spec.tag),
        }],
        traceback_ref: Some(TracebackId::from(SHARED_TRACEBACK_ID)),
        phase: PhaseIndicator::Provisional {
            timestamp_ns: spec.timestamp_ns,
        },
    }
}

fn traceback(spec: &RunSpec) -> ResponsibilityTraceback {
    ResponsibilityTraceback {
        traceback_id: TracebackId::from(SHARED_TRACEBACK_ID),
        claim_id: ClaimId::from(SHARED_CLAIM_ID),
        chain_node_ids: vec![
            NodeId::from(format!("node-{}", spec.tag)),
            NodeId::from(SHARED_ROOT_NODE_ID),
        ],
        chain_edge_ids: vec![EdgeId::from(format!("edge-{}", spec.tag))],
        root_cause_node_id: NodeId::from(format!("node-{}", spec.tag)),
        explanation_summary: format!("traceback {}", spec.tag),
        confidence: 0.77,
    }
}

fn alert(spec: &RunSpec) -> ResponsibilityAlert {
    ResponsibilityAlert {
        alert_id: AlertId::from(SHARED_ALERT_ID),
        timestamp_ns: spec.timestamp_ns,
        display_profile: DisplayProfile::UnsafeByEngine,
        decision: AlertDecision::Unsafe,
        actionable: true,
        severity: AlertSeverity::Critical,
        confidence: 0.9,
        authority: AlertAuthority::Authoritative,
        reasoning: format!("reasoning for {}", spec.tag),
        title: format!("persistence alert {}", spec.tag),
        summary: format!("summary for {}", spec.tag),
        action_hint: Some(format!("review {}", spec.tag)),
        primary_agent: AgentRef {
            agent_id: AgentId::from(format!("agent-{}", spec.tag)),
            claimed_role: Some("assistant".into()),
        },
        involved_agents: vec![],
        engine: EngineSection {
            claim_ids: vec![SHARED_CLAIM_ID.to_string()],
            classification_score: 0.9,
            classification_route: "policy_violation".into(),
            score_breakdown: ScoreBreakdown::default(),
            score_model_version: "roundtrip".into(),
            ..EngineSection::default()
        },
        timeline: BehaviorTimeline::default(),
        graph_snapshot: GraphSnapshot {
            snapshot_id: format!("snapshot-{}", spec.tag),
            timestamp_ns: spec.timestamp_ns,
            ..GraphSnapshot::default()
        },
        phase: PhaseIndicator::Provisional {
            timestamp_ns: spec.timestamp_ns,
        },
        provisional_ref: None,
    }
}

async fn write_run(writer: &PersistenceWriter, spec: &RunSpec) {
    assert!(
        writer
            .write_blocking(PersistRecord::RunStart {
                run_id: spec.run_id.into(),
                timestamp_ns: spec.timestamp_ns,
            })
            .await
    );
    assert!(
        writer
            .write_blocking(PersistRecord::Claim(claim(spec)))
            .await
    );
    assert!(
        writer
            .write_blocking(PersistRecord::Traceback(traceback(spec)))
            .await
    );
    assert!(
        writer
            .write_blocking(PersistRecord::Alert(alert(spec)))
            .await
    );
}

fn export_flushed_db(source: &Path, destination: &Path) {
    let src = Connection::open(source).expect("open flushed source DB");
    let mut dst = Connection::open(destination).expect("open export destination DB");
    let backup = rusqlite::backup::Backup::new(&src, &mut dst).expect("start SQLite backup");
    backup
        .run_to_completion(64, std::time::Duration::from_millis(10), None)
        .expect("finish SQLite backup");
    drop(backup);
    // sql.js consumes a single main file; leave the export in rollback-journal
    // mode rather than copying WAL flags without a matching -wal sidecar.
    dst.execute_batch("PRAGMA journal_mode=DELETE;")
        .expect("normalize exported journal mode");
}

#[tokio::test]
async fn shared_ids_round_trip_per_run_through_real_writer_and_reader() {
    let dir = tempfile::tempdir().expect("tempdir");
    let db_path = dir.path().join("persistence_roundtrip.db");
    let writer = PersistenceWriter::new(WriterConfig {
        db_path: db_path.to_string_lossy().into_owned(),
        ..WriterConfig::default()
    })
    .expect("open writer");

    for spec in &RUNS {
        write_run(&writer, spec).await;
    }
    writer.flush().await.expect("flush writer");
    assert_eq!(writer.lost_persist_count(), 0);
    assert_eq!(writer.write_failure_count(), 0);
    assert_eq!(writer.channel_closed_count(), 0);

    let reader = PersistenceReader::open(&db_path.to_string_lossy()).expect("open reader");
    assert_eq!(reader.list_runs().expect("list runs").len(), RUNS.len());

    let conn = Connection::open(&db_path).expect("open DB for SQL contract checks");
    let schema_version: i64 = conn
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .expect("read schema version");
    assert_eq!(schema_version, 5);

    for spec in &RUNS {
        let alerts = reader
            .get_alerts(spec.run_id)
            .expect("reader alerts by run");
        assert_eq!(
            alerts.len(),
            1,
            "{} must own exactly one alert",
            spec.run_id
        );
        let alert = &alerts[0];
        assert_eq!(alert.alert_id.as_str(), SHARED_ALERT_ID);
        assert_eq!(alert.title, format!("persistence alert {}", spec.tag));
        assert_eq!(alert.summary, format!("summary for {}", spec.tag));
        assert_eq!(
            alert.engine.claim_ids,
            vec![SHARED_CLAIM_ID.to_string()],
            "engine.claim_ids must link the shared claim for {}",
            spec.run_id
        );
        assert_eq!(
            alert.graph_snapshot.snapshot_id,
            format!("snapshot-{}", spec.tag)
        );

        let (claim_type, subject, evidence_json, explanations_json, traceback_ref): (
            String,
            String,
            String,
            String,
            Option<String>,
        ) = conn
            .query_row(
                "SELECT claim_type, subject_agent_id, evidence_refs_json,
                        competing_explanations_json, traceback_ref
                 FROM responsibility_claims WHERE run_id = ?1 AND claim_id = ?2",
                params![spec.run_id, SHARED_CLAIM_ID],
                |row| {
                    Ok((
                        row.get(0)?,
                        row.get(1)?,
                        row.get(2)?,
                        row.get(3)?,
                        row.get(4)?,
                    ))
                },
            )
            .expect("claim row for run");
        assert_eq!(claim_type, "PolicyViolation");
        assert_eq!(subject, format!("agent-{}", spec.tag));
        assert_eq!(traceback_ref.as_deref(), Some(SHARED_TRACEBACK_ID));

        let evidence_refs: Vec<EvidenceId> =
            serde_json::from_str(&evidence_json).expect("parse evidence_refs_json");
        assert!(!evidence_refs.is_empty(), "evidence refs must survive SQL");
        assert!(
            evidence_refs
                .iter()
                .any(|id| id.as_str() == evidence_id(spec.tag)),
            "{} evidence refs must not cross runs: {evidence_refs:?}",
            spec.run_id
        );

        let explanations: Vec<CompetingExplanation> =
            serde_json::from_str(&explanations_json).expect("parse competing_explanations_json");
        assert_eq!(explanations.len(), 1);
        assert_eq!(explanations[0].label, format!("{}-alternative", spec.tag));
        assert_eq!(
            explanations[0].evidence_refs[0].as_str(),
            format!("evidence-{}-alt", spec.tag)
        );
        assert_eq!(
            explanations[0].rationale,
            format!("{} competing rationale", spec.tag)
        );

        let (nodes_json, edges_json, summary, root): (String, String, String, String) = conn
            .query_row(
                "SELECT chain_node_ids_json, chain_edge_ids_json, explanation_summary,
                        root_cause_node_id
                 FROM responsibility_tracebacks WHERE run_id = ?1 AND traceback_id = ?2",
                params![spec.run_id, SHARED_TRACEBACK_ID],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
            )
            .expect("traceback row for run");
        let chain_nodes: Vec<NodeId> =
            serde_json::from_str(&nodes_json).expect("parse chain_node_ids_json");
        assert_eq!(
            chain_nodes,
            vec![
                NodeId::from(format!("node-{}", spec.tag)),
                NodeId::from(SHARED_ROOT_NODE_ID)
            ]
        );
        let chain_edges: Vec<EdgeId> =
            serde_json::from_str(&edges_json).expect("parse chain_edge_ids_json");
        assert_eq!(
            chain_edges,
            vec![EdgeId::from(format!("edge-{}", spec.tag))]
        );
        assert_eq!(summary, format!("traceback {}", spec.tag));
        assert_eq!(root, format!("node-{}", spec.tag));

        for table in [
            "responsibility_alerts",
            "responsibility_claims",
            "responsibility_tracebacks",
        ] {
            let count: i64 = conn
                .query_row(
                    &format!("SELECT COUNT(*) FROM {table} WHERE run_id = ?1"),
                    params![spec.run_id],
                    |row| row.get(0),
                )
                .expect("count rows per run");
            assert_eq!(count, 1, "{table} must isolate {}", spec.run_id);
        }
    }

    let mut violations = conn
        .prepare("PRAGMA foreign_key_check")
        .expect("prepare foreign_key_check");
    assert!(
        !violations.exists([]).expect("run foreign_key_check"),
        "foreign_key_check must report no violations"
    );

    if let Some(export) = std::env::var_os("MTAGENTLENS_CONTRACT_DB") {
        export_flushed_db(&db_path, Path::new(&export));
    }
}
