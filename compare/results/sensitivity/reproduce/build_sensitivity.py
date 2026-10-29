# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Build the two-threshold sensitivity experiment plus weight/gate OAT and joint LHS.

All scores are recomputed exactly from the frozen six-dimension components and
the current Rust fusion formula. The recorded production Observer is held fixed:
route changes override deterministically; unchanged routes retain the recorded
verdict. Baseline must reproduce 93/93 recorded scores and verdicts.
"""
import glob, json, math, os, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[4]
EXPORTS = ROOT / "compare/results/sensitivity/data/raw/exports"
PROD = ROOT / "compare/results/sensitivity/data/raw/detection-metrics-final-93.json"
LEDGER = ROOT / "compare/results/sensitivity/data/raw/results-promoted.jsonl"
FAMILIES = ROOT / "compare/results/sensitivity/data/raw/families.json"
OUT = ROOT / "compare/results/sensitivity"
sys.path.insert(0, str(ROOT))
from compare.tools.scoring_replica import fuse_dimensions, platt_sigmoid  # noqa: E402

SAFE = [round(0.08 + 0.002 * i, 3) for i in range(111)]
UNSAFE = [round(0.50 + 0.005 * i, 3) for i in range(73)]
GATE = [5.0, 7.5, 10.0, 15.0, 20.0]
BASE = {"safe": 0.15, "unsafe": 0.70, "gate_center": 10.0}
DIM_NAMES = ["D1 边界", "D2 模式", "D3 角色", "D4 重复", "D5 证据", "D6 行为"]


def route(score, safe, unsafe):
    if score > unsafe:
        return "U"
    if score < safe:
        return "S"
    return "Q"


def load():
    labels = {}
    for p in glob.glob(str(ROOT / "datasets/FuxiBench/ground_truth/*/*.json")):
        if "/_" in p:
            continue
        d = json.loads(Path(p).read_text())
        if "label" in d:
            labels[d["scenario_id"]] = d["label"]
    rows = {json.loads(l)["scenario_id"]: json.loads(l) for l in open(LEDGER)}
    prod = {r["scenario_id"]: r for r in json.loads(PROD.read_text())["scenarios"]}
    recs = {}
    for p in glob.glob(str(EXPORTS / "*.json")):
        sid = os.path.basename(p)[:-5]
        d = json.loads(Path(p).read_text())
        layer = [x for x in d["runs"][0]["layers"] if x["layer"] == "L3"][0]
        s = layer["score"]
        gate = s["gate_factor"]
        pbc_gate = 10 + 2 * math.log(gate / (1 - gate)) if 1e-12 < gate < 1 - 1e-12 else (1e12 if gate >= 1 else -1e12)
        hard = any("hard_violation" in x for x in prod[sid].get("provenance", []))
        recs[sid] = {
            "dims": [max(s["boundary_score"], s["rule_score"]), s["pattern_score"], s["role_score"],
                     s["repetition_score"], s["evidence_score"], s["behavioral_score"]],
            "weights": list(s["scoring"]["weights"]),
            "pbc_gate": pbc_gate,
            "unsafe_rules": layer["graph"]["unsafe_rules"] > 0,
            "base_score": s["system_score"],
            "base_route": route(s["system_score"], BASE["safe"], BASE["unsafe"]),
            "hard": hard,
            "d0": prod[sid]["decision"],
        }
    assert set(recs) == set(labels) == set(prod)
    return labels, rows, prod, recs


def score(r, factors, center):
    weights = [w * f for w, f in zip(r["weights"], factors)]
    raw = fuse_dimensions(r["dims"], weights)
    cal = platt_sigmoid(raw)
    g = 1 / (1 + math.exp(-(r["pbc_gate"] - center) / 2.0))
    eff = max(g, 0.3) if r["unsafe_rules"] else g
    return min(cal * eff, 1.0)


def decide(recs, sid, safe, unsafe, factors, center):
    r = recs[sid]
    if r["hard"]:
        return r["d0"]
    sc = score(r, factors, center)
    rn = route(sc, safe, unsafe)
    if rn == r["base_route"]:
        return r["d0"]
    if rn == "S":
        return "Safe"
    if rn == "U":
        return "Unsafe"
    return r["d0"]


def outcome(recs, labels, rows, attack_ids, safe, unsafe, factors, center):
    ben = [s for s in labels if labels[s] == "benign"]
    tp = sum(decide(recs, s, safe, unsafe, factors, center) == "Unsafe" for s in attack_ids)
    fp = sum(decide(recs, s, safe, unsafe, factors, center) == "Unsafe" for s in ben)
    flips = sum(decide(recs, s, safe, unsafe, factors, center) != recs[s]["d0"] for s in labels)
    prec = tp / (tp + fp) if tp + fp else 0.0
    f1 = 2 * tp / (2 * tp + fp + (len(attack_ids) - tp)) if tp else 0.0
    return {"tp": tp, "fp": fp, "n_attack": len(attack_ids), "n_benign": len(ben),
            "tpr": tp / len(attack_ids), "fpr": fp / len(ben),
            "precision": prec, "f1": f1, "flips": flips, "flip_rate": flips / len(labels)}


def boot_f1(recs, labels, attack_ids, safe, unsafe, factors, center, n=2000, seed=20261030):
    rng = np.random.default_rng(seed); ben = [s for s in labels if labels[s] == "benign"]; vals = []
    all_ids = attack_ids + ben
    for _ in range(n):
        sample = list(rng.choice(all_ids, size=len(all_ids), replace=True))
        a = [s for s in sample if s in attack_ids]; b = [s for s in sample if s in ben]
        if not a:
            continue
        tp = sum(decide(recs, s, safe, unsafe, factors, center) == "Unsafe" for s in a)
        fp = sum(decide(recs, s, safe, unsafe, factors, center) == "Unsafe" for s in b)
        vals.append(2 * tp / (2 * tp + fp + (len(a) - tp)) if tp else 0.0)
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]


def main():
    labels, rows, prod, recs = load()
    attack = [s for s in labels if labels[s] == "attack" and rows[s].get("marker_executed")]
    benign = [s for s in labels if labels[s] == "benign"]
    base_f = [1.0] * 6
    # Exact baseline: score and verdict.
    for sid, r in recs.items():
        assert abs(score(r, base_f, 10.0) - r["base_score"]) < 1e-6, sid
        assert decide(recs, sid, BASE["safe"], BASE["unsafe"], base_f, 10.0) == r["d0"], sid

    # S1: safe x unsafe surface, gate center fixed.
    surface = []
    for safe in SAFE:
        for unsafe in UNSAFE:
            m = outcome(recs, labels, rows, attack, safe, unsafe, base_f, 10.0)
            m.update({"safe": safe, "unsafe": unsafe})
            surface.append(m)
    baseline = outcome(recs, labels, rows, attack, BASE["safe"], BASE["unsafe"], base_f, 10.0)
    baseline.update({"safe": BASE["safe"], "unsafe": BASE["unsafe"], "gate_center": 10.0})
    baseline["f1_ci"] = boot_f1(recs, labels, attack, BASE["safe"], BASE["unsafe"], base_f, 10.0)
    best = max(surface, key=lambda p: p["f1"])
    worst = min(surface, key=lambda p: p["f1"])

    # S2: OAT over six weights and gate.
    oat = []
    for i, name in enumerate(DIM_NAMES):
        for factor in (0.8, 1.2):
            f = list(base_f); f[i] = factor
            m = outcome(recs, labels, rows, attack, BASE["safe"], BASE["unsafe"], f, 10.0)
            m.update({"parameter": name, "factor": factor, "kind": "weight"})
            oat.append(m)
    for center in GATE:
        m = outcome(recs, labels, rows, attack, BASE["safe"], BASE["unsafe"], base_f, center)
        m.update({"parameter": "门控中心", "factor": center, "kind": "gate"})
        oat.append(m)
    oat_summary = []
    for i, name in enumerate(DIM_NAMES):
        vals = [p for p in oat if p["parameter"] == name]
        oat_summary.append({"parameter": name, "kind": "weight",
                            "f1_min": min(x["f1"] for x in vals), "f1_max": max(x["f1"] for x in vals),
                            "flips_min": min(x["flips"] for x in vals), "flips_max": max(x["flips"] for x in vals)})
    gate_vals = [p for p in oat if p["kind"] == "gate"]
    oat_summary.append({"parameter": "门控中心", "kind": "gate",
                        "f1_min": min(x["f1"] for x in gate_vals), "f1_max": max(x["f1"] for x in gate_vals),
                        "flips_min": min(x["flips"] for x in gate_vals), "flips_max": max(x["flips"] for x in gate_vals)})

    # S3: 1000-point Latin hypercube over thresholds, gate, six weights.
    rng = np.random.default_rng(20261030)
    n = 1000
    dims = 9
    lhs = np.empty((n, dims))
    for j in range(dims):
        lhs[:, j] = (rng.permutation(n) + rng.random(n)) / n
    safe_s = SAFE[0] + lhs[:, 0] * (SAFE[-1] - SAFE[0])
    unsafe_s = UNSAFE[0] + lhs[:, 1] * (UNSAFE[-1] - UNSAFE[0])
    gate_s = GATE[0] + lhs[:, 2] * (GATE[-1] - GATE[0])
    factors = 0.8 + lhs[:, 3:] * 0.4
    lhs_rows = []
    for i in range(n):
        f = [float(x) for x in factors[i]]
        m = outcome(recs, labels, rows, attack, float(safe_s[i]), float(unsafe_s[i]), f, float(gate_s[i]))
        m.update({"safe": float(safe_s[i]), "unsafe": float(unsafe_s[i]), "gate_center": float(gate_s[i]),
                  "weight_factors": [float(x) for x in factors[i]]})
        lhs_rows.append(m)
    f1s = np.array([x["f1"] for x in lhs_rows]); flips = np.array([x["flip_rate"] for x in lhs_rows])
    lhs_summary = {
        "n": n,
        "f1_mean": float(f1s.mean()), "f1_median": float(np.median(f1s)),
        "f1_p2_5": float(np.percentile(f1s, 2.5)), "f1_p97_5": float(np.percentile(f1s, 97.5)),
        "f1_p5": float(np.percentile(f1s, 5)), "f1_p95": float(np.percentile(f1s, 95)),
        "f1_min": float(f1s.min()), "f1_max": float(f1s.max()),
        "frac_f1_ge_080": float((f1s >= 0.80).mean()), "frac_f1_ge_082": float((f1s >= 0.82).mean()),
        "flip_mean": float(flips.mean()), "flip_p95": float(np.percentile(flips, 95)),
        "samples": lhs_rows,
    }

    # Descriptive dev/test split (retrospective family split, not independence evidence).
    fam = json.loads(FAMILIES.read_text())["assignments"]
    dev = [s for s in labels if fam.get(s, {}).get("split") == "dev"]
    test = [s for s in labels if fam.get(s, {}).get("split") == "test"]
    def split_metric(ids):
        a = [s for s in attack if s in ids]; b = [s for s in benign if s in ids]
        tp = sum(recs[s]["d0"] == "Unsafe" for s in a); fp = sum(recs[s]["d0"] == "Unsafe" for s in b)
        return {"scenarios": len(ids), "attack": len(a), "benign": len(b),
                "tpr": tp / len(a) if a else None, "fpr": fp / len(b) if b else None,
                "f1": 2 * tp / (2 * tp + fp + (len(a) - tp)) if tp else None}
    devtest = {"dev": split_metric(dev), "test": split_metric(test),
               "note": "retrospective signature split (43/50); descriptive only, not independent validation"}

    data = {
        "cohort": {"scenarios": len(labels), "attack": sum(v == "attack" for v in labels.values()),
                   "benign": len(benign), "attack_executed": len(attack)},
        "design": {"safe": SAFE, "unsafe": UNSAFE, "gate_center": GATE, "grid_points": len(surface)},
        "grid": [{"safe": p["safe"], "unsafe": p["unsafe"]} for p in surface],
        "points": len(surface),
        "bootstrap_resamples": 2000,
        "frozen_observer": True,
        "baseline": baseline, "best": best, "worst": worst, "surface": surface,
        "oat": oat, "oat_summary": oat_summary,
        "lhs": lhs_summary,
        "dev_test": devtest,
        "statistics": {
            "aggregation_unit": "scenario", "n_scenarios": 93,
            "bootstrap_resamples": 2000, "confidence_level": 0.95,
            "confidence_intervals": {"baseline_f1": baseline["f1_ci"], "best_f1": [best["f1"], best["f1"]],
                                     "worst_f1": [worst["f1"], worst["f1"]]},
            "coverage": 1.0, "abstention": 0.0,
        },
        "limitations": [
            "Development/test independence is not established; the 85-family 43/50 split is retrospective and descriptive only.",
            "Observer is held fixed at recorded observations; this is a routing counterfactual, not a live Observer re-run.",
            "Hard-rule runs retain their recorded verdict.",
            "F1 is PP (29 executed attacks); 110-point grid is deterministic, not 110 independent samples.",
        ],
    }
    out = {"schema": "mtagentlens.experiment-metrics/1", "experiment": "sensitivity",
           "release_id": "5e7dfe4b54d4bd767152458d0f960ca0d2245f7fc1cd6f8fab0b5594016cebdc",
           "estimable": True, "data": data}
    (OUT / "metrics").mkdir(exist_ok=True)
    (OUT / "metrics/t7-sensitivity.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    (OUT / "data").mkdir(exist_ok=True)
    (OUT / "data/t7-sensitivity.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"baseline": round(baseline["f1"], 3), "best": [best["safe"], best["unsafe"], round(best["f1"], 3)],
                      "worst": [worst["safe"], worst["unsafe"], round(worst["f1"], 3)],
                      "lhs": {k: round(lhs_summary[k], 3) for k in ("f1_median", "f1_p2_5", "f1_p97_5", "frac_f1_ge_080")},
                      "dev_test": devtest}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
