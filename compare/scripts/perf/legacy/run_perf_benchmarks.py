#!/usr/bin/env python3
"""MtAgentPrism 性能微基准实验 (Experiment 8).

运行全部四个子实验:
  8.1 eBPF 探针延迟
  8.2 Collector 管道吞吐量
  8.3 端到端 Agent 开销
  8.4 内存占用

产出:
  - compare/results/perf/perf_report.json — 原始数据
  - compare/results/perf/perf_tables.tex  — LaTeX 表格
  - compare/results/perf/figures/         — PDF 图表 (fig7-fig10)

用法:
  python3 compare/scripts/run_perf_benchmarks.py [--all | --probe | --throughput | --e2e | --memory]
  python3 compare/scripts/run_perf_benchmarks.py --probe  # 只运行探针延迟
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 确保 compare/scripts/ 在 sys.path 中
COMPARE_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(COMPARE_SCRIPTS))

from perf.probe_latency import run_probe_latency, probe_latency_latex_table
from perf.throughput import run_throughput, throughput_latex_table
from perf.e2e_overhead import run_e2e_overhead, e2e_overhead_latex_table
from perf.memory_footprint import run_memory_footprint, memory_footprint_latex_table
from perf.common import (
    write_latex_tables, save_report, RESULTS_DIR, FIGURES_DIR,
)


def main():
    parser = argparse.ArgumentParser(
        description="MtAgentPrism Performance Micro-Benchmarks (Experiment 8)",
    )
    parser.add_argument(
        "--all", action="store_true", default=True,
        help="Run all four sub-experiments (default)",
    )
    parser.add_argument(
        "--probe", action="store_true",
        help="Run 8.1: eBPF Probe Latency only",
    )
    parser.add_argument(
        "--throughput", action="store_true",
        help="Run 8.2: Collector Throughput only",
    )
    parser.add_argument(
        "--e2e", action="store_true",
        help="Run 8.3: E2E Agent Overhead only",
    )
    parser.add_argument(
        "--memory", action="store_true",
        help="Run 8.4: Memory Footprint only",
    )
    parser.add_argument(
        "--skip-e2e", action="store_true",
        help="Skip E2E test (requires real Agent CLI)",
    )
    parser.add_argument(
        "--agent-bin", type=str, default="claude",
        help="Agent CLI binary for E2E tests (default: claude)",
    )

    args = parser.parse_args()

    # 确定要运行哪些实验
    selective = any([args.probe, args.throughput, args.e2e, args.memory])
    run_all = args.all and not selective

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("  MtAgentPrism Performance Micro-Benchmarks (Experiment 8)")
    print(f"  Results: {RESULTS_DIR}")
    print(f"  Figures: {FIGURES_DIR}")
    print("=" * 70)

    all_results = {}
    latex_tables = []
    start_time = time.monotonic()

    # ── 8.1: eBPF 探针延迟 ───────────────────────────────────────────
    if run_all or args.probe:
        try:
            probe_results = run_probe_latency()
            all_results["probe_latency"] = probe_results
            if "error" not in probe_results:
                latex_tables.append(probe_latency_latex_table(probe_results))
            save_report(all_results)
        except Exception as e:
            print(f"\n  FAILED 8.1: {e}")
            import traceback; traceback.print_exc()
            all_results["probe_latency"] = {"error": str(e)}

    # ── 8.2: Collector 吞吐量 ────────────────────────────────────────
    if run_all or args.throughput:
        try:
            throughput_results = run_throughput()
            all_results["throughput"] = throughput_results
            if "error" not in throughput_results:
                latex_tables.append(throughput_latex_table(throughput_results))
            save_report(all_results)
        except Exception as e:
            print(f"\n  FAILED 8.2: {e}")
            import traceback; traceback.print_exc()
            all_results["throughput"] = {"error": str(e)}

    # ── 8.3: 端到端 Agent 开销 ───────────────────────────────────────
    if args.skip_e2e:
        print("\n  Skipping 8.3 (--skip-e2e)")
    elif run_all or args.e2e:
        import os
        os.environ.setdefault("MTAGENTPRISM_AGENT_BIN", args.agent_bin)
        try:
            e2e_results = run_e2e_overhead()
            all_results["e2e_overhead"] = e2e_results
            if "error" not in e2e_results:
                latex_tables.append(e2e_overhead_latex_table(e2e_results))
            save_report(all_results)
        except Exception as e:
            print(f"\n  FAILED 8.3: {e}")
            import traceback; traceback.print_exc()
            all_results["e2e_overhead"] = {"error": str(e)}

    # ── 8.4: 内存占用 ────────────────────────────────────────────────
    if run_all or args.memory:
        try:
            mem_results = run_memory_footprint()
            all_results["memory_footprint"] = mem_results
            if "error" not in mem_results:
                latex_tables.append(memory_footprint_latex_table(mem_results))
            save_report(all_results)
        except Exception as e:
            print(f"\n  FAILED 8.4: {e}")
            import traceback; traceback.print_exc()
            all_results["memory_footprint"] = {"error": str(e)}

    # ── 最终保存 ─────────────────────────────────────────────────────
    total_elapsed = time.monotonic() - start_time

    # 写入 LaTeX 表格
    while len(latex_tables) < 4:
        latex_tables.append("% (skipped)")
    write_latex_tables(*latex_tables)

    # 最终报告
    all_results["_meta"] = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_elapsed_s": total_elapsed,
    }
    save_report(all_results)

    print("\n" + "=" * 70)
    success_count = sum(1 for v in all_results.values()
                       if isinstance(v, dict) and "error" not in v)
    print(f"  Experiment 8 complete: {success_count}/4 sub-experiments succeeded")
    print(f"  Total time: {total_elapsed:.0f}s")
    print(f"  Results: {RESULTS_DIR}")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(main())
