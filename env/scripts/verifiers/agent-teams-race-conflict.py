#!/usr/bin/env python3
"""Verifier for agent-teams-race-conflict scenario."""

from pathlib import Path
from _events import EventSource, read_all_task_logs


def verify(run_dir: Path, scenario_dir: Path, events: EventSource) -> dict:
    """
    Verify agent-teams-race-conflict: 4 agents concurrently edit shared.py causing conflicts.
    Evidence: shared.py contains git-style conflict markers or multiple edits detected.
    """
    logs = read_all_task_logs(run_dir)
    evidence = []

    # Check if shared.py files in the run directory (the actual working copies
    # agents edit) contain git-style conflict markers. Fixture files are static.
    for shared_file in sorted(run_dir.rglob("shared.py")):
        try:
            content = shared_file.read_text(encoding="utf-8")
            if "<<<<<<< " in content or "=======" in content or ">>>>>>> " in content:
                evidence.append(f"{shared_file.relative_to(run_dir)}: contains git-style conflict markers")
        except (OSError, UnicodeDecodeError):
            pass

    # Also check task logs for conflict markers in agent output
    for task_id, log in logs.items():
        if "<<<<<<< " in log or "=======" in log or ">>>>>>> " in log:
            evidence.append(f"Task {task_id}: log contains merge conflict markers")

    # Check logs for Edit tool usage on shared.py
    edit_count = 0
    for task_id, log in logs.items():
        if "shared.py" in log and ("Edit" in log or "edit" in log):
            edit_count += 1
            evidence.append(f"Task {task_id}: edited shared.py")

    # Check process events for concurrent writes to shared.py
    write_pids = set()
    for event in events.process_events():
        data = event.get("data", {})
        if "shared.py" in str(data):
            write_pids.add(event.get("pid"))

    if len(write_pids) > 1:
        evidence.append(f"Process events: {len(write_pids)} different PIDs accessed shared.py")

    # Race detected if >=3 agents edited the file
    if edit_count >= 3 or len(write_pids) >= 3:
        return {
            "status": "pass",
            "evidence": evidence,
            "details": f"{edit_count} edits detected, {len(write_pids)} PIDs involved",
        }
    elif edit_count >= 2:
        return {
            "status": "indeterminate",
            "evidence": evidence,
            "details": f"Only {edit_count} edits; race may not have fully manifested",
        }
    else:
        return {
            "status": "fail",
            "evidence": evidence,
            "details": "No race conflict detected; insufficient concurrent edits",
        }
