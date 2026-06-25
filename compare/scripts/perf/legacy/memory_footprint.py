"""4.4 内存占用 — 测量 collector 长期运行的内存稳定性.

方法:
  1. 启动 collector 监控一个持续约 5 分钟的 Agent 会话
  2. 后台每秒采样 RSS (via /proc/[pid]/statm)
  3. 绘制 RSS 时间序列 + 标注 steady-state + peak
  4. 同时用 SystemRunner 采集 collector 自身指标

需要: sudo 权限, Claude Code
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from .common import (
    COLLECTOR_BIN,
    PerfStats,
    memory_timeline_chart,
    latex_table,
    sample_rss,
    save_report,
    setup_paper_style,
)


MEMORY_TEST_DURATION_S = 300  # 5 分钟
RSS_SAMPLE_INTERVAL_S = 1.0


def _find_collector_pid() -> int | None:
    """找到正在运行的 collector 进程 PID."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "mtagentlens"],
            capture_output=True, text=True,
        )
        pids = result.stdout.strip().split("\n")
        for pid_str in pids:
            if pid_str:
                pid = int(pid_str)
                # 检查是否是 collector 进程
                try:
                    cmdline = Path(f"/proc/{pid}/cmdline").read_text()
                    if "mtagentlens" in cmdline and "trace" in cmdline:
                        return pid
                except (FileNotFoundError, PermissionError):
                    continue
    except Exception:
        pass
    return None


def run_memory_footprint() -> dict:
    """运行内存占用基准测试, 返回结果字典."""
    print("\n" + "=" * 70)
    print("  Experiment 8.4: Memory Footprint")
    print("=" * 70)

    if not COLLECTOR_BIN.exists():
        print(f"  ERROR: Collector binary not found at {COLLECTOR_BIN}")
        return {"error": "collector not built"}

    # 检查 sudo
    sudo_check = subprocess.run(
        ["sudo", "-n", "true"],
        capture_output=True,
    )
    if sudo_check.returncode != 0:
        print("  WARNING: Passwordless sudo not available.")
        print("  Memory test requires sudo for collector.")

    # 启动 collector
    print(f"\n  Starting collector (trace mode, {MEMORY_TEST_DURATION_S}s duration)...")
    collector_proc = subprocess.Popen(
        ["sudo", "-n",
         str(COLLECTOR_BIN), "trace",
         "--ssl", "--process",
         "--quiet"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)

    # 找到 collector PID
    collector_pid = None
    for attempt in range(5):
        collector_pid = _find_collector_pid()
        if collector_pid:
            break
        time.sleep(1)

    if not collector_pid:
        print("  ERROR: Could not find collector PID")
        collector_proc.terminate()
        collector_proc.wait()
        return {"error": "collector PID not found"}

    print(f"  Collector PID: {collector_pid}")

    # 同时启动一个简单的 Agent 任务来产生负载
    agent_bin = os.environ.get("MTAGENTPRISM_AGENT_BIN", "claude")
    agent_proc = subprocess.Popen(
        [agent_bin, "-p",
         "List all Python files in compare/scripts/ directory. "
         "For each file, read it and summarize its purpose in one sentence. "
         "Then list all functions in compare/scripts/perf/common.py."],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 采样 RSS
    print(f"  Sampling RSS every {RSS_SAMPLE_INTERVAL_S}s for {MEMORY_TEST_DURATION_S}s...")
    rss_samples = sample_rss(collector_pid, MEMORY_TEST_DURATION_S, RSS_SAMPLE_INTERVAL_S)

    # 清理
    agent_proc.terminate()
    try:
        agent_proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        agent_proc.kill()

    collector_proc.terminate()
    try:
        collector_proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        collector_proc.kill()

    if not rss_samples:
        print("  ERROR: No RSS samples collected")
        return {"error": "no samples"}

    # 分析
    rss_stats = PerfStats.from_values(rss_samples, unit="MB")

    # Steady-state: 后 50% 样本的均值 (跳过前 30 秒的初始化期)
    half_point = max(1, len(rss_samples) // 2)
    init_samples = max(30, int(30 / RSS_SAMPLE_INTERVAL_S))  # 跳过前 30 秒
    if init_samples < len(rss_samples):
        steady_state_rss = sum(rss_samples[init_samples:]) / len(rss_samples[init_samples:])
    else:
        steady_state_rss = sum(rss_samples[half_point:]) / max(1, len(rss_samples) - half_point)

    peak_rss = max(rss_samples)

    print(f"\n  Results ({len(rss_samples)} samples over {MEMORY_TEST_DURATION_S}s):")
    print(f"    Mean RSS:    {rss_stats.mean:.1f} MB")
    print(f"    Median RSS:  {rss_stats.median:.1f} MB")
    print(f"    Peak RSS:    {peak_rss:.1f} MB")
    print(f"    Steady-State:{steady_state_rss:.1f} MB")
    print(f"    Std Dev:     {rss_stats.stddev:.1f} MB")
    print(f"    P95 RSS:     {rss_stats.p95:.1f} MB")

    # 计算增长趋势 (线性回归斜率)
    growth_rate = _compute_growth_rate(rss_samples)

    results = {
        "duration_s": MEMORY_TEST_DURATION_S,
        "sample_interval_s": RSS_SAMPLE_INTERVAL_S,
        "num_samples": len(rss_samples),
        "rss_stats": rss_stats,
        "peak_rss_mb": peak_rss,
        "steady_state_rss_mb": steady_state_rss,
        "growth_rate_mb_per_min": growth_rate,
        "timestamps": [i * RSS_SAMPLE_INTERVAL_S for i in range(len(rss_samples))],
        "rss_values": rss_samples,
    }

    _generate_charts(results)
    return results


def _compute_growth_rate(rss_values: list[float]) -> float:
    """计算 RSS 增长速率 (MB/min), 使用简单线性回归."""
    n = len(rss_values)
    if n < 2:
        return 0.0

    # 简单: 前半 vs 后半均值差
    mid = n // 2
    first_half_mean = sum(rss_values[:mid]) / mid
    second_half_mean = sum(rss_values[mid:]) / max(1, n - mid)
    time_span_min = (n * RSS_SAMPLE_INTERVAL_S) / 120.0  # 半段时间
    if time_span_min > 0:
        return (second_half_mean - first_half_mean) / time_span_min
    return 0.0


def _generate_charts(results: dict):
    """生成内存占用图表."""
    if "timestamps" not in results:
        return

    memory_timeline_chart(
        results["timestamps"],
        results["rss_values"],
        results["steady_state_rss_mb"],
        results["peak_rss_mb"],
    )


def memory_footprint_latex_table(results: dict) -> str:
    """生成内存占用 LaTeX 表格."""
    if "rss_stats" not in results:
        return ""

    rss = results["rss_stats"]
    headers = ["Metric", "Value"]
    rows = [
        ["Duration", f"{results['duration_s']} s"],
        ["Samples", str(results["num_samples"])],
        ["Mean RSS", f"{rss.mean:.1f} MB"],
        ["Median RSS", f"{rss.median:.1f} MB"],
        ["Peak RSS", f"{results['peak_rss_mb']:.1f} MB"],
        ["Steady-State RSS", f"{results['steady_state_rss_mb']:.1f} MB"],
        ["P95 RSS", f"{rss.p95:.1f} MB"],
        ["Std Dev", f"{rss.stddev:.1f} MB"],
        ["Growth Rate", f"{results.get('growth_rate_mb_per_min', 0):.2f} MB/min"],
    ]

    return latex_table(
        headers, rows,
        caption="Collector Memory Footprint — RSS sampled every second during "
                f"a {results['duration_s']}s agent session. "
                "Steady-state computed from samples after the first 30s initialization period.",
        label="tab:memory_footprint",
    )
