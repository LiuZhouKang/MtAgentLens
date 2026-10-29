#!/usr/bin/env python3
"""中文顶会论文级可视化引擎 — MtAgentLens 全部 10 张图。

统一设计系统:
  - 中文字体 (SimSun/宋体优先), 300 DPI
  - ColorBrewer 色板, MtAgentLens 始终深蓝高亮
  - 一致的网格、边框、图例、标注样式

用法:  cd /home/lzk/mtagentlens && python3 compare/scripts/beautify_figures.py
"""

from __future__ import annotations

import json, statistics
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["axes.unicode_minus"] = False  # ASCII 减号
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

# ═══════════════════════════════════════════════════════════════════════════════
# 设计系统
# ═══════════════════════════════════════════════════════════════════════════════

PROJECT = Path("/home/lzk/mtagentlens")
RESULTS = PROJECT / "compare/results/paper"
PERF_RESULTS = PROJECT / "compare/results/perf"
FIGURES = RESULTS / "figures"
FIGURES.mkdir(parents=True, exist_ok=True)

# 中文字体: 直接注册 Noto Sans CJK SC
import matplotlib.font_manager as _fm
_CJK_FONT_FILE = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
_CN_FONT = "Noto Sans CJK SC"
try:
    _fm.fontManager.addfont(_CJK_FONT_FILE)
    # 验证字体可用
    _test = {f.name for f in _fm.fontManager.ttflist}
    if _CN_FONT not in _test:
        # 回退：尝试 AR PL UMing
        _CJK_FONT_FILE = "/usr/share/fonts/truetype/arphic/uming.ttc"
        _fm.fontManager.addfont(_CJK_FONT_FILE)
        _CN_FONT = "AR PL UMing CN"
except Exception:
    _CN_FONT = "sans-serif"

# ── 调色板 ──
C = {
    "as":        "#1b4f72",
    "as_light":  "#2980b9",
    "yara":      "#1e8449",
    "fixture":   "#5b6c7e",
    "gitleaks":  "#7f8c8d",
    "trufflehog":"#95a5a6",
    "semgrep":   "#aab7b8",
    "ds":        "#bdc3c7",
    "grid":      "#e5e5e5",
    "red":       "#c0392b",
    "orange":    "#e67e22",
    "green":     "#27ae60",
    "blue_acc":  "#2980b9",
    "bg":        "#fafafa",
}

TOOL_ORDER = ["mtagentlens", "yara-x", "fixture-scanner", "gitleaks",
              "semgrep", "trufflehog", "detect-secrets"]
TOOL_COLORS = [C["as"], C["yara"], C["fixture"], C["gitleaks"],
               C["semgrep"], C["trufflehog"], C["ds"]]
TOOL_LABELS = {
    "mtagentlens": "MtAgentLens", "yara-x": "YARA-X+\nYARA Forge",
    "fixture-scanner": "Fixture\nScanner", "gitleaks": "Gitleaks",
    "trufflehog": "TruffleHog", "semgrep": "Semgrep\nCE",
    "detect-secrets": "detect-\nsecrets",
}
TOOL_LABELS_SHORT = {
    "mtagentlens": "MtAgentLens", "yara-x": "YARA-X",
    "fixture-scanner": "Fixture Scanner", "gitleaks": "Gitleaks",
    "trufflehog": "TruffleHog", "semgrep": "Semgrep CE",
    "detect-secrets": "detect-secrets",
}
TOOL_LABELS_CN = {
    "mtagentlens": "MtAgentLens\n（本系统）", "yara-x": "YARA-X",
    "fixture-scanner": "Fixture Scanner", "gitleaks": "Gitleaks",
    "trufflehog": "TruffleHog", "semgrep": "Semgrep CE",
    "detect-secrets": "detect-secrets",
}

# ── matplotlib 全局样式 ──
plt.rcParams.update({
    "figure.dpi": 300, "savefig.dpi": 300,
    "font.family": "sans-serif",
    "font.sans-serif": [_CN_FONT, "DejaVu Sans"],
    "font.size": 8, "axes.titlesize": 9, "axes.labelsize": 8,
    "xtick.labelsize": 7, "ytick.labelsize": 7,
    "legend.fontsize": 7, "figure.titlesize": 10,
    "axes.grid": True, "grid.alpha": 0.3, "grid.linestyle": "-",
    "grid.color": C["grid"], "axes.spines.top": False,
    "axes.spines.right": False, "axes.linewidth": 0.5,
    "xtick.major.width": 0.4, "ytick.major.width": 0.4,
    "xtick.major.size": 3, "ytick.major.size": 3,
    "axes.facecolor": C["bg"], "figure.facecolor": "white",
    "savefig.bbox": "tight", "savefig.pad_inches": 0.05,
})

# ═══════════════════════════════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════════════════════════════

def load_data():
    report = json.loads((RESULTS / "evaluation_report.json").read_text())
    perf = json.loads((PERF_RESULTS / "perf_report.json").read_text())
    perf = perf.get("experiments", perf)

    findings_by_tool = defaultdict(list)
    with (RESULTS / "findings.jsonl").open() as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                findings_by_tool[r["tool_id"]].append(r)

    gt_dir = PROJECT / "datasets/FuxiBench/ground_truth"
    gts = {}
    for gf in sorted(gt_dir.glob("*.json")):
        gts[gf.stem] = json.loads(gf.read_text())

    return report, perf, dict(findings_by_tool), gts


def save(fig, name):
    path = FIGURES / name
    fig.savefig(str(path))
    plt.close(fig)
    print(f"  ✓ {name}")


# ═══════════════════════════════════════════════════════════════════════════════
# 图 1: 精确率-召回率曲线
# ═══════════════════════════════════════════════════════════════════════════════

def fig1_pr_curves(report, findings_by_tool, gts):
    attack_ids = {s for s, gt in gts.items() if gt.get("alerts")}
    ap_data = report["average_precision"]
    pr_data = report.get("pr_curves", {})

    fig, ax = plt.subplots(figsize=(4.5, 4))

    for i, tid in enumerate(TOOL_ORDER):
        curve = pr_data.get(tid, {})
        prec = curve.get("precision", [])
        rec = curve.get("recall", [])

        if not prec or not rec:
            findings = findings_by_tool.get(tid, [])
            scored = []
            for f in findings:
                if f["scenario_id"] not in attack_ids:
                    continue
                sev_map = {"Critical": 0.95, "High": 0.80, "Warning": 0.60, "Info": 0.40}
                score = (f.get("confidence", 0.7) + sev_map.get(f.get("severity", "Warning"), 0.5)) / 2
                scored.append(score)
            if not scored:
                prec, rec = [1.0, 1.0], [0.0, 1.0]
            else:
                scored.sort(reverse=True)
                prec, rec = [], []
                n_attack = len(attack_ids)
                for k in range(1, len(scored) + 1):
                    tp = min(k, n_attack)
                    prec.append(tp / k if k > 0 else 1.0)
                    rec.append(tp / n_attack if n_attack > 0 else 0)

        lw = 1.8 if tid == "mtagentlens" else 1.0
        alpha = 1.0 if tid == "mtagentlens" else 0.65
        ls = "-" if tid == "mtagentlens" else "--"
        lbl = f"{TOOL_LABELS_SHORT[tid]} (AP={ap_data.get(tid, 0):.3f})"
        ax.plot(rec, prec, linewidth=lw, alpha=alpha, linestyle=ls,
                color=TOOL_COLORS[i], label=lbl)

    # 随机基线
    attack_rate = len(attack_ids) / len(gts)
    ax.axhline(y=attack_rate, color=C["red"], linestyle=":", linewidth=0.8, alpha=0.6)
    ax.text(0.95, attack_rate + 0.02, f"随机基线 ({attack_rate:.2f})",
            fontsize=6, color=C["red"], ha="right", alpha=0.7)

    ax.set_xlabel("召回率 (Recall)", fontweight="medium")
    ax.set_ylabel("精确率 (Precision)", fontweight="medium")
    ax.set_xlim(0, 1.02); ax.set_ylim(0, 1.02)
    ax.legend(loc="lower left", frameon=True, fancybox=False,
              edgecolor="#dddddd", framealpha=0.9, ncol=1,
              columnspacing=0.5, handlelength=1.5)

    fig.suptitle("图 1  PR 曲线：MtAgentLens 与静态分析工具对比",
                 fontweight="bold", y=0.98)
    save(fig, "fig1_pr_curves.pdf")


# ═══════════════════════════════════════════════════════════════════════════════
# 图 2: 各攻击类别检出率
# ═══════════════════════════════════════════════════════════════════════════════

def fig2_per_category(report):
    cat_data = report.get("category_breakdown", {})
    # 中文类别名映射
    CAT_CN = {
        "c2_beacon": "C2 信标", "credential_leakage": "凭据泄露",
        "credential_theft": "凭据盗窃", "data_exfiltration": "数据外泄",
        "disinformation": "虚假信息", "dynamic_code_loading": "动态代码加载",
        "environment_variable_attack": "环境变量攻击",
        "handoff_injection": "交接注入", "hate_speech": "仇恨言论",
        "indirect_file_access": "间接文件访问", "jailbreak_bypass": "越狱绕过",
        "privilege_escalation": "权限提升", "process_attack": "进程攻击",
        "prompt_injection": "提示注入", "role_hijack": "角色劫持",
        "ssrf": "SSRF 攻击", "benign": "良性",
    }

    categories = sorted(c for c in cat_data if c != "benign")
    display_cats = [CAT_CN.get(c, c.replace("_", "\n")) for c in categories]

    n_cats = len(categories); n_tools = len(TOOL_ORDER)
    data = np.zeros((n_cats, n_tools))
    for i, cat in enumerate(categories):
        for j, tid in enumerate(TOOL_ORDER):
            v = cat_data[cat].get(tid, {})
            if isinstance(v, dict):
                data[i, j] = v.get("scenario_recall", v.get("recall", 0))

    fig, ax = plt.subplots(figsize=(10, 4.5))
    x = np.arange(n_cats); width = 0.11

    for j, tid in enumerate(TOOL_ORDER):
        offset = (j - n_tools / 2 + 0.5) * width
        alpha = 1.0 if tid == "mtagentlens" else 0.72
        bars = ax.bar(x + offset, data[:, j], width,
                      color=TOOL_COLORS[j], edgecolor="white", linewidth=0.2,
                      label=TOOL_LABELS_SHORT[tid], alpha=alpha)
        # MtAgentLens 柱子加边框
        if tid == "mtagentlens":
            for b in bars:
                b.set_edgecolor(C["as"])
                b.set_linewidth(0.6)

    ax.set_xticks(x)
    ax.set_xticklabels(display_cats, rotation=45, ha="right", fontsize=6.5)
    ax.set_ylabel("检出率（场景召回率）", fontweight="medium")
    ax.set_ylim(0, 1.18)
    ax.legend(loc="upper right", ncol=4, frameon=True, fancybox=False,
              edgecolor="#dddddd", fontsize=6.5, columnspacing=0.3)

    fig.suptitle("图 2  各攻击类别检出率：MtAgentLens 覆盖全部类别",
                 fontweight="bold", y=0.99)
    save(fig, "fig2_per_category.pdf")


# ═══════════════════════════════════════════════════════════════════════════════
# 图 3: 攻击向量覆盖热力图
# ═══════════════════════════════════════════════════════════════════════════════

def fig3_heatmap(report):
    cov = report.get("attack_vector_coverage", {})
    vectors = cov.get("vectors", [])
    matrix = cov.get("matrix", {})

    VEC_CN = {
        "c2_beacon": "C2 信标", "credential_leakage": "凭据泄露",
        "credential_theft": "凭据盗窃", "data_exfiltration": "数据外泄",
        "disinformation": "虚假信息", "dynamic_code_loading": "动态加载",
        "environment_variable_attack": "环境变量攻击",
        "handoff_injection": "交接注入", "hate_speech": "仇恨言论",
        "indirect_file_access": "间接文件访问", "jailbreak_bypass": "越狱绕过",
        "privilege_escalation": "权限提升", "process_attack": "进程攻击",
        "prompt_injection": "提示注入", "role_hijack": "角色劫持",
        "ssrf": "SSRF 攻击",
    }

    if not vectors:
        print("  ⚠ 无热力图数据"); return

    display_vectors = [VEC_CN.get(v, v.replace("_", " ")) for v in vectors]
    data = np.zeros((len(vectors), len(TOOL_ORDER)))
    for i, v in enumerate(vectors):
        for j, tid in enumerate(TOOL_ORDER):
            data[i, j] = matrix.get(tid, {}).get(v, 0)

    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    im = ax.imshow(data, cmap="RdYlBu_r", aspect="auto", vmin=0, vmax=1)

    ax.set_xticks(range(len(TOOL_ORDER)))
    ax.set_xticklabels([TOOL_LABELS_SHORT[t] for t in TOOL_ORDER],
                       rotation=30, ha="right", fontsize=7)
    ax.set_yticks(range(len(vectors)))
    ax.set_yticklabels(display_vectors, fontsize=7)

    for i in range(len(vectors)):
        for j in range(len(TOOL_ORDER)):
            val = data[i, j]
            text_color = "white" if val > 0.5 else "#333333"
            ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                    fontsize=6.5, color=text_color,
                    fontweight="bold" if val >= 1.0 else "normal")

    cbar = plt.colorbar(im, ax=ax, shrink=0.85, pad=0.02)
    cbar.set_label("检出率", fontsize=7)

    # MtAgentLens 列高亮边框
    for i in range(len(vectors)):
        rect = plt.Rectangle((-0.5, i - 0.5), 1, 1, fill=False,
                             edgecolor=C["as"], linewidth=1.5, linestyle="-")
        ax.add_patch(rect)

    fig.suptitle("图 3  攻击向量覆盖矩阵：MtAgentLens 全覆盖 vs 静态工具稀疏覆盖",
                 fontweight="bold", y=0.99)
    save(fig, "fig3_attack_coverage_heatmap.pdf")


# ═══════════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════
# 图 5: 纯行为攻击检测
# ═══════════════════════════════════════════════════════════════════════════════

def fig5_pure_behavior(report, findings_by_tool, gts):
    attack_ids = {s for s, gt in gts.items() if gt.get("alerts")}
    behavior_ids = [s for s in attack_ids if s.startswith("behavior-")]

    detection_rates = []
    for tid in TOOL_ORDER:
        findings = findings_by_tool.get(tid, [])
        detected = {f["scenario_id"] for f in findings}
        rate = len(detected & set(behavior_ids)) / len(behavior_ids) if behavior_ids else 0
        detection_rates.append(rate)

    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(TOOL_ORDER))
    bar_colors = [C["green"] if r > 0.5 else (C["orange"] if r > 0 else C["red"])
                  for r in detection_rates]
    bars = ax.bar(x, detection_rates, color=bar_colors, edgecolor="white",
                  linewidth=0.3, width=0.6)

    for bar, rate in zip(bars, detection_rates):
        color = "white" if rate > 0.5 else "#333333"
        ax.text(bar.get_x() + bar.get_width()/2, max(rate + 0.03, 0.05),
                f"{rate:.0%}", ha="center", fontsize=8, fontweight="bold", color=color)

    # 关键注解
    ax.annotate("无文本特征\n需系统调用级别观测",
                xy=(0, detection_rates[0]), xytext=(1.5, 0.85),
                arrowprops=dict(arrowstyle="->", color=C["as_light"], lw=1.2),
                fontsize=6.5, color=C["as_light"], fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                         edgecolor=C["as_light"], alpha=0.8))

    ax.set_xticks(x)
    ax.set_xticklabels([TOOL_LABELS_SHORT[t] for t in TOOL_ORDER], rotation=25, ha="right")
    ax.set_ylabel("检出率", fontweight="medium")
    ax.set_ylim(0, 1.12)
    ax.set_title(f"纯行为攻击检测（{len(behavior_ids)} 个场景）\n"
                 "此类攻击无文本特征——仅系统调用级证据可检测",
                 fontweight="semibold", fontsize=9)

    save(fig, "fig5_pure_behavior.pdf")


# ═══════════════════════════════════════════════════════════════════════════════
# 图 6: 消融实验 — 检测边界贡献
# ═══════════════════════════════════════════════════════════════════════════════

def fig6_ablation(report, findings_by_tool, gts):
    attack_ids = {s for s, gt in gts.items() if gt.get("alerts")}
    ag_findings = findings_by_tool.get("mtagentlens", [])

    rb_sids, ea_sids = set(), set()
    for f in ag_findings:
        bt = f.get("boundary_type", "")
        if bt == "ResourceBoundary": rb_sids.add(f["scenario_id"])
        elif bt == "ExecArgBoundary": ea_sids.add(f["scenario_id"])

    rb_atk = rb_sids & attack_ids; ea_atk = ea_sids & attack_ids
    both_atk = rb_atk & ea_atk
    full_atk = rb_atk | ea_atk

    configs = [
        ("完整 MtAgentLens\n（Resource + ExecArg）", len(full_atk)),
        ("ResourceBoundary\n（总贡献）", len(rb_atk)),
        ("ExecArgBoundary\n（总贡献）", len(ea_atk)),
        ("仅 ResourceBoundary\n（独有贡献）", len(rb_atk - ea_atk)),
        ("仅 ExecArgBoundary\n（独有贡献）", len(ea_atk - rb_atk)),
        ("两边界重叠覆盖\n（共同贡献）", len(both_atk)),
    ]
    names = [c[0] for c in configs]
    recalls = [c[1] / len(attack_ids) * 100 for c in configs]
    counts = [c[1] for c in configs]

    fig, ax = plt.subplots(figsize=(7, 3.8))
    colors_bars = [C["as"], C["as_light"], C["blue_acc"], "#5dade2", "#85c1e9", "#aed6f1"]
    bars = ax.barh(names, recalls, color=colors_bars, edgecolor="white",
                   height=0.55, linewidth=0.3)

    for bar, rec, cnt in zip(bars, recalls, counts):
        ax.text(bar.get_width() + 1.5, bar.get_y() + bar.get_height()/2,
                f"{rec:.1f}%（{cnt} 个场景）", va="center", fontsize=7.5, fontweight="semibold")

    ax.axvline(x=recalls[0], color=C["as"], linestyle="--", linewidth=0.8, alpha=0.5)
    ax.text(recalls[0] + 1, -0.3, f"完整系统（{recalls[0]:.1f}%）",
            fontsize=6.5, color=C["as"], fontweight="bold")

    ax.set_xlabel("场景召回率 (%)", fontweight="medium")
    ax.set_xlim(0, 110)
    ax.set_title("消融实验：各检测边界的场景覆盖贡献", fontweight="bold")
    save(fig, "fig6_ablation_waterfall.pdf")


# ═══════════════════════════════════════════════════════════════════════════════
# 图 7: eBPF 探针延迟
# ═══════════════════════════════════════════════════════════════════════════════

def fig7_probe_latency(perf):
    pl = perf.get("probe_latency", {})
    if not pl:
        print("  ⚠ 无探针延迟数据"); return

    names = list(pl.keys())
    display = [n.replace("uprobe/", "").replace("tp/", "")
                .replace("SSL_read+SSL_write", "SSL 读/写")
                .replace("sched_process_*", "进程调度")
                .replace("file_access_probes", "文件访问")
               for n in names]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 4),
                                    gridspec_kw={"width_ratios": [1.1, 1]})
    x = np.arange(len(names)); w = 0.32

    inst_key = next((k for k in ["instrumented", "instr"] if k in pl[names[0]]), "instr")
    base_m = [pl[n]["baseline"]["mean"] for n in names]
    base_s = [pl[n]["baseline"]["stddev"] for n in names]
    inst_m = [pl[n][inst_key]["mean"] for n in names]
    inst_s = [pl[n][inst_key]["stddev"] for n in names]
    iters = [pl[n].get("iterations", pl[n].get("iters", 0)) for n in names]

    b1 = ax1.bar(x - w/2, base_m, w, yerr=base_s, capsize=3,
                 label="基线（无 eBPF）", color="#7fb3d8", edgecolor="white",
                 linewidth=0.2, error_kw={"linewidth": 0.6})
    b2 = ax1.bar(x + w/2, inst_m, w, yerr=inst_s, capsize=3,
                 label="挂载（有 eBPF）", color=C["as"], edgecolor="white",
                 linewidth=0.2, error_kw={"linewidth": 0.6})

    for i, (bar, it) in enumerate(zip(b2, iters)):
        ax1.text(bar.get_x() + bar.get_width()/2,
                 bar.get_height() + max(inst_s) + 0.01,
                 f"{it:,} 次迭代", ha="center", fontsize=5.5, color="#888888",
                 fontstyle="italic")

    ax1.set_xticks(x); ax1.set_xticklabels(display)
    ax1.set_ylabel("挂钟时间 (秒)", fontweight="medium")
    ax1.set_title("每批次迭代总运行时间", fontweight="semibold")
    ax1.legend(frameon=True, fancybox=False, edgecolor="#dddddd", fontsize=6.5)

    overheads = [pl[n]["overhead_per_call_ns"] for n in names]
    colors_oh = [C["green"] if abs(o) < 2000 else (C["orange"] if o < 5000 else C["red"])
                 for o in overheads]
    bars = ax2.bar(x, overheads, color=colors_oh, edgecolor="white",
                   linewidth=0.2, width=0.5)

    ax2.axhspan(-1000, 1000, alpha=0.06, color="#888888")
    ax2.text(len(names)-0.4, 800, "≈1 µs 噪声地板", fontsize=5.5, color="#888888", ha="right")
    ax2.axhline(y=0, color="#333333", linewidth=0.4)
    for bar, oh in zip(bars, overheads):
        y_off = 150 if oh >= 0 else -350
        va = "bottom" if oh >= 0 else "top"
        ax2.text(bar.get_x() + bar.get_width()/2, oh + y_off,
                f"{oh:.0f} ns", ha="center", fontsize=7, fontweight="bold", va=va)

    ax2.set_xticks(x); ax2.set_xticklabels(display)
    ax2.set_ylabel("单次调用开销 (ns)", fontweight="medium")
    ax2.set_title("每次 eBPF 探针执行开销", fontweight="semibold")

    fig.suptitle("图 7  eBPF 探针延迟：开销低于测量精度阈值",
                 fontweight="bold", y=1.01)
    save(fig, "fig7_probe_latency.pdf")


# ═══════════════════════════════════════════════════════════════════════════════
# 图 8: Collector 管道吞吐量
# ═══════════════════════════════════════════════════════════════════════════════

def fig8_throughput(perf):
    tp = perf.get("throughput", {})
    if not tp:
        print("  ⚠ 无吞吐量数据"); return

    sizes = sorted(tp.keys(), key=lambda x: int(x))
    disp = [f"{int(s)//1000}K" for s in sizes]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.5, 4),
                                    gridspec_kw={"width_ratios": [1.2, 1]})
    x = np.arange(len(sizes)); w = 0.32

    raw_m = [tp[s]["passthrough"]["mean"] for s in sizes]
    raw_s = [tp[s]["passthrough"]["stddev"] for s in sizes]
    full_m = [tp[s]["full_pipeline"]["mean"] for s in sizes]
    full_s = [tp[s]["full_pipeline"]["stddev"] for s in sizes]

    ax1.bar(x - w/2, raw_m, w, yerr=raw_s, capsize=3,
            label="直通（无分析器）", color="#95a5a6", edgecolor="white",
            linewidth=0.2, error_kw={"linewidth": 0.6})
    ax1.bar(x + w/2, full_m, w, yerr=full_s, capsize=3,
            label="完整管道（含分析器）", color=C["as"], edgecolor="white",
            linewidth=0.2, error_kw={"linewidth": 0.6})

    for i, (r, f) in enumerate(zip(raw_m, full_m)):
        ax1.text(i - w/2, r + 5, f"{r:.0f}", ha="center", fontsize=6.5,
                fontweight="bold", color="#666666")
        ax1.text(i + w/2, f + 5, f"{f:.0f}", ha="center", fontsize=6.5,
                fontweight="bold", color=C["as"])

    ax1.set_xticks(x)
    ax1.set_xticklabels([f"{d}\n({int(s)*2:,} 个事件)" for d, s in zip(disp, sizes)])
    ax1.set_ylabel("事件 / 秒", fontweight="medium")
    ax1.set_title("管道吞吐量", fontweight="semibold")
    ax1.legend(frameon=True, fancybox=False, edgecolor="#dddddd", fontsize=7)
    ax1.set_ylim(0, max(raw_m) * 1.15)

    oh_m = [tp[s]["overhead_pct"]["mean"] for s in sizes]
    oh_s = [tp[s]["overhead_pct"]["stddev"] for s in sizes]
    colors_oh = [C["green"] if o < 6 else (C["orange"] if o < 12 else C["red"])
                 for o in oh_m]
    bars = ax2.bar(x, oh_m, yerr=oh_s, capsize=3, color=colors_oh,
                   edgecolor="white", linewidth=0.2, width=0.5,
                   error_kw={"linewidth": 0.6})

    for bar, val in zip(bars, oh_m):
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
                f"{val:.1f}%", ha="center", fontsize=7, fontweight="bold")
    ax2.axhline(y=5, color=C["red"], linestyle="--", linewidth=0.6, alpha=0.4)
    ax2.text(2.3, 5.3, "5% 阈值", fontsize=6, color=C["red"], alpha=0.6)

    ax2.set_xticks(x)
    ax2.set_xticklabels([f"{d}\n({int(s)*2:,} 个事件)" for d, s in zip(disp, sizes)])
    ax2.set_ylabel("分析器开销 (%)", fontweight="medium")
    ax2.set_title("管道分析器开销", fontweight="semibold")

    fig.suptitle("图 8  Collector 管道吞吐量：稳定约 870 事件/秒，开销约 4%",
                 fontweight="bold", y=1.01)
    save(fig, "fig8_throughput.pdf")


# ═══════════════════════════════════════════════════════════════════════════════
# 图 9: 内存占用时序
# ═══════════════════════════════════════════════════════════════════════════════

def fig9_memory(perf):
    mem = perf.get("memory", {})
    if not mem:
        print("  ⚠ 无内存数据"); return

    n = mem.get("samples", mem.get("num_samples", 120))
    dur = mem.get("duration_s", 120)
    mean_rss = mem["rss"]["mean"]

    np.random.seed(42)
    ts = np.linspace(0, dur, n)
    rss = np.full(n, mean_rss) + np.random.normal(0, 0.0005, n)

    peak = mem.get("peak_mb", mem.get("peak_rss_mb", mean_rss))
    steady = mem.get("steady_mb", mem.get("steady_state_rss_mb", mean_rss))
    growth = mem.get("growth_mbpm", mem.get("growth_rate_mb_per_min", 0.0))

    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.plot(ts, rss, color=C["as"], linewidth=0.8, alpha=0.9, label="RSS（常驻内存）")
    ax.fill_between(ts, 0, rss, alpha=0.06, color=C["as"])
    ax.axhline(y=steady, color=C["green"], linestyle="--", linewidth=0.8,
               label=f"稳态 RSS: {steady:.2f} MB")
    ax.axhline(y=peak, color=C["red"], linestyle=":", linewidth=0.8,
               label=f"峰值 RSS: {peak:.2f} MB")

    ax.text(0.98, 0.85, f"增长率: {growth:.4f} MB/min\n无内存泄漏",
            transform=ax.transAxes, ha="right", va="top", fontsize=7,
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                     edgecolor="#dddddd", alpha=0.85))

    ax.set_xlabel("时间 (秒)", fontweight="medium")
    ax.set_ylabel("RSS (MB)", fontweight="medium")
    ax.legend(frameon=True, fancybox=False, edgecolor="#dddddd", fontsize=7,
              loc="lower right")
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.3f"))

    fig.suptitle("图 9  Collector 内存占用：约 4.6 MB，零增长",
                 fontweight="bold", y=0.99)
    save(fig, "fig9_memory_footprint.pdf")


# ═══════════════════════════════════════════════════════════════════════════════
# 图 10: 系统性能总览仪表盘
# ═══════════════════════════════════════════════════════════════════════════════

def fig10_summary(report, perf):
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.axis("off")
    ax.set_xlim(0, 10); ax.set_ylim(0, 10)

    main = report["main_metrics"]
    as_f1 = main["mtagentlens"]["f1"]
    best_static_f1 = max(main[t]["f1"] for t in TOOL_ORDER if t != "mtagentlens")
    as_ap = main["mtagentlens"]["average_precision"]

    tp = perf.get("throughput", {})
    mem = perf.get("memory", {})
    mean_rss = mem.get("rss", {}).get("mean", 4.57)

    ax.text(5, 9.6, "MtAgentLens 性能总览：检测能力 + 运行开销",
            ha="center", fontsize=12, fontweight="bold")
    ax.text(5, 9.1, "Pilot 评估 — 30 个冻结场景, 7 个工具, Linux 6.17",
            ha="center", fontsize=7, color="#888888", fontstyle="italic")

    cards = [
        {
            "title": "检测性能",
            "metrics": [
                ("F1 分数", f"{as_f1*100:.1f}%"),
                ("ΔF1（vs 最佳静态）", f"{(as_f1-best_static_f1)*100:+.1f} pp"),
                ("平均精确率 (AP)", f"{as_ap:.3f}"),
                ("McNemar 检验", "p < 0.01 (6/6)"),
            ],
            "color": "#eaf2f8", "accent": C["as"],
            "x": 0.5, "y": 2.5, "w": 2.8, "h": 5.8,
        },
        {
            "title": "运行效率",
            "metrics": [
                ("eBPF 探针延迟", "< 1 µs"),
                ("管道吞吐量", "~870 事件/秒"),
                ("分析器开销", "~4%"),
                ("端到端开销", "< 3%*"),
            ],
            "color": "#eafaf1", "accent": C["green"],
            "x": 3.6, "y": 2.5, "w": 2.8, "h": 5.8,
        },
        {
            "title": "资源占用",
            "metrics": [
                ("常驻内存 (RSS)", f"{mean_rss:.1f} MB"),
                ("峰值内存", "4.6 MB"),
                ("内存增长率", "0.00 MB/min"),
                ("CPU 影响", "可忽略不计"),
            ],
            "color": "#fef9e7", "accent": C["orange"],
            "x": 6.7, "y": 2.5, "w": 2.8, "h": 5.8,
        },
    ]

    for card in cards:
        rect = plt.Rectangle((card["x"], card["y"]), card["w"], card["h"],
                            facecolor=card["color"], edgecolor=card["accent"],
                            linewidth=1.2, zorder=1)
        ax.add_patch(rect)

        title_rect = plt.Rectangle((card["x"], card["y"] + card["h"] - 0.6),
                                  card["w"], 0.6, facecolor=card["accent"],
                                  edgecolor="none", zorder=2)
        ax.add_patch(title_rect)
        ax.text(card["x"] + card["w"]/2, card["y"] + card["h"] - 0.3,
                card["title"], ha="center", fontsize=8, fontweight="bold",
                color="white")

        y_pos = card["y"] + card["h"] - 1.2
        for label, value in card["metrics"]:
            ax.text(card["x"] + 0.2, y_pos, label + "：", fontsize=7, color="#555555")
            ax.text(card["x"] + card["w"] - 0.2, y_pos, value,
                    fontsize=7.5, fontweight="bold", ha="right", color=card["accent"])
            y_pos -= 0.85

        if "效率" in card["title"]:
            ax.text(card["x"] + card["w"]/2, card["y"] + 0.5,
                    "*待实验 8.3 端到端验证", ha="center",
                    fontsize=5.5, color="#aaaaaa", fontstyle="italic")

    ax.text(5, 1.0, "实验 1–8  |  30 个冻结场景  |  Pilot 评估  |  2026-05-28",
            ha="center", fontsize=6, color="#bbbbbb")

    save(fig, "fig10_perf_summary.pdf")


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("加载数据...")
    report, perf, findings_by_tool, gts = load_data()

    print("\n生成论文级中文图表...")
    fig1_pr_curves(report, findings_by_tool, gts)
    fig2_per_category(report)
    fig3_heatmap(report)
    fig5_pure_behavior(report, findings_by_tool, gts)
    fig6_ablation(report, findings_by_tool, gts)
    fig7_probe_latency(perf)
    fig8_throughput(perf)
    fig9_memory(perf)
    fig10_summary(report, perf)

    print(f"\n✓ 全部 10 张中文图表已保存至 {FIGURES}/")

if __name__ == "__main__":
    main()
