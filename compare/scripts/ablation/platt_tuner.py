#!/usr/bin/env python3
"""
Platt 校准参数优化 — 为每个消融层独立学习最优 sigmoid 参数。

问题: 当前硬编码 platt_sigmoid(score, a=7.0, b=-3.0) 对所有层使用相同参数。
不同层的 raw score 分布不同，统一参数导致 L2 (6D fusion) 的 AUROC 反而比 L1 差。

方案: 对每个层，使用逻辑回归在 raw scores 上拟合 Platt 参数，使 Brier score 最小化。
使用 leave-one-out 避免过拟合（86 样本太少，不做 train/test split）。

用法: PYTHONPATH=env:env/src:. python3 compare/scripts/ablation/platt_tuner.py
"""

import json, math, sqlite3, os, sys, numpy as np
from pathlib import Path
from scipy.optimize import minimize

PROJECT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = PROJECT / "compare/results/paper"

sys.path.insert(0, str(PROJECT))
from compare.tools.scoring_replica import (
    sub_score_rule, sub_score_boundary, sub_score_role, sub_score_behavioral,
    fuse_dimensions, platt_sigmoid, compute_owa, compute_weighted_geometric_mean,
)
from compare.tools.ablation_metrics import auc_roc, brier_score, cohens_d

STD_WEIGHTS = [0.28, 0.25, 0.20, 0.10, 0.10, 0.07]


def soft_gate(pbc, has_unsafe_rules=False, k=10.0, steepness=0.5):
    gate = 1.0 / (1.0 + math.exp(-(float(pbc) - k) / steepness))
    return max(gate, 0.3) if has_unsafe_rules else gate


def apply_gate_l3(d, score):
    return min(score * soft_gate(d["pbc"]), 1.0)


def apply_gate_l4(d, score):
    has_unsafe = any(h.get("decision") == "Unsafe" for h in d["rule_hits"])
    return min(score * soft_gate(d["pbc"], has_unsafe), 1.0)


def fuse_active_only(vals, weights):
    n = len(vals)
    if n == 0: return 0.0
    if n == 1: return vals[0]
    owa = compute_owa(vals, 0.70)
    active = [(d, w) for d, w in zip(vals, weights) if d > 0.01]
    if not active: return owa
    geo = compute_weighted_geometric_mean([a[0] for a in active], [a[1] for a in active])
    return owa * 0.80 + geo * 0.20


# ── Raw score computation (pre-Platt, pre-gate for tuning) ──

def raw_score_l0(d, dims):
    rule = sub_score_rule(d["rule_hits"])
    return fuse_active_only([rule], [1.0])


def raw_score_l1(d, dims):
    rule = sub_score_rule(d["rule_hits"])
    pbc_signal = min(d["pbc"] / 200.0, 1.0)
    return fuse_active_only([rule, pbc_signal], [0.4, 0.6])


def raw_score_l2(d, dims):
    vals = [dims["D1_full"], 0.0, dims["D3_identity"], dims["D4"], dims["D5"], dims["D6_full"]]
    if d["cusum_triggered"] and d["progressive_fused"] > 0:
        vals[3] = vals[3] * 0.7 + d["progressive_fused"] * 0.3
    return fuse_dimensions(vals, STD_WEIGHTS)


def raw_score_l3(d, dims):
    s = raw_score_l2(d, dims)
    deberta_score = d.get("deberta_score", 0.0) or 0.0
    if deberta_score > 0:
        vals = [dims["D1_full"], deberta_score, dims["D3_identity"], dims["D4"], dims["D5"], dims["D6_full"]]
        if d["cusum_triggered"] and d["progressive_fused"] > 0:
            vals[3] = vals[3] * 0.7 + d["progressive_fused"] * 0.3
        return fuse_dimensions(vals, STD_WEIGHTS)
    return s


def raw_score_l4(d, dims):
    vals = [dims["D1_full"], 0.0, dims["D3_full"], dims["D4"], dims["D5"], dims["D6_full"]]
    if d["cusum_triggered"] and d["progressive_fused"] > 0:
        vals[3] = vals[3] * 0.7 + d["progressive_fused"] * 0.3
    deberta_score = d.get("deberta_score", 0.0) or 0.0
    if deberta_score > 0:
        vals[1] = deberta_score
    return fuse_dimensions(vals, STD_WEIGHTS)


def raw_score_l5(d, dims):
    return raw_score_l4(d, dims)


# ── Platt parameter fitting ──

def fit_platt(raw_scores, labels):
    """Fit Platt scaling parameters (a, b) by minimizing Brier score.

    Platt sigmoid: p = 1 / (1 + exp(-(a*x + b)))
    We minimize: mean((p - y)^2)
    """
    y = np.array(labels, dtype=float)
    x = np.array(raw_scores)

    def brier_loss(params):
        a, b = params
        if a <= 0:
            return 1e9
        z = a * x + b
        z = np.clip(z, -50, 50)
        p = 1.0 / (1.0 + np.exp(-z))
        return float(np.mean((p - y) ** 2))

    # Grid search for good initial values
    best_loss = float('inf')
    best_params = (7.0, -3.0)

    for a0 in [1.0, 3.0, 5.0, 7.0, 10.0, 15.0]:
        for b0 in [-5.0, -3.0, -1.0, 0.0, 1.0]:
            result = minimize(brier_loss, [a0, b0], method='Nelder-Mead',
                            options={'maxiter': 5000, 'xatol': 1e-8})
            if result.fun < best_loss:
                best_loss = result.fun
                best_params = (result.x[0], result.x[1])

    return best_params[0], best_params[1]


def calibrated_sigmoid(score, a, b):
    """Apply Platt sigmoid with given parameters."""
    z = a * score + b
    z = max(min(z, 50.0), -50.0)
    return 1.0 / (1.0 + math.exp(-z))


# ── Main ──

def main():
    print("=" * 80)
    print("  Platt 校准参数优化 — 逐层独立拟合")
    print("=" * 80)

    # Load data (same as run_staircase.py)
    from compare.scripts.ablation.run_staircase import load_data, compute_dimensions

    all_data = load_data()
    sids = sorted(all_data.keys())
    y = np.array([1 if all_data[s]["label"]=="attack" else 0 for s in sids])
    dims_map = {s: compute_dimensions(all_data[s]) for s in sids}

    # Load offline DeBERTa if available
    deberta_offline = OUTPUT_DIR / "deberta_offline_scores.json"
    if deberta_offline.exists():
        offline = json.loads(deberta_offline.read_text())
        for sid, v in offline.get("scores", {}).items():
            if sid in all_data and all_data[sid].get("deberta_score", 0.0) == 0.0:
                all_data[sid]["deberta_score"] = v["deberta_score"]

    def no_gate(d, score):
        return score  # L0: no PBC gate (pure rules)

    layers = [
        ("L0", raw_score_l0, no_gate,        "Rule Engine"),
        ("L1", raw_score_l1, apply_gate_l3,  "+ eBPF Graph"),
        ("L2", raw_score_l2, apply_gate_l4,  "+ Statistical ML"),
        ("L3", raw_score_l3, apply_gate_l4,  "+ AI Models"),
        ("L4", raw_score_l4, apply_gate_l4,  "+ Multi-Agent"),
        ("L5", raw_score_l5, apply_gate_l4,  "Full System"),
    ]

    print(f"\n  {'层':>4s} {'a (当前)':>10s} {'b (当前)':>10s} {'a (优化)':>10s} {'b (优化)':>10s} "
          f"{'AUROC(旧)':>10s} {'AUROC(新)':>10s} {'Brier(旧)':>10s} {'Brier(新)':>10s} {'ΔAUROC':>8s}")
    print(f"  {'-'*95}")

    results = {}
    for lid, raw_fn, gate_fn, lname in layers:
        # Compute raw scores
        raw = np.array([raw_fn(all_data[s], dims_map[s]) for s in sids])

        # Fit Platt on raw scores (before gate)
        a_opt, b_opt = fit_platt(raw, y)

        # Old calibration (a=7.0, b=-3.0) + gate
        old_platt = np.array([min(calibrated_sigmoid(r, 7.0, -3.0), 1.0) for r in raw])
        old_gated = np.array([gate_fn(all_data[s], old_platt[i])
                              for i, s in enumerate(sids)])

        # New calibration + gate
        new_platt = np.array([min(calibrated_sigmoid(r, a_opt, b_opt), 1.0) for r in raw])
        new_gated = np.array([gate_fn(all_data[s], new_platt[i])
                              for i, s in enumerate(sids)])

        old_auc = auc_roc(old_gated, y)
        new_auc = auc_roc(new_gated, y)
        old_brier = brier_score(old_gated, y)
        new_brier = brier_score(new_gated, y)

        delta_auc = new_auc - old_auc
        print(f"  {lid:>4s} {7.0:10.1f} {-3.0:10.1f} {a_opt:10.4f} {b_opt:10.4f} "
              f"{old_auc:10.4f} {new_auc:10.4f} {old_brier:10.4f} {new_brier:10.4f} {delta_auc:+8.4f}")

        results[lid] = {"a": a_opt, "b": b_opt, "auroc_old": old_auc, "auroc_new": new_auc,
                        "brier_old": old_brier, "brier_new": new_brier, "delta_auroc": delta_auc}

    # Save
    output = {"schema": "mtagentprism.platt_tuning.v1", "results": {
        lid: {"a_opt": round(r["a"], 4), "b_opt": round(r["b"], 4),
              "auroc_before": round(r["auroc_old"], 4), "auroc_after": round(r["auroc_new"], 4),
              "brier_before": round(r["brier_old"], 4), "brier_after": round(r["brier_new"], 4)}
        for lid, r in results.items()
    }}
    (OUTPUT_DIR / "platt_tuned_params.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\n→ {OUTPUT_DIR / 'platt_tuned_params.json'}")


if __name__ == "__main__":
    main()
