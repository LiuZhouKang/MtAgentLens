# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Compute the 93-scenario external-baseline comparison from frozen outputs.

Main table: MtAgentLens, Agent Sentinel-style, LLM Guard, Meta Prompt Guard,
R-Judge A2 (full application trace). R-Judge task-only/no-result arms are not
part of this comparison.
"""
import json, glob, os, math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
BASE = ROOT / "compare/results/external/baselines"
rows = {r["scenario_id"]: r for r in (json.loads(l) for l in open(ROOT / "local/final-v1/results-promoted.jsonl"))}
gt = {}
for p in glob.glob(str(ROOT / "datasets/FuxiBench/ground_truth/*/*.json")):
    if "/_" in p:
        continue
    d = json.load(open(p)); gt[d["scenario_id"]] = d


def as_pred(sid):
    return not json.load(open(BASE / f"prompt/agent_sentinel_specific_tu/{sid}.json"))["agent_sentinel_response"].get("action_is_safe", True)


def lg_pred(sid):
    return not json.load(open(BASE / f"prompt/llm_guard/{sid}.json"))["llm_guard_response"].get("action_is_safe", True)


meta = {r["scenario_id"]: (r["prediction"] == "attack") for r in json.load(open(BASE / "prompt/meta_prompt_guard/results.json"))["results"]}
trace_sid = {}
for p in glob.glob(str(BASE / "app/A2/records/*.json")):
    r = json.load(open(p)); trace_sid[r["trace_id"]] = r.get("scenario_id") or os.path.basename(p)[:-5]
rj = {}
for l in open(BASE / "app/A2/judge-results.jsonl"):
    d = json.loads(l); sid = trace_sid.get(d["trace_id"])
    if sid and d.get("status") == "ok":
        rj[sid] = d.get("label") == "unsafe"

det = json.load(open(ROOT / "local/final-v1/detection-metrics-final-93.json"))
mdec = {r["scenario_id"]: r["decision"] == "Unsafe" for r in det["scenarios"]}
PRED = {
    "MtAgentLens": lambda s: mdec[s],
    "Agent Sentinel-style": as_pred,
    "LLM Guard": lg_pred,
    "Meta Prompt Guard": lambda s: meta.get(s, False),
    "R-Judge A2": lambda s: rj.get(s, False),
}
executed = {s for s, r in rows.items() if r.get("marker_executed")}
atk = [s for s in gt if gt[s]["label"] == "attack" and s in executed]
ben = [s for s in gt if gt[s]["label"] == "benign"]


def wilson(k, n, z=1.96):
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0, c - h), min(1, c + h)]


def metrics(p):
    tp = sum(1 for s in atk if p(s)); fp = sum(1 for s in ben if p(s))
    per = {}
    for typ in ("A", "B"):
        ss = [s for s in atk if gt[s]["attack_type"] == typ]
        k = sum(1 for s in ss if p(s))
        per[typ] = {"tp": k, "n": len(ss), "tpr": k / len(ss) if ss else None}
    return {"tp": tp, "n_attack": len(atk), "tpr": tp / len(atk), "tpr_ci": wilson(tp, len(atk)),
            "fp": fp, "n_benign": len(ben), "fpr": fp / len(ben), "fpr_ci": wilson(fp, len(ben)),
            "per_type": per, "coverage": 1.0,
            "decisions": {s: p(s) for s in atk + ben}}


res = {"schema": "mtagentlens.external-baselines/2",
       "cohort": {"scenarios": len(gt), "attack_executed": len(atk), "attack_all": 58, "benign": len(ben)},
       "primary": "PP (executed attacks + all benign)",
       "tools": {n: metrics(p) for n, p in PRED.items()}}

from math import comb
m = res["tools"]["MtAgentLens"]["decisions"]; fam = []
for n, p in PRED.items():
    if n == "MtAgentLens":
        continue
    o = res["tools"][n]["decisions"]; b = c = 0
    for s in m:
        if m[s] and not o[s]: b += 1
        elif o[s] and not m[s]: c += 1
    k = b + c
    pval = 1.0 if k == 0 else min(1.0, 2 * sum(comb(k, i) for i in range(0, min(b, c) + 1)) / 2 ** k)
    fam.append((n, pval, b, c))
order = sorted(fam, key=lambda x: x[1]); adj = {}; prev = 0
for i, (n, p, b, c) in enumerate(order):
    v = max(prev, min(1.0, (len(order) - i) * p)); adj[n] = v; prev = v
for n, p, b, c in fam:
    res["tools"][n]["vs_m"] = {"mcnemar_p": p, "holm_p": adj[n], "m_only": b, "baseline_only": c}

out = BASE / "metrics"; out.mkdir(exist_ok=True)
json.dump(res, open(out / "comparison.json", "w"), indent=2)
print(json.dumps({n: {"tpr": round(v["tpr"], 3), "fpr": round(v["fpr"], 3)} for n, v in res["tools"].items()}, indent=1))
