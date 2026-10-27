# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Overhead four-profile plan and execution contracts; no measurement claims."""
import hashlib
import json
import os
import stat
import time
from pathlib import Path

import pytest

from scripts.evaluation import overhead_driver as od


def executable(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def plan(tmp_path, *, workload=None, operations=2, timeout=5, env=None, agent_comm="agentx"):
    binary = executable(tmp_path / "collector-fake", "#!/usr/bin/env python3\nraise SystemExit(99)\n")
    return od.build_plan(binary, tmp_path / "out", workload or ["/bin/true", "--fixed"],
                         operations, timeout, agent_comm=agent_comm, env=env)


def test_plan_has_fixed_counts_profiles_tls_and_no_collector_for_c0(tmp_path):
    p = plan(tmp_path)
    assert p["schema"] == "mtagentlens.overhead-plan/1"
    assert p["concurrency"] == [1, 4, 8, 16, 32]
    assert p["repeats"] == 3
    assert len(p["blocks"]) == 15
    assert len(p["condition_runs"]) == 60
    assert p["workload_instances"] == 732
    assert p["condition_run_count"] == 60
    assert p["workload_instance_count"] == 732
    assert p["execution"]["execute"] is False
    assert p["execution"]["paid_profiles"] == ["C2", "C3"]
    assert p["warmup"] == {"executed": False, "counted_in_blocks": False,
                           "condition_runs": 0, "note": "no warmup run; requires a separate explicit plan"}

    by_profile = {name: [row for row in p["condition_runs"] if row["profile"] == name]
                  for name in od.PROFILES}
    assert {name: len(rows) for name, rows in by_profile.items()} == {"C0": 15, "C1": 15, "C2": 15, "C3": 15}
    assert {tuple(row["collector_flags"]) for row in by_profile["C0"]} == {()}
    assert {tuple(row["collector_flags"]) for row in by_profile["C1"]} == {("--capture-only",)}
    assert {tuple(row["collector_flags"]) for row in by_profile["C2"]} == {("--disable-responsibility-observer",)}
    assert {tuple(row["collector_flags"]) for row in by_profile["C3"]} == {()}
    assert all(row["collector"] is False and row["collector_command"] is None for row in by_profile["C0"])
    assert all(row["collector"] is True and row["collector_command"] is not None for name in od.PROFILES[1:]
               for row in by_profile[name])
    for name in od.PROFILES[1:]:
        assert {tuple(row["collector_command"][1:]) for row in by_profile[name]} == {
            tuple(["trace", *row["collector_flags"], "--ssl=true", "--process=true",
                   "--comm", p["agent_comm"], "--quiet",
                   "-o", str(Path(p["out"]) / row["condition_id"] / "trace.log")])
            for row in by_profile[name]
        }
    assert "--ssl=false" not in json.dumps([row["collector_command"] for row in p["condition_runs"]])

    assert [row["condition_id"] for row in p["condition_runs"][:4]] == [
        "c1-r1-C0", "c1-r1-C1", "c1-r1-C2", "c1-r1-C3"]
    assert p["condition_runs"][-1]["condition_id"] == "c32-r3-C3"
    assert sum(row["concurrency"] for row in p["condition_runs"]) == 732
    frozen = [str(Path("/bin/true").resolve()), "--fixed"]
    assert all(row["workload_argv"] == frozen for row in p["condition_runs"])
    assert p["workload_argv"] == frozen
    assert p["workload_argv"][0] == p["workload_executable"]
    assert all(row["collector_potentially_paid"] is (row["profile"] in ("C2", "C3"))
               for row in p["condition_runs"])
    assert p["blocked_condition_policy"]["order"] == "condition_runs_order"


def test_plan_binds_binary_workload_content_argv_env_and_configuration_hashes(tmp_path, monkeypatch):
    p = plan(tmp_path)
    workload = [str(Path("/bin/true").resolve()), "--fixed"]
    assert p["binary_sha256"] == hashlib.sha256(Path(p["binary"]).read_bytes()).hexdigest()
    assert p["workload_sha256"] == hashlib.sha256(
        json.dumps(workload, separators=(",", ":")).encode()).hexdigest()
    assert p["workload_executable"] == str(Path("/bin/true").resolve())
    assert p["workload_executable_sha256"] == hashlib.sha256(Path("/bin/true").read_bytes()).hexdigest()
    assert len(p["config_sha256"]) == len(p["plan_sha256"]) == len(p["config_env_sha256"]) == 64
    assert p["plan_sha256"] == plan(tmp_path)["plan_sha256"]
    changed = plan(tmp_path, workload=["/bin/true", "--changed"])
    assert changed["workload_sha256"] != p["workload_sha256"]
    assert changed["plan_sha256"] != p["plan_sha256"]

    monkeypatch.setenv("MTL_OBSERVER_PROVIDER", "real-local")
    first = plan(tmp_path)
    assert "real-local" not in json.dumps(first)
    assert "MTL_OBSERVER_PROVIDER" in first["config_env_keys"]
    monkeypatch.setenv("MTL_OBSERVER_PROVIDER", "other-provider")
    assert plan(tmp_path)["config_env_sha256"] != first["config_env_sha256"]

    script = executable(tmp_path / "workload-content", "#!/usr/bin/env python3\n")
    before = od.build_plan(Path(first["binary"]), tmp_path / "out-content", [str(script)],
                           1, agent_comm="agentx")
    script.write_text("#!/usr/bin/env python3\nprint('changed')\n")
    after = od.build_plan(Path(first["binary"]), tmp_path / "out-content", [str(script)],
                          1, agent_comm="agentx")
    assert before["workload_executable_sha256"] != after["workload_executable_sha256"]
    assert before["plan_sha256"] != after["plan_sha256"]


def test_manifest_rehash_rejects_changed_binary_workload_or_plan(tmp_path):
    script = executable(tmp_path / "workload-content", "#!/usr/bin/env python3\n")
    p = od.build_plan(executable(tmp_path / "collector", "#!/usr/bin/env python3\n"),
                      tmp_path / "out", [str(script)], 1, agent_comm="agentx")
    script.write_text("#!/usr/bin/env python3\nprint('changed')\n")
    with pytest.raises(ValueError, match="workload executable content"):
        od.verify_manifest(p)

    p = od.build_plan(executable(tmp_path / "collector2", "#!/usr/bin/env python3\n"),
                      tmp_path / "out2", ["/bin/true"], 1, agent_comm="agentx")
    Path(p["binary"]).write_text("#!/usr/bin/env python3\nprint('changed')\n")
    with pytest.raises(ValueError, match="binary content"):
        od.verify_manifest(p)

    p = od.build_plan(executable(tmp_path / "collector3", "#!/usr/bin/env python3\n"),
                      tmp_path / "out3", ["/bin/true"], 1, agent_comm="agentx")
    p["condition_runs"][0]["concurrency"] = 99
    with pytest.raises(ValueError, match="plan changed"):
        od.verify_manifest(p)


def test_plan_rejects_absent_workload_comm_and_bad_counts(tmp_path):
    binary = executable(tmp_path / "collector-fake", "#!/usr/bin/env python3\n")
    with pytest.raises(ValueError, match="workload"):
        od.build_plan(binary, tmp_path / "out", None, 1, agent_comm="agentx")
    with pytest.raises(ValueError, match="workload"):
        od.build_plan(binary, tmp_path / "out", "/bin/true --fixed", 1, agent_comm="agentx")
    with pytest.raises(ValueError, match="workload"):
        od.build_plan(binary, tmp_path / "out", ["/bin/true", ""], 1, agent_comm="agentx")
    with pytest.raises(ValueError, match="agent-comm"):
        od.build_plan(binary, tmp_path / "out", ["/bin/true"], 1, agent_comm="")
    with pytest.raises(ValueError, match="operations"):
        od.build_plan(binary, tmp_path / "out", ["/bin/true"], 0, agent_comm="agentx")
    with pytest.raises(ValueError, match="timeout"):
        od.build_plan(binary, tmp_path / "out", ["/bin/true"], 1, 0, agent_comm="agentx")


def condition(p, profile="C2", concurrency=1):
    return next(row for row in p["condition_runs"]
                if row["profile"] == profile and row["concurrency"] == concurrency)


def receipt(index, *, receipt_id=None, event=1_000_000, claim=2_000_000, alert=3_000_000,
            input_tokens=3, output_tokens=4, cost=0.01, **extra):
    row = {"receipt_id": receipt_id or f"r{index}", "started_ns": event - 1,
           "completed_ns": alert + 1, "event_ns": event, "claim_ns": claim, "alert_ns": alert,
           "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": cost}
    row.update(extra)
    return row


def test_workload_missing_fraction_is_not_capture_loss_and_missing_is_null(tmp_path):
    p = plan(tmp_path, operations=2)
    got = od.summarize_receipts(p, condition(p), [receipt(1, producer_event_count=999)],
                                now_ns=10_000_000)
    assert got["workload_expected_receipts"] == 2
    assert got["workload_observed_receipts"] == 1
    assert got["workload_missing_receipts"] == 1
    assert got["workload_missing_fraction"] == 0.5
    assert got["workload_completion_basis"] == "controlled_operation_receipts"
    assert "loss_denominator" not in got and "missing_fraction" not in got
    assert got["capture_loss"]["measured"] is False
    assert got["capture_loss"]["applicable"] is True
    assert got["capture_loss"]["missing_fraction"] is None
    assert got["capture_loss"]["status"] == "unsupported_unverified"
    assert got["receipts_complete"] is False
    assert got["token_usage"] == {"input_tokens": None, "output_tokens": None,
                                  "total_tokens": None, "known": False}
    assert got["cost_usd"] is None
    assert got["latency_ms"]["event_to_claim"]["samples"] == 1

    c0 = od.summarize_receipts(p, condition(p, "C0"), [receipt(1)], now_ns=10_000_000)
    assert c0["capture_loss"] == {"applicable": False, "measured": False,
                                  "missing_fraction": None, "status": "not_applicable",
                                  "reason": "C0 has no collector capture"}


@pytest.mark.parametrize("observations", [
    [], [{"path": "/tmp/op1", "source": "collector_tls_capture"}],
    [{"receipt_id": "r1", "source": "workload_stdout"}],
    [{"path": "/tmp/never", "source": "collector_tls_capture"}]])
def test_capture_loss_can_never_be_measured_without_a_real_adapter(tmp_path, observations):
    p = plan(tmp_path, operations=1)
    for rows in ([], [receipt(1, path="/tmp/op1")]):
        got = od.summarize_receipts(p, condition(p), rows, now_ns=10_000_000,
                                    capture_observations=observations)
        assert got["capture_loss"]["measured"] is False
        assert got["capture_loss"]["missing_fraction"] is None
        assert got["capture_loss"]["status"] == "unsupported_unverified"


def test_complete_receipts_sum_tokens_and_cost_only_from_receipts(tmp_path):
    p = plan(tmp_path, operations=2)
    got = od.summarize_receipts(p, condition(p), [receipt(1), receipt(2)], now_ns=10_000_000)
    assert got["workload_missing_receipts"] == 0
    assert got["workload_missing_fraction"] == 0.0
    assert got["receipts_complete"] is True
    assert got["token_usage"] == {"input_tokens": 6, "output_tokens": 8,
                                  "total_tokens": 14, "known": True}
    assert got["cost_usd"] == pytest.approx(0.02)


def test_missing_timings_are_null_and_never_zero(tmp_path):
    p = plan(tmp_path, operations=1)
    got = od.summarize_receipts(p, condition(p), [{"receipt_id": "bare"}], now_ns=10_000)
    assert got["latency_ms"]["event_to_claim"] is None
    assert got["latency_ms"]["claim_to_alert"] is None
    assert got["token_usage"]["known"] is False
    assert got["cost_usd"] is None


@pytest.mark.parametrize("field", ["event_ns", "claim_ns", "alert_ns"])
def test_timestamps_are_validated_not_coerced(tmp_path, field):
    p = plan(tmp_path, operations=1)
    for value in (-1, "1", True, 10_001):
        row = receipt(1)
        row[field] = value
        match = "future" if value == 10_001 else "timestamp"
        with pytest.raises(ValueError, match=match):
            od.summarize_receipts(p, condition(p), [row], now_ns=10_000)


def test_negative_latency_order_and_duplicate_receipts_are_rejected(tmp_path):
    p = plan(tmp_path, operations=2)
    with pytest.raises(ValueError, match="order"):
        od.summarize_receipts(p, condition(p), [receipt(1, event=3_000_000, claim=2_000_000)],
                              now_ns=10_000_000)
    with pytest.raises(ValueError, match="duplicate"):
        od.summarize_receipts(p, condition(p), [receipt(1), receipt(2, receipt_id="r1")],
                              now_ns=10_000_000)
    with pytest.raises(ValueError, match="exceeds"):
        od.summarize_receipts(p, condition(p), [receipt(i) for i in range(3)], now_ns=10_000_000)


def test_percentiles_use_p99_only_at_100_samples_else_p95_and_max(tmp_path):
    p99_plan = plan(tmp_path, operations=100)
    rows = [receipt(i, event=1_000_000, claim=1_000_000 + i * 1_000_000,
                    alert=2_000_000 + i * 1_000_000) for i in range(100)]
    got = od.summarize_receipts(p99_plan, condition(p99_plan), rows, now_ns=10_000_000_000)
    metric = got["latency_ms"]["event_to_claim"]
    assert metric["samples"] == 100
    assert metric["p95"] == 94
    assert metric["p99"] == 98
    assert metric["max"] == 99

    p95_plan = plan(tmp_path, operations=99)
    metric = od.summarize_receipts(p95_plan, condition(p95_plan), rows[:99],
                                   now_ns=10_000_000_000)
    assert metric["latency_ms"]["event_to_claim"]["samples"] == 99
    assert metric["latency_ms"]["event_to_claim"]["p99"] is None
    assert metric["latency_ms"]["event_to_claim"]["p95"] == 94
    assert metric["latency_ms"]["event_to_claim"]["max"] == 98


COLLECTOR_WAIT = """#!/usr/bin/env python3
import hashlib, json, os, signal, sys, time
from pathlib import Path

args = sys.argv[1:]
trace = Path(args[args.index("-o") + 1])
parent = trace.parent
parent.mkdir(parents=True, exist_ok=True)
(parent / "collector.args").write_text(json.dumps(args))
(parent / "collector.env").write_text(os.environ.get("MTL_OBSERVER_PROVIDER", "missing"))
(parent / "collector.cwd").write_text(str(Path.cwd()))
config = Path("env/config/observer.toml")
if config.is_file():
    (parent / "collector.config.sha256").write_text(hashlib.sha256(config.read_bytes()).hexdigest())

def stop(signum, frame):
    (parent / "collector.sigint").write_text(str(signum))
    raise SystemExit(0)

signal.signal(signal.SIGINT, stop)
while True:
    time.sleep(0.02)
"""

WORKLOAD_RECEIPTS = """#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path

index = os.environ["MTL_OVERHEAD_INSTANCE_INDEX"]
Path(f"workload-{index}.json").write_text(json.dumps({
    "argv": sys.argv[1:], "profile": os.environ["MTL_OVERHEAD_PROFILE"],
    "condition": os.environ["MTL_OVERHEAD_CONDITION_ID"],
    "operations": os.environ["MTL_OVERHEAD_OPERATIONS"]}))
time.sleep(0.03)
for item in range(int(os.environ["MTL_OVERHEAD_OPERATIONS"])):
    now = time.time_ns()
    print(json.dumps({"receipt_id": f"{index}-{item}", "started_ns": now,
        "completed_ns": now + 3_000_000, "event_ns": now,
        "claim_ns": now + 1_000_000, "alert_ns": now + 2_000_000,
        "input_tokens": 2, "output_tokens": 3, "cost_usd": 0.0}), flush=True)
"""


def fake_plan(tmp_path, collector=COLLECTOR_WAIT, workload=WORKLOAD_RECEIPTS, *,
              operations=2, timeout=5, env=None):
    binary = executable(tmp_path / "collector-fake", collector)
    workload_path = executable(tmp_path / "workload-fake", workload)
    return od.build_plan(binary, tmp_path / "out", [str(workload_path), "--fixed"],
                         operations, timeout, agent_comm="agentx", env=env)


def test_c0_runs_workload_concurrently_without_starting_collector(tmp_path):
    p = fake_plan(tmp_path)
    row = condition(p, "C0", 4)
    result = od.run_condition(p, row, env=os.environ)
    assert result["status"] == "completed", result
    assert result["scientific_admission"] == "not_claimed"
    assert result["collector"]["started"] is False
    assert len(result["workloads"]) == 4
    assert not list(Path(row["output"]).glob("collector.*"))
    assert result["receipts"]["workload_observed_receipts"] == 8
    assert result["receipts"]["workload_missing_receipts"] == 0
    for index in range(4):
        record = json.loads((Path(row["output"]) / f"workload-{index}.json").read_text())
        assert record["argv"] == ["--fixed"]
        assert record["profile"] == "C0"
        assert record["condition"] == "c4-r1-C0"
        assert record["operations"] == "2"


def test_c1_lifecycle_uses_tls_and_comm_and_reports_unproven_readiness(tmp_path):
    p = fake_plan(tmp_path)
    row = condition(p, "C1", 4)
    result = od.run_condition(p, row, env=os.environ)
    assert result["status"] == "completed", result
    assert result["collector"]["started"] is True
    assert result["collector"]["returncode"] == 0
    assert result["collector"]["attachment_proven"] is False
    assert result["collector"]["readiness"] == "unproven_20ms_probe"
    assert (Path(row["output"]) / "collector.sigint").read_text() == "2"
    args = json.loads((Path(row["output"]) / "collector.args").read_text())
    assert args[0] == "trace"
    assert "--capture-only" in args
    assert "--disable-responsibility-observer" not in args
    assert "--ssl=true" in args and "--process=true" in args
    assert "--ssl=false" not in args
    assert args[args.index("--comm") + 1] == p["agent_comm"]
    assert args[args.index("-o") + 1] == str(Path(row["output"]) / "trace.log")


def test_c3_preserves_selected_observer_environment_without_serializing_it(tmp_path, monkeypatch):
    monkeypatch.setenv("MTL_OBSERVER_PROVIDER", "real-local")
    p = fake_plan(tmp_path)
    result = od.run_condition(p, condition(p, "C3", 1), env=os.environ)
    assert result["status"] == "completed", result
    assert (Path(result["workloads"][0]["stdout"]).parent / "collector.env").read_text() == "real-local"


def test_nonzero_workload_and_collector_exit_are_captured_as_failures(tmp_path):
    p = fake_plan(tmp_path, workload="#!/usr/bin/env python3\nraise SystemExit(7)\n")
    result = od.run_condition(p, condition(p, "C0", 1), env=os.environ)
    assert result["status"] == "failed"
    assert "status 7" in result["error"]
    assert result["workloads"][0]["returncode"] == 7

    (tmp_path / "second").mkdir()
    p = fake_plan(tmp_path / "second", collector="#!/usr/bin/env python3\nraise SystemExit(9)\n")
    result = od.run_condition(p, condition(p, "C1", 1), env=os.environ)
    assert result["status"] == "failed"
    assert "collector" in result["error"] and "9" in result["error"]


def test_timeout_kills_workload_process_group_and_grandchildren(tmp_path, monkeypatch):
    marker = tmp_path / "grandchild-survived"
    monkeypatch.setenv("CHILD_MARKER", str(marker))
    body = """#!/usr/bin/env python3
import os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-c",
    "import os,time; time.sleep(2); open(os.environ['CHILD_MARKER'],'w').write('orphan')"])
Path("child.pid").write_text(str(child.pid))
time.sleep(5)
"""
    p = fake_plan(tmp_path, workload=body, operations=1, timeout=0.8)
    result = od.run_condition(p, condition(p, "C0", 1), env=os.environ)
    assert result["status"] == "failed"
    assert result["workloads"][0]["timed_out"] is True
    assert "timed out" in result["error"]
    time.sleep(1.5)
    assert not marker.exists()


def fake_result(profile, cost):
    return {"profile": profile, "status": "completed", "receipts": {"cost_usd": cost}}


def test_budget_state_uses_known_paid_receipts_and_blocks_on_unknown_or_reached(tmp_path):
    p = plan(tmp_path)
    assert od.budget_state([fake_result("C0", 0.0)], 1.0) == "within_confirmed_budget"
    assert od.budget_state([fake_result("C2", 0.4)], 1.0) == "within_confirmed_budget"
    assert od.budget_state([fake_result("C2", 0.6), fake_result("C3", 0.4)], 1.0) == "budget_reached"
    assert od.budget_state([fake_result("C2", None)], 1.0) == "cost_unknown"

    monkeypatch = pytest.MonkeyPatch()
    try:
        p = plan(tmp_path, env={"MTL_ALLOW_PAID": "1"})
        calls = []

        def fake_run(plan, condition, env=None):
            calls.append(condition["condition_id"])
            cost = 0.02 if condition["profile"] == "C2" else 0.0
            return fake_result(condition["profile"], cost)

        monkeypatch.setattr(od, "run_condition", fake_run)
        report = od.execute_plan(p, execute=True, env={"MTL_ALLOW_PAID": "1"},
                                 max_budget_usd=0.01)
        assert calls == ["c1-r1-C0", "c1-r1-C1", "c1-r1-C2"]
        assert report["budget"]["state"] == "budget_reached"
        assert report["budget"]["semantics"] == "confirmed_only_not_hard_cap"
        blocked = [row for row in report["results"] if row["status"] == "blocked"]
        assert blocked and all(row["reason"] == "confirmed budget reached" for row in blocked)
    finally:
        monkeypatch.undo()


def test_paid_guard_requires_existing_env_and_explicit_confirmed_budget(tmp_path):
    p = plan(tmp_path)
    with pytest.raises(ValueError, match="MTL_ALLOW_PAID"):
        od.check_execution(p, execute=True, env={}, max_budget_usd=1)
    with pytest.raises(ValueError, match="max-budget-usd"):
        od.check_execution(p, execute=True, env={"MTL_ALLOW_PAID": "1"}, max_budget_usd=None)
    for budget in (0, -1, True, "1", float("nan")):
        with pytest.raises(ValueError, match="max-budget-usd"):
            od.check_execution(p, execute=True, env={"MTL_ALLOW_PAID": "1"}, max_budget_usd=budget)
    assert od.check_execution(p, execute=False, env={}, max_budget_usd=None)["execute"] is False
    guard = od.check_execution(p, execute=True, env={"MTL_ALLOW_PAID": "1"}, max_budget_usd=2.5)
    assert guard == {"execute": True, "confirmed_budget_usd": 2.5,
                     "budget_semantics": "confirmed_only_not_hard_cap"}

    blocked = od.blocked_conditions(p, env={}, max_budget_usd=None)
    assert [row["condition_id"] for row in blocked] == [
        row["condition_id"] for row in p["condition_runs"] if row["profile"] in ("C2", "C3")]
    assert od.blocked_conditions(p, env={"MTL_ALLOW_PAID": "1"}, max_budget_usd=1) == []
    with pytest.raises(ValueError, match="MTL_ALLOW_PAID"):
        od.execute_plan(p, execute=True, env={}, max_budget_usd=1)
    assert not Path(p["out"]).exists()


def test_cli_defaults_to_plan_only_and_requires_workload_and_agent_comm(tmp_path, capsys):
    binary = executable(tmp_path / "collector-fake", "#!/usr/bin/env python3\nraise SystemExit(99)\n")
    out = tmp_path / "out"
    workload = executable(tmp_path / "workload", "#!/usr/bin/env python3\n")
    args = ["--binary", str(binary), "--out", str(out), "--workload-json",
            json.dumps([str(workload)]), "--operations-per-instance", "1",
            "--agent-comm", "agentx"]
    assert od.main(args) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["execution"]["execute"] is False
    assert printed["warmup"]["executed"] is False
    assert not out.exists()
    with pytest.raises(SystemExit) as error:
        od.main(["--binary", str(binary), "--out", str(out)])
    assert error.value.code != 0
    with pytest.raises(SystemExit) as error:
        od.main(["--binary", str(binary), "--out", str(out), "--workload-json",
                 json.dumps([str(workload)]), "--operations-per-instance", "1"])
    assert error.value.code != 0


def test_default_observer_config_file_is_bound_and_privately_materialized(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    source = project / 'env/config/observer.toml'
    source.parent.mkdir(parents=True)
    source.write_text('synthetic-test-config')
    monkeypatch.setattr(od, 'ROOT', project)
    p = fake_plan(tmp_path)
    result = od.run_condition(p, condition(p, 'C3', 1), env=os.environ)
    output = Path(condition(p, 'C3', 1)['output'])
    copied = output / '.collector-runtime/env/config/observer.toml'
    assert not (output / 'env/config/observer.toml').exists()
    assert copied.read_text() == 'synthetic-test-config'
    assert copied.stat().st_mode & 0o777 == 0o600
    assert copied.parents[2].stat().st_mode & 0o777 == 0o700
    assert result['status'] == 'completed'
    source.write_text('changed-test-config')
    with pytest.raises(ValueError, match='config file'):
        od.verify_manifest(p)


def test_plan_freezes_resolved_executable_against_path_and_cwd_drift(tmp_path, monkeypatch):
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir(); d2.mkdir()
    m1, m2 = tmp_path / "m1", tmp_path / "m2"
    executable(d1 / "agent", f"#!/usr/bin/env python3\nfrom pathlib import Path\nPath({str(m1)!r}).write_text('d1')\n")
    executable(d2 / "agent", f"#!/usr/bin/env python3\nfrom pathlib import Path\nPath({str(m2)!r}).write_text('d2')\n")
    binary = executable(tmp_path / "collector-fake", "#!/usr/bin/env python3\n")
    monkeypatch.setenv("PATH", f"{d1}:{d2}:{os.defpath}")
    p = od.build_plan(binary, tmp_path / "out-path", ["agent"], 1, agent_comm="agentx")
    assert p["workload_argv"] == [str((d1 / "agent").resolve())]
    monkeypatch.setenv("PATH", f"{d2}:{d1}:{os.defpath}")
    result = od.run_condition(p, condition(p, "C0", 1), env=os.environ)
    assert result["status"] != "failed", result
    assert m1.read_text() == "d1"
    assert not m2.exists()

    monkeypatch.chdir(d1)
    p = od.build_plan(binary, tmp_path / "out-cwd", ["./agent"], 1, agent_comm="agentx")
    monkeypatch.chdir(d2)
    result = od.run_condition(p, condition(p, "C0", 1), env=os.environ)
    assert result["status"] != "failed", result
    assert m1.read_text() == "d1"
    assert not m2.exists()


def test_c3_materializes_observer_config_only_in_private_runtime_cwd(tmp_path):
    p = fake_plan(tmp_path)
    c0 = condition(p, "C0", 1)
    result = od.run_condition(p, c0, env=os.environ)
    out0 = Path(c0["output"])
    assert result["status"] == "completed", result
    assert not (out0 / "env").exists()
    assert not (out0 / ".collector-runtime").exists()

    c3 = condition(p, "C3", 1)
    result = od.run_condition(p, c3, env=os.environ)
    out3 = Path(c3["output"])
    assert result["status"] == "completed", result
    assert not (out3 / "env/config/observer.toml").exists()
    runtime = out3 / ".collector-runtime"
    config = runtime / "env/config/observer.toml"
    assert config.is_file()
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert (out3 / "collector.cwd").read_text() == str(runtime)
    assert (out3 / "collector.config.sha256").read_text() == p["config_file_sha256"]["env/config/observer.toml"]
    assert result["config_privacy"]["same_uid_not_sandbox"] is True
    assert result["config_privacy"]["auth_isolation"] is False


def test_group_cleanup_after_parent_exit_prevents_orphan(tmp_path, monkeypatch):
    marker = tmp_path / "orphan-marker"
    monkeypatch.setenv("CHILD_MARKER", str(marker))
    body = """#!/usr/bin/env python3
import os, subprocess, sys
from pathlib import Path
subprocess.Popen([sys.executable, "-c",
    "import os,time; time.sleep(1); open(os.environ['CHILD_MARKER'],'w').write('orphan')"])
Path("child-spawned").write_text("yes")
"""
    p = fake_plan(tmp_path, workload=body, operations=1, timeout=5)
    result = od.run_condition(p, condition(p, "C0", 1), env=os.environ)
    assert result["status"] != "failed", result
    time.sleep(1.5)
    assert not marker.exists()


def test_cleanup_oserror_is_failed_not_success(tmp_path, monkeypatch):
    p = fake_plan(tmp_path)
    def deny(pid, sig):
        raise PermissionError("denied")
    monkeypatch.setattr(od.os, "killpg", deny)
    result = od.run_condition(p, condition(p, "C0", 1), env=os.environ)
    assert result["status"] == "failed"
    assert "cleanup failed" in result["error"]
