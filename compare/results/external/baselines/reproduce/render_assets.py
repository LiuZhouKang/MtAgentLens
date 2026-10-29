# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Render the T4 external-baseline table and figure (publication style)."""
import argparse, json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

BLUE = "#0072B2"; ORANGE = "#E69F00"; GREEN = "#009E73"; PINK = "#CC79A7"; GREY = "#7F7F7F"
STYLE = {
    "MtAgentLens": ("MtAgentLens", BLUE, "*", 11),
    "Agent Sentinel-style": ("Agent Sentinel-style", GREY, "o", 5.5),
    "LLM Guard": ("LLM Guard", ORANGE, "s", 5.5),
    "Meta Prompt Guard": ("Meta Prompt Guard", PINK, "^", 5.5),
    "R-Judge A2": ("R-Judge", GREEN, "D", 5.5),
}
ORDER = ["MtAgentLens", "Agent Sentinel-style", "LLM Guard", "Meta Prompt Guard", "R-Judge A2"]
INPUT = {
    "MtAgentLens": "OS + app",
    "Agent Sentinel-style": "task",
    "LLM Guard": "task + fixture",
    "Meta Prompt Guard": "task + fixture",
    "R-Judge A2": "app trace",
}


def f1(tp, na, fp):
    return 2 * tp / (2 * tp + fp + (na - tp)) if tp else 0.0


def table(data):
    rows = []
    for key in ORDER:
        v = data["tools"][key]; label = STYLE[key][0]
        prec = v["tp"] / (v["tp"] + v["fp"]) if v["tp"] + v["fp"] else 0.0
        rows.append(
            f"{label} ({INPUT[key]}) & {v['tpr']:.3f} [{v['tpr_ci'][0]:.3f}, {v['tpr_ci'][1]:.3f}] & "
            f"{v['fpr']:.3f} [{v['fpr_ci'][0]:.3f}, {v['fpr_ci'][1]:.3f}] & {prec:.3f} & "
            f"{f1(v['tp'], v['n_attack'], v['fp']):.3f} & {v['per_type']['A']['tpr']:.3f} & {v['per_type']['B']['tpr']:.3f} \\\\")
    return (r"""\begin{tabular}{lrrrrrr}
\toprule
Method (input) & TPR [95\% CI] & FPR [95\% CI] & Precision & F1 & TPR$_A$ & TPR$_B$ \\
\midrule
""" + "\n".join(rows) + r"""
\bottomrule
\end{tabular}
""")


def markdown(data):
    lines = ["| Method (input) | TPR | FPR | Precision | F1 | TPR_A | TPR_B |", "|---|---|---|---|---|---|---|"]
    for key in ORDER:
        v = data["tools"][key]; label = STYLE[key][0]
        prec = v["tp"] / (v["tp"] + v["fp"]) if v["tp"] + v["fp"] else 0.0
        lines.append(f"| {label} ({INPUT[key]}) | {v['tpr']:.3f} | {v['fpr']:.3f} | {prec:.3f} | {f1(v['tp'], v['n_attack'], v['fp']):.3f} | {v['per_type']['A']['tpr']:.3f} | {v['per_type']['B']['tpr']:.3f} |")
    return "\n".join(lines) + "\n"


def figure(data, out):
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "DejaVu Sans"],
        "font.size": 8, "axes.labelsize": 8, "axes.titlesize": 8,
        "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
        "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
        "xtick.major.size": 3, "ytick.major.size": 3,
        "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
    })
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.75), constrained_layout=True,
                             gridspec_kw={"width_ratios": [1.55, 1.0]})
    ax = axes[0]
    for key in ORDER:
        label, color, marker, ms = STYLE[key]
        v = data["tools"][key]; x, y = v["fpr"], v["tpr"]
        ax.errorbar(x, y, xerr=[[x - v["fpr_ci"][0]], [v["fpr_ci"][1] - x]],
                    yerr=[[y - v["tpr_ci"][0]], [v["tpr_ci"][1] - y]], fmt=marker,
                    color=color, ms=ms, mec="white" if key == "MtAgentLens" else color,
                    mew=0.8, capsize=2, elinewidth=0.7, zorder=3 if key == "MtAgentLens" else 2)
    short = {"MtAgentLens": "MtAgentLens", "Agent Sentinel-style": "AS-style",
             "LLM Guard": "LLM Guard", "Meta Prompt Guard": "Meta PG", "R-Judge A2": "R-Judge"}
    offsets = {"MtAgentLens": (8, 7), "Agent Sentinel-style": (-8, 8), "LLM Guard": (8, 8),
               "Meta Prompt Guard": (-8, 8), "R-Judge A2": (10, 7)}
    for key in ORDER:
        _, color, _, _ = STYLE[key]
        v = data["tools"][key]
        ha = "right" if key in ("Agent Sentinel-style", "Meta Prompt Guard") else "left"
        ax.annotate(short[key], (v["fpr"], v["tpr"]), textcoords="offset points",
                    xytext=offsets[key], fontsize=6.8, color=color, ha=ha)
    ax.set_xlim(0, 0.64); ax.set_ylim(0, 1.0)
    ax.set_xlabel("False positive rate"); ax.set_ylabel("TPR (executed attacks)")
    ax.set_title("Detection trade-off", loc="left", fontweight="bold")
    ax.grid(color="#EEEEEE", linewidth=0.5); ax.set_axisbelow(True)
    ax.text(0.03, 0.94, "better", transform=ax.transAxes, fontsize=6.5, color=GREY, style="italic")
    ax.text(0.985, 0.03, "29 executed attacks / 35 benign", transform=ax.transAxes,
            fontsize=6.2, color=GREY, ha="right")
    ax.plot([0.035, 0.095], [0.925, 0.965], color=GREY, lw=0.5, transform=ax.transAxes)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.text(-0.17, 1.05, "a", transform=ax.transAxes, fontsize=9, fontweight="bold", va="top")

    ax = axes[1]
    vals = [(key, f1(data["tools"][key]["tp"], data["tools"][key]["n_attack"], data["tools"][key]["fp"])) for key in ORDER]
    vals.sort(key=lambda x: x[1])
    for i, (key, val) in enumerate(vals):
        ax.barh(i, val, color=STYLE[key][1], height=0.62, edgecolor="white", linewidth=0.4)
        ax.text(val + 0.012, i, f"{val:.3f}", va="center", fontsize=6.5)
    ax.set_yticks(range(len(vals)))
    ax.set_yticklabels([STYLE[k][0] for k, _ in vals], fontsize=6.8)
    ax.set_xlim(0, 1.0); ax.set_xlabel("F1 (execution-conditioned)")
    ax.set_title("F1 score", loc="left", fontweight="bold")
    ax.grid(axis="x", color="#EEEEEE", linewidth=0.5); ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.text(-0.30, 1.05, "b", transform=ax.transAxes, fontsize=9, fontweight="bold", va="top")
    for ext in ("png", "pdf", "svg"):
        fig.savefig(out / f"fig4_baseline_comparison.{ext}", dpi=600, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    data = json.loads(a.metrics.read_text())
    (a.out / "tables").mkdir(parents=True, exist_ok=True)
    (a.out / "figures").mkdir(parents=True, exist_ok=True)
    (a.out / "tables" / "table4_external_baselines.tex").write_text(table(data))
    (a.out / "tables" / "table4_external_baselines.md").write_text(markdown(data))
    figure(data, a.out / "figures")


if __name__ == "__main__":
    main()
