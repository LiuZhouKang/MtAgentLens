#!/usr/bin/env python3
"""Build the canonical paper performance artifact from local microbenchmarks.

This script intentionally separates the paper gate artifact
(`performance_results.json`) from the legacy plotting report (`perf_report.json`).
Every completed experiment below is backed by a command run on this host. The
scope is a local collector/probe microbenchmark, not a production workload
benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import signal
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

PROJECT = Path(__file__).resolve().parents[3]
PERF_DIR = PROJECT / "compare" / "results" / "perf"
RAW_DIR = PERF_DIR / "raw"
BENCH_DIR = PROJECT / "compare" / "scripts" / "perf" / "bench_targets"
COLLECTOR_BIN = PROJECT / "collector" / "target" / "release" / "mtagentlens"
THROUGHPUT_BIN = PROJECT / "collector" / "target" / "release" / "perf_throughput"
OUTPUT_PATH = PERF_DIR / "performance_results.json"


@dataclass(frozen=True)
class CommandRecord:
    id: str
    command: list[str]
    cwd: Path
    returncode: int
    elapsed_s: float
    stdout_tail: str
    stderr_tail: str

    def as_artifact(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "cwd": str(self.cwd.relative_to(PROJECT)),
            "returncode": self.returncode,
            "elapsed_s": self.elapsed_s,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
        }


class BenchmarkError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT))
    except ValueError:
        return str(path)


def _tail(text: str, limit: int = 4000) -> str:
    return text[-limit:] if len(text) > limit else text


def _run(
    command: list[str],
    *,
    cwd: Path = PROJECT,
    timeout: int = 300,
    record_id: str,
) -> tuple[CommandRecord, subprocess.CompletedProcess[str]]:
    start = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = time.perf_counter() - start
    record = CommandRecord(
        id=record_id,
        command=_sanitize_command(command),
        cwd=cwd,
        returncode=result.returncode,
        elapsed_s=elapsed,
        stdout_tail=_tail(result.stdout or ""),
        stderr_tail=_tail(result.stderr or ""),
    )
    if result.returncode != 0:
        raise BenchmarkError(
            f"{record_id} failed with rc={result.returncode}: {record.stderr_tail or record.stdout_tail}"
        )
    return record, result


def _sanitize_command(command: Iterable[str]) -> list[str]:
    sanitized: list[str] = []
    skip_next = False
    for item in command:
        if skip_next:
            sanitized.append("<redacted>")
            skip_next = False
            continue
        if item in {"-S", "--stdin"}:
            sanitized.append(item)
            continue
        if "PASSWORD" in item.upper():
            sanitized.append("<redacted>")
        else:
            sanitized.append(item)
    return sanitized


def _sudo_prefix() -> list[str]:
    return ["sudo", "-S", "-p", ""]


def _sudo_password(required: bool) -> str | None:
    password = os.environ.get("MTAGENTLENS_SUDO_PASSWORD")
    if required and not password:
        raise BenchmarkError(
            "MTAGENTLENS_SUDO_PASSWORD is required for collector-backed performance microbenchmarks"
        )
    return password


@contextmanager
def _collector_process(
    *,
    command: list[str],
    password: str,
    stdout_path: Path,
    stderr_path: Path,
    startup_s: float,
    marker: str,
) -> Iterator[tuple[subprocess.Popen[str], int]]:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_file = stdout_path.open("w", encoding="utf-8")
    stderr_file = stderr_path.open("w", encoding="utf-8")
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            _sudo_prefix() + command,
            cwd=str(PROJECT),
            stdin=subprocess.PIPE,
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
            preexec_fn=os.setsid,
        )
        if proc.stdin:
            proc.stdin.write(password + "\n")
            proc.stdin.flush()
            proc.stdin.close()
        time.sleep(startup_s)
        if proc.poll() is not None:
            stdout_file.flush()
            stderr_file.flush()
            raise BenchmarkError(
                "collector exited during startup: "
                + _tail(stderr_path.read_text(encoding="utf-8", errors="replace"))
            )
        collector_pid = _find_pid_by_cmdline(marker)
        if collector_pid is None:
            raise BenchmarkError(f"collector process not found for marker {marker}")
        yield proc, collector_pid
    finally:
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGINT)
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait(timeout=5)
            except ProcessLookupError:
                pass
        stdout_file.close()
        stderr_file.close()


def _find_pid_by_cmdline(marker: str) -> int | None:
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        try:
            raw = (proc_dir / "cmdline").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        parts = [part.decode("utf-8", errors="replace") for part in raw.split(b"\x00") if part]
        if not parts:
            continue
        executable = Path(parts[0]).name
        cmdline = " ".join(parts)
        if executable == "mtagentlens" and marker in cmdline:
            return int(proc_dir.name)
    return None


def _rss_mb(pid: int) -> float:
    statm = Path(f"/proc/{pid}/statm").read_text(encoding="utf-8")
    rss_pages = int(statm.split()[1])
    return rss_pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        raise BenchmarkError("cannot summarize an empty sample")
    sorted_values = sorted(values)
    return {
        "n": len(values),
        "min": min(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "p95": _percentile(sorted_values, 95),
        "p99": _percentile(sorted_values, 99),
        "max": max(values),
        "stddev": statistics.stdev(values) if len(values) > 1 else 0.0,
        "raw_values": values,
    }


def _percentile(sorted_values: list[float], pct: int) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = round((len(sorted_values) - 1) * pct / 100.0)
    return sorted_values[max(0, min(len(sorted_values) - 1, index))]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.SubprocessError:
        return "unknown"


def _git_dirty() -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(PROJECT),
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(result.stdout.strip())
    except subprocess.SubprocessError:
        return True


def _ensure_binaries(*, skip_build: bool, command_log: list[CommandRecord]) -> None:
    if not skip_build:
        record, _ = _run(
            ["cargo", "build", "--release", "--bin", "mtagentlens", "--bin", "perf_throughput"],
            cwd=PROJECT / "collector",
            timeout=900,
            record_id="build_collector_perf_bins",
        )
        command_log.append(record)
    if not COLLECTOR_BIN.exists():
        raise BenchmarkError(f"collector binary missing: {COLLECTOR_BIN}")
    if not THROUGHPUT_BIN.exists():
        raise BenchmarkError(f"throughput binary missing: {THROUGHPUT_BIN}")


def _build_targets(command_log: list[CommandRecord]) -> None:
    record, _ = _run(
        ["make", "-C", _rel(BENCH_DIR), "clean", "all"],
        timeout=120,
        record_id="build_c_bench_targets",
    )
    command_log.append(record)


def _run_target(command: list[str], *, timeout: int, record_id: str) -> tuple[float, CommandRecord]:
    record, _ = _run(command, timeout=timeout, record_id=record_id)
    return record.elapsed_s, record


def run_probe_latency(
    *,
    runs: int,
    startup_s: float,
    sudo_password: str,
    command_log: list[CommandRecord],
) -> dict[str, Any]:
    configs = [
        {
            "id": "process_exec_tracepoint",
            "target": BENCH_DIR / "exec_loop",
            "iterations": 200,
            "collector_args": [
                str(COLLECTOR_BIN),
                "trace",
                "--ssl=false",
                "--process=true",
                "-c",
                "exec_loop",
                "--quiet",
                "--disable-semantic-observer",
            ],
        },
        {
            "id": "file_access_tracepoints",
            "target": BENCH_DIR / "file_loop",
            "iterations": 1000,
            "collector_args": [
                str(COLLECTOR_BIN),
                "trace",
                "--ssl=false",
                "--process=true",
                "-c",
                "file_loop",
                "--quiet",
                "--disable-semantic-observer",
            ],
        },
    ]
    all_runs: list[dict[str, Any]] = []
    summaries: dict[str, Any] = {}

    for config in configs:
        baseline_values: list[float] = []
        instrumented_values: list[float] = []
        run_rows: list[dict[str, Any]] = []
        for index in range(runs):
            target_cmd = [str(config["target"]), str(config["iterations"])]
            baseline_s, baseline_record = _run_target(
                target_cmd,
                timeout=180,
                record_id=f"probe_{config['id']}_baseline_{index + 1}",
            )
            command_log.append(baseline_record)
            baseline_values.append(baseline_s)

            log_path = RAW_DIR / f"probe_{config['id']}_{index + 1}.trace.log"
            stdout_path = RAW_DIR / f"probe_{config['id']}_{index + 1}.collector.stdout.log"
            stderr_path = RAW_DIR / f"probe_{config['id']}_{index + 1}.collector.stderr.log"
            collector_cmd = list(config["collector_args"]) + ["-o", str(log_path)]
            marker = str(log_path)
            with _collector_process(
                command=collector_cmd,
                password=sudo_password,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                startup_s=startup_s,
                marker=marker,
            ):
                instrumented_s, instrumented_record = _run_target(
                    target_cmd,
                    timeout=180,
                    record_id=f"probe_{config['id']}_instrumented_{index + 1}",
                )
            command_log.append(
                CommandRecord(
                    id=f"probe_{config['id']}_collector_{index + 1}",
                    command=_sanitize_command(_sudo_prefix() + collector_cmd),
                    cwd=PROJECT,
                    returncode=0,
                    elapsed_s=startup_s + instrumented_s,
                    stdout_tail=_tail(stdout_path.read_text(encoding="utf-8", errors="replace")),
                    stderr_tail=_tail(stderr_path.read_text(encoding="utf-8", errors="replace")),
                )
            )
            command_log.append(instrumented_record)
            instrumented_values.append(instrumented_s)
            delta_s = instrumented_s - baseline_s
            run_rows.append(
                {
                    "run_id": f"{config['id']}_{index + 1}",
                    "target": _rel(config["target"]),
                    "iterations": config["iterations"],
                    "baseline_s": baseline_s,
                    "instrumented_s": instrumented_s,
                    "delta_s": delta_s,
                    "overhead_per_iteration_ns": delta_s / config["iterations"] * 1e9,
                    "raw_log": _rel(log_path),
                    "collector_stdout": _rel(stdout_path),
                    "collector_stderr": _rel(stderr_path),
                }
            )

        baseline_stats = _stats(baseline_values)
        instrumented_stats = _stats(instrumented_values)
        overhead_values = [
            (instrumented - baseline) / config["iterations"] * 1e9
            for baseline, instrumented in zip(baseline_values, instrumented_values)
        ]
        summaries[config["id"]] = {
            "target": _rel(config["target"]),
            "iterations": config["iterations"],
            "baseline_s": baseline_stats,
            "instrumented_s": instrumented_stats,
            "overhead_per_iteration_ns": _stats(overhead_values),
            "note": "Negative deltas are retained as measurement noise, not clipped.",
        }
        all_runs.extend(run_rows)

    return {
        "status": "completed",
        "methodology": "paired wall-clock target runtime with and without process/file eBPF collector",
        "scope": "local process/file trace microbenchmark; not a live LLM-agent workload",
        "runs": all_runs,
        "summary": summaries,
    }


def run_throughput(
    *,
    runs: int,
    event_counts: list[int],
    command_log: list[CommandRecord],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for count in event_counts:
        for index in range(runs):
            record, result = _run(
                [str(THROUGHPUT_BIN), str(count)],
                timeout=300,
                record_id=f"throughput_{count}_{index + 1}",
            )
            command_log.append(record)
            json_line = result.stdout.strip().splitlines()[-1]
            data = json.loads(json_line)
            rows.append(
                {
                    "run_id": f"throughput_{count}_{index + 1}",
                    "event_pairs": count,
                    "total_events": data["total_events"],
                    "raw_events": data["raw_events"],
                    "full_pipeline_elapsed_s": data["full_pipeline"]["elapsed_s"],
                    "full_pipeline_events_per_sec": data["full_pipeline"]["events_per_sec"],
                    "passthrough_elapsed_s": data["passthrough"]["elapsed_s"],
                    "passthrough_events_per_sec": data["passthrough"]["events_per_sec"],
                    "analyzer_overhead_pct": data["analyzer_overhead_pct"],
                }
            )

    by_count: dict[str, Any] = {}
    for count in event_counts:
        count_rows = [row for row in rows if row["event_pairs"] == count]
        by_count[str(count)] = {
            "full_pipeline_events_per_sec": _stats(
                [row["full_pipeline_events_per_sec"] for row in count_rows]
            ),
            "passthrough_events_per_sec": _stats(
                [row["passthrough_events_per_sec"] for row in count_rows]
            ),
            "analyzer_overhead_pct": _stats([row["analyzer_overhead_pct"] for row in count_rows]),
        }

    return {
        "status": "completed",
        "methodology": "collector FakeRunner synthetic SSL-event pipeline throughput",
        "scope": "in-process analyzer throughput microbenchmark; not kernel ring-buffer saturation",
        "runs": rows,
        "summary": by_count,
    }


def run_memory(
    *,
    duration_s: float,
    sample_interval_s: float,
    startup_s: float,
    sudo_password: str,
    command_log: list[CommandRecord],
) -> dict[str, Any]:
    log_path = RAW_DIR / "memory_process_trace.trace.log"
    stdout_path = RAW_DIR / "memory_process_trace.collector.stdout.log"
    stderr_path = RAW_DIR / "memory_process_trace.collector.stderr.log"
    collector_cmd = [
        str(COLLECTOR_BIN),
        "trace",
        "--ssl=false",
        "--process=true",
        "-c",
        "file_loop",
        "--quiet",
        "--disable-semantic-observer",
        "-o",
        str(log_path),
    ]
    rss_samples: list[float] = []
    timestamps_s: list[float] = []
    with _collector_process(
        command=collector_cmd,
        password=sudo_password,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        startup_s=startup_s,
        marker=str(log_path),
    ) as (_, collector_pid):
        start = time.perf_counter()
        next_load = start
        while (time.perf_counter() - start) < duration_s:
            now = time.perf_counter()
            timestamps_s.append(now - start)
            rss_samples.append(_rss_mb(collector_pid))
            if now >= next_load:
                load_record, _ = _run(
                    [str(BENCH_DIR / "file_loop"), "200"],
                    timeout=60,
                    record_id="memory_load_file_loop",
                )
                command_log.append(load_record)
                next_load = time.perf_counter() + 0.5
            time.sleep(sample_interval_s)

    command_log.append(
        CommandRecord(
            id="memory_collector_trace",
            command=_sanitize_command(_sudo_prefix() + collector_cmd),
            cwd=PROJECT,
            returncode=0,
            elapsed_s=duration_s + startup_s,
            stdout_tail=_tail(stdout_path.read_text(encoding="utf-8", errors="replace")),
            stderr_tail=_tail(stderr_path.read_text(encoding="utf-8", errors="replace")),
        )
    )
    rss_stats = _stats(rss_samples)
    steady_samples = rss_samples[len(rss_samples) // 2 :] or rss_samples
    first_half = rss_samples[: len(rss_samples) // 2] or rss_samples
    second_half = steady_samples
    elapsed_min = max(duration_s / 120.0, 1e-9)
    growth_rate = (statistics.mean(second_half) - statistics.mean(first_half)) / elapsed_min
    run = {
        "run_id": "memory_process_trace_1",
        "duration_s": duration_s,
        "sample_interval_s": sample_interval_s,
        "sample_count": len(rss_samples),
        "mean_rss_mb": rss_stats["mean"],
        "median_rss_mb": rss_stats["median"],
        "p95_rss_mb": rss_stats["p95"],
        "peak_rss_mb": rss_stats["max"],
        "steady_state_rss_mb": statistics.mean(steady_samples),
        "growth_rate_mb_per_min": growth_rate,
        "timestamps_s": timestamps_s,
        "rss_samples_mb": rss_samples,
        "raw_log": _rel(log_path),
        "collector_stdout": _rel(stdout_path),
        "collector_stderr": _rel(stderr_path),
    }
    return {
        "status": "completed",
        "methodology": "RSS sampling of process-only trace collector under repeated file_loop load",
        "scope": "single-host steady-state RSS microbenchmark",
        "runs": [run],
        "summary": {
            "rss_mb": rss_stats,
            "peak_rss_mb": rss_stats["max"],
            "steady_state_rss_mb": run["steady_state_rss_mb"],
            "growth_rate_mb_per_min": growth_rate,
        },
    }


def run_time_to_detect(
    *,
    runs: int,
    startup_s: float,
    sudo_password: str,
    command_log: list[CommandRecord],
) -> dict[str, Any]:
    log_path = RAW_DIR / "ttd_process_runner.trace.log"
    stdout_path = RAW_DIR / "ttd_process_runner.collector.stdout.log"
    stderr_path = RAW_DIR / "ttd_process_runner.collector.stderr.log"
    collector_cmd = [
        str(COLLECTOR_BIN),
        "process",
        "--log-file",
        str(log_path),
        "--",
        "-c",
        "file_loop",
    ]
    rows: list[dict[str, Any]] = []
    with _collector_process(
        command=collector_cmd,
        password=sudo_password,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        startup_s=startup_s,
        marker=str(log_path),
    ):
        stdout_offset = stdout_path.stat().st_size if stdout_path.exists() else 0
        for index in range(runs):
            target_cmd = [str(BENCH_DIR / "file_loop"), "25"]
            action_start_ns = time.monotonic_ns()
            target_proc = subprocess.Popen(target_cmd, cwd=str(PROJECT))
            first_seen_ns, matched_line = _poll_new_stdout_event(
                stdout_path,
                stdout_offset,
                timeout_s=5.0,
                started_after_ns=action_start_ns,
            )
            observation_source = "process_runner_stdout_json"
            target_proc.wait(timeout=30)
            stdout_offset = stdout_path.stat().st_size if stdout_path.exists() else stdout_offset
            latency_ms = (first_seen_ns - action_start_ns) / 1_000_000.0
            rows.append(
                {
                    "run_id": f"time_to_detect_file_loop_{index + 1}",
                    "target_command": [_rel(Path(target_cmd[0])), target_cmd[1]],
                    "target_iterations": int(target_cmd[1]),
                    "latency_ms": latency_ms,
                    "observation_source": observation_source,
                    "matched_log_line": matched_line,
                    "raw_log": _rel(log_path),
                    "collector_stdout": _rel(stdout_path),
                    "collector_stderr": _rel(stderr_path),
                }
            )
            time.sleep(0.2)

    command_log.append(
        CommandRecord(
            id="time_to_detect_process_runner",
            command=_sanitize_command(_sudo_prefix() + collector_cmd),
            cwd=PROJECT,
            returncode=0,
            elapsed_s=startup_s,
            stdout_tail=_tail(stdout_path.read_text(encoding="utf-8", errors="replace")),
            stderr_tail=_tail(stderr_path.read_text(encoding="utf-8", errors="replace")),
        )
    )
    latencies = [row["latency_ms"] for row in rows]
    return {
        "status": "completed",
        "methodology": (
            "wall-clock latency from file_loop launch to first flushed low-level process-runner "
            "JSON observation"
        ),
        "scope": (
            "first low-level process eBPF runner event visibility for a local file_loop target; "
            "not trace-mode ResponsibilityRuntime latency and not end-to-end alert adjudication latency"
        ),
        "runs": rows,
        "summary": {"latency_ms": _stats(latencies)},
    }


def _poll_new_log_line(
    log_path: Path,
    offset: int,
    *,
    timeout_s: float,
    must_contain: str | None = None,
) -> tuple[int, str]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if log_path.exists() and log_path.stat().st_size > offset:
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                for line in f:
                    stripped = line.strip()
                    if stripped and (must_contain is None or must_contain in stripped):
                        return time.monotonic_ns(), _tail(stripped, 1000)
        time.sleep(0.005)
    raise BenchmarkError(f"timed out waiting for a new log line in {_rel(log_path)}")


def _poll_new_stdout_event(
    stdout_path: Path,
    offset: int,
    *,
    timeout_s: float,
    started_after_ns: int,
) -> tuple[int, str]:
    deadline = time.monotonic() + timeout_s
    wanted_events = {
        "EXEC",
        "PROCESS_EXEC",
        "FILE_OPEN",
        "FILE_WRITE",
        "FILE_DELETE",
        "FILE_TRUNCATE",
        "FILE_RENAME",
    }
    while time.monotonic() < deadline:
        if stdout_path.exists() and stdout_path.stat().st_size > offset:
            with stdout_path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                for line in f:
                    stripped = line.strip()
                    if not stripped.startswith("{"):
                        continue
                    try:
                        event = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if event.get("source") != "process":
                        continue
                    data = event.get("data")
                    if not isinstance(data, dict):
                        continue
                    event_timestamp = data.get("timestamp") or event.get("timestamp_ns")
                    if isinstance(event_timestamp, (int, float)) and event_timestamp < started_after_ns:
                        continue
                    if data.get("event") in wanted_events:
                        return time.monotonic_ns(), _tail(stripped, 1000)
        time.sleep(0.005)
    raise BenchmarkError(f"timed out waiting for a new stdout event in {_rel(stdout_path)}")


def build_artifact(
    *,
    skip_build: bool,
    probe_runs: int,
    throughput_runs: int,
    throughput_event_counts: list[int],
    memory_duration_s: float,
    memory_sample_interval_s: float,
    ttd_runs: int,
    collector_startup_s: float,
) -> dict[str, Any]:
    PERF_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    command_log: list[CommandRecord] = []
    sudo_password = _sudo_password(required=True)
    assert sudo_password is not None

    _ensure_binaries(skip_build=skip_build, command_log=command_log)
    _build_targets(command_log)

    experiments = {
        "probe_latency": run_probe_latency(
            runs=probe_runs,
            startup_s=collector_startup_s,
            sudo_password=sudo_password,
            command_log=command_log,
        ),
        "throughput": run_throughput(
            runs=throughput_runs,
            event_counts=throughput_event_counts,
            command_log=command_log,
        ),
        "memory": run_memory(
            duration_s=memory_duration_s,
            sample_interval_s=memory_sample_interval_s,
            startup_s=collector_startup_s,
            sudo_password=sudo_password,
            command_log=command_log,
        ),
        "time_to_detect": run_time_to_detect(
            runs=ttd_runs,
            startup_s=collector_startup_s,
            sudo_password=sudo_password,
            command_log=command_log,
        ),
    }

    artifact = {
        "schema": "mtagentlens.performance_results.v1",
        "status": "completed",
        "generated_at": _now_iso(),
        "provenance": {
            "git_commit": _git_commit(),
            "git_dirty": _git_dirty(),
            "kernel": platform.platform(),
            "machine": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "python": platform.python_version(),
            },
            "collector_binary": _rel(COLLECTOR_BIN),
            "collector_binary_hash": _sha256(COLLECTOR_BIN),
            "throughput_binary": _rel(THROUGHPUT_BIN),
            "throughput_binary_hash": _sha256(THROUGHPUT_BIN),
            "commands": [" ".join(record.command) for record in command_log],
        },
        "claim_boundary": {
            "supported": [
                "local process/file collector wall-clock overhead microbenchmarks",
                "synthetic FakeRunner analyzer throughput",
                "single-host collector RSS under repeated file events",
                "first-observation latency for low-level process-runner JSON events",
            ],
            "not_supported": [
                "production LLM-agent end-to-end latency",
                "trace-mode ResponsibilityRuntime end-to-end latency",
                "kernel ring-buffer saturation or loss under adversarial load",
                "TLS payload capture overhead for real provider traffic",
                "semantic observer sidecar latency",
                "cross-machine performance generalization",
            ],
        },
        "experiments": experiments,
        "command_records": [record.as_artifact() for record in command_log],
    }
    _assert_completed_artifact(artifact)
    return artifact


def _assert_completed_artifact(artifact: dict[str, Any]) -> None:
    required = {"probe_latency", "throughput", "memory", "time_to_detect"}
    missing = required - set(artifact.get("experiments", {}))
    if missing:
        raise BenchmarkError(f"missing experiments: {sorted(missing)}")
    for name in sorted(required):
        experiment = artifact["experiments"][name]
        if experiment.get("status") != "completed":
            raise BenchmarkError(f"{name} is not completed")
        if not experiment.get("runs"):
            raise BenchmarkError(f"{name} has no runs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true", help="Use existing release binaries")
    parser.add_argument("--probe-runs", type=int, default=3)
    parser.add_argument("--throughput-runs", type=int, default=3)
    parser.add_argument("--throughput-event-counts", type=str, default="1000,3000")
    parser.add_argument("--memory-duration-s", type=float, default=20.0)
    parser.add_argument("--memory-sample-interval-s", type=float, default=1.0)
    parser.add_argument("--ttd-runs", type=int, default=5)
    parser.add_argument("--collector-startup-s", type=float, default=3.0)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    event_counts = [int(item) for item in args.throughput_event_counts.split(",") if item.strip()]
    if not event_counts:
        raise BenchmarkError("--throughput-event-counts must contain at least one integer")
    artifact = build_artifact(
        skip_build=args.skip_build,
        probe_runs=args.probe_runs,
        throughput_runs=args.throughput_runs,
        throughput_event_counts=event_counts,
        memory_duration_s=args.memory_duration_s,
        memory_sample_interval_s=args.memory_sample_interval_s,
        ttd_runs=args.ttd_runs,
        collector_startup_s=args.collector_startup_s,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {_rel(args.output)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
