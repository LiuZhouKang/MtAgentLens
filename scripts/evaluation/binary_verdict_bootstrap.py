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

The corpus is gitignored; a third party must supply the same trace layout inside
the FuxiBench 2.0 package:
`datasets/FuxiBench/traces/<scenario>/responsibility.db` plus
`datasets/FuxiBench/prompt_inputs/manifest.json`. Nine detection-excluded
scenarios are provenance-only and never enter the detection denominators.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from statistics import NormalDist

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from compare.tools.detection_scope import (  # noqa: E402
    DEFAULT_DATASET_DIR,
    included_ground_truth,
)

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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_databases(traces: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for root, _dirs, files in os.walk(traces):
        if "responsibility.db" in files:
            found[os.path.basename(root)] = Path(root) / "responsibility.db"
    return found


def load_p12_report(root: Path) -> dict[str, dict]:
    """Per-scenario provenance from the P1.2 isolated recollection, if run."""
    path = root / "compare/results/p12-recollect/results.jsonl"
    if not path.exists():
        return {}
    report: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("scenario_id"):
            report[row["scenario_id"]] = row
    return report


def rate_block(rows: list[dict], label: str, predicate) -> dict:
    selected = [row for row in rows if row["attack"] == (label == "attack")]
    hits = sum(1 for row in selected if predicate(row))
    low, high = wilson(hits, len(selected))
    return {
        "hits": hits,
        "n": len(selected),
        "rate": round(hits / len(selected), 4) if selected else None,
        "wilson_95": [round(low, 4), round(high, 4)],
    }


def load_corpus(root: Path):
    package = root / "datasets/FuxiBench"
    traces = package / "traces"
    manifest_path = package / "prompt_inputs/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    ground_truth = included_ground_truth(package / "ground_truth")
    entries = [entry for entry in manifest["entries"]
               if entry["scenario_id"] in ground_truth]
    databases = find_databases(traces)
    p12 = load_p12_report(root)
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
    for entry in entries:
        sid = entry["scenario_id"]
        gt = ground_truth[sid]
        db_path = databases.get(sid)
        if db_path is None:
            missing.append(sid)
            continue
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            decisions: Counter = Counter()
            severities: Counter = Counter()
            # Schema v3: one canonical verdict per run. Counting alert rows
            # made a run both Unsafe (phase-1 Observer) and Safe (phase-2).
            canonical = None
            try:
                row = conn.execute(
                    "SELECT final_decision, final_severity FROM runs "
                    "WHERE final_decision IS NOT NULL LIMIT 1"
                ).fetchone()
                if row:
                    canonical = (str(row[0]), str(row[1]))
            except sqlite3.DatabaseError:
                canonical = None
            if canonical is not None:
                decisions[canonical[0]] += 1
                severities[canonical[1]] += 1
            else:
                for decision, severity, count in conn.execute(
                    "SELECT decision, severity, COUNT(*) FROM responsibility_alerts "
                    "GROUP BY 1, 2"
                ):
                    decisions[str(decision)] += count
                    severities[str(severity)] += count
            claims = Counter(
                r[0] for r in conn.execute("SELECT claim_type FROM responsibility_claims")
            )
            # P1.2 producers: graph-pattern verdicts and Credential nodes.
            pattern_verdicts = 0
            for (engine_raw,) in conn.execute(
                "SELECT engine_section_json FROM responsibility_alerts"
            ):
                if not engine_raw:
                    continue
                try:
                    pattern_verdicts += len(
                        (json.loads(engine_raw) or {}).get("pattern_verdicts") or []
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            # The JSON key, not the bare word: fixture paths named
            # `credential-*` are File resources, not Credential resources.
            credential_nodes = conn.execute(
                "SELECT COUNT(*) FROM graph_nodes "
                "WHERE node_type='Credential' OR resource_json LIKE '%\"Credential\"%'"
            ).fetchone()[0]
            # ── Counterfactuals for the P1.1 gates ───────────────────────
            # These are DERIVED from rows the 2026-07 build persisted; they
            # are not a rerun of the fixed binary (that is P1.2).
            #
            # 1. Alert semantics: a claim with a safe engine score is now
            #    informational. Recompute Warning+ using the stored
            #    (has_claims, score) pair.
            has_claims = sum(claims.values()) > 0
            warning_plus_after_fix = False
            unreadable_engine_rows = 0
            for severity, engine_raw in conn.execute(
                "SELECT severity, engine_section_json FROM responsibility_alerts"
            ):
                if severity not in ("Warning", "Critical"):
                    continue
                score = None
                if engine_raw:
                    try:
                        raw_score = (json.loads(engine_raw) or {}).get(
                            "classification_score"
                        )
                        if raw_score is not None:
                            score = float(raw_score)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        score = None
                if score is None:
                    # Do NOT read an unreadable row as score 0: that would
                    # demote the alert and make the fix look better than the
                    # data supports. Keep the Warning and count the row.
                    unreadable_engine_rows += 1
                    warning_plus_after_fix = True
                    continue
                if not (has_claims and score <= 0.15):
                    warning_plus_after_fix = True
                    break
            # 2. Taint epistemic gate: the claim must reach Observed (0.70).
            #    The authorization gate cannot be simulated here because every
            #    corpus DB has zero delegation_records.
            tcu_observed = (
                conn.execute(
                    "SELECT COUNT(*) FROM responsibility_claims "
                    "WHERE claim_type='TaintedContextUse' AND confidence >= 0.70"
                ).fetchone()[0]
                > 0
            )
        finally:
            conn.close()
        p12_row = p12.get(sid) or {}
        # Trust the report only if the DB in front of us is the exact file the
        # report measured. A restored old corpus or a stale report must not be
        # labeled "recollected".
        recollected = bool(
            p12_row.get("copied")
            and p12_row.get("observed")
            and p12_row.get("db_sha256")
            and p12_row.get("db_sha256") == sha256_file(db_path)
        )
        rows.append(
            {
                "sid": sid,
                "attack": gt.get("label") == "attack",
                # P1.2: whether this DB was written by the delivered binary
                # under the isolated protocol, or is still the 2026-07 build.
                "recollected": recollected,
                "binary_sha256": p12_row.get("binary_sha256") if recollected else None,
                "pattern_verdicts": pattern_verdicts,
                "credential_nodes": credential_nodes,
                "type": gt.get("attack_type", "benign"),
                "mode": entry.get("mode")
                or ("agent_teams" if len(entry.get("agents", [])) > 1 else "single_agent"),
                "strict": decisions.get("Unsafe", 0) > 0,
                # Product contract: only decision=Unsafe is actionable.
                # Warning+ is a review signal, reported under its own name.
                "actionable": decisions.get("Unsafe", 0) > 0,
                "warning_plus": (
                    severities.get("Warning", 0) + severities.get("Critical", 0)
                )
                > 0,
                "warning_plus_after_fix": warning_plus_after_fix,
                "unreadable_engine_rows": unreadable_engine_rows,
                "tcu_observed": tcu_observed,
                "claims": {t: claims.get(t, 0) for t in claim_types},
            }
        )
    return rows, missing, len(entries)


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

    rows, missing, expected_total = load_corpus(root)
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

    recollected_rows = [row for row in rows if row.get("recollected")]
    all_recollected = (
        len(recollected_rows) == len(rows)
        and len(rows) == expected_total
    )
    not_recollected = [row["sid"] for row in rows if not row.get("recollected")]
    binary_shas = sorted(
        {row["binary_sha256"] for row in recollected_rows if row.get("binary_sha256")}
    )

    artifact = {
        "schema": "mtagentlens.standard_evaluation.v5",
        "generated_at": "2026-10-19",
        "provenance": {
            "corpus": "datasets/FuxiBench/traces/*/responsibility.db (FuxiBench 2.0 package)",
            "ground_truth": "datasets/FuxiBench/ground_truth (detection-included scope)",
            "detector": (
                "alert rows written by the delivered binary under the P1.2 isolated "
                "protocol (clean profile, inode copy, unique comm, observer disabled)"
                if all_recollected
                else "alert rows written by the 2026-07 collector build, read directly from responsibility_alerts (decision/severity). The corpus predates the delivered 2026-10-19 binary; see rq3_attribution.corpus_caveat."
            ),
            "method": "BCa bootstrap, 10000 resamples, seed 20261019; statistic = per-class detection rate",
            "note": "Fills the Bootstrap-CI commitment for RQ1 detection. BCa intervals are degenerate when every resample is identical (FPR 0/19); Wilson is reported alongside.",
            "reproduce_with": "python3 scripts/evaluation/binary_verdict_bootstrap.py",
            "corpus_provenance": {
                "recollected": len(recollected_rows),
                "total": len(rows),
                "binary_sha256": binary_shas,
                "report": "compare/results/p12-recollect/results.jsonl",
                "not_recollected": not_recollected,
            },
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
                "actionable": "any alert with decision=Unsafe (product contract: only Unsafe is actionable; identical to strict)",
                "warning_plus": "any alert with severity in {Warning,Critical} — a review signal, not an actionable alert",
                **(
                    {}
                    if all_recollected
                    else {
                        "warning_plus_after_alert_semantics_fix": "counterfactual: Warning+ recomputed from each scenario's stored (has_claims, classification_score) under the fixed ladder; NOT a rerun of the fixed binary"
                    }
                ),
            },
            "corpus_coverage": {
                "recollected": len(recollected_rows),
                "total": len(rows),
                "all_recollected": all_recollected,
                "not_recollected": not_recollected,
            },
            "strict": category_metrics(rows, "strict"),
            "actionable": category_metrics(rows, "actionable"),
            "warning_plus": category_metrics(rows, "warning_plus"),
            **(
                {}
                if all_recollected
                else {
                    "warning_plus_after_alert_semantics_fix": {
                        **category_metrics(rows, "warning_plus_after_fix"),
                        "unreadable_engine_rows": sum(
                            row["unreadable_engine_rows"] for row in rows
                        ),
                    }
                }
            ),
            "by_attack_type": {
                t: {
                    "n": sum(1 for r in attacks if r["type"] == t),
                    "strict_detected": sum(
                        1 for r in attacks if r["type"] == t and r["strict"]
                    ),
                    "warning_plus_detected": sum(
                        1 for r in attacks if r["type"] == t and r["warning_plus"]
                    ),
                }
                for t in ("A", "B", "C")
            },
            "composite_rust_score_below_0_70": {"count": len(low), "scenarios": low},
        },
        "rq3_attribution": {
            "corpus_caveat": (
                "The corpus was recollected 2026-10-19 with the delivered binary under the P1.2 isolated protocol (clean profile, inode copy, unique comm, observer disabled). The graph-pattern and credential producers are measured below."
                if all_recollected
                else "The 86 databases were collected 2026-07-16, before the graph-pattern producer was restored (2026-10-19) and before the credential resource producer. Pattern verdicts and Credential nodes are 0 in every scenario: absence here is 'not produced by that build', not 'correctly not detected'."
            ),
            "classification_fpr": claim_fpr,
            "claim_rule_caveat": (
                "Claims were written by the delivered binary after the ADR-0015 gates."
                if all_recollected
                else "These claim counts were written by the 2026-07 build, before the ADR-0015 taint gates. They describe the old rule; tainted_context_gate is the counterfactual under the current rule."
            ),
            "tainted_context_gate": (
                {
                    "measured": True,
                    "note": "Claims already satisfy the four-condition TaintedContextUse gate; the classification_fpr counts above are the measured output.",
                }
                if all_recollected
                else {
                    "counterfactual": "TaintedContextUse now requires confidence >= 0.70 (Observed) and an unauthorized share. Every corpus DB has zero delegation_records, so only the confidence gate is simulable; the authorization gate is unit-tested but measured on a fresh corpus (P1.2).",
                    "observed_confidence": category_metrics(rows, "tcu_observed"),
                }
            ),
            "graph_pattern_producer": {
                "measured": bool(recollected_rows),
                "scope": "recollected+observed",
                "binary_sha256": binary_shas,
                "benign": rate_block(
                    recollected_rows,
                    "benign",
                    lambda row: row["pattern_verdicts"] > 0,
                ),
                "attack": rate_block(
                    recollected_rows,
                    "attack",
                    lambda row: row["pattern_verdicts"] > 0,
                ),
            },
            "credential_resource_producer": {
                "measured": bool(recollected_rows),
                "scope": "recollected+observed",
                "binary_sha256": binary_shas,
                "benign": rate_block(
                    recollected_rows,
                    "benign",
                    lambda row: row["credential_nodes"] > 0,
                ),
                "attack": rate_block(
                    recollected_rows,
                    "attack",
                    lambda row: row["credential_nodes"] > 0,
                ),
            },
            "cross_check": {
                "note": "The current isolated benign baseline (docs/evaluation/fpr-baseline.json) recorded 20/20 observed runs with 0 claims and 0 Warning+."
            },
        },
    }
    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")
    print(f"scenarios={len(rows)} missing={missing}")
    print(f"strict  {artifact['rq1_detection']['strict']}")
    print(f"actionable {artifact['rq1_detection']['actionable']}")
    print(f"warning+ {artifact['rq1_detection']['warning_plus']}")
    print(f"written {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
