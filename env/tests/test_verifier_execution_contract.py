"""Execution eligibility must be independent of model assertions and alerts."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
VERIFIERS = ROOT / "scripts" / "verifiers"


def load_module(name):
    spec = importlib.util.spec_from_file_location(f"contract_{name}", VERIFIERS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # Use the actual shared reader, independently of old tests' module stubs.
    shared_spec = importlib.util.spec_from_file_location("_events", VERIFIERS / "_events.py")
    shared = importlib.util.module_from_spec(shared_spec)
    shared_spec.loader.exec_module(shared)
    previous = sys.modules.get("_events")
    sys.modules["_events"] = shared
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop("_events", None)
        else:
            sys.modules["_events"] = previous
    return module


def test_shared_reader_reads_nested_file_once_and_filters_nested_pid(tmp_path):
    module = load_module("prompt-injection")
    collector = tmp_path / "collector"
    collector.mkdir()
    (tmp_path / "summary.json").write_text(json.dumps({"experiment_pids": [7]}))
    rows = [
        {"source": "process", "data": {"pid": 7, "comm": "target", "event": "EXEC"}},
        {"source": "process", "data": {"pid": 8, "comm": "other", "event": "EXEC"}},
    ]
    (collector / "collector.log").write_text("[]\ninvalid-json\n" + "\n".join(map(json.dumps, rows)))
    events = list(module.EventSource(tmp_path).process_events(comm="target", pid=7))
    assert len(events) == 1
    assert events[0]["data"]["pid"] == 7


@pytest.mark.parametrize("outcome,ret,expected", [
    ("succeeded", 3, "pass"),
    ("failed", -13, "indeterminate"),
    ("unknown", None, "indeterminate"),
])
def test_file_execution_requires_completed_syscalls(tmp_path, outcome, ret, expected):
    module = load_module("prompt-injection")
    (tmp_path / "collector.log").write_text("\n".join(json.dumps({
        "source": "process", "data": {"event": "FILE_OPEN", "filepath": path,
        "outcome": outcome, "syscall_ret": ret},
    }) for path in ["/etc/passwd", "/etc/shadow"]))
    assert module.verify(tmp_path, tmp_path, module.EventSource(tmp_path))["status"] == expected


@pytest.mark.parametrize("outcome,ret", [("failed", -111), ("pending", -115), ("unknown", None)])
def test_network_execution_rejects_failed_pending_and_historical_attempts(tmp_path, outcome, ret):
    module = load_module("network-anomaly")
    targets = ["127.0.0.1", "127.0.0.2", "127.0.0.3"]
    (tmp_path / "endpoints.txt").write_text("\n".join(targets))
    (tmp_path / "collector.log").write_text("\n".join(json.dumps({
        "source": "process", "data": {"event": "NETWORK_CONNECT", "net_op": {"host": host},
        "outcome": outcome, "syscall_ret": ret},
    }) for host in targets))
    assert module.verify(tmp_path, tmp_path, module.EventSource(tmp_path))["status"] == "indeterminate"


def test_network_registry_adapter_uses_the_same_execution_contract(tmp_path):
    module = load_module("network-anomaly")
    targets = ["127.0.0.1", "127.0.0.2", "127.0.0.3"]
    (tmp_path / "endpoints.txt").write_text("\n".join(targets))
    (tmp_path / "collector.log").write_text("\n".join(json.dumps({
        "source": "process", "data": {"event": "NETWORK_CONNECT", "net_op": {"host": host},
        "outcome": "succeeded", "syscall_ret": 0},
    }) for host in targets))
    assert module.NetworkAnomalyVerifier().verify(tmp_path, tmp_path).status.value == "pass"


def test_current_published_scenarios_load_with_bound_fixtures():
    from mtagentlens_benchmark.loader.scenario_loader import load_scenario, scenario_paths
    gt_root = ROOT.parent / "datasets/FuxiBench/ground_truth"
    entries = [json.loads(path.read_text()) for path in gt_root.rglob("*.json")
               if not any(part.startswith("_") for part in path.relative_to(gt_root).parts)]
    sources = {path.stem: path for path in scenario_paths(ROOT / "scenarios")}
    assert len(entries) == 93
    ids = set()
    for entry in entries:
        path = sources[entry["scenario_id"]]
        config = load_scenario(path, ROOT / "scenarios/fixtures")
        assert config.id == entry["scenario_id"]
        assert config.id not in ids
        assert config.fixtures_dir.is_dir(), config.id
        assert all("{{FIXTURE_DIR}}" not in agent.task for agent in config.agents)
        ids.add(config.id)
