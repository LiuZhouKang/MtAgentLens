"""Run records are checked against finalized SQLite data, not trusted labels."""
import hashlib
import json
import sqlite3

import pytest

from scripts.evaluation import final_verdict_metrics as metrics


def database(path, *, wal=False):
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE runs(run_id TEXT, final_decision TEXT, final_severity TEXT)")
    conn.execute("INSERT INTO runs VALUES('r', 'Safe', 'Info')")
    conn.commit()
    return conn


def record(path):
    from scripts.evaluation.run_artifacts import freeze_runtime_inputs
    collector = path.parent / "mtagentlens"
    collector.write_bytes(b"synthetic collector")
    runtime, digest = freeze_runtime_inputs(path.parent / "source", path.parent / "artifacts", {})
    return {"scenario_id": "s", "run_id": "r", "release_id": "release",
        "scenario_sha256": "scenario", "collector_sha256": hashlib.sha256(collector.read_bytes()).hexdigest(),
        "collector_binary": str(collector), "runtime_inputs": str(runtime), "configuration_sha256": digest,
        "db_path": str(path), "db_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "capture_quality_status": "unknown", "final_decision": "Safe", "final_severity": "Info", "decision_source": "runs.final_decision"}


def release():
    return {"release_id": "release", "case_bindings": {"s": {"scenario_sha256": "scenario"}},
            "case_labels": {"s": "benign"}}


def test_record_cannot_override_canonical_database_verdict(tmp_path):
    path = tmp_path / "run.db"
    database(path).close()
    row = {**record(path), "final_decision": "Unsafe"}
    with pytest.raises(ValueError, match="(?i)(canonical|verdict)"):
        metrics.validate_run_artifacts([row], release())


def test_missing_database_cannot_enter_release_metrics():
    row = {"scenario_id": "s", "run_id": "r", "release_id": "release",
        "scenario_sha256": "scenario", "collector_sha256": "a" * 64, "final_decision": "Unsafe"}
    with pytest.raises(ValueError, match="(?i)database"):
        metrics.validate_run_artifacts([row], release())


def test_duplicate_runs_are_rejected_without_optional_audits(tmp_path):
    path = tmp_path / "run.db"
    database(path).close()
    row = record(path)
    with pytest.raises(ValueError, match="(?i)duplicate"):
        metrics.validate_run_artifacts([row, row], release())


def test_snapshot_includes_committed_wal_and_has_stable_hash(tmp_path):
    from scripts.evaluation.run_artifacts import snapshot_database, read_canonical_verdict
    source = tmp_path / "source.db"
    conn = database(source, wal=True)
    destination = tmp_path / "final.db"
    snapshot_database(source, destination)
    conn.close()
    assert read_canonical_verdict(destination)["final_decision"] == "Safe"
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    read_canonical_verdict(destination)
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == digest
    assert not destination.with_name(destination.name + "-wal").exists()


def test_release_metrics_reject_database_with_unbound_wal(tmp_path):
    path = tmp_path / "run.db"
    conn = database(path, wal=True)
    try:
        with pytest.raises(ValueError, match="(?i)(wal|snapshot)"):
            metrics.validate_run_artifacts([record(path)], release())
    finally:
        conn.close()


def test_readiness_returns_blocker_without_observed_run_corpus():
    from compare.scripts.audit.audit_paper_experiment_readiness import _check_frozen_dataset, DEFAULT_DATASET
    check = _check_frozen_dataset(DEFAULT_DATASET)
    assert check.status == "blocker"
    unknown = check.evidence["unknown_runs"]
    assert isinstance(unknown, int) and 0 <= unknown <= 93


def test_runtime_freeze_excludes_model_tree_and_inference_server(tmp_path):
    from scripts.evaluation.run_artifacts import freeze_runtime_inputs, validate_runtime_inputs
    project = tmp_path / "project"
    config = project / "env/config/observer.toml"
    config.parent.mkdir(parents=True)
    config.write_text('model="fixture-only"\nauth_token="synthetic-secret"\n')
    model = project / "collector/models/deberta/model.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"synthetic model")
    server = project / "collector/models/inference_server.py"
    server.write_text("# synthetic ONNX server\n")
    root, digest = freeze_runtime_inputs(
        project, tmp_path / "artifacts", {"ANTHROPIC_AUTH_TOKEN": "synthetic-secret"})
    validate_runtime_inputs(root, digest)
    assert root.stat().st_mode & 0o077 == 0
    assert "synthetic-secret" not in (root / "runtime.json").read_text()
    assert (root / "env/config/observer.toml").read_text() == config.read_text()
    assert not (root / "collector/models").exists(), \
        "the frozen runtime must not copy an ONNX model tree or inference server"
    manifest = json.loads((root / "runtime.json").read_text())
    assert not any("collector/models" in path for path in manifest["files"])

    # Neither source mutation nor an explicit model directory may re-enter the
    # frozen runtime: the current product has no model input.
    model.write_bytes(b"different model")
    server.write_text("# changed ONNX server\n")
    external = tmp_path / "external/deberta"
    external.mkdir(parents=True)
    (external / "model.onnx").write_bytes(b"external model")
    (external.parent / "inference_server.py").write_text("# external server\n")
    second, second_digest = freeze_runtime_inputs(
        project, tmp_path / "artifacts2",
        {"ANTHROPIC_AUTH_TOKEN": "synthetic-secret", "MTAGENTLENS_MODEL_DIR": str(external)})
    validate_runtime_inputs(root, digest)
    validate_runtime_inputs(second, second_digest)
    assert not (second / "collector/models").exists(), \
        "MTAGENTLENS_MODEL_DIR must not inject a model into the frozen runtime"


@pytest.mark.parametrize("setting", [
    "MTAGENTLENS_OBSERVER_MODEL", "ANTHROPIC_MODEL",
    "MTAGENTLENS_AGENT_COMMS", "MTAGENTLENS_AGENT_COMM",
    "MTAGENTLENS_AGENT_CGROUP", "MTAGENTLENS_AGENT_EXE",
])
def test_runtime_manifest_binds_model_and_identity_settings(tmp_path, setting):
    from scripts.evaluation.run_artifacts import freeze_runtime_inputs, validate_runtime_inputs

    first, original = freeze_runtime_inputs(tmp_path / "source", tmp_path / "a", {setting: "first"})
    second, changed = freeze_runtime_inputs(tmp_path / "source", tmp_path / "b", {setting: "second"})
    assert original != changed, f"changing {setting} must change the recorded configuration"
    validate_runtime_inputs(first, original)
    validate_runtime_inputs(second, changed)
    manifest = json.loads((first / "runtime.json").read_text())
    assert setting in manifest["settings_sha256"]
    assert manifest["settings_sha256"][setting] != "first"


def test_same_agent_binary_path_with_changed_bytes_changes_configuration_hash(tmp_path):
    from scripts.evaluation.run_artifacts import freeze_runtime_inputs, validate_runtime_inputs

    binary = tmp_path / "synthetic-agent"
    binary.write_bytes(b"synthetic agent v1")
    settings = {"MTAGENTLENS_AGENT_BINARY": str(binary)}
    first, original = freeze_runtime_inputs(tmp_path / "source", tmp_path / "a", settings)
    binary.write_bytes(b"synthetic agent v2")
    second, changed = freeze_runtime_inputs(tmp_path / "source", tmp_path / "b", settings)
    assert original != changed, "agent bytes at the same path must change the configuration hash"
    first_manifest = json.loads((first / "runtime.json").read_text())
    second_manifest = json.loads((second / "runtime.json").read_text())
    assert first_manifest["agent_binary_sha256"] == hashlib.sha256(b"synthetic agent v1").hexdigest()
    assert second_manifest["agent_binary_sha256"] == hashlib.sha256(b"synthetic agent v2").hexdigest()
    assert "MTAGENTLENS_AGENT_BINARY" in first_manifest["settings_sha256"]
    validate_runtime_inputs(first, original)
    validate_runtime_inputs(second, changed)


@pytest.mark.parametrize("kind", ["missing", "symlink", "directory", "unreadable"])
def test_explicit_agent_binary_must_be_regular_before_creating_artifacts(tmp_path, kind):
    from scripts.evaluation.run_artifacts import freeze_runtime_inputs

    binary = tmp_path / "synthetic-agent"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_bytes(b"synthetic agent")
        binary.symlink_to(target)
    elif kind == "directory":
        binary.mkdir()
    elif kind == "unreadable":
        import os
        if os.geteuid() == 0:
            pytest.skip("root can read a mode-000 file")
        binary.write_bytes(b"synthetic agent")
        binary.chmod(0)
    output = tmp_path / "artifacts"
    with pytest.raises(ValueError, match="(?i)agent binary"):
        freeze_runtime_inputs(tmp_path, output, {"MTAGENTLENS_AGENT_BINARY": str(binary)})
    assert not output.exists()


def test_agent_binary_digest_survives_source_deletion_and_detects_current_mutation(tmp_path):
    from scripts.evaluation.run_artifacts import (
        freeze_runtime_inputs, validate_runtime_agent_binary, validate_runtime_inputs,
    )

    binary = tmp_path / "synthetic-agent"
    binary.write_bytes(b"synthetic agent v1")
    root, digest = freeze_runtime_inputs(
        tmp_path / "source", tmp_path / "artifacts", {"MTAGENTLENS_AGENT_BINARY": str(binary)})
    validate_runtime_agent_binary(root, binary)
    binary.unlink()
    # The immutable manifest proves the frozen bytes without the original file.
    validate_runtime_inputs(root, digest)
    with pytest.raises(ValueError, match="(?i)agent binary"):
        validate_runtime_agent_binary(root, binary)


def test_current_agent_binary_check_rejects_changed_bytes(tmp_path):
    from scripts.evaluation.run_artifacts import freeze_runtime_inputs, validate_runtime_agent_binary

    binary = tmp_path / "synthetic-agent"
    binary.write_bytes(b"synthetic agent v1")
    root, _ = freeze_runtime_inputs(
        tmp_path / "source", tmp_path / "artifacts", {"MTAGENTLENS_AGENT_BINARY": str(binary)})
    binary.write_bytes(b"synthetic agent v2")
    with pytest.raises(ValueError, match="(?i)agent binary"):
        validate_runtime_agent_binary(root, binary)


def test_missing_agent_binary_record_is_unavailable_without_a_fake_digest(tmp_path):
    from scripts.evaluation.run_artifacts import freeze_runtime_inputs, validate_runtime_agent_binary

    root, _ = freeze_runtime_inputs(tmp_path / "source", tmp_path / "artifacts", {})
    manifest = json.loads((root / "runtime.json").read_text())
    assert "agent_binary_sha256" not in manifest
    assert validate_runtime_agent_binary(root, tmp_path / "missing-agent") is None


def test_metrics_report_abstention_and_unknown_quality_outside_binary_denominator():
    rows = [{"scenario_id": "a", "final_decision": "Safe", "capture_quality_status": "healthy"},
        {"scenario_id": "b", "final_decision": "Uncertain", "capture_quality_status": "healthy"},
        {"scenario_id": "c", "final_decision": "Safe", "capture_quality_status": "unknown"},
        {"scenario_id": "d", "final_decision": None, "capture_quality_status": "unknown"}]
    report = metrics.summarize(rows, "benign")
    assert report["decided"] == 1
    assert report["safe"] == 1
    assert report["review"] == 1
    assert report["quality_limited_runs"] == 1
    assert report["unknown_verdict"] == 1
    assert report["binary_decision_coverage"] == 0.25


def test_runtime_freezes_explicit_vocabulary_bytes_not_only_its_path(tmp_path):
    from scripts.evaluation.run_artifacts import freeze_runtime_inputs, validate_runtime_inputs

    project = tmp_path / "source"
    project.mkdir()
    vocabulary = project / "custom.toml"
    vocabulary.write_text('authorized_roles = ["first"]\n')
    settings = {"MTAGENTLENS_SCENARIO_PATTERNS": str(vocabulary)}
    first, original = freeze_runtime_inputs(project, tmp_path / "a", settings)
    vocabulary.write_text('authorized_roles = ["second"]\n')
    second, changed = freeze_runtime_inputs(project, tmp_path / "b", settings)
    assert original != changed, "a policy edit at the same path must change the configuration hash"
    assert (first / "collector/patterns/runtime-scenario-patterns.toml").read_text() == 'authorized_roles = ["first"]\n'
    validate_runtime_inputs(first, original)
    validate_runtime_inputs(second, changed)


def test_runtime_rejects_unreadable_explicit_vocabulary_before_creating_artifacts(tmp_path):
    from scripts.evaluation.run_artifacts import freeze_runtime_inputs

    output = tmp_path / "artifacts"
    with pytest.raises(ValueError, match="vocabulary"):
        freeze_runtime_inputs(tmp_path, output, {"MTAGENTLENS_SCENARIO_PATTERNS": "missing.toml"})
    assert not output.exists()


def clean_capture_quality():
    """A loss-free process helper whose consumer saw one event."""
    return {
        "status": "healthy",
        "sources": {
            "Process:4242": {
                "started": True,
                "attached": 1,
                "final_stats_seen": True,
                "drain_complete": True,
                "exit_status": 0,
                "lost": 0,
                "rate_limited": 0,
            }
        },
        "analysis": {
            "partial_tls_events": 0,
            "persist_dropped": 0,
            "persist_closed": 0,
            "persist_failures": 0,
            "persist_flush_failed": False,
            "raw_log_failures": 0,
            "event_counts": {"process": 1},
        },
    }


def test_capture_quality_accepts_observed_process_events():
    from scripts.evaluation.run_artifacts import capture_quality_status

    assert capture_quality_status(clean_capture_quality()) == "healthy"


def test_capture_quality_rejects_zero_observation_with_a_configured_process_helper():
    from scripts.evaluation.run_artifacts import capture_quality_status

    quality = clean_capture_quality()
    quality["analysis"]["event_counts"]["process"] = 0
    assert capture_quality_status(quality) == "unknown"


def test_capture_quality_treats_missing_event_counts_as_unknown():
    from scripts.evaluation.run_artifacts import capture_quality_status

    quality = clean_capture_quality()
    del quality["analysis"]["event_counts"]
    assert capture_quality_status(quality) == "unknown"


def test_capture_quality_does_not_gate_ssl_coverage():
    from scripts.evaluation.run_artifacts import capture_quality_status

    quality = clean_capture_quality()
    quality["sources"] = {
        "SSL:4242": {
            "started": True,
            "attached": 1,
            "final_stats_seen": True,
            "drain_complete": True,
            "exit_status": 0,
            "lost": 0,
            "rate_limited": 0,
        }
    }
    quality["analysis"]["event_counts"] = {"ssl": 0}
    assert capture_quality_status(quality) == "healthy"


def test_rows_must_match_the_role_of_the_file_they_came_from(tmp_path):
    """A benign scenario in the attack set silently inflates the attack rate."""
    path = tmp_path / "run.db"
    database(path).close()
    row = record(path)
    with pytest.raises(ValueError, match="(?i)benign"):
        metrics.validate_run_artifacts([row], release(), expected_label="attack")
    metrics.validate_run_artifacts([row], release(), expected_label="benign")


def test_capture_quality_degrades_on_raw_log_write_failures():
    from scripts.evaluation.run_artifacts import capture_quality_status

    quality = clean_capture_quality()
    quality["analysis"]["raw_log_failures"] = 1
    assert capture_quality_status(quality) == "degraded"


def test_capture_quality_degrades_when_a_tls_core_probe_is_missing():
    from scripts.evaluation.run_artifacts import capture_quality_status

    quality = clean_capture_quality()
    quality["sources"]["SSL:4242"] = {
        "started": True,
        "attached": 3,
        "final_stats_seen": True,
        "drain_complete": True,
        "exit_status": 0,
        "lost": 0,
        "rate_limited": 0,
        "core_read": True,
        "core_write": False,
    }
    assert capture_quality_status(quality) == "degraded"


def test_incomplete_initial_snapshot_degrades_canonical_quality():
    from scripts.evaluation.run_artifacts import capture_quality_status
    quality=clean_capture_quality()
    next(iter(quality['sources'].values()))['initial_snapshot_complete']=False
    assert capture_quality_status(quality)=='degraded'


def test_streaming_partial_fragments_do_not_degrade_capture_quality():
    from scripts.evaluation.run_artifacts import capture_quality_status

    quality = clean_capture_quality()
    quality["analysis"]["partial_tls_events"] = 1234
    quality["analysis"]["decode_status"] = {"ok": 5}
    assert capture_quality_status(quality) == "healthy"


def test_decode_failures_degrade_capture_quality():
    from scripts.evaluation.run_artifacts import capture_quality_status

    quality = clean_capture_quality()
    quality["analysis"]["decode_status"] = {"malformed": 1}
    assert capture_quality_status(quality) == "degraded"
    quality["analysis"]["decode_status"] = {"transport_error": 2}
    assert capture_quality_status(quality) == "degraded"


def checkpoint_database(path, *, implementation=None, run_id="r"):
    """A finalized database whose frozen-analysis checkpoint may name its kernel."""
    conn = database(path)
    conn.execute(
        "CREATE TABLE analysis_checkpoints(run_id TEXT, checkpoint_kind TEXT, "
        "version INTEGER, payload_json TEXT)")
    payload = {"run_id": run_id, "contract": "frozen-evidence-reanalysis-v1",
               "capture_quality": {"status": "healthy", "sources": {}, "analysis": {}}}
    if implementation is not None:
        payload["implementation"] = implementation
    conn.execute("INSERT INTO analysis_checkpoints VALUES(?, 'frozen-analysis', 1, ?)",
                 (run_id, json.dumps(payload)))
    conn.commit()
    conn.close()


def test_checkpoint_implementation_is_reported(tmp_path):
    from scripts.evaluation.run_artifacts import read_canonical_verdict

    path = tmp_path / "run.db"
    checkpoint_database(path, implementation="a" * 64)
    assert read_canonical_verdict(path)["checkpoint_implementation"] == "a" * 64


def test_stale_checkpoint_is_rejected_when_the_kernel_is_pinned(tmp_path):
    from scripts.evaluation.run_artifacts import read_canonical_verdict

    path = tmp_path / "run.db"
    checkpoint_database(path, implementation="a" * 64)
    with pytest.raises(ValueError, match="(?i)stale"):
        read_canonical_verdict(path, expected_implementation="b" * 64)


def test_checkpoint_without_fingerprint_is_rejected_when_pinned(tmp_path):
    from scripts.evaluation.run_artifacts import read_canonical_verdict

    path = tmp_path / "run.db"
    checkpoint_database(path)
    with pytest.raises(ValueError, match="(?i)fingerprint"):
        read_canonical_verdict(path, expected_implementation="a" * 64)


def test_unpinned_read_still_accepts_a_legacy_checkpoint(tmp_path):
    from scripts.evaluation.run_artifacts import read_canonical_verdict

    path = tmp_path / "run.db"
    checkpoint_database(path)
    assert read_canonical_verdict(path)["checkpoint_implementation"] is None
