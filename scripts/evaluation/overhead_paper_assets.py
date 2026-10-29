# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Build publication-quality OverheadBench figures and paper tables.

Inputs are the frozen, already validated exports of ``overhead_report.py``:

    <report>/summary.json
    <report>/raw-per-run.json

The TLS microbenchmark JSON and the three C1 diagnostic windows are optional
probe supplements.  Nothing here re-measures or re-selects conditions; this
module is an analysis/export layer only.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, LogLocator

PROFILES = ("C0", "C1", "C2", "C3")
LEVELS = (1, 4, 8, 16, 32)
LABELS = {
    "C0": "C0  agent only",
    "C1": "C1  capture only",
    "C2": "C2  runtime + DB",
    "C3": "C3  complete system",
}
SHORT = {"C0": "C0", "C1": "C1", "C2": "C2", "C3": "C3"}
COLORS = {
    "C0": "#4D4D4D",
    "C1": "#0072B2",
    "C2": "#E69F00",
    "C3": "#009E73",
    "accent": "#D55E00",
    "muted": "#8C8C8C",
    "grid": "#E6E6E6",
}
MARKERS = ("o", "s", "^", "D")


class AssetError(ValueError):
    """Invalid report input; no partial paper asset set is emitted."""


def _load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise AssetError(f"cannot read {path}: {error}") from error


def load_report(report):
    report = Path(report)
    summary = _load(report / "summary.json")
    raw = _load(report / "raw-per-run.json")
    if summary.get("schema") != "mtagentlens.overhead-summary/1":
        raise AssetError("unexpected summary schema")
    if not raw.get("runs"):
        raise AssetError("raw-per-run.json has no runs")
    return summary, raw


def cell(summary, profile, concurrency):
    key = f"{profile}/{concurrency}"
    if key not in summary["cells"]:
        raise AssetError(f"summary has no cell {key}")
    return summary["cells"][key]


def runs(raw, profile, concurrency):
    return [row for row in raw["runs"]
            if row["profile"] == profile and row["concurrency"] == concurrency
            and row.get("included")]


def _values(raw, profile, concurrency, key):
    return [row[key] for row in runs(raw, profile, concurrency)
            if isinstance(row.get(key), (int, float))]


def _fmt(value, digits=2, suffix=""):
    if value is None:
        return "—"
    return f"{value:,.{digits}f}{suffix}"


def _short_num(value):
    if value is None:
        return "—"
    if abs(value) >= 1e6:
        return f"{value / 1e6:.2f}M"
    if abs(value) >= 1e3:
        return f"{value / 1e3:.1f}k"
    if abs(value) >= 10:
        return f"{value:.0f}"
    return f"{value:.1f}"


def style():
    plt.rcParams.update({
        "font.family": "Nimbus Sans",
        "font.size": 8.2,
        "axes.titlesize": 9.2,
        "axes.labelsize": 8.4,
        "axes.titleweight": "bold",
        "axes.labelcolor": "#222222",
        "text.color": "#222222",
        "xtick.color": "#222222",
        "ytick.color": "#222222",
        "axes.edgecolor": "#333333",
        "axes.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.direction": "out",
        "ytick.direction": "out",
        "xtick.major.width": 0.8,
        "ytick.major.width": 0.8,
        "xtick.major.size": 3.2,
        "ytick.major.size": 3.2,
        "grid.color": COLORS["grid"],
        "grid.linewidth": 0.6,
        "legend.frameon": False,
        "legend.fontsize": 7.6,
        "legend.handlelength": 1.2,
        "legend.handletextpad": 0.6,
        "figure.dpi": 120,
        "savefig.dpi": 450,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.04,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "axes.unicode_minus": False,
    })


def _axis(ax, *, ygrid=True):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if ygrid:
        ax.yaxis.grid(True, color=COLORS["grid"], linewidth=0.6)
        ax.set_axisbelow(True)
    ax.tick_params(direction="out", length=3.2, width=0.8, labelsize=8)
    return ax


def _jitter(count, width=0.08):
    if count <= 1:
        return [0.0]
    span = width * 2
    return [-width + span * index / (count - 1) for index in range(count)]


def _barbar(ax, xs, stats, colors, *, width=0.62, digits=2, fmt="{:.2f}"):
    means = [row["mean"] for row in stats]
    ax.bar(xs, means, width=width, color=colors, edgecolor="none", alpha=0.82,
           zorder=2)
    for x, row in zip(xs, stats):
        if row["mean"] is None:
            continue
        low = row["mean"] - row["min"] if row["min"] is not None else 0
        high = row["max"] - row["mean"] if row["max"] is not None else 0
        ax.errorbar([x], [row["mean"]], yerr=[[low], [high]], fmt="none",
                    ecolor="#333333", elinewidth=0.9, capsize=3.0, capthick=0.9,
                    zorder=3)
        anchor = row["max"] if row["max"] is not None else row["mean"]
        ax.annotate(fmt.format(row["mean"]), (x, anchor), xytext=(0, 4),
                    textcoords="offset points", ha="center", va="bottom",
                    fontsize=7.4, color="#222222")


def _points(ax, xs, value_groups, colors, *, size=13, zorder=5):
    for x, values, color in zip(xs, value_groups, colors):
        for offset, value in zip(_jitter(len(values)), values):
            ax.scatter([x + offset], [value], s=size, facecolor="white",
                       edgecolor=color, linewidth=0.8, zorder=zorder)


def _line_series(ax, xs, stats, *, color, label, marker="o"):
    nan = float("nan")
    means = [row["mean"] if row["mean"] is not None else nan for row in stats]
    if all(value != value for value in means):
        return
    ax.plot(xs, means, color=color, linewidth=1.4, marker=marker, markersize=4.2,
            markerfacecolor=color, markeredgecolor="white", markeredgewidth=0.7,
            label=label, zorder=4)
    low = [row["min"] if row["min"] is not None else nan for row in stats]
    high = [row["max"] if row["max"] is not None else nan for row in stats]
    ax.fill_between(xs, low, high, color=color, alpha=0.14, linewidth=0, zorder=1)


def _save(fig, out, name):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for extension in ("pdf", "svg", "png"):
        fig.savefig(out / f"{name}.{extension}")
    plt.close(fig)


def _note(fig, text, y=0.005):
    """Reserved for in-figure notes; captions are kept in the Markdown report."""
    return None


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def fig_component_walltime(summary, raw, out):
    style()
    fig, ax = plt.subplots(figsize=(3.45, 2.45), constrained_layout=True)
    xs = list(range(len(PROFILES)))
    stats = [cell(summary, profile, 1)["condition_elapsed_s"] for profile in PROFILES]
    colors = [COLORS[profile] for profile in PROFILES]
    _barbar(ax, xs, stats, colors, digits=2)
    _points(ax, xs, [_values(raw, profile, 1, "condition_elapsed_s")
                     for profile in PROFILES], colors)
    ax.set_xticks(xs)
    ax.set_xticklabels(["C0\nagent only", "C1\ncapture", "C2\n+ runtime/DB",
                        "C3\ncomplete"], fontsize=7.4)
    ax.set_ylabel("Condition wall time (s)")
    ax.set_ylim(0, max(row["max"] for row in stats) * 1.28)
    ax.set_title("Component accounting at one agent instance")
    _axis(ax)
    _note(fig, "Bars: mean of three repeats; whiskers: min–max; open markers: repeats.")
    _save(fig, out, "fig01_component_walltime")


def fig_collector_cpu(summary, raw, out):
    style()
    profiles = ("C1", "C2", "C3")
    fig, ax = plt.subplots(figsize=(3.1, 2.35), constrained_layout=True)
    xs = list(range(len(profiles)))
    stats = [cell(summary, profile, 1)["collector_cpu_seconds"] for profile in profiles]
    colors = [COLORS[profile] for profile in profiles]
    _barbar(ax, xs, stats, colors, fmt="{:.2f}")
    _points(ax, xs, [_values(raw, profile, 1, "collector_cpu_seconds")
                     for profile in profiles], colors)
    ax.set_xticks(xs)
    ax.set_xticklabels([SHORT[p] for p in profiles])
    ax.set_ylabel("Collector CPU time (CPU·s)")
    ax.set_ylim(0, max(row["max"] for row in stats) * 1.35)
    ax.set_title("Collector CPU cost at one instance")
    _axis(ax)
    _note(fig, "Whole-condition CPU time, all collector processes; n=3.")
    _save(fig, out, "fig02_collector_cpu")


def fig_peak_rss(summary, raw, out):
    style()
    profiles = ("C1", "C2", "C3")
    fig, ax = plt.subplots(figsize=(3.1, 2.35), constrained_layout=True)
    xs = list(range(len(profiles)))
    stats = [cell(summary, profile, 1)["collector_rss_peak_mib"] for profile in profiles]
    colors = [COLORS[profile] for profile in profiles]
    _barbar(ax, xs, stats, colors, fmt="{:.0f}")
    _points(ax, xs, [_values(raw, profile, 1, "collector_rss_peak_mib")
                     for profile in profiles], colors)
    ax.set_xticks(xs)
    ax.set_xticklabels([SHORT[p] for p in profiles])
    ax.set_ylabel("Peak RSS (MiB)")
    ax.set_ylim(0, max(row["max"] for row in stats) * 1.28)
    ax.set_title("Collector peak memory at one instance")
    _axis(ax)
    _note(fig, "Peak of the collector process tree; bars are means, n=3.")
    _save(fig, out, "fig03_peak_rss")


def fig_storage_accounting(summary, raw, out):
    style()
    fig, ax = plt.subplots(figsize=(3.45, 2.35), constrained_layout=True)
    profiles = ("C2", "C3")
    xs = [0, 1]
    db = [cell(summary, p, 1)["db_total_peak_mib"] for p in profiles]
    trace = [cell(summary, p, 1)["trace_final_mib"] for p in profiles]
    width = 0.34
    ax.bar([x - width / 2 for x in xs], [row["mean"] for row in db],
           width=width, color="#56B4E9", edgecolor="none", alpha=0.9,
           label="DB + WAL (peak)")
    ax.bar([x + width / 2 for x in xs], [row["mean"] for row in trace],
           width=width, color="#CC79A7", edgecolor="none", alpha=0.9,
           label="trace (final)")
    for x, row_db, row_trace in zip(xs, db, trace):
        ax.errorbar([x - width / 2], [row_db["mean"]],
                    yerr=[[row_db["mean"] - row_db["min"]],
                          [row_db["max"] - row_db["mean"]]],
                    fmt="none", ecolor="#333333", elinewidth=0.9, capsize=3)
        ax.errorbar([x + width / 2], [row_trace["mean"]],
                    yerr=[[row_trace["mean"] - row_trace["min"]],
                          [row_trace["max"] - row_trace["mean"]]],
                    fmt="none", ecolor="#333333", elinewidth=0.9, capsize=3)
    ax.set_xticks(xs)
    ax.set_xticklabels([SHORT[p] for p in profiles])
    ax.set_ylabel("Storage (MiB)")
    ax.set_title("Persistence footprint at one instance")
    ax.text(0 - width / 2, db[0]["max"] + 0.14, "DB+WAL", ha="center",
            va="bottom", fontsize=7.2, color="#2C8CBF")
    ax.text(0 + width / 2, trace[0]["max"] + 0.14, "trace", ha="center",
            va="bottom", fontsize=7.2, color="#A85A8A")
    ax.set_ylim(0, max(max(db[0]["max"], db[1]["max"]),
                       max(trace[0]["max"], trace[1]["max"])) * 1.2)
    _axis(ax)
    _note(fig, "C1 has no responsibility database; bars are means, whiskers min–max.")
    _save(fig, out, "fig04_storage_accounting")


def _scaling_figure(summary, raw, out, *, key, ylabel, title, name, logy=True,
                    profiles=("C0", "C3"), digits=1, suffix="", note=None):
    style()
    fig, ax = plt.subplots(figsize=(3.55, 2.6), constrained_layout=True)
    xs = list(range(len(LEVELS)))
    for profile in profiles:
        stats = [cell(summary, profile, level)[key] for level in LEVELS]
        _line_series(ax, xs, stats, color=COLORS[profile], label=LABELS[profile],
                     marker="o" if profile == "C0" else "D")
        _points(ax, xs, [_values(raw, profile, level, key) for level in LEVELS],
                [COLORS[profile]] * len(LEVELS), size=11)
    ax.set_xscale("linear")
    ax.set_xticks(xs)
    ax.set_xticklabels([str(level) for level in LEVELS])
    ax.set_xlabel("Concurrent agent instances")
    ax.set_ylabel(ylabel)
    if logy:
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(LogLocator(base=10, numticks=6))
        ax.yaxis.set_minor_locator(LogLocator(base=10, subs=tuple(range(2, 10)),
                                              numticks=12))
        ax.yaxis.set_major_formatter(FuncFormatter(
            lambda value, _position: f"{value:g}"))
    ax.set_title(title)
    ax.legend(loc="upper left")
    _axis(ax)
    _note(fig, note or "Bars/points: three repeats; line: mean; band: min–max.")
    _save(fig, out, name)


def fig_walltime_scaling(summary, raw, out):
    _scaling_figure(summary, raw, out, key="condition_elapsed_s",
                    ylabel="Condition wall time (s)",
                    title="End-to-end condition cost vs concurrency",
                    name="fig05_walltime_scaling", logy=True,
                    note="C0 has no collector phase; C3 includes phase-2 review and drain.")


def fig_rss_scaling(summary, raw, out):
    _scaling_figure(summary, raw, out, key="collector_rss_peak_mib",
                    ylabel="Peak collector RSS (MiB)",
                    title="Collector memory vs concurrency",
                    name="fig06_rss_scaling", logy=False,
                    note="Only C3 has a collector; C0 line is absent by design.")


def fig_cpu_scaling(summary, raw, out):
    _scaling_figure(summary, raw, out, key="collector_cpu_seconds",
                    ylabel="Collector CPU time (CPU·s)",
                    title="Collector CPU cost vs concurrency",
                    name="fig07_cpu_scaling", logy=True,
                    note="Whole-condition CPU time; C3 only (C0 has no collector).")


def fig_storage_scaling(summary, raw, out):
    style()
    fig, ax = plt.subplots(figsize=(3.55, 2.6), constrained_layout=True)
    xs = list(range(len(LEVELS)))
    db = [cell(summary, "C3", level)["db_total_peak_mib"] for level in LEVELS]
    trace = [cell(summary, "C3", level)["trace_final_mib"] for level in LEVELS]
    _line_series(ax, xs, db, color="#56B4E9", label="DB + WAL (peak)")
    _line_series(ax, xs, trace, color="#CC79A7", label="trace (final)", marker="s")
    for series, values in ((db, "db_total_peak_mib"), (trace, "trace_final_mib")):
        _points(ax, xs, [_values(raw, "C3", level, values) for level in LEVELS],
                ["#56B4E9" if values == "db_total_peak_mib" else "#CC79A7"] * len(LEVELS),
                size=10)
    ax.set_yscale("log")
    ax.set_xticks(xs)
    ax.set_xticklabels([str(level) for level in LEVELS])
    ax.set_xlabel("Concurrent agent instances")
    ax.set_ylabel("Storage (MiB)")
    ax.set_title("Persistence growth vs concurrency")
    ax.legend(loc="upper left")
    ax.yaxis.set_major_locator(LogLocator(base=10, numticks=6))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    _axis(ax)
    _note(fig, "C3 only; DB+WAL is the peak, trace is the final file size.")
    _save(fig, out, "fig08_storage_scaling")


def _source_lost(raw, profile, concurrency, prefix):
    values = []
    for run in runs(raw, profile, concurrency):
        total = 0
        found = False
        for name, source in (run.get("layer_counters") or {}).get("sources", {}).items():
            if name.startswith(prefix):
                total += int(source.get("lost") or 0)
                found = True
        values.append(total if found else None)
    return values


def fig_capture_quality(summary, raw, out):
    style()
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.65), constrained_layout=True,
                             gridspec_kw={"width_ratios": (1.05, 1.0)})
    ax = axes[0]
    xs = list(range(len(LEVELS)))
    totals = [cell(summary, "C3", level)["alert_total_operations"]["mean"]
              for level in LEVELS]
    ax.bar(xs, totals, width=0.62, color="#D9D9D9", edgecolor="none", zorder=2)
    for x, total in zip(xs, totals):
        ax.annotate(f"0/{int(total)}", (x, 0), xytext=(0, 6),
                    textcoords="offset points", ha="center", va="bottom",
                    fontsize=7.4, color="#1B5E20")
    ax.set_xticks(xs)
    ax.set_xticklabels([str(level) for level in LEVELS])
    ax.set_xlabel("Concurrent agent instances")
    ax.set_ylabel("Completed controlled operations")
    ax.set_ylim(0, max(totals) * 1.22)
    ax.set_title("(a) Controlled operations: 0 missing")
    _axis(ax)
    ax = axes[1]
    process = _source_lost(raw, "C3", 32, "Process")
    tls = _source_lost(raw, "C3", 32, "SSL")
    positions = [0, 1]
    values = [process, tls]
    labels = ["Process source", "TLS source"]
    for position, group, color, label in zip(positions, values, ("#D55E00", "#0072B2"),
                                             labels):
        clean = [value for value in group if value is not None]
        if not clean:
            continue
        mean = sum(clean) / len(clean)
        ax.bar([position], [mean], width=0.55, color=color, alpha=0.82,
               edgecolor="none", zorder=2)
        ax.errorbar([position], [mean],
                    yerr=[[mean - min(clean)], [max(clean) - mean]],
                    fmt="none", ecolor="#333333", elinewidth=0.9, capsize=3)
        for offset, value in zip(_jitter(len(clean), 0.07), clean):
            ax.scatter([position + offset], [value], s=14, facecolor="white",
                       edgecolor=color, linewidth=0.8, zorder=4)
    ax.set_yscale("symlog", linthresh=1)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Ring-buffer drops at 32 instances")
    ax.set_title("(b) Source-layer drops (32 instances)")
    _axis(ax, ygrid=True)
    ax.annotate("13.4k–16.4k", (0, max(process or [0])), xytext=(0, 6),
                textcoords="offset points", ha="center", fontsize=7.2,
                color="#D55E00")
    ax.annotate("0", (1, 0.35), xytext=(0, 6), textcoords="offset points",
                ha="center", fontsize=7.6, color="#0072B2")
    ax.scatter([1], [0.35], s=26, marker="_", color="#0072B2", linewidth=1.6,
               zorder=5)
    _note(fig, "Loss denominators are successfully completed controlled file operations; "
               "source drops are a different layer and are never added into the loss rate.")
    _save(fig, out, "fig09_capture_quality")


def fig_alert_latency(summary, raw, out):
    style()
    fig, ax = plt.subplots(figsize=(3.55, 2.6), constrained_layout=True)
    xs = list(range(len(LEVELS)))
    p50 = [cell(summary, "C3", level)["alert_p50_ms"] for level in LEVELS]
    p95 = [cell(summary, "C3", level)["alert_p95_ms"] for level in LEVELS]
    observer = [cell(summary, "C3", level)["observer_p50_ms"] for level in LEVELS]
    _line_series(ax, xs, p50, color="#0072B2", label="linked alert p50")
    _line_series(ax, xs, p95, color="#D55E00", label="linked alert p95", marker="s")
    _line_series(ax, xs, observer, color="#777777", label="Observer API p50",
                 marker="^")
    coverage = []
    for level in LEVELS:
        linked = cell(summary, "C3", level)["alert_linked_operations"]["mean"]
        total = cell(summary, "C3", level)["alert_total_operations"]["mean"]
        coverage.append(f"{linked:.0f}/{total:.0f}")
    ax.set_yscale("log")
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{level}\n{text}" for level, text in zip(LEVELS, coverage)])
    ax.set_xlabel("Concurrent agent instances\n(mean linked/total per run)")
    ax.set_ylabel("Linked final-emission latency (ms)")
    ax.set_title("Linked alert latency and coverage")
    ax.legend(loc="upper left")
    ax.yaxis.set_major_locator(LogLocator(base=10, numticks=8))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    _axis(ax)
    _note(fig, "Only linked captured-action → final broadcast samples; coverage is "
               "linked/total controlled operations, not a miss rate.")
    _save(fig, out, "fig10_alert_latency")


def fig_observer_cost(summary, raw, out):
    style()
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.7), constrained_layout=True)
    xs = list(range(len(LEVELS)))
    attempts = [cell(summary, "C3", level)["observer_attempts"] for level in LEVELS]
    cost = [cell(summary, "C3", level)["observer_cost_upper_bound_usd"] for level in LEVELS]
    ax = axes[0]
    ax.bar(xs, [row["mean"] for row in attempts], width=0.62, color=COLORS["C3"],
           alpha=0.82, edgecolor="none", zorder=2)
    for x, row in zip(xs, attempts):
        ax.errorbar([x], [row["mean"]],
                    yerr=[[row["mean"] - row["min"]], [row["max"] - row["mean"]]],
                    fmt="none", ecolor="#333333", elinewidth=0.9, capsize=3)
        anchor = row["max"] if row["max"] is not None else row["mean"]
        ax.annotate(f"{row['mean']:.1f}", (x, anchor), xytext=(0, 4),
                    textcoords="offset points", ha="center", fontsize=7.2)
    ax.set_xticks(xs)
    ax.set_xticklabels([str(level) for level in LEVELS])
    ax.set_xlabel("Concurrent agent instances")
    ax.set_ylabel("Observer attempts per condition")
    ax.set_title("(a) Real Observer calls")
    ax.set_ylim(0, max(row["max"] for row in attempts) * 1.3)
    _axis(ax)
    ax = axes[1]
    means = [row["mean"] for row in cost]
    ax.bar(xs, means, width=0.62, color="#CC79A7", alpha=0.86, edgecolor="none",
           zorder=2)
    for x, row in zip(xs, cost):
        ax.errorbar([x], [row["mean"]],
                    yerr=[[row["mean"] - row["min"]], [row["max"] - row["mean"]]],
                    fmt="none", ecolor="#333333", elinewidth=0.9, capsize=3)
        anchor = row["max"] if row["max"] is not None else row["mean"]
        ax.annotate(f"${row['mean']:.3f}", (x, anchor), xytext=(0, 4),
                    textcoords="offset points", ha="center", fontsize=7.0)
    ax.set_xticks(xs)
    ax.set_xticklabels([str(level) for level in LEVELS])
    ax.set_xlabel("Concurrent agent instances")
    ax.set_ylabel("Observer cost upper bound (USD)")
    ax.set_title("(b) Observer cost per condition")
    ax.set_ylim(0, max(row["max"] for row in cost) * 1.38)
    _axis(ax)
    _note(fig, "Peak-rate upper bound from real usage; not an invoice. n=3.")
    _save(fig, out, "fig11_observer_cost")


def fig_graph_cost(summary, raw, out):
    style()
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.7), constrained_layout=True)
    xs = list(range(len(LEVELS)))
    nodes = [cell(summary, "C3", level)["graph_nodes_final"] for level in LEVELS]
    edges = [cell(summary, "C3", level)["graph_edges_final"] for level in LEVELS]
    ax = axes[0]
    _line_series(ax, xs, nodes, color="#0072B2", label="nodes")
    _line_series(ax, xs, edges, color="#009E73", label="edges", marker="s")
    ax.set_yscale("log")
    ax.set_xticks(xs)
    ax.set_xticklabels([str(level) for level in LEVELS])
    ax.set_xlabel("Concurrent agent instances")
    ax.set_ylabel("Final persisted graph size")
    ax.set_title("(a) Responsibility graph size")
    ax.legend(loc="upper left")
    ax.yaxis.set_major_locator(LogLocator(base=10, numticks=6))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    _axis(ax)
    ax = axes[1]
    total_ms = [{"mean": row["mean"] / 1e6,
                 "min": row["min"] / 1e6 if row["min"] is not None else None,
                 "max": row["max"] / 1e6 if row["max"] is not None else None,
                 "n": row["n"]}
                for row in (cell(summary, "C3", level)["graph_update_total_ns"]
                            for level in LEVELS)]
    _line_series(ax, xs, total_ms, color="#D55E00", label="graph update total")
    ax.set_yscale("log")
    ax.set_xticks(xs)
    ax.set_xticklabels([str(level) for level in LEVELS])
    ax.set_xlabel("Concurrent agent instances")
    ax.set_ylabel("Online graph update time (ms)")
    ax.set_title("(b) Graph update cost")
    ax.yaxis.set_major_locator(LogLocator(base=10, numticks=6))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    _axis(ax)
    _note(fig, "Graph timing covers online builder + delta buffer only; it excludes "
               "phase-2 correlation, scoring, snapshots and DB waits.")
    _save(fig, out, "fig12_graph_cost")


def _probe_rows(tls, diagnostics):
    """Keep the TLS microbenchmark and the C1 diagnostic windows separate."""
    rows = {}
    for repeat in tls.get("repeats", []):
        for row in repeat.get("programs", []):
            if not row.get("covered") or not isinstance(row.get("ns_per_call"),
                                                           (int, float)):
                continue
            entry = rows.setdefault(row.get("name"),
                                    {"source": "TLS microbenchmark",
                                     "ns": [], "counts": []})
            entry["ns"].append(row["ns_per_call"])
            if isinstance(row.get("run_cnt_delta"), (int, float)):
                entry["counts"].append(row["run_cnt_delta"])
    for data in diagnostics:
        for row in data.get("programs", []):
            if row.get("name", "").startswith("probe_SSL"):
                continue  # never merge a diagnostic window into the TLS benchmark
            if not row.get("covered") or not isinstance(row.get("ns_per_call"),
                                                       (int, float)):
                continue
            entry = rows.setdefault(row.get("name"),
                                    {"source": "C1 diagnostic",
                                     "ns": [], "counts": []})
            entry["ns"].append(row["ns_per_call"])
            if isinstance(row.get("run_cnt_delta"), (int, float)):
                entry["counts"].append(row["run_cnt_delta"])
    return rows


def fig_probe_cost(summary, raw, out, *, probes, diagnostics):
    style()
    tls, diagnostics_data = probes
    rows = _probe_rows(tls, diagnostics_data)
    selected = [(name, values) for name, values in rows.items()
                if values["ns"] and name in (
                    "probe_SSL_rw_enter", "probe_SSL_read_exit", "probe_SSL_write_exit",
                    "enter_syscall", "complete_syscall", "handle_exec", "handle_exit")]
    labels = {
        "probe_SSL_rw_enter": "SSL rw enter",
        "probe_SSL_read_exit": "SSL read exit",
        "probe_SSL_write_exit": "SSL write exit",
        "enter_syscall": "syscall enter",
        "complete_syscall": "syscall complete",
        "handle_exec": "exec",
        "handle_exit": "exit",
    }
    fig, ax = plt.subplots(figsize=(3.7, 2.8), constrained_layout=True)
    ys = list(range(len(selected)))[::-1]
    colors = ["#0072B2" if name.startswith("probe_SSL") else "#E69F00"
              for name, _ in selected]
    means = [sum(values["ns"]) / len(values["ns"]) for _, values in selected]
    ax.barh(ys, means, height=0.62, color=colors, alpha=0.85, edgecolor="none",
            zorder=2)
    for y, (name, values) in zip(ys, selected):
        mean = sum(values["ns"]) / len(values["ns"])
        ax.errorbar([mean], [y], xerr=[[mean - min(values["ns"])],
                                       [max(values["ns"]) - mean]],
                    fmt="none", ecolor="#333333", elinewidth=0.9, capsize=3)
        anchor = max(values["ns"])
        ax.annotate(f"{mean:.1f} ns", (anchor, y), xytext=(4, 0),
                    textcoords="offset points", va="center", fontsize=7.0)
    ax.set_yticks(ys)
    ax.set_yticklabels([labels[name] for name, _ in selected])
    ax.set_xscale("log")
    ax.set_xlabel("BPF program execution time (ns/call, log scale)")
    ax.set_title("Measured BPF program cost")
    ax.xaxis.set_major_locator(LogLocator(base=10, numticks=8))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    ax.set_xlim(40, 20000)
    _axis(ax, ygrid=False)
    ax.xaxis.grid(True, color=COLORS["grid"], linewidth=0.6)
    ax.set_axisbelow(True)
    _note(fig, "TLS: 3 loss-free repeats, 256 B payload. Other programs: 3 short C1 "
               "diagnostic windows and may include fast filtered returns.")
    _save(fig, out, "fig13_probe_cost")


def fig_task_delta(summary, raw, out):
    style()
    fig, ax = plt.subplots(figsize=(3.6, 2.55), constrained_layout=True)
    xs = list(LEVELS)
    for index, level in enumerate(xs):
        rows = [row for row in raw.get("paired_overheads", [])
                if row["concurrency"] == level
                and row.get("agent_duration_increase_percent") is not None]
        values = [row["agent_duration_increase_percent"] for row in rows]
        if not values:
            continue
        mean = sum(values) / len(values)
        ax.scatter([index] * len(values), values, s=16, facecolor="white",
                   edgecolor="#0072B2", linewidth=0.9, zorder=4)
        ax.scatter([index], [mean], s=34, marker="D", color="#D55E00", zorder=5)
    ax.axhline(0, color="#555555", linewidth=0.9, zorder=1)
    ax.set_xticks(range(len(xs)))
    ax.set_xticklabels([str(level) for level in LEVELS])
    ax.set_xlabel("Concurrent agent instances")
    ax.set_ylabel("Paired C3 − C0 agent duration (%)")
    ax.set_title("Fixed-task duration difference")
    ax.set_ylim(-30, 30)
    _axis(ax)
    ax.scatter([], [], s=16, facecolor="white", edgecolor="#0072B2",
               linewidth=0.9, label="repeat block")
    ax.scatter([], [], s=34, marker="D", color="#D55E00", label="mean")
    ax.legend(loc="lower right", ncol=1)
    _note(fig, "Three paired blocks per level; negative values retained. Descriptive "
               "only; no significance claim from n=3.")
    _save(fig, out, "fig14_task_delta")


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def _stat(row, key, digits=2, scale=1.0, suffix=""):
    value = row.get(key)
    if not isinstance(value, dict) or value.get("mean") is None:
        return "—"
    mean = value["mean"] / scale
    low = value["min"] / scale if value.get("min") is not None else mean
    high = value["max"] / scale if value.get("max") is not None else mean
    return f"{mean:,.{digits}f}{suffix} [{low:,.{digits}f}–{high:,.{digits}f}]"


def _write_csv(path, header, rows):
    with Path(path).open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _write_md(path, title, header, rows, note=""):
    lines = [f"### {title}", "", "| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] + ["---:"] * (len(header) - 1)) + "|"]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |"
                 for row in rows)
    if note:
        lines.extend(["", note])
    lines.append("")
    Path(path).write_text("\n".join(lines))


def table_component(summary, out):
    metrics = [
        ("Condition wall time (s)", "condition_elapsed_s", 2, 1),
        ("Agent end-to-end (s)", "agent_duration_s", 2, 1),
        ("Collector CPU (CPU·s)", "collector_cpu_seconds", 2, 1),
        ("Collector CPU mean (%)", "collector_cpu_mean_percent", 2, 1),
        ("Peak RSS (MiB)", "collector_rss_peak_mib", 1, 1),
        ("DB + WAL peak (MiB)", "db_total_peak_mib", 2, 1),
        ("Trace final (MiB)", "trace_final_mib", 2, 1),
        ("Graph nodes (final)", "graph_nodes_final", 1, 1),
        ("Graph edges (final)", "graph_edges_final", 1, 1),
        ("Graph updates (count)", "graph_update_count", 1, 1),
        ("Graph update total (ms)", "graph_update_total_ns", 2, 1e6),
        ("Observer attempts", "observer_attempts", 1, 1),
        ("Observer input tokens", "observer_input_tokens", 0, 1),
        ("Observer output tokens", "observer_output_tokens", 0, 1),
        ("Observer cost UB (USD)", "observer_cost_upper_bound_usd", 6, 1),
        ("Total cost UB (USD)", "total_cost_upper_bound_usd", 6, 1),
    ]
    header = ["Metric", "C0 (1)", "C1 (1)", "C2 (1)", "C3 (1)"]
    rows = []
    for label, key, digits, scale in metrics:
        row = [label]
        for profile in PROFILES:
            row.append(_stat(cell(summary, profile, 1), key, digits, scale))
        rows.append(row)
    _write_csv(Path(out) / "table1_component_accounting.csv",
               ["metric"] + [f"{p}_1" for p in PROFILES], rows)
    _write_md(Path(out) / "table1_component_accounting.md",
              "T1. Component accounting at one concurrent agent instance",
              header, rows,
              "Values are mean [min–max] over three repeats. C0 has no collector; C1 has no responsibility DB or graph. C2−C1 is the combined runtime+DB increment, not graph-only. Costs are peak-rate upper bounds, not invoices.")


def table_scaling(summary, out):
    metrics = [
        ("Condition wall time (s)", "condition_elapsed_s", 2, 1),
        ("Agent end-to-end (s)", "agent_duration_s", 2, 1),
        ("Collector CPU (CPU·s)", "collector_cpu_seconds", 2, 1),
        ("Collector CPU mean (%)", "collector_cpu_mean_percent", 1, 1),
        ("Peak RSS (MiB)", "collector_rss_peak_mib", 1, 1),
        ("DB + WAL peak (MiB)", "db_total_peak_mib", 2, 1),
        ("Trace final (MiB)", "trace_final_mib", 2, 1),
        ("Graph nodes/edges", "graph_nodes_final", 1, 1),
        ("Graph update total (ms)", "graph_update_total_ns", 2, 1e6),
        ("Observer attempts", "observer_attempts", 1, 1),
        ("Observer cost UB (USD)", "observer_cost_upper_bound_usd", 6, 1),
    ]
    header = ["Metric", "C0/1", "C0/4", "C0/8", "C0/16", "C0/32",
              "C3/1", "C3/4", "C3/8", "C3/16", "C3/32"]
    rows = []
    for label, key, digits, scale in metrics:
        row = [label]
        for profile in ("C0", "C3"):
            for level in LEVELS:
                value = _stat(cell(summary, profile, level), key, digits, scale)
                if key == "graph_nodes_final":
                    edges = _stat(cell(summary, profile, level), "graph_edges_final",
                                  digits, scale)
                    value = f"{value.split(' [')[0]}/{edges.split(' [')[0]}"
                row.append(value)
        rows.append(row)
    _write_csv(Path(out) / "table2_scaling.csv",
               ["metric"] + [f"{p}_{l}" for p in ("C0", "C3") for l in LEVELS], rows)
    _write_md(Path(out) / "table2_scaling.md",
              "T2. Complete-system scaling and baseline by concurrency",
              header, rows,
              "Mean [min–max] over three repeats per cell. C1/C2 are not planned at 4/8/16/32. C0 has no collector resources or Observer. Costs are peak-rate upper bounds.")


def table_capture(summary, raw, out):
    header = ["Profile/conc", "Raw missing (fraction)", "Persisted loss (fraction)", "Process drops",
              "TLS drops", "Linked/total", "Coverage", "p50 (ms)", "p95 (ms)"]
    rows = []
    for profile, level in (("C1", 1), ("C2", 1), ("C3", 1), ("C3", 4), ("C3", 8),
                           ("C3", 16), ("C3", 32)):
        row = cell(summary, profile, level)
        process = _source_lost(raw, profile, level, "Process")
        tls = _source_lost(raw, profile, level, "SSL")
        process_text = "—" if not any(value is not None for value in process) else \
            f"{min(v for v in process if v is not None):,}–{max(v for v in process if v is not None):,}"
        tls_text = "—" if not any(value is not None for value in tls) else \
            f"{min(v for v in tls if v is not None):,}–{max(v for v in tls if v is not None):,}"
        linked = row["alert_linked_operations"]["mean"]
        total = row["alert_total_operations"]["mean"]
        coverage = row["alert_coverage"]["mean"]
        rows.append([
            f"{profile}/{level}",
            _stat(row, "raw_missing_fraction", 3),
            _stat(row, "persisted_operation_missing_fraction", 3)
            if profile != "C1" else "—",
            process_text, tls_text,
            f"{linked:.1f}/{total:.0f}",
            f"{coverage * 100:.2f}%" if coverage is not None else "—",
            _stat(row, "alert_p50_ms", 1),
            _stat(row, "alert_p95_ms", 1),
        ])
    _write_csv(Path(out) / "table3_capture_alert.csv", header, rows)
    _write_md(Path(out) / "table3_capture_alert.md",
              "T3. Controlled-operation integrity, source-layer drops and alert latency",
              header, rows,
              "Controlled missing is measured against completed controlled file operations, not global events. Source-layer drops are separate counters and are never added into the missing fraction. Alert latency covers only linked captured-action → final collector broadcast samples. Coverage is linked/total controlled operations, not a miss rate.")


def table_observer_graph(summary, out):
    header = ["Conc", "Observer attempts", "Input tokens", "Output tokens",
              "Observer cost UB", "Graph nodes", "Graph edges", "Graph updates",
              "Graph total (ms)", "DB+WAL (MiB)", "Trace (MiB)"]
    rows = []
    for level in LEVELS:
        row = cell(summary, "C3", level)
        rows.append([
            level,
            _stat(row, "observer_attempts", 1),
            _stat(row, "observer_input_tokens", 0),
            _stat(row, "observer_output_tokens", 0),
            _stat(row, "observer_cost_upper_bound_usd", 6),
            _stat(row, "graph_nodes_final", 1),
            _stat(row, "graph_edges_final", 1),
            _stat(row, "graph_update_count", 0),
            _stat(row, "graph_update_total_ns", 2, 1e6),
            _stat(row, "db_total_peak_mib", 2),
            _stat(row, "trace_final_mib", 2),
        ])
    _write_csv(Path(out) / "table4_observer_graph.csv", header, rows)
    _write_md(Path(out) / "table4_observer_graph.md",
              "T4. Observer, responsibility graph and persistence for C3",
              header, rows,
              "Mean [min–max] over three repeats. Graph timing excludes phase-2 correlation, scoring, snapshots and DB waits. Cost is a peak-rate upper bound from real usage.")


def table_probes(probes, diagnostics, out):
    tls, diagnostic_data = probes
    rows_map = _probe_rows(tls, diagnostic_data)
    labels = {
        "probe_SSL_rw_enter": "TLS read/write entry",
        "probe_SSL_read_exit": "TLS read exit",
        "probe_SSL_write_exit": "TLS write exit",
        "enter_syscall": "syscall entry",
        "complete_syscall": "syscall completion",
        "handle_exec": "process exec",
        "handle_exit": "process exit",
    }
    header = ["Program", "Source", "Covered windows", "ns/call (mean [min–max])"]
    rows = []
    for name, values in rows_map.items():
        if not values["ns"] or name not in labels:
            continue
        n = len(values["ns"])
        mean = sum(values["ns"]) / n
        rows.append([labels[name], values["source"], n,
                     f"{mean:.1f} [{min(values['ns']):.1f}–{max(values['ns']):.1f}]"])
    summary = tls.get("summary", {}).get("ns_per_successful_ssl_operation", {})
    if summary.get("mean") is not None:
        rows.append(["TLS successful read+write operation",
                     "Microbenchmark aggregate", summary.get("n", 0),
                     f"{summary['mean']:.1f} [{summary['min']:.1f}–{summary['max']:.1f}]"])
    _write_csv(Path(out) / "table5_probes.csv", header, rows)
    _write_md(Path(out) / "table5_probes.md",
              "T5. Measured BPF program cost",
              header, rows,
              "TLS: 256 B payload, 256 iterations, 16 warm-up, three loss-free repeats. Diagnostic windows may include fast filtered returns and system-wide triggers; values are not a per-effective-event processing cost and do not produce a single-call p95.")


def table_task_delta(raw, out):
    header = ["Concurrency", "Block", "Agent duration increase (%)",
              "Batch wall-span increase (%)"]
    rows = []
    for row in raw.get("paired_overheads", []):
        rows.append([row["concurrency"], row["block_id"],
                     f"{row['agent_duration_increase_percent']:.2f}"
                     if row.get("agent_duration_increase_percent") is not None else "—",
                     f"{row['agent_wall_span_increase_percent']:.2f}"
                     if row.get("agent_wall_span_increase_percent") is not None else "—"])
    _write_csv(Path(out) / "table6_task_delta.csv", header, rows)
    _write_md(Path(out) / "table6_task_delta.md",
              "T6. Paired C3 versus C0 task duration by repeat block",
              header, rows,
              "Only blocks with 100% completed fixed work are compared. Negative values are retained. Three descriptive repeats per concurrency; no significance claim.")


def table_ledger(report, out):
    execution = _load(Path(report) / ".." / "formal" / "execution-report.json")
    amendment_path = Path(report) / ".." / "formal-recovery-amendment.json"
    amendment = _load(amendment_path) if amendment_path.is_file() else {}
    header = ["Field", "Value"]
    archived = execution.get("recovery", {}).get("archived_attempts", [])
    rows = [
        ["Formal conditions completed", f"{execution.get('conditions_completed')}/36"],
        ["Execution report complete", str(bool(execution.get("complete")))],
        ["Frozen plan SHA-256", execution.get("plan_sha256", "—")],
        ["Formal known cost upper bound", f"${execution.get('known_cost_upper_bound_usd', 0):.6f}"],
        ["Prior cohort cost upper bound", f"${execution.get('prior_cost_upper_bound_usd', 0):.6f}"],
        ["All cohort cost upper bound", f"${execution.get('all_cohort_cost_upper_bound_usd', 0):.6f}"],
        ["All cohort censored reserve", f"${execution.get('all_cohort_censored_reserve_usd', 0):.6f}"],
        ["All cohort observed usage upper bound", f"${execution.get('all_cohort_observed_usage_upper_bound_usd', 0):.6f}"],
        ["Archived truncated attempt", archived[0]["condition_id"] if archived else "—"],
        ["Archived attempt path", archived[0]["path"] if archived else "—"],
        ["Recovery guard", (f"{amendment.get('frozen_shutdown_timeout_s')} s → "
                            f"{amendment.get('recovery_shutdown_timeout_s')} s")
         if amendment else "—"],
        ["Recovery status", amendment.get("recovered_status", "—")],
        ["Recovery wall time", f"{amendment.get('recovered_condition_elapsed_s', 0):.2f} s"]
        if amendment else "—",
        ["Recovery cost upper bound", f"${amendment.get('cost_upper_bound_usd', 0):.6f}"]
        if amendment else "—",
    ]
    _write_csv(Path(out) / "table7_ledger.csv", header, rows)
    _write_md(Path(out) / "table7_ledger.md",
              "T7. Execution, recovery and cost ledger",
              header, rows,
              "Costs are conservative peak-rate upper bounds from real usage, not invoices. The archived attempt remains counted; the recovery changed only the harness shutdown guard.")


def table_all_conditions(raw, out):
    header = ["Condition", "Profile", "Conc", "Repeat", "Status", "Wall (s)",
              "Peak RSS (MiB)", "Observer attempts", "Observer cost UB",
              "Linked/total", "Raw missing", "Persisted loss"]
    rows = []
    for row in raw["runs"]:
        rows.append([
            row["condition_id"], row["profile"], row["concurrency"], row["repeat"],
            row.get("result_status"), f"{row['condition_elapsed_s']:.2f}",
            f"{row['collector_rss_peak_mib']:.1f}"
            if row.get("collector_rss_peak_mib") is not None else "—",
            row.get("observer_attempts", "—"),
            f"{row['observer_cost_upper_bound_usd']:.6f}"
            if row.get("observer_cost_upper_bound_usd") is not None else "—",
            f"{row.get('alert_linked_operations', 0)}/{row.get('alert_total_operations', 0)}",
            f"{row['raw_missing_fraction']:.4f}"
            if row.get("raw_missing_fraction") is not None else "—",
            f"{row['persisted_operation_missing_fraction']:.4f}"
            if row.get("persisted_operation_missing_fraction") is not None else "—",
        ])
    _write_csv(Path(out) / "tableA1_all_conditions.csv", header, rows)
    _write_md(Path(out) / "tableA1_all_conditions.md",
              "T-A1. All formal condition runs",
              header, rows,
              "One row per formal condition run. C1/C2 high-concurrency cells are not planned, not missing. Raw/persisted missing apply to controlled file operations only.")


def generate(report, out, *, probes=None, diagnostics=None, formats=("pdf", "svg", "png")):
    report = Path(report)
    out = Path(out)
    summary, raw = load_report(report)
    figures = out / "figures"
    tables = out / "tables"
    figures.mkdir(parents=True, exist_ok=True)
    tables.mkdir(parents=True, exist_ok=True)
    fig_component_walltime(summary, raw, figures)
    fig_collector_cpu(summary, raw, figures)
    fig_peak_rss(summary, raw, figures)
    fig_storage_accounting(summary, raw, figures)
    fig_walltime_scaling(summary, raw, figures)
    fig_rss_scaling(summary, raw, figures)
    fig_cpu_scaling(summary, raw, figures)
    fig_storage_scaling(summary, raw, figures)
    fig_capture_quality(summary, raw, figures)
    fig_alert_latency(summary, raw, figures)
    fig_observer_cost(summary, raw, figures)
    fig_graph_cost(summary, raw, figures)
    if probes:
        fig_probe_cost(summary, raw, figures, probes=probes, diagnostics=diagnostics or [])
    fig_task_delta(summary, raw, figures)
    table_component(summary, tables)
    table_scaling(summary, tables)
    table_capture(summary, raw, tables)
    table_observer_graph(summary, tables)
    if probes:
        table_probes(probes, diagnostics or [], tables)
    table_task_delta(raw, tables)
    table_ledger(report, tables)
    table_all_conditions(raw, tables)
    return {"figures": sorted(path.name for path in figures.iterdir()),
            "tables": sorted(path.name for path in tables.iterdir())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True,
                        help="directory with summary.json and raw-per-run.json")
    parser.add_argument("--out", type=Path, required=True,
                        help="output root; writes figures/ and tables/")
    parser.add_argument("--probe", type=Path, default=None,
                        help="TLS probe measurement JSON")
    parser.add_argument("--diagnostic", type=Path, action="append", default=[],
                        help="C1 diagnostic JSON; repeatable")
    args = parser.parse_args(argv)
    probes = None
    if args.probe:
        probes = (_load(args.probe), [_load(path) for path in args.diagnostic])
    try:
        result = generate(args.report, args.out, probes=probes,
                          diagnostics=[_load(path) for path in args.diagnostic])
    except AssetError as error:
        print(f"error: {error}")
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
