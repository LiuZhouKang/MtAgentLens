"""共享工具: 统计计算、图表生成、LaTeX 表格."""

from __future__ import annotations

import json
import math
import statistics
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# ── 路径常量 ────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[3]
COLLECTOR_BIN = PROJECT_ROOT / "collector" / "target" / "release" / "mtagentprism"
PERF_DIR = Path(__file__).resolve().parent
BENCH_TARGETS_DIR = PERF_DIR / "bench_targets"
RESULTS_DIR = PROJECT_ROOT / "compare" / "results" / "perf"
FIGURES_DIR = RESULTS_DIR / "figures"

# ── 统计结构 ────────────────────────────────────────────────────────────────


@dataclass
class PerfStats:
    """一次测量的统计摘要."""
    min: float
    mean: float
    median: float
    p95: float
    p99: float
    max: float
    stddev: float
    n: int
    unit: str
    raw_values: list[float] = field(repr=False)

    @classmethod
    def from_values(cls, values: Sequence[float], unit: str = "s") -> "PerfStats":
        n = len(values)
        if n == 0:
            return cls(0, 0, 0, 0, 0, 0, 0, 0, unit, [])
        s = sorted(values)
        return cls(
            min=min(values),
            mean=statistics.mean(values),
            median=statistics.median(values),
            p95=_percentile(s, 95),
            p99=_percentile(s, 99),
            max=max(values),
            stddev=statistics.stdev(values) if n > 1 else 0.0,
            n=n,
            unit=unit,
            raw_values=list(values),
        )

    def fmt(self, key: str) -> str:
        val = getattr(self, key)
        if key in ("n",):
            return str(val)
        if self.unit == "s":
            if val < 1e-6:
                return f"{val*1e9:.1f} ns"
            if val < 1e-3:
                return f"{val*1e6:.1f} µs"
            if val < 1:
                return f"{val*1e3:.1f} ms"
        if self.unit == "ns":
            if val < 1e3:
                return f"{val:.1f} ns"
            if val < 1e6:
                return f"{val/1e3:.1f} µs"
        if self.unit == "MB":
            return f"{val:.1f} MB"
        return f"{val:.3f}"


def _percentile(sorted_values: list[float], pct: int) -> float:
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    idx = max(0, min(n - 1, int(n * pct / 100.0)))
    return sorted_values[idx]


# ── 命令执行 ────────────────────────────────────────────────────────────────


def run_timed(
    command: list[str],
    runs: int = 10,
    sudo: bool = False,
    timeout: int = 300,
    cwd: Optional[Path] = None,
) -> PerfStats:
    """运行命令 N 次, 返回每次 wall-clock 时间的统计."""
    times: list[float] = []
    for i in range(runs):
        cmd = ["sudo", "-n"] + command if sudo else list(command)
        start = time.perf_counter()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
        elapsed = time.perf_counter() - start
        if result.returncode != 0:
            stderr_tail = result.stderr[-500:] if result.stderr else "(empty)"
            raise RuntimeError(
                f"Command '{' '.join(cmd)}' failed run {i+1}/{runs} "
                f"(rc={result.returncode}): {stderr_tail}"
            )
        times.append(elapsed)
    return PerfStats.from_values(times, unit="s")


def run_background(command: list[str], sudo: bool = False) -> subprocess.Popen:
    """启动后台进程, 返回 Popen 对象."""
    cmd = ["sudo", "-n"] + command if sudo else list(command)
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def sample_rss(pid: int, duration_s: float, interval_s: float = 1.0) -> list[float]:
    """定期采样 RSS (MB), 返回序列."""
    samples: list[float] = []
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        try:
            statm = Path(f"/proc/{pid}/statm").read_text()
            rss_pages = int(statm.split()[1])
            rss_mb = rss_pages * 4 / 1024  # 4KB pages → MB
            samples.append(rss_mb)
        except (FileNotFoundError, ValueError, IndexError):
            pass
        time.sleep(interval_s)
    return samples


# ── 绘图 ────────────────────────────────────────────────────────────────────


def setup_paper_style():
    """设置论文级 matplotlib 样式."""
    plt.rcParams.update({
        "figure.dpi": 150,
        "savefig.dpi": 150,
        "font.family": "serif",
        "font.serif": ["Times New Roman", "DejaVu Serif"],
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.labelsize": 9,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 8,
        "figure.titlesize": 11,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })


def save_figure(fig, name: str):
    """保存图表到 results/ 目录."""
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIGURES_DIR / name
    fig.savefig(str(path), bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)
    print(f"  Figure saved: {path}")


def bar_chart_with_error(
    labels: list[str],
    means: list[float],
    errors: list[float],
    title: str,
    xlabel: str,
    ylabel: str,
    output_name: str,
    colors: Optional[list[str]] = None,
    figsize: tuple[int, int] = (8, 4),
):
    """分组柱状图 + 误差线."""
    setup_paper_style()
    fig, ax = plt.subplots(figsize=figsize)
    x = np.arange(len(labels))
    bars = ax.bar(
        x, means, yerr=errors, capsize=3,
        color=colors or "#2c3e50", edgecolor="white", linewidth=0.5,
    )
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    save_figure(fig, output_name)
    return fig


def latency_comparison_chart(
    probe_names: list[str],
    baseline_means: list[float],
    instrumented_means: list[float],
    overheads_ns: list[float],
    output_name: str = "fig7_probe_latency.pdf",
):
    """双栏对比图: 基线 vs 探针附加 vs 单次开销."""
    setup_paper_style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))

    x = np.arange(len(probe_names))
    width = 0.35

    # 左侧: 基线 vs 探针附加
    ax1.bar(x - width/2, baseline_means, width, label="Baseline (no eBPF)",
            color="#7fb3d8", edgecolor="white", linewidth=0.5)
    ax1.bar(x + width/2, instrumented_means, width, label="Instrumented (with eBPF)",
            color="#1a5276", edgecolor="white", linewidth=0.5)
    ax1.set_xticks(x)
    ax1.set_xticklabels(probe_names, rotation=30, ha="right")
    ax1.set_ylabel("Wall-clock Time (s)")
    ax1.set_title("Total Runtime: Baseline vs Instrumented")
    ax1.legend(fontsize=7)

    # 右侧: 单次探针开销
    colors = ["#e74c3c" if o > 1000 else "#27ae60" for o in overheads_ns]
    bars = ax2.bar(x, overheads_ns, color=colors, edgecolor="white", linewidth=0.5)
    ax2.set_xticks(x)
    ax2.set_xticklabels(probe_names, rotation=30, ha="right")
    ax2.set_ylabel("Per-Call Overhead (ns)")
    ax2.set_title("Per-Call eBPF Probe Overhead")
    # 标注数值
    for bar, val in zip(bars, overheads_ns):
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(overheads_ns)*0.02,
                f"{val:.0f}", ha="center", fontsize=7)

    plt.tight_layout()
    save_figure(fig, output_name)
    return fig


def memory_timeline_chart(
    timestamps: list[float],
    rss_values: list[float],
    steady_state: float,
    peak: float,
    output_name: str = "fig10_memory_footprint.pdf",
):
    """内存占用的时间序列折线图."""
    setup_paper_style()
    fig, ax = plt.subplots(figsize=(8, 3.5))

    ax.plot(timestamps, rss_values, color="#1a5276", linewidth=0.8, alpha=0.9)
    ax.fill_between(timestamps, 0, rss_values, alpha=0.1, color="#1a5276")
    ax.axhline(y=steady_state, color="#27ae60", linestyle="--", linewidth=1,
               label=f"Steady-State RSS: {steady_state:.1f} MB")
    ax.axhline(y=peak, color="#e74c3c", linestyle=":", linewidth=1,
               label=f"Peak RSS: {peak:.1f} MB")

    ax.set_xlabel("Time (s)")
    ax.set_ylabel("RSS (MB)")
    ax.set_title("Collector Memory Footprint Over Time")
    ax.legend(fontsize=7)

    plt.tight_layout()
    save_figure(fig, output_name)
    return fig


# ── LaTeX 表格生成 ──────────────────────────────────────────────────────────


def latex_table(
    headers: list[str],
    rows: list[list[str]],
    caption: str,
    label: str,
    placement: str = "t",
    bold_row: int = -1,
) -> str:
    """生成论文级 LaTeX 表格."""
    n_cols = len(headers)
    col_spec = "l" + "c" * (n_cols - 1)
    lines = [
        f"\\begin{{table}}[{placement}]",
        f"  \\centering",
        f"  \\caption{{{caption}}}",
        f"  \\label{{{label}}}",
        f"  \\begin{{tabular}}{{{col_spec}}}",
        f"    \\toprule",
    ]
    # Header
    lines.append("    " + " & ".join(f"\\textbf{{{h}}}" for h in headers) + " \\\\")
    lines.append("    \\midrule")
    # Rows
    for i, row in enumerate(rows):
        prefix = "    \\rowcolor{gray!10} " if i == bold_row else "    "
        cell_str = " & ".join(
            f"\\textbf{{{c}}}" if i == bold_row else c for c in row
        )
        lines.append(prefix + cell_str + " \\\\")
    lines.extend([
        "    \\bottomrule",
        "  \\end{tabular}",
        "\\end{table}",
    ])
    return "\n".join(lines)


def write_latex_tables(
    probe_table: str,
    throughput_table: str,
    overhead_table: str,
    memory_table: str,
):
    """将所有 LaTeX 表格写入 perf_tables.tex."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "perf_tables.tex"
    content = "\n\n".join([probe_table, throughput_table, overhead_table, memory_table])
    path.write_text(content)
    print(f"  LaTeX tables written to: {path}")


# ── 报告保存 ────────────────────────────────────────────────────────────────


def save_report(data: dict):
    """将基准报告保存为 JSON."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / "perf_report.json"

    class Encoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, PerfStats):
                return {
                    "min": obj.min, "mean": obj.mean, "median": obj.median,
                    "p95": obj.p95, "p99": obj.p99, "max": obj.max,
                    "stddev": obj.stddev, "n": obj.n, "unit": obj.unit,
                }
            return super().default(obj)

    path.write_text(json.dumps(data, indent=2, cls=Encoder, ensure_ascii=False))
    print(f"  Report saved to: {path}")
