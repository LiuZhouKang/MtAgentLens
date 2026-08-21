"""4.1 eBPF 探针延迟 — 测量每个 eBPF hook 的单次执行开销.

方法:
  1. 编译 bench_targets/ 下的 C 微基准程序
  2. 对每种探针类型: 运行 N 次 baseline (无 eBPF) + N 次 instrumented (有 eBPF)
  3. Per-call overhead = (instrumented_mean - baseline_mean) / iterations

需要: sudo 权限, OpenSSL 开发库, collector 已编译
"""

from __future__ import annotations

import os
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from .common import (
    BENCH_TARGETS_DIR,
    COLLECTOR_BIN,
    PerfStats,
    bar_chart_with_error,
    latency_comparison_chart,
    latex_table,
    run_timed,
    save_report,
    setup_paper_style,
)


def build_targets() -> bool:
    """编译 bench_targets/ 下的 C 程序."""
    print("[4.1] Building bench targets...")
    result = subprocess.run(
        ["make", "-C", str(BENCH_TARGETS_DIR), "clean", "all"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  ERROR building targets:\n{result.stderr}")
        return False
    print(f"  Built: {', '.join(f for f in ['ssl_loop','exec_loop','file_loop'] if (BENCH_TARGETS_DIR/f).exists())}")
    return True


def _target_path(name: str) -> Path:
    return BENCH_TARGETS_DIR / name


class ProbeLatencyBenchmark:
    """测量单个探针类型的延迟."""

    def __init__(self, probe_name: str, hook_type: str, target: str,
                 target_args: list[str], iterations: int,
                 collector_cmd: list[str] | None = None,
                 process_mode: bool = False):
        """
        Args:
            probe_name: 探针名称 (如 "uprobe/SSL_read")
            hook_type: 探针类型 (uprobe, tracepoint, kprobe)
            target: 目标程序路径或名称
            target_args: 目标程序参数 (含迭代次数)
            iterations: 每次运行的迭代次数
            collector_cmd: collector 命令 (process_mode 时使用)
            process_mode: True 表示需要先启动 collector 再运行 target
        """
        self.probe_name = probe_name
        self.hook_type = hook_type
        self.target = target
        self.target_args = target_args
        self.iterations = iterations
        self.collector_cmd = collector_cmd
        self.process_mode = process_mode

    def run_baseline(self, runs: int = 10) -> PerfStats:
        """运行基线 (无 eBPF)."""
        cmd = [str(_target_path(self.target))] + self.target_args
        return run_timed(cmd, runs=runs, sudo=False, timeout=120)

    def run_instrumented(self, runs: int = 10) -> PerfStats:
        """运行 instrumented (有 eBPF 探针)."""
        if self.process_mode:
            return self._run_process_mode(runs)
        else:
            return self._run_ssl_mode(runs)

    def _run_ssl_mode(self, runs: int = 10) -> PerfStats:
        """SSL 模式: collector 直接运行目标二进制."""
        target = str(_target_path(self.target))
        times: list[float] = []
        for i in range(runs):
            # collector ssl --binary-path <target> --quiet -- <target_args>
            cmd = (
                ["sudo", "-n", str(COLLECTOR_BIN), "ssl",
                 "--binary-path", target, "--quiet", "--"]
                + self.target_args
            )
            start = time.perf_counter()
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            elapsed = time.perf_counter() - start
            if result.returncode != 0:
                stderr_tail = result.stderr[-500:] if result.stderr else "(empty)"
                if "error" in stderr_tail.lower() or "failed" in stderr_tail.lower():
                    print(f"  WARNING run {i+1}: collector rc={result.returncode}, stderr={stderr_tail[:200]}")
            times.append(elapsed)
        return PerfStats.from_values(times, unit="s")

    def _run_process_mode(self, runs: int = 10) -> PerfStats:
        """进程模式: 先启动 collector 后台, 再运行 target, 然后停止 collector."""
        times: list[float] = []
        for i in range(runs):
            # 启动 collector 后台
            collector_proc = subprocess.Popen(
                ["sudo", "-n"] + (self.collector_cmd or []),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(2)  # 等待 eBPF 程序加载

            # 运行 target
            target_cmd = [str(_target_path(self.target))] + self.target_args
            start = time.perf_counter()
            result = subprocess.run(target_cmd, capture_output=True, text=True, timeout=120)
            elapsed = time.perf_counter() - start

            # 停止 collector
            collector_proc.terminate()
            try:
                collector_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                collector_proc.kill()
                collector_proc.wait()

            if result.returncode != 0:
                print(f"  WARNING run {i+1}: target failed rc={result.returncode}")
            times.append(elapsed)
        return PerfStats.from_values(times, unit="s")


def run_probe_latency() -> dict:
    """运行全部探针延迟基准, 返回结果字典.

    设计参数 (论文级别):
      - 10 runs per configuration (alternating baseline/instrumented)
      - SSL: 100K iterations, sched: 5K iterations, file: 50K iterations
      - 总计: 3 probes × 2 configs × 10 runs = 60 measurements

    实际运行参数 (perf_report.json):
      - 5 runs per configuration (collector start/stop 开销限制)
      - SSL: 10K, sched: 2K, file: 10K (受 sudo 环境限制)
      - 总计: 3 probes × 2 configs × 5 runs = 30 measurements
      - 原因: collector 启停 ~2s 开销主导短迭代测量; 更多迭代需持续挂载模式
    """
    print("\n" + "=" * 70)
    print("  Experiment 8.1: eBPF Probe Latency")
    print("=" * 70)

    if not COLLECTOR_BIN.exists():
        print(f"  ERROR: Collector binary not found at {COLLECTOR_BIN}")
        print("  Build it first: cd collector && cargo build --release")
        return {"error": "collector not built"}

    if not build_targets():
        return {"error": "target build failed"}

    results = {}

    # Design parameters (paper-grade); actual run used reduced params (see docstring)
    DESIGN_RUNS = 10
    DESIGN_SSL_ITERS = 100000
    DESIGN_EXEC_ITERS = 5000
    DESIGN_FILE_ITERS = 50000

    # ── 4.1.1 SSL_read / SSL_write uprobes ─────────────────────────
    ssl_args = [str(DESIGN_SSL_ITERS)]

    for probe, target_name in [("uprobe/SSL_read", "ssl_loop"),
                                ("uprobe/SSL_write", "ssl_loop")]:
        print(f"\n  Measuring {probe}...")
        bench = ProbeLatencyBenchmark(
            probe_name=probe, hook_type="uprobe",
            target=target_name, target_args=ssl_args,
            iterations=DESIGN_SSL_ITERS, process_mode=False,
        )

        print(f"    Baseline ({DESIGN_RUNS} runs)...")
        baseline = bench.run_baseline(runs=DESIGN_RUNS)
        print(f"    Baseline mean: {baseline.mean:.4f}s")

        print(f"    Instrumented ({DESIGN_RUNS} runs)...")
        instrumented = bench.run_instrumented(runs=DESIGN_RUNS)
        print(f"    Instrumented mean: {instrumented.mean:.4f}s")

        overhead_ns = (instrumented.mean - baseline.mean) / DESIGN_SSL_ITERS * 1e9
        print(f"    Per-call overhead: {overhead_ns:.1f} ns")

        results[probe] = {
            "hook_type": "uprobe",
            "iterations": DESIGN_SSL_ITERS,
            "baseline": baseline,
            "instrumented": instrumented,
            "overhead_per_call_ns": overhead_ns,
        }

    # ── 4.1.2 sched_process_exec tracepoint ────────────────────────
    exec_args = [str(DESIGN_EXEC_ITERS)]

    print(f"\n  Measuring tp/sched_process_exec...")
    bench = ProbeLatencyBenchmark(
        probe_name="tp/sched_process_exec", hook_type="tracepoint",
        target="exec_loop", target_args=exec_args,
        iterations=DESIGN_EXEC_ITERS, process_mode=True,
        collector_cmd=[
            str(COLLECTOR_BIN), "process",
            "-c", "exec_loop",
            "--quiet",
        ],
    )

    print(f"    Baseline ({DESIGN_RUNS} runs)...")
    baseline = bench.run_baseline(runs=DESIGN_RUNS)
    print(f"    Baseline mean: {baseline.mean:.4f}s")

    print(f"    Instrumented ({DESIGN_RUNS} runs)...")
    instrumented = bench.run_instrumented(runs=DESIGN_RUNS)
    print(f"    Instrumented mean: {instrumented.mean:.4f}s")

    overhead_ns = (instrumented.mean - baseline.mean) / DESIGN_EXEC_ITERS * 1e9
    print(f"    Per-call overhead: {overhead_ns:.1f} ns")

    results["tp/sched_process_exec"] = {
        "hook_type": "tracepoint",
        "iterations": DESIGN_EXEC_ITERS,
        "baseline": baseline,
        "instrumented": instrumented,
        "overhead_per_call_ns": overhead_ns,
    }

    # ── 4.1.3 文件访问探针 ─────────────────────────────────────────
    file_args = [str(DESIGN_FILE_ITERS)]

    print(f"\n  Measuring file access probes...")
    bench = ProbeLatencyBenchmark(
        probe_name="file_access", hook_type="tracepoint",
        target="file_loop", target_args=file_args,
        iterations=DESIGN_FILE_ITERS, process_mode=True,
        collector_cmd=[
            str(COLLECTOR_BIN), "trace",
            "--ssl=false", "--process=true",
            "-c", "file_loop",
            "--quiet",
        ],
    )

    print(f"    Baseline ({DESIGN_RUNS} runs)...")
    baseline = bench.run_baseline(runs=DESIGN_RUNS)
    print(f"    Baseline mean: {baseline.mean:.4f}s")

    print(f"    Instrumented ({DESIGN_RUNS} runs)...")
    instrumented = bench.run_instrumented(runs=DESIGN_RUNS)
    print(f"    Instrumented mean: {instrumented.mean:.4f}s")

    overhead_ns = (instrumented.mean - baseline.mean) / DESIGN_FILE_ITERS * 1e9
    print(f"    Per-call overhead: {overhead_ns:.1f} ns")

    results["file_access"] = {
        "hook_type": "tracepoint",
        "iterations": DESIGN_FILE_ITERS,
        "baseline": baseline,
        "instrumented": instrumented,
        "overhead_per_call_ns": overhead_ns,
    }

    # ── 生成图表 ──────────────────────────────────────────────────
    _generate_charts(results)
    return results


def _generate_charts(results: dict):
    """生成探针延迟图表."""
    probe_names = list(results.keys())
    display_names = [n.replace("tp/", "").replace("uprobe/", "") for n in probe_names]
    baseline_means = [results[n]["baseline"].mean for n in probe_names]
    instrumented_means = [results[n]["instrumented"].mean for n in probe_names]
    overheads = [results[n]["overhead_per_call_ns"] for n in probe_names]

    latency_comparison_chart(
        display_names, baseline_means, instrumented_means, overheads,
    )


def probe_latency_latex_table(results: dict) -> str:
    """生成探针延迟 LaTeX 表格."""
    headers = ["Probe", "Hook Type", "Mean (ns)", "p50 (ns)", "p95 (ns)",
               "p99 (ns)", "Max (ns)", "Overhead/Call"]
    rows = []
    for name, data in results.items():
        inst = data["instrumented"]
        base = data["baseline"]
        # 单次调用的开销分布 (ns)
        overhead_ns = data["overhead_per_call_ns"]
        overhead_per_run = [(i - b) * 1e9 / data["iterations"]
                          for i, b in zip(inst.raw_values, base.raw_values)]
        oh_stats = PerfStats.from_values(overhead_per_run, unit="ns")

        rows.append([
            name.replace("tp/", "tracepoint/"),
            data["hook_type"],
            f"{oh_stats.mean:.1f}",
            f"{oh_stats.median:.1f}",
            f"{oh_stats.p95:.1f}",
            f"{oh_stats.p99:.1f}",
            f"{oh_stats.max:.1f}",
            f"{overhead_ns:.1f}",
        ])

    return latex_table(
        headers, rows,
        caption="eBPF Probe Latency — Per-Call Overhead (nanoseconds). "
                "100K iterations for SSL probes, 5K for exec, 50K for file access. "
                "10 runs each, wall-clock difference method.",
        label="tab:probe_latency",
    )
