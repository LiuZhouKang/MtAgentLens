"""Real harmless subprocesses supply native lifecycle evidence, without an API."""

import json
import os
import sys
from pathlib import Path

import pytest

from mtagentlens_benchmark.engine import executor
from mtagentlens_benchmark.types import AgentSpec, ScenarioConfig, ScenarioMode


def run_native(tmp_path, monkeypatch, *, timeout=3, missing=False, count=1, client_body=""):
    binary = tmp_path / "test-client"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json,os,time,sys\n"
        "from pathlib import Path\n"
        "time.sleep(.1)\n"
        "print(json.dumps({'audit_visible':Path('runner-events.jsonl').exists(),"
        "'truth_env':[k for k in os.environ if k.startswith('MTAGENTLENS_TRUTH')]}))\n"
        + client_body
    )
    binary.chmod(0o700)
    monkeypatch.setattr(executor, "_find_claude_binary_with_diagnostics",
                        lambda: (str(tmp_path / "missing" if missing else binary), {}))
    monkeypatch.setattr(executor, "_apply_benchmark_env", lambda *args: None)
    monkeypatch.setattr(executor, "_capture_agent_artifacts", lambda *args: None)
    monkeypatch.setattr(executor, "Path", lambda value: tmp_path / "legacy-pid"
                        if value == "/tmp/mtagentlens-agent-pid" else Path(value))
    monkeypatch.setattr(executor.os, "getuid", lambda: 1000)
    monkeypatch.delenv("MTAGENTLENS_CLEAN_PROFILE", raising=False)
    run = tmp_path / "run"
    for name in ("logs", "status"):
        (run / name).mkdir(parents=True)
    config = ScenarioConfig(id="scenario", mode=ScenarioMode.SINGLE_AGENT, description="native test",
        agents=[AgentSpec(role_file="same-label", task="harmless") for _ in range(count)],
        fixtures_dir=tmp_path, timeout_seconds=timeout)
    success, summary = executor._run_process_mode(
        config, run, run / "status", run / "logs", run_id="actual-run-id")
    rows = [json.loads(line) for line in (run / "runner-events.jsonl").read_text().splitlines()]
    return success, summary, rows, run


def test_native_launch_is_recorded_without_conversation_or_detector_ids(tmp_path, monkeypatch):
    success, summary, rows, run = run_native(tmp_path, monkeypatch, count=2)
    assert success
    assert summary["source_run_uuid"] == rows[0]["source_run_uuid"]
    assert {row["run_id"] for row in rows} == {"actual-run-id"}
    assert {row["scenario_id"] for row in rows} == {"scenario"}
    assert rows[0]["event_type"] == "run_started"
    assert rows[-1]["event_type"] == "run_finished"
    assert rows[-1]["details"]["state"] == "completed"
    assert len({row["event_id"] for row in rows}) == len(rows)
    starts = [row for row in rows if row["event_type"] == "process_started"]
    assert {row["actor_id"] for row in starts} == {"runner-agent-0", "runner-agent-1"}
    for row in starts:
        key = row["process_key"]
        assert key["pid"] > 0
        assert key["start_ticks"] is None or key["start_ticks"] > 0
        assert key["ticks_per_second"] == os.sysconf("SC_CLK_TCK")
        sample = row["details"]["observation"]
        assert sample["schema"] == "mtagentlens.process-observation/v1"
        attempt = next(item for item in rows if item["event_type"] == "launch_attempt"
                       and item["actor_id"] == row["actor_id"])
        assert attempt["timestamp_ns"] <= sample["sample_before_ns"] <= sample["sample_after_ns"] <= row["timestamp_ns"]
        assert sample["status"] == "observed"
        assert sample["target"]["start_ticks_before"] == sample["target"]["start_ticks_after"] == key["start_ticks"]
    assert any(row["process_key"]["start_ticks"] is not None for row in starts)
    for index in range(2):
        stdout = (run / "logs" / f"agent_{index}.log").read_text().splitlines()[0]
        assert json.loads(stdout) == {"audit_visible": False, "truth_env": []}
    assert not any("conversation_id" in row or "session_id" in row for row in rows)
    assert {row["schema"] for row in rows} == {"mtagentlens.runner-event/v4"}
    assert all(row["producer"].startswith("process-audit/4@sha256:") for row in rows)
    for row in starts:
        attempt = next(item for item in rows if item["event_type"] == "launch_attempt"
                       and item["actor_id"] == row["actor_id"])
        witness = attempt["details"]["runner_context"]
        sample = row["details"]["observation"]
        assert witness["schema"] == "mtagentlens.runner-context/v1"
        assert witness["status"] == "observed"
        assert witness["sample_before_ns"] <= witness["sample_after_ns"] <= attempt["timestamp_ns"]
        assert attempt["timestamp_ns"] <= sample["sample_before_ns"]
        assert witness["observer"]["pid"] == sample["observer"]["pid"]
        assert witness["observer"]["time_offsets_ns"] == sample["observer"]["time_offsets_ns"]
        assert witness["observer"]["namespaces"] == sample["observer"]["namespaces"]


def test_unavailable_runner_clock_witness_is_recorded_without_blocking_the_launch(tmp_path, monkeypatch):
    from mtagentlens_benchmark.engine import process_context

    monkeypatch.setattr(
        executor, "observe_runner",
        lambda: process_context.observe_runner(proc_root=tmp_path / "absent"),
    )
    success, _, rows, _ = run_native(tmp_path, monkeypatch)
    assert success
    attempt = next(row for row in rows if row["event_type"] == "launch_attempt")
    witness = attempt["details"]["runner_context"]
    assert witness["status"] == "unavailable"
    assert witness["boot_id"] is None
    assert witness["sample_before_ns"] <= witness["sample_after_ns"] <= attempt["timestamp_ns"]


@pytest.mark.parametrize("missing", [False, True])
def test_timeout_and_missing_binary_remain_in_the_ledger(tmp_path, monkeypatch, missing):
    success, _, rows, _ = run_native(tmp_path, monkeypatch, timeout=.01, missing=missing)
    assert not success
    types = [row["event_type"] for row in rows]
    assert "launch_attempt" in types
    assert ("launch_failed" if missing else "process_timeout") in types
    assert rows[-1]["event_type"] == "run_finished"


def test_existing_ledger_blocks_launch_without_overwriting(tmp_path):
    from mtagentlens_benchmark.engine.process_audit import ProcessAudit
    path = tmp_path / "runner-events.jsonl"
    path.write_text("existing history")
    with pytest.raises(ValueError):
        with ProcessAudit(path, run_id="run", scenario_id="scenario"):
            pytest.fail("must refuse before launch")
    assert path.read_text() == "existing history"


@pytest.mark.parametrize("run_id,scenario_id", [(" ", "scenario"), ("run", ""), (None, "scenario")])
def test_invalid_audit_context_is_rejected_before_execution(tmp_path, run_id, scenario_id):
    from mtagentlens_benchmark.engine.process_audit import ProcessAudit
    with pytest.raises(ValueError):
        with ProcessAudit(tmp_path / "runner-events.jsonl", run_id=run_id, scenario_id=scenario_id):
            pytest.fail("invalid context must not launch")
    assert not (tmp_path / "runner-events.jsonl").exists()


@pytest.mark.parametrize("unavailable", [False, True])
def test_process_key_compatibility_view_preserves_observation_unknowns(monkeypatch, unavailable):
    from mtagentlens_benchmark.engine import process_audit

    key = {"pid": 4242, "start_ticks": None if unavailable else 22,
           "boot_id": None, "ticks_per_second": os.sysconf("SC_CLK_TCK")}
    monkeypatch.setattr(process_audit, "observe_process", lambda pid: {
        "process_key": key, "observation": {"status": "unavailable" if unavailable else "observed"}})
    assert process_audit.process_key(4242) == key


def test_post_launch_log_failure_is_not_mislabeled_as_binary_missing(tmp_path, monkeypatch):
    original = executor._write_status

    def failing_status(path, status):
        if status.get("state") == "done":
            raise FileNotFoundError("simulated post-launch output failure")
        return original(path, status)

    monkeypatch.setattr(executor, "_write_status", failing_status)
    with pytest.raises(FileNotFoundError):
        run_native(tmp_path, monkeypatch)
    rows = [json.loads(line) for line in (tmp_path / "run/runner-events.jsonl").read_text().splitlines()]
    assert "launch_failed" not in {row["event_type"] for row in rows}
    assert rows[-1]["details"]["state"] == "interrupted"


@pytest.mark.parametrize("outcome", ["exit", "timeout", "missing"])
def test_actual_runner_cli_bundle_and_annotation_queue_preserve_unknowns(tmp_path, monkeypatch, outcome):
    import subprocess
    from tools.annotator.evidence import load_bundle
    from tools.annotator.records import RecordStore
    from tools.annotator.tests.test_evidence import fixture_inputs

    _, summary, _, run = run_native(tmp_path, monkeypatch,
        timeout=.01 if outcome == "timeout" else 3, missing=outcome == "missing")
    spec = fixture_inputs(tmp_path)
    bundle = tmp_path / "bundle"
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, "-m", "tools.annotator", "import-runner",
        "--ledger", str(run / "runner-events.jsonl"), "--release", str(spec["release_root"]),
        "--codebook-version", "pilot/1", "--out", str(bundle)],
        cwd=root, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    manifest = load_bundle(bundle)
    assert manifest["binding"]["source_run_uuid"] == summary["source_run_uuid"]
    assert manifest["binding"]["run_id"] == "actual-run-id"
    store = RecordStore(tmp_path / "records.sqlite")
    task = store.register_bundle(bundle)
    assert store.export()["tasks"][0]["revisions"] == []
    unknown = {name: {"value": "unknown", "evidence_refs": []}
               for name in ("attempted", "executed", "succeeded")}
    unknown.update(identities={event["event_id"]: None for event in manifest["events"]},
                   roots={"actors": None, "evidence_refs": []}, notes="Automated native-source regression")
    store.save(task, "regression", unknown, method="controlled_execution", expected_revision=None)
    export = store.export()
    assert export["paper_metrics_available"] is False
    assert export["tasks"][0]["revisions"][0]["annotation"]["succeeded"]["value"] == "unknown"
