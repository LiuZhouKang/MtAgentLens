# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Render the two-threshold sensitivity figure and tables (Chinese journal style)."""
import json, argparse
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib import cm

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Noto Sans CJK SC", "Noto Sans CJK JP", "DejaVu Sans"],
    "axes.unicode_minus": False,
    "font.size": 8, "axes.labelsize": 8, "axes.titlesize": 8,
    "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
    "axes.linewidth": 0.6, "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
})


def tables(d, out):
    (out / "tables").mkdir(exist_ok=True)
    rows = []
    for label, p in (("部署点", d["baseline"]), ("最优网格点", d["best"]), ("最差网格点", d["worst"])):
        rows.append(f"{label} & {p['safe']:.2f} & {p['unsafe']:.2f} & {p['tpr']:.3f} & "
                    f"{p['fpr']:.3f} & {p['precision']:.3f} & {p['f1']:.3f} & {p['flips']} \\\\")
    tex = r"""\begin{tabular}{lrrrrrrr}
\toprule
点 & 安全阈值 & 不安全阈值 & TPR & FPR & 精确率 & F1 & 翻转数 \\
\midrule
""" + "\n".join(rows) + r"""
\bottomrule
\end{tabular}
"""
    (out / "tables" / "table8_sensitivity.tex").write_text(tex)
    md = ["| 点 | 安全阈值 | 不安全阈值 | TPR | FPR | 精确率 | F1 | 翻转数 |", "|---|---|---|---|---|---|---|---|"]
    for label, p in (("部署点", d["baseline"]), ("最优网格点", d["best"]), ("最差网格点", d["worst"])):
        md.append(f"| {label} | {p['safe']:.2f} | {p['unsafe']:.2f} | {p['tpr']:.3f} | {p['fpr']:.3f} | {p['precision']:.3f} | {p['f1']:.3f} | {p['flips']} |")
    (out / "tables" / "table8_sensitivity.md").write_text("\n".join(md) + "\n")

    rows = []
    for o in d["oat_summary"]:
        rows.append(f"{o['parameter']} & {o['f1_min']:.3f}--{o['f1_max']:.3f} & {o['flips_min']}--{o['flips_max']} \\\\")
    tex = r"""\begin{tabular}{lrr}
\toprule
参数 & F1 范围 & 翻转数范围 \\
\midrule
""" + "\n".join(rows) + r"""
\bottomrule
\end{tabular}
"""
    (out / "tables" / "table8_oat.tex").write_text(tex)
    md = ["| 参数 | F1 范围 | 翻转数范围 |", "|---|---|---|"]
    for o in d["oat_summary"]:
        md.append(f"| {o['parameter']} | {o['f1_min']:.3f}--{o['f1_max']:.3f} | {o['flips_min']}--{o['flips_max']} |")
    (out / "tables" / "table8_oat.md").write_text("\n".join(md) + "\n")

    l = d["lhs"]
    tex = (r"""\begin{tabular}{lr}
\toprule
联合采样（$n=1000$） & 值 \\
\midrule
""" + f"F1 中位数 & {l['f1_median']:.3f} \\\\\n"
       + f"F1 95\\% 区间 & [{l['f1_p2_5']:.3f}, {l['f1_p97_5']:.3f}] \\\\\n"
       + f"F1 $\\geq$ 0.80 比例 & {l['frac_f1_ge_080']:.1%} \\\\\n"
       + r"""\bottomrule
\end{tabular}
""")
    (out / "tables" / "table8_lhs.tex").write_text(tex)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    d = json.loads(a.metrics.read_text())["data"]
    tables(d, a.out)


if __name__ == "__main__":
    main()
