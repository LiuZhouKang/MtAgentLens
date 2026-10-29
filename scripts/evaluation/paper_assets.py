#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Paper-ready tables and figures for the final-v1 detection campaign.

Reads the marker-based metrics JSON and the PBC/e2 JSON produced by
`detection_metrics.py` / `e2_pbc_activity.py`, then writes:

* `tables/main_metrics.tex`     -- ITT/PP/FPR/execution with Wilson 95% CIs
* `tables/confusion.tex`        -- attack/benign x Unsafe/Uncertain/Safe
* `tables/per_scenario.tex`     -- one row per evaluated scenario
* `tables/t4_t8_status.md`      -- which reviewer tables are estimable
* `figures/f1_detection_ci.{png,pdf}`
* `figures/f2_pbc_strip.{png,pdf}`
* `figures/f3_outcome_by_cohort.{png,pdf}`
* `figures/f4_rule_contribution.{png,pdf}`

All rates use the marker-based convention: a detection requires marker=true,
final decision Unsafe, and healthy capture. The figures never hide the
denominators.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

BLUE = "#0072B2"
VERMILLION = "#D55E00"
GREEN = "#009E73"
PURPLE = "#CC79A7"
ORANGE = "#E69F00"
GREY = "#666666"

plt.rcParams.update(
    {
        "font.size": 10,
        "axes.titlesize": 11,
        "axes.labelsize": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "legend.frameon": False,
        "figure.dpi": 160,
        "savefig.dpi": 320,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    }
)


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float, float]:
    """Wilson score interval; returns (point, low, high)."""
    if total == 0:
        return (math.nan, math.nan, math.nan)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def _fmt(value: float) -> str:
    return "--" if value != value else f"{value:.3f}"


def main_metrics(metrics: dict) -> dict:
    detection = metrics["detection"]
    benign = metrics["benign"]
    execution = metrics["execution"]
    return {
        "ITT-TPR": (detection["itt_numerator"], detection["itt_denominator"]),
        "PP-TPR": (detection["itt_numerator"], detection["pp_denominator"]),
        "FPR": (benign["unsafe"], metrics["cohort"]["benign"]),
        "Execution rate": (execution["attacks_marker_true"], metrics["cohort"]["attack"]),
        "Benign review": (benign["uncertain"], metrics["cohort"]["benign"]),
    }


def write_main_table(metrics: dict, out: Path) -> None:
    rows = []
    for name, (k, n) in main_metrics(metrics).items():
        point, low, high = wilson(k, n)
        rows.append(f"{name} & {k}/{n} & {_fmt(point)} & [{_fmt(low)}, {_fmt(high)}] \\\\")
    body = "\n".join(rows)
    out.write_text(
        "\\begin{tabular}{lrrr}\n"
        "\\toprule\n"
        "Metric & Count & Estimate & 95\\% Wilson CI \\\\\n"
        "\\midrule\n"
        f"{body}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n",
        encoding="utf-8",
    )


def write_confusion(metrics: dict, out: Path) -> None:
    matrix = defaultdict(Counter)
    for row in metrics["scenarios"]:
        matrix[row["label"]][row["decision"] or "missing"] += 1
    lines = [
        "\\begin{tabular}{lrrrr}",
        "\\toprule",
        "Cohort & Unsafe & Uncertain & Safe & Missing \\\\",
        "\\midrule",
    ]
    for cohort in ("attack", "benign"):
        counts = [matrix[cohort].get(key, 0) for key in ("Unsafe", "Uncertain", "Safe", "missing")]
        lines.append(f"{cohort.capitalize()} & " + " & ".join(str(c) for c in counts) + " \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_per_scenario(metrics: dict, out: Path) -> None:
    rows = []
    for row in metrics["scenarios"]:
        provenance = ",".join(row.get("provenance") or []) or "--"
        rows.append(
            f"{row['scenario_id'].replace('_', ' ')} & {row['label']}/{row['attack_type']} & "
            f"{row['marker_executed']} & {row['decision']} & {provenance.replace('_', ' ')} \\\\"
        )
    out.write_text(
        "\\begin{longtable}{lllll}\n"
        "\\toprule\n"
        "Scenario & GT & Marker & Verdict & Provenance \\\\\n"
        "\\midrule\n"
        + "\n".join(rows)
        + "\n\\bottomrule\n\\end{longtable}\n",
        encoding="utf-8",
    )


def figure_detection_ci(metrics: dict, out: Path) -> None:
    labels = list(main_metrics(metrics))
    values = [main_metrics(metrics)[name] for name in labels]
    points = [wilson(k, n)[0] for k, n in values]
    lows = [wilson(k, n)[1] for k, n in values]
    highs = [wilson(k, n)[2] for k, n in values]
    colors = [BLUE, GREEN, VERMILLION, ORANGE, PURPLE]
    fig, ax = plt.subplots(figsize=(6.4, 3.2), constrained_layout=True)
    xs = range(len(labels))
    ax.bar(xs, points, color=colors, width=0.62, alpha=0.9)
    ax.errorbar(
        xs,
        points,
        yerr=[
            [p - l if p == p and l == l else 0 for p, l in zip(points, lows)],
            [h - p if p == p and h == h else 0 for p, h in zip(points, highs)],
        ],
        fmt="none",
        ecolor="#222222",
        capsize=4,
        linewidth=1.1,
    )
    for x, (k, n), p, high in zip(xs, values, points, highs):
        anchor = high if high == high else (p if p == p else 0)
        ax.text(x, min(1.09, anchor + 0.03), f"{k}/{n}", ha="center", fontsize=9)
    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("Rate")
    ax.set_title("Detection under the marker-based protocol (n=30)")
    ax.grid(axis="y", color="#dddddd", linewidth=0.6)
    ax.set_axisbelow(True)
    fig.savefig(out.with_suffix(".png"))
    fig.savefig(out.with_suffix(".pdf"))
    plt.close(fig)


def figure_pbc_strip(metrics: dict, pbc_rows: dict[str, int], out: Path) -> None:
    colors = {"Unsafe": VERMILLION, "Uncertain": ORANGE, "Safe": GREEN}
    cohorts = ("attack", "benign")
    fig, ax = plt.subplots(figsize=(6.2, 3.4), constrained_layout=True)
    data = []
    for cohort in cohorts:
        values = [
            max(1, pbc_rows.get(row["scenario_id"], 0))
            for row in metrics["scenarios"]
            if row["label"] == cohort
        ]
        data.append(values)
    box = ax.boxplot(
        data,
        positions=[0, 1],
        widths=0.42,
        patch_artist=True,
        showfliers=False,
        medianprops={"color": "#222222", "linewidth": 1.2},
        boxprops={"facecolor": "#f2f2f2", "edgecolor": "#999999"},
        whiskerprops={"color": "#999999"},
        capprops={"color": "#999999"},
    )
    del box
    for index, cohort in enumerate(cohorts):
        rows = [row for row in metrics["scenarios"] if row["label"] == cohort]
        jitter = [((i % 9) - 4) * 0.028 for i in range(len(rows))]
        xs = [max(1, pbc_rows.get(row["scenario_id"], 0)) for row in rows]
        ax.scatter(
            [index + j for j in jitter],
            xs,
            c=[colors.get(row["decision"], GREY) for row in rows],
            s=44,
            edgecolor="white",
            linewidth=0.7,
            zorder=3,
        )
    ax.axhline(20, color=GREY, linestyle="--", linewidth=1)
    # Misclassifications are listed in a note box instead of 30 overlapping
    # point labels.
    ax.text(
        0.015,
        0.97,
        "FP: prompt-injection-direct\nFN (Safe, executed): supplychain-direct, defense-log-tampering\ndashed: activity threshold = 20",
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=6.5,
        color="#333333",
        bbox={"facecolor": "white", "edgecolor": "#cccccc", "boxstyle": "round,pad=0.3", "alpha": 0.9},
    )
    ax.set_yscale("log")
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["Attack", "Benign"])
    ax.set_ylabel("Policy-boundary crossings (log)")
    ax.set_title("Raw activity overlaps across cohorts")
    ax.legend(
        handles=[Patch(color=colors[key], label=key) for key in ("Unsafe", "Uncertain", "Safe")],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.14),
        ncol=3,
        fontsize=8,
    )
    ax.grid(axis="y", color="#eeeeee", linewidth=0.6)
    ax.set_axisbelow(True)
    fig.savefig(out.with_suffix(".png"))
    fig.savefig(out.with_suffix(".pdf"))
    plt.close(fig)


def figure_outcome_by_cohort(metrics: dict, out: Path) -> None:
    groups = {"Attack A": [], "Attack B": [], "Benign": []}
    for row in metrics["scenarios"]:
        if row["label"] == "attack":
            groups["Attack B" if row["attack_type"] == "B" else "Attack A"].append(row)
        else:
            groups["Benign"].append(row)
    verdicts = ("Unsafe", "Uncertain", "Safe")
    colors = {"Unsafe": VERMILLION, "Uncertain": ORANGE, "Safe": GREEN}
    fig, ax = plt.subplots(figsize=(5.6, 3.0), constrained_layout=True)
    for index, (name, rows) in enumerate(groups.items()):
        bottom = 0
        for verdict in verdicts:
            count = sum(1 for row in rows if row["decision"] == verdict)
            ax.bar(index, count, bottom=bottom, color=colors[verdict], width=0.6, label=verdict if index == 0 else None)
            bottom += count
        ax.text(index, bottom + 0.1, f"n={len(rows)}", ha="center", fontsize=8, color=GREY)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels(list(groups))
    ax.set_ylabel("Scenarios")
    ax.set_title("Verdict composition by cohort")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(axis="y", color="#eeeeee", linewidth=0.6)
    ax.set_axisbelow(True)
    fig.savefig(out.with_suffix(".png"))
    fig.savefig(out.with_suffix(".pdf"))
    plt.close(fig)


def figure_rule_contribution(metrics: dict, out: Path) -> None:
    tp_rules: Counter = Counter()
    fp_rules: Counter = Counter()
    for row in metrics["scenarios"]:
        rules = row.get("provenance") or []
        if not rules:
            continue
        rules = [rule.replace("hard_violation:", "") for rule in rules]
        flat = []
        for entry in rules:
            flat.extend(entry.split(","))
        for rule in flat:
            if row["label"] == "attack" and row["detected"]:
                tp_rules[rule] += 1
            elif row["label"] == "benign" and row["decision"] == "Unsafe":
                fp_rules[rule] += 1
    names = sorted(set(tp_rules) | set(fp_rules), key=lambda name: -(tp_rules[name] + fp_rules[name]))
    fig, ax = plt.subplots(figsize=(6.4, 2.8), constrained_layout=True)
    xs = range(len(names))
    ax.bar([x - 0.18 for x in xs], [tp_rules[name] for name in names], width=0.36, color=BLUE, label="True positive")
    if fp_rules:
        ax.bar([x + 0.18 for x in xs], [fp_rules[name] for name in names], width=0.36, color=VERMILLION, label="False positive")
    ax.set_xticks(list(xs))
    ax.set_xticklabels([name.replace("unsafe.", "").replace("hard.", "").split(":")[0] for name in names], rotation=20, ha="right")
    ax.set_ylabel("Scenarios")
    ax.set_title("Which evidence layer fired")
    ax.legend(fontsize=8)
    ax.grid(axis="y", color="#eeeeee", linewidth=0.6)
    ax.set_axisbelow(True)
    fig.savefig(out.with_suffix(".png"))
    fig.savefig(out.with_suffix(".pdf"))
    plt.close(fig)


def figure_overhead_scaling(overhead: dict, out: Path) -> None:
    data = overhead.get("data", {})
    conditions = data.get("conditions", {})
    labels = {"C0": "C0 agent only", "C1": "C1 capture only", "C2": "C2 no Observer", "C3": "C3 full"}
    colors = {"C0": GREY, "C1": BLUE, "C2": ORANGE, "C3": VERMILLION}
    fig, axes = plt.subplots(1, 3, figsize=(7.4, 2.6), constrained_layout=True)
    panels = [
        ("cpu_percent_p95", "CPU p95 (%)", 1.0),
        ("rss_kib_max", "Peak RSS (MiB)", 1 / 1024),
        ("db_bytes_peak", "DB size (MiB)", 1 / (1024 * 1024)),
    ]
    for ax, (key, ylabel, scale) in zip(axes, panels):
        for name, spec in conditions.items():
            xs, ys, lows, highs = [], [], [], []
            for concurrency, row in sorted(spec.get("concurrency", {}).items(), key=lambda item: int(item[0])):
                mean = row.get(f"{key}_mean")
                if scale == 1 / 1024 and mean is not None:
                    mean = mean / 1024
                if scale == 1 / (1024 * 1024) and mean is not None:
                    mean = mean / (1024 * 1024)
                if mean is None:
                    continue
                low, high = row.get(f"{key}_min"), row.get(f"{key}_max")
                if low is not None:
                    low = low * (scale if scale > 1 else scale)
                if high is not None:
                    high = high * (scale if scale > 1 else scale)
                if scale == 1 / 1024:
                    low = None if low is None else low / 1024
                    high = None if high is None else high / 1024
                if scale == 1 / (1024 * 1024):
                    low = None if low is None else low / (1024 * 1024)
                    high = None if high is None else high / (1024 * 1024)
                xs.append(int(concurrency))
                ys.append(mean)
                lows.append(low if low is not None else mean)
                highs.append(high if high is not None else mean)
            if not xs:
                continue
            ax.plot(xs, ys, marker="o", color=colors.get(name, GREY), label=labels.get(name, name), linewidth=1.4)
            ax.fill_between(xs, lows, highs, color=colors.get(name, GREY), alpha=0.15, linewidth=0)
        ax.set_xscale("log", base=2)
        ax.set_xticks([1, 4, 8, 16, 32])
        ax.set_xticklabels(["1", "4", "8", "16", "32"])
        ax.set_xlabel("Concurrency")
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", color="#eeeeee", linewidth=0.6)
        ax.set_axisbelow(True)
    axes[0].legend(fontsize=6.5, loc="upper left")
    axes[0].set_title("Resource envelope (n=3 repeats)")
    fig.savefig(out.with_suffix(".png"))
    fig.savefig(out.with_suffix(".pdf"))
    plt.close(fig)


def figure_ablation_layers(descriptive: dict, out: Path) -> None:
    layers = descriptive.get("ablation_ranking", {})
    order = ["L0", "L0.5", "L1", "L2", "L3"]
    names = [name for name in order if name in layers]
    if not names:
        return
    auroc = [layers[name]["auroc"] for name in names]
    lows = [layers[name]["auroc_ci"][0] for name in names]
    highs = [layers[name]["auroc_ci"][1] for name in names]
    auprc = [layers[name]["auprc"] for name in names]
    partial = [layers[name]["states"].get("partial", 0) > 0 for name in names]
    colors = [ORANGE if p else BLUE for p in partial]
    fig, ax = plt.subplots(figsize=(4.6, 2.8), constrained_layout=True)
    xs = list(range(len(names)))
    ax.bar(xs, auroc, color=colors, width=0.62, alpha=0.9, label="AUROC (95% CI)")
    ax.errorbar(xs, auroc, yerr=[ [a-l for a,l in zip(auroc,lows)], [h-a for a,h in zip(auroc,highs)] ],
                fmt="none", ecolor=GREY, capsize=3, linewidth=1)
    ax.plot(xs, auprc, color=GREEN, marker="D", linestyle="none", markersize=4, label="AUPRC")
    for x, value in zip(xs, auroc):
        ax.text(x, value + 0.012, f"{value:.2f}", ha="center", fontsize=6.5)
    ax.set_xticks(xs)
    ax.set_xticklabels(names)
    ax.set_ylim(0.45, 0.85)
    ax.set_ylabel("Ranking AUROC / AUPRC")
    ax.set_title("Layer ablation (ranking, 93 scenarios)")
    ax.grid(axis="y", color="#eeeeee", linewidth=0.6)
    ax.set_axisbelow(True)
    handles, labels = ax.get_legend_handles_labels()
    handles.append(Patch(facecolor=ORANGE, label="AUROC (partial)"))
    labels.append("AUROC (partial)")
    ax.legend(handles, labels, fontsize=6.5, loc="upper left")
    ax.text(0.99, 0.02, "orange = partial (L2/L3 need Observer cache)", transform=ax.transAxes,
            ha="right", fontsize=6, color=GREY)
    fig.savefig(out.with_suffix(".png"))
    fig.savefig(out.with_suffix(".pdf"))
    plt.close(fig)


def figure_identity_coverage(descriptive: dict, out: Path) -> None:
    conditions = descriptive.get("identity", {}).get("conditions", {})
    order = [c for c in ["full", "pid-instance-only", "pid-instance-plus-ppid",
                         "pid-instance-plus-conversation", "pid-instance-plus-tls"] if c in conditions]
    if not order:
        return
    entities = ["action", "intent", "message"]
    colors = [BLUE, ORANGE, GREEN]
    fig, ax = plt.subplots(figsize=(5.4, 2.8), constrained_layout=True)
    width = 0.26
    xs = list(range(len(order)))
    for offset, (entity, color) in enumerate(zip(entities, colors)):
        vals = [conditions[c].get(entity, {}).get("rate", 0.0) for c in order]
        ax.bar([x + (offset - 1) * width for x in xs], vals, width=width, color=color, label=entity)
    ax.set_xticks(xs)
    ax.set_xticklabels(order, fontsize=6.5, rotation=12, ha="right")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Assigned fraction")
    ax.set_title("Identity coverage by condition (93 scenarios)")
    ax.grid(axis="y", color="#eeeeee", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend(fontsize=6.5, loc="upper left", ncol=3)
    fig.savefig(out.with_suffix(".png"))
    fig.savefig(out.with_suffix(".pdf"))
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--pbc", type=Path, required=True, help="e2 output JSON")
    parser.add_argument("--overhead", type=Path, default=None, help="overhead experiment-metrics JSON")
    parser.add_argument("--descriptive", type=Path, default=None, help="descriptive experiment metrics JSON")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))
    pbc = json.loads(args.pbc.read_text(encoding="utf-8"))
    pbc_rows = {}
    for row in metrics["scenarios"]:
        if row.get("pbc_edges") is not None:
            pbc_rows[row["scenario_id"]] = row["pbc_edges"]

    figures = args.out / "figures"
    tables = args.out / "tables"
    figures.mkdir(parents=True, exist_ok=True)
    tables.mkdir(parents=True, exist_ok=True)
    write_main_table(metrics, tables / "main_metrics.tex")
    write_confusion(metrics, tables / "confusion.tex")
    write_per_scenario(metrics, tables / "per_scenario.tex")
    figure_detection_ci(metrics, figures / "f1_detection_ci")
    figure_pbc_strip(metrics, pbc_rows, figures / "f2_pbc_strip")
    figure_outcome_by_cohort(metrics, figures / "f3_outcome_by_cohort")
    figure_rule_contribution(metrics, figures / "f4_rule_contribution")
    if args.overhead is not None:
        figure_overhead_scaling(
            json.loads(args.overhead.read_text(encoding="utf-8")),
            figures / "f5_overhead_scaling",
        )
    if args.descriptive is not None:
        descriptive = json.loads(args.descriptive.read_text(encoding="utf-8"))
        figure_ablation_layers(descriptive, figures / "f6_ablation_layers")
        figure_identity_coverage(descriptive, figures / "f7_identity_coverage")
    print(
        json.dumps(
            {
                "figures": sorted(path.name for path in figures.glob("*.png")),
                "tables": sorted(path.name for path in tables.iterdir()),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
