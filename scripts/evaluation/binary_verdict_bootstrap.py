#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
# Copyright (c) 2026 MtAgentLens Project
"""Measure the delivered binary's verdict on the FuxiBench corpus.

Reads `responsibility_alerts` directly from each scenario's
`responsibility.db` (no evaluation-script rescoring) and reports:

  * TPR/FPR for two definitions: `decision=Unsafe` and Warning+
    (`severity in {Warning, Critical}`);
  * BCa bootstrap 95% intervals (10,000 resamples, fixed seed). When every
    resample is identical (e.g. FPR 0/19) the BCa interval is degenerate and
    the Wilson interval is reported alongside;
  * per-claim-type benign/attack counts for the claim classifiers.

Usage:
    python3 scripts/evaluation/binary_verdict_bootstrap.py \
        [--root .] [--out compare/results/paper/standard_evaluation_v5.json]

The corpus is gitignored; a third party must supply the same trace layout:
`compare/Dataset/FuxiBench/traces/<scenario>/responsibility.db` plus
`compare/Dataset/FuxiBench/prompt_inputs/manifest.json`.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sqlite3
from collections import Counter
from pathlib import Path
from statistics import NormalDist

SEED = 20261019
RESAMPLES = 10_000
ND = NormalDist()


def phi(x: float) -> float:
    return ND.cdf(x)


def phi_inv(p: float) -> float:
    return ND.inv_cdf(p)


def bca(values: list[int], resamples: int = RESAMPLES, seed: int = SEED):
    """BCa 95% interval for the mean of a 0/1 sample."""
    n = len(values)
    if n == 0:
        return 0.0, [0.0, 1.0]
    theta = sum(values) / n
    rng = random.Random(seed)
    boots = sorted(
        sum(values[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(resamples)
    )
    jack = [(sum(values) - v) / (n - 1) for v in values] if n > 1 else [theta]
    mean_j = sum(jack) / len(jack)
    num = sum((mean_j - v) ** 3 for v in jack)
    den = 6 * (sum((mean_j - v) ** 2 for v in jack) ** 1.5)
    accel = num / den if den else 0.0
    prop = min(max(sum(1 for b in boots if b < theta) / resamples, 1e-9), 1 - 1e-9)
    z0 = phi_inv(prop)
    interval = []
    for alpha in (0.025, 0.975):
        z = phi_inv(alpha)
        denominator = 1 - accel * (z0 + z)
        adjusted = phi(z0 + (z0 + z) / denominator) if denominator else alpha
        interval.append(boots[min(max(int(adjusted * resamples), 0), resamples - 1)])
    return theta, interval


def wilson(k: int, n: int, z: float = 1.959963984540054) -> list[float]:
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [(c - m) / d, (c + m) / d]


def find_databases(traces: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for root, _dirs, files in os.walk(traces):
        if "responsibility.db" in files:
            found[os.path.basename(root)] = Path(root) / "responsibility.db"
    return found


def load_corpus(root: Path):
    traces = root / "compare/Dataset/FuxiBench/traces"
    manifest_path = root / "compare/Dataset/FuxiBench/prompt_inputs/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    databases = find_databases(traces)
    rows = []
    missing = []
    claim_types = (
        "TaintedContextUse",
        "PolicyViolation",
        "SuspiciousDelegation",
        "UnauthorizedCapabilityUse",
        "BlockedAttempt",
        "ResponsibilityAnomaly",
    )
    for entry in manifest["entries"]:
        sid = entry["scenario_id"]
        db_path = databases.get(sid)
        if db_path is None:
            missing.append(sid)
            continue
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            decisions: Counter = Counter()
            severities: Counter = Counter()
            for decision, severity, count in conn.execute(
                "SELECT decision, severity, COUNT(*) FROM responsibility_alerts "
                "GROUP BY 1, 2"
            ):
                decisions[str(decision)] += count
                severities[str(severity)] += count
            claims = Counter(
                r[0] for r in conn.execute("SELECT claim_type FROM responsibility_claims")
            )
        finally:
            conn.close()
        rows.append(
            {
                "sid": sid,
                "attack": entry.get("attack_type", "benign") != "benign",
                "type": entry.get("attack_type", "benign"),
                "mode": entry.get("mode")
                or ("agent_teams" if len(entry.get("agents", [])) > 1 else "single_agent"),
                "strict": decisions.get("Unsafe", 0) > 0,
                "actionable": (severities.get("Warning", 0) + severities.get("Critical", 0))
                > 0,
                "claims": {t: claims.get(t, 0) for t in claim_types},
            }
        )
    return rows, missing


def category_metrics(rows, key):
    attacks = [r for r in rows if r["attack"]]
    benign = [r for r in rows if not r["attack"]]
    a = [1 if r[key] else 0 for r in attacks]
    b = [1 if r[key] else 0 for r in benign]
    tp, fn = sum(a), len(a) - sum(a)
    fp, tn = sum(b), len(b) - sum(b)
    tpr, tpr_ci = bca(a)
    fpr, fpr_ci = bca(b)
    precision = tp / (tp + fp) if tp + fp else 0.0
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
    denom = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    mcc = (tp * tn - fp * fn) / denom if denom else 0.0
    return {
        "TP": tp,
        "FN": fn,
        "FP": fp,
        "TN": tn,
        "TPR": tpr,
        "TPR_CI95": tpr_ci,
        "TPR_wilson95": wilson(tp, tp + fn),
        "FPR": fpr,
        "FPR_CI95": fpr_ci,
        "FPR_wilson95": wilson(fp, fp + tn),
        "Precision": precision,
        "F1": f1,
        "MCC": mcc,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument(
        "--out",
        default="compare/results/paper/standard_evaluation_v5.json",
        help="artifact path (relative to --root)",
    )
    args = parser.parse_args()
    root = Path(args.root).resolve()

    rows, missing = load_corpus(root)
    attacks = [r for r in rows if r["attack"]]
    benign = [r for r in rows if not r["attack"]]
    type_counts = Counter(r["type"] for r in attacks)

    claim_fpr = {}
    for claim in rows[0]["claims"] if rows else {}:
        b = [1 if r["claims"][claim] > 0 else 0 for r in benign]
        a = [1 if r["claims"][claim] > 0 else 0 for r in attacks]
        entry = {
            "benign": f"{sum(b)}/{len(b)}",
            "fpr": (sum(b) / len(b)) if b else 0.0,
            "wilson95": [round(x, 3) for x in wilson(sum(b), len(b))],
            "attack": f"{sum(a)}/{len(a)}",
            "recall": (sum(a) / len(a)) if a else 0.0,
        }
        if sum(a) + sum(b) == 0:
            entry["note"] = (
                "enum variant exists but no producer emitted it in this corpus; "
                "0/19 and 0/67 are empty-set observations, not tested negatives"
            )
        claim_fpr[claim] = entry

    composed = json.loads(
        (root / "compare/results/paper/main_evaluation.json").read_text()
    )
    low = [
        s["sid"]
        for s in composed["per_scenario"]
        if s["is_attack"] and (s.get("rust_score") or 0) < 0.70
    ]

    artifact = {
        "schema": "mtagentlens.standard_evaluation.v5",
        "generated_at": "2026-10-19",
        "provenance": {
            "corpus": "compare/Dataset/FuxiBench/traces/*/responsibility.db (86 scenario databases)",
            "ground_truth": "compare/Dataset/FuxiBench/prompt_inputs/manifest.json",
            "detector": "delivered binary verdict, read directly from responsibility_alerts (decision/severity)",
            "method": "BCa bootstrap, 10000 resamples, seed 20261019; statistic = per-class detection rate",
            "note": "Fills the Bootstrap-CI commitment for RQ1 detection. BCa intervals are degenerate when every resample is identical (FPR 0/19); Wilson is reported alongside.",
            "reproduce_with": "python3 scripts/evaluation/binary_verdict_bootstrap.py",
        },
        "dataset_stats": {
            "total_scenarios": len(rows),
            "attack_scenarios": len(attacks),
            "benign_scenarios": len(benign),
            "type_a": type_counts.get("A", 0),
            "type_b": type_counts.get("B", 0),
            "type_c": type_counts.get("C", 0),
        },
        "rq1_detection": {
            "definitions": {
                "strict": "any alert with decision=Unsafe",
                "actionable": 'any alert with severity in {Warning,Critical} (product "Warning+")',
            },
            "strict": category_metrics(rows, "strict"),
            "actionable": category_metrics(rows, "actionable"),
            "by_attack_type": {
                t: {
                    "n": sum(1 for r in attacks if r["type"] == t),
                    "strict_detected": sum(
                        1 for r in attacks if r["type"] == t and r["strict"]
                    ),
                    "actionable_detected": sum(
                        1 for r in attacks if r["type"] == t and r["actionable"]
                    ),
                }
                for t in ("A", "B", "C")
            },
            "composite_rust_score_below_0_70": {"count": len(low), "scenarios": low},
        },
        "rq3_attribution": {
            "corpus_caveat": "The 86 databases were collected 2026-07-16, before the graph-pattern producer was restored (2026-10-19) and before the credential resource producer. Pattern verdicts and Credential nodes are 0 in every scenario: absence here is 'not produced by that build', not 'correctly not detected'.",
            "classification_fpr": claim_fpr,
            "graph_pattern_producer": {
                "measured": False,
                "reason": "producer restored 2026-10-19; corpus predates it",
            },
            "credential_resource_producer": {
                "measured": False,
                "reason": "corpus has zero Credential nodes in all 86 scenarios",
            },
            "cross_check": {
                "note": "The 2026-10-19 benign FPR rerun (docs/evaluation/archive/fpr-baseline-2026-10-19.json) recorded 0 claims across 18 observed scenarios, while this July corpus records TaintedContextUse in 15/19 benign scenarios. Different binaries/corpora; neither is the current clean-host number."
            },
        },
    }
    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")
    print(f"scenarios={len(rows)} missing={missing}")
    print(f"strict  {artifact['rq1_detection']['strict']}")
    print(f"warning {artifact['rq1_detection']['actionable']}")
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
