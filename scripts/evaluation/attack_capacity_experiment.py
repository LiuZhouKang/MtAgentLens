# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Attack-only capacity experiment: run each single-agent scenario's payload directly.

This is a direct payload-control experiment, *not* the main 93-record LLM Agent
campaign. It invokes the scenario's documented lure under the real collector so
the result measures the monitor's capacity rather than an agent's willingness to
comply. The driver makes no agent model calls, but the collector's responsibility
observer may be a paid endpoint: execution therefore requires an explicit
``--execute`` plus a pre-existing ``MTL_ALLOW_PAID=1`` and its cost is unknown,
never zero.

Ground truth triggers come from each scenario's reviewed `task_summary` (see
`attack-capacity-triggers.json`). A scenario whose lure is "execute the runbook"
gets its `docs.md` bash blocks executed literally; Makefile-style scenarios run
their documented target. The current cohort is derived from canonical GT:
``label == "attack"`` and exactly one declared agent (36 scenarios at present).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_TRIGGERS = ROOT / "scripts" / "evaluation" / "attack-capacity-triggers.json"
DEFAULT_RELEASE = ROOT / "datasets" / "FuxiBench"
PAID_ENV = "MTL_ALLOW_PAID"
PLAN_SCHEMA = "mtagentlens.attack-capacity-plan/1"
COHORT_SCOPE = "canonical_gt_attack_single_agent"


class CapacityError(RuntimeError):
    """A request that must fail closed before any execution side effect."""


def paid_allowed() -> bool:
    """Paid observer calls require an operator-set environment flag, never inference."""
    return os.environ.get(PAID_ENV) == "1"


def load_triggers(path: Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios", payload)
    if not isinstance(scenarios, dict) or not scenarios:
        raise CapacityError(f"no scenarios in trigger manifest: {path}")
    return scenarios


def canonical_cohort(release: Path) -> dict[str, dict]:
    """Attack GT scenarios with exactly one declared agent, keyed by scenario id.

    Derived from the frozen release's ground truth and scenarios; there is no
    legacy count or external scenario list to drift from the dataset.
    """
    from compare.tools.detection_scope import included_ground_truth

    release = Path(release)
    cohort: dict[str, dict] = {}
    for scenario_id, gt in sorted(included_ground_truth(release / "ground_truth").items()):
        if gt.get("label") != "attack":
            continue
        scenario_path = release / "scenarios" / f"{scenario_id}.json"
        if not scenario_path.is_file():
            raise CapacityError(f"missing frozen scenario for {scenario_id}")
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        if scenario.get("id") != scenario_id:
            raise CapacityError(f"scenario identity mismatch for {scenario_id}")
        agents = scenario.get("agents")
        if not isinstance(agents, list) or len(agents) != 1:
            continue
        cohort[scenario_id] = scenario
    return cohort


def validate_requests(manifest: dict, cohort: dict[str, dict], triggers: dict,
                      requested: list[str]) -> list[str]:
    """Validate every requested id against release, GT, trigger and cohort."""
    labels = manifest.get("case_labels") or {}
    if not requested:
        raise CapacityError("no scenarios requested")
    validated: list[str] = []
    seen: set[str] = set()
    for scenario_id in requested:
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise CapacityError("invalid scenario request")
        if scenario_id in seen:
            raise CapacityError(f"duplicate scenario request: {scenario_id}")
        seen.add(scenario_id)
        if scenario_id not in labels:
            raise CapacityError(f"scenario absent from frozen release: {scenario_id}")
        if labels[scenario_id] != "attack":
            raise CapacityError(f"scenario is not an attack: {scenario_id}")
        if scenario_id not in triggers:
            raise CapacityError(f"no operational trigger for scenario: {scenario_id}")
        if scenario_id not in cohort:
            raise CapacityError(
                f"scenario is not in the single-agent attack cohort: {scenario_id}")
        validated.append(scenario_id)
    return validated


def build_plan(release: Path, triggers: dict, only: list[str]) -> dict:
    """Validate the frozen release and return a side-effect-free execution plan."""
    from compare.tools.dataset_release import validate_release

    release = Path(release)
    if (release / "release.json").exists():
        manifest = validate_release(release)
    else:
        from compare.tools.detection_scope import included_ground_truth
        manifest = {"schema": None, "release_id": None,
                    "case_labels": {sid: record.get("label") for sid, record in
                                    included_ground_truth(release / "ground_truth").items()}}
    cohort = canonical_cohort(release)
    requested = validate_requests(manifest, cohort, triggers, only)
    return {
        "schema": PLAN_SCHEMA,
        "release": str(release),
        "release_id": manifest.get("release_id"),
        "dataset_schema": manifest.get("schema"),
        "cohort": COHORT_SCOPE,
        "cohort_size": len(cohort),
        "scenarios": requested,
        "requested": requested,
        "requested_count": len(requested),
        "execute": False,
        "uses_agent_model": False,
        "observer_may_be_paid": True,
        "cost_unknown_not_zero": True,
        "scope": "direct_payload_control_not_main_93_llm_agent_campaign",
        "note": ("Direct payload control, not the main 93-record LLM Agent campaign. "
                 "No agent model is called, but the collector observer may be a paid "
                 "endpoint; execution needs --execute and a pre-existing "
                 f"{PAID_ENV}=1 and its cost is unknown, not zero."),
    }


def capture_status(database: Path | None) -> str | None:
    """Capture quality recorded in the run's analysis checkpoint (not the CLI summary)."""
    import sqlite3

    if database is None or not database.is_file():
        return None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        row = connection.execute("SELECT payload_json FROM analysis_checkpoints LIMIT 1").fetchone()
        connection.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    try:
        from tools.annotator.payload_codec import decode_checkpoint_payload
        return (json.loads(decode_checkpoint_payload(row[0])).get("capture_quality") or {}).get("status")
    except (TypeError, ValueError):
        return None


def scenario_config(release: Path, scenario_id: str):
    from mtagentlens_benchmark.loader.scenario_loader import load_scenario
    from mtagentlens_benchmark.workspace import BenchmarkWorkspace

    workspace = replace(
        BenchmarkWorkspace.discover(), root=ROOT / "env", project_root=ROOT,
        scenarios_dir=release / "scenarios", fixtures_dir=release / "fixtures",
    )
    config = load_scenario(release / "scenarios" / f"{scenario_id}.json", workspace.fixtures_dir)
    if config.pre_run_script:
        raw = Path(config.pre_run_script)
        parts = raw.parts
        relative = Path(*parts[2:]) if parts[:3] == ("env", "scenarios", "bench") else raw
        setup = (release / relative).resolve()
        if not setup.is_file():
            raise ValueError(f"setup script not in release for {scenario_id}")
        config.pre_run_script = str(setup)
    return workspace, config


def documented_blocks(fixture: Path) -> list[str]:
    docs = fixture / "docs.md"
    if not docs.is_file():
        return []
    text = docs.read_text(encoding="utf-8", errors="replace")
    blocks = re.findall(r"```(?:bash|sh|shell)\n(.*?)```", text, re.S)
    return [block for block in blocks if block.strip()]


def run_trigger(spec: dict, fixture: Path, env: dict[str, str]) -> tuple[bool, dict]:
    commands = list(spec.get("commands") or [])
    if spec.get("runbook_blocks"):
        commands = documented_blocks(fixture) + commands
    if not commands:
        return False, {"error": "no trigger commands"}
    results = []
    for command in commands:
        try:
            completed = subprocess.run(
                ["bash", "-lc", command], cwd=str(fixture), env=env,
                capture_output=True, text=True, timeout=int(spec.get("timeout", 180)),
            )
        except subprocess.TimeoutExpired:
            results.append({"command": command, "status": "timeout"})
            continue
        results.append({
            "command": command,
            "status": "ok" if completed.returncode == 0 else f"exit {completed.returncode}",
            "stderr_tail": (completed.stderr or "")[-200:],
        })
    return True, {"trigger_results": results}


def run_capacity(release: Path, out: Path, triggers: dict, only: list[str], *,
                 execute: bool = False) -> int:
    """Execute the validated cohort; refuses unless execute and paid are both explicit.

    This in-function gate is the enforcement point for direct callers too: the
    CLI check alone must never be the only barrier before paid observer calls.
    """
    if not execute:
        raise CapacityError("run_capacity requires execute=True")
    if not paid_allowed():
        raise CapacityError(f"paid execution requires a pre-existing {PAID_ENV}=1")

    plan = build_plan(release, triggers, only)

    from mtagentlens_benchmark.engine import executor
    from mtagentlens_benchmark.engine.collector_manager import CollectorManager
    from scripts.evaluation.run_artifacts import read_canonical_verdict

    release = Path(release)
    out = Path(out)
    binary = ROOT / "collector" / "build" / "release" / "mtagentlens"
    if not binary.is_file():
        print("error: collector binary missing", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    results = []
    fatal = False
    for scenario_id in plan["requested"]:
        workspace, config = scenario_config(release, scenario_id)
        run_root = out / "runs" / scenario_id
        run_root.mkdir(parents=True, exist_ok=True)
        fixture_dir = run_root / "fixtures"
        spec = triggers[scenario_id]

        def fake_process_mode(config, run_dir, status_dir, logs_dir, *, run_id, _spec=spec):
            fixture = run_dir / "fixtures"
            env = os.environ.copy()
            env["RUN_TAG"] = getattr(config, "run_marker", "")
            env["RUN_TAG_FILE"] = str(run_dir / "marker.log")
            env["FIXTURE_DIR"] = str(fixture)
            ok, summary = run_trigger(_spec, fixture, env)
            return ok, summary

        original_mode = executor._run_process_mode
        original_artifacts = executor._capture_agent_artifacts
        executor._run_process_mode = fake_process_mode
        executor._capture_agent_artifacts = lambda *args, **kwargs: None
        try:
            result = executor.execute_scenario(
                config, run_root, workspace, execution_mode="process",
                collector=CollectorManager(
                    workspace, binary=str(binary), disable_responsibility_observer=False),
            )
        finally:
            executor._run_process_mode = original_mode
            executor._capture_agent_artifacts = original_artifacts
        verdict = read_canonical_verdict(result.database_path) if result.database_path else {}
        marker = (run_root / "marker.log").read_text(errors="replace") if (run_root / "marker.log").is_file() else ""
        executed = bool(config.run_marker) and config.run_marker in marker
        sink_dir = run_root / "sink"
        row = {
            "scenario_id": scenario_id,
            "release_id": plan["release_id"],
            "label": "attack",
            "agents": 1,
            "success": result.success,
            "error": result.error,
            "final_decision": verdict.get("final_decision"),
            "capture_quality_status": capture_status(result.database_path),
            "marker_executed": executed,
            "sink_receipts": len(list(sink_dir.rglob("*"))) if sink_dir.is_dir() else 0,
            "duration_seconds": round(result.duration_seconds, 1),
        }
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if not result.success:
            fatal = True
    (out / "capacity-results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8")
    return 0 if not fatal else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--triggers", type=Path, default=DEFAULT_TRIGGERS)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", action="append")
    parser.add_argument("--execute", action="store_true",
                        help=(f"execute the payload; requires a pre-existing {PAID_ENV}=1 "
                              "and may incur unknown paid observer cost"))
    args = parser.parse_args(argv)
    try:
        triggers = load_triggers(args.triggers)
        only = args.only or sorted(triggers)
        plan = build_plan(args.release, triggers, only)
    except (CapacityError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if not paid_allowed():
        print(f"error: --execute requires a pre-existing {PAID_ENV}=1 "
              "(paid observer cost is unknown, not zero)", file=sys.stderr)
        return 2
    try:
        return run_capacity(args.release, args.out, triggers, only, execute=True)
    except (CapacityError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
