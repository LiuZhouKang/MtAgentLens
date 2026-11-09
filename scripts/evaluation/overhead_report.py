# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Write the frozen OverheadBench T8 table and F2 figure from real artifacts.

The input is a study root produced by the overhead driver:

    <study>/formal-plan.json
    <study>/formal/execution-report.json
    <study>/formal/<condition_id>/result.json
    <study>/formal/<condition_id>/measurement-evidence.json

Pilot data is never accepted as formal.  ``--allow-partial`` may read a
``pilot-plan.json`` / ``pilot/`` study, but the output is explicitly labelled
PARTIAL - NOT FINAL.  Missing values stay null and are rendered as an em dash;
they are never replaced by zero.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import textwrap

from scripts.evaluation.overhead_workload import (
    PEAK_PER_MILLION,
    PRICE_DATE,
    PRICE_SOURCE,
)

REPORT_SCHEMA = "mtagentlens.overhead-report/1"
RAW_SCHEMA = "mtagentlens.overhead-report-raw/1"
PROBE_MEASUREMENT_SCHEMA = "mtagentlens.probe-overhead-measurement/1"
PROBE_DIAGNOSTIC_SCHEMA = "mtagentlens.probe-overhead-diagnostic/1"
PROFILES = ("C0", "C1", "C2", "C3")
LEVELS = (1, 4, 8, 16, 32)
EXPECTED_MATRIX = {
    1: ("C0", "C1", "C2", "C3"),
    4: ("C0", "C3"),
    8: ("C0", "C3"),
    16: ("C0", "C3"),
    32: ("C0", "C3"),
}
REPEATS = 3
PLANNED_CONDITIONS = 36
PROFILE_LABELS = {
    "C0": "agent_alone",
    "C1": "collector_trace_capture_only",
    "C2": "collector_trace_disable_responsibility_observer",
    "C3": "collector_trace_default",
}
COST_BASIS = "deepseek_peak_rate_upper_bound_not_invoice"
COST_NOTE = (
    "Cost values are upper bounds computed from real token usage at DeepSeek "
    f"official peak rates ({PRICE_DATE}, {PRICE_SOURCE}); they are not an invoice. "
    "Claude's self-mapped reported_cost_usd is intentionally ignored."
)
PROBE_NOTE = (
    "Only BPF program execution time is reported here. Hook trigger, userspace "
    "consumption, ring-buffer handling and end-to-end collection latency are out "
    "of scope. A zero run_cnt delta is uncovered, not 0 ns."
)
DIAGNOSTIC_NOTE = (
    "Short diagnostic window over existing programs. Counts may include fast "
    "filtered returns; this is not a full per-event processing cost and cannot "
    "produce an event-level p95."
)


class ReportError(ValueError):
    """Invalid or non-final study input; no formal report is produced."""


def _load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ReportError(f"cannot read JSON {path}: {error}") from error


def _as_paths(value):
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [Path(value)]
    return [Path(item) for item in value]


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _fraction(value):
    value = _number(value)
    return value if value is not None and 0 <= value <= 1 else None


def _dig(value, *keys):
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _stats(values):
    known = [value for value in values if value is not None]
    if not known:
        return {"n": 0, "mean": None, "min": None, "max": None}
    return {"n": len(known), "mean": sum(known) / len(known),
            "min": min(known), "max": max(known)}


def _usage_tokens(usage):
    if not isinstance(usage, dict):
        return None
    incoming = usage.get("input_tokens")
    outgoing = usage.get("output_tokens")
    if any(type(value) is not int or value < 0 for value in (incoming, outgoing)):
        return None
    cached = usage.get("cache_read_input_tokens")
    created = usage.get("cache_creation_input_tokens")
    if any(type(value) is not int or value < 0 for value in (cached, created)):
        return None
    return incoming, outgoing, cached, created


def _usage_cost(usage, model):
    """Peak-rate upper bound from usage, never from reported_cost_usd."""
    if model not in ("deepseek-flash", "deepseek-v4-flash"):
        return None
    tokens = _usage_tokens(usage)
    if tokens is None:
        return None
    incoming, outgoing, cached, created = tokens
    return ((incoming + created) * PEAK_PER_MILLION["input"]
            + cached * PEAK_PER_MILLION["cached_input"]
            + outgoing * PEAK_PER_MILLION["output"]) / 1e6


def validate_plan(plan):
    """Validate the frozen 36-condition §6 matrix; raise on any drift."""
    if not isinstance(plan, dict):
        raise ReportError("frozen plan must be a JSON object")
    if not str(plan.get("schema", "")).startswith("mtagentlens.overhead-plan/"):
        raise ReportError("frozen plan has an unexpected schema")
    if "§6" not in str(plan.get("protocol", "")):
        raise ReportError("frozen plan does not name EXPERIMENT-SUITE-7 §6")
    matrix = plan.get("matrix")
    profiles = _dig(matrix, "concurrency_profiles")
    if not isinstance(profiles, dict):
        raise ReportError("frozen plan has no matrix.concurrency_profiles")
    if {str(key) for key in profiles} != {str(level) for level in LEVELS}:
        raise ReportError("frozen plan matrix does not contain exactly concurrency 1/4/8/16/32")
    for level in LEVELS:
        actual = profiles.get(str(level), profiles.get(level))
        if not isinstance(actual, list) or set(actual) != set(EXPECTED_MATRIX[level]):
            raise ReportError(
                f"matrix concurrency {level} must contain exactly "
                f"{sorted(EXPECTED_MATRIX[level])}; C1/C2 at concurrency > 1 "
                "are not planned by §6")
    if plan.get("repeats") != REPEATS:
        raise ReportError(f"frozen plan must declare {REPEATS} repeats")
    rows = plan.get("condition_runs")
    if not isinstance(rows, list) or len(rows) != PLANNED_CONDITIONS:
        raise ReportError(
            f"frozen plan must contain exactly {PLANNED_CONDITIONS} condition_runs")
    expected = {(level, repeat, profile) for level in LEVELS
                for repeat in range(1, REPEATS + 1) for profile in EXPECTED_MATRIX[level]}
    actual, ids = [], set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ReportError(f"condition_runs[{index}] is not an object")
        condition_id = row.get("condition_id")
        if not isinstance(condition_id, str) or not condition_id:
            raise ReportError(f"condition_runs[{index}] has no condition_id")
        if condition_id in ids:
            raise ReportError(f"duplicate condition_id in frozen plan: {condition_id}")
        ids.add(condition_id)
        profile, level, repeat = row.get("profile"), row.get("concurrency"), row.get("repeat")
        if profile not in PROFILES:
            raise ReportError(f"{condition_id} has unsupported profile {profile!r}")
        if level not in LEVELS or type(repeat) is not int or repeat not in (1, 2, 3):
            raise ReportError(f"{condition_id} has invalid concurrency/repeat")
        if row.get("block_id") != f"c{level}-r{repeat}":
            raise ReportError(
                f"{condition_id} block_id does not match c{level}-r{repeat}")
        if level > 1 and profile in ("C1", "C2"):
            raise ReportError(
                f"{condition_id}: C1/C2 at concurrency > 1 are not planned by §6")
        actual.append((level, repeat, profile))
    if len(actual) != PLANNED_CONDITIONS or set(actual) != expected:
        raise ReportError(
            f"condition_runs do not match the §6 {PLANNED_CONDITIONS}-condition matrix")
    blocks = plan.get("blocks")
    if blocks is not None and (not isinstance(blocks, list) or len(blocks) != 15):
        raise ReportError("frozen plan must contain 15 paired blocks")
    if plan.get("condition_run_count") not in (None, PLANNED_CONDITIONS):
        raise ReportError("frozen plan condition_run_count is inconsistent")
    digest = plan.get("plan_sha256")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)):
        raise ReportError("frozen plan has no valid plan_sha256")
    return plan


def resolve_study(study, *, allow_partial=False):
    study = Path(study)
    if not study.is_dir():
        raise ReportError(f"study root is not a directory: {study}")
    formal_plan = study / "formal-plan.json"
    if formal_plan.is_file():
        return {"phase": "formal", "plan_path": formal_plan,
                "run_dir": study / "formal",
                "execution_path": study / "formal" / "execution-report.json"}
    if not allow_partial:
        raise ReportError(
            "formal-plan.json not found; pilot data cannot be reported as formal. "
            "Use --allow-partial for an explicitly NOT FINAL diagnostic export.")
    pilot_plan = study / "pilot-plan.json"
    if pilot_plan.is_file():
        return {"phase": "pilot", "plan_path": pilot_plan,
                "run_dir": study / "pilot",
                "execution_path": study / "pilot" / "execution-report.json"}
    raise ReportError("study root has neither formal-plan.json nor pilot-plan.json")


def _load_runs(paths, plan, *, allow_partial):
    planned = {row["condition_id"]: row for row in plan["condition_runs"]}
    found, warnings = {}, []
    run_dir = paths["run_dir"]
    if not run_dir.is_dir():
        raise ReportError(f"condition directory is missing: {run_dir}")
    for child in sorted(run_dir.iterdir()):
        result_path = child / "result.json"
        if not child.is_dir() or not result_path.is_file():
            continue
        try:
            result = _load_json(result_path)
        except ReportError:
            if not allow_partial:
                raise
            warnings.append(f"unreadable result skipped: {result_path}")
            continue
        condition_id = result.get("condition_id")
        if condition_id not in planned:
            if not allow_partial:
                raise ReportError(f"result is not in the frozen plan: {result_path}")
            warnings.append(f"unplanned result skipped: {result_path}")
            continue
        if condition_id in found:
            raise ReportError(f"duplicate result.json for {condition_id}")
        found[condition_id] = (child, result)

    missing = [condition_id for condition_id in planned if condition_id not in found]
    if not allow_partial and missing:
        raise ReportError(
            f"formal cohort is incomplete: {len(missing)} missing condition runs "
            f"({len(found)}/{PLANNED_CONDITIONS} present); refusing formal conclusion. "
            f"Missing: {', '.join(missing)}")
    if not found:
        raise ReportError("no condition result.json files found")

    runs = []
    for condition_id, row in planned.items():
        if condition_id not in found:
            continue
        directory, result = found[condition_id]
        for key in ("condition_id", "block_id", "profile", "concurrency", "repeat"):
            if result.get(key) != row.get(key):
                message = (f"{condition_id} result {key}={result.get(key)!r} does not "
                           f"match frozen plan {row.get(key)!r}")
                if not allow_partial:
                    raise ReportError(message)
                warnings.append(message)
        if (result.get("condition_id") != row["condition_id"]
                or result.get("profile") != row["profile"]
                or result.get("concurrency") != row["concurrency"]
                or result.get("repeat") != row["repeat"]):
            continue
        if result.get("status") != "completed" and not allow_partial:
            raise ReportError(
                f"{condition_id} status is {result.get('status')!r}; "
                "refusing formal conclusion")
        evidence_path = directory / "measurement-evidence.json"
        evidence = None
        if evidence_path.is_file():
            evidence = _load_json(evidence_path)
            if evidence.get("condition_id") != condition_id:
                message = f"{condition_id} measurement evidence has a mismatched condition_id"
                if not allow_partial:
                    raise ReportError(message)
                warnings.append(message)
                evidence = None
        elif not allow_partial:
            raise ReportError(
                f"{condition_id} is missing measurement-evidence.json; "
                "refusing formal conclusion")
        else:
            warnings.append(f"{condition_id} has no measurement-evidence.json")
        runs.append(_run_record(row, result, evidence,
                                result_path=directory / "result.json",
                                evidence_path=evidence_path if evidence else None,
                                warnings=warnings))
    return runs, warnings


def read_graph_size(path):
    """Final persisted graph size: last snapshot JSON, then legacy row tables."""
    if not path or not Path(path).is_file():
        return None, None
    try:
        connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "graph_snapshots" in tables:
                row = connection.execute(
                    "SELECT nodes_json, edges_json FROM graph_snapshots "
                    "ORDER BY timestamp_ns DESC LIMIT 1").fetchone()
                if row is not None:
                    nodes, edges = json.loads(row[0]), json.loads(row[1])
                    if not isinstance(nodes, list) or not isinstance(edges, list):
                        return None, None
                    return len(nodes), len(edges)
            if {"graph_nodes", "graph_edges"} <= tables:
                return (connection.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0],
                        connection.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0])
            return None, None
        finally:
            connection.close()
    except (sqlite3.Error, ValueError, TypeError):
        return None, None


def read_layer_counters(path):
    """Keep transport, semantic filtering and persistence counters separate."""
    healths, runtime, persistence = [], None, None
    path = Path(path)
    for line in path.read_text(errors="replace").splitlines() if path.is_file() else []:
        index = line.find('{"event":"CAPTURE_HEALTH"')
        if index >= 0:
            try:
                healths.append(json.loads(line[index:])["health"])
            except (ValueError, KeyError):
                pass
        if "[diagnostics:json] " in line:
            try:
                runtime = json.loads(line.split("[diagnostics:json] ", 1)[1])
            except ValueError:
                pass
        match = re.search(r"persistence: flushed cleanly \((\d+) dropped, (\d+) write failures, (\d+) closed-channel\)", line)
        if match:
            persistence = dict(zip(("dropped", "write_failures", "closed_channel"),
                                   map(int, match.groups())))
    # Concurrent source runners can print an older partial snapshot last.
    # Prefer the snapshot with the most terminal source witnesses, not line order.
    health = max(healths, key=lambda h: sum(
        int(s.get("drain_complete") is True) + int(s.get("final_stats_seen") is True)
        for s in h.get("sources", {}).values()), default={})
    return {"capture_status": health.get("status"), "sources": health.get("sources"),
            "persistence": persistence,
            "runtime_semantic_counters": ({k: v for k, v in runtime.items()
                if k.startswith("actions_dropped_") or k in ("partial_tls_events", "decode_status")}
                if runtime is not None else None)}


def paired_overheads(runs):
    pairs = []
    by_block = {(r["block_id"], r["profile"]): r for r in runs if r["included"]}
    for run in runs:
        if run["profile"] != "C3" or not run["included"]:
            continue
        baseline = by_block.get((run["block_id"], "C0"))
        pair = {"block_id": run["block_id"], "concurrency": run["concurrency"]}
        for metric, name in (("agent_duration_s", "agent_duration_increase_percent"),
                             ("agent_wall_span_s", "agent_wall_span_increase_percent")):
            before = baseline.get(metric) if baseline else None
            after = run.get(metric)
            complete = baseline and baseline.get("work_completion_rate") == 1 and run.get("work_completion_rate") == 1
            pair[name] = ((after / before - 1) * 100
                          if complete and before and after is not None else None)
        pairs.append(pair)
    return pairs


def _run_record(row, result, evidence, *, result_path, evidence_path, warnings):
    profile, concurrency = row["profile"], row["concurrency"]
    evidence = evidence if isinstance(evidence, dict) else {}
    agents = evidence.get("agents") if isinstance(evidence.get("agents"), list) else []
    record = {
        "condition_id": row["condition_id"], "block_id": row["block_id"],
        "profile": profile, "concurrency": concurrency, "repeat": row["repeat"],
        "result_status": result.get("status"),
        "included": result.get("status") == "completed",
        "source_result": str(result_path),
        "source_evidence": str(evidence_path) if evidence_path else None,
        "condition_elapsed_s": _number(result.get("duration_s")),
    }
    completion = result.get("work_completion_rate")
    if completion is None:
        completion = _dig(result, "receipts", "workload_completion_rate")
    record["work_completion_rate"] = _fraction(completion)

    record["agent_instances_expected"] = concurrency
    record["agent_instances_observed"] = len(agents)
    durations = [_number(agent.get("duration_s")) for agent in agents
                 if isinstance(agent, dict)]
    if len(durations) == concurrency and all(value is not None for value in durations):
        record["agent_duration_s"] = sum(durations) / concurrency
    else:
        record["agent_duration_s"] = None
    starts = [_count(agent.get("started_ns")) for agent in agents
              if isinstance(agent, dict)]
    ends = [_count(agent.get("completed_ns")) for agent in agents
            if isinstance(agent, dict)]
    record["agent_wall_span_s"] = (
        (max(ends) - min(starts)) / 1e9
        if (len(starts) == concurrency and len(ends) == concurrency
            and all(value is not None for value in starts + ends))
        else None)

    agent_costs, inputs, outputs, reported = [], [], [], []
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        model = agent.get("model")
        usage = agent.get("usage")
        agent_costs.append(_usage_cost(usage, model))
        tokens = _usage_tokens(usage)
        inputs.append(tokens[0] if tokens else None)
        outputs.append(tokens[1] if tokens else None)
        reported.append(_number(usage.get("reported_cost_usd"))
                        if isinstance(usage, dict) else None)
    complete_agents = (len(agents) == concurrency and agents
                       and all(cost is not None for cost in agent_costs)
                       and all(token is not None for token in inputs + outputs))
    if complete_agents:
        record["agent_cost_upper_bound_usd"] = sum(agent_costs)
        record["agent_input_tokens"] = sum(inputs)
        record["agent_output_tokens"] = sum(outputs)
        record["reported_cost_usd_ignored"] = (
            sum(reported) if all(value is not None for value in reported) else None)
    else:
        record["agent_cost_upper_bound_usd"] = None
        record["agent_input_tokens"] = None
        record["agent_output_tokens"] = None
        record["reported_cost_usd_ignored"] = None

    observer = evidence.get("observer") if isinstance(evidence.get("observer"), dict) else {}
    attempts = _count(observer.get("attempts"))
    record["observer_attempts"] = attempts
    record["observer_requests"] = _count(observer.get("requests"))
    record["observer_failed_attempts"] = _count(observer.get("failed_attempts"))
    records = observer.get("attempt_records")
    records = records if isinstance(records, list) else []
    if attempts == 0:
        record["observer_input_tokens"] = 0
        record["observer_output_tokens"] = 0
        record["observer_cost_upper_bound_usd"] = 0.0
        record["observer_known_cost_upper_bound_usd"] = 0.0
    elif attempts is not None and len(records) == attempts:
        costs, tokens_in, tokens_out = [], [], []
        for attempt in records:
            usage = attempt.get("usage") if isinstance(attempt, dict) else None
            costs.append(_usage_cost(usage, attempt.get("model")
                                     if isinstance(attempt, dict) else None))
            tokens = _usage_tokens(usage)
            tokens_in.append(tokens[0] if tokens else None)
            tokens_out.append(tokens[1] if tokens else None)
        known_costs = [cost for cost in costs if cost is not None]
        record["observer_known_cost_upper_bound_usd"] = (
            sum(known_costs) if known_costs else (0.0 if not costs else None))
        if all(cost is not None for cost in costs):
            record["observer_cost_upper_bound_usd"] = sum(costs)
            record["observer_input_tokens"] = (
                sum(tokens_in) if all(value is not None for value in tokens_in) else None)
            record["observer_output_tokens"] = (
                sum(tokens_out) if all(value is not None for value in tokens_out) else None)
        else:
            record["observer_cost_upper_bound_usd"] = None
            record["observer_input_tokens"] = None
            record["observer_output_tokens"] = None
    else:
        record["observer_input_tokens"] = None
        record["observer_output_tokens"] = None
        record["observer_cost_upper_bound_usd"] = None
        record["observer_known_cost_upper_bound_usd"] = None
    caches = [_count(_dig(a, "usage", "cache_read_input_tokens")) for a in records]
    record["observer_cache_read_tokens"] = (sum(caches) if attempts is not None
        and len(caches) == attempts and all(v is not None for v in caches) else None)

    if (record["agent_cost_upper_bound_usd"] is not None
            and record["observer_cost_upper_bound_usd"] is not None):
        record["total_cost_upper_bound_usd"] = (
            record["agent_cost_upper_bound_usd"]
            + record["observer_cost_upper_bound_usd"])
    else:
        record["total_cost_upper_bound_usd"] = None

    resources = result.get("resources") if isinstance(result.get("resources"), dict) else {}
    record["layer_counters"] = read_layer_counters(Path(result_path).parent / "collector.stderr")
    record["workload_rss_peak_mib"] = (
        value / 1024 if (value := _number(_dig(resources, "workloads", "rss_kib", "max"))) is not None else None)
    record["workload_cpu_mean_percent"] = _number(_dig(resources, "workloads", "cpu_percent", "mean"))
    exact_cpu = [_number(a.get("agent_cpu_seconds")) for a in agents]
    record["agent_cpu_seconds"] = (sum(exact_cpu) if len(exact_cpu) == concurrency
        and all(v is not None for v in exact_cpu) else None)
    record["collector_cpu_seconds"] = _number(_dig(resources, "collector", "cpu_seconds", "max")) if profile != "C0" else None
    rss_mean = _number(_dig(resources, "collector", "rss_kib", "mean")) if profile != "C0" else None
    record["collector_rss_mean_mib"] = rss_mean / 1024 if rss_mean is not None else None
    for statistic, label in (("mean", "mean"), ("max", "peak")):
        pss = _number(_dig(resources, "collector", "pss_kib", statistic)) if profile != "C0" else None
        record[f"collector_pss_{label}_mib"] = pss / 1024 if pss is not None else None
    record["collector_pss_missing_samples"] = (
        _number(_dig(resources, "collector", "pss_missing_samples")) if profile != "C0" else None)
    if profile == "C0":
        record["collector_cpu_mean_percent"] = None
        record["collector_rss_peak_mib"] = None
        record["trace_final_mib"] = None
        record["db_main_final_mib"] = None
        record["db_wal_final_mib"] = None
        record["db_wal_peak_mib"] = None
        record["db_total_peak_mib"] = None
    else:
        record["collector_cpu_mean_percent"] = _number(
            _dig(resources, "collector", "cpu_percent", "mean"))
        rss_kib = _number(_dig(resources, "collector", "rss_kib", "max"))
        record["collector_rss_peak_mib"] = rss_kib / 1024 if rss_kib is not None else None
        trace_bytes = _number(_dig(resources, "trace_bytes", "final"))
        record["trace_final_mib"] = trace_bytes / (1024 * 1024) if trace_bytes is not None else None
        if profile in ("C2", "C3"):
            main = _number(_dig(resources, "database_bytes", "main", "final"))
            wal_final = _number(_dig(resources, "database_bytes", "wal", "final"))
            wal_peak = _number(_dig(resources, "database_bytes", "wal", "peak"))
            total_peak = _number(_dig(resources, "database_bytes", "total", "peak"))
            record["db_main_final_mib"] = main / (1024 * 1024) if main is not None else None
            record["db_wal_final_mib"] = (
                wal_final / (1024 * 1024) if wal_final is not None else None)
            record["db_wal_peak_mib"] = (
                wal_peak / (1024 * 1024) if wal_peak is not None else None)
            record["db_total_peak_mib"] = (
                total_peak / (1024 * 1024) if total_peak is not None else None)
        else:
            record["db_main_final_mib"] = None
            record["db_wal_final_mib"] = None
            record["db_wal_peak_mib"] = None
            record["db_total_peak_mib"] = None

    graph = evidence.get("graph_update")
    record["graph_nodes_final"], record["graph_edges_final"] = read_graph_size(result.get("database_path"))
    record["graph_update_count"] = _count(_dig(graph, "total_count"))
    if record["graph_update_count"] is None:
        record["graph_update_count"] = _count(_dig(graph, "count"))
    record["graph_update_total_ns"] = _count(_dig(graph, "total_ns"))
    record["graph_update_mean_us"] = (record["graph_update_total_ns"] / record["graph_update_count"] / 1000
        if record["graph_update_count"] and record["graph_update_total_ns"] is not None else None)
    record["graph_update_p50_us"] = _number(_dig(graph, "p50_us"))
    record["graph_update_p95_us"] = _number(_dig(graph, "p95_us"))
    attempt_latencies = [_number(a.get("elapsed_ms")) for a in records if isinstance(a, dict)]
    attempt_latencies = [v for v in attempt_latencies if v is not None]
    record["observer_p50_ms"] = _percentile(attempt_latencies, .50)
    record["observer_p95_ms"] = _percentile(attempt_latencies, .95)

    controlled = result.get("controlled_capture")
    controlled = controlled if isinstance(controlled, dict) else {}
    record["controlled_capture_status"] = controlled.get("status")
    record["controlled_operations_observed"] = _count(_dig(controlled, "denominator", "observed"))
    if profile == "C0":
        record["raw_missing_fraction"] = None
        record["persisted_operation_missing_fraction"] = None
    else:
        record["raw_missing_fraction"] = _fraction(
            controlled.get("raw_transport_missing_fraction"))
        record["persisted_operation_missing_fraction"] = (
            None if profile == "C1" else _fraction(
                controlled.get("persisted_operation_missing_fraction")))

    alert = evidence.get("alert_latency")
    alert = alert if isinstance(alert, dict) else {}
    record["alert_available"] = alert.get("available") is True
    record["alert_linked_operations"] = _count(alert.get("linked_operations"))
    record["alert_total_operations"] = _count(alert.get("total_operations"))
    if (record["alert_available"]
            and record["alert_total_operations"] not in (None, 0)
            and record["alert_linked_operations"] is not None):
        record["alert_coverage"] = (
            record["alert_linked_operations"] / record["alert_total_operations"])
    else:
        record["alert_coverage"] = None
    latencies = [_number(sample.get("latency_ms"))
                 for sample in alert.get("samples", [])
                 if isinstance(sample, dict)]
    record["alert_samples"] = len(latencies)
    record["alert_p50_ms"] = _percentile(latencies, .50)
    record["alert_p95_ms"] = _percentile(latencies, .95)
    if not record["included"]:
        warnings.append(f"{row['condition_id']} status is {result.get('status')!r}; excluded")
    return record


METRICS = (
    ("workload_rss_peak_mib", "mib"),
    ("workload_cpu_mean_percent", "percent"),
    ("agent_cpu_seconds", "seconds"),
    ("collector_cpu_seconds", "seconds"),
    ("graph_update_mean_us", "us"),
    ("graph_update_p50_us", "us"),
    ("graph_update_p95_us", "us"),
    ("observer_p50_ms", "ms"),
    ("observer_p95_ms", "ms"),
    ("observer_cache_read_tokens", "tokens"),
    ("graph_nodes_final", "count"),
    ("graph_edges_final", "count"),
    ("work_completion_rate", "fraction"),
    ("agent_duration_s", "seconds"),
    ("condition_elapsed_s", "seconds"),
    ("agent_wall_span_s", "seconds"),
    ("collector_cpu_mean_percent", "percent"),
    ("collector_rss_peak_mib", "mib"),
    ("collector_rss_mean_mib", "mib"),
    ("collector_pss_mean_mib", "mib"),
    ("collector_pss_peak_mib", "mib"),
    ("collector_pss_missing_samples", "count"),
    ("db_main_final_mib", "mib"),
    ("db_wal_final_mib", "mib"),
    ("db_wal_peak_mib", "mib"),
    ("db_total_peak_mib", "mib"),
    ("trace_final_mib", "mib"),
    ("graph_update_count", "count"),
    ("graph_update_total_ns", "ns"),
    ("observer_attempts", "count"),
    ("observer_requests", "count"),
    ("observer_failed_attempts", "count"),
    ("observer_input_tokens", "tokens"),
    ("observer_output_tokens", "tokens"),
    ("agent_cost_upper_bound_usd", "usd"),
    ("observer_cost_upper_bound_usd", "usd"),
    ("total_cost_upper_bound_usd", "usd"),
    ("raw_missing_fraction", "fraction"),
    ("persisted_operation_missing_fraction", "fraction"),
    ("alert_linked_operations", "count"),
    ("alert_total_operations", "count"),
    ("alert_coverage", "fraction"),
    ("alert_p50_ms", "ms"),
    ("alert_p95_ms", "ms"),
)
RUN_FIELDS = (
    "workload_rss_peak_mib", "workload_cpu_mean_percent", "agent_cpu_seconds",
    "collector_cpu_seconds", "graph_update_mean_us", "graph_update_p50_us",
    "graph_update_p95_us", "observer_p50_ms", "observer_p95_ms",
    "observer_cache_read_tokens",
    "graph_nodes_final", "graph_edges_final",
    "condition_elapsed_s", "work_completion_rate", "agent_instances_expected",
    "agent_instances_observed", "agent_duration_s", "agent_wall_span_s",
    "agent_cost_upper_bound_usd", "agent_input_tokens", "agent_output_tokens",
    "reported_cost_usd_ignored", "collector_cpu_mean_percent",
    "collector_rss_peak_mib", "db_main_final_mib", "db_wal_final_mib",
    "collector_rss_mean_mib",
    "collector_pss_mean_mib", "collector_pss_peak_mib", "collector_pss_missing_samples",
    "db_wal_peak_mib", "db_total_peak_mib", "trace_final_mib",
    "graph_update_count", "graph_update_total_ns", "observer_attempts",
    "observer_requests", "observer_failed_attempts", "observer_input_tokens",
    "observer_output_tokens", "observer_cost_upper_bound_usd",
    "observer_known_cost_upper_bound_usd", "total_cost_upper_bound_usd",
    "raw_missing_fraction", "persisted_operation_missing_fraction",
    "controlled_capture_status", "alert_available", "alert_linked_operations",
    "controlled_operations_observed",
    "alert_total_operations", "alert_coverage", "alert_samples",
    "alert_p50_ms", "alert_p95_ms",
)


def _build_cells(runs):
    cells = []
    for profile in PROFILES:
        for concurrency in LEVELS:
            planned = profile in EXPECTED_MATRIX[concurrency]
            group = [run for run in runs
                     if run["profile"] == profile and run["concurrency"] == concurrency]
            included = [run for run in group if run["included"]]
            failed = [run for run in group if not run["included"]]
            if not planned:
                status = "not_planned"
            elif not included:
                status = "missing"
            elif len(included) < REPEATS:
                status = "partial"
            else:
                status = "complete"
            cell = {
                "profile": profile, "profile_label": PROFILE_LABELS[profile],
                "concurrency": concurrency, "planned": planned,
                "cell_status": status, "n_runs": len(included),
                "n_planned": REPEATS, "n_failed": len(failed),
            }
            for key, _kind in METRICS:
                cell[key] = _stats([run.get(key) for run in included])
            cells.append(cell)
    return cells


def _fmt_value(value, kind):
    if value is None:
        return "—"
    if kind == "fraction":
        return f"{value * 100:.1f}%"
    if kind == "seconds":
        return f"{value:.3f}"
    if kind == "percent":
        return f"{value:.2f}"
    if kind == "mib":
        return f"{value:.4f}"
    if kind == "usd":
        return f"{value:.6f}"
    if kind == "ms":
        return f"{value:.3f}"
    if kind == "tokens":
        return f"{value:.1f}"
    if kind == "ns":
        return f"{value:.1f}"
    return f"{value:.1f}"


def _stats_text(cell, key, kind):
    stats = cell[key]
    if not stats["n"]:
        return "—"
    suffix = "†" if stats["n"] < cell["n_runs"] else ""
    return (f"{_fmt_value(stats['mean'], kind)} "
            f"[{_fmt_value(stats['min'], kind)}–{_fmt_value(stats['max'], kind)}]{suffix}")


def _n_text(cell):
    if cell["cell_status"] == "not_planned":
        return "0 (not planned)"
    text = f"{cell['n_runs']}/{REPEATS}"
    if cell["n_failed"]:
        text += f" ({cell['n_failed']} failed)"
    return text


def _status_text(cell):
    return {"not_planned": "not planned", "missing": "missing",
            "partial": "partial", "complete": "complete"}[cell["cell_status"]]


def _coverage_text(runs):
    parts = []
    for concurrency in LEVELS:
        group = [run for run in runs
                 if run["included"] and run["profile"] == "C3"
                 and run["concurrency"] == concurrency]
        linked = [run.get("alert_linked_operations") for run in group]
        total = [run.get("alert_total_operations") for run in group]
        if group and all(value is not None for value in linked + total):
            parts.append(f"{concurrency}: {sum(linked)}/{sum(total)}")
        else:
            parts.append(f"{concurrency}: n/a")
    return "; ".join(parts)


def _write_t8_csv(path, cells, status, phase):
    fields = ["report_status", "phase", "profile", "profile_label", "concurrency",
              "planned", "cell_status", "n_runs", "n_planned", "n_failed",
              "cost_basis", "notes"]
    for key, _kind in METRICS:
        fields.extend([f"{key}_mean", f"{key}_min", f"{key}_max", f"{key}_n"])
    with Path(path).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for cell in cells:
            row = {
                "report_status": status, "phase": phase,
                "profile": cell["profile"], "profile_label": cell["profile_label"],
                "concurrency": cell["concurrency"],
                "planned": "true" if cell["planned"] else "false",
                "cell_status": cell["cell_status"], "n_runs": cell["n_runs"],
                "n_planned": cell["n_planned"], "n_failed": cell["n_failed"],
                "cost_basis": COST_BASIS,
                "notes": ("not planned by §6; not a missing attempt"
                          if not cell["planned"] else ""),
            }
            for key, _kind in METRICS:
                stats = cell[key]
                row[f"{key}_mean"] = "" if stats["mean"] is None else stats["mean"]
                row[f"{key}_min"] = "" if stats["min"] is None else stats["min"]
                row[f"{key}_max"] = "" if stats["max"] is None else stats["max"]
                row[f"{key}_n"] = stats["n"]
            writer.writerow(row)


def _write_raw(path_json, path_csv, loaded, runs, status):
    payload = {
        "schema": RAW_SCHEMA, "report_status": status, "phase": loaded["phase"],
        "plan_path": str(loaded["plan_path"]),
        "plan_sha256": loaded["plan"]["plan_sha256"],
        "execution_path": str(loaded["execution_path"]),
        "execution_complete": loaded["execution"].get("complete"),
        "cost_basis": COST_BASIS, "cost_note": COST_NOTE,
        "pricing_source": PRICE_SOURCE, "pricing_date": PRICE_DATE,
        "warnings": loaded["warnings"], "runs": runs,
        "paired_overheads": paired_overheads(runs),
    }
    Path(path_json).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    fields = ["condition_id", "block_id", "profile", "concurrency", "repeat",
              "result_status", "included", "source_result", "source_evidence"]
    fields.extend(RUN_FIELDS)
    with Path(path_csv).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for run in runs:
            row = {key: run.get(key) for key in fields}
            writer.writerow(row)


def _stats_mean(cell, key):
    stats = cell[key]
    return "—" if not stats["n"] else _fmt_value(stats["mean"], dict(METRICS)[key])


def _write_t8_md(path, loaded, cells, status, probe_data, diagnostics):
    plan = loaded["plan"]
    lines = []
    if status == "final_complete":
        lines.extend([
            "# T8 — Overhead accounting (FINAL — 36/36 formal condition runs)", "",
            f"- Phase: `{loaded['phase']}`",
            f"- Plan SHA-256: `{plan['plan_sha256']}`",
            f"- Complete frozen cohort: {PLANNED_CONDITIONS}/{PLANNED_CONDITIONS} "
            "formal condition runs; execution report complete.",
        ])
        recovery = loaded["execution"].get("recovery")
        if isinstance(recovery, dict) and recovery.get("archived_attempts"):
            archived = ", ".join(str(row.get("condition_id"))
                                 for row in recovery["archived_attempts"])
            guard = ""
            if (recovery.get("frozen_shutdown_timeout_s") is not None
                    and recovery.get("recovery_shutdown_timeout_s") is not None):
                guard = (f" (harness shutdown guard {recovery['frozen_shutdown_timeout_s']:g} s"
                         f" -> {recovery['recovery_shutdown_timeout_s']:g} s)")
            lines.append(
                f"- Recovery: archived truncated attempt(s) {archived} retained; the same "
                f"frozen condition was re-run under a raised harness shutdown guard{guard}. "
                "Matrix, workload, profile flags and measurement definitions are unchanged; "
                "archived attempt cost remains in the ledger.")
    else:
        lines.extend([
            "# T8 — Overhead accounting (PARTIAL — NOT FINAL)", "",
            "> **PARTIAL — NOT FINAL.** This is a diagnostic export, not a formal "
            "complete result. Do not cite it as the 36-condition formal cohort.",
            "",
            f"- Phase: `{loaded['phase']}` (pilot data is never formal)",
            f"- Plan SHA-256: `{plan['plan_sha256']}`",
            f"- Available successful condition runs: "
            f"{sum(cell['n_runs'] for cell in cells)}/{PLANNED_CONDITIONS}; "
            f"execution report complete: {bool(loaded['execution'].get('complete'))}.",
        ])
        if loaded["execution"].get("failure"):
            lines.append(f"- Execution failure: {loaded['execution']['failure']}")
    lines.extend([
        "", "## Reading the table", "",
        f"- Planned repeats per cell: {REPEATS}. Each metric is rendered as "
        "`mean [min–max]` over successful repeats; `†` marks fewer known repeats "
        "than the cell's n.",
        "- Em dash (`—`) means missing or not applicable; missing is never replaced "
        "by zero. `T8.csv` carries the full min/max and known-repeat count for every "
        "metric, including the combined Observer and alert columns.",
        "- C1/C2 at concurrency 4/8/16/32 are **not planned** by §6 and are not "
        "missing attempts.",
        "- Raw controlled-operation missing and persisted-operation loss are "
        "separate; C0 has no collector capture and C1 has no persistence.",
        "- Agent end-to-end duration is the mean per-instance "
        "`agent-metrics.json` duration, not the collector's elapsed time.",
        "- Condition elapsed is the driver's whole condition wall time; it includes "
        "collector startup, the fixed workload and the final phase-2 review/drain. "
        "It is not a per-agent task duration and C0 has no collector phase.",
        "- Collector CPU is `resources.collector.cpu_percent.mean` (percent of one "
        "core; values above 100 mean multiple threads). Peak RSS is the mean of "
        "per-run `rss_kib.max`.",
        "- PSS is measured from process-tree smaps_rollup, not estimated from RSS. "
        "Mean/peak use available complete-tree samples; missing samples are reported. "
        "Unmapped kernel allocations are excluded. Historical runs without PSS remain unknown.",
        f"- {COST_NOTE}",
        "- Alert latency is the linked captured-action → final collector broadcast "
        "attempt endpoint. Unlinked operations do not become zero-latency samples.",
        "", "## T8-a: completion, duration and resources", "",
        "| Profile | Conc | Status | n | Work completion | Agent E2E (s) | "
        "Condition elapsed (s) | CPU mean (%) | Peak RSS (MiB) | Mean PSS (MiB) | Peak PSS (MiB) | PSS missing samples | DB main final (MiB) | "
        "WAL peak (MiB) | DB+WAL peak (MiB) | Trace final (MiB) |",
        "|---|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for cell in cells:
        lines.append(
            f"| {cell['profile']} | {cell['concurrency']} | {_status_text(cell)} | "
            f"{_n_text(cell)} | {_stats_text(cell, 'work_completion_rate', 'fraction')} | "
            f"{_stats_text(cell, 'agent_duration_s', 'seconds')} | "
            f"{_stats_text(cell, 'condition_elapsed_s', 'seconds')} | "
            f"{_stats_text(cell, 'collector_cpu_mean_percent', 'percent')} | "
            f"{_stats_text(cell, 'collector_rss_peak_mib', 'mib')} | "
            f"{_stats_text(cell, 'collector_pss_mean_mib', 'mib')} | "
            f"{_stats_text(cell, 'collector_pss_peak_mib', 'mib')} | "
            f"{_stats_text(cell, 'collector_pss_missing_samples', 'count')} | "
            f"{_stats_text(cell, 'db_main_final_mib', 'mib')} | "
            f"{_stats_text(cell, 'db_wal_peak_mib', 'mib')} | "
            f"{_stats_text(cell, 'db_total_peak_mib', 'mib')} | "
            f"{_stats_text(cell, 'trace_final_mib', 'mib')} |")
    lines.extend([
        "", "## T8-b: graph maintenance, Observer and cost upper bound", "",
        "| Profile | Conc | Status | n | Graph updates | Graph total (ns) | "
        "Observer attempts | Observer req/fail | Observer tokens in/out | "
        "Agent cost UB (USD) | Observer cost UB (USD) | Total cost UB (USD) |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for cell in cells:
        lines.append(
            f"| {cell['profile']} | {cell['concurrency']} | {_status_text(cell)} | "
            f"{_n_text(cell)} | {_stats_text(cell, 'graph_update_count', 'count')} | "
            f"{_stats_text(cell, 'graph_update_total_ns', 'ns')} | "
            f"{_stats_text(cell, 'observer_attempts', 'count')} | "
            f"{_stats_mean(cell, 'observer_requests')}/{_stats_mean(cell, 'observer_failed_attempts')} | "
            f"{_stats_mean(cell, 'observer_input_tokens')}/{_stats_mean(cell, 'observer_output_tokens')} | "
            f"{_stats_text(cell, 'agent_cost_upper_bound_usd', 'usd')} | "
            f"{_stats_text(cell, 'observer_cost_upper_bound_usd', 'usd')} | "
            f"{_stats_text(cell, 'total_cost_upper_bound_usd', 'usd')} |")
    lines.extend([
        "", "## T8-c: controlled-operation loss and alert association", "",
        "| Profile | Conc | Status | n | Raw op missing | Persist loss | "
        "Alert linked/total | Coverage | p50 (ms) | p95 (ms) |",
        "|---|---:|---|---:|---|---:|---:|---:|---:|---:|",
    ])
    for cell in cells:
        linked = cell["alert_linked_operations"]
        total = cell["alert_total_operations"]
        linked_text = ("—" if not linked["n"] or not total["n"]
                       else f"{linked['mean']:.1f}/{total['mean']:.1f}")
        lines.append(
            f"| {cell['profile']} | {cell['concurrency']} | {_status_text(cell)} | "
            f"{_n_text(cell)} | {_stats_text(cell, 'raw_missing_fraction', 'fraction')} | "
            f"{_stats_text(cell, 'persisted_operation_missing_fraction', 'fraction')} | "
            f"{linked_text} | {_stats_text(cell, 'alert_coverage', 'fraction')} | "
            f"{_stats_text(cell, 'alert_p50_ms', 'ms')} | "
            f"{_stats_text(cell, 'alert_p95_ms', 'ms')} |")
    lines.extend(["", "## T8-d: paired C3 versus C0 task duration", "",
        "Ratios use matching repeat blocks and 100% completed fixed work. Negative values are retained. "
        "These are three descriptive repeats, without a significance claim.", "",
        "| Concurrency | Block | Mean per-agent duration increase (%) | Batch wall-span increase (%) |",
        "|---:|---|---:|---:|"])
    for pair in paired_overheads(loaded["runs"]):
        lines.append(f"| {pair['concurrency']} | {pair['block_id']} | "
            f"{_fmt_value(pair['agent_duration_increase_percent'], 'percent')} | "
            f"{_fmt_value(pair['agent_wall_span_increase_percent'], 'percent')} |")
    lines.extend(["", "## T8-e: workload resources and measured stage timing", "",
        "Workload CPU time comes from child wait accounting; RSS and CPU percentages are sampled. "
        "CPU percentages cover the entire condition including collector drain, so C0/C3 denominators differ. "
        "Graph timing covers online graph-builder updates and delta buffering; it excludes "
        "phase-2 batch correlation, pattern scoring, snapshots and persistence. "
        "Graph p50/p95 use at most the first 20,000 updates. "
        "Observer latency is per actual API attempt, distinct from final-emission latency. "
        "Cached tokens are provider input-cache usage, not application decision-cache hits.", "",
        "| Profile | Conc | Agent CPU (s) | Workload peak RSS (MiB) | Workload CPU mean (%) | Collector CPU (s) | Graph nodes/edges (final persisted) | Graph mean/p50/p95 (μs) | Observer p50/p95 (ms) | Observer cached input tokens |",
        "|---|---:|---:|---:|---:|---:|---|---|---|---:|"])
    for cell in cells:
        if not cell["planned"]:
            continue
        lines.append(f"| {cell['profile']} | {cell['concurrency']} | "
            f"{_stats_text(cell, 'agent_cpu_seconds', 'seconds')} | "
            f"{_stats_text(cell, 'workload_rss_peak_mib', 'mib')} | "
            f"{_stats_text(cell, 'workload_cpu_mean_percent', 'percent')} | "
            f"{_stats_text(cell, 'collector_cpu_seconds', 'seconds')} | "
            f"{_stats_text(cell, 'graph_nodes_final', 'count')} / {_stats_text(cell, 'graph_edges_final', 'count')} | "
            f"{_stats_text(cell, 'graph_update_mean_us', 'us')} / "
            f"{_stats_text(cell, 'graph_update_p50_us', 'us')} / "
            f"{_stats_text(cell, 'graph_update_p95_us', 'us')} | "
            f"{_stats_text(cell, 'observer_p50_ms', 'ms')} / "
            f"{_stats_text(cell, 'observer_p95_ms', 'ms')} | "
            f"{_stats_text(cell, 'observer_cache_read_tokens', 'tokens')} |")
    lines.extend(["", "## Layer diagnostics (per run)", "",
        "Source loss totals are separate from operation missing fractions and semantic filtering. "
        "Full reason counters, source completeness and semantic counters are in raw-per-run.json. "
        "Absent terminal counters remain unknown; source totals are never added into an event-loss rate.", "",
        "| Condition | Capture status | Source lost counters | Persistence dropped / write failures / closed channel |",
        "|---|---|---|---|"])
    for run in loaded["runs"]:
        if run["profile"] == "C0":
            continue
        layer = run["layer_counters"]
        sources = layer["sources"]
        source_text = (", ".join(f"{name.split(':')[0]}={source.get('lost', 'unknown')}"
            for name, source in sorted(sources.items())) if sources else "unknown")
        persistence = layer["persistence"]
        writer_text = (" / ".join(str(persistence[k]) for k in
            ("dropped", "write_failures", "closed_channel")) if persistence else
            "not applicable" if run["profile"] == "C1" else "unknown")
        lines.append(f"| {run['condition_id']} | {layer['capture_status'] or 'unknown'} | {source_text} | {writer_text} |")
    lines.extend(["", "## Probe supplement", ""])
    if not probe_data:
        lines.append("Probe supplement: pending (no --probes file supplied).")
    else:
        for probe_path, data in probe_data:
            lines.extend(_probe_lines(probe_path, data))
    lines.extend(["", "## Diagnostics", ""])
    if not diagnostics:
        lines.append("Diagnostics: pending (no --diagnostics file supplied).")
    else:
        for diagnostic_path, data in diagnostics:
            lines.extend(_diagnostic_lines(diagnostic_path, data))
    lines.extend([
        "", "## F2 caption", "",
        "F2 plots only the complete system (C3); C0 end-to-end duration remains in "
        "T8-a and is not a second figure. Individual repeats are open markers "
        "(o/s/^ = repeats 1/2/3), filled diamonds and lines are means, and vertical "
        "bars are min–max. No CI and no p99 are inferred from n=3. Empty latency "
        "points mean there was no linked final-emission alert; missing is not zero.",
        f"Alert correlation coverage (linked/total): {_coverage_text(loaded['runs'])}.",
        "F2 files: `F2.pdf`, `F2.svg`, `F2.png` (300 dpi). Raw per-run source: "
        "`raw-per-run.json`, `raw-per-run.csv`.",
    ])
    if loaded["warnings"]:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in loaded["warnings"])
    Path(path).write_text("\n".join(lines) + "\n")


def _probe_lines(path, data):
    status = data.get("status")
    if status != "ok":
        return [f"### `{path.name}`", "", f"TLS probe measurement status: {status!r}; "
                f"no values reported. {data.get('error', '')}"]
    lines = [f"### `{path.name}`", "",
             f"TLS probe measurement status: ok; repeats requested: "
             f"{data.get('repeats_requested', '—')}."]
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    total = summary.get("ns_per_successful_ssl_operation")
    if isinstance(total, dict) and total.get("mean") is not None:
        lines.append(
            "Per successful SSL operation (sum of measured TLS program time / "
            f"successful read+write operations): {total['mean']:.3f} ns/op "
            f"[{total.get('min', 0):.3f}–{total.get('max', 0):.3f}], "
            f"n={total.get('n', 0)}.")
    else:
        lines.append("Per successful SSL operation: — (no covered TLS programs).")
    programs = {}
    for repeat in data.get("repeats", []):
        if not isinstance(repeat, dict):
            continue
        for row in repeat.get("programs", []):
            if not isinstance(row, dict):
                continue
            key = (row.get("id"), str(row.get("name", "")))
            entry = programs.setdefault(key, {"ns": [], "covered": 0, "runs": 0})
            entry["runs"] += 1
            if row.get("covered") is True:
                entry["covered"] += 1
                value = _number(row.get("ns_per_call"))
                if value is not None:
                    entry["ns"].append(value)
    lines.extend(["", "| Program ID | Name | Covered repeats | ns/call (mean [min–max]) |",
                  "|---:|---|---:|---:|"])
    for (program_id, name), entry in sorted(programs.items(),
                                            key=lambda item: str(item[0])):
        if entry["ns"]:
            stats = _stats(entry["ns"])
            text = (f"{stats['mean']:.3f} [{stats['min']:.3f}–{stats['max']:.3f}]")
        else:
            text = "— (uncovered)"
        lines.append(f"| {program_id} | {name} | {entry['covered']}/{entry['runs']} | {text} |")
    if not programs:
        lines.append("| — | no programs reported | 0/0 | — |")
    lines.extend(["", PROBE_NOTE, ""])
    return lines


def _diagnostic_lines(path, data):
    status = data.get("status")
    if status != "ok":
        return [f"### `{path.name}`", "", f"Diagnostics status: {status!r}; "
                f"no values reported. {data.get('error', '')}"]
    lines = [f"### `{path.name}`", "",
             f"Diagnostics status: ok; mode: `{data.get('mode', '—')}`; "
             f"window: {data.get('window_seconds', '—')} s.", "",
             "| Program ID | Name | Run count delta | Run time (ns) | ns/call | Covered |",
             "|---:|---|---:|---:|---:|---|"]
    for row in data.get("programs", []):
        if not isinstance(row, dict):
            continue
        value = _number(row.get("ns_per_call"))
        text = "— (uncovered)" if value is None else f"{value:.3f}"
        lines.append(
            f"| {row.get('id', '—')} | {row.get('name', '')} | "
            f"{row.get('run_cnt_delta', '—')} | {row.get('run_time_ns_delta', '—')} | "
            f"{text} | {row.get('covered')} |")
    if not data.get("programs"):
        lines.append("| — | no programs reported | — | — | — | — |")
    lines.extend(["", DIAGNOSTIC_NOTE, ""])
    return lines


def _import_pyplot():
    try:
        import matplotlib
        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise ReportError(
            "matplotlib is required for F2; use the existing Python environment "
            "with matplotlib installed (no new package is installed here)") from error
    return plt


def _plot_series(ax, runs, key, *, color, label, markers, linestyle="-",
                 transform=lambda value: value):
    values_by_level = {level: [] for level in LEVELS}
    for run in runs:
        if (not run["included"] or run["profile"] != "C3"
                or run["concurrency"] not in values_by_level):
            continue
        value = run.get(key)
        if value is None:
            continue
        values_by_level[run["concurrency"]].append((run["repeat"], transform(value)))
    xs, means, mins, maxs = [], [], [], []
    for level in LEVELS:
        values = [value for _, value in values_by_level[level]]
        xs.append(level)
        if not values:
            means.append(None)
            mins.append(None)
            maxs.append(None)
            continue
        means.append(sum(values) / len(values))
        mins.append(min(values))
        maxs.append(max(values))
    ax.plot(xs, means, color=color, marker="D", markersize=5, linewidth=1.4,
            linestyle=linestyle, label=label, zorder=4)
    for level, values in values_by_level.items():
        if values:
            ax.vlines(level, min(value for _, value in values),
                      max(value for _, value in values), color=color, alpha=.55,
                      linewidth=1.2, zorder=2)
    for level, values in values_by_level.items():
        for repeat, value in values:
            ax.scatter([level], [value], s=30, facecolors="none", edgecolors=color,
                       marker=markers[(repeat - 1) % len(markers)], alpha=.85,
                       linewidths=1.0, zorder=3)


def _write_figure(out, runs, status):
    plt = _import_pyplot()
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9,
                         "axes.titlesize": 10, "axes.labelsize": 9,
                         "legend.fontsize": 8})
    colors = {"raw": "#0072B2", "persisted": "#D55E00", "latency": "#009E73",
              "rss": "#CC79A7", "cpu": "#E69F00"}
    markers = ("o", "s", "^")
    fig, axes = plt.subplots(2, 2, figsize=(12.5, 9.6))
    fig.subplots_adjust(left=.075, right=.985, top=.88, bottom=.17,
                        hspace=.42, wspace=.24)
    title = "F2 — Complete-system overhead and capture quality (C3)"
    fig.text(.5, .965, title, ha="center", va="top", fontsize=14, fontweight="bold")
    label = ("FINAL — frozen 36-condition cohort" if status == "final_complete"
             else "PARTIAL — NOT FINAL")
    fig.text(.5, .928, label, ha="center", va="top", fontsize=10,
             color=("#1b5e20" if status == "final_complete" else "#b00020"),
             fontweight="bold")

    ax = axes[0][0]
    _plot_series(ax, runs, "raw_missing_fraction", color=colors["raw"],
                 label="raw missing", markers=markers)
    _plot_series(ax, runs, "persisted_operation_missing_fraction",
                 color=colors["persisted"], label="persisted loss", markers=markers,
                 linestyle="--")
    ax.set_title("(a) Controlled-operation missing fraction")
    ax.set_ylabel("Missing fraction")
    ax.set_ylim(-.02, 1.05)
    ax.legend(loc="best", frameon=False)
    for level in LEVELS:
        group = [r for r in runs if r["included"] and r["profile"] == "C3" and r["concurrency"] == level]
        if group and all(r.get("raw_missing_fraction") == 0 and
                         r.get("persisted_operation_missing_fraction") == 0 and
                         r.get("controlled_operations_observed") is not None for r in group):
            denominator = sum(r["controlled_operations_observed"] for r in group)
            ax.annotate(f"0/{denominator}", (level, 0), xytext=(0, 10),
                        textcoords="offset points", ha="center", fontsize=8)

    ax = axes[0][1]
    _plot_series(ax, runs, "alert_p95_ms", color=colors["latency"],
                 label="linked alert p95", markers=markers, transform=lambda ms: ms / 1000)
    ax.set_title("(b) Linked final-emission latency p95")
    ax.set_ylabel("Latency (s)")
    ax.set_ylim(bottom=0)
    if not any(run["included"] and run["profile"] == "C3"
               and run.get("alert_p95_ms") is not None for run in runs):
        ax.text(.03, .9, "no linked final-emission alerts", transform=ax.transAxes,
                fontsize=8, color="#666666", va="top")

    ax = axes[1][0]
    _plot_series(ax, runs, "collector_rss_peak_mib", color=colors["rss"],
                 label="collector peak RSS", markers=markers)
    ax.set_title("(c) Collector peak RSS")
    ax.set_ylabel("Peak RSS (MiB)")
    ax.set_ylim(bottom=0)

    ax = axes[1][1]
    _plot_series(ax, runs, "collector_cpu_mean_percent", color=colors["cpu"],
                 label="collector CPU mean", markers=markers)
    ax.set_title("(d) Collector CPU mean")
    ax.set_ylabel("CPU mean (% of one core)")
    ax.set_ylim(bottom=0)

    for row in axes:
        for ax in row:
            ax.set_xscale("log", base=2)
            ax.set_xticks(LEVELS)
            ax.set_xticklabels([str(level) for level in LEVELS])
            ax.minorticks_off()
            ax.set_xlim(.85, 38)
            ax.set_xlabel("Concurrency (agent instances)")
            ax.grid(True, which="major", color="#dddddd", linewidth=.6)
            ax.set_axisbelow(True)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)

    caption = (
        "Individual repeats are open markers (o/s/^ = repeats 1/2/3); filled "
        "diamonds and lines are means; vertical bars are min–max. No CI and no p99 "
        "are inferred from n=3. Only complete-system C3 is plotted; C0 duration is "
        "in T8. Source ring-buffer drops are reported separately in T8; controlled-operation "
        "misses do not measure global event loss. Alert coverage (linked/total): " + _coverage_text(runs) +
        ". Empty latency points mean no linked final-emission alert; missing is not zero.")
    fig.text(.5, .015, textwrap.fill(caption, 145), ha="center", va="bottom", fontsize=8,
             color="#333333")
    for suffix, options in (("pdf", {}), ("svg", {}), ("png", {"dpi": 300})):
        fig.savefig(Path(out) / f"F2.{suffix}", bbox_inches="tight", **options)
    plt.close(fig)


def _load_probe_files(paths):
    loaded = []
    for path in paths:
        data = _load_json(path)
        if data.get("schema") != PROBE_MEASUREMENT_SCHEMA:
            raise ReportError(f"unexpected probe measurement schema in {path}")
        loaded.append((Path(path), data))
    return loaded


def _load_diagnostic_files(paths):
    loaded = []
    for path in paths:
        data = _load_json(path)
        if data.get("schema") != PROBE_DIAGNOSTIC_SCHEMA:
            raise ReportError(f"unexpected probe diagnostic schema in {path}")
        loaded.append((Path(path), data))
    return loaded


def load_study(study, *, allow_partial=False):
    paths = resolve_study(study, allow_partial=allow_partial)
    plan = validate_plan(_load_json(paths["plan_path"]))
    if not paths["execution_path"].is_file():
        raise ReportError(f"execution report is missing: {paths['execution_path']}")
    execution = _load_json(paths["execution_path"])
    if not str(execution.get("schema", "")).startswith("mtagentlens.overhead-execution/"):
        raise ReportError("execution report has an unexpected schema")
    if execution.get("plan_sha256") != plan["plan_sha256"]:
        raise ReportError("execution report plan_sha256 does not match the frozen plan")
    warnings = []
    if execution.get("phase") not in (None, paths["phase"]):
        message = (f"execution report phase {execution.get('phase')!r} does not "
                   f"match the {paths['phase']!r} study path")
        if not allow_partial:
            raise ReportError(message + "; refusing formal conclusion")
        warnings.append(message)
    if not allow_partial:
        if execution.get("complete") is not True:
            raise ReportError("execution report is not complete; refusing formal conclusion")
        if execution.get("conditions_completed") != PLANNED_CONDITIONS:
            raise ReportError(
                f"execution report completed {execution.get('conditions_completed')} "
                f"of {PLANNED_CONDITIONS} conditions; refusing formal conclusion")
        if execution.get("conditions_expected") not in (None, PLANNED_CONDITIONS):
            raise ReportError("execution report condition count is inconsistent")
    runs, run_warnings = _load_runs(paths, plan, allow_partial=allow_partial)
    warnings.extend(run_warnings)
    return {**paths, "plan": plan, "execution": execution,
            "runs": runs, "warnings": warnings}


def _write_summary(path, cells, status, loaded, probe_data, diagnostic_data):
    """Machine-readable cell means for claim registration and downstream checks."""
    document = {
        "schema": "mtagentlens.overhead-summary/1",
        "status": status, "phase": loaded["phase"],
        "plan_sha256": loaded["plan"]["plan_sha256"],
        "condition_runs": len(loaded["runs"]),
        "cells": {f"{cell['profile']}/{cell['concurrency']}": cell for cell in cells},
        "probe": [{"path": str(source), "status": data.get("status"),
                   "summary": data.get("summary")} for source, data in probe_data],
        "diagnostics": [{"path": str(source), "status": data.get("status"),
                         "programs": len(data.get("programs", []))}
                        for source, data in diagnostic_data],
    }
    Path(path).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")


def generate_report(study, out, *, probes=None, diagnostics=None,
                    allow_partial=False):
    """Validate inputs, then write T8, raw exports and F2."""
    loaded = load_study(study, allow_partial=allow_partial)
    probe_data = _load_probe_files(_as_paths(probes))
    diagnostic_data = _load_diagnostic_files(_as_paths(diagnostics))
    _import_pyplot()  # fail before writing partial outputs if the plot stack is absent
    status = "final_complete" if not allow_partial else "partial_not_final"
    cells = _build_cells(loaded["runs"])
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "t8_csv": out / "T8.csv", "t8_md": out / "T8.md",
        "raw_json": out / "raw-per-run.json", "raw_csv": out / "raw-per-run.csv",
        "f2_pdf": out / "F2.pdf", "f2_svg": out / "F2.svg", "f2_png": out / "F2.png",
        "summary_json": out / "summary.json",
    }
    _write_t8_csv(files["t8_csv"], cells, status, loaded["phase"])
    _write_raw(files["raw_json"], files["raw_csv"], loaded, loaded["runs"], status)
    _write_t8_md(files["t8_md"], loaded, cells, status, probe_data, diagnostic_data)
    _write_summary(files["summary_json"], cells, status, loaded, probe_data, diagnostic_data)
    _write_figure(out, loaded["runs"], status)
    return {"schema": REPORT_SCHEMA, "status": status, "phase": loaded["phase"],
            "plan_sha256": loaded["plan"]["plan_sha256"],
            "condition_runs": len(loaded["runs"]),
            "planned_condition_runs": PLANNED_CONDITIONS,
            "files": {key: str(path) for key, path in files.items()},
            "warnings": loaded["warnings"]}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True,
                        help="study root with formal-plan.json and formal/")
    parser.add_argument("--out", type=Path, required=True,
                        help="output directory for T8/F2/raw exports")
    parser.add_argument("--probes", type=Path, action="append",
                        help="optional TLS probe measurement JSON; repeatable")
    parser.add_argument("--diagnostics", type=Path, action="append",
                        help="optional existing-program diagnostic JSON; repeatable")
    parser.add_argument("--allow-partial", action="store_true",
                        help="export clearly labelled PARTIAL / NOT FINAL diagnostics")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = generate_report(
            args.study, args.out, probes=args.probes, diagnostics=args.diagnostics,
            allow_partial=args.allow_partial)
    except (ReportError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
