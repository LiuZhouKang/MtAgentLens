# SPDX-License-Identifier: Apache-2.0 OR MIT
"""System-load telemetry must come from a real, completed collector run."""
import pytest
import json
import subprocess
import sys
import os
from pathlib import Path

from scripts.evaluation import system_load as load


def quality():
    return {"sources": {"Process:1": {"lost": 2, "rate_limited": 0,
        "final_stats_seen": True, "drain_complete": True, "exit_status": 0,
        "initial_snapshot_complete": True, "stdout_skipped": 0, "stdout_error": False,
        "stderr_error": False}}, "analysis": {"persist_dropped": 0,
        "persist_closed": 0, "persist_failures": 0, "raw_log_failures": 0,
        "persist_flush_failed": False, "event_counts": {"process": 10}}}


def test_measured_loss_is_preserved_and_degrades_result():
    result = load.telemetry(quality())
    assert result["lost_events"] == 2
    assert result["healthy"] is False


@pytest.mark.parametrize("field", ["lost", "rate_limited", "final_stats_seen", "drain_complete", "exit_status", "initial_snapshot_complete"])
def test_missing_producer_counters_fail_instead_of_becoming_zero(field):
    value = quality()
    del value["sources"]["Process:1"][field]
    with pytest.raises(ValueError, match="telemetry"):
        load.telemetry(value)


def test_missing_persistence_counters_fail():
    value = quality()
    del value["analysis"]["persist_failures"]
    with pytest.raises(ValueError, match="telemetry"):
        load.telemetry(value)


def test_visibility_latency_uses_operation_start_and_first_committed_observation():
    operations = [{"path": "/tmp/one", "started_ns": 1000000}, {"path": "/tmp/two", "started_ns": 2000000}]
    result = load.visibility(operations, {"/tmp/one": 6000000})
    assert result["observed_operations"] == 1
    assert result["controlled_operation_missing_fraction"] == .5
    assert result["missing_operations"] == ["/tmp/two"]
    assert result["latency_ms"]["max"] == 5
    assert "alert" not in result["metric"]


def test_negative_visibility_clock_is_rejected():
    with pytest.raises(ValueError, match="clock"):
        load.visibility([{"path": "x", "started_ns": 10}], {"x": 9})


def test_proc_stat_parser_handles_parentheses_in_comm():
    fields = ["S", "7"] + ["0"] * 22
    fields[11], fields[12], fields[19], fields[21] = "20", "5", "111", "9"
    assert load.parse_proc_stat("42 (a (b)) " + " ".join(fields)) == {
        "pid": 42, "ppid": 7, "ticks": 25, "start_ticks": 111, "rss_pages": 9}


def test_sampler_records_real_pss_and_explicit_phase_boundaries():
    sampler = load.Sampler(os.getpid())
    sampler.sample()
    assert sampler.samples[0]['phase'] == 'startup'
    assert sampler.samples[0]['pss_bytes_sum'] > 0
    sampler.mark_phase('workload')
    sampler.sample()
    phases = sampler.phase_summary()
    assert phases['startup']['sample_count'] >= 2
    assert phases['workload']['sample_count'] >= 2
    assert phases['workload']['sampled_peak_pss_bytes'] > 0
    assert phases['workload']['pss_missing_samples'] == 0


def test_phase_summary_preserves_missing_pss_and_cpu_delta():
    sampler = load.Sampler(1)
    sampler.samples = [
        {'phase': 'workload', 'monotonic_ns': 1_000_000_000,
         'cpu_seconds_lower_bound': 2., 'rss_bytes_sum': 100, 'pss_bytes_sum': None},
        {'phase': 'workload', 'monotonic_ns': 3_000_000_000,
         'cpu_seconds_lower_bound': 3., 'rss_bytes_sum': 200, 'pss_bytes_sum': None},
    ]
    result = sampler.phase_summary()['workload']
    assert result['cpu_seconds_lower_bound'] == 1.
    assert result['average_cpu_cores_lower_bound'] == .5
    assert result['sampled_peak_pss_bytes'] is None
    assert result['pss_missing_samples'] == 2


def test_plan_discloses_real_scope_and_disables_paid_and_tls_paths(tmp_path):
    binary = tmp_path / "mtagentlens"
    binary.write_bytes(b"test")
    binary.chmod(0o700)
    plan = load.make_plan(binary, tmp_path / "out", [1, 4], 20, 100)
    assert plan["scope"] == "process_kernel_runtime_persistence_load"
    assert plan["observer_enabled"] is False
    assert plan["tls_enabled"] is False
    assert "onnx_enabled" not in plan
    assert all("ONNX" not in limit for limit in plan["limits"])
    assert plan["concurrency"] == [1, 4]
    assert plan["binary_sha256"]


def test_real_worker_produces_exact_controlled_open_ledger(tmp_path):
    proc = subprocess.Popen([sys.executable, str(Path(load.__file__).resolve()), "_worker", "mtl-test-worker",
        str(tmp_path / "files"), "2"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    assert json.loads(proc.stdout.readline())["ready"] == proc.pid
    stdout, stderr = proc.communicate("go\n", timeout=5)
    assert proc.returncode == 0, stderr
    records = [json.loads(line) for line in stdout.splitlines()]
    assert len(records) == 2
    assert len({row["path"] for row in records}) == 2
    for record in records:
        assert record["pid"] == proc.pid
        assert record["completed_ns"] >= record["started_ns"] > 0
        assert Path(record["path"]).read_bytes() == b"synthetic system-load payload\n"
