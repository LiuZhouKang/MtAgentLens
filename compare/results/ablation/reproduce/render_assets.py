# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Render T7 ranking table, operating-point table and Figure 7."""
import json, argparse
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

BLUE = "#0072B2"; ORANGE = "#E69F00"; GREEN = "#009E73"; GREY = "#666666"
LAYERS = ["L0", "L0.5", "L1", "L2", "L3"]


def fmt(x):
    return f"{x:.3f}"


def tables(d, out):
    ranking = d["data"]["ranking"]["layers"]
    contrasts = {c["to"]: c for c in d["data"]["contrasts"]}
    rows = []
    for name in LAYERS:
        r = ranking[name]; c = contrasts.get(name)
        delta = "" if c is None else fmt(c["delta_auc"])
        holm = "" if c is None else f"{c['holm_p']:.3f}"
        rows.append(f"{name} & {fmt(r['auc'])} [{fmt(r['auc_ci'][0])}, {fmt(r['auc_ci'][1])}] & "
                    f"{fmt(r['auprc'])} & {delta} & {holm} \\\\")
    tex = r"""\begin{tabular}{lrrrr}
\toprule
Layer & AUROC [95\% CI] & AUPRC & $\Delta$AUROC & Holm $p$ \\
\midrule
""" + "\n".join(rows) + r"""
\bottomrule
\end{tabular}
"""
    (out / "tables" / "table7_ranking.tex").write_text(tex)
    md = ["| Layer | AUROC [95% CI] | AUPRC | ΔAUROC | Holm p |", "|---|---|---|---|---|"]
    for name in LAYERS:
        r = ranking[name]; c = contrasts.get(name)
        delta = "" if c is None else fmt(c["delta_auc"])
        holm = "" if c is None else f"{c['holm_p']:.3f}"
        md.append(f"| {name} | {fmt(r['auc'])} [{fmt(r['auc_ci'][0])}, {fmt(r['auc_ci'][1])}] | {fmt(r['auprc'])} | {delta} | {holm} |")
    (out / "tables" / "table7_ranking.md").write_text("\n".join(md) + "\n")

    pp = d["data"]["operating_point"]["pp"]
    rows = []
    for label, key in (("L1 score @0.15", "L1_score_threshold"), ("L3 production", "L3_production")):
        r = pp[key]
        rows.append(f"{label} & {fmt(r['tpr'])} [{fmt(r['tpr_ci'][0])}, {fmt(r['tpr_ci'][1])}] & "
                    f"{fmt(r['fpr'])} [{fmt(r['fpr_ci'][0])}, {fmt(r['fpr_ci'][1])}] & "
                    f"{fmt(r['precision'])} & {fmt(r['f1'])} \\\\")
    tex = r"""\begin{tabular}{lrrrr}
\toprule
System & TPR [95\% CI] & FPR [95\% CI] & Precision & F1 \\
\midrule
""" + "\n".join(rows) + r"""
\bottomrule
\end{tabular}
"""
    (out / "tables" / "table7_operating.tex").write_text(tex)
    (out / "tables" / "table7_operating.md").write_text(
        "| System | TPR | FPR | Precision | F1 |\n|---|---|---|---|---|\n" +
        "\n".join(f"| {l} | {fmt(pp[k]['tpr'])} | {fmt(pp[k]['fpr'])} | {fmt(pp[k]['precision'])} | {fmt(pp[k]['f1'])} |"
                  for l, k in (("L1 score @0.15", "L1_score_threshold"), ("L3 production", "L3_production"))) + "\n")


def figure(d, out):
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": ["Arial", "DejaVu Sans"],
        "font.size": 8, "axes.labelsize": 8, "axes.titlesize": 8,
        "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
        "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
        "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
    })
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.85), constrained_layout=True,
                             gridspec_kw={"width_ratios": [1.25, 1.0]})
    ax = axes[0]
    rank = d["data"]["ranking"]["layers"]
    x = np.arange(len(LAYERS))
    auc = [rank[l]["auc"] for l in LAYERS]; ap = [rank[l]["auprc"] for l in LAYERS]
    lo = [rank[l]["auc"] - rank[l]["auc_ci"][0] for l in LAYERS]
    hi = [rank[l]["auc_ci"][1] - rank[l]["auc"] for l in LAYERS]
    ax.errorbar(x, auc, yerr=[lo, hi], fmt="o-", color=BLUE, ms=4.5, lw=1.0, capsize=2.5,
                elinewidth=0.8, label="AUROC")
    ax.plot(x, ap, "s--", color=GREEN, ms=4, lw=0.9, label="AUPRC")
    ax.set_xticks(x); ax.set_xticklabels(LAYERS)
    ax.set_ylim(0.35, 0.88); ax.set_ylabel("Score")
    ax.set_title("Ranking capability", loc="left", fontweight="bold")
    ax.grid(color="#EEEEEE", linewidth=0.5); ax.set_axisbelow(True)
    ax.legend(frameon=False, loc="lower right", ncol=1)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.text(-0.16, 1.05, "a", transform=ax.transAxes, fontsize=9, fontweight="bold", va="top")

    ax = axes[1]
    pp = d["data"]["operating_point"]["pp"]
    metrics = [("TPR", "tpr"), ("FPR", "fpr"), ("Precision", "precision"), ("F1", "f1")]
    l1 = pp["L1_score_threshold"]; l3 = pp["L3_production"]
    ys = np.arange(len(metrics))[::-1]
    for y, (label, key) in zip(ys, metrics):
        a, b = l1[key], l3[key]
        ax.plot([a, b], [y, y], color="#BBBBBB", lw=1.2, zorder=1)
        ax.scatter([a], [y], color=ORANGE, s=28, zorder=2, edgecolor="white", linewidth=0.4)
        ax.scatter([b], [y], color=BLUE, s=28, zorder=3, edgecolor="white", linewidth=0.4)
        ax.annotate(fmt(a), (a, y), xytext=(0, 5), textcoords="offset points",
                    ha="center", va="bottom", fontsize=6.2, color=ORANGE)
        ax.annotate(fmt(b), (b, y), xytext=(0, 5), textcoords="offset points",
                    ha="center", va="bottom", fontsize=6.2, color=BLUE)
    ax.set_ylim(-0.5, 3.65)
    ax.set_yticks(ys); ax.set_yticklabels([m[0] for m in metrics])
    ax.set_xlim(0, 1.05); ax.set_xlabel("Value (PP: 29 attacks, 35 benign)")
    ax.set_title("Operating point (PP)", loc="left", fontweight="bold")
    ax.grid(axis="x", color="#EEEEEE", linewidth=0.5); ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.scatter([], [], color=ORANGE, s=24, label="L1 score @0.15")
    ax.scatter([], [], color=BLUE, s=24, label="L3 production")
    ax.legend(frameon=False, loc="upper left", fontsize=6.5)
    ax.text(-0.30, 1.05, "b", transform=ax.transAxes, fontsize=9, fontweight="bold", va="top")
    for ext in ("png", "pdf", "svg"):
        fig.savefig(out / "figures" / f"fig7_ablation.{ext}", dpi=600, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    d = json.loads(a.metrics.read_text())
    (a.out / "tables").mkdir(parents=True, exist_ok=True)
    (a.out / "figures").mkdir(parents=True, exist_ok=True)
    tables(d, a.out); figure(d, a.out)


if __name__ == "__main__":
    main()
