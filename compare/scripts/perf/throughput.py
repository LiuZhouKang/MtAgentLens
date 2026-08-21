"""4.2 Collector 吞吐量 — 测量管道事件处理速率.

方法:
  1. 运行 perf_throughput Rust 二进制, 使用 FakeRunner 生成 N 个合成事件
  2. 对比 Full Pipeline (含 Analyzer 链) vs Passthrough (纯事件流)
  3. 多次运行取统计

需要: collector 已编译 (cargo build --release)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .common import (
    COLLECTOR_BIN,
    PROJECT_ROOT,
    PerfStats,
    bar_chart_with_error,
    latex_table,
    run_timed,
    save_report,
)


def run_throughput() -> dict:
    """运行吞吐量基准测试, 返回结果字典."""
    print("\n" + "=" * 70)
    print("  Experiment 8.2: Collector Throughput")
    print("=" * 70)

    if not COLLECTOR_BIN.exists():
        print(f"  ERROR: Collector binary not found at {COLLECTOR_BIN}")
        return {"error": "collector not built"}

    # 确保 perf_throughput 二进制已编译
    print("  Building perf_throughput binary...")
    build_result = subprocess.run(
        ["cargo", "build", "--release", "--bin", "perf_throughput"],
        cwd=str(PROJECT_ROOT / "collector"),
        capture_output=True, text=True,
    )
    if build_result.returncode != 0:
        print(f"  ERROR building perf_throughput:\n{build_result.stderr[-500:]}")
        return {"error": "build failed"}

    throughput_bin = PROJECT_ROOT / "collector" / "target" / "release" / "perf_throughput"
    if not throughput_bin.exists():
        print(f"  ERROR: throughput binary not found at {throughput_bin}")
        return {"error": "binary not found"}

    results = {}

    # 在三个数量级测试
    event_counts = [10_000, 50_000, 100_000]
    runs_per_config = 5

    for count in event_counts:
        print(f"\n  Testing {count} event pairs ({count*2} events)...")

        cmd = [str(throughput_bin), str(count)]
        count_results = {"full_pipeline": [], "passthrough": [], "overhead_pct": []}

        for run_idx in range(runs_per_config):
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                print(f"    Run {run_idx+1} failed: {result.stderr[-200:]}")
                continue

            # 最后一行是 JSON
            stdout_lines = result.stdout.strip().split("\n")
            json_line = stdout_lines[-1]
            try:
                data = json.loads(json_line)
                count_results["full_pipeline"].append(data["full_pipeline"]["events_per_sec"])
                count_results["passthrough"].append(data["passthrough"]["events_per_sec"])
                count_results["overhead_pct"].append(data["analyzer_overhead_pct"])
            except (json.JSONDecodeError, KeyError) as e:
                print(f"    Run {run_idx+1} parse error: {e}")
                continue

        full_stats = PerfStats.from_values(count_results["full_pipeline"], unit="events/s")
        raw_stats = PerfStats.from_values(count_results["passthrough"], unit="events/s")
        oh_stats = PerfStats.from_values(count_results["overhead_pct"], unit="%")

        print(f"    Full Pipeline: {full_stats.mean:.0f} evt/s "
              f"(±{full_stats.stddev:.0f})")
        print(f"    Passthrough:   {raw_stats.mean:.0f} evt/s "
              f"(±{raw_stats.stddev:.0f})")
        print(f"    Analyzer Overhead: {oh_stats.mean:.1f}%")

        results[f"{count}"] = {
            "full_pipeline": full_stats,
            "passthrough": raw_stats,
            "overhead_pct": oh_stats,
        }

    _generate_charts(results)
    return results


def _generate_charts(results: dict):
    """生成吞吐量图表."""
    from .common import setup_paper_style, save_figure
    import matplotlib.pyplot as plt
    import numpy as np

    setup_paper_style()
    fig, ax = plt.subplots(figsize=(8, 4))

    labels = list(results.keys())
    x = np.arange(len(labels))
    width = 0.35

    full_means = [results[k]["full_pipeline"].mean for k in labels]
    full_errs = [results[k]["full_pipeline"].stddev for k in labels]
    raw_means = [results[k]["passthrough"].mean for k in labels]
    raw_errs = [results[k]["passthrough"].stddev for k in labels]

    ax.bar(x - width/2, full_means, width, yerr=full_errs, capsize=3,
           label="Full Pipeline (HTTPParser)", color="#1a5276",
           edgecolor="white", linewidth=0.5)
    ax.bar(x + width/2, raw_means, width, yerr=raw_errs, capsize=3,
           label="Passthrough (No Analyzers)", color="#7fb3d8",
           edgecolor="white", linewidth=0.5)

    ax.set_xticks(x)
    ax.set_xticklabels([f"{int(k):,} pairs" for k in labels])
    ax.set_ylabel("Events / Second")
    ax.set_title("Collector Pipeline Throughput: Full vs Passthrough")
    ax.legend(fontsize=8)

    save_figure(fig, "fig8_throughput.pdf")


def throughput_latex_table(results: dict) -> str:
    """生成吞吐量 LaTeX 表格."""
    headers = ["Event Pairs", "Full Pipeline (evt/s)", "Passthrough (evt/s)",
               "Analyzer Overhead (%)"]
    rows = []
    for count_str, data in results.items():
        full = data["full_pipeline"]
        raw = data["passthrough"]
        oh = data["overhead_pct"]
        rows.append([
            f"{int(count_str):,}",
            f"{full.mean:.0f} ± {full.stddev:.0f}",
            f"{raw.mean:.0f} ± {raw.stddev:.0f}",
            f"{oh.mean:.1f}",
        ])

    return latex_table(
        headers, rows,
        caption="Collector Pipeline Throughput — Full Analyzer Chain vs Passthrough. "
                "Synthetic SSL events via FakeRunner, 5 runs per event count.",
        label="tab:throughput",
    )
