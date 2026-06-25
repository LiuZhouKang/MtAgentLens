#!/usr/bin/env python3
"""
MtAgentLens Ablation Study v16 — Capability Staircase.

Evaluates the marginal contribution of each detection component by
building up the system layer by layer:

  L0: Rules + CUSUM           (no eBPF, gate disabled)
  L1: + PBC Detection         (eBPF boundary crossing + gate)
  L2: + Evidence + Identity   (evidence correlation + identity resolution)
  L3: + Observer / Full System (LLM semantic arbitration)

Each layer transition measures the value of ONE component group.
DeBERTa is excluded — its training distribution shift (short chat vs.
long task descriptions) yields no measurable AUROC contribution on this
benchmark, making it unsuitable for staircase evaluation.

Usage: PYTHONPATH=env:env/src:. python3 compare/scripts/ablation/run_staircase.py
"""

import csv, json, math, os, sqlite3, sys
from pathlib import Path
import numpy as np

PROJECT = Path(__file__).resolve().parents[3]
TRACES = PROJECT / "compare/Dataset/FuxiBench/traces"
MANIFEST = PROJECT / "compare/Dataset/FuxiBench/prompt_inputs/manifest.json"
OUTPUT_DIR = PROJECT / "compare/results/paper"
DEBERTA_OFFLINE = OUTPUT_DIR / "deberta_offline_scores.json"
CSV_DIR = OUTPUT_DIR / "tables"

sys.path.insert(0, str(PROJECT))
from compare.tools.scoring_replica import compute_score
from compare.tools.ablation_metrics import (
    evaluate_layer, ablation_report, auc_roc, cohens_d,
)
from compare.tools.latex_tables import generate_all_pdfs

THRESHOLD = 0.15


# ══════════════════════════════════════════════════════════════════════════════
# Data loading — reads every scenario's responsibility.db into a flat dict
# ══════════════════════════════════════════════════════════════════════════════

def find_db(sid):
    """Locate the responsibility.db for a given scenario ID."""
    for root, dirs, files in os.walk(TRACES):
        if "responsibility.db" in files and os.path.basename(root) == sid:
            return Path(root) / "responsibility.db"
    return None


def load_data():
    """Load all 86 scenarios from the frozen dataset into memory."""
    manifest = json.loads(MANIFEST.read_text())
    gt_map = {}
    for e in manifest["entries"]:
        at = e.get("attack_type", "benign")
        gt_map[e["scenario_id"]] = {
            "label": "attack" if at != "benign" else "benign",
            "attack_type": at,
        }

    all_data = {}
    for sid in sorted(gt_map.keys()):
        db_path = find_db(sid)
        if not db_path:
            continue
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row

        d = {}

        # ── Event counts ──
        d["process_count"] = db.execute("SELECT COUNT(*) FROM process_events").fetchone()[0]
        d["tls_count"] = db.execute("SELECT COUNT(*) FROM tls_sessions").fetchone()[0]
        d["action_count"] = db.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
        d["intent_count"] = db.execute("SELECT COUNT(*) FROM intents").fetchone()[0]

        # ── Graph structure ──
        d["pbc"] = db.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE edge_type='PolicyBoundaryCrossing'"
        ).fetchone()[0]
        anomaly_types = [
            "PolicyBoundaryCrossing", "TaintPropagatesTo",
            "AgentDelegatesTo", "ContextFlowsFrom", "CompetingExplanation",
        ]
        d["anomaly_edges"] = sum(
            db.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE edge_type=?", (t,)
            ).fetchone()[0]
            for t in anomaly_types
        )
        d["total_edges"] = max(db.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0], 1)
        d["total_nodes"] = db.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]

        d["evidence"] = [dict(r) for r in db.execute("SELECT * FROM correlation_evidence")]
        d["agent_count"] = max(
            db.execute("SELECT COUNT(*) FROM agent_identities").fetchone()[0], 1
        )

        # ── Action diversity ──
        atypes = [r[0] for r in db.execute("SELECT action_type FROM actions").fetchall()]
        d["action_diversity"] = min(len(set(atypes)) / 8.0, 1.0)

        # ── Alert parsing ──
        d["rule_hits"] = []
        d["rust_score"] = None
        d["rust_route"] = ""
        d["observer_route"] = ""
        d["observer_display_profile"] = ""
        d["observer_decision"] = ""
        d["observer_rust_score"] = None
        d["cusum_triggered"] = False
        d["progressive_fused"] = 0.0
        d["deberta_score"] = 0.0

        for row in db.execute(
            "SELECT display_profile, decision, engine_section_json FROM responsibility_alerts"
        ):
            display_profile = row[0] or ""
            alert_decision = row[1] or ""
            ej = row[2]
            if not ej:
                continue
            try:
                ejd = json.loads(ej)
                # Rule verdicts
                for rv in ejd.get("rule_verdicts", []):
                    d["rule_hits"].append({
                        "rule_id": rv.get("rule_id", ""),
                        "decision": (
                            "Unsafe"
                            if "unsafe" in rv.get("rule_id", "").lower()
                            else "Safe"
                        ),
                    })
                # Last non-observer alert provides the engine baseline
                route = ejd.get("classification_route", "")
                if "observer" not in route.lower():
                    d["rust_score"] = ejd.get("classification_score")
                    d["rust_route"] = route
                d["cusum_triggered"] = d["cusum_triggered"] or ejd.get("cusum_triggered", False)
                d["progressive_fused"] = max(
                    d["progressive_fused"], ejd.get("progressive_fused", 0.0)
                )
                # First observer alert captured with paired score
                if "observer" in route.lower() and not d["observer_route"]:
                    d["observer_route"] = route
                    d["observer_display_profile"] = display_profile
                    d["observer_decision"] = alert_decision
                    d["observer_rust_score"] = ejd.get("classification_score")
                if ejd.get("deberta_score", 0.0) > 0:
                    d["deberta_score"] = max(d["deberta_score"], ejd.get("deberta_score", 0.0))
            except Exception:
                pass

        # ── Taint & intent ──
        d["tainted"] = db.execute(
            "SELECT COUNT(*) FROM responsibility_claims WHERE claim_type='TaintedContextUse'"
        ).fetchone()[0]
        d["pollution"] = db.execute(
            "SELECT COUNT(*) FROM context_pollution_events"
        ).fetchone()[0]
        ti = max(db.execute("SELECT COUNT(*) FROM intents").fetchone()[0], 1)
        se = db.execute(
            "SELECT COUNT(*) FROM intents WHERE intent_class='side_effect'"
        ).fetchone()[0]
        d["intent_ratio"] = se / ti

        db.close()
        d["label"] = gt_map[sid]["label"]
        d["attack_type"] = gt_map[sid]["attack_type"]
        all_data[sid] = d

    return all_data


# ══════════════════════════════════════════════════════════════════════════════
# Layer scoring functions
# ══════════════════════════════════════════════════════════════════════════════
#
# Each layer adds one capability group.  L0 is the minimal system
# (rule engine + temporal CUSUM), L3 is the full MtAgentLens pipeline.
# The gate always uses the real PBC count — it is part of the kernel-level
# safety net, not a separable component.


def _compute_rep(d):
    """Temporal repetition score input — includes CUSUM progressive fusion."""
    rep = d.get("action_count", 0)
    if d["cusum_triggered"] and d["progressive_fused"] > 0:
        rep = int(rep + d["progressive_fused"] * 50)
    return rep


def _intent_boost(d, s):
    """Side-effect intent amplification — fires when >30% of intents are side-effects."""
    if d.get("intent_ratio", 0) > 0.3:
        return min(s * (1.0 + d["intent_ratio"] * 0.1), 1.0)
    return s


def _apply_observer(d, s):
    """Replicate the Observer LLM's directional score adjustment.

    Uses the stored observer route and decision from the production run.
    Unsafe → pushes score upward; Safe → pulls score downward.
    """
    rs = d.get("observer_rust_score") or d.get("rust_score")
    if rs is None:
        return s

    rr = d.get("observer_route", "") or d.get("rust_route", "") or ""
    if "observer" not in rr.lower():
        return s
    if "failed" in rr.lower() or "uncertain" in rr.lower():
        return s

    # Direction from route string
    if "unsafe" in rr.lower():
        obs_conf = rs if rs > 0 else 0.5
        return max(min(s + obs_conf * 0.3, 0.99), 0.15)
    if "safe" in rr.lower():
        conf = rs if rs < s else (1.0 - rs)
        return s * (1.0 - conf * 0.7)

    # Fallback: display_profile + decision
    dp = d.get("observer_display_profile", "")
    dec = d.get("observer_decision", "")
    if "ObserverJudged" in dp:
        if "Unsafe" in dec:
            return max(min(s + (rs if rs > 0 else 0.5) * 0.3, 0.99), 0.15)
        elif "Safe" in dec:
            return s * (1.0 - ((rs if rs < s else 1.0 - rs)) * 0.7)

    return s


def score_l0(d):
    """L0: Rules + CUSUM only.  No eBPF signal, gate disabled (≈1.0).

    This represents a pure text/heuristic baseline without kernel visibility.
    """
    import sys as _sys
    rep = _compute_rep(d)
    return compute_score(
        rule_hits=d["rule_hits"],
        patterns=[],
        pbc_count=0,
        total_edges=d["total_edges"],
        evidence_records=[],
        agent_count=1,
        repetition_count=rep,
        anomaly_edge_count=0,
        confirmed_pbc=0,
        gate_pbc=_sys.float_info.max,  # gate → 1.0 (pass-through)
    )


def score_l1(d):
    """L1: + PBC Detection.  eBPF boundary-crossing edges enter the fusion pipeline.

    The PBC gate becomes active with real kernel data.
    """
    rep = _compute_rep(d)
    pbc = d["pbc"]
    return compute_score(
        rule_hits=d["rule_hits"],
        patterns=[],
        pbc_count=pbc,
        total_edges=d["total_edges"],
        evidence_records=[],
        agent_count=1,
        repetition_count=rep,
        anomaly_edge_count=d["anomaly_edges"],
        confirmed_pbc=pbc,
        gate_pbc=pbc,
    )


def score_l2(d):
    """L2: + Evidence + Identity.  Cross-context evidence correlation and
    agent identity resolution are added to the scoring pipeline."""
    rep = _compute_rep(d)
    pbc = d["pbc"]
    s = compute_score(
        rule_hits=d["rule_hits"],
        patterns=[],
        pbc_count=pbc,
        total_edges=d["total_edges"],
        evidence_records=d["evidence"],
        agent_count=d["agent_count"],
        repetition_count=rep,
        anomaly_edge_count=d["anomaly_edges"],
        confirmed_pbc=pbc,
        gate_pbc=pbc,
    )
    return _intent_boost(d, s)


def score_l3(d):
    """L3: + Observer ≡ Full System.  LLM semantic arbitration for grey-zone cases.

    Identical to the production MtAgentLens pipeline."""
    rep = _compute_rep(d)
    pbc = d["pbc"]
    s = compute_score(
        rule_hits=d["rule_hits"],
        patterns=[],
        pbc_count=pbc,
        total_edges=d["total_edges"],
        evidence_records=d["evidence"],
        agent_count=d["agent_count"],
        repetition_count=rep,
        anomaly_edge_count=d["anomaly_edges"],
        confirmed_pbc=pbc,
        gate_pbc=pbc,
    )
    s = _apply_observer(d, s)
    return _intent_boost(d, s)


# ══════════════════════════════════════════════════════════════════════════════
# CSV export
# ══════════════════════════════════════════════════════════════════════════════

def export_csv(name, headers, rows):
    """Write a table as CSV to compare/results/paper/tables/<name>.csv."""
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    path = CSV_DIR / f"{name}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for row in rows:
            w.writerow(row)
    return path


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("═" * 100)
    print("  MtAgentLens Ablation Study v16 — Capability Staircase (4-layer)")
    print("═" * 100)

    print("\nLoading data ...")
    all_data = load_data()
    sids = sorted(all_data.keys())
    y = np.array([1 if all_data[s]["label"] == "attack" else 0 for s in sids])
    meta = [{"attack_type": all_data[s]["attack_type"]} for s in sids]

    n_att = int(np.sum(y))
    n_ben = len(y) - n_att
    print(f"  {len(sids)} scenarios ({n_att} attack, {n_ben} benign)")

    # ── Offline DeBERTa scores (supplementary, not used in scoring) ──
    if DEBERTA_OFFLINE.exists():
        offline = json.loads(DEBERTA_OFFLINE.read_text())
        for sid, v in offline.get("scores", {}).items():
            if sid in all_data and isinstance(v, dict) and "deberta_score" in v:
                if all_data[sid].get("deberta_score", 0.0) == 0.0:
                    all_data[sid]["deberta_score"] = v["deberta_score"]

    # ── Observer coverage ──
    obs_count = sum(
        1 for s in sids
        if "observer" in (all_data[s].get("observer_route", "").lower())
    )
    print(f"  Observer triggered: {obs_count}/{len(sids)} scenarios")

    # ── Layer definitions ──
    layers = [
        ("L0", "Rules + CUSUM",            score_l0),
        ("L1", "+ PBC Detection",          score_l1),
        ("L2", "+ Evidence + Identity",    score_l2),
        ("L3", "+ Observer (Full System)", score_l3),
    ]

    layer_scores_list = []
    for lid, lname, lfn in layers:
        scores = np.array([lfn(all_data[s]) for s in sids])
        layer_scores_list.append((lid, scores))

    report = ablation_report(layer_scores_list, y, fixed_threshold=THRESHOLD)

    # ═══════════════════════════════════════════════════════════════════
    # Table 1 — Primary metrics (threshold-free)
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*100}")
    print("  Table 1: Primary Metrics — AUROC, Cohen's d, Calibration")
    print(f"{'─'*100}")
    hdr = f"  {'Layer':>4s} {'Capability':28s} {'AUROC':>7s} {'95% CI':>16s} {'AUPRC':>7s} {'Cohen d':>9s} {'Brier':>8s} {'ECE':>7s}"
    print(hdr)
    print(f"  {'─'*94}")

    csv1 = []
    for lm in report["layer_metrics"]:
        ci = f"[{lm['auroc_95ci'][0]:.4f}, {lm['auroc_95ci'][1]:.4f}]"
        idx = [l[0] for l in layers].index(lm["layer"])
        name = layers[idx][1]
        print(f"  {lm['layer']:>4s} {name:28s} "
              f"{lm['auroc']:7.4f} {ci:>16s} {lm['auprc']:7.4f} {lm['cohens_d']:9.4f} "
              f"{lm['brier_score']:8.4f} {lm['ece']:7.4f}")
        csv1.append([lm["layer"], name, lm["auroc"], lm["auroc_95ci"][0], lm["auroc_95ci"][1],
                      lm["auprc"], lm["cohens_d"], lm["brier_score"], lm["ece"],
                      lm["youden_threshold"]])

    export_csv("table1_primary_metrics",
               ["Layer", "Capability", "AUROC", "AUROC_CI_low", "AUROC_CI_high",
                "AUPRC", "Cohen_d", "Brier", "ECE", "Youden_threshold"], csv1)

    # ═══════════════════════════════════════════════════════════════════
    # Table 2 — Marginal contributions
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*100}")
    print("  Table 2: Marginal Contributions — ΔAUROC + DeLong + ΔCohen's d")
    print(f"{'─'*100}")
    dh = f"  {'Transition':>10s} {'ΔAUROC':>8s} {'DeLong z':>9s} {'p-value':>9s} {'Sig':>5s} {'ΔCohen d':>10s} {'ΔBrier':>8s}  {'Component Added':>24s}"
    print(dh)
    print(f"  {'─'*96}")

    csv2 = []
    layer_names = {l[0]: l[1] for l in layers}
    for c in report["layer_comparisons"]:
        sig = "✓" if c["delong_significant"] else "—"
        interp = layer_names.get(c["to_layer"], c["to_layer"])
        print(f"  {c['from_layer']+'→'+c['to_layer']:>10s} {c['delta_auroc']:+8.4f} {c['delong_z']:9.3f} "
              f"{c['delong_p']:9.4f} {sig:>5s} {c['delta_cohens_d']:+10.4f} {c['delta_brier']:+8.4f}  {interp:>24s}")
        csv2.append([c["from_layer"] + "→" + c["to_layer"], c["delta_auroc"],
                      c["delong_z"], c["delong_p"], sig,
                      c["delta_cohens_d"], c["delta_brier"], interp])

    export_csv("table2_layer_contributions",
               ["Transition", "ΔAUROC", "DeLong_z", "p_value", "Significant",
                "ΔCohen_d", "ΔBrier", "Component"], csv2)

    # ═══════════════════════════════════════════════════════════════════
    # Table 3 — Fixed-threshold binary classification
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*100}")
    print("  Table 3: Binary Classification at Fixed Threshold (0.15)")
    print(f"{'─'*100}")
    ch = f"  {'Layer':>4s} {'Capability':28s} {'TP':>4s} {'FP':>4s} {'FN':>4s} {'TN':>4s} {'TPR':>7s} {'FPR':>7s} {'F1':>7s} {'MCC':>7s}"
    print(ch)
    print(f"  {'─'*90}")

    csv3 = []
    for lm, (lid, lname, lfn) in zip(report["layer_metrics"], layers):
        print(f"  {lid:>4s} {lname:28s} {lm['fixed_TP']:>4d} {lm['fixed_FP']:>4d} {lm['fixed_FN']:>4d} {lm['fixed_TN']:>4d} "
              f"{lm['fixed_TPR']:7.4f} {lm['fixed_FPR']:7.4f} {lm['fixed_F1']:7.4f} {lm['fixed_MCC']:7.4f}")
        csv3.append([lid, lname, lm["fixed_TP"], lm["fixed_FP"], lm["fixed_FN"], lm["fixed_TN"],
                      lm["fixed_TPR"], lm["fixed_FPR"], lm["fixed_F1"], lm["fixed_MCC"]])

    export_csv("table3_binary_classification",
               ["Layer", "Capability", "TP", "FP", "FN", "TN", "TPR", "FPR", "F1", "MCC"], csv3)

    # ═══════════════════════════════════════════════════════════════════
    # Table 4 — Per-type AUROC
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*100}")
    print("  Table 4: Per-Attack-Type AUROC")
    print(f"{'─'*100}")
    print(f"  {'Layer':>4s}  {'Type A (n=10)':>14s}  {'Type B (n=34)':>14s}  {'Type C (n=23)':>14s}  {'Overall':>10s}")

    csv4 = []
    for lid, scores in layer_scores_list:
        parts = []
        for at in ['A', 'B', 'C']:
            idx = [i for i, m in enumerate(meta) if m["attack_type"] == at] + \
                  [i for i, m in enumerate(meta) if m["attack_type"] == "benign"]
            sub_s = scores[idx]; sub_l = y[idx]
            val = auc_roc(sub_s, sub_l) if np.sum(sub_l == 1) > 0 and np.sum(sub_l == 0) > 0 else float("nan")
            parts.append(val)
        overall = auc_roc(scores, y)
        print(f"  {lid:>4s}  {parts[0]:14.4f}  {parts[1]:14.4f}  {parts[2]:14.4f}  {overall:10.4f}")
        csv4.append([lid, parts[0], parts[1], parts[2], overall])

    export_csv("table4_per_type_auroc",
               ["Layer", "Type_A_AUROC", "Type_B_AUROC", "Type_C_AUROC", "Overall_AUROC"], csv4)

    # ═══════════════════════════════════════════════════════════════════
    # Table 5 — Score distribution
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*100}")
    print("  Table 5: Score Distribution (Percentiles)")
    print(f"{'─'*100}")
    print(f"  {'Layer':>4s}  {'Att P25':>8s} {'Att P50':>8s} {'Att P75':>8s} {'Ben P25':>8s} {'Ben P50':>8s} {'Ben P75':>8s}")

    csv5 = []
    for lid, scores in layer_scores_list:
        att = scores[y == 1]; ben = scores[y == 0]
        row = [lid, np.percentile(att, 25), np.percentile(att, 50), np.percentile(att, 75),
               np.percentile(ben, 25), np.percentile(ben, 50), np.percentile(ben, 75)]
        print(f"  {lid:>4s}  {row[1]:8.4f} {row[2]:8.4f} {row[3]:8.4f} {row[4]:8.4f} {row[5]:8.4f} {row[6]:8.4f}")
        csv5.append(row)

    export_csv("table5_score_distribution",
               ["Layer", "Attack_P25", "Attack_P50", "Attack_P75",
                "Benign_P25", "Benign_P50", "Benign_P75"], csv5)

    # ═══════════════════════════════════════════════════════════════════
    # Table 6 — Optimal threshold (Youden's J) metrics
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*100}")
    print("  Table 6: Optimal Threshold (Youden's J) Classification")
    print(f"{'─'*100}")
    oh = f"  {'Layer':>4s} {'Y-Thresh':>9s} {'TP':>4s} {'FP':>4s} {'FN':>4s} {'TN':>4s} {'TPR':>7s} {'FPR':>7s} {'F1':>7s} {'MCC':>7s}"
    print(oh)
    print(f"  {'─'*88}")
    csv6 = []
    for lm in report["layer_metrics"]:
        print(f"  {lm['layer']:>4s} {lm['youden_threshold']:9.4f} {lm['optimal_TP']:>4d} {lm['optimal_FP']:>4d} "
              f"{lm['optimal_FN']:>4d} {lm['optimal_TN']:>4d} {lm['optimal_TPR']:7.4f} {lm['optimal_FPR']:7.4f} "
              f"{lm['optimal_F1']:7.4f} {lm['optimal_MCC']:7.4f}")
        csv6.append([lm["layer"], lm["youden_threshold"], lm["optimal_TP"], lm["optimal_FP"],
                      lm["optimal_FN"], lm["optimal_TN"], lm["optimal_TPR"], lm["optimal_FPR"],
                      lm["optimal_F1"], lm["optimal_MCC"]])
    export_csv("table6_optimal_threshold",
               ["Layer", "Youden_Threshold", "TP", "FP", "FN", "TN", "TPR", "FPR", "F1", "MCC"], csv6)

    # ═══════════════════════════════════════════════════════════════════
    # Table 7 — Per-type Cohen's d
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*100}")
    print("  Table 7: Per-Attack-Type Cohen's d")
    print(f"{'─'*100}")
    print(f"  {'Layer':>4s}  {'Type A':>10s}  {'Type B':>10s}  {'Type C':>10s}  {'Overall':>10s}")
    csv7 = []
    for lid, scores in layer_scores_list:
        parts = []
        for at in ['A', 'B', 'C']:
            idx = [i for i, m in enumerate(meta) if m["attack_type"] == at] + \
                  [i for i, m in enumerate(meta) if m["attack_type"] == "benign"]
            sub_s = scores[idx]; sub_l = y[idx]
            d_val = cohens_d(sub_s, sub_l) if np.sum(sub_l == 1) > 0 and np.sum(sub_l == 0) > 0 else 0.0
            parts.append(d_val)
        overall = cohens_d(scores, y)
        print(f"  {lid:>4s}  {parts[0]:10.4f}  {parts[1]:10.4f}  {parts[2]:10.4f}  {overall:10.4f}")
        csv7.append([lid, parts[0], parts[1], parts[2], overall])
    export_csv("table7_per_type_cohens_d",
               ["Layer", "Type_A_Cohen_d", "Type_B_Cohen_d", "Type_C_Cohen_d", "Overall_Cohen_d"], csv7)

    # ═══════════════════════════════════════════════════════════════════
    # Table 8 — Score distribution detail (mean, std, separation)
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*100}")
    print("  Table 8: Score Distribution Detail — Mean, Std, Separation")
    print(f"{'─'*100}")
    sh = f"  {'Layer':>4s} {'Att μ':>8s} {'Att σ':>7s} {'Ben μ':>8s} {'Ben σ':>7s} {'μ Diff':>8s} {'Cohens d':>9s}"
    print(sh)
    print(f"  {'─'*80}")
    csv8 = []
    for lid, scores in layer_scores_list:
        att = scores[y == 1]; ben = scores[y == 0]
        att_mu = np.mean(att); att_sig = np.std(att, ddof=1)
        ben_mu = np.mean(ben); ben_sig = np.std(ben, ddof=1)
        mu_diff = att_mu - ben_mu
        d_val = cohens_d(scores, y)
        print(f"  {lid:>4s} {att_mu:8.4f} {att_sig:7.4f} {ben_mu:8.4f} {ben_sig:7.4f} {mu_diff:8.4f} {d_val:9.4f}")
        csv8.append([lid, att_mu, att_sig, ben_mu, ben_sig, mu_diff, d_val])
    export_csv("table8_score_detail",
               ["Layer", "Attack_Mean", "Attack_Std", "Benign_Mean", "Benign_Std",
                "Mean_Difference", "Cohen_d"], csv8)

    # ═══════════════════════════════════════════════════════════════════
    # Consistency check — L3 must match main evaluation
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'─'*100}")
    print("  L3 ≡ Main Evaluation Consistency Check")
    main_eval_path = OUTPUT_DIR / "main_evaluation.json"
    if main_eval_path.exists():
        main_eval = json.loads(main_eval_path.read_text())
        main_m = main_eval.get("full_metrics", main_eval.get("legacy_metrics", {}))
        l3_m = report["layer_metrics"][-1]
        ok = (abs(l3_m["fixed_TPR"] - main_m.get("TPR", 0)) < 0.01 and
              abs(l3_m["fixed_FPR"] - main_m.get("FPR", 0)) < 0.01)
        print(f"  L3: TPR={l3_m['fixed_TPR']:.4f}  FPR={l3_m['fixed_FPR']:.4f}  F1={l3_m['fixed_F1']:.4f}")
        print(f"  Main: TPR={main_m.get('TPR',0):.4f}  FPR={main_m.get('FPR',0):.4f}  F1={main_m.get('F1',0):.4f}")
        print(f"  Match: {'YES' if ok else 'NO — investigate'}")
    else:
        print("  (Skipped: main_evaluation.json not found)")

    # ── Save JSON results ──
    report_path = OUTPUT_DIR / "ablation_metrics_v16.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))

    summary = {
        "schema": "mtagentlens.ablation_summary.v16",
        "primary_metrics": [
            {"layer": lm["layer"], "auroc": lm["auroc"], "auroc_95ci": lm["auroc_95ci"],
             "auprc": lm["auprc"], "cohens_d": lm["cohens_d"],
             "brier": lm["brier_score"], "ece": lm["ece"]}
            for lm in report["layer_metrics"]
        ],
        "layer_contributions": report["delta_summary"],
    }
    (OUTPUT_DIR / "ablation_summary_v16.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2))

    # Legacy-format results
    legacy = {
        "schema": "mtagentlens.ablation_staircase.v16",
        "results": [
            {"lid": lm["layer"], "TP": lm["fixed_TP"], "FP": lm["fixed_FP"],
             "FN": lm["fixed_FN"], "TN": lm["fixed_TN"],
             "TPR": lm["fixed_TPR"], "FPR": lm["fixed_FPR"],
             "F1": lm["fixed_F1"], "MCC": lm["fixed_MCC"],
             "auroc": lm["auroc"], "auprc": lm["auprc"],
             "cohens_d": lm["cohens_d"], "brier": lm["brier_score"], "ece": lm["ece"]}
            for lm in report["layer_metrics"]
        ],
    }
    (OUTPUT_DIR / "ablation_results.json").write_text(
        json.dumps(legacy, ensure_ascii=False, indent=2))

    # ── Generate LaTeX PDF tables ──
    print(f"\n{'─'*100}")
    print("  Generating LaTeX PDF tables ...")
    pdfs = generate_all_pdfs(OUTPUT_DIR, layers, report, layer_scores_list, y, meta)
    for p in pdfs:
        print(f"  → tables/{p}")

    print(f"\n→ {report_path}")
    print(f"→ {CSV_DIR}/  (CSV tables + LaTeX PDFs)")
    print(f"\n{'═'*100}")
    print("  Done. v16 Capability Staircase — 4 layers.")
    print(f"{'═'*100}")


if __name__ == "__main__":
    main()
