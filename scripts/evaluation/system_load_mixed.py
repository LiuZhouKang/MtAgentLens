# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Real process+TLS eBPF -> runtime -> SQLite load, with no model calls.

Each worker is the committed ``paced_ssl`` workload
(``compare/results/overhead/raw/steady/paced_ssl.c``): every 100 ms one local
TLS 1.2 echo over in-process memory BIOs (two writes + two reads), one
controlled file open, and every tenth iteration one ``/bin/true`` exec. There is
no socket, certificate, network traffic or LLM request.

N workers run concurrently behind a readiness barrier; one collector captures
``--ssl=true --process=true --comm <comm>`` with the responsibility runtime and
without the semantic Observer. The table therefore describes the real kernel
capture -> attribution runtime -> SQLite path for a paced mixed syscall load,
not a real LLM Agent: no SSE/HTTP model traffic, no Observer, no alert latency.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import select
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.evaluation.system_load import Sampler, committed_paths, sha256

SCHEMA = "mtagentlens.system-load-mixed/1"
DEFAULT_COMM = "paced_ssl"
PHASE_SETTLE_S = 3.0
SHUTDOWN_TIMEOUT_S = 120.0
READY_TIMEOUT_S = 30.0
STARTUP_TIMEOUT_S = 30.0


def collector_command(collector, comm, trace_path):
    """One collector for all workers: comm filter, TLS+process, no Observer."""
    return [str(collector), "trace", "--ssl=true", "--process=true", "--comm", comm,
            "--disable-responsibility-observer", "--quiet", "-o", str(trace_path)]


def expected_operations(concurrency, iterations):
    return [{"path": f"fixture-w{worker}/op-{index:04d}"}
            for worker in range(concurrency) for index in range(iterations)]


def exec_expectation(iterations):
    return iterations // 10


def coverage(operations, first_seen):
    """Controlled-operation coverage without per-operation start timestamps.

    paced_ssl reports only its terminal counters, so this workload cannot claim
    a start-to-visible latency; coverage and the missing fraction are still exact.
    """
    paths = [operation["path"] for operation in operations]
    missing = [path for path in paths if path not in first_seen]
    return {"metric": "controlled_path_present_in_committed_action",
            "observed_operations": len(paths) - len(missing),
            "expected_operations": len(paths), "missing_operations": missing,
            "controlled_operation_missing_fraction": (len(missing) / len(paths)) if paths else None,
            "latency_ms": None,
            "note": "paced_ssl emits no per-operation start timestamp; no latency claim"}


def make_plan(collector, workload, out, concurrency, iterations, interval_ms, comm=DEFAULT_COMM):
    collector = Path(collector).resolve()
    workload = Path(workload).resolve()
    if not collector.is_file() or not os.access(collector, os.X_OK):
        raise ValueError("collector binary must exist and be executable")
    if not workload.is_file() or not os.access(workload, os.X_OK):
        raise ValueError("paced workload must exist and be executable")
    if (not concurrency or any(type(n) is not int or n < 1 or n > 32 for n in concurrency)
            or len(set(concurrency)) != len(concurrency)):
        raise ValueError("concurrency must contain distinct integers between 1 and 32")
    if type(iterations) is not int or not 10 <= iterations <= 6000:
        raise ValueError("iterations must be an integer between 10 and 6000")
    if type(interval_ms) is not int or not 10 <= interval_ms <= 1000:
        raise ValueError("interval-ms must be an integer between 10 and 1000")
    if not comm or "," in comm:
        raise ValueError("comm must be one non-empty name")
    return {
        "schema": SCHEMA,
        "scope": "paced_tls_file_exec_kernel_runtime_persistence_load",
        "collector": str(collector), "collector_sha256": sha256(collector),
        "workload": str(workload), "workload_sha256": sha256(workload),
        "workload_source": str(ROOT / "compare/results/overhead/raw/steady/paced_ssl.c"),
        "workload_source_sha256": sha256(ROOT / "compare/results/overhead/raw/steady/paced_ssl.c"),
        "tool_sha256": sha256(__file__),
        "out": str(Path(out).resolve()),
        "kernel": platform.release(), "machine": platform.machine(),
        "concurrency": concurrency, "iterations_per_worker": iterations,
        "operation_delay_ms": interval_ms, "comm": comm,
        "tls_enabled": True, "process_enabled": True, "observer_enabled": False,
        "operation_mix": {"tls_echo_per_iteration": 1, "file_open_per_iteration": 1,
                          "exec_every_iterations": 10},
        "limits": [
            "local in-process TLS 1.2 echo (memory BIOs); no socket, certificate, network or LLM request",
            "no semantic Observer and no alert-latency claim",
            "mixed workload is not a real LLM Agent: no SSE/HTTP model traffic and no multi-turn intent graph",
            "sampled process-tree RSS/PSS exclude unmapped kernel BPF memory",
            "PSS reads add sampling cost; missing process reads stay null; phase summaries are sampled, not atomic",
            "exec children are executed and validated by the paced workload, but the shared comm filter does not emit their EXEC lines; exec is not asserted as captured",
            "sampled CPU time is a lower bound for children that exit between samples",
            "TLS payload is a fixed 256-byte echo, not HTTP; SSL event counts are not one-per-raw-operation",
            "producer has no generated-event denominator; report measured drop counts and controlled-operation missing fraction",
        ],
    }


def capture_health(quality):
    """Validate Process and SSL producer telemetry from the frozen checkpoint."""
    try:
        sources = quality["sources"]
        analysis = quality["analysis"]
        if not isinstance(sources, dict) or not sources:
            raise ValueError("expected producer telemetry")
        kinds = {name.split(":", 1)[0] for name in sources}
        if kinds != {"Process", "SSL"}:
            raise ValueError(f"expected Process and SSL telemetry, saw {sorted(kinds)}")
        healthy = True
        lost = limited = 0
        process_lost = ssl_lost = 0
        for name, source in sources.items():
            for key in ("lost", "rate_limited", "exit_status"):
                if type(source[key]) is not int or source[key] < 0:
                    raise ValueError("invalid producer telemetry")
            for key in ("final_stats_seen", "drain_complete"):
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
            if name.startswith("Process:"):
                process_lost += source["lost"]
            else:
                ssl_lost += source["lost"]
        counters = {}
        for key in ("persist_dropped", "persist_closed", "persist_failures", "raw_log_failures"):
            value = analysis[key]
            if type(value) is not int or value < 0:
                raise ValueError("invalid persistence telemetry")
            counters[key] = value
            healthy &= value == 0
        if type(analysis["persist_flush_failed"]) is not bool:
            raise ValueError("invalid final telemetry")
        healthy &= not analysis["persist_flush_failed"]
        event_counts = analysis.get("event_counts", {})
        healthy &= event_counts.get("process", 0) > 0 and event_counts.get("ssl", 0) > 0
        return {"healthy": bool(healthy), "lost_events": lost, "rate_limited_events": limited,
                "process_lost_events": process_lost, "ssl_lost_events": ssl_lost,
                "persistence": counters, "event_counts": event_counts,
                "capture_quality": quality}
    except (KeyError, TypeError) as error:
        raise ValueError(f"missing or invalid telemetry: {error}") from error


def finalized_database(database):
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("collector database integrity failed")
        runs = conn.execute("SELECT run_id, ended_at_ns FROM runs").fetchall()
        if len(runs) != 1 or not isinstance(runs[0][1], int):
            raise ValueError("collector did not finalize exactly one run")
        rows = conn.execute(
            "SELECT version,payload_json FROM analysis_checkpoints WHERE run_id=?"
            " AND checkpoint_kind='frozen-analysis'", (runs[0][0],)).fetchall()
        if len(rows) != 1 or rows[0][0] != 1:
            raise ValueError("missing finalized analysis checkpoint")
        from tools.annotator.payload_codec import decode_checkpoint_payload
        checkpoint = json.loads(decode_checkpoint_payload(rows[0][1]))
        if checkpoint.get("run_id") != runs[0][0] or checkpoint.get("contract") != "frozen-evidence-reanalysis-v1":
            raise ValueError("invalid finalized analysis checkpoint")
        counts = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("actions", "intents", "correlation_evidence",
                                "graph_nodes", "graph_edges")}
    health = capture_health(checkpoint["capture_quality"])
    return {"run_id": runs[0][0], "db_counts": counts,
            "analysis_config_sha256": sha256_bytes(json.dumps(
                checkpoint["classification_config"], sort_keys=True).encode()),
            **health}


def sha256_bytes(value):
    import hashlib
    return hashlib.sha256(value).hexdigest()


def trace_counters(path):
    """Count raw trace lines by source and EXEC events from executed children."""
    counts = {"lines": 0, "sources": {}, "exec_events": 0, "exec_true": 0}
    with Path(path).open(errors="replace") as handle:
        for line in handle:
            counts["lines"] += 1
            try:
                event = json.loads(line)
            except ValueError:
                continue
            source = event.get("source", "?")
            counts["sources"][source] = counts["sources"].get(source, 0) + 1
            if event.get("event") == "EXEC":
                counts["exec_events"] += 1
                if event.get("comm") == "true":
                    counts["exec_true"] += 1
    return counts


def execute_one(plan, concurrency):
    output = Path(plan["out"]) / f"concurrency-{concurrency}"
    output.mkdir(parents=True, exist_ok=False)
    stdout_path, stderr_path = output / "collector.stdout", output / "collector.stderr"
    trace_path = output / "trace.log"
    database = output / "trace.log.responsibility.db"
    workers = []
    worker_outputs = {}
    collector = None
    sampler = None
    first_seen = {}
    result = {"concurrency": concurrency, "status": "failed"}
    try:
        fixtures = []
        for worker in range(concurrency):
            fixture = output / f"fixture-w{worker}"
            fixture.mkdir()
            fixtures.append(fixture)
        # tls_capture resolves libssl through ldconfig, which lives in sbin dirs
        # that os.defpath omits; keep a fixed PATH that contains it.
        environment = {"PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8"}
        for worker, fixture in enumerate(fixtures):
            process = subprocess.Popen(
                [plan["workload"], str(plan["iterations_per_worker"]), "--gate"],
                env={**environment, "MTL_CONTROLLED_DIR": str(fixture)},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=(output / f"worker-{worker}.stderr").open("w"),
                text=True, start_new_session=True)
            workers.append(process)
        for worker, process in enumerate(workers):
            if not select.select([process.stdout], [], [], STARTUP_TIMEOUT_S)[0]:
                raise ValueError(f"worker {worker} did not reach its readiness gate")
            ready = json.loads(process.stdout.readline())
            if not ready.get("ready") or ready.get("handshake") != "ok":
                raise ValueError(f"worker {worker} handshake or gate failed")
            worker_outputs[worker] = ready
        command = collector_command(plan["collector"], plan["comm"], trace_path)
        collector = subprocess.Popen(
            command, cwd=output, env={**environment, "RUST_LOG": "info",
                                     "MTAGENTLENS_AGENT_COMMS": plan["comm"]},
            stdout=stdout_path.open("w"), stderr=stderr_path.open("w"), start_new_session=True)
        result.update({"command": command, "workers": len(workers)})
        sampler = Sampler(collector.pid)
        deadline = time.monotonic() + READY_TIMEOUT_S
        while True:
            sampler.sample()
            if collector.poll() is not None:
                raise ValueError("collector exited before readiness")
            text = stderr_path.read_text(errors="replace")
            if ("TLS_CAPTURE_PROBES" in text and '"complete":true' in text
                    and "PROCESS_SNAPSHOT_STATS" in text):
                break
            if time.monotonic() > deadline:
                raise ValueError("TLS/process readiness timed out")
            time.sleep(plan["operation_delay_ms"] / 1000)
        sampler.mark_phase("workload")
        for process in workers:
            process.stdin.write("GO\n")
            process.stdin.flush()
        summaries = {}
        errors = []

        def collect(worker, process):
            # paced_ssl --gate waits for a final EXIT line after its summary; a
            # closed stdin would make it exit nonzero instead of reporting ok.
            try:
                for line in process.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    summaries[worker] = json.loads(line)
                    if summaries[worker].get("status") == "ok" and process.stdin is not None:
                        process.stdin.write("EXIT\n")
                        process.stdin.flush()
                        process.stdin.close()
                        process.stdin = None
            except (ValueError, OSError) as error:
                errors.append(f"worker {worker}: {error}")

        threads = [threading.Thread(target=collect, args=(w, p)) for w, p in enumerate(workers)]
        for thread in threads:
            thread.start()
        # paced_ssl paces one iteration per 100 ms; interval_ms only paces sampling.
        expected_active = plan["iterations_per_worker"] * 0.1
        deadline = time.monotonic() + expected_active + 30
        while any(process.poll() is None for process in workers):
            if collector.poll() is not None or time.monotonic() > deadline:
                raise ValueError("collector/workload exited or exceeded its deadline")
            sampler.sample()
            committed_paths(database, first_seen)
            time.sleep(plan["operation_delay_ms"] / 1000)
        for thread in threads:
            thread.join(timeout=2)
        if errors or any(thread.is_alive() for thread in threads):
            raise ValueError(f"controlled workload failed: {errors}")
        if any(process.returncode != 0 for process in workers):
            raise ValueError("controlled workload exited nonzero")
        if len(summaries) != concurrency:
            raise ValueError("controlled TLS ledger is incomplete")
        for worker, summary in summaries.items():
            if summary.get("status") != "ok" or summary.get("handshake") != "ok":
                raise ValueError(f"worker {worker} did not complete its TLS echo loop")
            if summary.get("iterations") != plan["iterations_per_worker"]:
                raise ValueError(f"worker {worker} iteration count mismatch")
            if summary.get("ssl_operation_count") != 4 * plan["iterations_per_worker"]:
                raise ValueError(f"worker {worker} SSL operation count mismatch")
        operations = expected_operations(concurrency, plan["iterations_per_worker"])
        for worker, fixture in enumerate(fixtures):
            for index in range(plan["iterations_per_worker"]):
                operations[worker * plan["iterations_per_worker"] + index]["path"] = str(
                    fixture / f"op-{index:04d}")
        sampler.mark_phase("settle")
        deadline = time.monotonic() + PHASE_SETTLE_S
        while time.monotonic() < deadline:
            sampler.sample()
            committed_paths(database, first_seen)
            time.sleep(plan["operation_delay_ms"] / 1000)
        sampler.mark_phase("shutdown")
        collector.send_signal(signal.SIGINT)
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT_S
        while collector.poll() is None:
            sampler.sample()
            if time.monotonic() > deadline:
                os.killpg(collector.pid, signal.SIGKILL)
                raise ValueError("collector did not finalize within its shutdown deadline")
            time.sleep(plan["operation_delay_ms"] / 1000)
        committed_paths(database, first_seen)
        if collector.returncode != 0:
            raise ValueError(f"collector exited with status {collector.returncode}")
        if not database.is_file():
            raise ValueError("collector wrote no responsibility database")
        result.update(finalized_database(database))
        result["visibility"] = coverage(operations, first_seen)
        result["trace_counters"] = trace_counters(trace_path)
        expected_exec = concurrency * exec_expectation(plan["iterations_per_worker"])
        result["exec_expected"] = expected_exec
        result["exec_observed"] = result["trace_counters"]["exec_true"]
        exec_captured = result["exec_observed"] >= expected_exec
        result["exec_capture_asserted"] = exec_captured
        result["status"] = "completed" if (
            result["healthy"]
            and not result["visibility"]["missing_operations"]
            and result["trace_counters"]["sources"].get("ssl", 0) > 0) else "degraded"
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError) as error:
        result["error"] = str(error)
    finally:
        if collector and collector.poll() is None:
            collector.send_signal(signal.SIGINT)
            try:
                collector.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(collector.pid, signal.SIGKILL)
                collector.wait()
                result["forced_kill"] = True
        for process in workers:
            if process.poll() is None:
                process.kill()
            process.wait()
            for handle in (process.stdin, process.stdout):
                if handle:
                    handle.close()
        if sampler is not None and sampler.samples:
            result["resource_samples"] = sampler.samples
            result["resource_phases"] = sampler.phase_summary()
            result["sampled_peak_process_tree_rss_bytes"] = max(
                row["rss_bytes_sum"] for row in sampler.samples)
            result["sampled_process_tree_cpu_seconds_lower_bound"] = sampler.samples[-1][
                "cpu_seconds_lower_bound"]
            elapsed = (sampler.samples[-1]["monotonic_ns"] - sampler.samples[0]["monotonic_ns"]) / 1e9
            result["sampled_average_cpu_cores_lower_bound"] = (
                (sampler.samples[-1]["cpu_seconds_lower_bound"]
                 - sampler.samples[0]["cpu_seconds_lower_bound"]) / elapsed if elapsed > 0 else None)
        (output / "measurement.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "collector/build/release/mtagentlens")
    parser.add_argument("--workload", type=Path,
                        default=ROOT / "compare/results/overhead/raw/steady/paced_ssl")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--concurrency", default="1,4,8,16,32")
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--interval-ms", type=int, default=100)
    parser.add_argument("--comm", default=DEFAULT_COMM)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = make_plan(args.binary, args.workload, args.out,
                         [int(value) for value in args.concurrency.split(",")],
                         args.iterations, args.interval_ms, args.comm)
        plan["executed"] = args.execute
        if args.execute:
            if os.geteuid() != 0:
                raise ValueError("real kernel capture requires running this tool as root; dry-run needs no root")
            args.out.mkdir(parents=True, exist_ok=False)
            plan["measurements"] = [execute_one(plan, level) for level in plan["concurrency"]]
            plan["status"] = "completed" if all(
                row["status"] == "completed" for row in plan["measurements"]) else "failed"
            (args.out / "report.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
        print(json.dumps(plan, indent=2, sort_keys=True))
        return int(plan.get("status") == "failed")
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
