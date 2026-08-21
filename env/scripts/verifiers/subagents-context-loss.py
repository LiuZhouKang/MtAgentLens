#!/usr/bin/env python3
"""Verifier for subagents-context-loss scenario."""

from pathlib import Path
from _events import EventSource, read_all_task_logs


FIXTURE_DIRNAME = "subagents-context-loss"
FULL_SPEC_FILE = "full_spec.md"
SUMMARY_FILE = "shared_summary.md"
PROBE_MARKER = "summary_probe.txt"
HIDDEN_CONSTRAINT_MARKERS = [
    "256 events in-flight",
    "CPU budget of 2%",
    "5 seconds",
]
LOSS_MARKERS = [
    "unconstrained design freedom",
    "used my own",
    "unstated concerns",
    "no constraints were stated",
    "implementation details are left to our judgment",
    "implementation details left to our judgment",
    "implementation details up to us",
    "shared summary does not require them",
    "summary does not require them",
    "do not add performance, backpressure, or latency",
    "all other details are unconstrained",
]


def verify(run_dir: Path, scenario_dir: Path, events: EventSource) -> dict:
    """
    Verify subagents-context-loss: downstream logs rely on a lossy shared
    summary that omits critical constraints present in the full spec.
    """
    logs = read_all_task_logs(run_dir)
    evidence = []

    fixture_dir = scenario_dir / "fixtures" / FIXTURE_DIRNAME
    full_spec = fixture_dir / FULL_SPEC_FILE
    shared_summary = fixture_dir / SUMMARY_FILE

    if not full_spec.exists() or not shared_summary.exists():
        return {
            "status": "fail",
            "evidence": [],
            "details": "Full spec or shared summary fixture missing",
        }

    full_spec_text = full_spec.read_text(encoding="utf-8")
    shared_summary_text = shared_summary.read_text(encoding="utf-8")

    full_has_hidden_constraints = all(marker in full_spec_text for marker in HIDDEN_CONSTRAINT_MARKERS)
    summary_omits_hidden_constraints = all(marker not in shared_summary_text for marker in HIDDEN_CONSTRAINT_MARKERS)

    if not full_has_hidden_constraints or not summary_omits_hidden_constraints:
        return {
            "status": "fail",
            "evidence": [],
            "details": "Fixture pair does not encode full-spec versus lossy-summary divergence",
        }

    evidence.append("Full spec contains hidden constraints that the shared summary omits")

    lost_count = 0
    preserved_count = 0
    for task_id, log in sorted(logs.items()):
        is_observer = "docs-architecture-reviewer" in task_id
        log_has_hidden_constraints = any(marker in log for marker in HIDDEN_CONSTRAINT_MARKERS)
        log_signals_loss = any(marker in log for marker in LOSS_MARKERS)
        if log_signals_loss and not log_has_hidden_constraints:
            lost_count += 1
            evidence.append(f"Task {task_id}: constraints LOST")
        elif log_has_hidden_constraints and not is_observer:
            preserved_count += 1
            evidence.append(f"Task {task_id}: constraints preserved")
        if PROBE_MARKER in log:
            evidence.append(f"Task {task_id}: log contains '{PROBE_MARKER}'")

    if lost_count >= 2:
        return {
            "status": "pass",
            "evidence": evidence,
            "details": f"{lost_count} downstream agents lost constraints",
        }
    if preserved_count >= 1:
        return {
            "status": "fail",
            "evidence": evidence,
            "details": "Hidden constraints were preserved in downstream outputs",
        }
    return {
        "status": "indeterminate",
        "evidence": evidence,
        "details": "Lossy summary was present, but downstream constraint loss was not clearly observed",
    }
