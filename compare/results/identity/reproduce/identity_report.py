#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Render the IdentityBench paper table, figure and summary from frozen metrics."""
import argparse
import csv
import json
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

PKG = Path(__file__).resolve().parent.parent
BLUE = "#0072B2"
ORANGE = "#E69F00"
GREEN = "#009E73"
GREY = "#666666"
PURPLE = "#CC79A7"
CONDS = [
    ("C0", "PID instance"),
    ("C1", "+PPID"),
    ("C2", "+TLS"),
    ("C3", "+conversation"),
    ("C4", "Full"),
]
CSV_FIELDS = [
    "condition", "name", "assigned_known", "reference_known", "coverage",
    "all_known_pair_f1", "ci_low", "ci_high", "assigned_only_f1",
    "false_split_rate", "b3", "precision", "recall", "tp", "fp", "fn", "tn",
]


def _load(metrics_path, audit_path):
    data = json.loads(metrics_path.read_text())["data"]
    audit = json.loads(audit_path.read_text())
    by_condition = {row["condition"]: row for row in audit["conditions"]}
    known = int(audit["reference_known"])
    records = []
    for key, name in CONDS:
        metrics = data["conditions"][key]
        counts = by_condition[key]
        if int(counts["reference_known"]) != known:
            raise SystemExit(f"audit.json has inconsistent reference_known for {key}")
        lo, hi = data["statistics"]["confidence_intervals"][f"{key}_pairwise_f1"]
        for metric_key, audit_key in (("coverage", "coverage"), ("pairwise_f1", "f1"),
                                       ("pairwise_precision", "precision"), ("pairwise_recall", "recall"),
                                       ("pairwise_f1_evaluated", "conditional_f1")):
            if abs(metrics[metric_key] - counts[audit_key]) > 1e-12:
                raise SystemExit(f"stale or inconsistent audit: {key} {metric_key}")
        assert lo <= metrics["pairwise_f1"] <= hi
        records.append({
            "condition": key,
            "name": name,
            "assigned_known": int(counts["assigned_known"]),
            "reference_known": int(counts["reference_known"]),
            "coverage": metrics["coverage"],
            "all_known_pair_f1": metrics["pairwise_f1"],
            "ci_low": lo,
            "ci_high": hi,
            "assigned_only_f1": metrics["pairwise_f1_evaluated"],
            "false_split_rate": metrics["false_split_rate"],
            "b3": metrics["b3"],
            "precision": metrics["pairwise_precision"],
            "recall": metrics["pairwise_recall"],
            "tp": int(counts["tp"]),
            "fp": int(counts["fp"]),
            "fn": int(counts["fn"]),
            "tn": int(counts["tn"]),
        })
    return data, audit, records, known


def _notes(known, sampled, informative):
    return [
        ("a", f"Coverage is assigned / known over the {known:,} known-reference units "
              f"({sampled:,} sampled; {sampled - known:,} unknown references excluded); "
              f"{informative}/30 scenarios have a known reference."),
        ("b", "All-known pair F1 includes every known-reference unit; each unassigned unit "
              "forms a unique singleton. CI: scenario bootstrap, 5,000 resamples."),
        ("c", "Assigned-only F1 is conditional on assignment; abstentions are excluded."),
        ("d", "False split = FN / (TP + FN): the share of reference-same-agent pairs "
              "split by the predicted partition."),
        ("e", "Observed FP = 0 and pairwise P = 1.000 across all five conditions "
              "in the evaluated reference cohort."),
        ("f", "Scores quantify agreement between predicted and reference identity partitions."),
    ]


def _write_md(path, records, notes):
    lines = [
        "# Table 5. IdentityBench partition consistency by condition",
        "",
        "| Condition | Assigned / known (coverage)[a] | All-known pair F1 [95% CI][b] | Assigned-only F1[c] | False-split pairs[d] |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in records:
        lines.append(
            f"| {r['condition']} {r['name']} | "
            f"{r['assigned_known']:,} / {r['reference_known']:,} "
            f"({r['coverage'] * 100:.1f}%) | "
            f"{r['all_known_pair_f1']:.3f} [{r['ci_low']:.3f}, {r['ci_high']:.3f}] | "
            f"{r['assigned_only_f1']:.3f} | "
            f"{r['false_split_rate'] * 100:.1f}% |"
        )
    lines += ["", "**Notes.**", ""]
    lines += [f"**[{marker}]** {text}  " for marker, text in notes]
    lines += [
        "",
        "### Diagnostic supplement (known-reference cohort)",
        "",
        "| Condition | P | R | B³ | TP | FP | FN | TN |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in records:
        lines.append(
            f"| {r['condition']} {r['name']} | {r['precision']:.3f} | "
            f"{r['recall']:.3f} | {r['b3']:.3f} | {r['tp']:,} | {r['fp']:,} | "
            f"{r['fn']:,} | {r['tn']:,} |"
        )
    path.write_text("\n".join(lines) + "\n")


def _tex_escape(text):
    return text.replace("%", r"\%")


def _write_tex(path, records, notes):
    lines = [
        r"{\small\setlength{\tabcolsep}{4pt}",
        r"\begin{tabular}{@{}lrrrr@{}}",
        r"\toprule",
        r"Condition & \shortstack{Assigned / known\\(coverage)\textsuperscript{a}} & "
        r"\shortstack{All-known pair F1\\{[95\% CI]}\textsuperscript{b}} & "
        r"\shortstack{Assigned-only\\F1\textsuperscript{c}} & \shortstack{False-split\\pairs\textsuperscript{d}} \\",
        r"\midrule",
    ]
    for r in records:
        lines.append(
            f"{r['condition']} {r['name']} & "
            f"{r['assigned_known']:,} / {r['reference_known']:,} "
            f"({r['coverage'] * 100:.1f}\\%) & "
            f"{r['all_known_pair_f1']:.3f} "
            f"[{r['ci_low']:.3f}, {r['ci_high']:.3f}] & "
            f"{r['assigned_only_f1']:.3f} & "
            f"{r['false_split_rate'] * 100:.1f}\\% \\\\"
        )
    lines += [
        r"\bottomrule",
        r"\end{tabular}",
        r"\par\smallskip",
        r"{\footnotesize",
    ]
    lines += [
        f"\\textsuperscript{{{marker}}} {_tex_escape(text)}\\par"
        for marker, text in notes
    ]
    lines += ["}", "}"]
    path.write_text("\n".join(lines) + "\n")


def _write_csv(path, records):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        for r in records:
            writer.writerow({field: r[field] for field in CSV_FIELDS})


def _write_preview(path_base, records, notes):
    fig = plt.figure(figsize=(13, 2.7), facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    x0, x1 = 0.025, 0.975
    head_top, head_bot = 0.94, 0.76
    row_h = 0.135
    body_bot = head_bot - len(records) * row_h

    ax.add_patch(plt.Rectangle((x0, head_bot), x1 - x0, head_top - head_bot,
                               facecolor="#E9ECEF", edgecolor="none"))

    columns = [
        (0.035, "left", "Condition"),
        (0.300, "center", "Assigned / known\n(coverage)"),
        (0.525, "center", "All-known pair F1\n[95% CI]"),
        (0.750, "center", "Assigned-only F1"),
        (0.915, "center", "False-split pairs"),
    ]
    for x, align, label in columns:
        ax.text(x, (head_top + head_bot) / 2, label, ha=align, va="center",
                fontsize=10, fontweight="bold", linespacing=1.15)

    for i, r in enumerate(records):
        top = head_bot - i * row_h
        bottom = top - row_h
        y = (top + bottom) / 2
        if i % 2:
            ax.add_patch(plt.Rectangle((x0, bottom), x1 - x0, row_h,
                                       facecolor="#F4F6F8", edgecolor="none"))
        ax.text(0.035, y, r["condition"], ha="left", va="center",
                fontsize=10, fontweight="bold", color="#4A6FA5")
        ax.text(0.083, y, r["name"], ha="left", va="center", fontsize=10)
        ax.text(0.300, y,
                f"{r['assigned_known']:,} / {r['reference_known']:,} "
                f"({r['coverage'] * 100:.1f}%)",
                ha="center", va="center", fontsize=10)
        ax.text(0.525, y,
                f"{r['all_known_pair_f1']:.3f} "
                f"[{r['ci_low']:.3f}, {r['ci_high']:.3f}]",
                ha="center", va="center", fontsize=10)
        ax.text(0.750, y, f"{r['assigned_only_f1']:.3f}",
                ha="center", va="center", fontsize=10)
        ax.text(0.915, y, f"{r['false_split_rate'] * 100:.1f}%",
                ha="center", va="center", fontsize=10)

    ax.plot([x0, x1], [head_top, head_top], color="#333333", lw=1.1,
            solid_capstyle="butt")
    ax.plot([x0, x1], [head_bot, head_bot], color="#8A8A8A", lw=0.7,
            solid_capstyle="butt")
    ax.plot([x0, x1], [body_bot, body_bot], color="#333333", lw=1.0,
            solid_capstyle="butt")

    for ext in ("png", "pdf", "svg"):
        fig.savefig(path_base.with_suffix(f".{ext}"), dpi=300, facecolor="white")
    plt.close(fig)


def _write_figure(path, data, conditions):
    from matplotlib.colors import Normalize
    from matplotlib.lines import Line2D

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 9.0,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    })

    audit = json.loads((PKG / "report" / "audit.json").read_text())
    audit_by_condition = {row["condition"]: row for row in audit["conditions"]}
    unknown = audit.get("unknown_reason_counts", {})
    known = int(audit["reference_known"])

    assigned = {}
    for key, _ in CONDS:
        counts = audit_by_condition[key]
        reasons = unknown.get(key, {})
        assigned[key] = (
            int(counts["assigned_known"]),
            int(reasons.get("process_not_assigned", 0)),
            int(reasons.get("ambiguous_message", 0)),
        )
        if sum(assigned[key]) != known:
            raise SystemExit(f"identity figure totals do not match known units: {key}")

    coverage = {}
    with (PKG / "data" / "kind_coverage.csv").open(newline="", encoding="utf-8") as handle:
        for record in csv.DictReader(handle):
            coverage[(record["condition"], record["unit_kind"].lower())] = (
                int(record["assigned_known"]), int(record["reference_known"]))

    fig, axes = plt.subplots(
        1, 3, figsize=(13, 4.2),
        gridspec_kw={"width_ratios": [1.1, 1.0, 0.95]},
    )
    fig.patch.set_facecolor("white")
    fig.subplots_adjust(left=0.145, right=0.985, top=0.88, bottom=0.28,
                        wspace=0.30)

    assigned_color = "#0072B2"
    process_color = "#D8A133"
    ambiguous_color = "#9A79AF"
    navy = "#17324D"
    connector = "#C9CED6"
    ink = "#222222"
    spine_color = "#8A8A8A"
    ys = list(range(len(CONDS)))[::-1]
    ylim = (-0.5, 4.5)
    legend_y = -0.23

    # Panel A: identity assignment as a share of known-reference units.
    ax = axes[0]
    for i, (key, _) in enumerate(CONDS):
        y = ys[i]
        left = 0.0
        for value, color, text_color, is_assigned in (
                (assigned[key][0], assigned_color, "white", True),
                (assigned[key][1], process_color, "#1A1A1A", False),
                (assigned[key][2], ambiguous_color, "#1A1A1A", False)):
            if value <= 0:
                continue
            width = value / known * 100.0
            ax.barh(y, width, left=left, height=0.58, color=color,
                    edgecolor="white", linewidth=0.6)
            if is_assigned:
                ax.text(left + width / 2.0, y, f"{width:.1f}%",
                        ha="center", va="center", fontsize=9.0,
                        color=text_color)
            left += width
    ax.set_yticks(ys)
    ax.set_yticklabels([f"{key}  {name}" for key, name in CONDS], fontsize=9.0)
    ax.set_xlim(0, 100)
    ax.set_ylim(*ylim)
    ax.set_xticks([0, 50, 100])
    ax.tick_params(axis="x", labelsize=9.0, length=2.5, width=0.6,
                   color="#777777")
    ax.tick_params(axis="y", labelsize=9.0, length=0, pad=2.5)
    ax.set_xlabel("Known units (%)", fontsize=10.0, labelpad=3)
    ax.set_title("A  Identity assignment", loc="left", fontsize=11.0,
                 fontweight="bold", color=ink, pad=4)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(spine_color)
        ax.spines[side].set_linewidth(0.7)
    ax.set_axisbelow(True)

    handles = [
        Patch(facecolor=assigned_color, edgecolor="none", label="Assigned"),
        Patch(facecolor=process_color, edgecolor="none", label="Process unassigned"),
        Patch(facecolor=ambiguous_color, edgecolor="none", label="Message ambiguous"),
    ]
    ax.legend(handles=handles, loc="upper left",
              bbox_to_anchor=(0.0, legend_y), ncol=1, frameon=False,
              fontsize=9.0, handlelength=1.1, handleheight=0.85,
              borderaxespad=0.0, labelspacing=0.28, borderpad=0.0)

    # Panel B: agreement under two evaluation scopes.
    ax = axes[1]
    for i, (key, _) in enumerate(CONDS):
        y = ys[i]
        all_known = conditions[key]["pairwise_f1"]
        assigned_only = conditions[key]["pairwise_f1_evaluated"]
        lo, hi = data["statistics"]["confidence_intervals"][f"{key}_pairwise_f1"]
        ax.plot([min(all_known, assigned_only), max(all_known, assigned_only)],
                [y, y], color=connector, lw=0.9, zorder=1,
                solid_capstyle="round")
        ax.errorbar(all_known, y,
                    xerr=[[all_known - lo], [hi - all_known]],
                    fmt="none", ecolor=navy, elinewidth=0.8,
                    capsize=2.0, capthick=0.8, zorder=2)
        ax.plot(all_known, y, marker="D", markersize=5.0, color=navy,
                markeredgecolor=navy, markeredgewidth=0.6, zorder=3)
        ax.plot(assigned_only, y, marker="o", markersize=8.0,
                markerfacecolor="none", markeredgecolor=assigned_color,
                markeredgewidth=0.9, zorder=4)
    ax.set_xlim(0, 1.06)
    ax.set_ylim(*ylim)
    ax.set_yticks([])
    ax.set_xticks([0.0, 0.5, 1.0])
    ax.set_xticklabels(["0", "0.5", "1"])
    ax.tick_params(axis="x", labelsize=9.0, length=2.5, width=0.6,
                   color="#777777")
    ax.set_xlabel("Pairwise F1", fontsize=10.0, labelpad=3)
    ax.set_title("B  Partition agreement", loc="left", fontsize=11.0,
                 fontweight="bold", color=ink, pad=4)
    ax.grid(axis="x", color="#ECECEC", lw=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(spine_color)
    ax.spines["bottom"].set_linewidth(0.7)

    legend_b = [
        Line2D([0], [0], marker="D", linestyle="none", markersize=5.0,
               markerfacecolor=navy, markeredgecolor=navy,
               label="All-known (95% CI)"),
        Line2D([0], [0], marker="o", linestyle="none", markersize=8.0,
               markerfacecolor="none", markeredgecolor=assigned_color,
               markeredgewidth=0.9, label="Assigned-only"),
    ]
    ax.legend(handles=legend_b, loc="upper left",
              bbox_to_anchor=(0.0, legend_y), ncol=1, frameon=False,
              fontsize=9.0, handlelength=1.0, handletextpad=0.5,
              borderaxespad=0.0, labelspacing=0.28, borderpad=0.0)

    # Panel C: coverage by unit type.
    ax = axes[2]
    kinds = [("message", "Message"), ("intent", "Intent"), ("action", "Action")]
    matrix = []
    for key, _ in CONDS:
        row = []
        for kind, _ in kinds:
            assigned_units, reference_units = coverage[(key, kind)]
            row.append(assigned_units / reference_units * 100.0
                       if reference_units else 0.0)
        matrix.append(row)
    cmap = plt.get_cmap("Blues")
    ax.pcolormesh([0, 1, 2, 3], [0, 1, 2, 3, 4, 5], matrix,
                  cmap=cmap, vmin=0, vmax=100,
                  edgecolors="white", linewidth=0.8)
    ax.set_xlim(0, 3)
    ax.set_ylim(5, 0)
    ax.set_xticks([0.5, 1.5, 2.5])
    ax.set_xticklabels([label for _, label in kinds], fontsize=9.0)
    ax.set_yticks([])
    ax.tick_params(axis="x", length=0, pad=2)
    for spine in ax.spines.values():
        spine.set_visible(False)
    for i in range(len(CONDS)):
        for j in range(len(kinds)):
            percent = matrix[i][j]
            rgba = cmap(Normalize(0, 100)(percent))
            luminance = 0.2126 * rgba[0] + 0.7152 * rgba[1] + 0.0722 * rgba[2]
            text_color = "white" if luminance < 0.5 else "#1A1A1A"
            ax.text(j + 0.5, i + 0.5, f"{percent:.1f}%",
                    ha="center", va="center", fontsize=9.0,
                    color=text_color)
    ax.set_xlabel("Assigned (%)", fontsize=10.0, labelpad=3)
    ax.set_title("C  Coverage by unit type", loc="left", fontsize=11.0,
                 fontweight="bold", color=ink, pad=4)

    for ext in ("png", "pdf", "svg"):
        fig.savefig(path.with_suffix(f".{ext}"),
                    dpi=300 if ext == "png" else 72,
                    facecolor="white")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", type=Path,
                        default=PKG / "metrics" / "identity-metrics.json")
    parser.add_argument("--out", type=Path, default=PKG)
    parser.add_argument("--audit", type=Path,
                        default=PKG / "report" / "audit.json")
    args = parser.parse_args()

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    })

    data, audit, records, known = _load(args.metrics, args.audit)
    sampled = int(audit["sampled_units"])
    informative = int(audit["scenarios_with_known_reference"])
    notes = _notes(known, sampled, informative)

    args.out.mkdir(parents=True, exist_ok=True)
    for name in ("tables", "figures", "report"):
        (args.out / name).mkdir(exist_ok=True)

    _write_md(args.out / "tables" / "t5_identity.md", records, notes)
    _write_tex(args.out / "tables" / "t5_identity.tex", records, notes)
    _write_csv(args.out / "tables" / "t5_identity.csv", records)
    _write_preview(args.out / "tables" / "t5_identity", records, notes)
    _write_figure(args.out / "figures" / "identity_bench", data, data["conditions"])

    try:
        audit_ref = str(args.audit.resolve().relative_to(PKG))
    except ValueError:
        audit_ref = str(args.audit.resolve())
    by_condition = {r["condition"]: r for r in records}
    c2 = by_condition["C2"]
    c4 = by_condition["C4"]
    summary = {
        "schema": "mtagentlens.identity-bench-summary/1",
        "cohort": {
            "scenarios": int(audit["scenarios"]),
            "units": sampled,
            "known": known,
            "informative_scenarios": informative,
        },
        "reference": data["reference"],
        "conditions": {key: data["conditions"][key] for key, _ in CONDS},
        "statistics": data["statistics"],
        "audit_file": audit_ref,
        "reference_review_audit": {"ai_added_reference_units": 0, "consensus_matches_declared": 68, "two_matching_votes": audit["ai_vote_counts"]["max_matching_votes_2"], "three_matching_votes": audit["ai_vote_counts"]["max_matching_votes_3"]},
        "unknown_reason_counts": audit["unknown_reason_counts"],
        "limitations_audit": {"same_process_negative_reference_pairs": audit["same_process_negative_reference_pairs"], "C1_known_units_with_missing_process_metadata": audit["limitation_counts"]["C1"].get("process_metadata_missing", 0)},
        "note": "Scores quantify agreement between predicted and reference identity partitions.",
        "claims": [
            f"No observed false-positive pairs in the {known:,}-unit known-reference "
            "cohort across all five conditions (FP = 0, pairwise P = 1.000); these are "
            "cohort-bounded observations, not claims beyond this cohort.",
            f"C2 +TLS assigns {c2['assigned_known']:,} / {known:,} known-reference "
            f"units ({c2['coverage'] * 100:.1f}%).",
            f"C4 Full assigns {c4['assigned_known']:,} / {known:,} known-reference "
            f"units ({c4['coverage'] * 100:.1f}%).",
            "All-known pair F1 treats each abstention as a unique singleton; "
            "assigned-only F1 excludes abstentions.",
            f"Coverage denominator is {known:,} known-reference units of {sampled:,} "
            f"sampled ({informative}/30 scenarios with known reference); "
            f"{sampled - known:,} unknown references are excluded.",
        ],
    }
    (args.out / "report" / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote identity tables, previews, figures and summary under {args.out}")


if __name__ == "__main__":
    main()
