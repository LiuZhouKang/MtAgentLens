# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Overhead four-profile plan and subprocess lifecycle driver.

Profiles are fixed: C0 agent-alone, C1 trace --capture-only, C2 trace
--disable-responsibility-observer (runtime+DB, Observer off), C3 default trace
(Observer on, no ONNX). C1-C3 keep TLS and process capture enabled; only the
profile boundary flag differs. This module only builds plans and, with an
explicit --execute, runs controlled workloads. It never claims measurements.

The workload contract is JSONL receipts on stdout: one object per controlled
operation with a unique ``receipt_id`` and optional ``path``. Missing stdout
receipts measure workload completion only; they are NOT eBPF capture loss.
Capture loss stays null unless independent capture observations are supplied
and matched to receipt ids/paths. Timestamps are optional; missing stays null.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "mtagentlens.overhead-plan/1"
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
REPEATS = 3
PAID_PROFILES = ("C2", "C3")
PAID_ENV = "MTL_ALLOW_PAID"
BUDGET_FLAG = "--max-budget-usd"
BUDGET_SEMANTICS = "confirmed_only_not_hard_cap"
CONFIG_ENV_PREFIXES = ("MTAGENTLENS_", "MTL_", "OBSERVER_", "MODEL_", "OPENAI_",
                       "ANTHROPIC_", "AZURE_", "AWS_", "GOOGLE_", "CLAUDE_", "DEEPSEEK_")


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


def build_plan(binary, out, workload_argv, operations_per_instance, timeout_s=600, *,
               agent_comm, env=None, execute=False):
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
    out = Path(out).resolve()

    binary_sha = sha256_file(binary)
    workload_sha = sha256_bytes(canonical(workload).encode())
    executable_sha = sha256_file(workload_executable)
    config_env = environment_binding(env)
    config_files = config_file_binding()
    configuration = {
        "schema": SCHEMA, "concurrency": list(CONCURRENCY), "repeats": REPEATS,
        "profiles": list(PROFILES), "profile_flags": PROFILE_FLAGS,
        "operations_per_instance": operations_per_instance, "timeout_s": float(timeout_s),
        "agent_comm": agent_comm, "binary_sha256": binary_sha,
        "workload_sha256": workload_sha,
        "workload_executable_sha256": executable_sha,
        "config_env_sha256": config_env["sha256"],
        "config_file_sha256": config_files,
    }
    config_sha = sha256_bytes(canonical(configuration).encode())

    condition_runs = []
    blocks = []
    for concurrency in CONCURRENCY:
        for repeat in range(1, REPEATS + 1):
            block_id = f"c{concurrency}-r{repeat}"
            block = {"block_id": block_id, "concurrency": concurrency, "repeat": repeat,
                     "condition_order": list(PROFILES), "condition_ids": []}
            for profile in PROFILES:
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
        "schema": SCHEMA, "scope": "four_profile_overhead_execution_plan",
        "binary": str(binary), "binary_sha256": binary_sha,
        "workload_argv": list(workload), "workload_sha256": workload_sha,
        "workload_executable": str(workload_executable),
        "workload_executable_sha256": executable_sha,
        "agent_comm": agent_comm,
        "config_env_sha256": config_env["sha256"],
        "config_file_sha256": config_files,
        "config_env_keys": config_env["keys"],
        "config_sha256": config_sha, "out": str(out),
        "concurrency": list(CONCURRENCY), "repeats": REPEATS,
        "profiles": list(PROFILES), "profile_labels": PROFILE_LABELS,
        "operations_per_instance": operations_per_instance,
        "timeout_s": float(timeout_s),
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
            "stdout receipt misses measure workload completion only; capture loss stays null without independent matched observations",
            "controlled expected receipt count is the denominator; producer event counts are not substituted",
            "token and cost are unknown unless complete receipts provide them",
            "budget is confirmed, not a hard provider cost cap; execution stops on reached budget or unknown cost",
            "single host and kernel; no multihost or cross-kernel inference",
            "collector attachment is unproven; a 20 ms liveness probe is not attachment proof",
            "C3 requires an externally verified real Observer provider; a mock is not a valid C3 run",
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


def _capture_loss(condition, receipts, observations):
    # No independent capture adapter exists yet. Self-declared source strings
    # are not evidence, so capture loss always stays unmeasured/null.
    if condition["profile"] == "C0":
        return {"applicable": False, "measured": False, "missing_fraction": None,
                "status": "not_applicable", "reason": "C0 has no collector capture"}
    return {"applicable": True, "measured": False, "missing_fraction": None,
            "status": "unsupported_unverified",
            "reason": "no independent capture adapter; capture loss integration pending"}


def summarize_receipts(plan, condition, receipts, *, now_ns=None, capture_observations=None):
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

    return {
        "workload_expected_receipts": expected,
        "workload_observed_receipts": observed,
        "workload_missing_receipts": missing,
        "workload_missing_fraction": missing / expected,
        "workload_completion_basis": "controlled_operation_receipts",
        "controlled_operations_expected": expected,
        "capture_loss": _capture_loss(condition, [row for row, _ in normalized], capture_observations),
        "receipts_complete": complete,
        "latency_ms": {
            "event_to_claim": _latency_metric(event_to_claim),
            "claim_to_alert": _latency_metric(claim_to_alert),
        },
        "latency_provenance": {
            "event_to_claim": {"event_ns": "workload_stdout_receipt",
                               "claim_ns": "workload_stdout_receipt"},
            "claim_to_alert": {"claim_ns": "workload_stdout_receipt",
                               "alert_ns": "workload_stdout_receipt"},
        },
        "token_usage": token_usage,
        "cost_usd": cost,
    }


def _valid_budget(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def blocked_conditions(plan, *, env=None, max_budget_usd=None):
    """Paid profiles blocked in preregistered condition order, never reordered."""
    env = os.environ if env is None else env
    reasons = []
    if env.get(PAID_ENV) != "1":
        reasons.append(f"paid execution requires pre-existing {PAID_ENV}=1")
    if not _valid_budget(max_budget_usd):
        reasons.append(f"paid execution requires explicit {BUDGET_FLAG} > 0")
    if not reasons:
        return []
    reason = "; ".join(reasons)
    return [{"condition_id": row["condition_id"], "profile": row["profile"], "reason": reason}
            for row in plan["condition_runs"] if row["collector_potentially_paid"]]


def check_execution(plan, *, execute, env=None, max_budget_usd=None):
    """Guard before any process or output directory exists."""
    if not execute:
        return {"execute": False, "confirmed_budget_usd": None,
                "budget_semantics": BUDGET_SEMANTICS}
    blocked = blocked_conditions(plan, env=env, max_budget_usd=max_budget_usd)
    if blocked:
        raise ValueError(blocked[0]["reason"])
    return {"execute": True, "confirmed_budget_usd": float(max_budget_usd),
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


def load_receipts(paths):
    receipts = []
    for path in paths:
        path = Path(path)
        if not path.is_file():
            continue
        for line_number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                receipt = json.loads(line)
            except ValueError as error:
                raise ValueError(f"invalid receipt JSON in {path.name}:{line_number}") from error
            if not isinstance(receipt, dict):
                raise ValueError(f"receipt in {path.name}:{line_number} is not an object")
            receipts.append(receipt)
    return receipts


def _kill_group(process):
    # Do not fall back to process.kill(): the PID may be unrelated or reused.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


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
    processes = []
    handles = []
    errors = []
    started = time.monotonic()
    deadline = started + timeout
    try:
        if condition["collector"]:
            collector_stdout = (output / "collector.stdout").open("w")
            collector_stderr = (output / "collector.stderr").open("w")
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
                collector.wait(timeout=min(30, max(1, timeout)))
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
        for handle in handles:
            try:
                handle.close()
            except OSError:
                pass

    receipt_paths = [Path(item["stdout"]) for item in result["workloads"]]
    try:
        receipts = load_receipts(receipt_paths)
        result["receipts"] = summarize_receipts(plan, condition, receipts,
                                                now_ns=time.time_ns())
    except (OSError, ValueError) as error:
        errors.append(str(error))
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


def execute_plan(plan, *, execute=False, env=None, max_budget_usd=None):
    guard = check_execution(plan, execute=execute, env=env, max_budget_usd=max_budget_usd)
    if not execute:
        return {"executed": False, "plan_sha256": plan["plan_sha256"], "condition_runs": 0}
    verify_manifest(plan, env)
    output = Path(plan["out"])
    output.mkdir(parents=True, exist_ok=False)
    results = []
    budget = "within_confirmed_budget"
    stop_all = False
    unknown_cost = False
    for condition in plan["condition_runs"]:
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
    report = {
        "schema": "mtagentlens.overhead-report/1", "executed": True,
        "plan_sha256": plan["plan_sha256"], "status": status,
        "scientific_admission": "not_claimed",
        "budget": {"confirmed_budget_usd": guard["confirmed_budget_usd"],
                   "semantics": guard["budget_semantics"], "state": budget,
                   "known_cost_usd": known_cost},
        "condition_runs": len(results), "results": results, "limits": plan["limits"],
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
    parser.add_argument("--max-budget-usd", type=float,
                        help="confirmed budget, not a hard provider cost cap")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        workload = json.loads(args.workload_json)
        plan = build_plan(args.binary, args.out, workload, args.operations_per_instance,
                          args.timeout_s, agent_comm=args.agent_comm, env=os.environ,
                          execute=args.execute)
        if args.execute:
            report = execute_plan(plan, execute=True, env=os.environ,
                                  max_budget_usd=args.max_budget_usd)
            print(json.dumps(report, indent=2, sort_keys=True))
            return int(report["status"] != "completed")
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
