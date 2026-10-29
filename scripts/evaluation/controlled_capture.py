# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Controlled file-open capture adapter (``controlled_file_open/v1``).

The denominator is the set of unique, successfully completed controlled
workload operations. Each operation receipt carries an exact absolute path,
process instance ``(pid, process_start_time_ns)``, wall-clock start/complete
times, and optionally an observed monotonic range. A receipt is matched only
against:

* a raw process ``FILE_OPEN`` trace event with the same ``filepath``, ``pid``,
  ``start_time_ns`` and ``outcome == "succeeded"``; the event's coalesced
  ``count`` is never used to infer extra operations; and
* a persisted ``actions`` row with ``action_type == "FileOpen"``,
  ``outcome == "succeeded"``, the same process instance, and an exact parsed
  ``{"Path": <absolute path>}`` in ``normalized_args_json``.

One observation may satisfy at most one receipt. Missing raw and persisted
observations are reported separately. This is controlled-operation visibility,
not global syscall/ring-buffer loss. Missing artifacts, unproven capture health,
incomplete/duplicate receipts or an unsupported schema make the run NOT
ESTIMABLE rather than zero loss. A positive fraction over a valid complete
denominator remains a measured result.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import sqlite3
import stat
import sys
import tempfile
import time
import uuid
from pathlib import Path

SCHEMA = "mtagentlens.controlled-capture/v1"
ADAPTER_VERSION = "controlled_file_open/v1"
EMPIRICAL_VALIDATION = "pending_real_recording"
PROFILES = ("C0", "C1", "C2", "C3")
PERSISTED_PROFILES = ("C2", "C3")
PID_NAMESPACE_ASSUMPTION = "same_host_single_pid_namespace"
IDENTITY_ASSUMPTION = "not_authenticated"
SCOPE = "controlled_file_open_operations_only"
SCIENTIFIC_SCOPE = "not_global_syscall_or_ringbuf_loss"
LIMITATIONS = [
    "fractions cover only successfully completed controlled file-open operations",
    "raw transport and persisted-operation missing fractions are separate",
    "ring-buffer diagnostics are reported separately and are not the denominator",
    "coalesced raw count>1 is never expanded without an exact operation anchor",
    "empirical_validation stays pending_real_recording until a real recording is supplied",
]


def _sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_count(value):
    return type(value) is int and value >= 0


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def load_receipts(paths):
    """Read operation receipts; only an explicit header record is skipped."""
    records = []
    reasons = []
    for path in paths:
        try:
            lines = Path(path).read_text(errors="replace").splitlines()
        except OSError:
            reasons.append(f"missing_receipt_file: {path}")
            continue
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                reasons.append(f"invalid_receipt_json: {path}:{line_number}")
                continue
            if not isinstance(value, dict):
                reasons.append(f"unknown_record: {path}:{line_number}")
                continue
            record_type = value.get("record_type")
            if record_type == "header":
                continue
            if record_type is not None:
                reasons.append(f"unknown_record: {record_type!r} in {path}:{line_number}")
                continue
            records.append(value)
    return records, reasons


def _validate_receipts(receipts, expected):
    reasons = []
    if type(expected) is not int or expected < 1:
        raise ValueError("expected_receipts must be a positive integer")
    if not isinstance(receipts, list):
        raise ValueError("receipts must be a list")
    if len(receipts) != expected:
        reasons.append(
            f"incomplete_receipts: {len(receipts)}/{expected} controlled operations")
    rows = []
    seen_ids = set()
    seen_anchors = set()
    birth_ranges = {}
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, dict):
            reasons.append(f"invalid_receipt: receipt {index} is not an object")
            continue
        receipt_id = receipt.get("receipt_id")
        if not isinstance(receipt_id, str) or not receipt_id:
            reasons.append(f"invalid_receipt: receipt {index} has no receipt_id")
            continue
        if receipt_id in seen_ids:
            reasons.append(f"duplicate_receipt_id: {receipt_id}")
            continue
        seen_ids.add(receipt_id)
        path = receipt.get("path")
        pid = receipt.get("pid")
        start_ns = receipt.get("process_start_time_ns")
        started_ns = receipt.get("started_ns")
        completed_ns = receipt.get("completed_ns")
        invalid = False
        if not isinstance(path, str) or not path or not Path(path).is_absolute():
            reasons.append(f"invalid_receipt: {receipt_id} path must be absolute")
            invalid = True
        if type(pid) is not int or pid <= 0:
            reasons.append(f"invalid_receipt: {receipt_id} pid must be a positive integer")
            invalid = True
        if type(start_ns) is not int or start_ns <= 0:
            reasons.append(
                f"invalid_receipt: {receipt_id} process_start_time_ns must be positive")
            invalid = True
        if not _is_count(started_ns) or not _is_count(completed_ns):
            reasons.append(f"invalid_receipt: {receipt_id} needs wall-clock started/completed_ns")
            invalid = True
        elif completed_ns < started_ns:
            reasons.append(f"invalid_receipt: {receipt_id} completed before started")
            invalid = True
        resolution = receipt.get("process_start_resolution_ns", 1)
        if type(resolution) is not int or resolution < 1:
            reasons.append(
                f"invalid_receipt: {receipt_id} process_start_resolution_ns must be positive")
            invalid = True
        pid_namespace = receipt.get(
            "pid_namespace_assumption", PID_NAMESPACE_ASSUMPTION)
        if pid_namespace != PID_NAMESPACE_ASSUMPTION:
            reasons.append(
                f"invalid_receipt: {receipt_id} unsupported pid_namespace_assumption")
            invalid = True
        mono_started = receipt.get("monotonic_started_ns")
        mono_completed = receipt.get("monotonic_completed_ns")
        if (mono_started is None) != (mono_completed is None):
            reasons.append(f"invalid_receipt: {receipt_id} has an incomplete monotonic range")
            invalid = True
        elif mono_started is not None:
            if not _is_count(mono_started) or not _is_count(mono_completed):
                reasons.append(
                    f"invalid_receipt: {receipt_id} monotonic range must be non-negative integers")
                invalid = True
            elif mono_completed < mono_started:
                reasons.append(f"invalid_receipt: {receipt_id} monotonic range is inverted")
                invalid = True
        if invalid:
            continue
        anchor = (path, pid, start_ns, resolution)
        if anchor in seen_anchors:
            reasons.append(
                f"duplicate_occurrence_anchor: {receipt_id} reuses {path} pid={pid} start={start_ns}")
            continue
        if any(start_ns < prior_start + prior_resolution
               and prior_start < start_ns + resolution
               for prior_start, prior_resolution in birth_ranges.get((path, pid), ())):
            reasons.append(
                f"overlapping_occurrence_anchor: {receipt_id} reuses {path} pid={pid} "
                f"within another birth range")
            continue
        seen_anchors.add(anchor)
        birth_ranges.setdefault((path, pid), []).append((start_ns, resolution))
        rows.append({"receipt_id": receipt_id, "path": path, "pid": pid,
                     "process_start_time_ns": start_ns,
                     "process_start_resolution_ns": resolution,
                     "started_ns": started_ns, "completed_ns": completed_ns,
                     "monotonic_started_ns": mono_started,
                     "monotonic_completed_ns": mono_completed,
                     "pid_namespace_assumption": pid_namespace})
    return rows, reasons


def _read_trace(trace_path):
    observations = []
    malformed = 0
    try:
        lines = Path(trace_path).read_text(errors="replace").splitlines()
    except OSError:
        return None, 0
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        if not isinstance(event, dict):
            malformed += 1
            continue
        data = event.get("data")
        if not isinstance(data, dict) or data.get("event") != "FILE_OPEN":
            continue
        observations.append({
            "source": event.get("source"),
            "filepath": data.get("filepath"),
            "pid": data.get("pid"),
            "start_time_ns": data.get("start_time_ns"),
            "outcome": data.get("outcome"),
            "count": data.get("count"),
            "timestamp": data.get("timestamp"),
        })
    return observations, malformed


def _read_actions(database_path):
    # The recorded path must itself be the regular file that is read. Resolving
    # first would follow a final-component symlink and silently admit a
    # different database (another run's, or a stale/empty one) as this run's
    # artifact; the metrics re-hash would follow the same link.
    entry = Path(database_path)
    try:
        mode = entry.lstat().st_mode
    except OSError:
        return None, ["database_missing"]
    if not stat.S_ISREG(mode):
        return None, ["database_not_regular_file"]
    path = entry.resolve()
    wal = path.with_name(path.name + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        return None, ["database_wal_present"]
    # An empty -wal is a fully checkpointed database; -shm is process-shared
    # memory that carries no data after exit. Rejecting either would mark every
    # cleanly closed real recording as unfinalized.
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version != 5:
                return None, [f"user_version={version} is not schema5"]
            columns = {row[1] for row in conn.execute("PRAGMA table_info(actions)")}
            required = {
                "action_id", "pid", "process_start_time_ns", "action_type",
                "normalized_args_json", "outcome",
            }
            if not required <= columns:
                return None, sorted(required - columns)
            rows = list(conn.execute(
                "SELECT action_id, pid, process_start_time_ns, action_type, "
                "normalized_args_json, outcome FROM actions "
                "WHERE action_type = 'FileOpen' AND outcome = 'succeeded' "
                "ORDER BY action_id"))
    except sqlite3.Error as error:
        return None, [str(error)]
    observations = []
    for action_id, pid, start_ns, action_type, args_json, outcome in rows:
        try:
            args = json.loads(args_json or "[]")
        except (TypeError, ValueError):
            continue
        paths = [arg.get("Path") for arg in args
                 if isinstance(arg, dict) and isinstance(arg.get("Path"), str)]
        observations.append({
            "action_id": str(action_id), "pid": pid,
            "start_time_ns": start_ns, "action_type": action_type,
            "outcome": outcome, "paths": paths,
        })
    return observations, []


def _start_in_birth_interval(receipt, observed_start):
    if type(observed_start) is not int:
        return False
    start = receipt["process_start_time_ns"]
    return start <= observed_start < start + receipt["process_start_resolution_ns"]


def _match_raw(receipts, observations):
    used = set()
    matched = {}
    for receipt in receipts:
        for index, observation in enumerate(observations):
            if index in used:
                continue
            if not (observation["source"] == "process"
                    and observation["filepath"] == receipt["path"]
                    and observation["pid"] == receipt["pid"]
                    and _start_in_birth_interval(
                        receipt, observation["start_time_ns"])
                    and observation["outcome"] == "succeeded"):
                continue
            if receipt["monotonic_started_ns"] is not None:
                timestamp = observation["timestamp"]
                if (type(timestamp) is not int
                        or not receipt["monotonic_started_ns"] <= timestamp
                        <= receipt["monotonic_completed_ns"]):
                    continue
            used.add(index)
            matched[receipt["receipt_id"]] = observation
            break
    return matched


def _match_persisted(receipts, observations):
    used = set()
    matched = {}
    for receipt in receipts:
        for index, observation in enumerate(observations):
            if index in used:
                continue
            if (observation["action_type"] == "FileOpen"
                    and observation["outcome"] == "succeeded"
                    and observation["pid"] == receipt["pid"]
                    and _start_in_birth_interval(
                        receipt, observation["start_time_ns"])
                    and receipt["path"] in observation["paths"]):
                used.add(index)
                matched[receipt["receipt_id"]] = observation
                break
    return matched


def _artifacts(receipt_paths, trace_path, database_path):
    receipt_files = []
    combined = hashlib.sha256()
    for path in receipt_paths:
        path = Path(path)
        try:
            payload = path.read_bytes()
        except OSError:
            receipt_files.append({"path": str(path), "sha256": None})
            continue
        combined.update(payload)
        receipt_files.append({"path": str(path), "sha256": _sha256_bytes(payload)})
    trace_file = None
    if trace_path is not None:
        trace_file = {"path": str(trace_path), "sha256": None}
        try:
            trace_file["sha256"] = _sha256_file(trace_path)
        except OSError:
            pass
    database = None
    if database_path is not None:
        database = {"path": str(database_path), "sha256": None}
        try:
            database["sha256"] = _sha256_file(database_path)
        except OSError:
            pass
    return {
        "receipt_files": receipt_files,
        "receipt_combined_sha256": combined.hexdigest(),
        "trace_file": trace_file,
        "database": database,
    }


def build_controlled_capture(
    *,
    profile,
    condition_id,
    receipts,
    receipt_paths,
    expected_receipts,
    trace_path,
    database_path=None,
    health=None,
    ringbuf_diagnostics=None,
) -> dict:
    """Bind receipts/trace/DB and return per-run controlled capture evidence."""
    if profile not in PROFILES:
        raise ValueError(f"unsupported profile {profile!r}")
    if not isinstance(condition_id, str) or not condition_id:
        raise ValueError("condition_id is required")
    result = {
        "schema": SCHEMA,
        "adapter_version": ADAPTER_VERSION,
        "condition_id": condition_id,
        "profile": profile,
        "scope": SCOPE,
        "scientific_scope": SCIENTIFIC_SCOPE,
        "empirical_validation": EMPIRICAL_VALIDATION,
        "pid_namespace_assumption": PID_NAMESPACE_ASSUMPTION,
        "identity_assumption": IDENTITY_ASSUMPTION,
        "status": "not_estimable",
        "estimable": False,
        "reasons": [],
        "denominator": {
            "kind": "unique_completed_controlled_operations",
            "expected": expected_receipts,
            "observed": len(receipts) if isinstance(receipts, list) else None,
            "complete": False,
        },
        "artifact_binding": _artifacts(receipt_paths, trace_path, database_path),
        "raw_transport_missing_fraction": None,
        "persisted_operation_missing_fraction": None,
        "raw_transport_missing_count": None,
        "persisted_operation_missing_count": None,
        "coverage": None,
        "matches": {"raw": {}, "persisted": {}},
        "unmatched_raw_receipt_ids": [],
        "unmatched_persisted_receipt_ids": [],
        "ringbuf_diagnostics": list(ringbuf_diagnostics or []),
        "health": dict(health or {}),
        "limitations": list(LIMITATIONS),
    }
    if profile == "C0":
        result.update(status="not_applicable", reasons=["C0 has no collector capture"])
        return result

    file_receipts, file_reasons = load_receipts(receipt_paths)
    result["reasons"].extend(file_reasons)
    if _canonical(file_receipts) != _canonical(receipts):
        result["reasons"].append("receipts_argument_mismatch")
    observed_receipts, reasons = _validate_receipts(receipts, expected_receipts)
    result["denominator"]["complete"] = len(observed_receipts) == expected_receipts
    result["denominator"]["observed"] = len(observed_receipts)
    result["reasons"].extend(reasons)

    if trace_path is None or not Path(trace_path).is_file():
        result["reasons"].append("missing_raw_trace")
        trace_observations = None
    else:
        trace_observations, malformed = _read_trace(trace_path)
        result["raw_trace_malformed_lines"] = malformed
    if result["artifact_binding"]["trace_file"] is not None:
        if result["artifact_binding"]["trace_file"]["sha256"] is None:
            result["reasons"].append("missing_raw_trace_bytes")

    if not isinstance(health, dict) or health.get("attachment_proven") is not True:
        result["reasons"].append("capture_health_unproven")
    if profile in PERSISTED_PROFILES:
        if health is None or health.get("final_flush_observed") is not True:
            result["reasons"].append("persisted_flush_unproven")
        if database_path is None or not Path(database_path).is_file():
            result["reasons"].append("missing_database")
            actions = None
        else:
            actions, schema_problems = _read_actions(database_path)
            if schema_problems:
                result["reasons"].append(
                    "unsupported_schema: " + ", ".join(schema_problems))
            if result["artifact_binding"]["database"] is not None and (
                    result["artifact_binding"]["database"]["sha256"] is None):
                result["reasons"].append("missing_database_bytes")
    else:
        actions = None

    if any(file["sha256"] is None for file in result["artifact_binding"]["receipt_files"]):
        result["reasons"].append("missing_receipt_file")
    if result["reasons"]:
        return result

    raw_matched = _match_raw(observed_receipts, trace_observations or [])
    persisted_matched = (
        _match_persisted(observed_receipts, actions) if profile in PERSISTED_PROFILES else {})
    expected = expected_receipts
    raw_missing = expected - len(raw_matched)
    result["matches"]["raw"] = {
        receipt_id: {key: value for key, value in observation.items()
                     if key in ("filepath", "pid", "start_time_ns", "outcome",
                                "count", "timestamp")}
        for receipt_id, observation in raw_matched.items()}
    result["unmatched_raw_receipt_ids"] = sorted(
        receipt["receipt_id"] for receipt in observed_receipts
        if receipt["receipt_id"] not in raw_matched)
    result["raw_transport_missing_count"] = raw_missing
    result["raw_transport_missing_fraction"] = raw_missing / expected
    coverage = {"receipts": len(observed_receipts) / expected,
                "raw_transport": len(raw_matched) / expected}
    if profile in PERSISTED_PROFILES:
        persisted_missing = expected - len(persisted_matched)
        result["matches"]["persisted"] = {
            receipt_id: observation["action_id"]
            for receipt_id, observation in persisted_matched.items()}
        result["unmatched_persisted_receipt_ids"] = sorted(
            receipt["receipt_id"] for receipt in observed_receipts
            if receipt["receipt_id"] not in persisted_matched)
        result["persisted_operation_missing_count"] = persisted_missing
        result["persisted_operation_missing_fraction"] = persisted_missing / expected
        coverage["persisted_operation"] = len(persisted_matched) / expected
    else:
        coverage["persisted_operation"] = None
    result["coverage"] = coverage
    result["status"] = "measured"
    result["estimable"] = True
    return result


def _set_process_comm(comm):
    if not isinstance(comm, str) or not comm or len(comm.encode()) > 15:
        raise ValueError("comm must be a non-empty string of at most 15 bytes")
    if ctypes.CDLL(None).prctl(15, comm.encode(), 0, 0, 0) != 0:
        raise RuntimeError("cannot set isolated process comm")


def _process_start_ticks(pid):
    raw = Path(f"/proc/{pid}/stat").read_text()
    close = raw.rfind(")")
    if close < 0:
        raise ValueError("cannot parse /proc/self/stat")
    fields = raw[close + 2:].split()
    if len(fields) < 20:
        raise ValueError("cannot parse /proc/self/stat starttime")
    return int(fields[19])


def monotonic_birth_interval(ticks, tick_ns, *, monotonic_before, boottime,
                             monotonic_after):
    """Convert /proc's suspend-inclusive clock to BPF's monotonic clock.

    Bracket the clock sample rather than guessing an exact process birth;
    /proc's tick truncation and the clock-read interval remain uncertainty.
    """
    if monotonic_after < monotonic_before:
        raise ValueError('inverted process birth clock witness')
    offset_upper = boottime-monotonic_before
    return ticks*tick_ns-offset_upper, tick_ns+monotonic_after-monotonic_before


def run_workload(operations, comm, cwd=None):
    """Emit exact controlled-operation receipts; the caller owns a private cwd."""
    if type(operations) is not int or operations < 1:
        raise ValueError("operations must be a positive integer")
    _set_process_comm(comm)
    directory = Path(tempfile.mkdtemp(
        prefix=f"controlled-{os.getpid()}-", dir=Path(cwd or Path.cwd())))
    directory.chmod(0o700)
    pid = os.getpid()
    tick_ns = math.ceil(1_000_000_000 / os.sysconf("SC_CLK_TCK"))
    mono_before = time.monotonic_ns()
    boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    mono_after = time.monotonic_ns()
    start_ns, birth_resolution_ns = monotonic_birth_interval(
        _process_start_ticks(pid),tick_ns,monotonic_before=mono_before,
        boottime=boot,monotonic_after=mono_after)
    receipts = []
    for index in range(operations):
        path = directory / f"op-{index}-{uuid.uuid4().hex}"
        monotonic_started = time.monotonic_ns()
        started = time.time_ns()
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_TRUNC, 0o600)
        try:
            payload = b"controlled file-open operation\n"
            if os.write(descriptor, payload) != len(payload):
                raise RuntimeError("short controlled workload write")
        finally:
            os.close(descriptor)
        completed = time.time_ns()
        monotonic_completed = time.monotonic_ns()
        receipt = {
            "receipt_id": f"{pid}-{uuid.uuid4().hex}",
            "path": str(path.resolve()),
            "pid": pid,
            "process_start_time_ns": start_ns,
            "process_start_resolution_ns": birth_resolution_ns,
            "process_start_clock_witness": {"monotonic_before_ns":mono_before,
                "boottime_ns":boot,"monotonic_after_ns":mono_after,
                "source":"proc_boottime_ticks_to_bpf_monotonic"},
            "started_ns": started,
            "completed_ns": completed,
            "monotonic_started_ns": monotonic_started,
            "monotonic_completed_ns": monotonic_completed,
            "pid_namespace_assumption": PID_NAMESPACE_ASSUMPTION,
            "identity_assumption": IDENTITY_ASSUMPTION,
        }
        print(json.dumps(receipt, sort_keys=True), flush=True)
        receipts.append(receipt)
    return receipts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    workload = subparsers.add_parser(
        "workload", help="perform controlled file opens and emit JSONL receipts")
    workload.add_argument("--operations", type=int, required=True)
    workload.add_argument("--comm", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "workload":
            run_workload(args.operations, args.comm)
            return 0
        raise ValueError(f"unsupported command {args.command!r}")
    except (OSError, ValueError, RuntimeError) as error:
        print(f"controlled capture workload: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
