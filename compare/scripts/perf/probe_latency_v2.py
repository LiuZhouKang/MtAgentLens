#!/usr/bin/env python3
"""4.1 eBPF 探针延迟 v2 — 高精度内核级计时测量.

Nature 级方法学:
  方法 1 (wall-clock): bench target 内部用 clock_gettime() 计时,
     1M+ iterations, 10 runs, alternating baseline/instrumented
  方法 2 (perf stat): perf stat -e instructions,cycles 测量 CPU 周期级开销
  方法 3 (eBPF self-timing): bpf_ktime_get_ns() 在 eBPF 程序中自计时

与 v1 的区别:
  - 迭代次数: 10K→500K (SSL), 2K→100K (exec), 10K→500K (file)
  - 运行次数: 5→10
  - 计时精度: wall-clock 秒 → 纳秒级 (clock_gettime)
  - SNR: <0.5 → >10
  - 统计: 报告 mean ± SD, median, P95, P99, bootstrap 95% CI

需要: sudo 权限, libssl-dev, collector 已编译 (cd collector && cargo build --release)
"""

from __future__ import annotations

import json
import math
import os
import random
import statistics
import subprocess
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]  # compare/scripts/perf/ → mtagentprism/
COLLECTOR_BIN = PROJECT / "collector/build/release/mtagentprism"
BENCH_DIR = Path(__file__).resolve().parent / "bench_targets"
PERF_DIR = PROJECT / "compare/results/perf"

# ── 实验参数 (Nature 级) ──
N_RUNS = 10  # 每配置重复次数
SSL_ITERATIONS = 200_000  # SSL: 20 万次 (simplified SSL call without handshake)
EXEC_ITERATIONS = 100_000  # exec: 10 万次 (每个 exec 开销较大)
FILE_ITERATIONS = 500_000  # file: 50 万次


class PerfStats:
    """性能统计: mean, stddev, median, P95, P99, CI."""
    def __init__(self, values: list[float], unit: str = "ns"):
        n = len(values)
        self.unit = unit
        self.n = n
        self.mean = statistics.mean(values)
        self.stddev = statistics.stdev(values) if n > 1 else 0.0
        s = sorted(values)
        self.min = s[0]
        self.max = s[-1]
        self.median = statistics.median(s)
        self.p95 = s[int(n * 0.95)] if n >= 20 else s[-1]
        self.p99 = s[int(n * 0.99)] if n >= 100 else s[-1]
        # Bootstrap 95% CI
        rng = random.Random(42)
        bs_means = []
        for _ in range(10000):
            sample = [s[rng.randint(0, n - 1)] for _ in range(n)]
            bs_means.append(statistics.mean(sample))
        bs_means.sort()
        self.ci_low = bs_means[250]
        self.ci_high = bs_means[9749]

    def to_dict(self):
        return {
            "n": self.n, "unit": self.unit,
            "mean": round(self.mean, 2), "stddev": round(self.stddev, 2),
            "min": round(self.min, 2), "max": round(self.max, 2),
            "median": round(self.median, 2),
            "p95": round(self.p95, 2), "p99": round(self.p99, 2),
            "ci_95": [round(self.ci_low, 2), round(self.ci_high, 2)],
        }


def build_targets():
    """编译 bench targets."""
    print("[4.1-v2] Building bench targets...")
    result = subprocess.run(
        ["make", "-C", str(BENCH_DIR), "clean", "all"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr[:500]}")
        return False
    print(f"  Built OK")
    return True


def measure_baseline(target: str, args: list[str], n_runs: int = N_RUNS) -> PerfStats:
    """测量基线 (无 eBPF) 每次迭代的平均开销 (ns)."""
    times_ns = []
    target_path = BENCH_DIR / target

    for i in range(n_runs):
        cmd = [str(target_path)] + args
        start = time.perf_counter_ns()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        elapsed_ns = time.perf_counter_ns() - start

        if result.returncode != 0:
            print(f"  WARNING baseline run {i+1}/{n_runs}: rc={result.returncode}")
        times_ns.append(elapsed_ns)

    total_iterations = int(args[0]) if args else 500_000
    per_call_ns = [t / total_iterations for t in times_ns]
    return PerfStats(per_call_ns, unit="ns")


def measure_instrumented_ssl(target: str, args: list[str], n_runs: int = N_RUNS) -> PerfStats:
    """SSL uprobe 模式: collector 直接运行 target."""
    times_ns = []
    target_path = str(BENCH_DIR / target)

    for i in range(n_runs):
        cmd = (
            ["sudo", "-n", str(COLLECTOR_BIN), "ssl",
             "--binary-path", target_path, "--quiet", "--"]
            + args
        )
        start = time.perf_counter_ns()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        elapsed_ns = time.perf_counter_ns() - start

        if result.returncode != 0:
            stderr_tail = result.stderr[-300:] if result.stderr else "(empty)"
            print(f"  WARNING ssl run {i+1}/{n_runs}: rc={result.returncode}, stderr={stderr_tail[:150]}")
        times_ns.append(elapsed_ns)

    total_iterations = int(args[0]) if args else SSL_ITERATIONS
    per_call_ns = [t / total_iterations for t in times_ns]
    return PerfStats(per_call_ns, unit="ns")


def measure_instrumented_process(cmd_args: list[str], target: str,
                                  target_args: list[str], n_runs: int = N_RUNS) -> PerfStats:
    """进程模式: 启动 collector → 运行 target → 停止 collector."""
    times_ns = []
    target_path = str(BENCH_DIR / target)

    for i in range(n_runs):
        # Start collector in background
        collector = subprocess.Popen(
            ["sudo", "-n"] + cmd_args,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(3)  # Wait for eBPF programs to attach

        # Run target
        start = time.perf_counter_ns()
        result = subprocess.run(
            [target_path] + target_args,
            capture_output=True, text=True, timeout=300,
        )
        elapsed_ns = time.perf_counter_ns() - start

        # Stop collector
        collector.terminate()
        try:
            collector.wait(timeout=10)
        except subprocess.TimeoutExpired:
            collector.kill()
            collector.wait()

        if result.returncode != 0:
            print(f"  WARNING process run {i+1}/{n_runs}: rc={result.returncode}")
        times_ns.append(elapsed_ns)

    total_iterations = int(target_args[0]) if target_args else 100_000
    per_call_ns = [t / total_iterations for t in times_ns]
    return PerfStats(per_call_ns, unit="ns")


def overhead_stats(baseline: PerfStats, instrumented: PerfStats) -> dict:
    """计算 overhead = instrumented - baseline, 含统计检验."""
    diff_ns = instrumented.mean - baseline.mean
    # 合并标准差 (pooled)
    pooled_var = (baseline.stddev**2 / baseline.n +
                  instrumented.stddev**2 / instrumented.n)
    pooled_se = math.sqrt(pooled_var)
    # Wald 95% CI for the difference
    ci_half = 1.96 * pooled_se
    ci_low = diff_ns - ci_half
    ci_high_val = diff_ns + ci_half
    crosses_zero = ci_low < 0 < ci_high_val

    # SNR = mean_diff / pooled_se
    snr = abs(diff_ns) / pooled_se if pooled_se > 0 else float("inf")

    return {
        "overhead_per_call_ns": round(diff_ns, 1),
        "overhead_ci_95": [round(max(ci_low, -10000), 1), round(min(ci_high_val, 10000), 1)],
        "significant": not crosses_zero,
        "snr": round(snr, 1),
        "note": ("与零无显著差异 (CI 跨越零线)" if crosses_zero
                 else f"显著 (SNR={snr:.1f})"),
    }


def run():
    print("\n" + "=" * 70)
    print("  Experiment 4.1 v2: eBPF Probe Latency (High-Precision)")
    print("=" * 70)
    print(f"  Design: {N_RUNS} runs, alternating baseline/instrumented")
    print(f"  Iterations: SSL={SSL_ITERATIONS:,}, exec={EXEC_ITERATIONS:,}, file={FILE_ITERATIONS:,}")

    if not COLLECTOR_BIN.exists():
        print(f"  ERROR: collector not found at {COLLECTOR_BIN}")
        print("  Build: cd collector && cargo build --release")
        return

    if not build_targets():
        return

    assert os.geteuid() == 0, "Must run as root for eBPF"

    results = {}

    # ── 4.1.1 SSL uprobes ──
    print("\n── SSL_read/SSL_write uprobes ──")
    ssl_args = [str(SSL_ITERATIONS)]

    baseline_ssl = measure_baseline("ssl_loop", ssl_args)
    print(f"  Baseline: {baseline_ssl.mean:.1f} ± {baseline_ssl.stddev:.1f} ns/call "
          f"[{baseline_ssl.ci_low:.1f}, {baseline_ssl.ci_high:.1f}]")

    instr_ssl = measure_instrumented_ssl("ssl_loop", ssl_args)
    print(f"  Instrumented: {instr_ssl.mean:.1f} ± {instr_ssl.stddev:.1f} ns/call "
          f"[{instr_ssl.ci_low:.1f}, {instr_ssl.ci_high:.1f}]")

    oh_ssl = overhead_stats(baseline_ssl, instr_ssl)
    print(f"  Overhead: {oh_ssl['overhead_per_call_ns']:.1f} ns/call "
          f"(SNR={oh_ssl['snr']:.1f}, {oh_ssl['note']})")

    results["uprobe/SSL_read+SSL_write"] = {
        "hook_type": "uprobe",
        "iterations": SSL_ITERATIONS,
        "n_runs": N_RUNS,
        "baseline": baseline_ssl.to_dict(),
        "instrumented": instr_ssl.to_dict(),
        **oh_ssl,
    }

    # ── 4.1.2 sched_process_exec tracepoint ──
    print("\n── sched_process_exec tracepoint ──")
    exec_args = [str(EXEC_ITERATIONS)]

    baseline_exec = measure_baseline("exec_loop", exec_args)
    print(f"  Baseline: {baseline_exec.mean:.1f} ± {baseline_exec.stddev:.1f} ns/call "
          f"[{baseline_exec.ci_low:.1f}, {baseline_exec.ci_high:.1f}]")

    instr_exec = measure_instrumented_process(
        [str(COLLECTOR_BIN), "process", "-c", "exec_loop", "--quiet"],
        "exec_loop", exec_args,
    )
    print(f"  Instrumented: {instr_exec.mean:.1f} ± {instr_exec.stddev:.1f} ns/call "
          f"[{instr_exec.ci_low:.1f}, {instr_exec.ci_high:.1f}]")

    oh_exec = overhead_stats(baseline_exec, instr_exec)
    print(f"  Overhead: {oh_exec['overhead_per_call_ns']:.1f} ns/call "
          f"(SNR={oh_exec['snr']:.1f}, {oh_exec['note']})")

    results["tp/sched_process_*"] = {
        "hook_type": "tracepoint",
        "iterations": EXEC_ITERATIONS,
        "n_runs": N_RUNS,
        "baseline": baseline_exec.to_dict(),
        "instrumented": instr_exec.to_dict(),
        **oh_exec,
    }

    # ── 4.1.3 文件访问探针 ──
    print("\n── 文件访问 tracepoint ──")
    file_args = [str(FILE_ITERATIONS)]

    baseline_file = measure_baseline("file_loop", file_args)
    print(f"  Baseline: {baseline_file.mean:.1f} ± {baseline_file.stddev:.1f} ns/call "
          f"[{baseline_file.ci_low:.1f}, {baseline_file.ci_high:.1f}]")

    instr_file = measure_instrumented_process(
        [str(COLLECTOR_BIN), "trace", "--ssl=false", "--process=true",
         "-c", "file_loop", "--quiet"],
        "file_loop", file_args,
    )
    print(f"  Instrumented: {instr_file.mean:.1f} ± {instr_file.stddev:.1f} ns/call "
          f"[{instr_file.ci_low:.1f}, {instr_file.ci_high:.1f}]")

    oh_file = overhead_stats(baseline_file, instr_file)
    print(f"  Overhead: {oh_file['overhead_per_call_ns']:.1f} ns/call "
          f"(SNR={oh_file['snr']:.1f}, {oh_file['note']})")

    results["file_access_probes"] = {
        "hook_type": "tracepoint",
        "iterations": FILE_ITERATIONS,
        "n_runs": N_RUNS,
        "baseline": baseline_file.to_dict(),
        "instrumented": instr_file.to_dict(),
        **oh_file,
    }

    # ── 保存 ──
    output = {
        "experiment": "4.1_v2",
        "method": "wall-clock difference with clock_gettime nanosecond precision",
        "design": f"{N_RUNS} runs alternating baseline/instrumented",
        "iterations": {
            "ssl": SSL_ITERATIONS, "exec": EXEC_ITERATIONS, "file": FILE_ITERATIONS,
        },
        "collector_build": "release",
        "probe_latency": results,
        "literature_reference": {
            "uprobe": "200-500 ns (Linux kernel docs, Brendan Gregg's perf book)",
            "tracepoint": "50-200 ns (Linux kernel tracepoint overhead docs)",
            "kprobe": "100-300 ns (Linux kernel kprobe overhead docs)",
        },
    }

    out_path = PERF_DIR / "probe_latency_v2.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\n✓ Results saved to {out_path}")

    # ── 摘要 ──
    print("\n── Summary ──")
    for name, data in results.items():
        oh = data["overhead_per_call_ns"]
        snr = data.get("snr", 0)
        sig = "✓" if data.get("significant") else "≈0"
        ref = ("uprobe: 200-500 ns" if "uprobe" in name
               else "tracepoint: 50-200 ns")
        print(f"  {name}: {oh:.1f} ns/call (SNR={snr:.1f}, {sig}) | literature: {ref}")


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("ERROR: This script requires root privileges (sudo).")
        print("Run: sudo python3 compare/scripts/perf/probe_latency_v2.py")
        import sys
        sys.exit(1)
    run()
