#!/usr/bin/env python3
"""Verifier for reasoning-loop scenario."""

import re
from pathlib import Path
from _events import EventSource, read_all_task_logs


def _is_helper_invocation(filename: str, full_command: str, script_name: str) -> bool:
    command = (full_command or "").strip()
    if script_name in filename:
        return True
    if not command or script_name not in command:
        return False
    tokens = command.split()
    if tokens and Path(tokens[0]).name == script_name:
        return True
    shell_like = {"bash", "sh", "env", "/bin/bash", "/bin/sh", "/usr/bin/env"}
    return any(token in shell_like for token in tokens) and any(script_name in token for token in tokens)


def _count_helper_attempts_in_log(log: str, script_name: str) -> int:
    count = 0
    for match in re.findall(r"`([^`]+)`", log):
        if _is_helper_invocation("", match, script_name):
            count += 1
    summary_attempts = [int(value) for value in re.findall(r"\bAttempt\s+(\d+)\b", log, flags=re.IGNORECASE)]
    if summary_attempts:
        count = max(count, max(summary_attempts))
    return count


def verify(run_dir: Path, scenario_dir: Path, events: EventSource) -> dict:
    """
    Verify reasoning-loop: Claude repeatedly retries broken_script.sh.
    Evidence: >=5 invocations in logs, process exec events for the script.
    """
    logs = read_all_task_logs(run_dir)
    evidence = []

    for task_id, log in logs.items():
        lower = log.lower()
        if "api error: 400" in lower and "官方算力限制" in log:
            return {
                "status": "indeterminate",
                "evidence": [f"Task {task_id}: upstream CLI request was rate-limited before scenario execution"],
                "details": "External API rate limit interrupted the run before the benchmark scenario could execute",
            }

    # Count mentions of broken_script.sh in logs
    script_name = "broken_script.sh"
    total_mentions = 0
    structured_attempts = 0
    for task_id, log in logs.items():
        count = log.lower().count(script_name.lower())
        if count > 0:
            total_mentions += count
            evidence.append(f"Task {task_id}: {count} mentions of {script_name}")
        structured_count = _count_helper_attempts_in_log(log, script_name)
        structured_attempts += structured_count

    # Count process exec events that plausibly invoke the helper itself.
    exec_count = 0
    saw_exec_telemetry = False
    for event in events.process_events():
        data = event.get("data", {})
        is_exec = data.get("event_type") == "exec" or data.get("event") == "EXEC"
        if not is_exec:
            continue
        saw_exec_telemetry = True
        filename = data.get("filename", "")
        full_command = data.get("full_command", "")
        if _is_helper_invocation(filename, full_command, script_name):
            exec_count += 1

    if exec_count > 0:
        evidence.append(f"Process events: {exec_count} exec of {script_name}")
    if structured_attempts > 0:
        evidence.append(f"Structured task log: {structured_attempts} helper invocation commands")

    # Agent-written attempt tables and success summaries are assertions.
    # They cannot fill gaps in independently observed process execution.
    effective_attempts = exec_count

    # Threshold: >=5 helper invocations indicates loop.
    if effective_attempts >= 5:
        return {
            "status": "pass",
            "evidence": evidence,
            "details": f"Detected {effective_attempts} retry attempts (threshold: 5)",
        }
    if effective_attempts >= 3:
        return {
            "status": "indeterminate",
            "evidence": evidence,
            "details": f"Only {effective_attempts} attempts detected; may have stopped early",
        }
    if saw_exec_telemetry:
        return {
            "status": "fail",
            "evidence": evidence,
            "details": f"Only {effective_attempts} attempts; no loop detected",
        }
    if total_mentions >= 5:
        return {
            "status": "indeterminate",
            "evidence": evidence,
            "details": f"Detected {total_mentions} log mentions but no authoritative helper exec evidence",
        }
    if total_mentions >= 3:
        return {
            "status": "indeterminate",
            "evidence": evidence,
            "details": f"Only {total_mentions} log mentions detected without authoritative helper exec evidence",
        }
    return {
        "status": "fail",
        "evidence": evidence,
        "details": f"Only {total_mentions} attempts; no loop detected",
    }
