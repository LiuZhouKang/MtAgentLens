# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Real process-eBPF -> runtime -> SQLite load, with no model or TLS calls.

Visibility latency is an upper bound from a controlled open's start until a
poll sees its committed action row. It includes batching and polling, and is
not alert latency. Process-only inputs need not create intent correlation edges.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_plan(binary, out, concurrency, operations, interval_ms):
    binary = Path(binary).resolve()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("collector binary must exist and be executable")
    if not concurrency or any(type(n) is not int or n < 1 or n > 32 for n in concurrency) or len(set(concurrency)) != len(concurrency):
        raise ValueError("concurrency must contain distinct integers between 1 and 32")
    if operations < 1 or interval_ms < 1:
        raise ValueError("operations and interval-ms must be positive")
    return {"schema": "mtagentlens.system-load/1", "scope": "process_kernel_runtime_persistence_load",
        "binary": str(binary), "binary_sha256": sha256(binary), "tool_sha256": sha256(__file__),
        "out": str(Path(out).resolve()), "kernel": platform.release(), "machine": platform.machine(),
        "concurrency": concurrency, "operations_per_worker": operations, "sample_interval_ms": interval_ms,
        "operation_delay_ms": 10, "observer_enabled": False, "tls_enabled": False,
        "limits": ["controlled file opens; no LLM-agent or TLS workload", "no semantic Observer costs",
            "no alert-latency claim", "sampled process-tree RSS excludes kernel BPF map memory",
            "sampled CPU time is a lower bound for children that exit between samples",
            "producer has no generated-event denominator; report measured drop counts and controlled-operation missing fraction",
            "process-only inputs may produce an empty intent-correlation graph"]}


def telemetry(quality):
    try:
        sources = quality["sources"]
        analysis = quality["analysis"]
        if not isinstance(sources, dict) or not sources or any(not name.startswith("Process:") for name in sources):
            raise ValueError("expected only process telemetry")
        healthy = True
        lost = limited = 0
        for source in sources.values():
            for key in ("lost", "rate_limited", "exit_status"):
                if type(source[key]) is not int or source[key] < 0:
                    raise ValueError("invalid producer telemetry")
            for key in ("final_stats_seen", "drain_complete", "initial_snapshot_complete"):
                if type(source[key]) is not bool:
                    raise ValueError("invalid producer telemetry")
                healthy &= source[key]
            for key in ("stdout_skipped",):
                if type(source[key]) is not int or source[key] < 0:
                    raise ValueError("invalid parser telemetry")
                healthy &= source[key] == 0
            for key in ("stdout_error", "stderr_error"):
                if type(source[key]) is not bool:
                    raise ValueError("invalid parser telemetry")
                healthy &= not source[key]
            healthy &= source["lost"] == source["rate_limited"] == source["exit_status"] == 0
            healthy &= source.get("forced_kill") is not True
            lost += source["lost"]
            limited += source["rate_limited"]
        counters = {}
        for key in ("persist_dropped", "persist_closed", "persist_failures", "raw_log_failures"):
            value = analysis[key]
            if type(value) is not int or value < 0:
                raise ValueError("invalid persistence telemetry")
            counters[key] = value
            healthy &= value == 0
        if type(analysis["persist_flush_failed"]) is not bool or type(analysis["event_counts"]["process"]) is not int:
            raise ValueError("invalid final telemetry")
        healthy &= not analysis["persist_flush_failed"] and analysis["event_counts"]["process"] > 0
        return {"healthy": bool(healthy), "lost_events": lost, "rate_limited_events": limited,
            "persistence": counters, "capture_quality": quality}
    except (KeyError, TypeError) as error:
        raise ValueError(f"missing or invalid telemetry: {error}") from error


def visibility(operations, first_seen):
    values = []
    missing = []
    for operation in operations:
        if operation["path"] not in first_seen:
            missing.append(operation["path"])
            continue
        elapsed = first_seen[operation["path"]] - operation["started_ns"]
        if elapsed < 0:
            raise ValueError("visibility clock moved backwards")
        values.append(elapsed / 1_000_000)
    ordered = sorted(values)
    return {"metric": "operation_start_to_first_observed_committed_action_upper_bound",
        "observed_operations": len(values), "missing_operations": missing,
        "expected_operations": len(operations),
        "controlled_operation_missing_fraction": len(missing) / len(operations) if operations else None,
        "latency_ms": None if not values else {"min": ordered[0], "max": ordered[-1],
            "mean": sum(values) / len(values), "p50": ordered[(len(values) - 1) // 2],
            "p95": ordered[round((len(values) - 1) * .95)]}}


def parse_proc_stat(text):
    prefix, fields = text.rsplit(")", 1)
    parts = fields.split()
    return {"pid": int(prefix.split("(", 1)[0]), "ppid": int(parts[1]),
        "ticks": int(parts[11]) + int(parts[12]), "start_ticks": int(parts[19]), "rss_pages": int(parts[21])}


class Sampler:
    def __init__(self, pid):
        self.pid = pid
        self.samples = []
        self.ticks = {}

    def sample(self):
        rows = {}
        for path in Path("/proc").glob("[0-9]*/stat"):
            try:
                row = parse_proc_stat(path.read_text())
                rows[row["pid"]] = row
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
        selected = {self.pid}
        while True:
            expanded = selected | {pid for pid, row in rows.items() if row["ppid"] in selected}
            if expanded == selected:
                break
            selected = expanded
        tree = [rows[pid] for pid in selected if pid in rows]
        for row in tree:
            key = (row["pid"], row["start_ticks"])
            self.ticks[key] = max(self.ticks.get(key, 0), row["ticks"])
        self.samples.append({"monotonic_ns": time.monotonic_ns(), "processes": len(tree),
            "rss_bytes_sum": sum(max(0, row["rss_pages"]) for row in tree) * os.sysconf("SC_PAGE_SIZE"),
            "cpu_seconds_lower_bound": sum(self.ticks.values()) / os.sysconf("SC_CLK_TCK")})


def committed_paths(database, first_seen):
    if not database.is_file():
        return
    try:
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=.05) as conn:
            rows = conn.execute("SELECT normalized_args_json FROM actions WHERE action_type='FileOpen' AND outcome='succeeded'").fetchall()
        seen_at = time.time_ns()
        for (raw,) in rows:
            for arg in json.loads(raw):
                if isinstance(arg, dict) and isinstance(arg.get("Path"), str):
                    first_seen.setdefault(arg["Path"], seen_at)
    except sqlite3.OperationalError as error:
        if "no such table" not in str(error) and "locked" not in str(error):
            raise


def finalized_database(database):
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("collector database integrity failed")
        runs = conn.execute("SELECT run_id, ended_at_ns FROM runs").fetchall()
        if len(runs) != 1 or not isinstance(runs[0][1], int):
            raise ValueError("collector did not finalize exactly one run")
        rows = conn.execute("SELECT version,payload_json FROM analysis_checkpoints WHERE run_id=? AND checkpoint_kind='frozen-analysis'", (runs[0][0],)).fetchall()
        if len(rows) != 1 or rows[0][0] != 1:
            raise ValueError("missing finalized analysis checkpoint")
        from tools.annotator.payload_codec import decode_checkpoint_payload
        checkpoint = json.loads(decode_checkpoint_payload(rows[0][1]))
        if checkpoint.get("run_id") != runs[0][0] or checkpoint.get("contract") != "frozen-evidence-reanalysis-v1":
            raise ValueError("invalid finalized analysis checkpoint")
        counts = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("actions", "intents", "correlation_evidence", "graph_nodes", "graph_edges")}
    configuration = checkpoint["classification_config"]
    return {"run_id": runs[0][0], "db_counts": counts,
        "analysis_implementation": checkpoint["implementation"],
        "classification_config": configuration,
        "classification_config_sha256": hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest(),
        **telemetry(checkpoint["capture_quality"])}


def worker(comm, directory, operations):
    if ctypes.CDLL(None).prctl(15, comm.encode(), 0, 0, 0) != 0:
        raise RuntimeError("cannot set isolated process comm")
    directory = Path(directory)
    directory.mkdir()
    print(json.dumps({"ready": os.getpid()}), flush=True)
    if sys.stdin.readline().strip() != "go":
        raise RuntimeError("workload barrier was not released")
    for index in range(operations):
        path = directory / f"op-{index}"
        started = time.time_ns()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            payload = b"synthetic system-load payload\n"
            if os.write(fd, payload) != len(payload):
                raise RuntimeError("short workload write")
        finally:
            os.close(fd)
        print(json.dumps({"path": str(path), "pid": os.getpid(), "started_ns": started,
            "completed_ns": time.time_ns()}), flush=True)
        time.sleep(.01)


def execute_one(plan, concurrency):
    output = Path(plan["out"]) / f"concurrency-{concurrency}"
    output.mkdir(parents=True, exist_ok=False)
    stdout_path, stderr_path = output / "collector.stdout", output / "collector.stderr"
    log_path = output / "trace.log"
    database = output / "trace.log.responsibility.db"
    workers = []
    collector = None
    first_seen = {}
    operations = []
    sampler = None
    result = {"concurrency": concurrency, "status": "failed"}
    try:
        with tempfile.TemporaryDirectory(prefix="mtl-load-") as temporary, stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
            workspace = Path(temporary)
            comm = "mtll-" + workspace.name[-8:]
            # Keep workers behind a barrier until the real kernel snapshot is complete.
            for index in range(concurrency):
                proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_worker", comm,
                    str(workspace / f"w{index}"), str(plan["operations_per_worker"])],
                    cwd=workspace, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                workers.append(proc)
            import select
            for proc in workers:
                if not select.select([proc.stdout], [], [], 10)[0] or not json.loads(proc.stdout.readline()).get("ready"):
                    raise ValueError("workload did not reach its barrier")
            command = [plan["binary"], "trace", "--ssl=false", "--process=true", "--comm", comm,
                "--disable-responsibility-observer", "--quiet", "-o", str(log_path)]
            environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "RUST_LOG": "info",
                "MTAGENTLENS_AGENT_COMMS": comm, "MTAGENTLENS_WORKSPACE_ROOT": str(workspace)}
            result.update({"command": command, "environment": environment,
                "execution_config_sha256": hashlib.sha256(json.dumps({"command": command, "environment": environment}, sort_keys=True).encode()).hexdigest()})
            collector = subprocess.Popen(command, cwd=workspace, env=environment,
                stdout=stdout, stderr=stderr, start_new_session=True)
            sampler = Sampler(collector.pid)
            deadline = time.monotonic() + 20
            while True:
                sampler.sample()
                if collector.poll() is not None:
                    raise ValueError("collector exited before readiness")
                if '"event":"PROCESS_SNAPSHOT_STATS","complete":true' in stderr_path.read_text(errors="replace"):
                    break
                if time.monotonic() > deadline:
                    raise ValueError("kernel process snapshot readiness timed out")
                time.sleep(plan["sample_interval_ms"] / 1000)
            for proc in workers:
                proc.stdin.write("go\n")
                proc.stdin.close()
                proc.stdin = None
            # Drain pipes while sampling so a full worker pipe cannot cap throughput.
            import threading
            errors = []
            def collect(proc):
                try:
                    for line in proc.stdout:
                        operations.append(json.loads(line))
                except (ValueError, OSError) as error:
                    errors.append(str(error))
            threads = [threading.Thread(target=collect, args=(proc,)) for proc in workers]
            for thread in threads:
                thread.start()
            deadline = time.monotonic() + plan["operations_per_worker"] * .01 + 30
            while any(proc.poll() is None for proc in workers):
                if collector.poll() is not None or time.monotonic() > deadline:
                    raise ValueError("collector/workload exited or exceeded its deadline")
                sampler.sample()
                committed_paths(database, first_seen)
                time.sleep(plan["sample_interval_ms"] / 1000)
            for thread in threads:
                thread.join(timeout=2)
            if errors or any(thread.is_alive() for thread in threads) or any(proc.returncode != 0 for proc in workers):
                raise ValueError(f"controlled workload failed: {errors}")
            if len(operations) != concurrency * plan["operations_per_worker"]:
                raise ValueError("controlled workload ledger is incomplete")
            # Flush the final committed action rows before freezing the verdict.
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                sampler.sample()
                committed_paths(database, first_seen)
                time.sleep(plan["sample_interval_ms"] / 1000)
            collector.send_signal(signal.SIGINT)
            deadline = time.monotonic() + 20
            while collector.poll() is None:
                sampler.sample()
                committed_paths(database, first_seen)
                if time.monotonic() > deadline:
                    raise ValueError("collector failed to finalize within 20 seconds")
                time.sleep(plan["sample_interval_ms"] / 1000)
            committed_paths(database, first_seen)
            if collector.returncode != 0:
                raise ValueError(f"collector exited with status {collector.returncode}")
            result.update(finalized_database(database))
            result["visibility"] = visibility(operations, first_seen)
            result["status"] = "completed" if result["healthy"] and not result["visibility"]["missing_operations"] else "degraded"
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError) as error:
        result["error"] = str(error)
    finally:
        if collector and collector.poll() is None:
            collector.send_signal(signal.SIGINT)
            try:
                collector.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(collector.pid, signal.SIGKILL)
                collector.wait()
                result["forced_kill"] = True
        worker_status = []
        for proc in workers:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            worker_status.append({"pid": proc.pid, "returncode": proc.returncode,
                "stderr": proc.stderr.read()})
            for handle in (proc.stdin, proc.stdout, proc.stderr):
                if handle:
                    handle.close()
        result["workers"] = worker_status
        if sampler and sampler.samples:
            result["resource_samples"] = sampler.samples
            result["sampled_peak_process_tree_rss_bytes"] = max(row["rss_bytes_sum"] for row in sampler.samples)
            result["sampled_process_tree_cpu_seconds_lower_bound"] = sampler.samples[-1]["cpu_seconds_lower_bound"]
            elapsed = (sampler.samples[-1]["monotonic_ns"] - sampler.samples[0]["monotonic_ns"]) / 1_000_000_000
            result["sampled_average_cpu_cores_lower_bound"] = (
                (sampler.samples[-1]["cpu_seconds_lower_bound"] - sampler.samples[0]["cpu_seconds_lower_bound"]) / elapsed
                if elapsed > 0 else None)
        (output / "operations.json").write_text(json.dumps(operations, sort_keys=True) + "\n")
        (output / "first-visible.json").write_text(json.dumps(first_seen, sort_keys=True) + "\n")
        result["artifacts_sha256"] = {path.name: sha256(path) for path in output.iterdir() if path.is_file()}
        (output / "measurement.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "collector/build/release/mtagentlens")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--concurrency", default="1,4,8,16,32")
    parser.add_argument("--operations", type=int, default=100)
    parser.add_argument("--interval-ms", type=int, default=100)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        plan = make_plan(args.binary, args.out, [int(value) for value in args.concurrency.split(",")], args.operations, args.interval_ms)
        plan["executed"] = args.execute
        if args.execute:
            if os.geteuid() != 0:
                raise ValueError("real kernel capture requires running this tool as root; dry-run needs no root")
            args.out.mkdir(parents=True, exist_ok=False)
            plan["measurements"] = [execute_one(plan, concurrency) for concurrency in plan["concurrency"]]
            plan["status"] = "completed" if all(row["status"] == "completed" for row in plan["measurements"]) else "failed"
            (args.out / "report.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
        print(json.dumps(plan, indent=2, sort_keys=True))
        return int(plan.get("status") == "failed")
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "_worker":
        worker(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    else:
        raise SystemExit(main())
