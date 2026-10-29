# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Aggregate executed overhead condition runs into the T8 metrics envelope.

The overhead driver writes one ``result.json`` per condition run
(15 blocks x 4 profiles). This adapter validates those artifacts against the
frozen plan and aggregates them by profile x concurrency for
``experiment_suite_audit``. It never turns a missing sample into zero. Capture
metrics come only from each run's ``controlled_capture`` record produced by the
``controlled_file_open/v1`` adapter; their source bytes are re-hashed here.

CPU/RSS/database values are same-host /proc and SQLite-file observations. The
reported cell is the maximum of per-run p95/max values, which is a conservative
description across repeats, not an inferential estimate. The optional block
bootstrap is descriptive and is emitted only for a complete cohort.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Sequence

from scripts.evaluation import controlled_capture as capture_adapter
from scripts.evaluation import overhead_driver as capture_driver

SCHEMA = "mtagentlens.overhead-metrics/2"
PLAN_SCHEMA = "mtagentlens.overhead-plan/2"
METRICS_SCHEMA = "mtagentlens.experiment-metrics/1"
PROTOCOL = "EXPERIMENT-SUITE-7-REVISED-2026-10-26 §6"
PROFILES = ("C0", "C1", "C2", "C3")
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
BLOCKS = 15
CONDITIONS = 36
WORKLOAD_INSTANCES = 372
ORDER_SEED = capture_driver.ORDER_SEED
REAL_OBSERVER_PROVIDERS = ("llm_env", "llm_config")
BOOTSTRAP_RESAMPLES = 10000
CONFIDENCE_LEVEL = 0.95
BOOTSTRAP_SEED = 20261027


def _is_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _number(value: Any) -> float | int | None:
    return value if _is_number(value) else None


def _pick(mapping: Any, *keys: str) -> Any:
    value = mapping
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def validate_plan(plan: Any) -> dict:
    if not isinstance(plan, dict):
        raise ValueError("overhead plan must be a JSON object")
    if plan.get("schema") != PLAN_SCHEMA:
        raise ValueError(
            f"overhead plan schema must be {PLAN_SCHEMA}; old 60-condition "
            "plans are not the current protocol")
    supplied_sha = plan.get("plan_sha256")
    if not isinstance(supplied_sha, str) or len(supplied_sha) != 64:
        raise ValueError("overhead plan must carry its supplied plan_sha256")
    manifest = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if _canonical_sha(manifest) != supplied_sha:
        raise ValueError(
            "overhead plan_sha256 does not match the supplied plan content; "
            "refusing a recomputed or edited plan")
    conditions = plan.get("condition_runs")
    if not isinstance(conditions, list) or len(conditions) != CONDITIONS:
        raise ValueError(
            f"overhead plan must contain exactly {CONDITIONS} condition_runs")
    profiles = tuple(plan.get("profiles") or ())
    if profiles != PROFILES:
        raise ValueError(f"overhead plan profiles must be {list(PROFILES)}")
    if tuple(plan.get("concurrency") or ()) != CONCURRENCY:
        raise ValueError(f"overhead plan concurrency must be {list(CONCURRENCY)}")
    if plan.get("repeats") != REPEATS:
        raise ValueError(f"overhead plan repeats must be {REPEATS}")
    matrix = plan.get("matrix")
    if matrix is not None:
        expected_matrix = {str(level): list(names)
                           for level, names in CONCURRENCY_PROFILES.items()}
        if matrix.get("concurrency_profiles") != expected_matrix:
            raise ValueError("overhead plan matrix is not the frozen 36-run protocol")
    blocks_value = plan.get("blocks")
    if not isinstance(blocks_value, list) or len(blocks_value) != BLOCKS:
        raise ValueError(f"overhead plan must contain exactly {BLOCKS} blocks")
    block_meta: dict[str, dict] = {}
    for block in blocks_value:
        if not isinstance(block, dict):
            raise ValueError("overhead block is not an object")
        block_id = block.get("block_id")
        if not isinstance(block_id, str) or not block_id:
            raise ValueError("overhead block_id is missing")
        if block_id in block_meta:
            raise ValueError(f"duplicate overhead block_id {block_id!r}")
        concurrency = block.get("concurrency")
        if type(concurrency) is not int or concurrency not in CONCURRENCY:
            raise ValueError(
                f"block {block_id!r} concurrency must be one of {list(CONCURRENCY)}")
        repeat = block.get("repeat")
        if type(repeat) is not int or repeat not in range(1, REPEATS + 1):
            raise ValueError(f"block {block_id!r} repeat must be 1..{REPEATS}")
        expected_profiles = list(CONCURRENCY_PROFILES[concurrency])
        declared = block.get("profiles")
        if declared is not None and sorted(declared) != sorted(expected_profiles):
            raise ValueError(
                f"block {block_id!r} profiles do not match the {concurrency}-way matrix")
        order = block.get("condition_order")
        if order is not None:
            if (not isinstance(order, list)
                    or sorted(order) != sorted(expected_profiles)):
                raise ValueError(
                    f"block {block_id!r} condition_order is not the planned profiles")
            expected_ids = [f"{block_id}-{profile}" for profile in order]
            declared_ids = block.get("condition_ids")
            if declared_ids is not None and declared_ids != expected_ids:
                raise ValueError(
                    f"block {block_id!r} condition_ids differ from condition_order")
        block_meta[block_id] = block

    ids: set[str] = set()
    seen_tuples: set[tuple[int, int, str]] = set()
    profiles_by_block: dict[str, list[str]] = {}
    operations = plan.get("operations_per_instance")
    if operations is not None and (
            type(operations) is not int or operations < 1):
        raise ValueError("overhead operations_per_instance must be a positive integer")
    for condition in conditions:
        if not isinstance(condition, dict):
            raise ValueError("overhead condition run is not an object")
        condition_id = condition.get("condition_id")
        if not isinstance(condition_id, str) or not condition_id:
            raise ValueError("overhead condition_id is missing")
        if condition_id in ids:
            raise ValueError(f"duplicate overhead condition_id {condition_id!r}")
        ids.add(condition_id)
        profile = condition.get("profile")
        if profile not in PROFILES:
            raise ValueError(f"{condition_id}: unknown profile {profile!r}")
        concurrency = condition.get("concurrency")
        if type(concurrency) is not int or concurrency not in CONCURRENCY:
            raise ValueError(
                f"{condition_id}: concurrency must be one of {list(CONCURRENCY)}")
        expected_profiles = CONCURRENCY_PROFILES[concurrency]
        if profile not in expected_profiles:
            raise ValueError(
                f"{condition_id}: profile {profile!r} is not planned at "
                f"concurrency {concurrency}")
        repeat = condition.get("repeat")
        if type(repeat) is not int or repeat not in range(1, REPEATS + 1):
            raise ValueError(f"{condition_id}: repeat must be 1..{REPEATS}")
        block_id = condition.get("block_id")
        if not isinstance(block_id, str) or not block_id:
            raise ValueError(f"{condition_id}: block_id is missing")
        if block_id not in block_meta:
            raise ValueError(f"{condition_id}: block_id {block_id!r} is not planned")
        meta = block_meta[block_id]
        if meta["concurrency"] != concurrency:
            raise ValueError(f"{condition_id}: concurrency differs from its block")
        if meta["repeat"] != repeat:
            raise ValueError(f"{condition_id}: repeat differs from its block")
        expected = condition.get("expected_receipts")
        if type(expected) is not int or expected < 1:
            raise ValueError(f"{condition_id}: expected_receipts must be positive")
        if "workload_instances" in condition:
            instances = condition["workload_instances"]
            if type(instances) is not int or instances != concurrency:
                raise ValueError(
                    f"{condition_id}: workload_instances must equal concurrency")
        if operations is not None and expected != concurrency * operations:
            raise ValueError(
                f"{condition_id}: expected_receipts differs from concurrency x operations")
        key = (concurrency, repeat, profile)
        if key in seen_tuples:
            raise ValueError(f"duplicate overhead condition tuple {key!r}")
        seen_tuples.add(key)
        profiles_by_block.setdefault(block_id, []).append(profile)
    expected_tuples = {
        (concurrency, repeat, profile)
        for concurrency in CONCURRENCY
        for repeat in range(1, REPEATS + 1)
        for profile in CONCURRENCY_PROFILES[concurrency]
    }
    if seen_tuples != expected_tuples:
        raise ValueError("overhead condition tuples are not the exact 36-run matrix")
    for block_id, meta in block_meta.items():
        expected_profiles = list(CONCURRENCY_PROFILES[meta["concurrency"]])
        if sorted(profiles_by_block.get(block_id, [])) != sorted(expected_profiles):
            raise ValueError(
                f"block {block_id!r} does not contain each planned profile exactly once")
    block_levels = [meta["concurrency"] for meta in block_meta.values()]
    for concurrency in CONCURRENCY:
        if block_levels.count(concurrency) != REPEATS:
            raise ValueError(
                f"overhead plan must contain {REPEATS} blocks per concurrency")

    workload_instances = sum(
        condition.get("workload_instances", condition["concurrency"])
        for condition in conditions)
    if workload_instances != WORKLOAD_INSTANCES:
        raise ValueError(
            f"overhead workload instances must be {WORKLOAD_INSTANCES}")
    if "block_count" in plan and plan["block_count"] != BLOCKS:
        raise ValueError(f"overhead plan block_count must be {BLOCKS}")
    if "condition_run_count" in plan and plan["condition_run_count"] != CONDITIONS:
        raise ValueError(f"overhead plan condition_run_count must be {CONDITIONS}")
    for key in ("workload_instances", "workload_instance_count"):
        if key in plan and plan[key] != workload_instances:
            raise ValueError(f"overhead plan {key} is inconsistent with condition_runs")
    if "expected_receipts" in plan:
        expected_total = sum(condition["expected_receipts"] for condition in conditions)
        if plan["expected_receipts"] != expected_total:
            raise ValueError("overhead plan expected_receipts is inconsistent")
    return {
        "schema": PLAN_SCHEMA,
        "protocol": plan.get("protocol", PROTOCOL),
        "profiles": list(PROFILES),
        "conditions": conditions,
        "blocks": sorted(block_meta),
        "block_meta": block_meta,
        "plan_sha256": supplied_sha,
    }


def _load_result(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"result is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("result is not a JSON object")
    return value


def _db_final(database: dict, key: str):
    value = _pick(database, key, "final")
    if value is not None:
        return _number(value)
    return _number(_pick(database, "final", key))


def _file_final(value):
    if isinstance(value, dict):
        return _number(value.get("final"))
    return _number(value)


def _run_values(result: dict, condition: dict) -> dict:
    """Extract one condition run's raw cell values; null means unmeasured."""
    if result.get("condition_id") != condition["condition_id"]:
        raise ValueError("result condition_id differs from plan")
    if result.get("profile") != condition["profile"]:
        raise ValueError(
            f"result profile {result.get('profile')!r} differs from plan "
            f"{condition['profile']!r}")
    if result.get("concurrency") != condition["concurrency"]:
        raise ValueError("result concurrency differs from plan")
    if result.get("repeat") != condition["repeat"]:
        raise ValueError("result repeat differs from plan")
    if result.get("block_id") != condition["block_id"]:
        raise ValueError("result block_id differs from plan")
    if "workload_instances" in condition:
        instances = result.get("workload_instances")
        if instances is not None and instances != condition["workload_instances"]:
            raise ValueError("result workload_instances differs from plan")
    run_status = result.get("status")
    if run_status not in ("completed", "degraded"):
        raise ValueError(f"condition run status is {run_status!r}")
    resources = result.get("resources")
    if not isinstance(resources, dict) or resources.get("available") is not True:
        raise ValueError("resource sampling is unavailable")
    collector = resources.get("collector") if isinstance(resources.get("collector"), dict) else {}
    workloads = resources.get("workloads") if isinstance(resources.get("workloads"), dict) else {}
    database = resources.get("database_bytes") if isinstance(resources.get("database_bytes"), dict) else {}
    receipts = result.get("receipts") if isinstance(result.get("receipts"), dict) else {}
    if receipts.get("workload_expected_receipts") != condition["expected_receipts"]:
        raise ValueError("result expected receipts differ from plan")
    expected = receipts.get("workload_expected_receipts")
    observed = receipts.get("workload_observed_receipts")
    if type(expected) is not int or expected < 1:
        raise ValueError("result expected receipts are not a positive integer")
    if type(observed) is not int or not 0 <= observed <= expected:
        raise ValueError("result observed receipts are not within the planned count")
    if run_status == "completed" and observed != expected:
        raise ValueError("completed run has incomplete workload receipts")
    completion_rate = observed / expected
    latency = receipts.get("latency_ms") if isinstance(receipts.get("latency_ms"), dict) else {}
    tokens = receipts.get("token_usage") if isinstance(receipts.get("token_usage"), dict) else {}

    profile = condition["profile"]
    workload_cpu = _number(_pick(workloads, "cpu_percent", "p95"))
    workload_rss = _number(_pick(workloads, "rss_kib", "max"))
    workload_cpu_seconds = _number(_pick(workloads, "cpu_seconds", "max"))
    trace_bytes = _file_final(resources.get("trace_bytes"))
    if profile == "C0":
        if result.get("collector", {}).get("started"):
            raise ValueError("C0 must not start a collector")
        # C0 is the agent-alone baseline: its own process tree is the only
        # resource series. It is still a real measured cell, not a zero.
        cpu = workload_cpu
        rss = workload_rss
        db_peak = None
        db_main = None
        db_wal = None
        db_total = None
        collector_cpu_seconds = 0.0
        attachment = None
    else:
        if result.get("collector", {}).get("started") is not True:
            raise ValueError("collector did not start for a collector profile")
        if result.get("collector", {}).get("attachment_proven") is not True:
            raise ValueError("collector attachment is not proven")
        if profile == "C3":
            provider = result.get("collector", {}).get("observer_provider")
            if provider not in REAL_OBSERVER_PROVIDERS:
                raise ValueError(
                    f"C3 Observer provider {provider!r} is not a known real "
                    "configuration marker (llm_env/llm_config)")
        cpu = _number(_pick(collector, "cpu_percent", "p95"))
        rss = _number(_pick(collector, "rss_kib", "max"))
        collector_cpu_seconds = _number(_pick(collector, "cpu_seconds", "max"))
        db_peak = _number(database.get("peak"))
        db_main = _db_final(database, "main")
        db_wal = _db_final(database, "wal")
        db_total = _db_final(database, "total")
        attachment = True

    total_cpu_seconds = (
        collector_cpu_seconds + workload_cpu_seconds
        if _is_number(collector_cpu_seconds) and _is_number(workload_cpu_seconds)
        else None)
    receipt_event_to_claim = _number(_pick(latency, "event_to_claim", "p95"))
    receipt_claim_to_alert = _number(_pick(latency, "claim_to_alert", "p95"))
    tokens_known = tokens.get("known") is True
    receipt_tokens = _number(tokens.get("total_tokens")) if tokens_known else None
    receipt_cost = _number(receipts.get("cost_usd"))
    receipt_claims = {
        "source": "workload_stdout_receipts",
        "trust": "not_a_runtime_measurement",
        "event_to_claim_p95": receipt_event_to_claim,
        "claim_to_alert_p95": receipt_claim_to_alert,
        "observer_tokens": receipt_tokens,
        "cost_usd": receipt_cost,
        "limitations": (
            "workload self-report; not runtime Observer usage and not verified "
            "alert latency; missing stays null"),
    }

    return {
        "profile": profile,
        "concurrency": condition["concurrency"],
        "block_id": condition["block_id"],
        "repeat": condition.get("repeat"),
        "run_status": run_status,
        "duration_s": _number(result.get("duration_s")),
        "work_completion_rate": completion_rate,
        "work_completion_observed": observed,
        "work_completion_expected": expected,
        "cpu_percent_p95": cpu,
        "workload_cpu_percent_p95": workload_cpu,
        "rss_kib_max": rss,
        "db_bytes_peak": db_peak,
        "db_main_bytes": db_main,
        "db_wal_bytes": db_wal,
        "db_total_bytes": db_total,
        "trace_bytes": trace_bytes,
        "collector_cpu_seconds": collector_cpu_seconds,
        "workload_cpu_seconds": workload_cpu_seconds,
        "total_cpu_seconds": total_cpu_seconds,
        # Runtime usage and verified alert association are not wired here.
        "event_to_claim_p95": None,
        "claim_to_alert_p95": None,
        "observer_tokens": None,
        "cost_usd": None,
        "receipt_claims": receipt_claims,
        "attachment_proven": attachment,
        "run_sha256": _canonical_sha(result),
    }


_CELL_METRICS = (
    "cpu_percent_p95", "workload_cpu_percent_p95", "rss_kib_max",
    "db_bytes_peak", "db_main_bytes", "db_wal_bytes", "db_total_bytes",
    "trace_bytes", "duration_s", "work_completion_rate",
    "collector_cpu_seconds", "workload_cpu_seconds", "total_cpu_seconds",
)


def _series(values: Sequence[float | int | None]) -> dict:
    present = [value for value in values if _is_number(value)]
    if not present:
        return {"values": list(values), "n": 0, "missing": len(values),
                "mean": None, "min": None, "max": None, "range": None}
    return {
        "values": list(values),
        "n": len(present),
        "missing": len(values) - len(present),
        "mean": sum(present) / len(present),
        "min": min(present),
        "max": max(present),
        "range": max(present) - min(present),
    }


def _apply_series(cell: dict, key: str, values: Sequence[float | int | None]) -> None:
    stats = _series(values)
    cell[f"{key}_values"] = stats["values"]
    cell[f"{key}_mean"] = stats["mean"]
    cell[f"{key}_min"] = stats["min"]
    cell[f"{key}_max"] = stats["max"]
    cell[f"{key}_range"] = stats["range"]
    cell[f"{key}_n"] = stats["n"]
    cell[key] = stats["mean"]


def _aggregate_cell(profile: str, concurrency: int, runs: list[dict]) -> dict:
    basis = "workload_tree" if profile == "C0" else "collector_tree"
    cell = {
        "concurrency": concurrency,
        "runs": len(runs),
        "expected_runs": REPEATS,
        "status": ("complete" if len(runs) == REPEATS
                   else "missing" if not runs else "incomplete"),
        "cpu_basis": basis,
        "rss_basis": basis,
    }
    for key in _CELL_METRICS:
        _apply_series(cell, key, [run.get(key) for run in runs])
    cell["run_order"] = [
        {"block_id": run["block_id"], "repeat": run["repeat"]} for run in runs]
    cell["raw_values"] = {key: cell[f"{key}_values"] for key in _CELL_METRICS}
    cell["summary"] = {
        key: {field: cell[f"{key}_{field}"]
              for field in ("n", "mean", "min", "max", "range")}
        for key in _CELL_METRICS}
    cell["cpu_seconds"] = cell["total_cpu_seconds"]
    cell["cpu_seconds_values"] = cell["total_cpu_seconds_values"]
    cell["cpu_seconds_mean"] = cell["total_cpu_seconds_mean"]
    cell["cpu_seconds_min"] = cell["total_cpu_seconds_min"]
    cell["cpu_seconds_max"] = cell["total_cpu_seconds_max"]
    cell["cpu_seconds_range"] = cell["total_cpu_seconds_range"]
    receipt_values = {
        "event_to_claim_p95": [_pick(run.get("receipt_claims") or {},
                                     "event_to_claim_p95") for run in runs],
        "claim_to_alert_p95": [_pick(run.get("receipt_claims") or {},
                                     "claim_to_alert_p95") for run in runs],
        "observer_tokens": [_pick(run.get("receipt_claims") or {},
                                  "observer_tokens") for run in runs],
        "cost_usd": [_pick(run.get("receipt_claims") or {}, "cost_usd")
                     for run in runs],
    }
    claims = {
        "source": "workload_stdout_receipts",
        "trust": "not_a_runtime_measurement",
        "limitations": (
            "workload self-report; runtime Observer usage and verified alert "
            "association are not wired, so these are not real measurements"),
    }
    for key, values in receipt_values.items():
        stats = _series(values)
        claims[f"{key}_values"] = stats["values"]
        claims[f"{key}_mean"] = stats["mean"]
        claims[f"{key}_min"] = stats["min"]
        claims[f"{key}_max"] = stats["max"]
        claims[f"{key}_range"] = stats["range"]
        claims[key] = stats["mean"]
    cell["receipt_claims"] = claims
    # Unknown runtime evidence remains null; it is never replaced by receipts.
    cell["event_to_claim_p95"] = None
    cell["claim_to_alert_p95"] = None
    cell["observer_tokens"] = None
    cell["cost_usd"] = None
    cell["runtime_usage"] = {
        "status": "pending_runtime_usage_wiring",
        "observer_tokens": None,
        "cost_usd": None,
    }
    cell["alert_latency"] = {
        "status": "pending_verified_alert_association",
        "event_to_claim_p95": None,
        "claim_to_alert_p95": None,
    }
    cell["aggregation"] = (
        "mean of per-run raw values; min/max/range are reported alongside and "
        "are not treated as the typical cost")
    return cell


def _total_cpu(run: dict) -> float | None:
    if run["profile"] == "C0":
        value = run.get("cpu_percent_p95")
        return float(value) if _is_number(value) else None
    collector = run.get("cpu_percent_p95")
    workload = run.get("workload_cpu_percent_p95")
    if not _is_number(collector) or not _is_number(workload):
        return None
    return float(collector) + float(workload)


def _bootstrap_statistics(
    complete_runs: list[dict], *, expected_blocks: int, seed: int = BOOTSTRAP_SEED
) -> dict | None:
    """Descriptive block bootstrap; blocks need only their planned arms.

    C3-C0 is paired in all 15 blocks. C1/C2 exist only in the three concurrency-1
    blocks, so their component contrasts use those three paired blocks.
    """
    if expected_blocks != BLOCKS:
        return None
    blocks: dict[str, dict[str, float | None]] = {}
    block_keys: dict[str, tuple[int, int]] = {}
    for run in complete_runs:
        value = _total_cpu(run)
        key = (run["concurrency"], run["repeat"])
        if block_keys.setdefault(run["block_id"], key) != key:
            return None
        block = blocks.setdefault(run["block_id"], {})
        if run["profile"] in block:
            return None
        block[run["profile"]] = value
    if len(blocks) != expected_blocks:
        return None
    for block_id, values in blocks.items():
        concurrency = block_keys[block_id][0]
        if set(values) != set(CONCURRENCY_PROFILES[concurrency]):
            return None
    if sum(len(values) for values in blocks.values()) != CONDITIONS:
        return None

    rng = random.Random(seed)

    def interval(values):
        if not values or any(not _is_number(value) for _, value in values):
            return None
        strata: dict[int, list[float]] = {}
        for block_id, value in values:
            strata.setdefault(block_keys[block_id][0], []).append(value)
        if any(len(stratum) != REPEATS for stratum in strata.values()):
            return None
        means = []
        ordered = [strata[concurrency] for concurrency in CONCURRENCY
                   if concurrency in strata]
        for _ in range(BOOTSTRAP_RESAMPLES):
            # Concurrency levels are fixed design strata, not random
            # observations. Resample paired blocks only within each level.
            sample = [rng.choice(stratum) for stratum in ordered
                      for _ in range(len(stratum))]
            means.append(sum(sample) / len(sample))
        return [_percentile(means, 0.025), _percentile(means, 0.975)]

    intervals = {}
    contrast_blocks = {}
    unavailable = []
    for name in PROFILES:
        values = [(block_id, block[name]) for block_id, block in blocks.items()
                  if name in block]
        result = interval(values) if values else None
        if result is not None:
            intervals[f"{name}_total_cpu_percent"] = result
        else:
            unavailable.append(f"{name}_total_cpu_percent")
    for left, right, name in (
            ("C3", "C0", "C3_minus_C0_total_cpu_percent"),
            ("C1", "C0", "C1_minus_C0_total_cpu_percent"),
            ("C2", "C1", "C2_minus_C1_total_cpu_percent"),
            ("C3", "C2", "C3_minus_C2_total_cpu_percent")):
        values = [
            (block_id,
             block[left] - block[right]
             if _is_number(block[left]) and _is_number(block[right]) else None)
            for block_id, block in blocks.items()
            if left in block and right in block]
        result = interval(values) if values else None
        if result is not None:
            intervals[name] = result
            contrast_blocks[name] = len(values)
        else:
            unavailable.append(name)
    if not intervals:
        return None
    return {
        "aggregation_unit": "block",
        "stratified_by": "concurrency",
        "paired_profiles": True,
        "n_blocks": len(blocks),
        "n_blocks_by_profile": {
            name: sum(1 for block in blocks.values() if name in block)
            for name in PROFILES},
        "contrast_blocks": contrast_blocks,
        "blocks_with_four_profiles": sum(
            1 for block in blocks.values() if len(block) == 4),
        "blocks_with_two_profiles": sum(
            1 for block in blocks.values() if len(block) == 2),
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "confidence_level": CONFIDENCE_LEVEL,
        "confidence_intervals": intervals,
        "unavailable_contrasts": unavailable,
        "coverage": 1.0,
        "abstention": 0.0,
        "estimator": (
            "concurrency-stratified percentile bootstrap over paired repeat "
            "blocks; block values are raw per-run CPU p95, collector+workload "
            "for C1-C3 and workload-only for C0"),
        "primary_endpoint": (
            "none declared; the C3-C0 total-CPU interval is descriptive, not a "
            "fixed inferential endpoint"),
        "seed": seed,
    }


def _verify_capture_artifact(path_value, expected_sha, label):
    if not isinstance(path_value, str) or not path_value:
        raise ValueError(f"{label} path is missing")
    path = Path(path_value)
    if not path.is_file():
        raise ValueError(f"{label} changed or is missing: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha:
        raise ValueError(f"{label} sha256 changed: {path}")
    return digest


def _artifact_capture_health(profile: str, trace_path, database_path):
    """Re-derive attachment/flush proof from the artifacts the driver read.

    The health booleans recorded inside result.json are writer-supplied. C1
    capture-only proves attachment from the terminal CAPTURE_HEALTH event in
    its trace; C2/C3 prove attachment and final flush from the latest
    persisted checkpoint. The metrics adapter must not accept a measured
    fraction when the artifact itself disproves the claim.
    """
    evidence = capture_driver.inspect_collector_evidence(
        Path("collector.stdout"), Path("collector.stderr"),
        Path(database_path) if profile in ("C2", "C3") else None,
        Path(trace_path) if profile == "C1" else None,
        required_source_kinds=capture_driver.REQUIRED_SOURCE_KINDS)
    return (evidence["attachment"]["proven"],
            evidence["final_flush"]["observed"])


def _verify_measured_capture_health(profile: str, capture: dict,
                                    trace_path, database_path) -> None:
    proven, flush = _artifact_capture_health(profile, trace_path, database_path)
    if proven is not True:
        raise ValueError(
            "controlled_capture has no artifact-proven attachment for a "
            f"measured {profile} run")
    health = capture.get("health")
    if not isinstance(health, dict) or health.get("attachment_proven") is not True:
        raise ValueError("controlled_capture health claims an unproven attachment")
    if health.get("final_flush_observed") is not flush:
        raise ValueError(
            "controlled_capture final-flush claim differs from the artifact")


def _verify_controlled_capture(result: dict, condition: dict) -> dict:
    """Validate one run's structured adapter record and re-hash its sources."""
    capture = result.get("controlled_capture")
    if not isinstance(capture, dict):
        raise ValueError("controlled_capture adapter record is missing")
    if capture.get("schema") != "mtagentlens.controlled-capture/v1":
        raise ValueError("controlled_capture schema is unsupported")
    if capture.get("adapter_version") != "controlled_file_open/v1":
        raise ValueError("controlled_capture adapter version is unsupported")
    if capture.get("condition_id") != condition["condition_id"]:
        raise ValueError("controlled_capture condition_id differs from plan")
    if capture.get("profile") != condition["profile"]:
        raise ValueError("controlled_capture profile differs from plan")
    profile = condition["profile"]
    if profile == "C0":
        if capture.get("status") != "not_applicable":
            raise ValueError("C0 controlled_capture must be not_applicable")
        return {"status": "not_applicable", "estimable": False,
                "raw_transport_missing_fraction": None,
                "persisted_operation_missing_fraction": None,
                "operation_denominator": None, "evidence": None,
                "reasons": list(capture.get("reasons") or [])}
    binding = capture.get("artifact_binding")
    receipt_files = binding.get("receipt_files") if isinstance(binding, dict) else None
    if not isinstance(receipt_files, list) or not receipt_files:
        raise ValueError("controlled_capture receipt artifacts are missing")
    receipt_paths = [entry.get("path") for entry in receipt_files
                     if isinstance(entry, dict)]
    if any(not isinstance(path, str) or not path for path in receipt_paths):
        raise ValueError("controlled_capture receipt artifact path is missing")
    trace_file = binding.get("trace_file")
    database_file = binding.get("database")
    receipts, receipt_reasons = capture_adapter.load_receipts(receipt_paths)
    if receipt_reasons:
        raise ValueError("controlled_capture receipts cannot be read: "
                         + "; ".join(receipt_reasons))
    rebuilt = capture_adapter.build_controlled_capture(
        profile=profile, condition_id=condition["condition_id"],
        receipts=receipts, receipt_paths=receipt_paths,
        expected_receipts=condition["expected_receipts"],
        trace_path=(trace_file.get("path")
                    if isinstance(trace_file, dict) else None),
        database_path=(database_file.get("path")
                       if isinstance(database_file, dict) else None),
        health=capture.get("health"),
        ringbuf_diagnostics=capture.get("ringbuf_diagnostics"),
    )
    if (json.dumps(rebuilt, sort_keys=True, separators=(",", ":"))
            != json.dumps(capture, sort_keys=True, separators=(",", ":"))):
        raise ValueError(
            "controlled_capture differs from recomputation on the actual receipts, trace and DB")
    capture = rebuilt
    if capture.get("status") != "measured":
        return {"status": "not_estimable", "estimable": False,
                "raw_transport_missing_fraction": None,
                "persisted_operation_missing_fraction": None,
                "operation_denominator": None, "evidence": None,
                "reasons": list(capture.get("reasons") or ["adapter_not_measured"])}
    denominator = capture.get("denominator")
    if not isinstance(denominator, dict):
        raise ValueError("controlled_capture denominator is missing")
    if denominator.get("expected") != condition["expected_receipts"]:
        raise ValueError("controlled_capture denominator differs from plan")
    if denominator.get("observed") != condition["expected_receipts"]:
        raise ValueError("controlled_capture observed receipts are short")
    if denominator.get("complete") is not True:
        raise ValueError("controlled_capture denominator is incomplete")
    expected = denominator["expected"]
    combined = hashlib.sha256()
    for entry in receipt_files:
        if not isinstance(entry, dict):
            raise ValueError("controlled_capture receipt artifact is malformed")
        expected_sha = entry.get("sha256")
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            raise ValueError("controlled_capture receipt sha256 is missing")
        _verify_capture_artifact(entry.get("path"), expected_sha, "receipt")
        combined.update(Path(entry["path"]).read_bytes())
    if binding.get("receipt_combined_sha256") != combined.hexdigest():
        raise ValueError("controlled_capture combined receipt sha256 changed")
    trace = binding.get("trace_file")
    if not isinstance(trace, dict) or not isinstance(trace.get("sha256"), str):
        raise ValueError("controlled_capture trace artifact is missing")
    _verify_capture_artifact(trace.get("path"), trace["sha256"], "trace")
    database = binding.get("database")
    if profile in ("C2", "C3"):
        if not isinstance(database, dict) or not isinstance(database.get("sha256"), str):
            raise ValueError("controlled_capture database artifact is missing")
        _verify_capture_artifact(database.get("path"), database["sha256"], "database")
    raw = capture.get("raw_transport_missing_fraction")
    if not _is_number(raw) or not 0 <= raw <= 1:
        raise ValueError("controlled_capture raw fraction is not a rate")
    persisted = capture.get("persisted_operation_missing_fraction")
    if profile == "C1":
        if persisted is not None:
            raise ValueError("C1 must not report persisted-operation capture")
    elif not _is_number(persisted) or not 0 <= persisted <= 1:
        raise ValueError("controlled_capture persisted fraction is not a rate")
    raw_count = capture.get("raw_transport_missing_count")
    if raw_count != round(raw * expected):
        raise ValueError("controlled_capture raw count differs from its fraction")
    if profile != "C1":
        persisted_count = capture.get("persisted_operation_missing_count")
        if persisted_count != round(persisted * expected):
            raise ValueError("controlled_capture persisted count differs from its fraction")
    _verify_measured_capture_health(
        profile, capture, trace.get("path"),
        database.get("path") if isinstance(database, dict) else None)
    return {
        "status": "measured", "estimable": True,
        "raw_transport_missing_fraction": raw,
        "persisted_operation_missing_fraction": persisted,
        "operation_denominator": expected,
        "raw_transport_missing_count": raw_count,
        "persisted_operation_missing_count": capture.get(
            "persisted_operation_missing_count") if profile != "C1" else None,
        "empirical_validation": capture.get("empirical_validation"),
        "scope": capture.get("scope"),
        "evidence": {
            "condition_id": condition["condition_id"],
            "adapter_version": capture.get("adapter_version"),
            "receipt_combined_sha256": combined.hexdigest(),
            "trace_sha256": trace["sha256"],
            "database_sha256": (database.get("sha256")
                                if isinstance(database, dict) else None),
        },
        "reasons": [],
    }


def _aggregate_capture(profile: str, runs: list[dict]) -> dict:
    if profile == "C0":
        return {"status": "not_applicable", "estimable": False,
                "operation_denominator": None,
                "raw_transport_missing_fraction": None,
                "persisted_operation_missing_fraction": None,
                "raw_transport_missing_count": None,
                "persisted_operation_missing_count": None,
                "coverage": {"raw_transport": None, "persisted_operation": None},
                "evidence": [], "reasons": ["C0 has no collector capture"],
                "empirical_validation": "pending_real_recording",
                "scope": "controlled_file_open_operations_only"}
    captures = [run["controlled_capture"] for run in runs]
    if not captures or any(capture["status"] != "measured" for capture in captures):
        return {"status": "not_estimable", "estimable": False,
                "operation_denominator": None,
                "raw_transport_missing_fraction": None,
                "persisted_operation_missing_fraction": None,
                "raw_transport_missing_count": None,
                "persisted_operation_missing_count": None,
                "coverage": {"raw_transport": None, "persisted_operation": None},
                "evidence": [capture["evidence"] for capture in captures
                             if capture.get("evidence")],
                "reasons": sorted({reason for capture in captures
                                   for reason in capture.get("reasons", [])}),
                "empirical_validation": "pending_real_recording",
                "scope": "controlled_file_open_operations_only"}
    denominator = sum(capture["operation_denominator"] for capture in captures)
    raw_missing = sum(capture["raw_transport_missing_count"] for capture in captures)
    persisted_missing = (
        sum(capture["persisted_operation_missing_count"] for capture in captures)
        if profile != "C1" else None)
    return {
        "status": "measured", "estimable": True,
        "operation_denominator": denominator,
        "raw_transport_missing_fraction": raw_missing / denominator,
        "persisted_operation_missing_fraction": (
            persisted_missing / denominator if profile != "C1" else None),
        "raw_transport_missing_count": raw_missing,
        "persisted_operation_missing_count": persisted_missing,
        "coverage": {
            "raw_transport": 1 - raw_missing / denominator,
            "persisted_operation": (1 - persisted_missing / denominator
                                    if profile != "C1" else None),
        },
        "evidence": [capture["evidence"] for capture in captures],
        "reasons": [],
        "empirical_validation": "pending_real_recording",
        "scope": "controlled_file_open_operations_only",
    }


def build_metrics(
    plan_value: Any,
    results_root: str | Path | None = None,
    *,
    release_id: str,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    plan = validate_plan(plan_value)
    if not isinstance(release_id, str) or not release_id:
        raise ValueError("release_id is required")

    root = Path(results_root) if results_root is not None else Path(plan_value.get("out", ""))
    expected_by_cell: dict[tuple[str, int], int] = {}
    for condition in plan["conditions"]:
        key = (condition["profile"], condition["concurrency"])
        expected_by_cell[key] = expected_by_cell.get(key, 0) + 1

    runs_by_cell: dict[tuple[str, int], list[dict]] = {}
    failures: list[dict] = []
    degraded: list[dict] = []
    missing: list[str] = []
    provider_by_profile: dict[str, set[str]] = {name: set() for name in PROFILES}

    for condition in plan["conditions"]:
        condition_id = condition["condition_id"]
        path = root / condition_id / "result.json"
        if not path.is_file():
            missing.append(condition_id)
            continue
        try:
            result = _load_result(path)
            run = _run_values(result, condition)
            run["controlled_capture"] = _verify_controlled_capture(result, condition)
        except ValueError as exc:
            failures.append({"condition_id": condition_id, "reason": str(exc)})
            continue
        if run["run_status"] != "completed":
            degraded.append({
                "condition_id": condition_id,
                "run_status": run["run_status"],
                "work_completion_rate": run["work_completion_rate"],
                "reason": "workload did not complete the planned controlled operations",
            })
        if condition["profile"] == "C3":
            provider_by_profile["C3"].add(
                _pick(result, "collector", "observer_provider"))
        runs_by_cell.setdefault(
            (condition["profile"], condition["concurrency"]), []).append(run)

    conditions: dict[str, dict] = {}
    not_planned: dict[str, dict] = {}
    for profile in PROFILES:
        planned_levels = [level for level in CONCURRENCY
                          if profile in CONCURRENCY_PROFILES[level]]
        levels = {
            str(level): _aggregate_cell(
                profile, level, runs_by_cell.get((profile, level), []))
            for level in planned_levels
        }
        expected_runs = sum(
            count for (name, _), count in expected_by_cell.items() if name == profile)
        observed_runs = sum(
            len(runs) for (name, _), runs in runs_by_cell.items() if name == profile)
        unplanned = [level for level in CONCURRENCY
                     if profile not in CONCURRENCY_PROFILES[level]]
        if unplanned:
            not_planned[profile] = {str(level): "not_planned_by_protocol"
                                    for level in unplanned}
        conditions[profile] = {
            "concurrency": levels,
            "planned_concurrency": planned_levels,
            "not_planned_concurrency": unplanned,
            "not_planned": not_planned.get(profile, {}),
            "runs_expected": expected_runs,
            "runs_observed": observed_runs,
            "complete": observed_runs == expected_runs,
        }

    provider_set = provider_by_profile["C3"]
    if provider_set == {"llm_env"}:
        provider = "llm_env"
    elif provider_set == {"llm_config"}:
        provider = "llm_config"
    elif provider_set == {"llm_env", "llm_config"}:
        provider = "llm_env+llm_config"
    else:
        provider = "unknown"
    conditions["C3"]["observer_provider"] = provider

    controlled_capture = {
        profile: _aggregate_capture(profile, [
            run for (name, _), runs in runs_by_cell.items() if name == profile
            for run in runs])
        for profile in PROFILES
    }

    expected_runs = len(plan["conditions"])
    observed_runs = sum(len(runs) for runs in runs_by_cell.values())
    expected_blocks = len(plan["blocks"])
    complete_blocks = 0
    for block_id in plan["blocks"]:
        meta = plan["block_meta"][block_id]
        planned_profiles = set(CONCURRENCY_PROFILES[meta["concurrency"]])
        block_runs = [
            run for runs in runs_by_cell.values() for run in runs
            if run["block_id"] == block_id
        ]
        if (len(block_runs) == len(planned_profiles)
                and {run["profile"] for run in block_runs} == planned_profiles):
            complete_blocks += 1
    # The superseded 60-cell design was four profiles at every concurrency.
    not_planned_condition_runs = (
        len(PROFILES) * len(CONCURRENCY) * REPEATS - expected_runs)
    coverage = complete_blocks / expected_blocks if expected_blocks else None
    abstention = 1.0 - coverage if coverage is not None else None

    all_runs = [run for runs in runs_by_cell.values() for run in runs]
    statistics = None
    if (observed_runs == expected_runs and complete_blocks == expected_blocks
            and not failures and not degraded):
        statistics = _bootstrap_statistics(
            all_runs, expected_blocks=expected_blocks, seed=seed)

    limitations = [
        "same-host /proc and SQLite-file observations; kernel memory is system-wide",
        "cell values report raw per-run values plus mean/min/max/range; the max "
        "is not presented as the typical cost",
        "C1/C2 at concurrency 4/8/16/32 are not planned by the protocol, not "
        "missing attempts; only C3-C0 spans all 15 paired blocks",
        "C1/C2 component contrasts use only the three concurrency-1 blocks",
        "work_completion_rate is observed/expected controlled receipts; missing "
        "receipts reduce completion rather than becoming zero-valued capture loss",
        "CPU seconds are /proc cumulative observations while processes are alive; "
        "exited children are not reconstructed",
        "trace/database/WAL byte values are file observations; a missing file stays "
        "null rather than zero",
        "workload receipt latency/token/cost are self-reported claims kept under "
        "receipt_claims; they are not runtime Observer usage or verified alert latency",
        "runtime Observer usage and verified alert association are pending lead "
        "wiring and stay null",
        "controlled capture is scoped to completed controlled file-open operations, "
        "not global syscall or ring-buffer loss",
        "raw transport and persisted-operation missing fractions are separate; "
        "C1 has no persistence and C0 has no capture",
        "C3 requires an llm_env/llm_config configuration marker; mock, unknown, "
        "or missing provider evidence invalidates the condition run",
        "a measured capture fraction is rejected unless the trace CAPTURE_HEALTH "
        "(C1) or the persisted checkpoint (C2/C3) proves attachment and flush",
        "Observer provider markers are configuration evidence only, not model-call efficacy",
    ]
    if degraded:
        limitations.append(
            "degraded condition runs report their real work_completion_rate; "
            "they are not treated as complete fixed-work measurements")
    if statistics is None:
        limitations.append(
            "block bootstrap is not emitted: the 15-block/36-run cohort is incomplete "
            "or at least one measured value is missing")
    complete = (observed_runs == expected_runs and complete_blocks == expected_blocks
                and not missing and not failures and not degraded
                and statistics is not None)
    data = {
        "protocol": plan.get("protocol", PROTOCOL),
        "conditions": conditions,
        "controlled_capture": controlled_capture,
        "coverage": coverage,
        "abstention": abstention,
        "condition_runs_expected": expected_runs,
        "condition_runs_observed": observed_runs,
        "not_planned_condition_runs": not_planned_condition_runs,
        "not_planned": not_planned,
        "blocks_expected": expected_blocks,
        "blocks_complete": complete_blocks,
        "missing_condition_runs": missing,
        "invalid_condition_runs": failures,
        "degraded_condition_runs": degraded,
        "plan_sha256": plan["plan_sha256"],
    }
    if statistics is not None:
        data["statistics"] = statistics

    payload = {
        "schema": METRICS_SCHEMA,
        "experiment": "overhead",
        "release_id": release_id,
        "estimable": complete,
        "data": data,
        "limitations": limitations,
        "producer": {
            "adapter": "overhead_metrics",
            "plan_schema": PLAN_SCHEMA,
            "results_root": str(root),
            "measurement_schema": SCHEMA,
        },
    }
    if not complete:
        payload["not_estimable_reason"] = (
            "overhead cohort is incomplete: "
            f"{observed_runs}/{expected_runs} condition runs, "
            f"{complete_blocks}/{expected_blocks} complete blocks, "
            f"{len(failures)} invalid, {len(degraded)} degraded, "
            f"{len(missing)} missing")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--out", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        payload = build_metrics(
            plan,
            args.results,
            release_id=args.release_id,
        )
    except (OSError, ValueError) as exc:
        print(f"overhead metrics: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps({"schema": payload["schema"], "estimable": payload["estimable"],
                      "out": str(args.out) if args.out else None}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
