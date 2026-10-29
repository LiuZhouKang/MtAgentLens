// SPDX-License-Identifier: Apache-2.0 OR MIT
// Replay: reanalyze the complete versioned frozen-evidence checkpoint in a
// recorded run's responsibility database. Missing inputs are an error.
//
// The collector produces exactly one schema (`collector.responsibility.db`).
// This tool reads that schema and nothing else: the legacy causal artifact
// (`trace.log.causal.db`, `*.alerts.db`) is no longer produced, and a replay
// that silently fell back to it would be reporting on a file nobody writes.
//
// Exit codes: 0 on a completed replay (parity gap or not), 1 on failure,
// 2 on bad arguments.

use clap::Parser;
use mtagentlens::responsibility::claims::attribution::{
    ATTRIBUTION_RESULTS_KIND, ATTRIBUTION_RESULTS_VERSION, AttributionResults,
};
use mtagentlens::responsibility::classification::final_decision::FinalVerdict;
use mtagentlens::responsibility::identity::{IdentityCondition, IdentityUnitKind, IdentityView};
use mtagentlens::responsibility::persistence::PersistenceReader;
use mtagentlens::responsibility::persistence::schema::{
    LATEST_SCHEMA_VERSION, open_responsibility_db_read_only,
};
use mtagentlens::responsibility::replay::ReplayExecutor;
use mtagentlens::responsibility::replay::ablation::{
    ABLATION_PROTOCOL, ABLATION_SCHEMA, AblationReviewCache, rebuild as rebuild_ablation,
    rebuild_with_reviews,
};
use mtagentlens::responsibility::replay::checkpoint::{
    AnalysisCheckpoint, CHECKPOINT_KIND, CHECKPOINT_VERSION, REPLAY_CONTRACT,
    identity_view_for_checkpoint, implementation_fingerprint, quality_status,
};
use mtagentlens::responsibility::replay::run_metrics::{
    RUN_METRICS_KIND, RUN_METRICS_VERSION, RunMetrics,
};
use mtagentlens::responsibility::types::alert_types::{
    ResponsibilityAlert, ResponsibilityClaim, ResponsibilityTraceback,
};
use mtagentlens::responsibility::types::evidence_types::CorrelationEvidence;
use mtagentlens::responsibility::types::{
    CompetingExplanation, EdgeId, EvidenceId, NodeId, PhaseIndicator,
};
use rusqlite::params;
use serde::Serialize;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::OpenOptions;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

#[derive(Debug, Parser)]
#[command(about = "Reanalyze a recorded run's complete versioned frozen-evidence checkpoint")]
struct Args {
    /// Run directory containing collector/collector.responsibility.db
    #[arg(long, conflicts_with = "sqlite")]
    run_dir: Option<String>,

    /// Path to a live responsibility database
    #[arg(long)]
    sqlite: Option<String>,

    /// Write the model-free identity experiment JSON to a new file
    #[arg(long, conflicts_with = "ablation_output")]
    identity_output: Option<String>,

    /// Write the model-free typed attribution JSON to a new file
    #[arg(long, conflicts_with_all = ["identity_output", "ablation_output"])]
    attribution_output: Option<String>,

    /// Write the raw-fact L0-L3 ablation reconstruction JSON to a new file
    #[arg(long, conflicts_with_all = ["identity_output", "attribution_output"])]
    ablation_output: Option<String>,

    /// Read-only typed L2/L3 review cache for the ablation export
    #[arg(
        long,
        requires = "ablation_output",
        conflicts_with_all = ["identity_output", "attribution_output"]
    )]
    ablation_review_cache: Option<String>,

    /// Produce a typed L2/L3 review cache through the configured Observer
    #[arg(
        long,
        requires = "execute",
        conflicts_with_all = [
            "identity_output",
            "attribution_output",
            "ablation_output",
            "ablation_review_cache",
            "validate_recording"
        ]
    )]
    ablation_review_live: Option<String>,

    /// Required paid-execution acknowledgment for --ablation-review-live
    #[arg(long)]
    execute: bool,

    /// Print a strict read-only recording validation report and exit 1 on any failure
    #[arg(
        long,
        conflicts_with_all = ["identity_output", "attribution_output", "ablation_output"]
    )]
    validate_recording: bool,
}

fn main() {
    // The persistence reader reports unknown enum values through `log`; without
    // a logger those warnings are discarded, which is exactly the silent
    // fallback the reader's comments promise not to be. Default to warnings on
    // stderr; RUST_LOG can raise the level.
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("warn")).init();
    let args = Args::parse();

    if args.validate_recording {
        let report = validate_recording_from_args(&args);
        match serde_json::to_string_pretty(&report) {
            Ok(payload) => println!("{payload}"),
            Err(err) => {
                eprintln!("recording validation report error: {err}");
                std::process::exit(1);
            }
        }
        if !report.is_ok() {
            std::process::exit(1);
        }
        return;
    }

    if let Some(output) = &args.identity_output {
        let database = resolve_export_database(&args);
        match write_identity_experiment(&database, Path::new(output)) {
            Ok(payload) => {
                println!("identity_output={output}");
                println!("schema={}", payload["schema"].as_str().unwrap_or_default());
                let runs = payload["runs"].as_array().map_or(0, Vec::len);
                println!("runs={runs}");
            }
            Err(err) => {
                eprintln!("identity experiment error: {err}");
                std::process::exit(1);
            }
        }
        return;
    }

    if let Some(output) = &args.attribution_output {
        let database = resolve_export_database(&args);
        match write_attribution_experiment(&database, Path::new(output)) {
            Ok(payload) => {
                println!("attribution_output={output}");
                println!("schema={}", payload["schema"].as_str().unwrap_or_default());
                let runs = payload["runs"].as_array().map_or(0, Vec::len);
                println!("runs={runs}");
            }
            Err(err) => {
                eprintln!("attribution experiment error: {err}");
                std::process::exit(1);
            }
        }
        return;
    }

    if let Some(output) = &args.ablation_review_live {
        let paid = std::env::var("MTL_ALLOW_PAID").ok();
        if let Err(err) = preflight_ablation_review_live(args.execute, paid.as_deref()) {
            eprintln!("ablation review live error: {err}");
            std::process::exit(1);
        }
        let database = resolve_export_database(&args);
        let provider = match load_live_observer_provider() {
            Ok(provider) => provider,
            Err(err) => {
                eprintln!("ablation review live error: {err}");
                std::process::exit(1);
            }
        };
        let runtime = match tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
        {
            Ok(runtime) => runtime,
            Err(err) => {
                eprintln!("ablation review live error: failed to start runtime: {err}");
                std::process::exit(1);
            }
        };
        match runtime.block_on(write_ablation_review_live(
            &database,
            Path::new(output),
            &provider,
        )) {
            Ok(cache) => {
                println!("ablation_review_live={output}");
                let layers = cache["layers"].as_array().map_or(0, Vec::len);
                println!("layers={layers}");
            }
            Err(err) => {
                eprintln!("ablation review live error: {err}");
                std::process::exit(1);
            }
        }
        return;
    }

    if let Some(output) = &args.ablation_output {
        let database = resolve_export_database(&args);
        match write_ablation_experiment(
            &database,
            Path::new(output),
            args.ablation_review_cache.as_deref().map(Path::new),
        ) {
            Ok(payload) => {
                println!("ablation_output={output}");
                println!("schema={}", payload["schema"].as_str().unwrap_or_default());
                let runs = payload["runs"].as_array().map_or(0, Vec::len);
                println!("runs={runs}");
            }
            Err(err) => {
                eprintln!("ablation experiment error: {err}");
                std::process::exit(1);
            }
        }
        return;
    }

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
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize)]
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
        .get_authoritative_evidence(&run.run_id)
        .map_err(|err| format!("failed to load persisted authoritative evidence: {err}"))?;
    let persisted_alerts = reader
        .get_authoritative_alerts(&run.run_id)
        .map_err(|err| format!("failed to load persisted authoritative alerts: {err}"))?;

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
// Strict recording validation
// ═══════════════════════════════════════════════════════════════════════════

const RECORDING_VALIDATION_SCHEMA: &str = "mtagentlens.recording-validation/v1";
const RECORDING_VALIDATION_SCOPE: &str = "offline-quiescent structural and replay integrity only; checkpoint-time persist counters do not prove later writes or a final flush; not scientific admission, a final packager, efficacy, or reference proof";

#[derive(Debug, Clone, Serialize)]
struct RecordingValidationReport {
    schema: &'static str,
    status: &'static str,
    read_only: bool,
    scope: &'static str,
    capture_quality: BTreeMap<String, String>,
    primary_eligible: bool,
    reference_status: &'static str,
    runs: Vec<RecordingValidationRun>,
    failures: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct RecordingValidationRun {
    run_id: String,
    status: &'static str,
    capture_quality: Option<String>,
    evidence: Option<Comparison>,
    alerts: Option<Comparison>,
    failures: Vec<String>,
}

impl RecordingValidationReport {
    fn new() -> Self {
        Self {
            schema: RECORDING_VALIDATION_SCHEMA,
            status: "ok",
            read_only: true,
            scope: RECORDING_VALIDATION_SCOPE,
            capture_quality: BTreeMap::new(),
            primary_eligible: false,
            reference_status: "unreviewed",
            runs: Vec::new(),
            failures: Vec::new(),
        }
    }

    fn fail(&mut self, failure: String) {
        self.failures.push(failure);
        self.status = "invalid";
    }

    fn is_ok(&self) -> bool {
        self.status == "ok" && self.failures.is_empty()
    }
}

impl RecordingValidationRun {
    fn new(run_id: &str) -> Self {
        Self {
            run_id: run_id.to_string(),
            status: "ok",
            capture_quality: None,
            evidence: None,
            alerts: None,
            failures: Vec::new(),
        }
    }

    fn fail(&mut self, failure: String) {
        self.failures.push(failure);
        self.status = "invalid";
    }
}

fn validate_recording_from_args(args: &Args) -> RecordingValidationReport {
    let database = match (&args.run_dir, &args.sqlite) {
        (Some(run_dir), None) => resolve_live_db_path(Path::new(run_dir)),
        (None, Some(db_path)) => Some(PathBuf::from(db_path)),
        _ => None,
    };
    match database {
        Some(path) => validate_recording(&path),
        None => {
            let mut report = RecordingValidationReport::new();
            report.fail("one of --run-dir or --sqlite is required".into());
            report
        }
    }
}

/// Read-only release validation for one quiescent recording database.
///
/// This proves structural/replay integrity only. It says nothing about
/// detection efficacy, an independent execution reference, or admission to the
/// primary experiment; the report states both limits explicitly.
fn validate_recording(database: &Path) -> RecordingValidationReport {
    let mut report = RecordingValidationReport::new();
    let path = database.to_string_lossy();
    let conn = match open_responsibility_db_read_only(&path) {
        Ok(conn) => conn,
        Err(err) => {
            report.fail(format!(
                "failed to open {} read-only: {err}",
                database.display()
            ));
            return report;
        }
    };

    let mut current_schema = false;
    match schema_versions(&conn) {
        Ok((Some(user_version), Some(recorded_version)))
            if user_version == LATEST_SCHEMA_VERSION
                && recorded_version == LATEST_SCHEMA_VERSION =>
        {
            current_schema = true;
        }
        Ok((user_version, recorded_version)) => report.fail(format!(
            "schema version mismatch: user_version={user_version:?} schema_version={recorded_version:?}, both must be current {LATEST_SCHEMA_VERSION}"
        )),
        Err(err) => report.fail(format!("failed to read schema version: {err}")),
    }

    match conn.query_row("PRAGMA integrity_check", [], |row| row.get::<_, String>(0)) {
        Ok(result) if result == "ok" => {}
        Ok(result) => report.fail(format!("SQLite integrity_check failed: {result}")),
        Err(err) => report.fail(format!("SQLite integrity_check failed: {err}")),
    }

    match foreign_key_violation_count(&conn) {
        Ok(0) => {}
        Ok(count) => report.fail(format!(
            "SQLite foreign_key_check reported {count} violating row(s)"
        )),
        Err(err) => report.fail(format!("SQLite foreign_key_check failed: {err}")),
    }

    if !current_schema {
        return report;
    }

    let reader = match PersistenceReader::open(&path) {
        Ok(reader) => reader,
        Err(err) => {
            report.fail(format!(
                "failed to open {} for validation: {err}",
                database.display()
            ));
            return report;
        }
    };
    let runs = match reader.list_runs() {
        Ok(runs) => runs,
        Err(err) => {
            report.fail(format!("failed to list runs: {err}"));
            return report;
        }
    };
    if runs.is_empty() {
        report.fail(format!("no run row in {}", database.display()));
        return report;
    }

    for run in &runs {
        let mut run_report = RecordingValidationRun::new(&run.run_id);
        validate_run_recording(&conn, &reader, run, &mut run_report);
        if let Some(quality) = &run_report.capture_quality {
            report
                .capture_quality
                .insert(run.run_id.clone(), quality.clone());
        }
        if run_report.status != "ok" {
            report.status = "invalid";
        }
        report.runs.push(run_report);
    }
    report
}

/// Read the two schema headers independently. A newer `schema_version` row
/// must not hide an older `user_version` header (or vice versa): both are part
/// of the recorded identity and both must equal the current version.
fn schema_versions(conn: &rusqlite::Connection) -> rusqlite::Result<(Option<i64>, Option<i64>)> {
    use rusqlite::OptionalExtension;
    let user_version: Option<i64> = conn
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .optional()?;
    let table_exists: Option<i64> = conn
        .query_row(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    let recorded_version = match table_exists {
        Some(_) => Some(conn.query_row(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version",
            [],
            |row| row.get(0),
        )?),
        None => None,
    };
    Ok((user_version, recorded_version))
}

fn foreign_key_violation_count(conn: &rusqlite::Connection) -> rusqlite::Result<usize> {
    let mut statement = conn.prepare("PRAGMA foreign_key_check")?;
    let mut rows = statement.query([])?;
    let mut count = 0;
    while rows.next()?.is_some() {
        count += 1;
    }
    Ok(count)
}

fn validate_run_recording(
    conn: &rusqlite::Connection,
    reader: &PersistenceReader,
    run: &mtagentlens::responsibility::types::replay_types::RunRecord,
    report: &mut RecordingValidationRun,
) {
    match run.ended_at_ns {
        None => report.fail(format!("run {} ended_at_ns is missing", run.run_id)),
        // The legacy reader casts SQLite i64 to u64. A negative stored time
        // therefore looks far in the future unless rejected here.
        Some(ended) if ended > i64::MAX as u64 => report.fail(format!(
            "run {} ended_at_ns is not a non-negative SQLite timestamp",
            run.run_id
        )),
        Some(ended) if ended < run.started_at_ns => report.fail(format!(
            "run {} ended_at_ns {ended} is before started_at_ns {}",
            run.run_id, run.started_at_ns
        )),
        Some(_) => {}
    }

    let current = implementation_fingerprint();
    let checkpoint = match load_current_checkpoint(reader, &run.run_id, &current) {
        Ok(checkpoint) => checkpoint,
        Err(err) => {
            report.fail(err);
            return;
        }
    };
    let mut inputs_valid = true;
    if let Err(err) = checkpoint.validate(&run.run_id) {
        report.fail(format!("run {} checkpoint is invalid: {err}", run.run_id));
        inputs_valid = false;
    }
    if checkpoint.identity_input.is_none() {
        report.fail(format!(
            "run {} checkpoint lacks the frozen PRE-resolver identity input",
            run.run_id
        ));
        inputs_valid = false;
    }
    if checkpoint.identity_policy.is_none() {
        report.fail(format!(
            "run {} checkpoint lacks the frozen identity boundary policy",
            run.run_id
        ));
        inputs_valid = false;
    }
    if checkpoint.captured_request_contexts.is_none() {
        report.fail(format!(
            "run {} checkpoint lacks the frozen captured request contexts",
            run.run_id
        ));
        inputs_valid = false;
    }
    if let Err(err) = identity_view_for_checkpoint(&checkpoint, IdentityCondition::Full) {
        report.fail(format!(
            "run {} Full identity view failed: {err}",
            run.run_id
        ));
        inputs_valid = false;
    }
    report.capture_quality = Some(quality_status(&checkpoint.capture_quality).to_string());
    // These counters are frozen before evaluation. Zero proves only that the
    // checkpoint recorded no observed loss up to this boundary; it cannot prove
    // a later write succeeded or that a final flush receipt exists.
    if let Err(err) = validate_persist_health(&checkpoint.capture_quality) {
        report.fail(format!("run {} {err}", run.run_id));
        inputs_valid = false;
    }
    if !inputs_valid {
        return;
    }

    let evaluation = checkpoint.evaluate();
    if let Err(err) = validate_canonical_verdict(conn, &run.run_id, &evaluation.verdict) {
        report.fail(err);
    }
    if let Err(err) = validate_persisted_claims_and_tracebacks(
        conn,
        &run.run_id,
        &evaluation.claims,
        &evaluation.tracebacks,
    ) {
        report.fail(err);
    }
    let authoritative_claim_ids: BTreeSet<String> = evaluation
        .claims
        .iter()
        .filter(|claim| matches!(claim.phase, PhaseIndicator::Authoritative { .. }))
        .map(|claim| claim.claim_id.as_str().to_string())
        .collect();
    if let Err(err) = validate_stored_attribution(
        reader,
        &run.run_id,
        &evaluation.attributions,
        &authoritative_claim_ids,
    ) {
        report.fail(err);
    }
    if let Err(err) = validate_stored_run_metrics(reader, &run.run_id, &evaluation.run_metrics) {
        report.fail(err);
    }

    let replay = match ReplayExecutor::reauthoritative(reader, &run.run_id) {
        Ok(replay) => replay,
        Err(err) => {
            report.fail(format!(
                "run {} authoritative replay failed: {err}",
                run.run_id
            ));
            return;
        }
    };
    if filter_attribution_claims(&replay.attributions, &authoritative_claim_ids)
        != filter_attribution_claims(&evaluation.attributions, &authoritative_claim_ids)
    {
        report.fail(format!(
            "run {} ReplayExecutor attributions differ from checkpoint evaluation",
            run.run_id
        ));
    }
    if replay.run_metrics != evaluation.run_metrics {
        report.fail(format!(
            "run {} ReplayExecutor run_metrics differ from checkpoint evaluation",
            run.run_id
        ));
    }
    if let Err(err) =
        claim_traceback_closure(&replay.claims, &replay.tracebacks, &replay.attributions)
    {
        report.fail(format!("run {} {err}", run.run_id));
    }

    let persisted_evidence = match reader.get_authoritative_evidence(&run.run_id) {
        Ok(evidence) => evidence,
        Err(err) => {
            report.fail(format!(
                "run {} failed to load persisted authoritative evidence: {err}",
                run.run_id
            ));
            return;
        }
    };
    let persisted_alerts = match reader.get_authoritative_alerts(&run.run_id) {
        Ok(alerts) => alerts,
        Err(err) => {
            report.fail(format!(
                "run {} failed to load persisted authoritative alerts: {err}",
                run.run_id
            ));
            return;
        }
    };
    let evidence = Comparison::between(
        persisted_evidence
            .iter()
            .map(evidence_key)
            .collect::<Vec<_>>(),
        replay.evidence.iter().map(evidence_key).collect::<Vec<_>>(),
    );
    let alerts = Comparison::between(
        persisted_alerts.iter().map(alert_key).collect::<Vec<_>>(),
        replay.alerts.iter().map(alert_key).collect::<Vec<_>>(),
    );
    if evidence.has_gap() {
        report.fail(format!(
            "run {} evidence parity gap: missing_from_replay={:?} extra_in_replay={:?}",
            run.run_id, evidence.missing_from_replay, evidence.extra_in_replay
        ));
    }
    if alerts.has_gap() {
        report.fail(format!(
            "run {} alert parity gap: missing_from_replay={:?} extra_in_replay={:?}",
            run.run_id, alerts.missing_from_replay, alerts.extra_in_replay
        ));
    }
    report.evidence = Some(evidence);
    report.alerts = Some(alerts);
}

fn validate_persist_health(quality: &serde_json::Value) -> Result<(), String> {
    let analysis = quality.get("analysis").unwrap_or(&serde_json::Value::Null);
    for key in ["persist_dropped", "persist_closed", "persist_failures"] {
        match analysis.get(key).and_then(serde_json::Value::as_u64) {
            Some(0) => {}
            Some(count) => {
                return Err(format!(
                    "capture_quality.analysis.{key} is {count}, not zero"
                ));
            }
            None => {
                return Err(format!(
                    "capture_quality.analysis.{key} is missing or not an unsigned integer"
                ));
            }
        }
    }
    match analysis
        .get("persist_flush_failed")
        .and_then(serde_json::Value::as_bool)
    {
        Some(false) => Ok(()),
        Some(true) => Err("capture_quality.analysis.persist_flush_failed is true".into()),
        None => {
            Err("capture_quality.analysis.persist_flush_failed is missing or not a boolean".into())
        }
    }
}

/// Compare the run's canonical verdict columns to the checkpoint evaluation.
/// Missing columns are invalid; metrics are never used to fill them in.
fn validate_canonical_verdict(
    conn: &rusqlite::Connection,
    run_id: &str,
    expected: &FinalVerdict,
) -> Result<(), String> {
    use rusqlite::OptionalExtension;

    let row: Option<(
        Option<String>,
        Option<String>,
        Option<String>,
        Option<String>,
    )> = conn
        .query_row(
            "SELECT final_decision, final_severity, final_authority,
                    decision_provenance_json
             FROM runs WHERE run_id=?1",
            params![run_id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .optional()
        .map_err(|err| format!("run {run_id} failed to read canonical verdict: {err}"))?;
    let (decision, severity, authority, provenance) =
        row.ok_or_else(|| format!("run {run_id} canonical verdict row is missing"))?;

    let expected_decision = format!("{:?}", expected.decision);
    match decision.as_deref() {
        Some(value) if value == expected_decision => {}
        Some(value) => {
            return Err(format!(
                "run {run_id} canonical verdict final_decision {value:?} is not {expected_decision:?}"
            ));
        }
        None => {
            return Err(format!(
                "run {run_id} canonical verdict final_decision is missing"
            ));
        }
    }
    let expected_severity = format!("{:?}", expected.severity);
    match severity.as_deref() {
        Some(value) if value == expected_severity => {}
        Some(value) => {
            return Err(format!(
                "run {run_id} canonical verdict final_severity {value:?} is not {expected_severity:?}"
            ));
        }
        None => {
            return Err(format!(
                "run {run_id} canonical verdict final_severity is missing"
            ));
        }
    }
    match authority.as_deref() {
        Some(value) if value == expected.authority.as_str() => {}
        Some(value) => {
            return Err(format!(
                "run {run_id} canonical verdict final_authority {value:?} is not {:?}",
                expected.authority.as_str()
            ));
        }
        None => {
            return Err(format!(
                "run {run_id} canonical verdict final_authority is missing"
            ));
        }
    }
    let raw_provenance = provenance
        .ok_or_else(|| format!("run {run_id} canonical verdict decision_provenance is missing"))?;
    let persisted_provenance: Vec<String> =
        serde_json::from_str(&raw_provenance).map_err(|err| {
            format!("run {run_id} canonical verdict decision_provenance is invalid JSON: {err}")
        })?;
    if persisted_provenance != expected.provenance {
        return Err(format!(
            "run {run_id} canonical verdict decision_provenance differs from evaluation"
        ));
    }
    Ok(())
}

struct PersistedClaimRow {
    claim_type: String,
    subject: String,
    policy: String,
    boundary: String,
    state: String,
    severity: String,
    confidence: f64,
    evidence_refs: Vec<EvidenceId>,
    competing_explanations: serde_json::Value,
    traceback_ref: Option<String>,
    phase: String,
}

struct PersistedTracebackRow {
    claim_id: String,
    chain_node_ids: Vec<NodeId>,
    chain_edge_ids: Vec<EdgeId>,
    root_cause_node_id: String,
    explanation_summary: String,
    confidence: f64,
}

fn stored_f32_matches(stored: f64, expected: f32) -> bool {
    (stored as f32).to_bits() == expected.to_bits()
}

/// Compare every meaningful persisted authoritative claim and its traceback to
/// the exact evaluation values. Provisional claim/traceback history may stay;
/// only rows belonging to authoritative claims are part of this contract.
fn validate_persisted_claims_and_tracebacks(
    conn: &rusqlite::Connection,
    run_id: &str,
    claims: &[ResponsibilityClaim],
    tracebacks: &[ResponsibilityTraceback],
) -> Result<(), String> {
    use std::collections::BTreeSet;

    let unexpected_phases: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM responsibility_claims
             WHERE run_id=?1 AND phase NOT LIKE 'Authoritative%'
               AND phase NOT LIKE 'Provisional%'",
            params![run_id],
            |row| row.get(0),
        )
        .map_err(|err| format!("run {run_id} failed to classify persisted claim phases: {err}"))?;
    if unexpected_phases > 0 {
        return Err(format!(
            "run {run_id} has {unexpected_phases} persisted claim(s) whose phase is neither Authoritative nor Provisional"
        ));
    }

    let mut claim_statement = conn
        .prepare(
            "SELECT claim_id, claim_type, subject_agent_id, policy_id, boundary_id,
                    claim_state, severity, confidence, evidence_refs_json,
                    competing_explanations_json, traceback_ref, phase
             FROM responsibility_claims
             WHERE run_id=?1 AND phase LIKE 'Authoritative%'",
        )
        .map_err(|err| format!("run {run_id} failed to prepare persisted claim query: {err}"))?;
    let raw_claims = claim_statement
        .query_map(params![run_id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, String>(5)?,
                row.get::<_, String>(6)?,
                row.get::<_, f64>(7)?,
                row.get::<_, String>(8)?,
                row.get::<_, String>(9)?,
                row.get::<_, Option<String>>(10)?,
                row.get::<_, String>(11)?,
            ))
        })
        .map_err(|err| format!("run {run_id} failed to query persisted claims: {err}"))?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(|err| format!("run {run_id} failed to read persisted claims: {err}"))?;

    let mut persisted_claims = BTreeMap::new();
    for (
        claim_id,
        claim_type,
        subject,
        policy,
        boundary,
        state,
        severity,
        confidence,
        evidence_refs_json,
        competing_json,
        traceback_ref,
        phase,
    ) in raw_claims
    {
        let evidence_refs: Vec<EvidenceId> =
            serde_json::from_str(&evidence_refs_json).map_err(|err| {
                format!(
                    "run {run_id} persisted authoritative claim {claim_id} evidence_refs_json is invalid: {err}"
                )
            })?;
        let competing: Vec<CompetingExplanation> =
            serde_json::from_str(&competing_json).map_err(|err| {
                format!(
                    "run {run_id} persisted authoritative claim {claim_id} competing_explanations_json is invalid: {err}"
                )
            })?;
        let competing_explanations = serde_json::to_value(competing).map_err(|err| {
            format!(
                "run {run_id} persisted authoritative claim {claim_id} competing_explanations cannot be compared: {err}"
            )
        })?;
        persisted_claims.insert(
            claim_id,
            PersistedClaimRow {
                claim_type,
                subject,
                policy,
                boundary,
                state,
                severity,
                confidence,
                evidence_refs,
                competing_explanations,
                traceback_ref,
                phase,
            },
        );
    }

    let expected_claims: BTreeMap<&str, &ResponsibilityClaim> = claims
        .iter()
        .filter(|claim| matches!(claim.phase, PhaseIndicator::Authoritative { .. }))
        .map(|claim| (claim.claim_id.as_str(), claim))
        .collect();
    if persisted_claims
        .keys()
        .map(String::as_str)
        .ne(expected_claims.keys().copied())
    {
        let missing: Vec<&str> = expected_claims
            .keys()
            .filter(|claim_id| !persisted_claims.contains_key(**claim_id))
            .copied()
            .collect();
        let extra: Vec<&str> = persisted_claims
            .keys()
            .filter(|claim_id| !expected_claims.contains_key(claim_id.as_str()))
            .map(String::as_str)
            .collect();
        return Err(format!(
            "run {run_id} persisted authoritative claim set differs from evaluation: missing={missing:?} extra={extra:?}"
        ));
    }
    for (claim_id, claim) in &expected_claims {
        let stored = &persisted_claims[*claim_id];
        let mut mismatches = Vec::new();
        if stored.claim_type != format!("{:?}", claim.claim_type) {
            mismatches.push("claim_type");
        }
        if stored.subject != claim.subject.as_str() {
            mismatches.push("subject_agent_id");
        }
        if stored.policy != claim.policy_id.as_str() {
            mismatches.push("policy_id");
        }
        if stored.boundary != claim.boundary_id.as_str() {
            mismatches.push("boundary_id");
        }
        if stored.state != format!("{:?}", claim.claim_state) {
            mismatches.push("claim_state");
        }
        if stored.severity != format!("{:?}", claim.severity) {
            mismatches.push("severity");
        }
        if !stored_f32_matches(stored.confidence, claim.confidence) {
            mismatches.push("confidence");
        }
        if stored.evidence_refs != claim.evidence_refs {
            mismatches.push("evidence_refs");
        }
        let expected_competing =
            serde_json::to_value(&claim.competing_explanations).map_err(|err| {
                format!(
                    "run {run_id} expected authoritative claim {claim_id} competing_explanations cannot be compared: {err}"
                )
            })?;
        if stored.competing_explanations != expected_competing {
            mismatches.push("competing_explanations");
        }
        let expected_traceback_ref = claim
            .traceback_ref
            .as_ref()
            .map(|traceback| traceback.as_str());
        if stored.traceback_ref.as_deref() != expected_traceback_ref {
            mismatches.push("traceback_ref");
        }
        if stored.phase != format!("{:?}", claim.phase) {
            mismatches.push("phase");
        }
        if !mismatches.is_empty() {
            return Err(format!(
                "run {run_id} persisted authoritative claim {claim_id} differs from evaluation fields: {}",
                mismatches.join(",")
            ));
        }
    }

    let authoritative_claim_ids: BTreeSet<&str> = expected_claims.keys().copied().collect();
    let expected_tracebacks: BTreeMap<&str, &ResponsibilityTraceback> = tracebacks
        .iter()
        .filter(|traceback| authoritative_claim_ids.contains(traceback.claim_id.as_str()))
        .map(|traceback| (traceback.traceback_id.as_str(), traceback))
        .collect();
    let mut traceback_statement = conn
        .prepare(
            "SELECT traceback_id, claim_id, chain_node_ids_json, chain_edge_ids_json,
                    root_cause_node_id, explanation_summary, confidence
             FROM responsibility_tracebacks WHERE run_id=?1",
        )
        .map_err(|err| {
            format!("run {run_id} failed to prepare persisted traceback query: {err}")
        })?;
    let raw_tracebacks = traceback_statement
        .query_map(params![run_id], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, String>(4)?,
                row.get::<_, String>(5)?,
                row.get::<_, f64>(6)?,
            ))
        })
        .map_err(|err| format!("run {run_id} failed to query persisted tracebacks: {err}"))?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(|err| format!("run {run_id} failed to read persisted tracebacks: {err}"))?;

    let mut persisted_tracebacks = BTreeMap::new();
    for (
        traceback_id,
        claim_id,
        chain_nodes_json,
        chain_edges_json,
        root_cause_node_id,
        explanation_summary,
        confidence,
    ) in raw_tracebacks
    {
        // Tracebacks attached to provisional claims are retained history.
        if !authoritative_claim_ids.contains(claim_id.as_str()) {
            continue;
        }
        let chain_node_ids: Vec<NodeId> =
            serde_json::from_str(&chain_nodes_json).map_err(|err| {
                format!(
                    "run {run_id} persisted traceback {traceback_id} chain_node_ids_json is invalid: {err}"
                )
            })?;
        let chain_edge_ids: Vec<EdgeId> =
            serde_json::from_str(&chain_edges_json).map_err(|err| {
                format!(
                    "run {run_id} persisted traceback {traceback_id} chain_edge_ids_json is invalid: {err}"
                )
            })?;
        persisted_tracebacks.insert(
            traceback_id,
            PersistedTracebackRow {
                claim_id,
                chain_node_ids,
                chain_edge_ids,
                root_cause_node_id,
                explanation_summary,
                confidence,
            },
        );
    }
    if persisted_tracebacks
        .keys()
        .map(String::as_str)
        .ne(expected_tracebacks.keys().copied())
    {
        let missing: Vec<&str> = expected_tracebacks
            .keys()
            .filter(|traceback_id| !persisted_tracebacks.contains_key(**traceback_id))
            .copied()
            .collect();
        let extra: Vec<&str> = persisted_tracebacks
            .keys()
            .filter(|traceback_id| !expected_tracebacks.contains_key(traceback_id.as_str()))
            .map(String::as_str)
            .collect();
        return Err(format!(
            "run {run_id} persisted authoritative traceback set differs from evaluation: missing={missing:?} extra={extra:?}"
        ));
    }
    for (traceback_id, traceback) in &expected_tracebacks {
        let stored = &persisted_tracebacks[*traceback_id];
        let mut mismatches = Vec::new();
        if stored.claim_id != traceback.claim_id.as_str() {
            mismatches.push("claim_id");
        }
        if stored.chain_node_ids != traceback.chain_node_ids {
            mismatches.push("chain_node_ids");
        }
        if stored.chain_edge_ids != traceback.chain_edge_ids {
            mismatches.push("chain_edge_ids");
        }
        if stored.root_cause_node_id != traceback.root_cause_node_id.as_str() {
            mismatches.push("root_cause_node_id");
        }
        if stored.explanation_summary != traceback.explanation_summary {
            mismatches.push("explanation_summary");
        }
        if !stored_f32_matches(stored.confidence, traceback.confidence) {
            mismatches.push("confidence");
        }
        if !mismatches.is_empty() {
            return Err(format!(
                "run {run_id} persisted traceback {traceback_id} differs from evaluation fields: {}",
                mismatches.join(",")
            ));
        }
    }
    Ok(())
}

/// Compare only attribution rows that belong to the final authoritative claim
/// set. Phase-1 provisional claims are retained history, not replay output.
fn filter_attribution_claims(
    results: &AttributionResults,
    authoritative: &std::collections::BTreeSet<String>,
) -> AttributionResults {
    let mut filtered = results.clone();
    filtered
        .claims
        .retain(|claim| authoritative.contains(claim.claim_id.as_str()));
    filtered
}

fn validate_stored_attribution(
    reader: &PersistenceReader,
    run_id: &str,
    expected: &AttributionResults,
    authoritative: &std::collections::BTreeSet<String>,
) -> Result<(), String> {
    let (version, payload) = reader
        .get_analysis_checkpoint(run_id, ATTRIBUTION_RESULTS_KIND)
        .map_err(|err| format!("run {run_id} failed to read attribution-results: {err}"))?
        .ok_or_else(|| format!("run {run_id} lacks stored attribution-results/1"))?;
    if version != ATTRIBUTION_RESULTS_VERSION {
        return Err(format!(
            "run {run_id} stored attribution-results version {version} is not {ATTRIBUTION_RESULTS_VERSION}"
        ));
    }
    let stored: AttributionResults = serde_json::from_value(payload).map_err(|err| {
        format!("run {run_id} stored attribution-results payload is invalid: {err}")
    })?;
    if stored.run_id != run_id {
        return Err(format!(
            "run {run_id} stored attribution-results run_id {:?} drifted",
            stored.run_id
        ));
    }
    if stored.claims.iter().any(|result| result.run_id != run_id) {
        return Err(format!(
            "run {run_id} stored attribution-results contains a foreign claim run_id"
        ));
    }
    if filter_attribution_claims(&stored, authoritative)
        != filter_attribution_claims(expected, authoritative)
    {
        return Err(format!(
            "run {run_id} stored attribution-results payload differs from exact checkpoint evaluation"
        ));
    }
    Ok(())
}

fn validate_stored_run_metrics(
    reader: &PersistenceReader,
    run_id: &str,
    expected: &RunMetrics,
) -> Result<(), String> {
    let (version, payload) = reader
        .get_analysis_checkpoint(run_id, RUN_METRICS_KIND)
        .map_err(|err| format!("run {run_id} failed to read run-metrics: {err}"))?
        .ok_or_else(|| format!("run {run_id} lacks stored run-metrics/1"))?;
    if version != RUN_METRICS_VERSION {
        return Err(format!(
            "run {run_id} stored run-metrics version {version} is not {RUN_METRICS_VERSION}"
        ));
    }
    let stored: RunMetrics = serde_json::from_value(payload)
        .map_err(|err| format!("run {run_id} stored run-metrics payload is invalid: {err}"))?;
    if stored.run_id != run_id {
        return Err(format!(
            "run {run_id} stored run-metrics run_id {:?} drifted",
            stored.run_id
        ));
    }
    if !run_metrics_equivalent(&stored, expected) {
        return Err(format!(
            "run {run_id} stored run-metrics payload differs from exact checkpoint evaluation"
        ));
    }
    Ok(())
}

/// Compare run-metrics at the precision the schema actually promises.
///
/// score_breakdown is persisted as untyped JSON, so a value that came from an
/// f32 can be written with a different f64 decimal spelling while still
/// denoting the same f32. Parse both sides back into the typed breakdown and
/// compare the actual f32 fields; a one-ULP semantic change is still rejected.
fn run_metrics_equivalent(stored: &RunMetrics, expected: &RunMetrics) -> bool {
    use mtagentlens::responsibility::types::refinement::ScoreBreakdown;
    let parse =
        |value: &serde_json::Value| serde_json::from_value::<ScoreBreakdown>(value.clone()).ok();
    let (Some(stored_breakdown), Some(expected_breakdown)) = (
        parse(&stored.score_breakdown),
        parse(&expected.score_breakdown),
    ) else {
        return stored == expected;
    };
    if stored_breakdown != expected_breakdown {
        return false;
    }
    let mut stored_rest = stored.clone();
    stored_rest.score_breakdown = serde_json::Value::Null;
    let mut expected_rest = expected.clone();
    expected_rest.score_breakdown = serde_json::Value::Null;
    stored_rest == expected_rest
}

fn claim_traceback_closure(
    claims: &[ResponsibilityClaim],
    tracebacks: &[ResponsibilityTraceback],
    attributions: &AttributionResults,
) -> Result<(), String> {
    use std::collections::HashMap;

    let claims_by_id: HashMap<&str, &ResponsibilityClaim> = claims
        .iter()
        .map(|claim| (claim.claim_id.as_str(), claim))
        .collect();
    let tracebacks_by_id: HashMap<&str, &ResponsibilityTraceback> = tracebacks
        .iter()
        .map(|traceback| (traceback.traceback_id.as_str(), traceback))
        .collect();

    for claim in claims {
        if let Some(reference) = &claim.traceback_ref {
            let traceback = tracebacks_by_id.get(reference.as_str()).ok_or_else(|| {
                format!(
                    "claim {} traceback_ref {} has no replay traceback",
                    claim.claim_id, reference
                )
            })?;
            if traceback.claim_id != claim.claim_id {
                return Err(format!(
                    "replay traceback {} belongs to claim {}, not claim {}",
                    traceback.traceback_id, traceback.claim_id, claim.claim_id
                ));
            }
        }
    }
    for traceback in tracebacks {
        if !claims_by_id.contains_key(traceback.claim_id.as_str()) {
            return Err(format!(
                "replay traceback {} references absent claim {}",
                traceback.traceback_id, traceback.claim_id
            ));
        }
    }
    for result in &attributions.claims {
        let claim = claims_by_id.get(result.claim_id.as_str()).ok_or_else(|| {
            format!(
                "attribution references claim {} absent from replay claims",
                result.claim_id
            )
        })?;
        let expected = claim
            .traceback_ref
            .as_ref()
            .map(|reference| reference.as_str().to_string());
        if result.traceback_ref.as_deref() != expected.as_deref() {
            return Err(format!(
                "attribution for claim {} traceback_ref {:?} does not match claim traceback_ref {:?}",
                result.claim_id, result.traceback_ref, expected
            ));
        }
    }
    Ok(())
}

// ═══════════════════════════════════════════════════════════════════════════
// Identity experiment
// ═══════════════════════════════════════════════════════════════════════════

/// Read-only, model-free identity experiment over every run in one database.
///
/// Each run is validated and predicted before the output file is opened, so one
/// invalid checkpoint cannot leave a partial or silently shortened experiment.
fn write_identity_experiment(database: &Path, output: &Path) -> Result<serde_json::Value, String> {
    let source_database_sha256 = sha256_file(database)?;
    let reader = PersistenceReader::open(&database.to_string_lossy())
        .map_err(|err| format!("failed to open {}: {err}", database.display()))?;
    let runs = reader
        .list_runs()
        .map_err(|err| format!("failed to list runs: {err}"))?;
    if runs.is_empty() {
        return Err(format!("no run row in {}", database.display()));
    }

    let current = implementation_fingerprint();
    let mut run_values = Vec::with_capacity(runs.len());
    for run in &runs {
        let checkpoint = load_current_checkpoint(&reader, &run.run_id, &current)?;
        if checkpoint.identity_input.is_none() {
            return Err(format!(
                "run {} checkpoint lacks the frozen PRE-resolver identity input",
                run.run_id
            ));
        }
        if checkpoint.identity_policy.is_none() {
            return Err(format!(
                "run {} checkpoint lacks the frozen identity boundary policy",
                run.run_id
            ));
        }

        let mut views = Vec::with_capacity(IdentityCondition::ALL.len());
        let mut counts = serde_json::Map::new();
        for condition in IdentityCondition::ALL {
            let view = identity_view_for_checkpoint(&checkpoint, condition).map_err(|err| {
                format!(
                    "run {} {} identity view failed: {err}",
                    run.run_id,
                    condition.as_str()
                )
            })?;
            counts.insert(
                condition.as_str().to_string(),
                serde_json::Value::Object(identity_counts(&view)),
            );
            views.push(
                serde_json::to_value(&view)
                    .map_err(|err| format!("failed to serialize identity view: {err}"))?,
            );
        }

        run_values.push(json!({
            "run_id": run.run_id,
            "source_fingerprint": checkpoint.implementation,
            "current_fingerprint": current,
            "views": views,
            "counts": counts,
        }));
    }

    let after = sha256_file(database)?;
    if after != source_database_sha256 {
        return Err(format!(
            "source database {} changed while reading: {source_database_sha256} -> {after}",
            database.display()
        ));
    }
    let payload = json!({
        "schema": "mtagentlens.identity-experiment/v1",
        "current_fingerprint": current,
        "source_database_sha256": source_database_sha256,
        "runs": run_values,
    });
    write_new_json(output, &payload, "identity experiment")?;
    Ok(payload)
}

/// Read-only raw-fact ablation reconstruction over every run in one database.
///
/// Each run is rebuilt from the checkpoint's PRE-resolver facts. The frozen
/// final graph and authoritative identities are not inputs; historical
/// checkpoints without the identity input are rejected instead of scored from
/// a guess. Nothing is written unless every run reconstructs.
fn preflight_ablation_review_live(execute: bool, paid: Option<&str>) -> Result<(), String> {
    if !execute {
        return Err("--ablation-review-live requires --execute".into());
    }
    if paid != Some("1") {
        return Err("--ablation-review-live requires MTL_ALLOW_PAID=1".into());
    }
    Ok(())
}

fn require_single_current_run(run_count: usize) -> Result<(), String> {
    if run_count == 1 {
        Ok(())
    } else {
        Err(format!(
            "--ablation-review-live requires exactly one current run; found {run_count}"
        ))
    }
}

fn load_live_observer_provider()
-> Result<mtagentlens::responsibility::observer::provider::ObserverLLMProvider, String> {
    use mtagentlens::responsibility::observer::provider::ObserverLLMProvider;
    if let Ok(provider) = ObserverLLMProvider::from_env() {
        return Ok(provider);
    }
    let config_paths = ["env/config/observer.toml", "observer_llm.toml"];
    match ObserverLLMProvider::load_from_paths(&config_paths) {
        Ok(Some(provider)) => Ok(provider),
        Ok(None) => Err(
            "no Observer provider configured; set environment variables or provide env/config/observer.toml"
                .into(),
        ),
        Err(err) => Err(format!("Observer config exists but is unusable: {err:?}")),
    }
}

async fn write_ablation_review_live(
    database: &Path,
    output: &Path,
    provider: &dyn mtagentlens::responsibility::observer::provider::ObserverProvider,
) -> Result<serde_json::Value, String> {
    use mtagentlens::responsibility::replay::ablation::review_with_provider;

    if output.exists() {
        return Err(format!(
            "refusing to write {}: output already exists",
            output.display()
        ));
    }
    let reader = PersistenceReader::open(&database.to_string_lossy())
        .map_err(|err| format!("failed to open {}: {err}", database.display()))?;
    let runs = reader
        .list_runs()
        .map_err(|err| format!("failed to list runs: {err}"))?;
    require_single_current_run(runs.len())?;
    let current = implementation_fingerprint();
    let checkpoint = load_current_checkpoint(&reader, &runs[0].run_id, &current)?;
    let cache = review_with_provider(&checkpoint, provider)
        .await
        .map_err(|err| format!("run {} ablation review failed: {err}", runs[0].run_id))?;
    let payload = serde_json::to_value(&cache)
        .map_err(|err| format!("failed to serialize ablation review cache: {err}"))?;
    write_new_json(output, &payload, "ablation review cache")?;
    Ok(payload)
}

fn write_ablation_experiment(
    database: &Path,
    output: &Path,
    review_cache: Option<&Path>,
) -> Result<serde_json::Value, String> {
    let reader = PersistenceReader::open(&database.to_string_lossy())
        .map_err(|err| format!("failed to open {}: {err}", database.display()))?;
    let runs = reader
        .list_runs()
        .map_err(|err| format!("failed to list runs: {err}"))?;
    if runs.is_empty() {
        return Err(format!("no run row in {}", database.display()));
    }
    if review_cache.is_some() && runs.len() != 1 {
        return Err(format!(
            "an ablation review cache binds exactly one run; {} has {} runs",
            database.display(),
            runs.len()
        ));
    }
    let cache = match review_cache {
        Some(path) => {
            let text = std::fs::read_to_string(path)
                .map_err(|err| format!("failed to read {}: {err}", path.display()))?;
            Some(
                serde_json::from_str::<AblationReviewCache>(&text)
                    .map_err(|err| format!("failed to parse {}: {err}", path.display()))?,
            )
        }
        None => None,
    };

    let current = implementation_fingerprint();
    let mut run_values = Vec::with_capacity(runs.len());
    for run in &runs {
        let checkpoint = load_current_checkpoint(&reader, &run.run_id, &current)?;
        let report = match &cache {
            Some(cache) => rebuild_with_reviews(&checkpoint, cache),
            None => rebuild_ablation(&checkpoint),
        }
        .map_err(|err| format!("run {} ablation reconstruction failed: {err}", run.run_id))?;
        run_values.push(
            serde_json::to_value(&report)
                .map_err(|err| format!("failed to serialize ablation report: {err}"))?,
        );
    }

    let payload = json!({
        "schema": ABLATION_SCHEMA,
        "protocol": ABLATION_PROTOCOL,
        "current_fingerprint": current,
        "runs": run_values,
    });
    write_new_json(output, &payload, "ablation experiment")?;
    Ok(payload)
}

/// Load the one current frozen-analysis checkpoint for a run.
///
/// Identity and attribution exports share this strict gate: a missing, stale,
/// foreign, or differently-contracted checkpoint is an error, never a silently
/// shorter export.
fn load_current_checkpoint(
    reader: &PersistenceReader,
    run_id: &str,
    current: &str,
) -> Result<AnalysisCheckpoint, String> {
    let (version, payload) = reader
        .get_analysis_checkpoint(run_id, CHECKPOINT_KIND)
        .map_err(|err| format!("failed to read analysis checkpoint: {err}"))?
        .ok_or_else(|| format!("run {run_id} lacks a complete versioned analysis checkpoint"))?;
    if version != CHECKPOINT_VERSION {
        return Err(format!(
            "run {run_id} checkpoint version {version} is not {CHECKPOINT_VERSION}"
        ));
    }
    let checkpoint: AnalysisCheckpoint = serde_json::from_value(payload)
        .map_err(|err| format!("run {run_id} checkpoint is invalid: {err}"))?;
    if checkpoint.run_id != run_id {
        return Err(format!(
            "run {run_id} checkpoint belongs to run {}",
            checkpoint.run_id
        ));
    }
    if checkpoint.contract != REPLAY_CONTRACT {
        return Err(format!(
            "run {run_id} checkpoint contract {:?} is not {REPLAY_CONTRACT:?}",
            checkpoint.contract
        ));
    }
    if checkpoint.implementation != current
        && !mtagentlens::responsibility::replay::checkpoint::recorded_reanalysis_enabled()
    {
        return Err(format!(
            "run {run_id} checkpoint implementation fingerprint {:?} is not current {current:?}",
            checkpoint.implementation
        ));
    }
    Ok(checkpoint)
}

/// Read-only, model-free typed attribution over every run in one database.
///
/// The export is fully validated and serialized before the output file is
/// opened, so one invalid run cannot leave a partial or silently shortened
/// experiment. It never rebuilds identity, correlation, or claims from legacy
/// tables: each run's value is exactly `checkpoint.evaluate().attributions`.
fn write_attribution_experiment(
    database: &Path,
    output: &Path,
) -> Result<serde_json::Value, String> {
    let source_database_sha256 = sha256_file(database)?;
    let reader = PersistenceReader::open(&database.to_string_lossy())
        .map_err(|err| format!("failed to open {}: {err}", database.display()))?;
    let runs = reader
        .list_runs()
        .map_err(|err| format!("failed to list runs: {err}"))?;
    if runs.is_empty() {
        return Err(format!("no run row in {}", database.display()));
    }

    let current = implementation_fingerprint();
    let mut run_values = Vec::with_capacity(runs.len());
    for run in &runs {
        let checkpoint = load_current_checkpoint(&reader, &run.run_id, &current)?;
        checkpoint.validate(&run.run_id)?;
        if checkpoint.identity_input.is_none() {
            return Err(format!(
                "run {} checkpoint lacks the frozen PRE-resolver identity input",
                run.run_id
            ));
        }
        if checkpoint.identity_policy.is_none() {
            return Err(format!(
                "run {} checkpoint lacks the frozen identity boundary policy",
                run.run_id
            ));
        }
        identity_view_for_checkpoint(&checkpoint, IdentityCondition::Full)
            .map_err(|err| format!("run {} Full identity view failed: {err}", run.run_id))?;
        if checkpoint.captured_request_contexts.is_none() {
            return Err(format!(
                "run {} checkpoint lacks the frozen captured request contexts",
                run.run_id
            ));
        }

        let attributions = checkpoint.evaluate().attributions;
        run_values.push(json!({
            "run_id": run.run_id,
            "source_fingerprint": checkpoint.implementation,
            "current_fingerprint": current,
            "attributions": attributions,
        }));
    }

    let after = sha256_file(database)?;
    if after != source_database_sha256 {
        return Err(format!(
            "source database {} changed while reading: {source_database_sha256} -> {after}",
            database.display()
        ));
    }
    let payload = json!({
        "schema": "mtagentlens.attribution-experiment/v1",
        "current_fingerprint": current,
        "source_database_sha256": source_database_sha256,
        "runs": run_values,
    });
    write_new_json(output, &payload, "attribution experiment")?;
    Ok(payload)
}

/// Stream one file through SHA256 without loading it into memory.
///
/// Experiment wrappers bind their predictions to the exact SQLite bytes they
/// were derived from. Callers hash before and after reading and reject a
/// database that changed underneath them.
fn sha256_file(path: &Path) -> Result<String, String> {
    let mut file = std::fs::File::open(path)
        .map_err(|err| format!("failed to open {} for hashing: {err}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let read = file
            .read(&mut buffer)
            .map_err(|err| format!("failed to hash {}: {err}", path.display()))?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(hex::encode(hasher.finalize()))
}

/// Serialize fully, then create the output exclusively. `create_new` refuses an
/// existing file, a symlink, and the source database itself.
fn write_new_json(output: &Path, payload: &serde_json::Value, what: &str) -> Result<(), String> {
    let bytes = serde_json::to_vec_pretty(payload)
        .map_err(|err| format!("failed to serialize {what}: {err}"))?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(output)
        .map_err(|err| format!("refusing to write {}: {err}", output.display()))?;
    file.write_all(&bytes)
        .map_err(|err| format!("failed to write {}: {err}", output.display()))
}

fn identity_counts(view: &IdentityView) -> serde_json::Map<String, serde_json::Value> {
    let kind_key = |kind: IdentityUnitKind| match kind {
        IdentityUnitKind::Action => "action",
        IdentityUnitKind::Intent => "intent",
        IdentityUnitKind::Message => "message",
    };
    let mut assigned = std::collections::BTreeMap::new();
    let mut unknown = std::collections::BTreeMap::new();
    for kind in [
        IdentityUnitKind::Action,
        IdentityUnitKind::Intent,
        IdentityUnitKind::Message,
    ] {
        assigned.insert(kind_key(kind), 0u64);
        unknown.insert(kind_key(kind), 0u64);
    }
    for unit in &view.units {
        let counters = if unit.actor.partition().is_some() {
            &mut assigned
        } else {
            &mut unknown
        };
        *counters
            .get_mut(kind_key(unit.kind))
            .expect("identity kind") += 1;
    }
    let mut counts = serde_json::Map::new();
    for kind in ["action", "intent", "message"] {
        counts.insert(
            kind.to_string(),
            json!({"assigned": assigned[kind], "unknown": unknown[kind]}),
        );
    }
    counts
}

// ═══════════════════════════════════════════════════════════════════════════
// Artifact resolution
// ═══════════════════════════════════════════════════════════════════════════

/// Resolve the export database with the same rules and exit codes as replay.
fn resolve_export_database(args: &Args) -> PathBuf {
    match (&args.run_dir, &args.sqlite) {
        (Some(run_dir), None) => match resolve_live_db_path(Path::new(run_dir)) {
            Some(path) => path,
            None => {
                eprintln!(
                    "no live responsibility database in {}",
                    Path::new(run_dir).display()
                );
                std::process::exit(1);
            }
        },
        (None, Some(db_path)) => PathBuf::from(db_path),
        _ => {
            eprintln!("one of --run-dir or --sqlite is required");
            std::process::exit(2);
        }
    }
}

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
             normalized_args_json, derivation_mode, signal_class, timestamp_ns, run_id,
             outcome, syscall_ret, syscall_name)
             VALUES ('action-1', 42, 1000, 'ProcessExec',
             '[{\"Command\":\"cat /etc/shadow\"}]', 'Direct', 'HighSignal', 1500, ?1,
             'succeeded', 0, 'execve')",
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
        seed_analysis_checkpoint(&db_path, run_id);
        run_dir
    }

    fn seed_analysis_checkpoint(db_path: &Path, run_id: &str) {
        use mtagentlens::responsibility::{
            evidence::EvidenceCorrelator,
            graph::{GraphBuilder, snapshot::create_snapshot},
            identity::union_find::{AuthoritativeIdentityProvider, AuthoritativeResolution},
            replay::checkpoint::{
                AnalysisCheckpoint, CHECKPOINT_KIND, CHECKPOINT_VERSION, REPLAY_CONTRACT,
                implementation_fingerprint,
            },
            signals::SignalGraph,
            types::BehaviorTimeline,
        };
        let reader = PersistenceReader::open(&db_path.to_string_lossy()).unwrap();
        let intents = reader.get_intents(run_id).unwrap();
        let actions = reader.get_actions(run_id).unwrap();
        let (_, identities) = reader.get_identities(run_id).unwrap();
        let resolution = AuthoritativeResolution {
            agents: identities.clone(),
            refinements: Vec::new(),
            subagent_boundaries: Vec::new(),
            unresolved: Vec::new(),
            conversation_agents: Vec::new(),
            ambiguous_messages: Vec::new(),
        };
        let identity = AuthoritativeIdentityProvider::new(&resolution);
        let correlation = EvidenceCorrelator::new().correlate_batch(
            &intents,
            &actions,
            &SignalGraph::new().snapshot(),
        );
        let mut builder = GraphBuilder::with_default_config();
        for evidence in &correlation.evidence {
            let intent = intents
                .iter()
                .find(|intent| intent.intent_id == evidence.intent_id)
                .unwrap();
            let action = actions
                .iter()
                .find(|action| action.action_id == evidence.action_id)
                .unwrap();
            builder.handle(evidence, intent, action, &identity);
        }
        use mtagentlens::responsibility::identity::{IdentityBoundaryPolicy, RawIdentityInput};
        use mtagentlens::responsibility::signals::signal_snapshot::ProcessExecInfo;
        use mtagentlens::responsibility::types::{MessageId, ProcessId};
        let mut identity_graph = SignalGraph::new();
        let agent = ProcessId::new(42, 1000);
        identity_graph.observe_exec(
            agent,
            ProcessId::new(1, 0),
            ProcessExecInfo {
                comm: "claude".into(),
                ..Default::default()
            },
            None,
            1000,
        );
        identity_graph.observe_message_direct(agent, MessageId::from("msg-1"));
        let checkpoint = AnalysisCheckpoint {
            run_id: run_id.into(),
            contract: REPLAY_CONTRACT.into(),
            implementation: implementation_fingerprint(),
            graph: create_snapshot(builder.graph(), "fixture-analysis".into(), 1600),
            identity_input: Some(RawIdentityInput::from_snapshot(
                &identity_graph.snapshot(),
                Vec::new(),
            )),
            identity_policy: Some(IdentityBoundaryPolicy::new(["claude"])),
            fallback_agent_id: identities.first().map(|identity| identity.agent_id.clone()),
            identities,
            evidence: correlation.evidence,
            intents,
            actions,
            delegations: Vec::new(),
            score_signals: Default::default(),
            observations: Vec::new(),
            disable_patterns: false,
            classification_config: Default::default(),
            workspace_prefix: "/fixture/".into(),
            timeline: BehaviorTimeline {
                steps: Vec::new(),
                total_duration_ms: 0,
            },
            capture_quality: json!({"status":"unknown", "sources":{}}),
            observer_enabled: None,
            graph_refinements: Vec::new(),
            identity_refinements: Vec::new(),
            evidence_refinements: correlation.refinements,
            non_link_count: correlation.non_links.len(),
            captured_request_contexts: None,
            graph_inputs: None,
        };
        checkpoint.validate(run_id).unwrap();
        let conn = rusqlite::Connection::open(db_path).unwrap();
        conn.execute("INSERT INTO analysis_checkpoints (run_id, checkpoint_kind, version, payload_json) VALUES (?1, ?2, ?3, ?4)",
            params![run_id, CHECKPOINT_KIND, CHECKPOINT_VERSION, serde_json::to_string(&checkpoint).unwrap()]).unwrap();
    }

    #[test]
    fn replay_reads_the_live_database_and_writes_a_report() {
        let dir = tempfile::tempdir().unwrap();
        let run_dir = seed_live_db(&dir);

        let report = replay_run_dir(&run_dir).unwrap();

        assert_eq!(report.mode, "reauthoritative");
        assert!(
            report.evidence.replayed_count > 0,
            "the frozen checkpoint must preserve the complete evidence input: {report:?}"
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

    #[tokio::test]
    async fn replay_reports_parity_when_persisted_evidence_matches() {
        let dir = tempfile::tempdir().unwrap();
        let run_dir = seed_live_db(&dir);
        let db_path = run_dir
            .join("collector")
            .join("collector.responsibility.db")
            .to_string_lossy()
            .into_owned();

        // Compare persisted live rows with the exact versioned analysis input.
        let first = replay_database(Path::new(&db_path), None).unwrap();
        assert!(first.evidence.replayed_count > 0);

        // Persist exactly what was replayed, keyed by the same semantic key.
        let reader = PersistenceReader::open(&db_path).unwrap();
        let run = reader.list_runs().unwrap().into_iter().next().unwrap();
        let result = ReplayExecutor::reauthoritative(&reader, &run.run_id).unwrap();
        drop(reader);
        use mtagentlens::responsibility::persistence::{
            PersistRecord, PersistenceWriter, WriterConfig,
        };
        let writer = PersistenceWriter::new(WriterConfig {
            db_path: db_path.clone(),
            ..Default::default()
        })
        .unwrap();
        writer
            .write_blocking(PersistRecord::RunStart {
                run_id: run.run_id.clone(),
                timestamp_ns: 1000,
            })
            .await;
        for evidence in &result.evidence {
            writer
                .write_blocking(PersistRecord::Evidence(evidence.clone()))
                .await;
        }
        for alert in &result.alerts {
            writer
                .write_blocking(PersistRecord::Alert(alert.clone()))
                .await;
        }
        writer.flush().await.unwrap();
        assert_eq!(writer.write_failure_count(), 0);

        let report = replay_run_dir(&run_dir).unwrap();
        assert_eq!(report.status, "ok", "report: {report:?}");
        assert_eq!(report.evidence.missing_from_replay, Vec::<String>::new());
        assert_eq!(report.evidence.extra_in_replay, Vec::<String>::new());
    }

    #[test]
    fn replay_rejects_live_tables_without_a_complete_analysis_checkpoint() {
        let directory = tempfile::tempdir().unwrap();
        let run_dir = seed_live_db(&directory);
        let path = run_dir.join("collector/collector.responsibility.db");
        let conn = rusqlite::Connection::open(path).unwrap();
        conn.execute("DELETE FROM analysis_checkpoints", [])
            .unwrap();
        drop(conn);
        let error = replay_run_dir(&run_dir).unwrap_err();
        assert!(
            error.contains("lacks a complete versioned analysis checkpoint"),
            "{error}"
        );
        assert!(!run_dir.join("replay.report.json").exists());
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

    fn write_identity_output(db_path: &Path, output: &Path) -> serde_json::Value {
        write_identity_experiment(db_path, output).unwrap()
    }

    fn assert_identity_failure(db_path: &Path, output: &Path, needle: &str) {
        let error = write_identity_experiment(db_path, output).unwrap_err();
        assert!(error.contains(needle), "{error}");
        assert!(
            !output.exists(),
            "a rejected experiment must not create output: {error}"
        );
    }

    fn edit_checkpoint(db_path: &Path, run_id: &str, edit: impl FnOnce(&mut serde_json::Value)) {
        let conn = rusqlite::Connection::open(db_path).unwrap();
        let payload: String = conn
            .query_row(
                "SELECT payload_json FROM analysis_checkpoints WHERE run_id=?1 AND checkpoint_kind=?2",
                params![run_id, CHECKPOINT_KIND],
                |row| row.get(0),
            )
            .unwrap();
        let mut value: serde_json::Value = serde_json::from_str(&payload).unwrap();
        edit(&mut value);
        conn.execute(
            "UPDATE analysis_checkpoints SET payload_json=?1 WHERE run_id=?2 AND checkpoint_kind=?3",
            params![serde_json::to_string(&value).unwrap(), run_id, CHECKPOINT_KIND],
        )
        .unwrap();
    }

    #[test]
    fn identity_output_exposes_five_views_for_a_real_sqlite_checkpoint() {
        let dir = tempfile::tempdir().unwrap();
        let run_dir = seed_live_db(&dir);
        let db_path = run_dir
            .join("collector")
            .join("collector.responsibility.db");
        let output = dir.path().join("identity-experiment.json");

        let payload = write_identity_output(&db_path, &output);

        assert_eq!(payload["schema"], "mtagentlens.identity-experiment/v1");
        assert_eq!(payload["current_fingerprint"], implementation_fingerprint());
        let runs = payload["runs"].as_array().unwrap();
        assert_eq!(runs.len(), 1);
        let run = &runs[0];
        assert_eq!(run["run_id"], "run-1");
        assert_eq!(run["source_fingerprint"], implementation_fingerprint());
        assert_eq!(run["current_fingerprint"], implementation_fingerprint());
        let views = run["views"].as_array().unwrap();
        assert_eq!(views.len(), IdentityCondition::ALL.len());
        for (view, condition) in views.iter().zip(IdentityCondition::ALL) {
            assert_eq!(view["condition"], condition.as_str());
            let counts = &run["counts"][condition.as_str()];
            for kind in ["action", "intent", "message"] {
                assert!(
                    counts[kind]["assigned"].is_u64() && counts[kind]["unknown"].is_u64(),
                    "missing {kind} counts for {}: {counts}",
                    condition.as_str()
                );
            }
        }
        assert!(
            run["counts"]["pid-instance-only"]["message"]["assigned"]
                .as_u64()
                .unwrap()
                >= 1,
            "the physical-process control must assign the fixture message: {run}"
        );
        assert!(
            run["counts"]["full"]["message"]["unknown"]
                .as_u64()
                .unwrap()
                >= 1,
            "without a conversation anchor full identity must abstain: {run}"
        );
        let text = std::fs::read_to_string(&output).unwrap();
        assert!(!text.contains("cat /etc/shadow"));
        assert!(!text.contains("accuracy"));
        assert!(!text.contains("probability"));
    }

    #[test]
    fn identity_output_rejects_a_checkpoint_without_pre_resolver_input() {
        let dir = tempfile::tempdir().unwrap();
        let run_dir = seed_live_db(&dir);
        let db_path = run_dir
            .join("collector")
            .join("collector.responsibility.db");
        edit_checkpoint(&db_path, "run-1", |value| {
            value["identity_input"] = serde_json::Value::Null;
        });
        assert_identity_failure(&db_path, &dir.path().join("out.json"), "PRE-resolver");
    }

    #[test]
    fn identity_output_rejects_a_checkpoint_without_a_frozen_policy() {
        let dir = tempfile::tempdir().unwrap();
        let run_dir = seed_live_db(&dir);
        let db_path = run_dir
            .join("collector")
            .join("collector.responsibility.db");
        edit_checkpoint(&db_path, "run-1", |value| {
            value["identity_policy"] = serde_json::Value::Null;
        });
        assert_identity_failure(&db_path, &dir.path().join("out.json"), "boundary policy");
    }

    #[test]
    fn identity_output_rejects_a_stale_implementation_fingerprint() {
        let dir = tempfile::tempdir().unwrap();
        let run_dir = seed_live_db(&dir);
        let db_path = run_dir
            .join("collector")
            .join("collector.responsibility.db");
        edit_checkpoint(&db_path, "run-1", |value| {
            value["implementation"] = json!("stale-fingerprint");
        });
        assert_identity_failure(&db_path, &dir.path().join("out.json"), "fingerprint");
    }

    #[test]
    fn identity_output_rejects_a_stale_checkpoint_version() {
        let dir = tempfile::tempdir().unwrap();
        let run_dir = seed_live_db(&dir);
        let db_path = run_dir
            .join("collector")
            .join("collector.responsibility.db");
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute(
            "UPDATE analysis_checkpoints SET version=?1 WHERE run_id='run-1'",
            params![CHECKPOINT_VERSION + 1],
        )
        .unwrap();
        drop(conn);
        assert_identity_failure(&db_path, &dir.path().join("out.json"), "version");
    }

    #[test]
    fn identity_output_rejects_a_checkpoint_from_another_run() {
        let dir = tempfile::tempdir().unwrap();
        let run_dir = seed_live_db(&dir);
        let db_path = run_dir
            .join("collector")
            .join("collector.responsibility.db");
        edit_checkpoint(&db_path, "run-1", |value| {
            value["run_id"] = json!("run-2");
        });
        assert_identity_failure(&db_path, &dir.path().join("out.json"), "run");
    }

    #[test]
    fn identity_output_rejects_an_empty_database() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("empty.db");
        drop(open_responsibility_db(&db_path.to_string_lossy()).unwrap());
        assert_identity_failure(&db_path, &dir.path().join("out.json"), "no run row");
    }

    #[test]
    fn identity_output_never_overwrites_an_existing_file() {
        let dir = tempfile::tempdir().unwrap();
        let run_dir = seed_live_db(&dir);
        let db_path = run_dir
            .join("collector")
            .join("collector.responsibility.db");
        let output = dir.path().join("out.json");
        std::fs::write(&output, b"keep-me").unwrap();
        let error = write_identity_experiment(&db_path, &output).unwrap_err();
        assert!(error.contains("refusing to write"), "{error}");
        assert_eq!(std::fs::read(&output).unwrap(), b"keep-me");
    }

    #[test]
    fn identity_output_never_overwrites_the_source_database() {
        let dir = tempfile::tempdir().unwrap();
        let run_dir = seed_live_db(&dir);
        let db_path = run_dir
            .join("collector")
            .join("collector.responsibility.db");
        let before = std::fs::read(&db_path).unwrap();
        let error = write_identity_experiment(&db_path, &db_path).unwrap_err();
        assert!(error.contains("refusing to write"), "{error}");
        assert_eq!(std::fs::read(&db_path).unwrap(), before);
    }

    fn seed_attribution_db(dir: &tempfile::TempDir) -> PathBuf {
        let run_dir = seed_live_db(dir);
        let db_path = run_dir
            .join("collector")
            .join("collector.responsibility.db");
        edit_checkpoint(&db_path, "run-1", |value| {
            value["captured_request_contexts"] = json!([]);
        });
        db_path
    }

    fn assert_attribution_failure(db_path: &Path, output: &Path, needle: &str) {
        let error = write_attribution_experiment(db_path, output).unwrap_err();
        assert!(error.contains(needle), "{error}");
        assert!(
            !output.exists(),
            "a rejected export must not create output: {error}"
        );
    }

    #[test]
    fn attribution_output_writes_all_runs_for_a_current_checkpoint() {
        use mtagentlens::responsibility::claims::attribution::ATTRIBUTION_SCHEMA;

        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        let output = dir.path().join("attribution-experiment.json");

        let payload = write_attribution_experiment(&db_path, &output).unwrap();

        assert_eq!(payload["schema"], "mtagentlens.attribution-experiment/v1");
        assert_eq!(payload["current_fingerprint"], implementation_fingerprint());
        let runs = payload["runs"].as_array().unwrap();
        assert_eq!(runs.len(), 1);
        let run = &runs[0];
        assert_eq!(run["run_id"], "run-1");
        assert_eq!(run["source_fingerprint"], implementation_fingerprint());
        assert_eq!(run["current_fingerprint"], implementation_fingerprint());
        let attributions = &run["attributions"];
        assert_eq!(attributions["schema"], ATTRIBUTION_SCHEMA);
        assert_eq!(attributions["run_id"], "run-1");
        assert!(attributions["claims"].as_array().unwrap().is_empty());
        let text = std::fs::read_to_string(&output).unwrap();
        assert!(!text.contains("accuracy"));
        assert!(!text.contains("ground_truth"));
    }

    #[test]
    fn ablation_output_writes_all_runs_from_raw_facts() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        let output = dir.path().join("ablation-experiment.json");

        let payload = write_ablation_experiment(&db_path, &output, None).unwrap();

        assert_eq!(payload["schema"], ABLATION_SCHEMA);
        assert_eq!(payload["protocol"], ABLATION_PROTOCOL);
        assert_eq!(payload["current_fingerprint"], implementation_fingerprint());
        let runs = payload["runs"].as_array().unwrap();
        assert_eq!(runs.len(), 1);
        let run = &runs[0];
        assert_eq!(run["run_id"], "run-1");
        assert_eq!(run["read_checkpoint_graph"], false);
        assert_eq!(run["read_checkpoint_identities"], false);
        let layers = run["layers"].as_array().unwrap();
        assert_eq!(layers.len(), 5);
        assert_eq!(
            layers
                .iter()
                .map(|layer| layer["layer"].as_str().unwrap())
                .collect::<Vec<_>>(),
            vec!["L0", "L0.5", "L1", "L2", "L3"]
        );
        assert!(layers[0]["score"].is_null());
        assert_eq!(layers[0]["score_kind"], "continuous");
        assert!(
            layers[0]["score_definition"]
                .as_str()
                .is_some_and(|definition| definition.contains("activity")),
            "L0 must export the raw-activity index definition: {layers:?}"
        );
        let raw_actions = layers[0]["visible"]["actions"]
            .as_u64()
            .expect("L0 exposes the raw action count");
        assert!(raw_actions > 0, "fixture must expose raw actions");
        for (index, layer) in layers.iter().enumerate() {
            let ranking = layer["ranking_score"]
                .as_f64()
                .unwrap_or_else(|| panic!("layer {index} has no per-run ranking score: {layer}"));
            assert!(
                ranking.is_finite(),
                "layer {index} ranking score is not finite"
            );
            assert_eq!(layer["score_kind"], "continuous", "layer {index}");
        }
        assert_eq!(
            layers[0]["ranking_score"].as_f64(),
            Some(raw_actions as f64),
            "L0 score-based metrics need the raw-activity index, not a fabricated kernel score"
        );
        assert!(!layers[4]["score"].is_null());
        assert_eq!(layers[4]["identity_condition"], "full");
        assert!(
            layers[0]["review_candidates"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        assert!(
            layers[1]["review_candidates"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        assert!(
            layers[2]["review_candidates"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        assert!(layers[3]["review_candidates"].is_array());
        assert!(layers[4]["review_candidates"].is_array());
    }

    #[test]
    fn ablation_review_live_requires_execute_and_paid_acknowledgment() {
        assert!(
            preflight_ablation_review_live(false, Some("1"))
                .unwrap_err()
                .contains("--execute")
        );
        assert!(
            preflight_ablation_review_live(true, None)
                .unwrap_err()
                .contains("MTL_ALLOW_PAID")
        );
        assert!(preflight_ablation_review_live(true, Some("0")).is_err());
        assert!(preflight_ablation_review_live(true, Some("1")).is_ok());
        assert!(require_single_current_run(1).is_ok());
        assert!(require_single_current_run(0).is_err());
        assert!(require_single_current_run(2).is_err());
    }

    struct FailingProvider;

    #[async_trait::async_trait]
    impl mtagentlens::responsibility::observer::provider::ObserverProvider for FailingProvider {
        async fn evaluate(
            &self,
            _input: &mtagentlens::responsibility::classification::types::ObserverInput,
        ) -> Result<
            mtagentlens::responsibility::observer::types::ObserverOutput,
            mtagentlens::responsibility::observer::provider::ObserverProviderError,
        > {
            Err(mtagentlens::responsibility::observer::provider::ObserverProviderError::Timeout)
        }
    }

    #[tokio::test]
    async fn live_review_failure_does_not_write_output_or_mutate_database() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        let before = std::fs::read(&db_path).unwrap();
        let output = dir.path().join("live-cache.json");
        let error = write_ablation_review_live(&db_path, &output, &FailingProvider)
            .await
            .unwrap_err();
        assert!(!output.exists(), "{error}");
        assert_eq!(std::fs::read(&db_path).unwrap(), before);
    }

    #[test]
    fn ablation_review_cache_is_single_run_and_typed() {
        use mtagentlens::responsibility::classification::candidates::CandidateRef;
        use mtagentlens::responsibility::classification::final_decision::Observation;
        use mtagentlens::responsibility::replay::ablation::{
            ABLATION_REVIEW_CACHE_SCHEMA, AblationLayer, AblationReviewCache, AblationReviewLayer,
        };
        use mtagentlens::responsibility::types::AlertDecision;

        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        let output = dir.path().join("ablation-experiment.json");
        let payload = write_ablation_experiment(&db_path, &output, None).unwrap();
        let run = &payload["runs"][0];
        let cache = AblationReviewCache {
            schema: ABLATION_REVIEW_CACHE_SCHEMA.into(),
            run_id: run["run_id"].as_str().unwrap().into(),
            source_implementation: run["source_fingerprint"].as_str().unwrap().into(),
            raw_fact_fingerprint: run["raw_fact_fingerprint"].as_str().unwrap().into(),
            provenance: "external_unverified".into(),
            layers: vec![AblationReviewLayer {
                layer: AblationLayer::L2,
                observations: vec![Observation::ScopedReview {
                    candidates: vec![CandidateRef {
                        id: "operation:external".into(),
                        version: "not-current".into(),
                    }],
                    decision: AlertDecision::Safe,
                    confidence: 0.9,
                    cited: 1,
                }],
                metadata: None,
            }],
        };
        let cache_path = dir.path().join("review-cache.json");
        std::fs::write(&cache_path, serde_json::to_vec(&cache).unwrap()).unwrap();
        let reviewed_output = dir.path().join("reviewed.json");
        let reviewed =
            write_ablation_experiment(&db_path, &reviewed_output, Some(&cache_path)).unwrap();
        assert_eq!(
            reviewed["runs"][0]["layers"][3]["review_candidates"],
            run["layers"][3]["review_candidates"]
        );

        let mut wrong_run = cache.clone();
        wrong_run.run_id = "run-other".into();
        std::fs::write(&cache_path, serde_json::to_vec(&wrong_run).unwrap()).unwrap();
        let error = write_ablation_experiment(
            &db_path,
            &dir.path().join("wrong-run.json"),
            Some(&cache_path),
        )
        .unwrap_err();
        assert!(error.contains("does not match"), "{error}");
        assert!(!dir.path().join("wrong-run.json").exists());
    }

    #[test]
    fn ablation_output_never_overwrites_an_existing_file() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        let output = dir.path().join("out.json");
        std::fs::write(&output, b"keep-me").unwrap();
        let error = write_ablation_experiment(&db_path, &output, None).unwrap_err();
        assert!(error.contains("refusing to write"), "{error}");
        assert_eq!(std::fs::read(&output).unwrap(), b"keep-me");
    }

    #[test]
    fn ablation_output_rejects_a_checkpoint_without_pre_resolver_input() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_live_db(&dir);
        let db_path = db_path
            .join("collector")
            .join("collector.responsibility.db");
        edit_checkpoint(&db_path, "run-1", |value| {
            value.as_object_mut().unwrap().remove("identity_input");
        });
        let output = dir.path().join("ablation-experiment.json");
        let error = write_ablation_experiment(&db_path, &output, None).unwrap_err();
        assert!(error.contains("PRE-resolver identity input"), "{error}");
        assert!(!output.exists(), "a rejected export must not create output");
    }

    #[test]
    fn attribution_output_rejects_dangling_frozen_graph_edges() {
        use mtagentlens::responsibility::types::{
            EdgeId, NodeId, PhaseIndicator, ResponsibilityEdge, ResponsibilityEdgeType,
        };
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        edit_checkpoint(&db_path, "run-1", |value| {
            let mut checkpoint: AnalysisCheckpoint = serde_json::from_value(value.clone()).unwrap();
            checkpoint.graph.edges.push(ResponsibilityEdge {
                edge_id: EdgeId::from("dangling"),
                edge_type: ResponsibilityEdgeType::IntentCausesAction,
                operation: None,
                source: NodeId::from("absent-source"),
                target: NodeId::from("absent-target"),
                evidence_refs: vec![],
                confidence: 1.0,
                phase: PhaseIndicator::Provisional { timestamp_ns: 1 },
            });
            *value = serde_json::to_value(checkpoint).unwrap();
        });
        assert_attribution_failure(&db_path, &dir.path().join("out.json"), "dangling");
    }

    #[test]
    fn attribution_output_rejects_a_checkpoint_without_pre_resolver_input() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        edit_checkpoint(&db_path, "run-1", |value| {
            value["identity_input"] = serde_json::Value::Null;
        });
        assert_attribution_failure(&db_path, &dir.path().join("out.json"), "PRE-resolver");
    }

    #[test]
    fn attribution_output_rejects_a_checkpoint_without_a_frozen_policy() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        edit_checkpoint(&db_path, "run-1", |value| {
            value["identity_policy"] = serde_json::Value::Null;
        });
        assert_attribution_failure(&db_path, &dir.path().join("out.json"), "boundary policy");
    }

    #[test]
    fn attribution_output_rejects_a_checkpoint_without_captured_request_contexts() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        edit_checkpoint(&db_path, "run-1", |value| {
            value["captured_request_contexts"] = serde_json::Value::Null;
        });
        assert_attribution_failure(
            &db_path,
            &dir.path().join("out.json"),
            "captured request contexts",
        );
    }

    #[test]
    fn attribution_output_rejects_a_stale_implementation_fingerprint() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        edit_checkpoint(&db_path, "run-1", |value| {
            value["implementation"] = json!("stale-fingerprint");
        });
        assert_attribution_failure(&db_path, &dir.path().join("out.json"), "fingerprint");
    }

    #[test]
    fn attribution_output_rejects_a_stale_checkpoint_version() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute(
            "UPDATE analysis_checkpoints SET version=?1 WHERE run_id='run-1'",
            params![CHECKPOINT_VERSION + 1],
        )
        .unwrap();
        drop(conn);
        assert_attribution_failure(&db_path, &dir.path().join("out.json"), "version");
    }

    #[test]
    fn attribution_output_rejects_a_checkpoint_from_another_run() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        edit_checkpoint(&db_path, "run-1", |value| {
            value["run_id"] = json!("run-2");
        });
        assert_attribution_failure(&db_path, &dir.path().join("out.json"), "run");
    }

    #[test]
    fn attribution_output_validates_every_run_before_writing() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns) VALUES ('run-2', 500)",
            [],
        )
        .unwrap();
        drop(conn);
        let output = dir.path().join("out.json");
        assert_attribution_failure(
            &db_path,
            &output,
            "run-2 lacks a complete versioned analysis checkpoint",
        );
        assert!(!output.exists());
    }

    #[test]
    fn attribution_output_never_overwrites_an_existing_file() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        let output = dir.path().join("out.json");
        std::fs::write(&output, b"keep-me").unwrap();
        let error = write_attribution_experiment(&db_path, &output).unwrap_err();
        assert!(error.contains("refusing to write"), "{error}");
        assert_eq!(std::fs::read(&output).unwrap(), b"keep-me");
    }

    #[cfg(unix)]
    #[test]
    fn attribution_output_never_follows_an_existing_symlink() {
        use std::os::unix::fs::symlink;

        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        let target = dir.path().join("target.json");
        std::fs::write(&target, b"keep-me").unwrap();
        let output = dir.path().join("out.json");
        symlink(&target, &output).unwrap();
        let error = write_attribution_experiment(&db_path, &output).unwrap_err();
        assert!(error.contains("refusing to write"), "{error}");
        assert_eq!(std::fs::read(&target).unwrap(), b"keep-me");
    }

    #[test]
    fn attribution_output_never_overwrites_the_source_database() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        let before = std::fs::read(&db_path).unwrap();
        let error = write_attribution_experiment(&db_path, &db_path).unwrap_err();
        assert!(error.contains("refusing to write"), "{error}");
        assert_eq!(std::fs::read(&db_path).unwrap(), before);
    }

    #[test]
    fn attribution_output_conflicts_with_identity_output() {
        let error = match Args::try_parse_from([
            "replay",
            "--sqlite",
            "run.db",
            "--identity-output",
            "identity.json",
            "--attribution-output",
            "attribution.json",
        ]) {
            Ok(_) => panic!("the two export modes must conflict"),
            Err(error) => error,
        };
        assert_eq!(error.kind(), clap::error::ErrorKind::ArgumentConflict);
    }

    #[test]
    fn replay_errors_without_a_live_database() {
        let dir = tempfile::tempdir().unwrap();
        let run_dir = dir.path().join("empty-run");
        std::fs::create_dir_all(&run_dir).unwrap();
        let err = replay_run_dir(&run_dir).unwrap_err();
        assert!(err.contains("no live responsibility database"), "{err}");
    }

    // ── Strict recording validation ─────────────────────────────────────

    /// Build a complete current recording on top of the legacy fixture without
    /// changing that fixture's assumptions: clean persisted health metadata,
    /// frozen captured contexts, finished run, persisted evidence/alerts exactly
    /// as replay produces them, and the two versioned analysis rows.
    async fn seed_valid_recording_db(dir: &tempfile::TempDir) -> PathBuf {
        use mtagentlens::responsibility::persistence::{
            PersistRecord, PersistenceWriter, WriterConfig,
        };

        let run_dir = seed_live_db(dir);
        let db_path = run_dir
            .join("collector")
            .join("collector.responsibility.db");
        edit_checkpoint(&db_path, "run-1", |value| {
            use mtagentlens::responsibility::types::{
                AgentId, EdgeId, NodeId, NormalizedResource, PhaseIndicator, ResponsibilityEdge,
                ResponsibilityEdgeType, ResponsibilityNode, ResponsibilityNodeType,
            };

            let mut checkpoint: AnalysisCheckpoint = serde_json::from_value(value.clone()).unwrap();
            let evidence_id = checkpoint.evidence[0].evidence_id.clone();
            let phase = PhaseIndicator::Authoritative {
                timestamp_ns: 1600,
                overrides: None,
            };
            let agent_node = NodeId::from("fixture-agent");
            let boundary_node = NodeId::from("fixture-boundary");
            checkpoint.graph.nodes.push(ResponsibilityNode {
                node_id: agent_node.clone(),
                node_type: ResponsibilityNodeType::Agent,
                agent_id: Some(AgentId::from("agent-1")),
                resource: None,
                evidence_refs: vec![evidence_id.clone()],
                timestamp_ns: 1600,
                phase: phase.clone(),
            });
            checkpoint.graph.nodes.push(ResponsibilityNode {
                node_id: boundary_node.clone(),
                node_type: ResponsibilityNodeType::PolicyBoundary,
                agent_id: None,
                resource: Some(NormalizedResource::File {
                    canonical_path: "/fixture/secret.txt".into(),
                }),
                evidence_refs: vec![evidence_id.clone()],
                timestamp_ns: 1600,
                phase: phase.clone(),
            });
            checkpoint.graph.edges.push(ResponsibilityEdge {
                edge_id: EdgeId::from("fixture-pbc"),
                edge_type: ResponsibilityEdgeType::PolicyBoundaryCrossing,
                operation: None,
                source: agent_node,
                target: boundary_node,
                evidence_refs: vec![evidence_id],
                confidence: 0.9,
                phase,
            });
            *value = serde_json::to_value(checkpoint).unwrap();
            value["capture_quality"] = json!({
                "status": "unknown",
                "sources": {},
                "analysis": {
                    "persist_dropped": 0u64,
                    "persist_closed": 0u64,
                    "persist_failures": 0u64,
                    "persist_flush_failed": false
                }
            });
            value["captured_request_contexts"] = json!([]);
        });

        let reader = PersistenceReader::open(&db_path.to_string_lossy()).unwrap();
        let checkpoint =
            load_current_checkpoint(&reader, "run-1", &implementation_fingerprint()).unwrap();
        let evaluation = checkpoint.evaluate();
        let replay = ReplayExecutor::reauthoritative(&reader, "run-1").unwrap();
        drop(reader);
        assert!(
            !evaluation.claims.is_empty(),
            "validator fixture must contain an authoritative claim"
        );
        assert!(
            !evaluation.tracebacks.is_empty(),
            "validator fixture must contain a traceback"
        );

        let writer = PersistenceWriter::new(WriterConfig {
            db_path: db_path.to_string_lossy().into_owned(),
            ..Default::default()
        })
        .unwrap();
        writer
            .write_blocking(PersistRecord::RunStart {
                run_id: "run-1".into(),
                timestamp_ns: 1000,
            })
            .await;
        for evidence in &replay.evidence {
            writer
                .write_blocking(PersistRecord::Evidence(evidence.clone()))
                .await;
        }
        for alert in &replay.alerts {
            writer
                .write_blocking(PersistRecord::Alert(alert.clone()))
                .await;
        }
        for claim in &evaluation.claims {
            writer
                .write_blocking(PersistRecord::Claim(claim.clone()))
                .await;
        }
        for traceback in &evaluation.tracebacks {
            writer
                .write_blocking(PersistRecord::Traceback(traceback.clone()))
                .await;
        }
        writer
            .write_blocking(PersistRecord::RunFinalVerdict {
                run_id: "run-1".into(),
                alert_id: replay.alerts.last().map(|alert| alert.alert_id.to_string()),
                verdict: evaluation.verdict.clone(),
            })
            .await;
        writer.flush().await.unwrap();
        assert_eq!(writer.write_failure_count(), 0);
        drop(writer);

        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute("UPDATE runs SET ended_at_ns=2000 WHERE run_id='run-1'", [])
            .unwrap();
        let authoritative_claims: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM responsibility_claims
                 WHERE run_id='run-1' AND phase LIKE 'Authoritative%'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert!(
            authoritative_claims > 0,
            "fixture must persist authoritative claims"
        );
        let tracebacks: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM responsibility_tracebacks WHERE run_id='run-1'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert!(tracebacks > 0, "fixture must persist tracebacks");
        for (kind, version, payload) in [
            (
                RUN_METRICS_KIND,
                RUN_METRICS_VERSION,
                serde_json::to_value(&evaluation.run_metrics).unwrap(),
            ),
            (
                ATTRIBUTION_RESULTS_KIND,
                ATTRIBUTION_RESULTS_VERSION,
                serde_json::to_value(&evaluation.attributions).unwrap(),
            ),
        ] {
            conn.execute(
                "INSERT INTO analysis_checkpoints
                 (run_id, checkpoint_kind, version, payload_json)
                 VALUES (?1, ?2, ?3, ?4)",
                params![
                    "run-1",
                    kind,
                    version,
                    serde_json::to_string(&payload).unwrap()
                ],
            )
            .unwrap();
        }
        conn.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);")
            .unwrap();
        drop(conn);
        db_path
    }

    fn edit_stored_checkpoint(
        db_path: &Path,
        run_id: &str,
        kind: &str,
        edit: impl FnOnce(&mut serde_json::Value),
    ) {
        let conn = rusqlite::Connection::open(db_path).unwrap();
        let payload: String = conn
            .query_row(
                "SELECT payload_json FROM analysis_checkpoints
                 WHERE run_id=?1 AND checkpoint_kind=?2",
                params![run_id, kind],
                |row| row.get(0),
            )
            .unwrap();
        let mut value: serde_json::Value = serde_json::from_str(&payload).unwrap();
        edit(&mut value);
        conn.execute(
            "UPDATE analysis_checkpoints SET payload_json=?1
             WHERE run_id=?2 AND checkpoint_kind=?3",
            params![serde_json::to_string(&value).unwrap(), run_id, kind],
        )
        .unwrap();
    }

    fn report_failures(report: &RecordingValidationReport) -> Vec<String> {
        let mut failures = report.failures.clone();
        for run in &report.runs {
            failures.extend(run.failures.iter().cloned());
        }
        failures
    }

    fn assert_recording_failure(report: &RecordingValidationReport, needle: &str) {
        assert!(!report.is_ok(), "expected failure containing {needle}");
        let failures = report_failures(report);
        assert!(
            failures.iter().any(|failure| failure.contains(needle)),
            "expected a failure containing {needle:?}: {failures:?}"
        );
    }

    #[tokio::test]
    async fn strict_recording_accepts_a_current_nonempty_fixture() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;

        let report = validate_recording(&db_path);

        assert!(report.is_ok(), "failures: {:?}", report_failures(&report));
        assert!(report.read_only);
        assert_eq!(report.runs.len(), 1);
        let run = &report.runs[0];
        assert_eq!(run.run_id, "run-1");
        assert_eq!(run.status, "ok");
        assert_eq!(run.capture_quality.as_deref(), Some("unknown"));
        let evidence = run.evidence.as_ref().unwrap();
        assert!(
            evidence.persisted_count > 0,
            "fixture must persist evidence"
        );
        assert!(evidence.replayed_count > 0);
        assert!(!evidence.has_gap());
        assert_eq!(
            report.capture_quality.get("run-1").map(String::as_str),
            Some("unknown")
        );
        assert!(!report.primary_eligible);
        assert_eq!(report.reference_status, "unreviewed");

        let payload = serde_json::to_value(&report).unwrap();
        assert_eq!(payload["schema"], RECORDING_VALIDATION_SCHEMA);
        assert_eq!(payload["read_only"], true);
        assert_eq!(payload["status"], "ok");
        let text = serde_json::to_string(&payload).unwrap();
        assert!(!text.contains("probability"));
        assert!(!text.contains("accuracy"));
    }

    #[tokio::test]
    async fn strict_recording_rejects_a_header_hidden_by_a_newer_version_table() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute_batch("PRAGMA user_version = 4;").unwrap();
        drop(conn);
        assert_recording_failure(&validate_recording(&db_path), "schema");
    }

    #[tokio::test]
    async fn strict_recording_rejects_a_mutated_canonical_verdict() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute("UPDATE runs SET final_decision = 'fabricated'", [])
            .unwrap();
        drop(conn);
        assert_recording_failure(&validate_recording(&db_path), "canonical verdict");
    }

    #[tokio::test]
    async fn strict_recording_rejects_replaced_claim_ids_without_panicking() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let claim_id = authoritative_claim_id(&db_path);
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute_batch("PRAGMA foreign_keys=OFF;").unwrap();
        conn.execute(
            "UPDATE responsibility_claims SET claim_id='replacement' WHERE claim_id=?1",
            params![claim_id],
        )
        .unwrap();
        conn.execute(
            "UPDATE responsibility_tracebacks SET claim_id='replacement' WHERE claim_id=?1",
            params![claim_id],
        )
        .unwrap();
        drop(conn);
        assert_recording_failure(&validate_recording(&db_path), "claim set differs");
    }

    #[tokio::test]
    async fn strict_recording_rejects_replaced_traceback_ids_without_panicking() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let claim_id = authoritative_claim_id(&db_path);
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute(
            "UPDATE responsibility_tracebacks SET traceback_id='replacement' WHERE claim_id=?1",
            params![claim_id],
        )
        .unwrap();
        drop(conn);
        assert_recording_failure(&validate_recording(&db_path), "traceback set differs");
    }

    #[tokio::test]
    async fn strict_recording_rejects_a_negative_sqlite_end_timestamp() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute("UPDATE runs SET ended_at_ns=-1", []).unwrap();
        drop(conn);
        assert_recording_failure(&validate_recording(&db_path), "ended_at_ns");
    }

    fn authoritative_claim_id(db_path: &Path) -> String {
        let conn = rusqlite::Connection::open(db_path).unwrap();
        conn.query_row(
            "SELECT claim_id FROM responsibility_claims
             WHERE run_id='run-1' AND phase LIKE 'Authoritative%'
             ORDER BY claim_id LIMIT 1",
            [],
            |row| row.get(0),
        )
        .unwrap()
    }

    #[tokio::test]
    async fn strict_recording_rejects_a_deleted_authoritative_claim() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute_batch(
            "DELETE FROM responsibility_tracebacks WHERE claim_id IN
                (SELECT claim_id FROM responsibility_claims
                 WHERE run_id='run-1' AND phase LIKE 'Authoritative%');
             DELETE FROM responsibility_claims
             WHERE run_id='run-1' AND phase LIKE 'Authoritative%';",
        )
        .unwrap();
        drop(conn);

        let report = validate_recording(&db_path);

        assert_recording_failure(&report, "authoritative claim");
    }

    #[tokio::test]
    async fn strict_recording_rejects_a_mutated_authoritative_claim_subject() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let claim_id = authoritative_claim_id(&db_path);
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute(
            "UPDATE responsibility_claims SET subject_agent_id='intruder'
             WHERE run_id='run-1' AND claim_id=?1",
            params![claim_id],
        )
        .unwrap();
        drop(conn);

        let report = validate_recording(&db_path);

        assert_recording_failure(&report, "subject_agent_id");
    }

    #[tokio::test]
    async fn strict_recording_rejects_a_mutated_claim_traceback_ref() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let claim_id = authoritative_claim_id(&db_path);
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute(
            "UPDATE responsibility_claims SET traceback_ref='fabricated-tb'
             WHERE run_id='run-1' AND claim_id=?1",
            params![claim_id],
        )
        .unwrap();
        drop(conn);

        let report = validate_recording(&db_path);

        assert_recording_failure(&report, "traceback_ref");
    }

    #[tokio::test]
    async fn strict_recording_rejects_a_mutated_traceback_root() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let claim_id = authoritative_claim_id(&db_path);
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute(
            "UPDATE responsibility_tracebacks SET root_cause_node_id='fabricated-root'
             WHERE run_id='run-1' AND claim_id=?1",
            params![claim_id],
        )
        .unwrap();
        drop(conn);

        let report = validate_recording(&db_path);

        assert_recording_failure(&report, "root_cause_node_id");
    }

    #[tokio::test]
    async fn strict_recording_rejects_an_empty_database() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("empty.db");
        drop(open_responsibility_db(&db_path.to_string_lossy()).unwrap());

        let report = validate_recording(&db_path);

        assert_recording_failure(&report, "no run row");
        assert!(report.runs.is_empty());
    }

    #[tokio::test]
    async fn strict_recording_rejects_an_unfinished_run() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute("UPDATE runs SET ended_at_ns=NULL WHERE run_id='run-1'", [])
            .unwrap();
        drop(conn);

        let report = validate_recording(&db_path);

        assert_recording_failure(&report, "ended_at_ns");
    }

    #[tokio::test]
    async fn strict_recording_rejects_a_noncurrent_schema_version() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute_batch(
            "DELETE FROM schema_version WHERE version > 4; PRAGMA user_version = 4;",
        )
        .unwrap();
        drop(conn);

        let report = validate_recording(&db_path);

        assert_recording_failure(&report, "schema version");
        assert!(
            report_failures(&report)
                .iter()
                .any(|failure| failure.contains(&LATEST_SCHEMA_VERSION.to_string()))
        );
    }

    #[tokio::test]
    async fn strict_recording_rejects_missing_persist_health_metadata() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        edit_checkpoint(&db_path, "run-1", |value| {
            value["capture_quality"]["analysis"] = serde_json::Value::Null;
        });

        let report = validate_recording(&db_path);

        assert_recording_failure(&report, "persist_dropped");
    }

    #[tokio::test]
    async fn strict_recording_rejects_nonzero_persist_loss() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        edit_checkpoint(&db_path, "run-1", |value| {
            value["capture_quality"]["analysis"]["persist_dropped"] = json!(1);
        });

        let report = validate_recording(&db_path);

        assert_recording_failure(&report, "persist_dropped");
        // quality_status cannot prove capture health without producer sources,
        // so the actual quality stays unknown while the loss itself is rejected.
        assert_eq!(report.runs[0].capture_quality.as_deref(), Some("unknown"));
    }

    #[tokio::test]
    async fn strict_recording_rejects_a_failed_persist_flush() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        edit_checkpoint(&db_path, "run-1", |value| {
            value["capture_quality"]["analysis"]["persist_flush_failed"] = json!(true);
        });

        let report = validate_recording(&db_path);

        assert_recording_failure(&report, "persist_flush_failed");
    }

    #[tokio::test]
    async fn strict_recording_ignores_provisional_evidence_and_alerts() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute(
            "INSERT INTO correlation_evidence
             (evidence_id, intent_id, action_id, match_level, confidence,
              matched_fields_json, derivation_mode, phase, timestamp_ns, run_id)
             SELECT 'prov-e1', intent_id, action_id, match_level, confidence,
                    matched_fields_json, derivation_mode,
                    'Provisional { timestamp_ns: 1 }', timestamp_ns, run_id
             FROM correlation_evidence LIMIT 1",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO responsibility_alerts
             (alert_id, timestamp_ns, display_profile, decision, severity, confidence,
              authority, reasoning, title, summary, action_hint, primary_agent_id,
              involved_agents_json, engine_section_json, timeline_json,
              graph_metrics_json, phase, provisional_ref, run_id)
             SELECT 'prov-a1', timestamp_ns, display_profile, decision, severity, confidence,
                    authority, reasoning, title, summary, action_hint, primary_agent_id,
                    involved_agents_json, engine_section_json, timeline_json,
                    graph_metrics_json, 'Provisional { timestamp_ns: 1 }', provisional_ref, run_id
             FROM responsibility_alerts LIMIT 1",
            [],
        )
        .unwrap();
        drop(conn);

        let report = validate_recording(&db_path);

        assert_eq!(report.status, "ok", "report: {report:?}");
    }

    #[tokio::test]
    async fn strict_recording_rejects_absent_persisted_run_metrics() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute(
            "DELETE FROM analysis_checkpoints WHERE checkpoint_kind=?1",
            params![RUN_METRICS_KIND],
        )
        .unwrap();
        drop(conn);

        let report = validate_recording(&db_path);

        assert_recording_failure(&report, "run-metrics");
    }

    #[tokio::test]
    async fn strict_recording_rejects_mutated_persisted_run_metrics() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        edit_stored_checkpoint(&db_path, "run-1", RUN_METRICS_KIND, |value| {
            value["run_id"] = json!("run-2");
        });

        let report = validate_recording(&db_path);

        assert_recording_failure(&report, "run-metrics");
    }

    #[tokio::test]
    async fn strict_recording_accepts_f32_equivalent_run_metrics_json() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        edit_stored_checkpoint(&db_path, "run-1", RUN_METRICS_KIND, |value| {
            let breakdown = value["score_breakdown"].as_object_mut().unwrap();
            let key = breakdown
                .iter()
                .find_map(|(key, item)| {
                    item.as_f64()
                        .filter(|number| *number != 0.0)
                        .map(|number| (key.clone(), number))
                })
                .map(|(key, _)| key)
                .expect("fixture has a nonzero f32");
            let original = breakdown.get(&key).unwrap().as_f64().unwrap();
            // Same f32, different f64 spelling: this is not a semantic change.
            let nudged = original * (1.0 + 1e-12);
            assert_eq!((nudged as f32).to_bits(), (original as f32).to_bits());
            breakdown.insert(key, json!(nudged));
        });

        let report = validate_recording(&db_path);

        assert_eq!(report.status, "ok", "report: {report:?}");
    }

    #[tokio::test]
    async fn strict_recording_rejects_a_semantically_changed_run_metric() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        edit_stored_checkpoint(&db_path, "run-1", RUN_METRICS_KIND, |value| {
            let breakdown = value["score_breakdown"].as_object_mut().unwrap();
            let key = breakdown
                .iter()
                .find_map(|(key, item)| {
                    item.as_f64()
                        .filter(|number| *number != 0.0)
                        .map(|number| (key.clone(), number))
                })
                .map(|(key, _)| key)
                .expect("fixture has a nonzero f32");
            let original = breakdown.get(&key).unwrap().as_f64().unwrap();
            breakdown.insert(key, json!(original + 1e-5));
        });

        let report = validate_recording(&db_path);

        assert_recording_failure(&report, "run-metrics");
    }

    #[tokio::test]
    async fn strict_recording_rejects_absent_persisted_attributions() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute(
            "DELETE FROM analysis_checkpoints WHERE checkpoint_kind=?1",
            params![ATTRIBUTION_RESULTS_KIND],
        )
        .unwrap();
        drop(conn);

        let report = validate_recording(&db_path);

        assert_recording_failure(&report, "attribution-results");
    }

    #[tokio::test]
    async fn strict_recording_rejects_mutated_persisted_attributions() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        edit_stored_checkpoint(&db_path, "run-1", ATTRIBUTION_RESULTS_KIND, |value| {
            value["run_id"] = json!("run-2");
        });

        let report = validate_recording(&db_path);

        assert_recording_failure(&report, "attribution-results");
    }

    #[tokio::test]
    async fn strict_recording_rejects_one_invalid_run_in_a_multi_run_database() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute(
            "INSERT INTO runs (run_id, started_at_ns, ended_at_ns)
             VALUES ('run-2', 500, 600)",
            [],
        )
        .unwrap();
        drop(conn);

        let report = validate_recording(&db_path);

        assert!(!report.is_ok());
        assert_eq!(report.runs.len(), 2);
        assert!(
            report
                .runs
                .iter()
                .any(|run| run.run_id == "run-1" && run.status == "ok")
        );
        assert!(
            report
                .runs
                .iter()
                .any(|run| run.run_id == "run-2" && run.status == "invalid")
        );
        assert_recording_failure(&report, "run-2");
    }

    #[tokio::test]
    async fn strict_recording_is_read_only_and_never_writes_a_replay_report() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_valid_recording_db(&dir).await;
        let run_dir = db_path.parent().unwrap().parent().unwrap().to_path_buf();
        let before = std::fs::read(&db_path).unwrap();
        let args = Args::try_parse_from([
            "replay",
            "--run-dir",
            run_dir.to_string_lossy().as_ref(),
            "--validate-recording",
        ])
        .unwrap();

        let report = validate_recording_from_args(&args);

        assert!(report.is_ok(), "failures: {:?}", report_failures(&report));
        assert!(!run_dir.join("replay.report.json").exists());
        assert_eq!(std::fs::read(&db_path).unwrap(), before);
    }

    #[test]
    fn strict_recording_conflicts_with_identity_and_attribution_outputs() {
        let error = Args::try_parse_from([
            "replay",
            "--sqlite",
            "run.db",
            "--validate-recording",
            "--identity-output",
            "identity.json",
        ])
        .unwrap_err();
        assert_eq!(error.kind(), clap::error::ErrorKind::ArgumentConflict);

        let error = Args::try_parse_from([
            "replay",
            "--sqlite",
            "run.db",
            "--validate-recording",
            "--attribution-output",
            "attribution.json",
        ])
        .unwrap_err();
        assert_eq!(error.kind(), clap::error::ErrorKind::ArgumentConflict);

        let error = Args::try_parse_from([
            "replay",
            "--sqlite",
            "run.db",
            "--validate-recording",
            "--ablation-output",
            "ablation.json",
        ])
        .unwrap_err();
        assert_eq!(error.kind(), clap::error::ErrorKind::ArgumentConflict);

        let error = Args::try_parse_from([
            "replay",
            "--sqlite",
            "run.db",
            "--identity-output",
            "identity.json",
            "--ablation-output",
            "ablation.json",
        ])
        .unwrap_err();
        assert_eq!(error.kind(), clap::error::ErrorKind::ArgumentConflict);

        let error = Args::try_parse_from([
            "replay",
            "--sqlite",
            "run.db",
            "--ablation-review-cache",
            "cache.json",
        ])
        .unwrap_err();
        assert_eq!(
            error.kind(),
            clap::error::ErrorKind::MissingRequiredArgument
        );

        let error = Args::try_parse_from([
            "replay",
            "--sqlite",
            "run.db",
            "--ablation-output",
            "ablation.json",
            "--attribution-output",
            "attribution.json",
            "--ablation-review-cache",
            "cache.json",
        ])
        .unwrap_err();
        assert_eq!(error.kind(), clap::error::ErrorKind::ArgumentConflict);

        let args = Args::try_parse_from([
            "replay",
            "--sqlite",
            "run.db",
            "--ablation-output",
            "ablation.json",
            "--ablation-review-cache",
            "cache.json",
        ])
        .unwrap();
        assert!(args.ablation_review_cache.is_some());

        let error = Args::try_parse_from([
            "replay",
            "--sqlite",
            "run.db",
            "--ablation-review-live",
            "live.json",
        ])
        .unwrap_err();
        assert_eq!(
            error.kind(),
            clap::error::ErrorKind::MissingRequiredArgument
        );

        let error = Args::try_parse_from([
            "replay",
            "--sqlite",
            "run.db",
            "--execute",
            "--ablation-review-live",
            "live.json",
            "--ablation-output",
            "ablation.json",
        ])
        .unwrap_err();
        assert_eq!(error.kind(), clap::error::ErrorKind::ArgumentConflict);

        let args = Args::try_parse_from([
            "replay",
            "--sqlite",
            "run.db",
            "--execute",
            "--ablation-review-live",
            "live.json",
        ])
        .unwrap();
        assert_eq!(args.ablation_review_live.as_deref(), Some("live.json"));

        let args =
            Args::try_parse_from(["replay", "--sqlite", "run.db", "--validate-recording"]).unwrap();
        assert!(args.validate_recording);
    }

    fn claim_with_traceback(claim_id: &str, traceback: Option<&str>) -> ResponsibilityClaim {
        use mtagentlens::responsibility::types::{
            AgentId, AlertSeverity, BoundaryId, ClaimId, PhaseIndicator, PolicyId,
            ResponsibilityClaimState, ResponsibilityClaimType, TracebackId,
        };
        ResponsibilityClaim {
            claim_id: ClaimId::from(claim_id),
            claim_type: ResponsibilityClaimType::PolicyViolation,
            subject: AgentId::from("agent-1"),
            policy_id: PolicyId::from("policy-1"),
            boundary_id: BoundaryId::from("boundary-1"),
            claim_state: ResponsibilityClaimState::Observed,
            severity: AlertSeverity::Warning,
            confidence: 0.5,
            evidence_refs: vec![],
            competing_explanations: vec![],
            traceback_ref: traceback.map(TracebackId::from),
            phase: PhaseIndicator::Provisional { timestamp_ns: 1 },
        }
    }

    fn traceback_for(claim_id: &str, traceback_id: &str) -> ResponsibilityTraceback {
        use mtagentlens::responsibility::types::{ClaimId, EdgeId, NodeId, TracebackId};
        ResponsibilityTraceback {
            traceback_id: TracebackId::from(traceback_id),
            claim_id: ClaimId::from(claim_id),
            chain_node_ids: Vec::<NodeId>::new(),
            chain_edge_ids: Vec::<EdgeId>::new(),
            root_cause_node_id: NodeId::from("node-1"),
            explanation_summary: "fixture".into(),
            confidence: 0.5,
        }
    }

    fn attribution_ref(claim_id: &str, traceback_ref: Option<&str>) -> AttributionResults {
        use mtagentlens::responsibility::claims::attribution::ATTRIBUTION_SCHEMA;
        serde_json::from_value(json!({
            "schema": ATTRIBUTION_SCHEMA,
            "run_id": "run-1",
            "claims": [{
                "schema": ATTRIBUTION_SCHEMA,
                "run_id": "run-1",
                "claim_id": claim_id,
                "traceback_ref": traceback_ref,
                "executors": [],
                "occurrences": [],
                "edges": [],
                "source_entry_candidates": [],
                "unique_entry": null,
                "root_groups": [],
                "ranked_agents": null,
                "abstentions": [],
                "limitations": [],
                "truncated": false
            }]
        }))
        .unwrap()
    }

    #[test]
    fn strict_recording_requires_claim_traceback_and_attribution_closure() {
        let claims = vec![claim_with_traceback("claim-1", Some("tb-1"))];
        let tracebacks = vec![traceback_for("claim-1", "tb-1")];
        let attributions = attribution_ref("claim-1", Some("tb-1"));
        claim_traceback_closure(&claims, &tracebacks, &attributions).unwrap();

        let wrong_claim = vec![claim_with_traceback("claim-2", Some("tb-1"))];
        let error = claim_traceback_closure(&wrong_claim, &tracebacks, &attributions).unwrap_err();
        assert!(error.contains("claim"), "{error}");

        let wrong_ref = attribution_ref("claim-1", Some("tb-2"));
        let error = claim_traceback_closure(&claims, &tracebacks, &wrong_ref).unwrap_err();
        assert!(error.contains("attribution"), "{error}");
    }

    fn sha256_of_bytes(bytes: &[u8]) -> String {
        use sha2::{Digest, Sha256};
        hex::encode(Sha256::digest(bytes))
    }

    #[test]
    fn identity_output_binds_the_actual_source_database_sha256() {
        let dir = tempfile::tempdir().unwrap();
        let run_dir = seed_live_db(&dir);
        let db_path = run_dir
            .join("collector")
            .join("collector.responsibility.db");
        let output = dir.path().join("identity-experiment.json");
        let before = std::fs::read(&db_path).unwrap();

        let payload = write_identity_output(&db_path, &output);

        assert_eq!(payload["source_database_sha256"], sha256_of_bytes(&before));
        assert_eq!(std::fs::read(&db_path).unwrap(), before);
        assert!(
            std::fs::read_to_string(&output)
                .unwrap()
                .contains(&sha256_of_bytes(&before))
        );
    }

    #[test]
    fn attribution_output_binds_the_actual_source_database_sha256() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = seed_attribution_db(&dir);
        let output = dir.path().join("attribution-experiment.json");
        let before = std::fs::read(&db_path).unwrap();

        let payload = write_attribution_experiment(&db_path, &output).unwrap();

        assert_eq!(payload["source_database_sha256"], sha256_of_bytes(&before));
        assert_eq!(std::fs::read(&db_path).unwrap(), before);
        assert!(
            std::fs::read_to_string(&output)
                .unwrap()
                .contains(&sha256_of_bytes(&before))
        );
    }
}
