# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Build the two-endpoint T7 ablation metrics from frozen 93-run exports.

Endpoint A (ranking): model-free AUROC/AUPRC for L0-L3 scores. L2/L3 verdict
state is partial because their Observer review cache does not bind after graph
reconstruction; the ranking score does not use Observer, so ranking remains
estimable and the verdict state is recorded separately.

Endpoint B (operating point): L1 score at the frozen 0.15 threshold versus the
production L3 final verdict (PP and ITT), with Wilson CIs and McNemar.
"""
import glob, json, math, sys
from pathlib import Path
import numpy as np
from math import comb

ROOT = Path(__file__).resolve().parents[4]
DATA = ROOT / "compare/results/ablation/data/exports"
PROD = ROOT / "compare/results/detection/data/detection-metrics-final-93.json"
LEDGER = ROOT / "compare/results/detection/data/results-promoted.jsonl"
sys.path.insert(0, str(ROOT))
from compare.tools.ablation_metrics import evaluate_layer, compare_layers, auc_pr  # noqa: E402

THRESHOLD = 0.15
LAYERS = ["L0", "L0.5", "L1", "L2", "L3"]


def load_labels():
    labels = {}
    for p in glob.glob(str(ROOT / "datasets/FuxiBench/ground_truth/*/*.json")):
        if "/_" in p:
            continue
        d = json.loads(Path(p).read_text())
        if "label" in d:
            labels[d["scenario_id"]] = d["label"]
    return labels


def wilson(k, n, z=1.96):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def mcnemar(a, b, ids):
    only_a = sum(1 for s in ids if a[s] and not b[s])
    only_b = sum(1 for s in ids if b[s] and not a[s])
    n = only_a + only_b
    p = 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, i) for i in range(min(only_a, only_b) + 1)) / 2 ** n)
    return {"l1_only": only_a, "l3_only": only_b, "mcnemar_p": p}


def main():
    labels = load_labels()
    rows = {json.loads(l)["scenario_id"]: json.loads(l) for l in open(LEDGER)}
    prod = {r["scenario_id"]: r["decision"] for r in json.loads(PROD.read_text())["scenarios"]}
    assert len(labels) == 93
    sids = sorted(labels)
    y = np.array([1 if labels[s] == "attack" else 0 for s in sids])
    scores = {name: [] for name in LAYERS}
    states = {name: {} for name in LAYERS}
    verdict_states = {name: {} for name in LAYERS}
    for sid in sids:
        d = json.loads((DATA / f"{sid}.json").read_text())
        assert d["schema"] == "mtagentlens.ablation-rebuild/v1"
        assert d["protocol"] == "raw-fact-staircase-v1"
        assert len(d["runs"]) == 1
        by = {l["layer"]: l for l in d["runs"][0]["layers"]}
        for name in LAYERS:
            layer = by[name]
            scores[name].append(float(layer["ranking_score"]))
            states[name][layer["state"]] = states[name].get(layer["state"], 0) + 1
            verdict_states[name][layer.get("verdict", {}).get("decision", "none") if layer.get("verdict") else "none"] = \
                verdict_states[name].get(layer.get("verdict", {}).get("decision", "none") if layer.get("verdict") else "none", 0) + 1
    ranking = {}
    for name in LAYERS:
        m = evaluate_layer(np.array(scores[name]), y, fixed_threshold=THRESHOLD, n_bootstrap=10000)
        ranking[name] = {
            "auc": m["auroc"], "auc_ci": m["auroc_95ci"], "auprc": m["auprc"],
            "cohens_d": m["cohens_d"], "brier": m["brier_score"], "ece": m["ece"],
            "state": "available",  # ranking endpoint is score-based
            "verdict_state": sorted(states[name]),
            "verdict_counts": verdict_states[name],
        }
    contrasts = []
    for prev, curr in zip(LAYERS, LAYERS[1:]):
        c = compare_layers(np.array(scores[prev]), np.array(scores[curr]), y, curr)
        contrasts.append({"from": prev, "to": curr, "delta_auc": c["delta_auroc"],
                          "delong_p": c["delong_p"]})
    # Holm over the four pre-declared adjacent contrasts
    order = sorted(contrasts, key=lambda c: c["delong_p"])
    running = 0.0
    for i, c in enumerate(order):
        running = max(running, min(1.0, c["delong_p"] * (len(order) - i)))
        c["holm_p"] = running

    # Paired bootstrap for the pre-declared graph contrast (L0 -> L0.5).
    rng = np.random.default_rng(20261029)
    b_auc, b_ap = [], []
    for _ in range(10000):
        idx = rng.integers(0, len(y), len(y))
        if len(set(y[idx])) < 2:
            continue
        b_auc.append(float(compare_layers(np.array(scores["L0"])[idx], np.array(scores["L0.5"])[idx], y[idx], "L0.5")["delta_auroc"]))
        b_ap.append(float(auc_pr(np.array(scores["L0.5"])[idx], y[idx]) - auc_pr(np.array(scores["L0"])[idx], y[idx])))
    graph = {"delta_auc": contrasts[0]["delta_auc"], "delta_auc_ci": [float(np.percentile(b_auc, 2.5)), float(np.percentile(b_auc, 97.5))],
             "delta_auprc": ranking["L0.5"]["auprc"] - ranking["L0"]["auprc"],
             "delta_auprc_ci": [float(np.percentile(b_ap, 2.5)), float(np.percentile(b_ap, 97.5))]}
    # Operating point
    l1 = {s: scores["L1"][sids.index(s)] >= THRESHOLD for s in sids}
    l3 = {s: prod[s] == "Unsafe" for s in sids}
    executed = {s for s, r in rows.items() if r.get("marker_executed")}
    for mode, atk in (("pp", [s for s in sids if labels[s] == "attack" and s in executed]),
                      ("itt", [s for s in sids if labels[s] == "attack"])):
        ben = [s for s in sids if labels[s] == "benign"]
        out = {}
        for name, pred in (("L1_score_threshold", l1), ("L3_production", l3)):
            tp = sum(1 for s in atk if pred[s]); fp = sum(1 for s in ben if pred[s])
            fn = len(atk) - tp
            prec = tp / (tp + fp) if tp + fp else 0.0
            f1 = 2 * tp / (2 * tp + fp + fn) if tp else 0.0
            out[name] = {"tp": tp, "fn": fn, "fp": fp, "tn": len(ben) - fp,
                         "tpr": tp / len(atk), "tpr_ci": wilson(tp, len(atk)),
                         "fpr": fp / len(ben), "fpr_ci": wilson(fp, len(ben)),
                         "precision": prec, "f1": f1}
        out["paired"] = mcnemar(l1, l3, atk + ben)
        out["attack_ids"] = atk
        out["benign_count"] = len(ben)
        if mode == "pp":
            pp = out
        else:
            itt = out
    stats = {
        "aggregation_unit": "scenario", "n_scenarios": 93,
        "bootstrap_resamples": 10000, "confidence_level": 0.95,
        "confidence_intervals": {
            **{f"auc_{k}": v["auc_ci"] for k, v in ranking.items()},
            **{f"pp_tpr_{k}": v["tpr_ci"] for k, v in pp.items() if k not in ("paired", "attack_ids", "benign_count")},
            **{f"pp_fpr_{k}": v["fpr_ci"] for k, v in pp.items() if k not in ("paired", "attack_ids", "benign_count")},
        },
        "coverage": 1.0, "abstention": 0.0,
        "holm_family": [f"{c['to']}_vs_{c['from']}" for c in contrasts],
        "holm_adjusted_p": {f"{c['to']}_vs_{c['from']}": c["holm_p"] for c in contrasts},
    }
    data = {
        "hard_sensitive_egress": False, "shared_thresholds": True,
        "layers": ranking, "contrasts": contrasts,
        "ranking": {"endpoint": "AUROC/AUPRC over the frozen 93-scenario cohort", "layers": ranking, "graph_contrast": graph},
        "operating_point": {"threshold": THRESHOLD, "pp": pp, "itt": itt},
        "statistics": stats,
        "limitations": [
            "L2/L3 verdict state is partial because Observer scoped reviews do not bind after graph reconstruction; ranking_score does not use Observer and remains estimable.",
            "L3 ranking shares the full-system detector score; Observer changes the verdict, not the continuous ranking score.",
            "L0 has no pre-fixed decision threshold; only AUROC/AUPRC are reported for L0.",
            "Operating point compares L1 score@0.15 with recorded production L3; it is not a re-run of the production observer.",
        ],
    }
    out = {
        "schema": "mtagentlens.experiment-metrics/1", "experiment": "ablation",
        "release_id": "5e7dfe4b54d4bd767152458d0f960ca0d2245f7fc1cd6f8fab0b5594016cebdc",
        "estimable": True, "data": data,
    }
    path = ROOT / "compare/results/ablation/metrics/t7-metrics.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"ranking": {k: round(v["auc"], 4) for k, v in ranking.items()},
                      "pp": {k: {x: round(v[x], 4) for x in ("tpr", "fpr", "precision", "f1")} for k, v in pp.items() if isinstance(v, dict) and "tpr" in v},
                      "mcnemar": pp["paired"]}, indent=1))


if __name__ == "__main__":
    main()
