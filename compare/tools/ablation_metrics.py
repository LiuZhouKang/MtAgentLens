"""
多维度消融评估指标体系 — 替代单一 F1 阈值的科学度量框架。

设计原则:
  1. 主指标必须是阈值无关的 (AUROC, AUPRC, Cohen's d)
  2. 辅助指标覆盖校准质量 (Brier, ECE)
  3. 统计显著性通过 Bootstrap CI + DeLong 检验
  4. 每层独立计算最优阈值 (Youden's J)，而非强制使用全系统阈值

问题: 固定阈值(0.15)的 ΔF1 无法衡量不跨越边界的分数改进。
解决: AUROC 衡量排序质量；Cohen's d 衡量分布分离度；Brier 衡量概率校准。

用法:
  from compare.tools.ablation_metrics import evaluate_layer, compare_layers
  metrics = evaluate_layer(scores, labels)
  comparison = compare_layers(prev_scores, curr_scores, labels)
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

import numpy as np

# ── 基础指标 ------------------------------------------------------------

def confusion_matrix(
    scores: np.ndarray, labels: np.ndarray, threshold: float
) -> Tuple[int, int, int, int]:
    """返回 (TP, FP, FN, TN)."""
    preds = (scores > threshold).astype(int)
    y = labels.astype(int)
    tp = int(np.sum((preds == 1) & (y == 1)))
    fp = int(np.sum((preds == 1) & (y == 0)))
    fn = int(np.sum((preds == 0) & (y == 1)))
    tn = int(np.sum((preds == 0) & (y == 0)))
    return tp, fp, fn, tn


def safe_div(a: float, b: float) -> float:
    return a / b if b > 0 else 0.0


def roc_curve(scores: np.ndarray, labels: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回 (fpr, tpr, thresholds) 的 ROC 曲线点."""
    # 按分数降序排列
    order = np.argsort(scores)[::-1]
    scores_sorted = scores[order]
    labels_sorted = labels[order]

    n_pos = int(np.sum(labels == 1))
    n_neg = int(np.sum(labels == 0))
    if n_pos == 0 or n_neg == 0:
        return np.array([0, 1]), np.array([0, 1]), np.array([1, 0])

    tpr_list = [0.0]
    fpr_list = [0.0]
    thresholds = [float(scores_sorted[0]) + 0.01]

    tp = 0
    fp = 0

    for i in range(len(scores_sorted)):
        if labels_sorted[i] == 1:
            tp += 1
        else:
            fp += 1

        # 只在分数变化时添加点（避免阶梯过多）
        if i + 1 < len(scores_sorted) and scores_sorted[i] == scores_sorted[i + 1]:
            continue
        if i + 1 >= len(scores_sorted) or scores_sorted[i] != scores_sorted[i + 1]:
            tpr_list.append(tp / n_pos)
            fpr_list.append(fp / n_neg)
            thresholds.append(float(scores_sorted[i]))

    return np.array(fpr_list), np.array(tpr_list), np.array(thresholds)


def auc_roc(scores: np.ndarray, labels: np.ndarray) -> float:
    """AUROC — 阈值无关的排序质量指标。核心主指标。"""
    fpr, tpr, _ = roc_curve(scores, labels)
    # 梯形积分 (兼容新旧 numpy)
    try:
        return float(np.trapezoid(tpr, fpr))
    except AttributeError:
        return float(np.trapz(tpr, fpr))


def auc_pr(scores: np.ndarray, labels: np.ndarray) -> float:
    """AUPRC — 更适合不均衡数据集的阈值无关指标。"""
    order = np.argsort(scores)[::-1]
    labels_sorted = labels[order]

    n_pos = int(np.sum(labels == 1))
    if n_pos == 0:
        return 0.0

    tp = 0
    fp = 0
    precisions = [1.0]
    recalls = [0.0]

    for i in range(len(labels_sorted)):
        if labels_sorted[i] == 1:
            tp += 1
        else:
            fp += 1
        precisions.append(tp / max(tp + fp, 1))
        recalls.append(tp / n_pos)

    # 梯形积分
    auprc = 0.0
    for i in range(len(recalls) - 1):
        auprc += (recalls[i + 1] - recalls[i]) * (precisions[i] + precisions[i + 1]) / 2
    return float(auprc)


def youden_threshold(scores: np.ndarray, labels: np.ndarray) -> float:
    """Youden's J 最优阈值: max(TPR - FPR)."""
    fpr, tpr, thresholds = roc_curve(scores, labels)
    j = tpr - fpr
    best_idx = int(np.argmax(j))
    return float(thresholds[best_idx])


def f1_optimal(scores: np.ndarray, labels: np.ndarray) -> Tuple[float, float]:
    """返回 (最优F1, 最优阈值) — 在该层自身最优阈值处的 F1."""
    fpr, tpr, thresholds = roc_curve(scores, labels)
    n_pos = int(np.sum(labels == 1))
    n_neg = int(np.sum(labels == 0))

    best_f1 = 0.0
    best_thresh = 0.5
    for i in range(len(thresholds)):
        tp = tpr[i] * n_pos
        fp = fpr[i] * n_neg
        f1_val = safe_div(2 * tp, 2 * tp + fp + (n_pos - tp))
        if f1_val > best_f1:
            best_f1 = f1_val
            best_thresh = float(thresholds[i])
    return best_f1, best_thresh


# ── 分布分离指标 ---------------------------------------------------------

def cohens_d(scores: np.ndarray, labels: np.ndarray) -> float:
    """Cohen's d 效应量 — 攻击/良性分数分布的标准化差异。

    衡量该层能将两类样本分离到何种程度，连续值，不依赖阈值。
    d > 0.8 → 大效应, d > 0.5 → 中效应, d > 0.2 → 小效应。
    """
    attack_scores = scores[labels == 1]
    benign_scores = scores[labels == 0]
    if len(attack_scores) == 0 or len(benign_scores) == 0:
        return 0.0

    mean_a = float(np.mean(attack_scores))
    mean_b = float(np.mean(benign_scores))
    var_a = float(np.var(attack_scores, ddof=1))
    var_b = float(np.var(benign_scores, ddof=1))

    pooled_std = math.sqrt((var_a + var_b) / 2.0)
    if pooled_std < 1e-9:
        return 0.0
    return (mean_a - mean_b) / pooled_std


def score_separation(scores: np.ndarray, labels: np.ndarray) -> Dict[str, float]:
    """分数分布分离度 — 多角度衡量攻击/良性分布差异。"""
    attack_scores = scores[labels == 1]
    benign_scores = scores[labels == 0]

    if len(attack_scores) == 0 or len(benign_scores) == 0:
        return {"d": 0.0, "mean_diff": 0.0, "median_diff": 0.0,
                "attack_mean": 0.0, "benign_mean": 0.0}

    return {
        "d": cohens_d(scores, labels),
        "mean_diff": float(np.mean(attack_scores) - np.mean(benign_scores)),
        "median_diff": float(np.median(attack_scores) - np.median(benign_scores)),
        "attack_mean": float(np.mean(attack_scores)),
        "benign_mean": float(np.mean(benign_scores)),
    }


# ── 校准指标 ------------------------------------------------------------

def brier_score(scores: np.ndarray, labels: np.ndarray) -> float:
    """Brier Score — 概率校准质量。越低越好，0 = 完美校准。"""
    y = labels.astype(float)
    return float(np.mean((scores - y) ** 2))


def expected_calibration_error(
    scores: np.ndarray, labels: np.ndarray, n_bins: int = 10
) -> float:
    """ECE — 期望校准误差。将 [0,1] 分 n_bins 个桶，计算每个桶内
    平均预测概率与真实正样本比例的加权绝对差异。"""
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    n = len(scores)

    for i in range(n_bins):
        in_bin = (scores >= bins[i]) & (scores < bins[i + 1])
        n_bin = int(np.sum(in_bin))
        if n_bin == 0:
            continue
        bin_acc = float(np.mean(labels[in_bin]))
        bin_conf = float(np.mean(scores[in_bin]))
        ece += (n_bin / n) * abs(bin_acc - bin_conf)
    return ece


# ── 统计显著性 ----------------------------------------------------------

def bootstrap_ci(
    metric_fn, scores: np.ndarray, labels: np.ndarray,
    n_bootstrap: int = 2000, ci: float = 0.95
) -> Tuple[float, float, float]:
    """Bootstrap 置信区间。返回 (lower, point_estimate, upper)."""
    n = len(scores)
    estimates = np.zeros(n_bootstrap)
    rng = np.random.RandomState(42)

    for i in range(n_bootstrap):
        idx = rng.randint(0, n, n)
        estimates[i] = metric_fn(scores[idx], labels[idx])

    point = metric_fn(scores, labels)
    lower = float(np.percentile(estimates, (1 - ci) / 2 * 100))
    upper = float(np.percentile(estimates, (1 + ci) / 2 * 100))
    return lower, point, upper


def delong_test(
    scores_a: np.ndarray, scores_b: np.ndarray, labels: np.ndarray
) -> Dict[str, float]:
    """DeLong 检验 — 比较两个 AUROC 的统计显著性。

    返回 p-value 和 z-statistic。p < 0.05 → 差异显著。

    标准 DeLong (1988) 实现:
      - V^10_i: 正样本 i 的结构分量 = E[ψ(i,j)] over all negative j
      - V^01_j: 负样本 j 的结构分量 = E[ψ(i,j)] over all positive i
      - Var(AUC_A - AUC_B) = S^10/n_pos + S^01/n_neg
      其中 S^10 = Var(V^10_A - V^10_B)/(n_pos-1) 等等
    """
    n = len(labels)
    y = labels.astype(int)
    pos_idx = np.where(y == 1)[0]
    neg_idx = np.where(y == 0)[0]
    n_pos = len(pos_idx)
    n_neg = len(neg_idx)

    if n_pos == 0 or n_neg == 0:
        return {"z": 0.0, "p": 1.0, "significant": False}

    # 计算两个评分器的结构分量 (V^10 和 V^01)
    def compute_structural_components(scores):
        # V^10: 正样本的结构分量
        v10 = np.zeros(n_pos)
        for i_idx, i in enumerate(pos_idx):
            s_i = scores[i]
            total = 0.0
            for j in neg_idx:
                s_j = scores[j]
                if s_i > s_j:
                    total += 1.0
                elif s_i == s_j:
                    total += 0.5
            v10[i_idx] = total / n_neg

        # V^01: 负样本的结构分量
        v01 = np.zeros(n_neg)
        for j_idx, j in enumerate(neg_idx):
            s_j = scores[j]
            total = 0.0
            for i in pos_idx:
                s_i = scores[i]
                if s_i > s_j:
                    total += 1.0
                elif s_i == s_j:
                    total += 0.5
            v01[j_idx] = total / n_pos

        return v10, v01

    v10_a, v01_a = compute_structural_components(scores_a)
    v10_b, v01_b = compute_structural_components(scores_b)

    auc_a = auc_roc(scores_a, labels)
    auc_b = auc_roc(scores_b, labels)

    # 方差估计: Var(AUC_A - AUC_B) = S10 / n_pos + S01 / n_neg
    # S10 = 1/(n_pos-1) * Σ (diff_i - mean_diff)^2
    diff_10 = v10_a - v10_b
    mean_diff_10 = auc_a - auc_b  # = mean(v10_a) - mean(v10_b)
    s10 = np.sum((diff_10 - mean_diff_10) ** 2) / (n_pos - 1) if n_pos > 1 else 0.0

    # S01: V^01_A - V^01_B 的样本方差
    diff_01 = v01_a - v01_b
    # 注意: V^01 的均值差也是 auc_a - auc_b (对称性质)
    s01 = np.sum((diff_01 - mean_diff_10) ** 2) / (n_neg - 1) if n_neg > 1 else 0.0

    var_auc_diff = s10 / n_pos + s01 / n_neg
    se = math.sqrt(max(var_auc_diff, 1e-12))

    z = (auc_b - auc_a) / se if se > 0 else 0.0

    # 双尾 p 值 (正态近似)
    p = 2.0 * (1.0 - _normal_cdf(abs(z)))
    return {"z": float(z), "p": float(p), "significant": p < 0.05}


def _normal_cdf(x: float) -> float:
    """标准正态分布 CDF (Abramowitz 近似)."""
    if x < 0:
        return 1.0 - _normal_cdf(-x)
    # 多项式近似
    b = [0.319381530, -0.356563782, 1.781477937, -1.821255978, 1.330274429]
    t = 1.0 / (1.0 + 0.2316419 * x)
    phi = 1.0 - (1.0 / math.sqrt(2 * math.pi)) * math.exp(-x * x / 2.0) * \
          (b[0] * t + b[1] * t ** 2 + b[2] * t ** 3 + b[3] * t ** 4 + b[4] * t ** 5)
    return phi


# ── 综合评估 ------------------------------------------------------------

def evaluate_layer(
    scores: np.ndarray,
    labels: np.ndarray,
    fixed_threshold: float = 0.15,
    n_bootstrap: int = 2000,
) -> Dict:
    """对某一层的分数进行全维度评估。

    Args:
        scores: 每场景的连续分数 [0, 1]
        labels: 真实标签 (1=attack, 0=benign)
        fixed_threshold: 固定阈值 (向后兼容)
        n_bootstrap: Bootstrap 重采样次数

    Returns:
        包含所有指标的字典
    """
    n_attack = int(np.sum(labels == 1))
    n_benign = int(np.sum(labels == 0))
    yt = youden_threshold(scores, labels)
    f1_opt, f1_thresh = f1_optimal(scores, labels)

    # 固定阈值二分类
    tp, fp, fn, tn = confusion_matrix(scores, labels, fixed_threshold)
    tpr_fixed = safe_div(tp, tp + fn)
    fpr_fixed = safe_div(fp, fp + tn)
    f1_fixed = safe_div(2 * tp, 2 * tp + fp + fn)
    mcc_fixed = safe_div(tp * tn - fp * fn, math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)))

    # 最优阈值二分类
    tp_opt, fp_opt, fn_opt, tn_opt = confusion_matrix(scores, labels, yt)
    f1_yt = safe_div(2 * tp_opt, 2 * tp_opt + fp_opt + fn_opt)
    mcc_yt = safe_div(
        tp_opt * tn_opt - fp_opt * fn_opt,
        math.sqrt((tp_opt + fp_opt) * (tp_opt + fn_opt) * (tn_opt + fp_opt) * (tn_opt + fn_opt))
    )

    # 阈值无关
    auroc = auc_roc(scores, labels)
    auprc_val = auc_pr(scores, labels)

    # 分布分离
    sep = score_separation(scores, labels)

    # 校准
    brier = brier_score(scores, labels)
    ece = expected_calibration_error(scores, labels)

    # Bootstrap AUROC CI
    auroc_lo, auroc_pt, auroc_hi = bootstrap_ci(
        lambda s, l: auc_roc(s, l), scores, labels, n_bootstrap
    )
    # Bootstrap Cohen's d CI
    d_lo, d_pt, d_hi = bootstrap_ci(
        lambda s, l: cohens_d(s, l), scores, labels, n_bootstrap
    )

    return {
        # 数据量
        "n_total": len(scores),
        "n_attack": n_attack,
        "n_benign": n_benign,

        # ── 主指标 (阈值无关) ──
        "auroc": round(auroc, 4),
        "auroc_95ci": [round(auroc_lo, 4), round(auroc_hi, 4)],
        "auprc": round(auprc_val, 4),
        "cohens_d": round(d_pt, 4),
        "cohens_d_95ci": [round(d_lo, 4), round(d_hi, 4)],

        # ── 阈值 --------------------------------------------------
        "youden_threshold": round(yt, 4),
        "f1_optimal_threshold": round(f1_thresh, 4),

        # ── 固定阈值 (向后兼容) ──
        "fixed_threshold": fixed_threshold,
        "fixed_TP": tp, "fixed_FP": fp, "fixed_FN": fn, "fixed_TN": tn,
        "fixed_TPR": round(tpr_fixed, 4),
        "fixed_FPR": round(fpr_fixed, 4),
        "fixed_F1": round(f1_fixed, 4),
        "fixed_MCC": round(mcc_fixed, 4),

        # ── 最优阈值 ──
        "optimal_TP": tp_opt, "optimal_FP": fp_opt, "optimal_FN": fn_opt, "optimal_TN": tn_opt,
        "optimal_TPR": round(safe_div(tp_opt, tp_opt + fn_opt), 4),
        "optimal_FPR": round(safe_div(fp_opt, fp_opt + tn_opt), 4),
        "optimal_F1": round(f1_yt, 4),
        "optimal_MCC": round(mcc_yt, 4),

        # ── 分数分布 ──
        **sep,

        # ── 校准 --------------------------------------------------
        "brier_score": round(brier, 4),
        "ece": round(ece, 4),

        # ── 分数分布统计 ──
        "scores_mean": round(float(np.mean(scores)), 4),
        "scores_std": round(float(np.std(scores, ddof=1)), 4),
        "attack_scores_mean": round(sep["attack_mean"], 4),
        "benign_scores_mean": round(sep["benign_mean"], 4),
    }


def compare_layers(
    prev_scores: np.ndarray,
    curr_scores: np.ndarray,
    labels: np.ndarray,
    layer_name: str = "",
) -> Dict:
    """比较相邻两层的贡献。

    核心: ΔAUROC + DeLong p-value + ΔCohen's d + ΔBrier。
    """
    auroc_prev = auc_roc(prev_scores, labels)
    auroc_curr = auc_roc(curr_scores, labels)
    d_prev = cohens_d(prev_scores, labels)
    d_curr = cohens_d(curr_scores, labels)
    brier_prev = brier_score(prev_scores, labels)
    brier_curr = brier_score(curr_scores, labels)
    ece_prev = expected_calibration_error(prev_scores, labels)
    ece_curr = expected_calibration_error(curr_scores, labels)

    # DeLong
    delong = delong_test(prev_scores, curr_scores, labels)

    # Youden 阈值变化
    yt_prev = youden_threshold(prev_scores, labels)
    yt_curr = youden_threshold(curr_scores, labels)

    return {
        "layer": layer_name,
        "delta_auroc": round(auroc_curr - auroc_prev, 4),
        "auroc_prev": round(auroc_prev, 4),
        "auroc_curr": round(auroc_curr, 4),
        "delong_z": delong["z"],
        "delong_p": delong["p"],
        "delong_significant": delong["significant"],
        "delta_cohens_d": round(d_curr - d_prev, 4),
        "cohens_d_prev": round(d_prev, 4),
        "cohens_d_curr": round(d_curr, 4),
        "delta_brier": round(brier_curr - brier_prev, 4),
        "brier_prev": round(brier_prev, 4),
        "brier_curr": round(brier_curr, 4),
        "delta_ece": round(ece_curr - ece_prev, 4),
        "ece_prev": round(ece_prev, 4),
        "ece_curr": round(ece_curr, 4),
        "youden_threshold_prev": round(yt_prev, 4),
        "youden_threshold_curr": round(yt_curr, 4),
    }


# ── 综合消融报告 ---------------------------------------------------------

def ablation_report(
    layer_scores: List[Tuple[str, np.ndarray]],
    labels: np.ndarray,
    fixed_threshold: float = 0.15,
) -> Dict:
    """生成完整的消融实验报告。

    Args:
        layer_scores: [(layer_name, scores_array), ...]
        labels: 真实标签
        fixed_threshold: 固定阈值

    Returns:
        完整的嵌套报告 dict
    """
    layers = []
    comparisons = []

    prev_scores = None
    prev_name = ""

    for name, scores in layer_scores:
        layer_metrics = evaluate_layer(scores, labels, fixed_threshold)
        layer_metrics["layer"] = name
        layers.append(layer_metrics)

        if prev_scores is not None:
            comp = compare_layers(prev_scores, scores, labels, name)
            comp["from_layer"] = prev_name
            comp["to_layer"] = name
            comparisons.append(comp)

        prev_scores = scores
        prev_name = name

    # 汇总 delta 表
    delta_summary = []
    for c in comparisons:
        delta_summary.append({
            "layer": c["to_layer"],
            "ΔAUROC": c["delta_auroc"],
            "ΔCohen's d": c["delta_cohens_d"],
            "ΔBrier": c["delta_brier"],
            "ΔECE": c["delta_ece"],
            "DeLong p": c["delong_p"],
            "Significant": c["delong_significant"],
        })

    return {
        "schema": "mtagentlens.ablation_metrics.v1",
        "n_total": len(labels),
        "n_attack": int(np.sum(labels == 1)),
        "n_benign": int(np.sum(labels == 0)),
        "fixed_threshold": fixed_threshold,
        "layer_metrics": layers,
        "layer_comparisons": comparisons,
        "delta_summary": delta_summary,
    }
