#!/usr/bin/env python3
"""MtAgentLens 论文性能图 — 统一字体规范。

文本：DejaVu Sans (regular / bold)
数学符号：STIXGeneral (μ, Σ 等希腊字母)
数值：DejaVu Sans bold（与文本统一，不用 STIX math bold）
单位：DejaVu Sans regular
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

PROJECT = Path(__file__).resolve().parents[3]
PERF_JSON = PROJECT / "compare" / "results" / "perf" / "perf_report.json"
OUT_DIR = PROJECT / "compare" / "results" / "perf" / "charts"

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "sans-serif"],
    "mathtext.fontset": "stix",
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "font.size": 7,
    "axes.labelsize": 8,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "axes.spines.right": False,
    "axes.spines.top": False,
    "axes.linewidth": 0.5,
    "xtick.major.width": 0.4,
    "ytick.major.width": 0.4,
    "xtick.major.size": 2.5,
    "ytick.major.size": 2.5,
    "legend.frameon": False,
    "axes.unicode_minus": False,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
})

PAL = {
    "hero":      "#0F4D92",
    "hero_lt":   "#5B8EC9",
    "neutral":   "#767676",
    "neutral_d": "#4D4D4D",
    "neutral_b": "#272727",
    "accent":    "#2E8B7B",
    "warn":      "#B64342",
}

# 统一字号层级
SZ_TICK   = 7     # 刻度标签
SZ_LABEL  = 8     # 轴标签
SZ_VALUE  = 9     # 数据标注数值
SZ_UNIT   = 7     # 数据标注单位
SZ_SYMBOL = 8.5   # 数学符号标注 (μ, Σ)


def save_pub(fig, name: str):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for fmt, kw in [(".svg", {}), (".pdf", {}), (".png", {"dpi": 300})]:
        fig.savefig(OUT_DIR / f"{name}{fmt}", bbox_inches="tight",
                    pad_inches=0.06, **kw)
    print(f"  ✓ {name}.{{svg,pdf,png}}")


# ═══════════════════════════════════════════════════════════════════════════
# 图 1 — eBPF 探针延迟
# ═══════════════════════════════════════════════════════════════════════════

def fig_probe_latency(data: dict):
    probes = data["experiments"]["probe_latency"]["probes"]
    labels = ["SSL_rw_enter\n(entry)", "SSL_read_exit\n(exit)", "SSL_write_exit\n(exit)"]
    values = [probes["probe_SSL_rw_enter"]["avg_ns"],
              probes["probe_SSL_read_exit"]["avg_ns"],
              probes["probe_SSL_write_exit"]["avg_ns"]]
    total_ns = data["experiments"]["probe_latency"]["total_per_op_ns"]

    fig, ax = plt.subplots(figsize=(4.8, 2.6))
    x = np.arange(len(labels))

    ax.bar(x, values, 0.50, color=[PAL["hero"], PAL["hero_lt"], PAL["hero_lt"]],
           edgecolor="white", linewidth=0.3, zorder=3)

    for i, val in enumerate(values):
        ax.text(i, val + 2.8, f"{val:.1f}", ha="center", va="bottom",
                fontsize=SZ_VALUE, fontweight="bold", color=PAL["neutral_b"])

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=SZ_TICK, color=PAL["neutral_d"])
    ax.set_ylabel("Latency (ns)", fontsize=SZ_LABEL, color=PAL["neutral"], labelpad=2)
    ax.set_ylim(0, max(values) * 1.28)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(4))
    ax.tick_params(axis="y", colors=PAL["neutral"])

    # Σ 数学符号 (STIX) + 数值 (DejaVu Sans bold) + 单位 (DejaVu Sans regular)
    ax.text(0.97, 0.94,
            r"$\mathbf{\Sigma}$" + f" = {total_ns:.0f} ns",
            transform=ax.transAxes, ha="right", va="top",
            fontsize=SZ_SYMBOL, color=PAL["hero"])

    fig.tight_layout(pad=0.3)
    save_pub(fig, "fig_perf_probe_latency")
    plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════
# 图 2 — Collector 内存占用
# ═══════════════════════════════════════════════════════════════════════════

def fig_memory(data: dict):
    mem = data["experiments"]["memory_footprint"]
    timeline = np.array(mem["rss_timeline"])
    t = np.arange(len(timeline))
    stats = mem["rss_stats"]
    ss = mem["steady_state_rss_mb"]

    fig, ax = plt.subplots(figsize=(5.0, 2.6))

    ax.fill_between(t, timeline, stats["min"] - 1, color=PAL["hero"],
                    alpha=0.04, linewidth=0, zorder=2)
    ax.plot(t, timeline, color=PAL["hero"], linewidth=1.1, zorder=3)

    ax.axhline(stats["mean"], color=PAL["warn"], linewidth=0.4,
               linestyle=(0, (3.5, 2.5)), zorder=1)
    ax.axhline(ss, color=PAL["accent"], linewidth=0.4,
               linestyle=(0, (1.5, 2.5)), zorder=1)

    # μ (STIX) + 数值 (DejaVu Sans) + 单位 (DejaVu Sans) — 统一规范
    ax.annotate(
        r"$\mathbf{\mu}$" + f" = {stats['mean']:.1f} MB",
        xy=(61, stats["mean"]), xytext=(63, stats["mean"] + 0.12),
        fontsize=SZ_SYMBOL, color=PAL["warn"], va="bottom", ha="left",
        bbox=dict(boxstyle="square,pad=0.15", facecolor="white",
                  edgecolor="none", alpha=0.9))

    ax.annotate(
        f"steady = {ss:.1f} MB",
        xy=(61, ss), xytext=(63, ss - 0.12),
        fontsize=SZ_SYMBOL, color=PAL["accent"], va="top", ha="left",
        bbox=dict(boxstyle="square,pad=0.15", facecolor="white",
                  edgecolor="none", alpha=0.9))

    ax.set_xlabel("Time (s)", fontsize=SZ_LABEL, color=PAL["neutral"], labelpad=2)
    ax.set_ylabel("RSS (MB)", fontsize=SZ_LABEL, color=PAL["neutral"], labelpad=2)
    ax.set_xlim(-1.5, 68)
    ax.set_ylim(36.1, 37.5)
    ax.xaxis.set_major_locator(mticker.MultipleLocator(15))
    ax.tick_params(axis="both", colors=PAL["neutral"])

    fig.tight_layout(pad=0.3)
    save_pub(fig, "fig_perf_memory")
    plt.close(fig)


def main() -> int:
    with open(PERF_JSON, encoding="utf-8") as fh:
        perf = json.load(fh)
    print("生成论文级性能图...")
    fig_probe_latency(perf)
    fig_memory(perf)
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
