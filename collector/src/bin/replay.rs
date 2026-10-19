// SPDX-License-Identifier: Apache-2.0 OR MIT
// Replay: re-run the authoritative pipeline over a recorded run's live
// responsibility database.
//
// The collector produces exactly one schema (`collector.responsibility.db`).
// This tool reads that schema and nothing else: the legacy causal artifact
// (`trace.log.causal.db`, `*.alerts.db`) is no longer produced, and a replay
// that silently fell back to it would be reporting on a file nobody writes.
//
// Exit codes: 0 on a completed replay (parity gap or not), 1 on failure,
// 2 on bad arguments.

use clap::Parser;
use mtagentlens::responsibility::persistence::PersistenceReader;
use mtagentlens::responsibility::replay::ReplayExecutor;
use mtagentlens::responsibility::types::alert_types::ResponsibilityAlert;
use mtagentlens::responsibility::types::evidence_types::CorrelationEvidence;
use serde_json::json;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

#[derive(Parser)]
#[command(
    about = "Re-run the authoritative pipeline over a recorded run's live responsibility database"
)]
struct Args {
    /// Run directory containing collector/collector.responsibility.db
    #[arg(long, conflicts_with = "sqlite")]
    run_dir: Option<String>,

    /// Path to a live responsibility database
    #[arg(long)]
    sqlite: Option<String>,
}

fn main() {
    // The persistence reader reports unknown enum values through `log`; without
    // a logger those warnings are discarded, which is exactly the silent
    // fallback the reader's comments promise not to be. Default to warnings on
    // stderr; RUST_LOG can raise the level.
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("warn")).init();
    let args = Args::parse();

    if let Some(run_dir) = &args.run_dir {
        match replay_run_dir(Path::new(run_dir)) {
            Ok(report) => print_report(&report),
            Err(err) => {
                eprintln!("replay error: {err}");
                std::process::exit(1);
            }
        }
        return;
    }

    if let Some(db_path) = &args.sqlite {
        match replay_database(Path::new(db_path), None) {
            Ok(report) => print_report(&report),
            Err(err) => {
                eprintln!("replay error: {err}");
                std::process::exit(1);
            }
        }
        return;
    }

    eprintln!("one of --run-dir or --sqlite is required");
    std::process::exit(2);
}

fn print_report(report: &RunDirReplayReport) {
    println!("run_dir={}", report.run_dir);
    println!("database={}", report.database);
    println!("mode={}", report.mode);
    println!("status={}", report.status);
    println!("evidence_persisted={}", report.evidence.persisted_count);
    println!("evidence_replayed={}", report.evidence.replayed_count);
    println!("evidence_matched={}", report.evidence.matched_count);
    println!("alerts_persisted={}", report.alerts.persisted_count);
    println!("alerts_replayed={}", report.alerts.replayed_count);
    println!("alerts_matched={}", report.alerts.matched_count);
    if !report.gap_sections.is_empty() {
        println!("gap_sections={}", report.gap_sections.join(","));
    }
    if !report.report_path.is_empty() {
        println!("report_path={}", report.report_path);
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Report
// ═══════════════════════════════════════════════════════════════════════════

#[derive(Debug, Clone, PartialEq, Eq)]
struct RunDirReplayReport {
    run_dir: String,
    database: String,
    mode: String,
    status: String,
    evidence: Comparison,
    alerts: Comparison,
    gap_sections: Vec<String>,
    report_path: String,
}

/// Set comparison by semantic key. Identifiers produced by replay are random
/// UUIDs, so comparing `evidence_id`/`alert_id` would always report a gap that
/// is not a behavioural difference.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
struct Comparison {
    persisted_count: usize,
    replayed_count: usize,
    matched_count: usize,
    missing_from_replay: Vec<String>,
    extra_in_replay: Vec<String>,
}

impl Comparison {
    /// Multiset comparison. Two identical alerts are two alerts: a per-key
    /// count difference is a parity gap, which a set comparison would hide.
    fn between(persisted: Vec<String>, replayed: Vec<String>) -> Self {
        fn counts(items: Vec<String>) -> BTreeMap<String, usize> {
            let mut map = BTreeMap::new();
            for item in items {
                *map.entry(item).or_insert(0) += 1;
            }
            map
        }
        let persisted_counts = counts(persisted);
        let replayed_counts = counts(replayed);
        let persisted_count: usize = persisted_counts.values().sum();
        let replayed_count: usize = replayed_counts.values().sum();
        let matched_count: usize = persisted_counts
            .iter()
            .map(|(key, count)| (*replayed_counts.get(key).unwrap_or(&0)).min(*count))
            .sum();
        let mut missing_from_replay = Vec::new();
        let mut extra_in_replay = Vec::new();
        for (key, count) in &persisted_counts {
            let replayed = *replayed_counts.get(key).unwrap_or(&0);
            if *count > replayed {
                missing_from_replay.push(format!("{key} (x{})", count - replayed));
            }
        }
        for (key, count) in &replayed_counts {
            let persisted = *persisted_counts.get(key).unwrap_or(&0);
            if *count > persisted {
                extra_in_replay.push(format!("{key} (x{})", count - persisted));
            }
        }
        Self {
            persisted_count,
            replayed_count,
            matched_count,
            missing_from_replay,
            extra_in_replay,
        }
    }

    fn has_gap(&self) -> bool {
        !self.missing_from_replay.is_empty() || !self.extra_in_replay.is_empty()
    }
}

fn evidence_key(evidence: &CorrelationEvidence) -> String {
    format!(
        "{}->{}@{}",
        evidence.intent_id.as_str(),
        evidence.action_id.as_str(),
        format!("{:?}", evidence.match_level)
    )
}

fn alert_key(alert: &ResponsibilityAlert) -> String {
    format!(
        "{:?}|{:?}|{}|{}",
        alert.decision, alert.severity, alert.primary_agent.agent_id, alert.title
    )
}

// ═══════════════════════════════════════════════════════════════════════════
// Replay
// ═══════════════════════════════════════════════════════════════════════════

fn replay_run_dir(run_dir: &Path) -> Result<RunDirReplayReport, String> {
    let database = resolve_live_db_path(run_dir).ok_or_else(|| {
        format!(
            "no live responsibility database in {} (expected collector/collector.responsibility.db)",
            run_dir.display()
        )
    })?;
    let report_path = run_dir.join("replay.report.json");
    replay_database(&database, Some(&report_path)).map(|mut report| {
        report.run_dir = run_dir.to_string_lossy().into_owned();
        report
    })
}

fn replay_database(
    database: &Path,
    report_path: Option<&Path>,
) -> Result<RunDirReplayReport, String> {
    let db_path = database.to_string_lossy();
    let reader = PersistenceReader::open(&db_path)
        .map_err(|err| format!("failed to open {}: {err}", database.display()))?;

    let run = reader
        .list_runs()
        .map_err(|err| format!("failed to list runs: {err}"))?
        .into_iter()
        .next()
        .ok_or_else(|| format!("no run row in {}", database.display()))?;

    let result = ReplayExecutor::reauthoritative(&reader, &run.run_id)
        .map_err(|err| format!("authoritative replay failed: {err}"))?;

    let persisted_evidence = reader
        .get_evidence(&run.run_id)
        .map_err(|err| format!("failed to load persisted correlation evidence: {err}"))?;
    let persisted_alerts = reader
        .get_alerts(&run.run_id)
        .map_err(|err| format!("failed to load persisted alerts: {err}"))?;

    let evidence = Comparison::between(
        persisted_evidence
            .iter()
            .map(evidence_key)
            .collect::<Vec<_>>(),
        result.evidence.iter().map(evidence_key).collect::<Vec<_>>(),
    );
    let alerts = Comparison::between(
        persisted_alerts.iter().map(alert_key).collect::<Vec<_>>(),
        result.alerts.iter().map(alert_key).collect::<Vec<_>>(),
    );

    let mut gap_sections = Vec::new();
    if evidence.has_gap() {
        gap_sections.push("causal_links_comparison".to_string());
    }
    if alerts.has_gap() {
        gap_sections.push("alerts_comparison".to_string());
    }
    let status = if gap_sections.is_empty() {
        "ok"
    } else {
        "parity_gap"
    };

    let mut report = RunDirReplayReport {
        run_dir: database.to_string_lossy().into_owned(),
        database: database.to_string_lossy().into_owned(),
        mode: "reauthoritative".to_string(),
        status: status.to_string(),
        evidence,
        alerts,
        gap_sections,
        report_path: String::new(),
    };

    if let Some(path) = report_path {
        let payload = json!({
            "run_dir": report.run_dir,
            "database": report.database,
            "mode": report.mode,
            "status": report.status,
            "gap_sections": report.gap_sections,
            "causal_links_comparison": comparison_json(&report.evidence),
            "alerts_comparison": comparison_json(&report.alerts),
        });
        std::fs::write(
            path,
            serde_json::to_vec_pretty(&payload).map_err(|err| err.to_string())?,
        )
        .map_err(|err| format!("failed to write {}: {err}", path.display()))?;
        report.report_path = path.to_string_lossy().into_owned();
    }

    Ok(report)
}

fn comparison_json(comparison: &Comparison) -> serde_json::Value {
    json!({
        "persisted_count": comparison.persisted_count,
        "replayed_count": comparison.replayed_count,
        "matched_count": comparison.matched_count,
        "missing_from_replay": comparison.missing_from_replay,
        "extra_in_replay": comparison.extra_in_replay,
    })
}

// ═══════════════════════════════════════════════════════════════════════════
// Artifact resolution
// ═══════════════════════════════════════════════════════════════════════════

fn resolve_artifact_from_summary(run_dir: &Path, key: &str) -> Option<PathBuf> {
    let bytes = std::fs::read(run_dir.join("summary.json")).ok()?;
    let summary: serde_json::Value = serde_json::from_slice(&bytes).ok()?;
    let path = summary.get("artifacts")?.get(key)?.as_str()?;
    let path = PathBuf::from(path);
    path.is_file().then_some(path)
}

fn resolve_live_db_path(run_dir: &Path) -> Option<PathBuf> {
    if let Some(path) = resolve_artifact_from_summary(run_dir, "responsibility_db") {
        return Some(path);
    }
    for candidate in [
        run_dir
            .join("collector")
            .join("collector.responsibility.db"),
        run_dir.join("collector.responsibility.db"),
    ] {
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    let mut candidates: Vec<PathBuf> = std::fs::read_dir(run_dir)
        .ok()?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.is_dir())
        .map(|dir| dir.join("collector.responsibility.db"))
        .filter(|path| path.is_file())
        .collect();
    candidates.sort();
    candidates.into_iter().next()
}

// ═══════════════════════════════════════════════════════════════════════════
// Tests
// ═══════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;
    use mtagentlens::responsibility::persistence::schema::open_responsibility_db;
    use rusqlite::params;

    fn seed_live_db(dir: &tempfile::TempDir) -> PathBuf {
        let run_dir = dir.path().join("run");
        std::fs::create_dir_all(run_dir.join("collector")).unwrap();
        let db_path = run_dir
            .join("collector")
            .join("collector.responsibility.db");
        let path = db_path.to_string_lossy().into_owned();
        let conn = open_responsibility_db(&path).unwrap();
        let run_id = "run-1";
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES (?1, 1000)",
            [run_id],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO process_events (pid, start_time_ns, parent_pid, parent_start_time_ns,
             comm, timestamp_ns, run_id) VALUES (42, 1000, 1, 0, 'claude', 1100, ?1)",
            [run_id],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO tls_sessions (tls_session_id, pid, start_time_ns, timestamp_ns, run_id)
             VALUES ('tls-1', 42, 1000, 1200, ?1)",
            [run_id],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO message_bindings (message_id, pid, start_time_ns, tls_session_id,
             binding_type, timestamp_ns, run_id)
             VALUES ('msg-1', 42, 1000, 'tls-1', 'TlsShared', 1300, ?1)",
            [run_id],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO intents (intent_id, pid, process_start_time_ns, message_id, tool_name,
             tool_args_json, intent_class, parse_state, primary_matching_eligible,
             authority_source, timestamp_ns, run_id)
             VALUES ('intent-1', 42, 1000, 'msg-1', 'Bash',
             '[{\"Command\":\"cat /etc/shadow\"}]', 'SideEffect', 'Complete', 1,
             'RequestHistoryAssistantToolUse', 1400, ?1)",
            [run_id],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO actions (action_id, pid, process_start_time_ns, action_type,
             normalized_args_json, derivation_mode, signal_class, timestamp_ns, run_id)
             VALUES ('action-1', 42, 1000, 'ProcessExec',
             '[{\"Command\":\"cat /etc/shadow\"}]', 'Direct', 'HighSignal', 1500, ?1)",
            [run_id],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO agent_identities (agent_id, process_ids_json, message_ids_json,
             tls_sessions_json, confidence, provisional_refs_json, run_id)
             VALUES ('agent-1', '[{\"pid\":42,\"start_time_ns\":1000}]', '[]', '[]',
             'Authoritative', '[]', ?1)",
            [run_id],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO identity_process_beliefs
             (agent_id, pid, start_time_ns, run_id, belief_level, observed_at_ns, source)
             VALUES ('agent-1', 42, 1000, ?1, 'Authoritative', 1600, 'test')",
            [run_id],
        )
        .unwrap();
        drop(conn);
        run_dir
    }

    #[test]
    fn replay_reads_the_live_database_and_writes_a_report() {
        let dir = tempfile::tempdir().unwrap();
        let run_dir = seed_live_db(&dir);

        let report = replay_run_dir(&run_dir).unwrap();

        assert_eq!(report.mode, "reauthoritative");
        assert!(
            report.evidence.replayed_count > 0,
            "the replayed pipeline must produce evidence from live tables: {report:?}"
        );
        assert!(report.report_path.ends_with("replay.report.json"));
        let payload: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&report.report_path).unwrap()).unwrap();
        assert_eq!(payload["mode"], "reauthoritative");
        assert!(payload.get("causal_links_comparison").is_some());
        assert!(payload.get("alerts_comparison").is_some());
        // Causal-only sections are gone by construction.
        assert!(payload.get("snapshot_comparison").is_none());
        assert!(payload.get("evidence_comparison").is_none());
        assert!(payload.get("violation_patterns_comparison").is_none());
    }

    #[test]
    fn replay_reports_parity_when_persisted_evidence_matches() {
        let dir = tempfile::tempdir().unwrap();
        let run_dir = seed_live_db(&dir);
        let db_path = run_dir
            .join("collector")
            .join("collector.responsibility.db")
            .to_string_lossy()
            .into_owned();

        // First replay tells us what the authoritative pipeline produces.
        let first = replay_database(Path::new(&db_path), None).unwrap();
        assert!(first.evidence.replayed_count > 0);

        // Persist exactly what was replayed, keyed by the same semantic key.
        let reader = PersistenceReader::open(&db_path).unwrap();
        let run = reader.list_runs().unwrap().into_iter().next().unwrap();
        let result = ReplayExecutor::reauthoritative(&reader, &run.run_id).unwrap();
        drop(reader);
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        for evidence in &result.evidence {
            conn.execute(
                "INSERT OR REPLACE INTO correlation_evidence
                 (evidence_id, intent_id, action_id, match_level, confidence,
                  matched_fields_json, phase, timestamp_ns, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, '{}', 'Provisional', ?6, ?7)",
                params![
                    evidence.evidence_id.as_str(),
                    evidence.intent_id.as_str(),
                    evidence.action_id.as_str(),
                    format!("{:?}", evidence.match_level),
                    evidence.confidence as f64,
                    evidence.timestamp_ns as i64,
                    run.run_id,
                ],
            )
            .unwrap();
        }
        for alert in &result.alerts {
            conn.execute(
                "INSERT OR REPLACE INTO responsibility_alerts
                 (alert_id, timestamp_ns, display_profile, decision, severity, confidence,
                  authority, reasoning, title, summary, primary_agent_id, involved_agents_json,
                  engine_section_json, timeline_json, graph_metrics_json, phase, run_id)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, '[]', '{}', '[]', '{}',
                  'Provisional', ?12)",
                params![
                    alert.alert_id.as_str(),
                    alert.timestamp_ns as i64,
                    format!("{:?}", alert.display_profile),
                    format!("{:?}", alert.decision),
                    format!("{:?}", alert.severity),
                    alert.confidence as f64,
                    format!("{:?}", alert.authority),
                    alert.reasoning,
                    alert.title,
                    alert.summary,
                    alert.primary_agent.agent_id.as_str(),
                    run.run_id,
                ],
            )
            .unwrap();
        }
        drop(conn);

        let report = replay_run_dir(&run_dir).unwrap();
        assert_eq!(report.status, "ok", "report: {report:?}");
        assert_eq!(report.evidence.missing_from_replay, Vec::<String>::new());
        assert_eq!(report.evidence.extra_in_replay, Vec::<String>::new());
    }

    #[test]
    fn comparison_counts_duplicate_keys() {
        let persisted = vec!["a".to_string(), "a".to_string()];
        let replayed = vec!["a".to_string()];
        let comparison = Comparison::between(persisted, replayed);
        assert_eq!(comparison.persisted_count, 2);
        assert_eq!(comparison.replayed_count, 1);
        assert_eq!(comparison.matched_count, 1);
        assert_eq!(comparison.missing_from_replay, vec!["a (x1)"]);
        assert!(comparison.has_gap());
    }

    #[test]
    fn replay_errors_without_a_live_database() {
        let dir = tempfile::tempdir().unwrap();
        let run_dir = dir.path().join("empty-run");
        std::fs::create_dir_all(&run_dir).unwrap();
        let err = replay_run_dir(&run_dir).unwrap_err();
        assert!(err.contains("no live responsibility database"), "{err}");
    }
}
