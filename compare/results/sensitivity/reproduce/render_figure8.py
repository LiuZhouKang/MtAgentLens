# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Final Figure 8: 3D terrain + OAT ledger + LHS ECDF."""
import json, argparse
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize, ListedColormap
from matplotlib.lines import Line2D
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import gaussian_filter

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Noto Sans CJK SC", "Noto Sans CJK JP", "DejaVu Sans"],
    "axes.unicode_minus": False,
    "font.size": 7.6, "axes.labelsize": 7.6, "axes.titlesize": 8.2,
    "xtick.labelsize": 6.4, "ytick.labelsize": 6.4, "legend.fontsize": 6.4,
    "axes.linewidth": 0.55, "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
})

def terrain(ax, d, cax):
    safe = d["design"]["safe"]; unsafe = d["design"]["unsafe"]
    grid = {(p["safe"], p["unsafe"]): p["f1"] for p in d["surface"]}
    X, Y = np.meshgrid(safe, unsafe, indexing="xy")
    Z = np.array([[grid[(s, u)] for s in safe] for u in unsafe])
    levels = np.unique(Z)
    interp = RegularGridInterpolator((np.array(unsafe), np.array(safe)), Z, method="linear")
    sf = np.linspace(min(safe), max(safe), 320); uf = np.linspace(min(unsafe), max(unsafe), 260)
    Xf, Yf = np.meshgrid(sf, uf, indexing="xy")
    Zf = interp(np.column_stack([Yf.ravel(), Xf.ravel()])).reshape(Xf.shape)
    Zf = gaussian_filter(Zf, sigma=6.0, mode="nearest")
    vis_levels = np.unique(np.percentile(levels, np.linspace(0, 100, 7)))
    cmap = plt.get_cmap("viridis"); norm = Normalize(vmin=0.76, vmax=0.89)
    ax.computed_zorder = False
    surf = ax.plot_surface(Xf, Yf, Zf, cmap=cmap, norm=norm, rstride=1, cstride=1,
                           linewidth=0, antialiased=True, shade=True, zorder=1)
    gx, gy, gz = d["best"]["safe"], d["best"]["unsafe"], d["best"]["f1"]
    ax.plot([gx], [gy], [gz + 0.008], marker="o", markersize=9.5,
            markerfacecolor="white", markeredgecolor="#222222", markeredgewidth=1.0,
            linestyle="none", zorder=20)
    ax.text(gx + 0.010, gy + 0.012, gz + 0.004, f"最优 F1={gz:.3f}",
            fontsize=6.3, color="#222222", zorder=21, va="center",
            bbox=dict(boxstyle="round,pad=0.16", facecolor="white", edgecolor="none", alpha=0.80))
    ax.set_xlabel("安全阈值 $t_s$", labelpad=9); ax.set_ylabel("不安全阈值 $t_u$", labelpad=7)
    ax.set_zlabel(""); ax.set_xlim(min(safe) - 0.022, max(safe) + 0.004); ax.set_ylim(min(unsafe) - 0.030, max(unsafe) + 0.006); ax.set_zlim(0.755, 0.885)
    ax.set_xticks([0.08, 0.15, 0.22, 0.30]); ax.set_yticks([0.50, 0.62, 0.70, 0.82]); ax.set_zticks([])
    ax.tick_params(axis="both", pad=0, labelsize=6)
    ax.view_init(elev=26, azim=-58); ax.set_box_aspect((1.35, 1.0, 0.82))
    ax.set_title("(a) 阈值敏感性地形（门控中心=10）", loc="left", pad=2, fontweight="bold")
    ax.grid(False)
    for plane in (ax.xaxis, ax.yaxis, ax.zaxis):
        plane.pane.set_facecolor((1, 1, 1, 0)); plane.pane.set_edgecolor("#E8E8E8")
    handles = [Line2D([0], [0], marker="*", color="none", markerfacecolor="#D62728", markeredgecolor="white", markersize=10, label="部署点"),
               Line2D([0], [0], marker="o", color="none", markerfacecolor="white", markeredgecolor="#111111", markersize=7, label="最优")]
    cb = plt.colorbar(surf, cax=cax, ticks=vis_levels)
    cb.set_label("F1 分数", fontsize=6.4, labelpad=3); cb.ax.tick_params(labelsize=5.7, pad=1)
    cb.ax.set_yticklabels([f"{v:.3f}" for v in vis_levels])


def ecdf(ax, d):
    base = d["baseline"]["f1"]
    x = np.sort(np.array([r["f1"] for r in d["lhs"]["samples"]]) - base)
    y = np.arange(1, len(x) + 1) / len(x)
    p25, p975 = np.percentile(x, [2.5, 97.5]); med = np.median(x)
    ax.fill_betweenx([0, 1], p25, p975, color="#009E73", alpha=0.10, lw=0)
    ax.step(x, y, where="post", color="#009E73", lw=1.45, zorder=3)
    ax.axvline(0, color="#222222", lw=0.75, ls="--", zorder=1)
    ax.axvline(med, color="#009E73", lw=0.8, ls=":", zorder=1)
    ax.scatter([med], [0.5], color="#007A5E", s=22, zorder=4)
    ax.set_xlim(-0.09, 0.045); ax.set_ylim(0, 1.02)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0]); ax.set_yticklabels(["0", "25", "50", "75", "100"])
    ax.set_xlabel("ΔF1（相对部署点）", labelpad=4); ax.set_ylabel("累积比例（%）", labelpad=2)
    ax.grid(color="#F2F2F2", lw=0.45); ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.set_title("(b) 联合采样 ECDF（n=1000）", loc="left", fontweight="bold", pad=7)
    ax.text(0.03, 0.94, f"中位数 {med:+.3f}\n95% [{p25:+.3f}, {p975:+.3f}]",
            transform=ax.transAxes, fontsize=6.2, va="top", color="#007A5E")
    frac = float(np.mean(x >= (0.80 - base)))
    ax.text(0.97, 0.12, f"P(F1 ≥ 0.80) = {frac*100:.1f}%", transform=ax.transAxes,
            ha="right", fontsize=6.0, color="#007A5E")
    ax.text(0.97, 0.06, f"n = {len(x)}", transform=ax.transAxes, ha="right", fontsize=5.8, color="#666666")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    d = json.loads(a.metrics.read_text())["data"]
    fig = plt.figure(figsize=(7.2, 4.2))
    ax3d = fig.add_axes([0.010, 0.055, 0.500, 0.895], projection="3d")
    cax = fig.add_axes([0.520, 0.31, 0.012, 0.40])
    terrain(ax3d, d, cax)
    axc = fig.add_axes([0.665, 0.225, 0.320, 0.665])
    ecdf(axc, d)
    fig.text(0.665, 0.105, "采样设置", fontsize=6.2, fontweight="bold", color="#222222")
    fig.text(0.665, 0.072, "safe 0.08–0.30；unsafe 0.50–0.86", fontsize=5.2, color="#333333")
    fig.text(0.665, 0.043, "gate 5–20；weights ×0.8–1.2；n=1000", fontsize=5.2, color="#333333")
    (a.out / "figures").mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf", "svg"):
        fig.savefig(a.out / "figures" / f"fig8_sensitivity.{ext}", dpi=600, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
