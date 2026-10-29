# SPDX-License-Identifier: Apache-2.0 OR MIT
"""TLS probe microbenchmark plan/measurement and bpftool counter diagnostics.

This is a narrow companion to the overhead driver, not a general benchmark
framework. It reuses the legacy ``bpftool prog show --json`` cumulative
``run_cnt`` / ``run_time_ns`` method without importing the legacy script.

Plan-only is the default. With ``--execute`` the TLS mode starts the collector
in raw SSL mode (no runtime), waits for the collector's real
``TLS_CAPTURE_PROBES`` attach marker, selects the program IDs created by this
load (intersected with ``bpftool link show``) and starts the target. The target
completes a real in-memory TLS handshake and warmup, prints a readiness JSON
line, then waits for ``GO`` before the measured read/write loop. Runtime stats
are enabled only around that measured loop and the sysctl value is restored in
``finally``.

``--diagnose-existing`` samples a caller-supplied program-ID set over a short
window so other probes can reuse the same counter-delta helper.

Only BPF program execution time is reported. Hook trigger, userspace
consumption, ring buffer handling, and end-to-end collection latency are out of
scope. A program with a zero ``run_cnt`` delta is uncovered, never 0 ns.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
from pathlib import Path
import platform
import select
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

SCHEMA_PLAN = "mtagentlens.probe-overhead-plan/1"
SCHEMA_DIAGNOSTIC = "mtagentlens.probe-overhead-diagnostic/1"
SCHEMA_MEASUREMENT = "mtagentlens.probe-overhead-measurement/1"
DEFAULT_REPEATS = 3
FIXED_PAYLOAD_BYTES = 256
DEFAULT_SYSCTL = Path("/proc/sys/kernel/bpf_stats_enabled")
DEFAULT_BPFTOOL = "bpftool"
DEFAULT_ITERATIONS = 20000
DEFAULT_WARMUP = 1000
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_ATTACH_TIMEOUT_S = 30.0
ATTACH_MARKER_EVENT = "TLS_CAPTURE_PROBES"


class ProbeError(RuntimeError):
    """Operational failure with optional captured stderr."""

    def __init__(self, message, *, stderr=""):
        super().__init__(message)
        self.stderr = stderr or ""


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def kernel_metadata():
    return {
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
    }


def resolve_executable(command, label):
    candidate = Path(command) if os.sep in str(command) else Path(shutil.which(str(command)) or "")
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ValueError(f"{label} must exist and be executable: {command}")
    return candidate.resolve()


def resolve_tool(command):
    candidate = Path(command) if os.sep in str(command) else Path(shutil.which(str(command)) or "")
    if not candidate.is_file():
        raise ValueError(f"bpftool executable not found: {command}")
    return candidate.resolve()


def find_libssl(explicit=None):
    if explicit:
        candidate = Path(explicit)
        if not candidate.is_file():
            raise ValueError(f"libssl path does not exist: {explicit}")
        return candidate.resolve()
    multiarch = f"{platform.machine()}-linux-gnu"
    for pattern in (f"/usr/lib/{multiarch}/libssl.so.3",
                    f"/lib/{multiarch}/libssl.so.3",
                    "/usr/lib64/libssl.so.3", "/lib64/libssl.so.3",
                    "/usr/lib/libssl.so.3", "/usr/local/lib/libssl.so.3",
                    f"/usr/lib/{multiarch}/libssl.so",
                    "/usr/lib64/libssl.so", "/usr/lib/libssl.so"):
        for match in sorted(glob.glob(pattern)):
            if Path(match).is_file():
                return Path(match).resolve()
    raise ValueError("libssl not found; pass --libssl explicitly")


def parse_programs(payload):
    try:
        rows = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid bpftool prog JSON: {exc}") from exc
    if not isinstance(rows, list):
        raise ValueError("bpftool prog JSON must be a list")
    programs = []
    for row in rows:
        if not isinstance(row, dict) or "id" not in row:
            error = row.get("error") if isinstance(row, dict) else None
            raise ValueError(f"bpftool program row missing id: {error or row!r}")
        try:
            program_id = int(row["id"])
            run_cnt = int(row.get("run_cnt", 0) or 0)
            run_time_ns = int(row.get("run_time_ns", 0) or 0)
            bytes_xlated = int(row.get("bytes_xlated", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid bpftool program counters: {row!r}") from exc
        programs.append({
            "id": program_id,
            "name": str(row.get("name", "")),
            "type": str(row.get("type", "")),
            "run_cnt": run_cnt,
            "run_time_ns": run_time_ns,
            "bytes_xlated": bytes_xlated,
            "map_ids": row.get("map_ids", []),
        })
    return programs


def parse_drop_map(payload):
    """Sum u64 drop reasons, supporting raw bytes and BTF/per-CPU output."""
    def count(value):
        if type(value) is int and value >= 0:
            return value
        if isinstance(value, str):
            result = int(value, 10 if value.isdecimal() else 0)
            if result >= 0:
                return result
        if isinstance(value, list) and len(value) == 8:
            octets = [int(part, 0) if isinstance(part, str) else part for part in value]
            if all(type(part) is int and 0 <= part <= 255 for part in octets):
                return int.from_bytes(bytes(octets), byteorder=sys.byteorder)
        raise ValueError("missing or invalid dropped_events counter")

    rows = json.loads(payload)
    if not isinstance(rows, list) or not rows:
        raise ValueError("dropped_events dump has no entries")
    total = 0
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("invalid dropped_events row")
        row = raw.get("formatted", raw)
        if not isinstance(row, dict):
            raise ValueError("invalid formatted dropped_events row")
        if "values" in row:
            values = row["values"]
            if not isinstance(values, list) or not values:
                raise ValueError("missing per-CPU dropped_events counters")
            total += sum(count(item.get("value")) for item in values if isinstance(item, dict))
            if any(not isinstance(item, dict) for item in values):
                raise ValueError("invalid per-CPU dropped_events row")
        else:
            total += count(row.get("value"))
    return total


def snapshot_drop_maps(bpftool, program_rows, *, sudo):
    """Inspect only maps referenced by this run's selected TLS programs."""
    snapshot = {"counts": {}, "owned_map_ids": [], "complete": False, "errors": []}
    try:
        ids = sorted({int(value) for row in program_rows for value in row.get("map_ids", [])})
    except (TypeError, ValueError):
        snapshot["errors"].append("invalid owned program map IDs")
        return snapshot
    snapshot["owned_map_ids"] = ids
    for map_id in ids:
        try:
            metadata = json.loads(run_bpftool(bpftool,
                ["map", "show", "id", str(map_id), "--json"], sudo=sudo))
            if isinstance(metadata, list) and len(metadata) == 1:
                metadata = metadata[0]
            if not isinstance(metadata, dict) or metadata.get("id") != map_id:
                raise ValueError("map identity differs from the requested owned ID")
            if metadata.get("name") != "dropped_events":
                continue
            if metadata.get("type") not in ("array", "percpu_array"):
                raise ValueError("unexpected dropped_events map type")
            snapshot["counts"][str(map_id)] = parse_drop_map(run_bpftool(bpftool,
                ["map", "dump", "id", str(map_id), "--json"], sudo=sudo))
        except (ProbeError, ValueError, TypeError) as exc:
            snapshot["errors"].append(f"map {map_id}: {exc}")
    if not snapshot["counts"]:
        snapshot["errors"].append("no readable dropped_events map owned by selected programs")
    snapshot["complete"] = bool(snapshot["counts"]) and not snapshot["errors"]
    return snapshot


def drop_map_delta(start, end):
    before, after = start.get("counts", {}), end.get("counts", {})
    known = (start.get("complete") is True and end.get("complete") is True
             and bool(before) and before.keys() == after.keys())
    if known:
        known = all(after[key] >= before[key] for key in before)
    delta = sum(after[key]-before[key] for key in before) if known else None
    observed = [after[key]-before[key] for key in before.keys() & after.keys()
                if after[key] >= before[key]]
    observed_delta = sum(observed) if observed else None
    quality = ("degraded" if observed_delta and observed_delta > 0 else
               "no_drops" if delta == 0 else "unknown")
    return {"quality": quality, "dropped_events_delta": delta,
            "observed_dropped_events_delta": observed_delta,
            "eligible_for_loss_free_cost": quality == "no_drops",
            "start_snapshot": start, "end_snapshot": end}


def parse_links(payload):
    try:
        rows = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid bpftool link JSON: {exc}") from exc
    if not isinstance(rows, list):
        raise ValueError("bpftool link JSON must be a list")
    program_ids = set()
    for row in rows:
        if isinstance(row, dict) and "prog_id" in row:
            try:
                program_ids.add(int(row["prog_id"]))
            except (TypeError, ValueError):
                continue
    return program_ids


def new_program_ids(before_ids, after_rows):
    before = {int(value) for value in before_ids}
    return sorted({row["id"] for row in after_rows} - before)


def select_run_programs(before_ids, after_rows, link_program_ids):
    """Select this load's program IDs; names are reported, never used to guess.

    ``link_program_ids`` must come from ``bpftool link show``. Loaded but
    unattached skeleton programs are excluded.
    """
    by_id = {row["id"]: row for row in after_rows}
    candidates = new_program_ids(before_ids, after_rows)
    if not candidates:
        raise ValueError("no bpftool programs loaded by this run")
    if link_program_ids is None:
        selected = candidates
    else:
        attached = {int(value) for value in link_program_ids}
        selected = [program_id for program_id in candidates if program_id in attached]
        if not selected:
            raise ValueError("no attached bpftool programs for this run")
    return [by_id[program_id] for program_id in selected]


def counter_deltas(start_rows, end_rows, program_ids):
    start_map = {row["id"]: row for row in start_rows}
    end_map = {row["id"]: row for row in end_rows}
    deltas = []
    for raw_id in program_ids:
        program_id = int(raw_id)
        if program_id not in start_map:
            raise ValueError(f"program id {program_id} missing from start snapshot")
        if program_id not in end_map:
            raise ValueError(f"program id {program_id} missing from end snapshot")
        start = start_map[program_id]
        end = end_map[program_id]
        run_cnt = end["run_cnt"] - start["run_cnt"]
        run_time_ns = end["run_time_ns"] - start["run_time_ns"]
        if run_cnt < 0 or run_time_ns < 0:
            raise ValueError(f"program id {program_id} counters decreased; reset or wrong snapshot")
        covered = run_cnt > 0
        deltas.append({
            "id": program_id,
            "name": end.get("name") or start.get("name", ""),
            "type": end.get("type") or start.get("type", ""),
            "start_run_cnt": start["run_cnt"],
            "end_run_cnt": end["run_cnt"],
            "start_run_time_ns": start["run_time_ns"],
            "end_run_time_ns": end["run_time_ns"],
            "run_cnt_delta": run_cnt,
            "run_time_ns_delta": run_time_ns,
            "covered": covered,
            "ns_per_call": (run_time_ns / run_cnt) if covered else None,
        })
    return deltas


def total_ns_per_operation(deltas, successful_operations):
    """Sum of measured program run time divided by all successful SSL ops.

    Per-program means are intentionally not summed: programs can run a
    different number of times for the same workload.
    """
    if type(successful_operations) is not int or successful_operations <= 0:
        raise ValueError("successful SSL operation count must be a positive integer")
    if not deltas:
        return None
    total_run_time_ns = sum(int(row["run_time_ns_delta"]) for row in deltas)
    covered = sum(1 for row in deltas if row["covered"])
    if covered == 0 or total_run_time_ns <= 0:
        return None
    return total_run_time_ns / successful_operations


class StatsGate:
    """Enable kernel BPF runtime stats for a window and restore the original value."""

    def __init__(self, path=DEFAULT_SYSCTL, *, sudo=False):
        self.path = Path(path)
        self.sudo = sudo
        self.original = None
        self.changed = False
        self.restored = False
        self._entered = False

    def _write(self, value):
        if self.sudo:
            proc = subprocess.run(
                ["sudo", "-n", "tee", str(self.path)],
                input=value + "\n", capture_output=True, text=True)
            if proc.returncode != 0:
                raise ProbeError(f"failed to write {self.path} with sudo -n",
                                 stderr=proc.stderr)
        else:
            self.path.write_text(value + "\n")

    def __enter__(self):
        original = self.path.read_text().strip()
        if original not in ("0", "1"):
            raise ValueError(
                f"{self.path} contains unexpected value {original!r}; expected 0 or 1")
        self.original = original
        if original != "1":
            self._write("1")
            self.changed = True
        self._entered = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self._entered and self.changed:
            try:
                self._write(self.original)
                self.restored = True
            finally:
                self.changed = False
                self._entered = False
        elif self._entered:
            self.restored = True
            self._entered = False
        return False


def sudo_prefix(sudo):
    return ["sudo", "-n"] if sudo else []


def run_bpftool(bpftool, args, *, sudo, timeout=30.0):
    argv = [str(bpftool), *args]
    try:
        proc = subprocess.run([*sudo_prefix(sudo), *argv], capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(f"bpftool command failed: {exc}") from exc
    if proc.returncode != 0:
        raise ProbeError(f"bpftool {' '.join(args)} failed with rc={proc.returncode}",
                         stderr=proc.stderr)
    return proc.stdout


def snapshot_programs(bpftool, *, sudo):
    return parse_programs(run_bpftool(bpftool, ["prog", "show", "--json"], sudo=sudo))


def snapshot_link_program_ids(bpftool, *, sudo):
    return parse_links(run_bpftool(bpftool, ["link", "show", "--json"], sudo=sudo))


def find_attach_marker(text):
    for line in text.splitlines():
        start = line.find("{")
        if start < 0:
            continue
        try:
            payload = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("event") == ATTACH_MARKER_EVENT:
            return payload
    return None


def read_log_tail(path, limit=4000):
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return ""
    return text[-limit:]


def resolve_out(path):
    out = Path(path)
    if out.exists() and out.is_dir():
        out = out / "probe-overhead.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def write_json(path, payload):
    Path(path).write_text(json.dumps(payload, indent=2) + "\n")


def build_plan(collector, target, out, *, repeats=DEFAULT_REPEATS,
               iterations=DEFAULT_ITERATIONS, warmup=DEFAULT_WARMUP, libssl=None,
               comm=None, bpftool=DEFAULT_BPFTOOL, sysctl=DEFAULT_SYSCTL,
               execute=False, use_sudo=True, timeout_s=DEFAULT_TIMEOUT_S,
               uid=None):
    collector_path = resolve_executable(collector, "collector binary")
    target_path = resolve_executable(target, "target binary")
    if type(repeats) is not int or repeats < 1:
        raise ValueError("repeats must be a positive integer")
    if type(iterations) is not int or iterations < 1:
        raise ValueError("iterations must be a positive integer")
    if type(warmup) is not int or warmup < 0:
        raise ValueError("warmup must be a non-negative integer")
    if timeout_s <= 0:
        raise ValueError("timeout seconds must be positive")
    libssl_path = find_libssl(libssl)
    bpftool_path = resolve_tool(bpftool)
    comm = comm or target_path.name
    if not isinstance(comm, str) or not 1 <= len(comm) <= 15:
        raise ValueError("target comm must be 1..15 characters; pass an explicit --comm")
    uid = os.getuid() if uid is None else int(uid)
    if uid < 0:
        raise ValueError("target uid must be non-negative")

    target_argv = [str(target_path), str(iterations), "--warmup", str(warmup), "--gate"]
    # -o avoids the collector's default libssl attach, so the explicit
    # --binary-path attach is the single probe set for this run. -u is a
    # BPF-side uid filter; -c is only a userspace event filter.
    collector_argv = [str(collector_path), "ssl", "--binary-path", str(libssl_path),
                      "--quiet", "--", "-o", "-c", comm, "-u", str(uid)]
    return {
        "schema": SCHEMA_PLAN,
        "mode": "tls_probe_microbenchmark",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution": {
            "execute": bool(execute),
            "requires_root": True,
            "privilege": ("sudo -n for collector/bpftool; target direct"
                          if use_sudo else "direct"),
            "stats_enabled_by_default": False,
        },
        "repeats": repeats,
        "iterations": iterations,
        "warmup": warmup,
        "payload_bytes": FIXED_PAYLOAD_BYTES,
        "target": {"path": str(target_path), "sha256": sha256_file(target_path),
                   "comm": comm, "uid": uid},
        "collector": {"path": str(collector_path), "sha256": sha256_file(collector_path)},
        "libssl": {"path": str(libssl_path), "sha256": sha256_file(libssl_path)},
        "bpftool": {"path": str(bpftool_path), "sha256": sha256_file(bpftool_path)},
        "sysctl": str(sysctl),
        "kernel": kernel_metadata(),
        "target_argv": target_argv,
        "collector_argv": collector_argv,
        "measurement": {
            "includes_handshake": False,
            "includes_warmup": False,
            "readiness_gate": "target stdout ready JSON then stdin GO",
            "denominator": "successful SSL_read + SSL_write operations reported by target",
            "program_selection": "new bpftool prog IDs intersected with bpftool link prog_ids",
            "program_identity": ("numeric bpftool prog IDs only; truncated names are "
                                 "reported, and a shared entry program aggregates all "
                                 "symbols attached to that program ID"),
            "stats_scope": "diagnostic window only",
            "isolation": ("BPF-side -u target uid; -c target comm is a userspace "
                          "event filter; still requires an isolated measurement window"),
            "uncovered_policy": "run_cnt delta 0 is covered=false and ns_per_call=null, not 0 ns",
            "not_measured": ["hook trigger", "userspace consumption",
                             "ring buffer handling", "end-to-end collection latency"],
        },
        "out": str(out),
        "timeout_s": timeout_s,
    }


def build_diagnose_plan(program_ids, out, *, window_seconds=5.0,
                        bpftool=DEFAULT_BPFTOOL, sysctl=DEFAULT_SYSCTL,
                        execute=False, use_sudo=True):
    ids = sorted({int(value) for value in program_ids})
    if not ids:
        raise ValueError("at least one --program-id is required for --diagnose-existing")
    if any(value < 1 for value in ids):
        raise ValueError("program IDs must be positive integers")
    if window_seconds < 0:
        raise ValueError("window seconds must be non-negative")
    bpftool_path = resolve_tool(bpftool)
    return {
        "schema": SCHEMA_PLAN,
        "mode": "existing_programs_diagnostic",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution": {
            "execute": bool(execute),
            "requires_root": True,
            "privilege": ("sudo -n for bpftool" if use_sudo else "direct"),
            "stats_enabled_by_default": False,
        },
        "program_ids": ids,
        "window_seconds": float(window_seconds),
        "bpftool": {"path": str(bpftool_path), "sha256": sha256_file(bpftool_path)},
        "sysctl": str(sysctl),
        "kernel": kernel_metadata(),
        "out": str(out),
        "note": ("Only BPF program run-time counters for the given IDs; no collector "
                 "launch, hook trigger, userspace consumption, or collection latency."),
    }


def _start_collector(plan, stderr_path, *, sudo):
    env = os.environ.copy()
    env["RUST_LOG"] = "info"
    handle = Path(stderr_path).open("w")
    try:
        proc = subprocess.Popen(
            [*sudo_prefix(sudo), *plan["collector_argv"]],
            stdout=subprocess.DEVNULL, stderr=handle, text=True, env=env,
        )
    except OSError:
        handle.close()
        raise
    return proc, handle


def _stop_process(proc):
    """Stop only a process this module started."""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def _wait_attach_marker(path, timeout_s, collector):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        text = read_log_tail(path, limit=200000)
        marker = find_attach_marker(text)
        if marker is not None:
            attached = int(marker.get("attached", 0) or 0)
            if attached < 1:
                raise ProbeError("collector reported zero attached TLS probes",
                                 stderr=read_log_tail(path))
            if not marker.get("core_read") or not marker.get("core_write"):
                raise ProbeError("collector did not attach both SSL_read and SSL_write",
                                 stderr=read_log_tail(path))
            return marker
        if collector.poll() is not None:
            raise ProbeError(
                f"collector exited before the TLS attach marker (rc={collector.returncode})",
                stderr=read_log_tail(path))
        time.sleep(0.05)
    raise ProbeError("timed out waiting for the collector TLS attach marker",
                     stderr=read_log_tail(path))


def _wait_target_ready(proc, timeout_s, stderr_path):
    ready, _, _ = select.select([proc.stdout], [], [], timeout_s)
    if not ready:
        raise ProbeError("timed out waiting for target readiness",
                         stderr=read_log_tail(stderr_path))
    line = proc.stdout.readline()
    if not line:
        raise ProbeError("target exited before its readiness line",
                         stderr=read_log_tail(stderr_path))
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"target readiness line is not JSON: {line[:200]!r}",
                         stderr=read_log_tail(stderr_path)) from exc
    if not payload.get("ready"):
        raise ProbeError("target readiness line did not set ready=true",
                         stderr=read_log_tail(stderr_path))
    return payload


def _target_operations(target_payload):
    value = target_payload.get("ssl_operation_count")
    writes = target_payload.get("ssl_write_count")
    reads = target_payload.get("ssl_read_count")
    if type(value) is not int or value <= 0:
        raise ProbeError("target did not report a positive ssl_operation_count")
    if type(writes) is not int or type(reads) is not int:
        raise ProbeError("target did not report integer read/write counts")
    if writes + reads != value:
        raise ProbeError("target read/write counts do not add up to ssl_operation_count")
    return value


def _read_target_result(stdout, stderr_path, *, expected_iterations, expected_warmup):
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise ProbeError("target produced no final JSON", stderr=read_log_tail(stderr_path))
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise ProbeError(f"target final line is not JSON: {lines[-1][:200]!r}",
                         stderr=read_log_tail(stderr_path)) from exc
    if payload.get("schema") != "mtagentlens.ssl-loop/1":
        raise ProbeError("target reported an unexpected schema",
                         stderr=read_log_tail(stderr_path))
    if payload.get("status") != "ok" or payload.get("handshake") != "ok":
        raise ProbeError("target did not report a successful handshake",
                         stderr=read_log_tail(stderr_path))
    if payload.get("payload_bytes") != FIXED_PAYLOAD_BYTES:
        raise ProbeError("target payload size does not match the fixed payload",
                         stderr=read_log_tail(stderr_path))
    if payload.get("iterations") != expected_iterations:
        raise ProbeError("target iteration count does not match the plan",
                         stderr=read_log_tail(stderr_path))
    if payload.get("warmup_iterations") != expected_warmup:
        raise ProbeError("target warmup count does not match the plan",
                         stderr=read_log_tail(stderr_path))
    elapsed = payload.get("elapsed_ns")
    if type(elapsed) is not int or elapsed < 0:
        raise ProbeError("target did not report a non-negative elapsed_ns",
                         stderr=read_log_tail(stderr_path))
    _target_operations(payload)
    return payload


def _summary(repeats):
    def statistics_for(rows):
        values = [row["totals"]["ns_per_successful_ssl_operation"] for row in rows
                  if row["totals"]["ns_per_successful_ssl_operation"] is not None]
        return {"n": len(values), "mean": statistics.mean(values) if values else None,
                "min": min(values) if values else None, "max": max(values) if values else None}

    eligible = [row for row in repeats
                if row.get("capture_quality", {}).get("eligible_for_loss_free_cost") is True]
    return {"repeats": len(repeats),
            "loss_free_repeats": len(eligible),
            "ns_per_successful_ssl_operation": statistics_for(eligible),
            "all_observed_ns_per_successful_ssl_operation": statistics_for(repeats),
            "quality_policy": "primary cost includes only verified zero-drop repeats; all raw data retained",
            "covered_program_ids": sorted({row["id"] for repeat in repeats
                                            for row in repeat["programs"] if row["covered"]})}


def run_diagnostic(plan, *, use_sudo):
    out_path = resolve_out(plan["out"])
    payload = {
        "schema": SCHEMA_DIAGNOSTIC,
        "mode": plan["mode"],
        "status": "ok",
        "program_ids": plan["program_ids"],
        "window_seconds": plan["window_seconds"],
        "kernel": plan["kernel"],
        "bpftool": plan["bpftool"],
        "sysctl": plan["sysctl"],
    }
    gate = StatsGate(plan["sysctl"], sudo=use_sudo)
    try:
        with gate:
            start = snapshot_programs(plan["bpftool"]["path"], sudo=use_sudo)
            if plan["window_seconds"] > 0:
                time.sleep(plan["window_seconds"])
            end = snapshot_programs(plan["bpftool"]["path"], sudo=use_sudo)
        deltas = counter_deltas(start, end, plan["program_ids"])
        payload.update({
            "start_snapshot": start,
            "end_snapshot": end,
            "programs": deltas,
            "totals": {
                "run_cnt": sum(row["run_cnt_delta"] for row in deltas),
                "run_time_ns": sum(row["run_time_ns_delta"] for row in deltas),
                "covered_programs": sum(1 for row in deltas if row["covered"]),
            },
            "stats_gate": {
                "sysctl": str(gate.path),
                "original": gate.original,
                "restored": gate.restored,
            },
            "note": ("Only BPF program execution counters for the given IDs; "
                     "uncovered programs have covered=false and ns_per_call=null."),
        })
    except Exception as exc:  # noqa: BLE001 - surfaced as a failure artifact
        payload.update({
            "status": "error",
            "error": str(exc),
            "stderr": getattr(exc, "stderr", ""),
            "stats_gate": {
                "sysctl": str(gate.path),
                "original": gate.original,
                "restored": gate.restored,
            },
        })
        write_json(out_path, payload)
        return 1
    write_json(out_path, payload)
    return 0


def run_measurement(plan, *, use_sudo):
    out_path = resolve_out(plan["out"])
    stem = out_path.stem
    collector_log = out_path.parent / f"{stem}.collector.stderr.log"
    target_log = out_path.parent / f"{stem}.target.stderr.log"
    payload = {
        "schema": SCHEMA_MEASUREMENT,
        "mode": plan["mode"],
        "status": "ok",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "kernel": plan["kernel"],
        "collector": plan["collector"],
        "target": plan["target"],
        "libssl": plan["libssl"],
        "bpftool": plan["bpftool"],
        "sysctl": plan["sysctl"],
        "payload_bytes": plan["payload_bytes"],
        "iterations": plan["iterations"],
        "warmup": plan["warmup"],
        "repeats_requested": plan["repeats"],
        "target_argv": plan["target_argv"],
        "collector_argv": plan["collector_argv"],
        "measurement": plan["measurement"],
    }
    collector = None
    collector_handle = None
    try:
        before = snapshot_programs(plan["bpftool"]["path"], sudo=use_sudo)
        before_ids = {row["id"] for row in before}
        collector, collector_handle = _start_collector(plan, collector_log, sudo=use_sudo)
        marker = _wait_attach_marker(collector_log, DEFAULT_ATTACH_TIMEOUT_S, collector)

        after = []
        link_ids = set()
        selected = []
        last_selection_error = None
        deadline = time.monotonic() + DEFAULT_ATTACH_TIMEOUT_S
        while time.monotonic() < deadline:
            after = snapshot_programs(plan["bpftool"]["path"], sudo=use_sudo)
            link_ids = snapshot_link_program_ids(plan["bpftool"]["path"], sudo=use_sudo)
            try:
                selected = select_run_programs(before_ids, after, link_ids)
                break
            except ValueError as exc:
                last_selection_error = exc
                time.sleep(0.05)
        if not selected:
            raise ProbeError(
                "could not select this run's attached program IDs from bpftool: "
                f"{last_selection_error}",
                stderr=read_log_tail(collector_log))
        selected_ids = [row["id"] for row in selected]
        payload["attach"] = {
            "marker": marker,
            "before_program_ids": sorted(before_ids),
            "new_program_ids": new_program_ids(before_ids, after),
            "link_program_ids": sorted(link_ids),
            "selected_program_ids": selected_ids,
            "selected_programs": selected,
        }

        repeats = []
        payload["repeats"] = repeats
        for run_index in range(1, plan["repeats"] + 1):
            target_handle = Path(target_log).open("w")
            target = None
            try:
                target = subprocess.Popen(
                    plan["target_argv"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=target_handle, text=True,
                )
                ready = _wait_target_ready(target, plan["timeout_s"], target_log)
                loss_start = snapshot_drop_maps(plan["bpftool"]["path"], selected, sudo=use_sudo)
                with StatsGate(plan["sysctl"], sudo=use_sudo) as gate:
                    start = snapshot_programs(plan["bpftool"]["path"], sudo=use_sudo)
                    target.stdin.write("GO\n")
                    target.stdin.flush()
                    stdout, _ = target.communicate(timeout=plan["timeout_s"])
                    end = snapshot_programs(plan["bpftool"]["path"], sudo=use_sudo)
                loss_end = snapshot_drop_maps(plan["bpftool"]["path"], selected, sudo=use_sudo)
                loss = drop_map_delta(loss_start, loss_end)
                if target.returncode != 0:
                    raise ProbeError(
                        f"target exited with rc={target.returncode}",
                        stderr=read_log_tail(target_log))
                target_payload = _read_target_result(
                    stdout, target_log,
                    expected_iterations=plan["iterations"],
                    expected_warmup=plan["warmup"])
                deltas = counter_deltas(start, end, selected_ids)
                operations = target_payload["ssl_operation_count"]
                per_operation = total_ns_per_operation(deltas, operations)
                repeats.append({
                    "index": run_index,
                    "status": "degraded" if loss["quality"] == "degraded" else "ok",
                    "capture_quality": loss,
                    "target": target_payload,
                    "start_snapshot": start,
                    "end_snapshot": end,
                    "programs": deltas,
                    "totals": {
                        "run_cnt": sum(row["run_cnt_delta"] for row in deltas),
                        "run_time_ns": sum(row["run_time_ns_delta"] for row in deltas),
                        "covered_programs": sum(1 for row in deltas if row["covered"]),
                        "successful_ssl_operations": operations,
                        "ns_per_successful_ssl_operation": per_operation,
                    },
                    "stats_gate": {
                        "sysctl": str(gate.path),
                        "original": gate.original,
                        "restored": gate.restored,
                    },
                })
            finally:
                _stop_process(target)
                target_handle.close()
        payload["summary"] = _summary(repeats)
        if any(row["capture_quality"]["quality"] == "degraded" for row in repeats):
            payload["status"] = "degraded"
        payload["artifacts"] = {
            "collector_stderr": str(collector_log),
            "target_stderr": str(target_log),
        }
    except Exception as exc:  # noqa: BLE001 - surfaced as a failure artifact
        _stop_process(collector)
        if collector_handle is not None:
            collector_handle.close()
        payload.update({
            "status": "error",
            "error": str(exc),
            "stderr": (getattr(exc, "stderr", "") or
                       read_log_tail(collector_log) + read_log_tail(target_log)),
            "artifacts": {
                "collector_stderr": str(collector_log),
                "target_stderr": str(target_log),
            },
        })
        write_json(out_path, payload)
        return 1
    finally:
        _stop_process(collector)
        if collector_handle is not None:
            collector_handle.close()
    write_json(out_path, payload)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="TLS probe microbenchmark (plan-only by default) and bpftool "
                    "counter diagnostics. Not a full collection-latency measurement.")
    parser.add_argument("--collector", help="collector binary (TLS mode)")
    parser.add_argument("--target", help="ssl_loop binary (TLS mode)")
    parser.add_argument("--out", required=True, help="output JSON file or directory")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    parser.add_argument("--libssl", help="explicit libssl path to attach")
    parser.add_argument("--comm", help="target comm filter (<=15 chars)")
    parser.add_argument("--bpftool", default=DEFAULT_BPFTOOL)
    parser.add_argument("--sysctl", default=str(DEFAULT_SYSCTL))
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--execute", action="store_true",
                        help="actually start the collector/target (default: plan only)")
    parser.add_argument("--diagnose-existing", action="store_true",
                        help="sample given program IDs over a short window; no collector")
    parser.add_argument("--program-id", action="append", type=int, default=[],
                        help="program ID for --diagnose-existing (repeatable)")
    parser.add_argument("--window-seconds", type=float, default=5.0,
                        help="diagnostic window for --diagnose-existing")
    parser.add_argument("--no-sudo", action="store_true",
                        help="invoke commands directly; never use a password prompt")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    out_path = resolve_out(args.out)
    use_sudo = not args.no_sudo and os.geteuid() != 0
    try:
        if args.diagnose_existing:
            if not args.program_id:
                parser.error("--program-id is required with --diagnose-existing")
            plan = build_diagnose_plan(
                args.program_id, out_path, window_seconds=args.window_seconds,
                bpftool=args.bpftool, sysctl=args.sysctl, execute=args.execute,
                use_sudo=use_sudo)
            if not args.execute:
                write_json(out_path, plan)
                return 0
            return run_diagnostic(plan, use_sudo=use_sudo)

        if not args.collector or not args.target:
            parser.error("--collector and --target are required unless --diagnose-existing")
        plan = build_plan(
            args.collector, args.target, out_path, repeats=args.repeats,
            iterations=args.iterations, warmup=args.warmup, libssl=args.libssl,
            comm=args.comm, bpftool=args.bpftool, sysctl=args.sysctl,
            execute=args.execute, use_sudo=use_sudo, timeout_s=args.timeout_seconds)
        if not args.execute:
            write_json(out_path, plan)
            return 0
        return run_measurement(plan, use_sudo=use_sudo)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
