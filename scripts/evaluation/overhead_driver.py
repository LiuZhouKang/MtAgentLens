# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Overhead four-profile plan and subprocess lifecycle driver.

Profiles are fixed: C0 agent-alone, C1 trace --capture-only, C2 trace
--disable-responsibility-observer (runtime+DB, Observer off), C3 default trace
(Observer on, no ONNX). C1-C3 keep TLS and process capture enabled; only the
profile boundary flag differs. This module only builds plans and, with an
explicit --execute, runs controlled workloads. It never claims measurements.

The workload contract is JSONL receipts on stdout: one object per controlled
operation with a unique ``receipt_id``. Missing stdout receipts measure workload
completion only; they are NOT eBPF capture loss. After shutdown the driver binds
typed receipts, the raw trace and the persisted actions through the
``controlled_file_open/v1`` adapter; missing evidence is not estimable, not
zero. Timestamps are optional for workload completion; missing stays null.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import shutil
import signal
import sqlite3
import subprocess
import threading
import time

from scripts.evaluation import controlled_capture

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "mtagentlens.overhead-plan/2"
PROTOCOL = "EXPERIMENT-SUITE-7-REVISED-2026-10-26 §6"
PROFILES = ("C0", "C1", "C2", "C3")
PROFILE_LABELS = {
    "C0": "agent_alone",
    "C1": "collector_trace_capture_only",
    "C2": "collector_trace_disable_responsibility_observer",
    "C3": "collector_trace_default",
}
PROFILE_FLAGS = {
    "C0": [],
    "C1": ["--capture-only"],
    "C2": ["--disable-responsibility-observer"],
    "C3": [],
}
CONCURRENCY = (1, 4, 8, 16, 32)
CONCURRENCY_PROFILES = {
    1: PROFILES,
    4: ("C0", "C3"),
    8: ("C0", "C3"),
    16: ("C0", "C3"),
    32: ("C0", "C3"),
}
MATRIX = CONCURRENCY_PROFILES
REPEATS = 3
ORDER_SEED = 20261028
BLOCK_COUNT = 15
CONDITION_COUNT = 36
WORKLOAD_INSTANCE_COUNT = 372
PAID_PROFILES = ("C2", "C3")
PAID_ENV = "MTL_ALLOW_PAID"
BUDGET_FLAG = "--max-budget-usd"
BUDGET_SEMANTICS = "confirmed_only_not_hard_cap"
CONFIG_ENV_PREFIXES = ("MTAGENTLENS_", "MTL_", "OBSERVER_", "MODEL_", "OPENAI_",
                       "ANTHROPIC_", "AZURE_", "AWS_", "GOOGLE_", "CLAUDE_", "DEEPSEEK_")
REQUIRED_SOURCE_KINDS = ("SSL", "Process")
REAL_OBSERVER_PROVIDERS = ("llm_env", "llm_config")
OBSERVER_PROVIDER_MARKERS = {
    "llm_env": "✓ Observer: LLM provider loaded from environment variables",
    "llm_config": "✓ Observer: LLM provider loaded from a config file",
    "mock": "Mock (zero-token",
    "disabled": "! Observer: disabled",
}
OBSERVER_MARKER_KEYS = {
    "llm_env": "observer_env",
    "llm_config": "observer_file",
    "mock": "observer_mock",
    "disabled": "observer_disabled",
}


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def resolve_executable(command):
    candidate = Path(command) if os.sep in command else Path(shutil.which(command) or "")
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ValueError("workload executable must exist and be executable")
    return candidate.resolve()


def environment_binding(env=None):
    """Hash selected config values; values are never serialized into a plan."""
    env = os.environ if env is None else env
    selected = {key: value for key, value in env.items()
                if key.startswith(CONFIG_ENV_PREFIXES)}
    return {"sha256": sha256_bytes(canonical(selected).encode()),
            "keys": sorted(selected)}


def config_file_binding():
    # These are the runtime's fallback config paths, relative to its cwd.
    return {name: sha256_file(ROOT / name) if (ROOT / name).is_file() else None
            for name in ('env/config/observer.toml', 'observer_llm.toml')}


def validate_workload(workload_argv):
    if not isinstance(workload_argv, (list, tuple)) or not workload_argv:
        raise ValueError("an explicit workload argv list is required; no shell string or default")
    workload = list(workload_argv)
    if any(not isinstance(arg, str) or not arg for arg in workload):
        raise ValueError("workload argv must contain non-empty strings")
    return workload


def workload_input_binding(workload_argv):
    """Bind explicit file arguments, including a Python workload's script.

    This is not a recursive interpreter/import dependency lock.
    """
    return {arg: {"resolved": str(Path(arg).resolve()), "sha256": sha256_file(arg)}
            for arg in workload_argv[1:] if Path(arg).is_absolute() and Path(arg).is_file()}


def block_condition_order(block_id, profiles, seed=ORDER_SEED):
    """Deterministic seeded shuffle archived in the frozen block manifest."""
    ordered = list(profiles)
    token = f"{seed}:{block_id}".encode()
    rng = random.Random(int.from_bytes(hashlib.sha256(token).digest()[:8], "big"))
    rng.shuffle(ordered)
    return ordered


def selected_condition_runs(plan, condition_ids=None):
    """Return planned rows only; selection never edits or shrinks the matrix."""
    planned = plan.get("condition_runs")
    if not isinstance(planned, list):
        raise ValueError("overhead plan has no condition_runs matrix")
    if condition_ids is None:
        return list(planned)
    if isinstance(condition_ids, str):
        condition_ids = [condition_ids]
    if not isinstance(condition_ids, (list, tuple)) or not condition_ids:
        raise ValueError("condition selection must be a non-empty list")
    if any(not isinstance(value, str) or not value for value in condition_ids):
        raise ValueError("condition selection must contain non-empty condition_id strings")
    if len(set(condition_ids)) != len(condition_ids):
        raise ValueError("duplicate condition_id in selection")
    by_id = {row.get("condition_id"): row for row in planned
             if isinstance(row, dict)}
    unknown = [value for value in condition_ids if value not in by_id]
    if unknown:
        raise ValueError(
            "selected condition_id is not planned in the frozen matrix: "
            + ", ".join(unknown))
    chosen = set(condition_ids)
    return [row for row in planned if row.get("condition_id") in chosen]


def build_plan(binary, out, workload_argv, operations_per_instance, timeout_s=600, *,
               agent_comm, env=None, execute=False, sample_interval_ms=100):
    binary = Path(binary).resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("collector binary must exist and be executable")
    if not isinstance(agent_comm, str) or not agent_comm:
        raise ValueError("an explicit non-empty agent-comm is required for the isolated workload")
    raw_workload = validate_workload(workload_argv)
    workload_executable = resolve_executable(raw_workload[0])
    workload = [str(workload_executable), *raw_workload[1:]]
    if type(operations_per_instance) is not int or operations_per_instance < 1:
        raise ValueError("operations-per-instance must be a positive integer")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout must be a positive finite number")
    if type(sample_interval_ms) is not int or sample_interval_ms < 10:
        raise ValueError("sample interval must be an integer >= 10 ms")
    out = Path(out).resolve()

    binary_sha = sha256_file(binary)
    workload_sha = sha256_bytes(canonical(workload).encode())
    executable_sha = sha256_file(workload_executable)
    workload_inputs = workload_input_binding(workload)
    config_env = environment_binding(env)
    config_files = config_file_binding()
    matrix = {
        "protocol": PROTOCOL,
        "concurrency_profiles": {str(level): list(names)
                                 for level, names in CONCURRENCY_PROFILES.items()},
        "four_profile_blocks": 3,
        "two_profile_blocks": 12,
        "order_seed": ORDER_SEED,
        "order": {"seed": ORDER_SEED, "method": "seeded_shuffle_within_block",
                  "archived_in": "blocks[].condition_order"},
    }
    configuration = {
        "schema": SCHEMA, "protocol": PROTOCOL, "matrix": matrix,
        "concurrency": list(CONCURRENCY), "repeats": REPEATS,
        "profiles": list(PROFILES), "profile_flags": PROFILE_FLAGS,
        "operations_per_instance": operations_per_instance, "timeout_s": float(timeout_s),
        "sample_interval_ms": sample_interval_ms,
        "agent_comm": agent_comm, "binary_sha256": binary_sha,
        "workload_sha256": workload_sha,
        "workload_executable_sha256": executable_sha,
        "workload_input_files": workload_inputs,
        "config_env_sha256": config_env["sha256"],
        "config_file_sha256": config_files,
    }
    config_sha = sha256_bytes(canonical(configuration).encode())

    condition_runs = []
    blocks = []
    for concurrency in CONCURRENCY:
        profiles = CONCURRENCY_PROFILES[concurrency]
        for repeat in range(1, REPEATS + 1):
            block_id = f"c{concurrency}-r{repeat}"
            order = block_condition_order(block_id, profiles)
            block = {"block_id": block_id, "concurrency": concurrency, "repeat": repeat,
                     "profiles": list(profiles), "condition_order": order,
                     "condition_ids": []}
            for profile in order:
                condition_id = f"{block_id}-{profile}"
                flags = list(PROFILE_FLAGS[profile])
                collector = profile != "C0"
                collector_command = None
                if collector:
                    collector_command = [str(binary), "trace", *flags, "--ssl=true",
                        "--process=true", "--comm", agent_comm, "--quiet",
                        "-o", str(out / condition_id / "trace.log")]
                row = {
                    "condition_id": condition_id, "block_id": block_id,
                    "concurrency": concurrency, "repeat": repeat, "profile": profile,
                    "profile_label": PROFILE_LABELS[profile],
                    "collector": collector, "collector_flags": flags,
                    "collector_command": collector_command,
                    "collector_potentially_paid": profile in PAID_PROFILES,
                    "workload_argv": list(workload),
                    "workload_instances": concurrency,
                    "expected_receipts": concurrency * operations_per_instance,
                    "output": str(out / condition_id),
                }
                condition_runs.append(row)
                block["condition_ids"].append(condition_id)
            blocks.append(block)

    plan = {
        "schema": SCHEMA, "scope": "overhead36_condition_matrix_execution_plan",
        "protocol": PROTOCOL, "matrix": matrix,
        "binary": str(binary), "binary_sha256": binary_sha,
        "workload_argv": list(workload), "workload_sha256": workload_sha,
        "workload_executable": str(workload_executable),
        "workload_executable_sha256": executable_sha,
        "workload_input_files": workload_inputs,
        "agent_comm": agent_comm,
        "config_env_sha256": config_env["sha256"],
        "config_file_sha256": config_files,
        "config_env_keys": config_env["keys"],
        "config_sha256": config_sha, "out": str(out),
        "concurrency": list(CONCURRENCY), "repeats": REPEATS,
        "profiles": list(PROFILES), "profile_labels": PROFILE_LABELS,
        "required_source_kinds": list(REQUIRED_SOURCE_KINDS),
        "operations_per_instance": operations_per_instance,
        "timeout_s": float(timeout_s),
        "sample_interval_ms": sample_interval_ms,
        "blocks": blocks, "condition_runs": condition_runs,
        "block_count": len(blocks), "condition_run_count": len(condition_runs),
        "workload_instance_count": sum(row["workload_instances"] for row in condition_runs),
        "workload_instances": sum(row["workload_instances"] for row in condition_runs),
        "warmup": {"executed": False, "counted_in_blocks": False, "condition_runs": 0,
                   "note": "no warmup run; requires a separate explicit plan"},
        "execution": {
            "execute": bool(execute), "requires_explicit_execute": True,
            "paid_profiles": list(PAID_PROFILES),
            "paid_guard": {"env": PAID_ENV, "value": "1", "budget_flag": BUDGET_FLAG,
                           "budget_semantics": BUDGET_SEMANTICS},
            "default_executes": False,
        },
        "blocked_condition_policy": {
            "order": "condition_runs_order", "paid_profiles": list(PAID_PROFILES),
            "unpaid_profiles": ["C0", "C1"],
            "requirement": f"pre-existing {PAID_ENV}=1 and explicit {BUDGET_FLAG} > 0",
        },
        "limits": [
            "plan only; no measurement, attachment, or performance claim",
            "stdout receipt misses measure workload completion only; controlled capture needs typed receipts, raw trace and proven health",
            "controlled expected receipt count is the denominator; producer event counts are not substituted",
            "matrix is fixed: C0/C1/C2/C3 only at concurrency 1; C0/C3 at 4/8/16/32; 3 repeats per cell",
            "C1/C2 high-concurrency cells are not planned by the protocol, not missing attempts",
            "block condition order is a fixed-seed shuffle archived in blocks[].condition_order",
            "workload receipt latency/token/cost are self-reported claims, not runtime usage or alert evidence",
            "runtime Observer usage and verified alert association remain unknown until wired; null is not zero",
            "budget is confirmed, not a hard provider cost cap; execution stops on reached budget or unknown cost",
            "single host and kernel; no multihost or cross-kernel inference",
            "collector attachment is unproven by a 20 ms liveness probe; attachment proof requires persisted checkpoint sources[].attached > 0",
            "resource samples cover same-host /proc and SQLite file size; kernel memory is system-wide, not a collector-only delta",
            "controlled capture is scoped to successfully completed controlled file-open operations; it is not global syscall/ringbuf loss",
            "C3 requires an llm_env/llm_config configuration marker; mock, unknown, or missing evidence is not a valid C3 run and the marker is not model-call efficacy proof",
            "no ONNX/classifier flag; ONNX is removed from the product",
            "same-UID output directories are not a security sandbox and do not provide auth isolation",
            "the driver never sets MTL_ALLOW_PAID and never infers a budget",
        ],
        "host": {"kernel": platform.release(), "machine": platform.machine()},
    }
    plan["plan_sha256"] = sha256_bytes(canonical(plan).encode())
    return plan


def verify_manifest(plan, env=None):
    if sha256_file(plan["binary"]) != plan["binary_sha256"]:
        raise ValueError("binary content changed since plan")
    if not plan.get("workload_argv") or plan["workload_argv"][0] != plan["workload_executable"]:
        raise ValueError("workload argv is not frozen to the resolved executable")
    executable = Path(plan["workload_executable"])
    if not executable.is_file():
        raise ValueError("workload executable missing since plan")
    if sha256_file(executable) != plan["workload_executable_sha256"]:
        raise ValueError("workload executable content changed since plan")
    if workload_input_binding(plan["workload_argv"]) != plan.get("workload_input_files", {}):
        raise ValueError("workload input content changed since plan")
    manifest = dict(plan)
    recorded = manifest.pop("plan_sha256")
    if sha256_bytes(canonical(manifest).encode()) != recorded:
        raise ValueError("plan changed since binding")
    if config_file_binding() != plan["config_file_sha256"]:
        raise ValueError("Observer config file changed since plan")
    if environment_binding(env)["sha256"] != plan["config_env_sha256"]:
        raise ValueError("config environment changed since plan")
    return True


def percentile(values, fraction):
    """Nearest-rank percentile; preserves the observed sample, no interpolation."""
    if not values or not 0 < fraction <= 1:
        raise ValueError("percentile requires samples and a fraction in (0, 1]")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _timestamp(receipt, key, now_ns):
    if key not in receipt:
        return None
    value = receipt[key]
    if type(value) is not int or value < 0:
        raise ValueError(f"{key} timestamp must be a non-negative integer")
    if value > now_ns:
        raise ValueError(f"{key} timestamp is in the future")
    return value


def _latency_metric(nanoseconds):
    if not nanoseconds:
        return None
    milliseconds = [value / 1_000_000 for value in nanoseconds]
    return {
        "samples": len(milliseconds), "min": min(milliseconds),
        "p50": percentile(milliseconds, .50), "p95": percentile(milliseconds, .95),
        "p99": percentile(milliseconds, .99) if len(milliseconds) >= 100 else None,
        "max": max(milliseconds),
        "percentile_rule": "nearest rank; p99 only with >=100 samples, otherwise p95 and max",
    }


def _unknown_tokens():
    return {"input_tokens": None, "output_tokens": None, "total_tokens": None, "known": False}


def summarize_receipts(plan, condition, receipts, *, now_ns=None):
    """Summarize workload receipts; capture loss is separate and may stay null."""
    if now_ns is None:
        now_ns = time.time_ns()
    if type(now_ns) is not int or now_ns < 0:
        raise ValueError("now_ns must be a non-negative integer")
    expected = condition.get("expected_receipts")
    if type(expected) is not int or expected < 1:
        raise ValueError("condition is missing a positive expected receipt count")
    seen = set()
    normalized = []
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, dict):
            raise ValueError(f"receipt {index} is not an object")
        receipt_id = receipt.get("receipt_id")
        if not isinstance(receipt_id, str) or not receipt_id:
            raise ValueError(f"receipt {index} has no explicit receipt_id")
        if receipt_id in seen:
            raise ValueError(f"duplicate receipt_id: {receipt_id}")
        seen.add(receipt_id)
        values = {key: _timestamp(receipt, key, now_ns) for key in
                  ("started_ns", "completed_ns", "event_ns", "claim_ns", "alert_ns")}
        if values["started_ns"] is not None and values["completed_ns"] is not None:
            if values["completed_ns"] < values["started_ns"]:
                raise ValueError(f"{receipt_id} completed before it started")
        for earlier, later in (("event_ns", "claim_ns"), ("claim_ns", "alert_ns"),
                               ("event_ns", "alert_ns")):
            if values[earlier] is not None and values[later] is not None:
                if values[later] < values[earlier]:
                    raise ValueError(f"{receipt_id} timestamp order is invalid: {later} < {earlier}")
        for key in ("input_tokens", "output_tokens"):
            if key in receipt and (type(receipt[key]) is not int or receipt[key] < 0):
                raise ValueError(f"{receipt_id} {key} must be a non-negative integer")
        if "cost_usd" in receipt:
            value = receipt["cost_usd"]
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or value < 0):
                raise ValueError(f"{receipt_id} cost_usd must be a non-negative finite number")
        normalized.append((receipt, values))

    observed = len(normalized)
    if observed > expected:
        raise ValueError(f"observed receipts {observed} exceeds expected {expected}")
    missing = expected - observed
    complete = missing == 0
    event_to_claim = [values["claim_ns"] - values["event_ns"] for _, values in normalized
                      if values["claim_ns"] is not None and values["event_ns"] is not None]
    claim_to_alert = [values["alert_ns"] - values["claim_ns"] for _, values in normalized
                      if values["alert_ns"] is not None and values["claim_ns"] is not None]

    tokens_known = complete and all(
        type(receipt.get("input_tokens")) is int and receipt["input_tokens"] >= 0
        and type(receipt.get("output_tokens")) is int and receipt["output_tokens"] >= 0
        for receipt, _ in normalized)
    if tokens_known:
        input_tokens = sum(receipt["input_tokens"] for receipt, _ in normalized)
        output_tokens = sum(receipt["output_tokens"] for receipt, _ in normalized)
        token_usage = {"input_tokens": input_tokens, "output_tokens": output_tokens,
                       "total_tokens": input_tokens + output_tokens, "known": True}
    else:
        token_usage = _unknown_tokens()
    cost_known = complete and all(
        isinstance(receipt.get("cost_usd"), (int, float))
        and not isinstance(receipt.get("cost_usd"), bool)
        and math.isfinite(receipt["cost_usd"]) and receipt["cost_usd"] >= 0
        for receipt, _ in normalized)
    cost = sum(receipt["cost_usd"] for receipt, _ in normalized) if cost_known else None

    latency_claims = {
        "event_to_claim": _latency_metric(event_to_claim),
        "claim_to_alert": _latency_metric(claim_to_alert),
    }
    return {
        "workload_expected_receipts": expected,
        "workload_observed_receipts": observed,
        "workload_missing_receipts": missing,
        "workload_missing_fraction": missing / expected,
        "workload_completion_rate": observed / expected,
        "workload_completion_basis": "controlled_operation_receipts",
        "controlled_operations_expected": expected,
        "receipts_complete": complete,
        "latency_ms": latency_claims,
        "latency_provenance": {
            "event_to_claim": {"event_ns": "workload_stdout_receipt",
                               "claim_ns": "workload_stdout_receipt"},
            "claim_to_alert": {"claim_ns": "workload_stdout_receipt",
                               "alert_ns": "workload_stdout_receipt"},
        },
        "token_usage": token_usage,
        "cost_usd": cost,
        "measurement_source": {
            "receipts": "workload_stdout_jsonl",
            "trust": "not_a_runtime_measurement",
            "limitations": (
                "latency/token/cost fields are workload self-reports; they do not "
                "prove runtime Observer usage or verified alert association"),
        },
        "receipt_claims": {
            "source": "workload_stdout_receipts",
            "trust": "not_a_runtime_measurement",
            "latency_ms": latency_claims,
            "token_usage": token_usage,
            "cost_usd": cost,
            "limitations": (
                "self-reported by the controlled workload; not runtime Observer "
                "usage, not verified alert latency, and missing stays null"),
        },
    }


def _valid_budget(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def blocked_conditions(plan, *, env=None, max_budget_usd=None, condition_ids=None):
    """Paid profiles blocked in a fixed condition order, never reordered.

    Selection filters the guard too: a calibration run that selects only C0
    does not require a paid allowance for the unselected paid rows.
    """
    env = os.environ if env is None else env
    selected = selected_condition_runs(plan, condition_ids)
    reasons = []
    if env.get(PAID_ENV) != "1":
        reasons.append(f"paid execution requires pre-existing {PAID_ENV}=1")
    if not _valid_budget(max_budget_usd):
        reasons.append(f"paid execution requires explicit {BUDGET_FLAG} > 0")
    if not reasons:
        return []
    reason = "; ".join(reasons)
    return [{"condition_id": row["condition_id"], "profile": row["profile"], "reason": reason}
            for row in selected if row["collector_potentially_paid"]]


def check_execution(plan, *, execute, env=None, max_budget_usd=None,
                    condition_ids=None):
    """Guard before any process or output directory exists."""
    selected = selected_condition_runs(plan, condition_ids)
    if not execute:
        return {"execute": False, "confirmed_budget_usd": None,
                "budget_semantics": BUDGET_SEMANTICS}
    blocked = blocked_conditions(plan, env=env, max_budget_usd=max_budget_usd,
                                 condition_ids=condition_ids)
    if blocked:
        raise ValueError(blocked[0]["reason"])
    return {"execute": True,
            "confirmed_budget_usd": (float(max_budget_usd)
                                     if max_budget_usd is not None else None),
            "budget_semantics": BUDGET_SEMANTICS}


def budget_state(results, confirmed_budget_usd):
    """Evaluate only paid conditions; missing cost is unknown, never zero."""
    if not _valid_budget(confirmed_budget_usd):
        raise ValueError("confirmed budget must be a positive finite number")
    costs = []
    for result in results:
        if result.get("profile") not in PAID_PROFILES:
            continue
        receipts = result.get("receipts")
        cost = receipts.get("cost_usd") if isinstance(receipts, dict) else None
        if cost is None:
            return "cost_unknown"
        costs.append(cost)
    if sum(costs) >= confirmed_budget_usd:
        return "budget_reached"
    return "within_confirmed_budget"


def _collector_environment(plan, env):
    environment = dict(os.environ if env is None else env)
    environment["MTAGENTLENS_AGENT_COMMS"] = plan["agent_comm"]
    environment["MTAGENTLENS_WORKSPACE_ROOT"] = plan["out"]
    environment.setdefault("RUST_LOG", "info")
    return environment


def _workload_environment(plan, condition, index, env):
    environment = dict(os.environ if env is None else env)
    environment.update({
        "MTL_OVERHEAD_BLOCK": condition["block_id"],
        "MTL_OVERHEAD_CONDITION_ID": condition["condition_id"],
        "MTL_OVERHEAD_PROFILE": condition["profile"],
        "MTL_OVERHEAD_CONCURRENCY": str(condition["concurrency"]),
        "MTL_OVERHEAD_INSTANCE_INDEX": str(index),
        "MTL_OVERHEAD_OPERATIONS": str(plan["operations_per_instance"]),
    })
    return environment


def _proc_status(pid: int) -> dict:
    try:
        lines = Path(f"/proc/{pid}/status").read_text().splitlines()
    except OSError:
        return {}
    values = {}
    for line in lines:
        key, _, value = line.partition(":")
        if key in ("VmRSS", "VmSize", "Threads", "PPid", "Uid"):
            values[key] = value.strip()
    return values


def _proc_cpu_seconds(pid: int) -> float | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    close = raw.rfind(")")
    if close < 0:
        return None
    fields = raw[close + 2:].split()
    if len(fields) < 15:
        return None
    ticks = int(fields[11]) + int(fields[12])
    return ticks / os.sysconf("SC_CLK_TCK")


def _proc_children(pid: int) -> list[int]:
    try:
        text = Path(f"/proc/{pid}/task/{pid}/children").read_text().strip()
    except OSError:
        return []
    children = []
    for token in text.split():
        try:
            children.append(int(token))
        except ValueError:
            continue
    return children


def descendant_pids(roots) -> set[int]:
    found: set[int] = set()
    pending = [pid for pid in roots if isinstance(pid, int) and pid > 0]
    while pending:
        pid = pending.pop()
        if pid in found:
            continue
        found.add(pid)
        pending.extend(_proc_children(pid))
    return found


def _tree_snapshot(roots) -> dict:
    pids = descendant_pids(roots)
    rss_kib = 0
    cpu_seconds = 0.0
    alive = 0
    cpu_by_process = {}
    for pid in pids:
        status = _proc_status(pid)
        if not status:
            continue
        alive += 1
        rss = status.get("VmRSS", "").split()
        if rss and rss[0].isdigit():
            rss_kib += int(rss[0])
        cpu = _proc_cpu_seconds(pid)
        if cpu is not None:
            cpu_seconds += cpu
            try:
                stat_text = Path(f'/proc/{pid}/stat').read_text()
                birth = stat_text[stat_text.rfind(')')+2:].split()[19]
                cpu_by_process[f'{pid}:{birth}'] = cpu
            except (OSError,IndexError):
                pass
    return {"processes": alive, "rss_kib": rss_kib, "cpu_seconds": cpu_seconds,
            "cpu_by_process": cpu_by_process}


def _kernel_memory_kib() -> dict:
    wanted = {"Slab", "SReclaimable", "KernelStack", "PageTables", "MemAvailable"}
    values = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            if key not in wanted:
                continue
            fields = rest.split()
            if fields and fields[0].isdigit():
                values[key] = int(fields[0])
    except OSError:
        return {}
    return values


def _file_bytes(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        return path.stat().st_size
    except OSError:
        return None


def _database_bytes(path: Path | None) -> dict:
    if path is None:
        return {"main": None, "wal": None, "total": None}
    main = _file_bytes(path)
    wal = _file_bytes(path.with_name(path.name + "-wal"))
    total = None if main is None else main + (wal or 0)
    return {"main": main, "wal": wal, "total": total}


def _series_stats(values, label="value"):
    values = [value for value in values if value is not None]
    if not values:
        return None
    return {
        "samples": len(values),
        "min": min(values),
        "mean": sum(values) / len(values),
        "p50": percentile(values, .50),
        "p95": percentile(values, .95),
        "max": max(values),
        "unit": label,
    }


class ResourceSampler(threading.Thread):
    """Sample real /proc, database size and kernel memory while conditions run."""

    def __init__(self, *, collector_pid, workload_pids, database, interval_s,
                 trace=None):
        super().__init__(daemon=True, name="mtl-overhead-sampler")
        self.collector_pid = collector_pid
        self.workload_pids = list(workload_pids)
        self.database = Path(database) if database is not None else None
        self.trace = Path(trace) if trace is not None else None
        self.interval_s = interval_s
        self.stop_event = threading.Event()
        self.rows: list[dict] = []
        self._previous = None
        self._sample_started = time.monotonic()
        self._lifetime_cpu = {"collector": {}, "workloads": {}}

    def _sample(self) -> dict:
        now = time.monotonic()
        collector = _tree_snapshot([self.collector_pid]) if self.collector_pid else {
            "processes": 0, "rss_kib": 0, "cpu_seconds": 0.0}
        workloads = _tree_snapshot(self.workload_pids)
        # A process disappearing must not subtract all of its prior CPU time.
        # PID plus start ticks separates reused numeric PIDs. Very short-lived
        # children unseen between samples remain an explicit sampling limit.
        for name, snapshot in (("collector", collector), ("workloads", workloads)):
            for identity, value in snapshot.pop('cpu_by_process', {}).items():
                self._lifetime_cpu[name][identity] = max(
                    value,self._lifetime_cpu[name].get(identity,0))
            if self._lifetime_cpu[name]:
                snapshot['cpu_seconds'] = sum(self._lifetime_cpu[name].values())
        row = {
            "elapsed_s": now - self._sample_started,
            "collector": {**collector, "cpu_percent": None},
            "workloads": {**workloads, "cpu_percent": None},
            "database": _database_bytes(self.database),
            "trace_bytes": _file_bytes(self.trace),
            "kernel_memory_kib": _kernel_memory_kib(),
        }
        if self._previous is not None:
            elapsed = max(1e-9, now - self._previous["monotonic"])
            for key in ("collector", "workloads"):
                delta = row[key]["cpu_seconds"] - self._previous[key]["cpu_seconds"]
                row[key]["cpu_percent"] = max(0.0, delta / elapsed * 100.0)
        self._previous = {
            "monotonic": now,
            "collector": {"cpu_seconds": collector["cpu_seconds"]},
            "workloads": {"cpu_seconds": workloads["cpu_seconds"]},
        }
        return row

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.rows.append(self._sample())
            except (OSError, ValueError):
                continue
            self.stop_event.wait(self.interval_s)

    def stop(self):
        self.stop_event.set()
        self.join(timeout=max(1.0, self.interval_s * 4))

    def summary(self) -> dict:
        if not self.rows:
            return {"available": False, "reason": "no resource samples were collected"}
        final_db = _database_bytes(self.database)
        collector_cpu = [row["collector"]["cpu_percent"] for row in self.rows]
        workload_cpu = [row["workloads"]["cpu_percent"] for row in self.rows]
        stride = max(1, len(self.rows) // 120)
        series = [{
            "elapsed_s": row["elapsed_s"],
            "collector_rss_kib": row["collector"]["rss_kib"],
            "collector_cpu_percent": row["collector"]["cpu_percent"],
            "collector_processes": row["collector"]["processes"],
            "workload_rss_kib": row["workloads"]["rss_kib"],
            "workload_cpu_percent": row["workloads"]["cpu_percent"],
            "workload_processes": row["workloads"]["processes"],
            "collector_cpu_seconds": row["collector"]["cpu_seconds"],
            "workload_cpu_seconds": row["workloads"]["cpu_seconds"],
            "trace_bytes": row["trace_bytes"],
            "database_main_bytes": row["database"]["main"],
            "database_wal_bytes": row["database"]["wal"],
            "database_bytes": row["database"]["total"],
        } for row in self.rows[::stride]]
        kernel_rows = [row["kernel_memory_kib"] for row in self.rows if row["kernel_memory_kib"]]
        kernel_summary = {}
        for key in ("Slab", "SReclaimable", "KernelStack", "PageTables", "MemAvailable"):
            values = [row[key] for row in kernel_rows if key in row]
            stats = _series_stats(values, label="KiB")
            if stats is not None:
                kernel_summary[key] = stats
        db_totals = [row["database"]["total"] for row in self.rows
                     if row["database"]["total"] is not None]
        db_main = [row["database"]["main"] for row in self.rows
                   if row["database"]["main"] is not None]
        db_wal = [row["database"]["wal"] for row in self.rows
                  if row["database"]["wal"] is not None]
        trace_values = [row["trace_bytes"] for row in self.rows
                        if row["trace_bytes"] is not None]
        trace_final = _file_bytes(self.trace)

        def file_stats(values, final):
            stats = _series_stats(values, "bytes")
            return {
                "final": final,
                "peak": max(values) if values else None,
                "stats": stats,
            }

        return {
            "available": True,
            "scope": "same-host /proc sampling; tree follows /proc/<pid>/task/<pid>/children",
            "sample_count": len(self.rows),
            "sample_interval_ms": int(round(self.interval_s * 1000)),
            "collector": {
                "rss_kib": _series_stats([row["collector"]["rss_kib"] for row in self.rows], "KiB"),
                "cpu_percent": _series_stats(collector_cpu, "percent"),
                "cpu_seconds": _series_stats(
                    [row["collector"]["cpu_seconds"] for row in self.rows], "seconds"),
                "processes": _series_stats(
                    [row["collector"]["processes"] for row in self.rows], "count"),
            },
            "workloads": {
                "rss_kib": _series_stats([row["workloads"]["rss_kib"] for row in self.rows], "KiB"),
                "cpu_percent": _series_stats(workload_cpu, "percent"),
                "cpu_seconds": _series_stats(
                    [row["workloads"]["cpu_seconds"] for row in self.rows], "seconds"),
                "processes": _series_stats(
                    [row["workloads"]["processes"] for row in self.rows], "count"),
            },
            "trace_bytes": file_stats(trace_values, trace_final),
            "database_bytes": {
                "peak": max(db_totals) if db_totals else None,
                "final": final_db,
                "main": file_stats(db_main, final_db["main"]),
                "wal": file_stats(db_wal, final_db["wal"]),
                "total": file_stats(db_totals, final_db["total"]),
            },
            "kernel_memory": kernel_summary,
            "series": series,
            "series_stride": stride,
            "limitations": [
                "system-wide kernel memory includes unrelated activity and is not a collector-only delta",
                "CPU percent is derived from /proc utime+stime across the observed process tree",
                "cpu_seconds is the cumulative /proc value observed while processes are alive; exited children are not reconstructed",
                "WAL absent means no WAL file at sample time; missing database/trace files stay null, not zero",
                "short conditions may have few samples; no p99 is reported",
            ],
        }


def _read_checkpoint_quality(database: Path) -> dict | None:
    if database is None or not database.is_file():
        return None
    try:
        with sqlite3.connect(
            database.resolve().as_uri() + "?mode=ro", uri=True
        ) as conn:
            rows = list(conn.execute(
                "SELECT payload_json FROM analysis_checkpoints "
                "WHERE checkpoint_kind='frozen-analysis' ORDER BY rowid"
            ))
    except sqlite3.Error:
        return None
    if not rows:
        return None
    payload = rows[-1][0]
    try:
        from tools.annotator.payload_codec import decode_checkpoint_payload
        value = json.loads(decode_checkpoint_payload(payload))
    except (TypeError, ValueError):
        return None
    quality = value.get("capture_quality") if isinstance(value, dict) else None
    return quality if isinstance(quality, dict) else None


def _collector_markers(stdout_path: Path) -> dict:
    markers = {}
    try:
        lines = stdout_path.read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    wanted = {
        "ssl_enabled": "✓ SSL monitoring enabled",
        "process_enabled": "✓ Process monitoring enabled",
        "runtime_enabled": "✓ Responsibility runtime enabled",
        "capture_only": "✓ Capture-only",
        "graceful_shutdown": "Trace agent shutting down gracefully",
    }
    for provider, marker_key in OBSERVER_MARKER_KEYS.items():
        wanted[marker_key] = OBSERVER_PROVIDER_MARKERS[provider]
    for key, needle in wanted.items():
        markers[key] = any(needle in line for line in lines)
    present = [candidate for candidate in ("llm_env", "llm_config", "mock", "disabled")
               if markers[OBSERVER_MARKER_KEYS[candidate]]]
    markers["observer_provider"] = (
        present[0] if len(present) == 1 else ("unknown" if not present else "ambiguous"))
    return markers


def _observer_provider_evidence(markers: dict) -> dict:
    provider = markers["observer_provider"]
    return {
        "provider": provider,
        "source": "collector stdout marker",
        "marker": OBSERVER_PROVIDER_MARKERS.get(provider),
        "marker_key": OBSERVER_MARKER_KEYS.get(provider),
        "known_real_markers": list(REAL_OBSERVER_PROVIDERS),
        "configuration_proof_only": True,
        "claim": (
            "provider configuration marker only; it does not prove a model call, "
            "response, or task efficacy"),
    }


def _source_kind(name: str) -> str:
    return str(name).split(":", 1)[0].strip().lower()


def _required_source_kinds(values=None) -> tuple[str, ...]:
    values = REQUIRED_SOURCE_KINDS if values is None else values
    if isinstance(values, str) or not hasattr(values, "__iter__"):
        raise ValueError("required source kinds must be an iterable of names, not a string")
    kinds = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("required source kinds must be non-empty strings")
        kind = _source_kind(value)
        if kind not in ("ssl", "process"):
            raise ValueError(f"unsupported required source kind {value!r}")
        if kind not in kinds:
            kinds.append(kind)
    if not kinds:
        raise ValueError("required source kinds must not be empty")
    return tuple(kinds)


def _source_attached(kind: str, row: dict) -> bool:
    if not isinstance(row, dict) or row.get("attach_error") is not None:
        return False
    if row.get("started") is not True:
        return False
    if kind == "ssl":
        attached = row.get("attached")
        return type(attached) is int and attached > 0
    if kind == "process":
        return row.get("initial_snapshot_complete") is True
    return False


def _terminal_checks(row: dict) -> dict:
    return {
        "final_stats_seen": row.get("final_stats_seen") is True,
        "drain_complete": row.get("drain_complete") is True,
        "exit_status_zero": type(row.get("exit_status")) is int and row.get("exit_status") == 0,
        "no_lost_events": type(row.get("lost")) is int and row.get("lost") == 0,
        "no_rate_limit_drops": (
            type(row.get("rate_limited")) is int and row.get("rate_limited") == 0),
        "no_parse_failures": (
            row.get("stdout_skipped") in (None, 0)
            and row.get("stdout_error") is not True
            and row.get("stderr_error") is not True),
        "not_forced_kill": row.get("forced_kill") is not True,
    }


def _row_groups(source_rows: dict, required: tuple[str, ...]) -> dict:
    groups = {kind: [] for kind in required}
    for name, row in source_rows.items():
        kind = _source_kind(name)
        if kind in groups:
            groups[kind].append((name, row))
    return groups


def _attachment_check(source_rows: dict, required: tuple[str, ...]) -> tuple[bool, list, dict]:
    groups = _row_groups(source_rows, required)
    missing = [kind for kind in required if not groups[kind]]
    checks = {
        kind: [{"source": name, "attached": _source_attached(kind, row)}
               for name, row in groups[kind]]
        for kind in required
    }
    proven = (
        not missing
        and bool(checks)
        and all(item["attached"] for items in checks.values() for item in items))
    return proven, missing, checks


def _trace_capture_health(trace_log: Path, required: tuple[str, ...]) -> dict:
    latest = None
    try:
        lines = Path(trace_log).read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        data = event.get("data")
        if isinstance(data, dict) and data.get("event") == "CAPTURE_HEALTH":
            latest = data
    if latest is None:
        return {
            "evidence_source": "trace_log_capture_health",
            "observed": False,
            "status": "missing",
            "reason": "no CAPTURE_HEALTH event in trace.log",
            "required_source_kinds": list(required),
            "missing_source_kinds": list(required),
            "sources": {},
            "attachment": {"proven": False, "checks": {}},
            "final_flush": {
                "observed": None, "applicable": False, "checks": {},
                "scope": "trace_transport_health_only",
                "basis": "C1 capture-only has no responsibility database and no CAPTURE_HEALTH event",
            },
        }
    health = latest.get("health")
    if not isinstance(health, dict):
        return {
            "evidence_source": "trace_log_capture_health",
            "observed": True,
            "status": "invalid",
            "reason": "latest CAPTURE_HEALTH event has no health object",
            "required_source_kinds": list(required),
            "missing_source_kinds": list(required),
            "sources": {},
            "attachment": {"proven": False, "checks": {}},
            "final_flush": {
                "observed": None, "applicable": False, "checks": {},
                "scope": "trace_transport_health_only",
                "basis": "latest CAPTURE_HEALTH health is malformed; no fallback",
            },
        }
    sources = health.get("sources")
    source_rows = {
        str(name): dict(row) for name, row in sources.items()
        if isinstance(name, str) and isinstance(row, dict)
    } if isinstance(sources, dict) else {}
    if not isinstance(sources, dict):
        return {
            "evidence_source": "trace_log_capture_health",
            "observed": True,
            "status": "invalid",
            "reason": "latest CAPTURE_HEALTH health has no sources object",
            "required_source_kinds": list(required),
            "missing_source_kinds": list(required),
            "sources": {},
            "attachment": {"proven": False, "checks": {}},
            "final_flush": {
                "observed": None, "applicable": False, "checks": {},
                "scope": "trace_transport_health_only",
                "basis": "latest CAPTURE_HEALTH sources are malformed; no fallback",
            },
        }
    proven, missing, checks = _attachment_check(source_rows, required)
    groups = _row_groups(source_rows, required)
    terminal = {
        kind: checks and all(all(_terminal_checks(row).values())
                             for _, row in groups[kind])
        for kind in required
    }
    terminal_checks = {
        name: _terminal_checks(row) for kind in required for name, row in groups[kind]
    }
    healthy = (
        health.get("status") == "healthy"
        and not missing
        and proven
        and all(terminal.values()))
    return {
        "evidence_source": "trace_log_capture_health",
        "observed": True,
        "status": "healthy" if healthy else "unhealthy",
        "snapshot_status": health.get("status"),
        "required_source_kinds": list(required),
        "missing_source_kinds": missing,
        "sources": source_rows,
        "attachment": {"proven": proven, "checks": checks},
        "terminal_checks": terminal_checks,
        "final_flush": {
            "observed": None,
            "applicable": False,
            "checks": terminal_checks,
            "scope": "trace_transport_health_only",
            "basis": (
                "C1 capture-only has no responsibility database; CAPTURE_HEALTH "
                "final-stats/drain/exit/loss checks are transport health, not DB flush proof"),
        },
    }


def _checkpoint_sources(quality: dict) -> dict:
    sources = quality.get("sources")
    source_rows = {}
    if isinstance(sources, dict):
        for name, row in sources.items():
            if not isinstance(row, dict):
                continue
            source_rows[str(name)] = {
                key: row.get(key) for key in (
                    "started", "attached", "attach_error", "final_stats_seen",
                    "drain_complete", "exit_status", "lost", "rate_limited",
                    "stdout_skipped", "stdout_error", "stderr_error",
                    "initial_snapshot_complete", "forced_kill",
                )
            }
    return source_rows


def _checkpoint_final_flush(source_rows: dict, analysis: dict | None,
                            required: tuple[str, ...]) -> dict:
    groups = _row_groups(source_rows, required)
    missing = [kind for kind in required if not groups[kind]]
    checks = {
        name: _terminal_checks(row) for kind in required for name, row in groups[kind]
    }
    observed = (
        not missing
        and isinstance(analysis, dict)
        and analysis.get("persist_flush_failed") is False
        and bool(checks)
        and all(all(values[key] for key in (
            "final_stats_seen", "drain_complete", "exit_status_zero", "not_forced_kill"))
            for values in checks.values()))
    return {
        "observed": bool(observed),
        "applicable": True,
        "checks": checks,
        "scope": "checkpoint_time",
        "basis": (
            "checkpoint persist_flush_failed=false plus required-source "
            "final_stats_seen/drain_complete/exit_status=0/not-forced-kill checks; "
            "upstream loss, rate limits and parse skips remain separate quality counters"),
        "limitations": (
            "checkpoint counters do not prove writes or flushes after the "
            "recorded checkpoint"),
    }


def _tail_ringbuf_diagnostics(stderr_path: Path) -> list[dict]:
    latest = {}
    try:
        lines = stderr_path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if not isinstance(value, dict):
            continue
        event = value.get("event")
        if isinstance(event, str) and event.endswith("RINGBUF_STATS"):
            latest[event] = value
    return [latest[key] for key in sorted(latest)]


def inspect_collector_evidence(stdout_path: Path, stderr_path: Path,
                               database: Path | None, trace_log: Path | None = None,
                               *, required_source_kinds=None) -> dict:
    """Parse startup markers, terminal capture health and frozen checkpoint data.

    C2/C3 use the latest persisted checkpoint; C1 capture-only has no DB, so its
    terminal health comes from the CAPTURE_HEALTH event FileLogger writes to
    trace.log. Attachment proof requires every planned source kind and explicit
    TLS attached/Process initial-snapshot evidence. Final-stats/drain/exit/
    parse/loss checks are reported separately and never promoted to capture loss.
    """
    required = _required_source_kinds(required_source_kinds)
    markers = _collector_markers(Path(stdout_path))
    diagnostics = _tail_ringbuf_diagnostics(Path(stderr_path))
    quality = _read_checkpoint_quality(database)
    trace_health = (
        _trace_capture_health(Path(trace_log), required)
        if trace_log is not None else None)
    if isinstance(quality, dict):
        source_rows = _checkpoint_sources(quality)
        analysis = quality.get("analysis")
        proven, missing, checks = _attachment_check(source_rows, required)
        final_flush = _checkpoint_final_flush(source_rows, analysis, required)
        evidence_source = "persisted_checkpoint"
        basis = (
            "latest persisted checkpoint: explicit started=true and TLS "
            "attached>0 or Process initial_snapshot_complete=true")
        persistence = {
            key: analysis.get(key) for key in (
                "persist_dropped", "persist_closed", "persist_failures",
                "persist_flush_failed", "raw_log_failures",
            )
        } if isinstance(analysis, dict) else None
    else:
        source_rows = trace_health["sources"] if trace_health is not None else {}
        proven = bool(trace_health and trace_health["attachment"]["proven"])
        missing = (
            trace_health["missing_source_kinds"] if trace_health is not None
            else list(required))
        checks = (
            trace_health["attachment"]["checks"] if trace_health is not None else {})
        final_flush = (
            trace_health["final_flush"] if trace_health is not None else {
                "observed": None,
                "applicable": database is not None,
                "checks": {},
                "scope": None,
                "basis": "no persisted checkpoint or CAPTURE_HEALTH event; no proof",
            })
        evidence_source = (
            trace_health["evidence_source"] if trace_health is not None else "missing")
        basis = (
            "trace.log CAPTURE_HEALTH: explicit started=true and TLS attached>0 "
            "or Process initial_snapshot_complete=true")
        persistence = None
    return {
        "schema": "mtagentlens.overhead-collector-evidence/v1",
        "startup_markers": markers,
        "observer_provider": markers["observer_provider"],
        "observer_provider_evidence": _observer_provider_evidence(markers),
        "ringbuf_diagnostics": diagnostics,
        "attachment": {
            "proven": bool(proven),
            "evidence_source": evidence_source,
            "required_source_kinds": list(required),
            "missing_source_kinds": missing,
            "checks": checks,
            "basis": basis,
            "sources": source_rows,
        },
        "persistence": persistence,
        "final_flush": final_flush,
        "trace_health": trace_health,
        "process_tls_health": {
            name: {"final_stats_seen": row.get("final_stats_seen"),
                   "drain_complete": row.get("drain_complete"),
                   "lost": row.get("lost"), "rate_limited": row.get("rate_limited")}
            for name, row in source_rows.items()
        },
        "limitations": [
            "stdout markers show configured/started runners, not proof of BPF attachment",
            "ring-buffer diagnostics are helper stderr, not operation-level capture loss",
            "checkpoint counters are checkpoint-time and do not prove writes or flushes after the recorded checkpoint",
            "C1 capture-only has no responsibility DB; trace CAPTURE_HEALTH is transport health, not DB final-flush proof",
            "observer provider markers are configuration evidence only, not model-call efficacy",
        ],
    }


def load_receipts(paths):
    receipts, reasons = controlled_capture.load_receipts(paths)
    if reasons:
        raise ValueError("; ".join(reasons))
    return receipts


def _kill_group(process):
    # Do not fall back to process.kill(): the PID may be unrelated or reused.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def collector_shutdown_timeout(plan):
    """Allow the frozen timeout for final Observer review and persistence drain."""
    return max(1.0, float(plan["timeout_s"]))


def run_condition(plan, condition, env=None):
    """Run one condition with real subprocesses; failures are lifecycle data."""
    verify_manifest(plan, env)
    output = Path(condition["output"])
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    collector_cwd = output
    if condition["profile"] == "C3":
        collector_cwd = output / ".collector-runtime"
        collector_cwd.mkdir(mode=0o700)
        for relative, digest in plan["config_file_sha256"].items():
            if digest is not None:
                target = collector_cwd / relative
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(ROOT / relative, target)
                target.chmod(0o600)
                if sha256_file(target) != digest:
                    raise ValueError("Observer config file changed while materializing")
    workload_argv = list(plan["workload_argv"])
    if condition["workload_argv"] != workload_argv:
        raise ValueError("condition workload argv differs from the shared immutable plan argv")
    timeout = float(plan["timeout_s"])
    result = {
        "condition_id": condition["condition_id"], "block_id": condition["block_id"],
        "profile": condition["profile"], "concurrency": condition["concurrency"],
        "repeat": condition["repeat"], "status": "failed",
        "scientific_admission": "not_claimed",
        "collector": {"started": False, "returncode": None, "forced_kill": False,
                      "attachment_proven": False, "readiness": "unproven_20ms_probe",
                      "readiness_probe_ms": 20},
        "workloads": [], "receipts": None,
        "config_privacy": {"collector_runtime": str(collector_cwd) if condition["collector"] else None,
                           "c3_private_materialization": condition["profile"] == "C3",
                           "same_uid_not_sandbox": True, "auth_isolation": False},
    }
    collector = None
    collector_stdout_path = output / "collector.stdout"
    collector_stderr_path = output / "collector.stderr"
    output_name = None
    if condition["collector"]:
        command = list(condition["collector_command"])
        output_name = Path(command[command.index("-o") + 1])
    database_path = (
        Path(str(output_name) + ".responsibility.db")
        if output_name is not None and condition["profile"] != "C1" else None)
    result["database_path"] = str(database_path) if database_path else None
    sampler = None
    processes = []
    handles = []
    errors = []
    started = time.monotonic()
    deadline = started + timeout
    try:
        if condition["collector"]:
            collector_stdout = collector_stdout_path.open("w")
            collector_stderr = collector_stderr_path.open("w")
            handles.extend((collector_stdout, collector_stderr))
            collector = subprocess.Popen(list(condition["collector_command"]), cwd=collector_cwd,
                env=_collector_environment(plan, env), stdout=collector_stdout,
                stderr=collector_stderr, start_new_session=True)
            result["collector"].update({"started": True, "pid": collector.pid,
                                        "command": list(condition["collector_command"])})
            # Liveness only. Attachment is not proven by this sleep.
            time.sleep(0.02)
            if collector.poll() is not None:
                raise ValueError(f"collector exited before workloads with status {collector.returncode}")

        for index in range(condition["workload_instances"]):
            stdout = (output / f"workload-{index}.stdout").open("w")
            stderr = (output / f"workload-{index}.stderr").open("w")
            handles.extend((stdout, stderr))
            process = subprocess.Popen(workload_argv, cwd=output,
                env=_workload_environment(plan, condition, index, env),
                stdout=stdout, stderr=stderr, start_new_session=True)
            processes.append({"index": index, "process": process,
                              "stdout": str(output / f"workload-{index}.stdout"),
                              "stderr": str(output / f"workload-{index}.stderr")})

        sampler = ResourceSampler(
            collector_pid=collector.pid if collector is not None else None,
            workload_pids=[item["process"].pid for item in processes],
            database=database_path,
            trace=(Path(str(output_name)) if output_name is not None else None),
            interval_s=plan.get("sample_interval_ms", 100) / 1000.0,
        )
        sampler.start()

        timed_out = False
        for item in processes:
            process = item["process"]
            item["timed_out"] = False
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
                item["timed_out"] = True
                _kill_group(process)
                process.wait()
        if timed_out:
            errors.append(f"workload timed out after {timeout:g}s")
        failed = [item for item in processes if item["process"].returncode != 0]
        if failed and not errors:
            errors.append(f"workload {failed[0]['index']} exited with status {failed[0]['process'].returncode}")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        errors.append(str(error))
    finally:
        if collector is not None and collector.poll() is None:
            try:
                collector.send_signal(signal.SIGINT)
                collector.wait(timeout=collector_shutdown_timeout(plan))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(collector.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                collector.wait()
                result["collector"]["forced_kill"] = True
                errors.append("collector forced kill after shutdown timeout")
            except OSError as error:
                errors.append(f"collector shutdown failed: {error}")
        if collector is not None:
            result["collector"]["returncode"] = collector.returncode
            if collector.returncode != 0 and not any("collector" in error for error in errors):
                errors.append(f"collector exited with status {collector.returncode}")
            try:
                _kill_group(collector)
            except OSError as error:
                errors.append(f"collector group cleanup failed: {error}")
        for item in processes:
            process = item["process"]
            try:
                _kill_group(process)
            except OSError as error:
                errors.append(f"workload {item['index']} group cleanup failed: {error}")
            if process.poll() is None:
                process.wait()
            if "timed_out" not in item:
                item["timed_out"] = False
            result["workloads"].append({
                "index": item["index"], "pid": process.pid, "returncode": process.returncode,
                "timed_out": item["timed_out"], "stdout": item["stdout"], "stderr": item["stderr"]})
        if sampler is not None:
            sampler.stop()
            result["resources"] = sampler.summary()
        for handle in handles:
            try:
                handle.close()
            except OSError:
                pass

    if sampler is None:
        result["resources"] = {"available": False, "reason": "processes did not reach sampling"}
    if condition["collector"]:
        trace_health_path = (
            Path(str(output_name))
            if condition["profile"] == "C1" and output_name is not None else None)
        evidence = inspect_collector_evidence(
            collector_stdout_path, collector_stderr_path, database_path,
            trace_health_path,
            required_source_kinds=plan["required_source_kinds"],
        )
        result["collector"]["evidence"] = evidence
        result["collector"]["attachment_proven"] = evidence["attachment"]["proven"]
        result["collector"]["observer_provider"] = evidence["observer_provider"]
        result["collector"]["final_flush_observed"] = evidence["final_flush"]["observed"]
        if condition["profile"] == "C3":
            provider = evidence["observer_provider"]
            if provider == "mock":
                errors.append("C3 Observer provider is Mock, not a real provider marker")
            elif provider not in REAL_OBSERVER_PROVIDERS:
                errors.append(
                    f"C3 Observer provider {provider!r} is not a known real provider marker")

    receipt_paths = [Path(item["stdout"]) for item in result["workloads"]]
    raw_receipts = []
    try:
        raw_receipts = load_receipts(receipt_paths)
        result["receipts"] = summarize_receipts(plan, condition, raw_receipts,
                                                now_ns=time.time_ns())
        result["work_completion_rate"] = result["receipts"]["workload_completion_rate"]
        evidence = result.get("collector", {}).get("evidence") or {}
        result["controlled_capture"] = controlled_capture.build_controlled_capture(
            profile=condition["profile"], condition_id=condition["condition_id"],
            receipts=raw_receipts, receipt_paths=[str(path) for path in receipt_paths],
            expected_receipts=condition["expected_receipts"],
            trace_path=(Path(str(output_name)) if output_name is not None else None),
            database_path=database_path,
            health={
                "attachment_proven": bool(evidence.get("attachment", {}).get("proven")),
                "final_flush_observed": evidence.get("final_flush", {}).get("observed"),
            },
            ringbuf_diagnostics=evidence.get("ringbuf_diagnostics"),
        )
    except (OSError, ValueError) as error:
        errors.append(str(error))
        result["controlled_capture"] = {
            "schema": controlled_capture.SCHEMA,
            "adapter_version": controlled_capture.ADAPTER_VERSION,
            "condition_id": condition["condition_id"], "profile": condition["profile"],
            "status": "not_estimable", "estimable": False, "error": str(error),
            "reasons": ["adapter_failed"],
        }
    if errors:
        result["status"] = "failed"
        result["error"] = "; ".join(errors)
    elif result["receipts"] is not None and not result["receipts"]["receipts_complete"]:
        result["status"] = "degraded"
    else:
        result["status"] = "completed"
    result["duration_s"] = time.monotonic() - started
    (output / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def execute_plan(plan, *, execute=False, env=None, max_budget_usd=None,
                 condition_ids=None):
    selected = selected_condition_runs(plan, condition_ids)
    guard = check_execution(plan, execute=execute, env=env,
                            max_budget_usd=max_budget_usd,
                            condition_ids=condition_ids)
    if not execute:
        return {"executed": False, "plan_sha256": plan["plan_sha256"],
                "condition_runs": 0, "planned_condition_runs": len(plan["condition_runs"]),
                "selected_condition_runs": len(selected)}
    verify_manifest(plan, env)
    output = Path(plan["out"])
    output.mkdir(parents=True, exist_ok=False)
    results = []
    paid_selected = any(row["collector_potentially_paid"] for row in selected)
    budget = ("within_confirmed_budget" if paid_selected
              else "no_paid_condition_selected")
    stop_all = False
    unknown_cost = False
    for condition in selected:
        if stop_all:
            results.append({"condition_id": condition["condition_id"],
                            "profile": condition["profile"], "status": "blocked",
                            "reason": "confirmed budget reached"})
            continue
        if unknown_cost and condition["collector_potentially_paid"]:
            results.append({"condition_id": condition["condition_id"],
                            "profile": condition["profile"], "status": "blocked",
                            "reason": "paid cost unknown"})
            continue
        results.append(run_condition(plan, condition, env=env))
        if paid_selected:
            budget = budget_state(results, guard["confirmed_budget_usd"])
            if budget == "budget_reached":
                stop_all = True
            elif budget == "cost_unknown":
                unknown_cost = True

    statuses = {row["status"] for row in results}
    if "failed" in statuses:
        status = "failed"
    elif "blocked" in statuses:
        status = "blocked"
    elif "degraded" in statuses:
        status = "degraded"
    else:
        status = "completed"
    paid_costs = [row["receipts"].get("cost_usd") for row in results
                  if row.get("profile") in PAID_PROFILES
                  and isinstance(row.get("receipts"), dict)]
    known_cost = sum(paid_costs) if paid_costs and all(
        cost is not None for cost in paid_costs) else None
    planned_count = len(plan["condition_runs"])
    full_cohort = len(selected) == planned_count
    selected_ids = ([row["condition_id"] for row in selected]
                    if condition_ids is not None else None)
    report = {
        "schema": "mtagentlens.overhead-report/2", "executed": True,
        "plan_sha256": plan["plan_sha256"], "status": status,
        "scientific_admission": "not_claimed",
        "protocol": plan.get("protocol"),
        "planned_condition_runs": planned_count,
        "condition_runs": len(results),
        "selection": {
            "condition_ids": selected_ids,
            "partial": not full_cohort,
            "planned_condition_runs": planned_count,
            "selected_condition_runs": len(selected),
        },
        "cohort": {
            "planned_condition_runs": planned_count,
            "executed_condition_runs": len(results),
            "complete": bool(full_cohort and len(results) == planned_count
                             and status == "completed"),
            "label": ("frozen_matrix_complete" if full_cohort else
                      "partial_calibration_or_recovery"),
        },
        "budget": {"confirmed_budget_usd": guard["confirmed_budget_usd"],
                   "semantics": guard["budget_semantics"], "state": budget,
                   "known_cost_usd": known_cost},
        "results": results, "limits": plan["limits"],
    }
    (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "collector/build/release/mtagentlens")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workload-json", required=True,
                        help="explicit JSON argv list for the workload; no shell interpretation")
    parser.add_argument("--operations-per-instance", type=int, required=True)
    parser.add_argument("--agent-comm", required=True,
                        help="exact process comm of the isolated workload; no fixed default")
    parser.add_argument("--timeout-s", type=float, default=600)
    parser.add_argument("--sample-interval-ms", type=int, default=100,
                        help="real /proc and database sampling interval (>=10 ms)")
    parser.add_argument("--max-budget-usd", type=float,
                        help="confirmed budget, not a hard provider cost cap")
    parser.add_argument("--execute", action="store_true",
                        help="explicitly execute the frozen plan (default: plan only)")
    parser.add_argument("--plan-only", action="store_true",
                        help="explicitly emit the plan and never execute")
    parser.add_argument("--condition-id", action="append", dest="condition_ids",
                        help="execute one already-frozen condition_id for calibration; "
                             "repeatable, never edits the matrix")
    args = parser.parse_args(argv)
    if args.execute and args.plan_only:
        parser.error("--execute and --plan-only are mutually exclusive")
    try:
        workload = json.loads(args.workload_json)
        plan = build_plan(args.binary, args.out, workload, args.operations_per_instance,
                          args.timeout_s, agent_comm=args.agent_comm, env=os.environ,
                          execute=args.execute, sample_interval_ms=args.sample_interval_ms)
        selected_condition_runs(plan, args.condition_ids)
        if args.execute:
            report = execute_plan(plan, execute=True, env=os.environ,
                                  max_budget_usd=args.max_budget_usd,
                                  condition_ids=args.condition_ids)
            print(json.dumps(report, indent=2, sort_keys=True))
            return int(report["status"] != "completed")
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
