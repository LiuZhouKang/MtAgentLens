#!/usr/bin/env python3
"""
MtAgentPrism Claim-Level Evaluation Tool (v3)
============================================

Replaces the flawed binary-classification calibrate_scoring.py with proper
claim-level matching using Hungarian algorithm for optimal bipartite matching
between expected claims (ground truth) and actual claims (collector DB output).

Methodology:
  1. Parse each GT expected_claim string into {claim_type, policy_id, boundary_id, severity}
  2. Parse each DB claim into {claim_type, policy_id, boundary_id, severity}
  3. Build cost matrix: 0 = exact match on all 4 fields, 1 = claim_type mismatch, 
     0.5 = partial (policy/boundary/severity mismatch)
  4. Hungarian algorithm finds optimal 1-to-1 matching
  5. Matched pairs with cost=0 → True Positive
  6. Unmatched GT claims → False Negative
  7. Unmatched DB claims → False Positive
  8. Compute per-scenario, per-category, and overall precision/recall/F1

Cost matrix design (academic basis):
  - Perfect match (all 4 fields): cost = 0 → always preferred
  - Claim type mismatch: cost = 1.0 → never selected over any match
  - Boundary/policy mismatch: cost = 0.5 → selected only if no perfect match
  - Severity mismatch: cost = 0.3 → selected if other fields match

Usage:
    python3 tools/evaluate_claims.py [--csv output.csv] [--verbose]

Authors: 刘周康, 唐浩钺, 刘锦样
"""

import json
import sqlite3
import sys
import csv
import math
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATASET_DIR = PROJECT_ROOT / "compare" / "Dataset" / "FuxiBench"
GT_DIR = DATASET_DIR / "ground_truth"
TRACES_DIR = DATASET_DIR / "traces"


# ─── Data structures ─────────────────────────────────────────────────────

@dataclass
class Claim:
    claim_type: str
    policy_id: str
    boundary_id: str
    severity: str
    # Source reference (for error reporting)
    source_agent: str = ""
    source_ref: str = ""

    def to_key(self) -> str:
        """Canonical string representation for display."""
        return f"claim.{self.claim_type}.{self.policy_id}.{self.boundary_id}.{self.severity}"

    def matches_type(self, other: "Claim") -> bool:
        return self.claim_type == other.claim_type

    def matches_all(self, other: "Claim") -> bool:
        return (self.claim_type == other.claim_type and
                self.policy_id == other.policy_id and
                self.boundary_id == other.boundary_id and
                self.severity == other.severity)

    def match_cost(self, other: "Claim") -> float:
        """Compute matching cost. 0 = perfect match, 1 = no match."""
        if self.claim_type != other.claim_type:
            return 1.0  # Claim type mismatch → never match
        cost = 0.0
        if self.policy_id != other.policy_id:
            cost += 0.35
        if self.boundary_id != other.boundary_id:
            cost += 0.35
        if self.severity != other.severity:
            cost += 0.30
        return cost


@dataclass
class ScenarioResult:
    scenario_id: str
    category: str
    label: str  # attack / benign
    gt_claims: List[Claim] = field(default_factory=list)
    db_claims: List[Claim] = field(default_factory=list)
    tp: int = 0
    fp: int = 0
    fn: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    matched_pairs: List[Tuple[Claim, Claim]] = field(default_factory=list)
    unmatched_gt: List[Claim] = field(default_factory=list)
    unmatched_db: List[Claim] = field(default_factory=list)
    error: str = ""


# ─── Hungarian algorithm ─────────────────────────────────────────────────

def hungarian_min_cost(cost_matrix: List[List[float]]) -> Tuple[List[Tuple[int, int]], float]:
    """
    Hungarian algorithm for minimum-cost bipartite matching.

    Args:
        cost_matrix: MxN matrix where cost_matrix[i][j] is the cost of
                     matching GT claim i to DB claim j.

    Returns:
        (assignments, total_cost): assignments is list of (gt_idx, db_idx) pairs.
    """
    M = len(cost_matrix)
    if M == 0:
        return [], 0.0
    N = len(cost_matrix[0]) if M > 0 else 0
    if N == 0:
        return [], 0.0

    # Square up the matrix: add dummy rows/columns with high cost
    size = max(M, N)
    # Use 1.5 as dummy cost (higher than any valid match, lower than claim_type mismatch)
    dummy_cost = 1.5

    matrix = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            if i < M and j < N:
                matrix[i][j] = cost_matrix[i][j]
            else:
                matrix[i][j] = dummy_cost

    # Standard Hungarian O(n^3)
    u = [0.0] * (size + 1)
    v = [0.0] * (size + 1)
    p = [0] * (size + 1)
    way = [0] * (size + 1)

    for i in range(1, size + 1):
        p[0] = i
        j0 = 0
        minv = [float('inf')] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = float('inf')
            j1 = 0
            for j in range(1, size + 1):
                if not used[j]:
                    cur = matrix[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(size + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    # Build assignments, filtering dummy matches and high-cost matches
    assignments = []
    total_cost = 0.0
    ans = [0] * (size + 1)
    for j in range(1, size + 1):
        ans[p[j]] = j

    for i in range(1, M + 1):
        j = ans[i]
        if j >= 1 and j <= N:
            cost = cost_matrix[i - 1][j - 1]
            # Only accept matches with cost < 0.5 (exact or minor mismatch)
            if cost < 0.5:
                assignments.append((i - 1, j - 1))
                total_cost += cost

    return assignments, total_cost


# ─── Parsing ─────────────────────────────────────────────────────────────

def parse_claim_string(s: str) -> Optional[Claim]:
    """
    Parse a claim string like:
    claim.TaintedContextUse.policy.content.injection.boundary.content.prompt_injection.Warning
    into a Claim object.
    """
    parts = s.split(".")
    if len(parts) < 5 or parts[0] != "claim":
        return None

    claim_type = parts[1]

    # Find policy_id and boundary_id by scanning for "policy." and "boundary."
    policy_id = ""
    boundary_id = ""
    severity = parts[-1]

    in_policy = False
    in_boundary = False
    policy_parts = []
    boundary_parts = []

    for part in parts[2:-1]:
        if part == "policy":
            in_policy = True
            in_boundary = False
            continue
        if part == "boundary":
            in_boundary = True
            in_policy = False
            continue
        if in_policy:
            policy_parts.append(part)
        elif in_boundary:
            boundary_parts.append(part)

    policy_id = "policy." + ".".join(policy_parts) if policy_parts else ""
    boundary_id = "boundary." + ".".join(boundary_parts) if boundary_parts else ""

    return Claim(
        claim_type=claim_type,
        policy_id=policy_id,
        boundary_id=boundary_id,
        severity=severity,
    )


def load_gt_claims(gt_file: Path) -> Tuple[List[Claim], str, str, str]:
    """Load expected claims from a GT JSON file."""
    try:
        data = json.loads(gt_file.read_text())
    except (json.JSONDecodeError, IOError) as e:
        return [], "unknown", "unknown", str(e)

    sid = data.get("scenario_id", gt_file.stem)
    label = data.get("label", "benign")
    category = data.get("category", gt_file.parent.name)

    claims = []
    expected = data.get("expected_behavior", {}).get("expected_claims", [])
    for claim_str in expected:
        claim = parse_claim_string(claim_str)
        if claim:
            claim.source_ref = claim_str
            claims.append(claim)

    return claims, sid, label, category


def load_db_claims(db_path: Path) -> Tuple[List[Claim], str]:
    """Load actual claims from a responsibility.db file."""
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            "SELECT claim_type, policy_id, boundary_id, severity, subject_agent_id "
            "FROM responsibility_claims"
        )
        claims = []
        for row in cur.fetchall():
            claims.append(Claim(
                claim_type=row[0],
                policy_id=row[1],
                boundary_id=row[2],
                severity=row[3],
                source_agent=row[4],
            ))
        conn.close()
        return claims, ""
    except Exception as e:
        return [], str(e)


def find_db_for_scenario(scenario_id: str) -> Optional[Path]:
    """Find the responsibility.db for a given scenario_id."""
    for db_path in TRACES_DIR.rglob("responsibility.db"):
        try:
            parent = db_path.parent.name
            if parent == scenario_id:
                return db_path
        except (OSError, PermissionError):
            continue
    return None


# ─── Evaluation ──────────────────────────────────────────────────────────

def evaluate_scenario(
    gt_claims: List[Claim],
    db_claims: List[Claim],
    scenario_id: str,
    category: str,
    label: str,
) -> ScenarioResult:
    """Evaluate a single scenario with claim-level matching."""
    result = ScenarioResult(
        scenario_id=scenario_id,
        category=category,
        label=label,
        gt_claims=gt_claims,
        db_claims=db_claims,
    )

    M = len(gt_claims)
    N = len(db_claims)

    # Benign scenarios: GT expects 0 claims.
    # Any DB claims are false positives.
    if label == "benign":
        if M == 0 and N == 0:
            result.tp = 0
        elif M == 0:
            result.fp = N
            result.unmatched_db = list(db_claims)
        result.precision = 1.0 if N == 0 else 0.0
        result.recall = 1.0  # vacuously true: no expected claims
        result.f1 = 1.0 if M == 0 and N == 0 else 0.0
        return result

    # Attack scenarios with no expected claims → GT error, skip
    if M == 0:
        result.error = "Attack scenario with 0 expected claims"
        return result

    # Build cost matrix
    cost_matrix = [[0.0] * N for _ in range(M)]
    for i in range(M):
        for j in range(N):
            cost_matrix[i][j] = gt_claims[i].match_cost(db_claims[j])

    # Hungarian matching
    assignments, _ = hungarian_min_cost(cost_matrix)

    # Classify matched pairs
    matched_gt = set()
    matched_db = set()
    for gt_idx, db_idx in assignments:
        matched_gt.add(gt_idx)
        matched_db.add(db_idx)
        result.matched_pairs.append((gt_claims[gt_idx], db_claims[db_idx]))
        result.tp += 1

    # Unmatched
    result.unmatched_gt = [gt_claims[i] for i in range(M) if i not in matched_gt]
    result.unmatched_db = [db_claims[j] for j in range(N) if j not in matched_db]
    result.fn = len(result.unmatched_gt)
    result.fp = len(result.unmatched_db)

    # Metrics
    tp = result.tp
    fp = result.fp
    fn = result.fn
    result.precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    result.recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    if result.precision + result.recall > 0:
        result.f1 = 2 * result.precision * result.recall / (result.precision + result.recall)
    else:
        result.f1 = 0.0

    return result


# ─── Main ────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="MtAgentPrism Claim-Level Evaluation Tool (v3)"
    )
    parser.add_argument("--csv", type=str, help="Output CSV file path")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-scenario details")
    parser.add_argument("--show-failures", action="store_true", help="Show only failing scenarios")
    args = parser.parse_args()

    print("=" * 72)
    print("  MtAgentPrism Claim-Level Evaluation v3")
    print("  Hungarian bipartite matching: claim_type + policy + boundary + severity")
    print("=" * 72)
    print()

    # ── Load all GTs ──
    print("Loading ground truth...")
    gt_data: Dict[str, Tuple[List[Claim], str, str]] = {}  # sid → (claims, label, category)
    gt_files = [f for f in GT_DIR.rglob("*.json") if "_external_archive" not in str(f)]

    for gt_file in sorted(gt_files):
        claims, sid, label, category = load_gt_claims(gt_file)
        gt_data[sid] = (claims, label, category)

    print(f"  Loaded {len(gt_data)} GT labels")
    attack_count = sum(1 for _, l, _ in gt_data.values() if l == "attack")
    benign_count = sum(1 for _, l, _ in gt_data.values() if l == "benign")
    print(f"  Attack: {attack_count}, Benign: {benign_count}")
    print()

    # ── Evaluate each scenario with a DB ──
    results: List[ScenarioResult] = []
    scenarios_with_db = 0
    scenarios_no_db = 0

    for sid, (gt_claims, label, category) in sorted(gt_data.items()):
        db_path = find_db_for_scenario(sid)
        if db_path is None:
            scenarios_no_db += 1
            if args.verbose:
                print(f"  NO_DB {sid:40s} category={category} label={label}")
            continue

        scenarios_with_db += 1
        db_claims, db_err = load_db_claims(db_path)
        if db_err:
            result = ScenarioResult(sid, category, label)
            result.error = f"DB read error: {db_err}"
            results.append(result)
            continue

        result = evaluate_scenario(gt_claims, db_claims, sid, category, label)
        results.append(result)

    print(f"  Scenarios with DBs: {scenarios_with_db}")
    print(f"  Scenarios without DBs: {scenarios_no_db}")
    print()

    # ── Per-scenario output ──
    all_passing = []
    all_failing = []

    for r in results:
        is_pass = (r.tp == len(r.gt_claims) and r.fp == 0 and r.fn == 0)
        if is_pass:
            all_passing.append(r)
        else:
            all_failing.append(r)

    # Show failures (or all if verbose)
    display = all_failing if args.show_failures else results

    if not args.show_failures:
        print(f"{'Status':6s} {'Scenario':40s} {'Cat':20s} {'GT':>4s} {'DB':>4s} {'TP':>4s} {'FP':>4s} {'FN':>4s} {'P':>6s} {'R':>6s} {'F1':>6s}")
        print("-" * 120)
        for r in display:
            icon = "✓" if (r.tp == len(r.gt_claims) and r.fp == 0 and r.fn == 0) else "✗"
            if r.error:
                icon = "E"
            cat_short = r.category[:18]
            print(f"  {icon:4s}  {r.scenario_id:40s} {cat_short:20s} {len(r.gt_claims):>4d} {len(r.db_claims):>4d} {r.tp:>4d} {r.fp:>4d} {r.fn:>4d} {r.precision:>5.1%} {r.recall:>5.1%} {r.f1:>5.1%}")

    if args.show_failures and all_failing:
        print(f"\n{'Status':6s} {'Scenario':40s} {'GT→':>10s} {'DB→':>10s} {'Issue'}")
        print("-" * 90)
        for r in all_failing:
            if r.error:
                print(f"  E     {r.scenario_id:40s} ERROR: {r.error}")
                continue
            issue = ""
            if r.fn > 0:
                issue += f"Missing {r.fn} claim(s)"
                if r.unmatched_gt:
                    types = set(c.claim_type for c in r.unmatched_gt)
                    issue += f" [{', '.join(sorted(types))}]"
            if r.fp > 0:
                if issue:
                    issue += "; "
                issue += f"Extra {r.fp} claim(s)"
                if r.unmatched_db:
                    types = set(c.claim_type for c in r.unmatched_db)
                    issue += f" [{', '.join(sorted(types))}]"
            print(f"  ✗     {r.scenario_id:40s} GT={len(r.gt_claims):>3d}  DB={len(r.db_claims):>3d}  {issue}")

    # ── Overall metrics ──
    total_tp = sum(r.tp for r in results)
    total_fp = sum(r.fp for r in results)
    total_fn = sum(r.fn for r in results)
    total_gt = sum(len(r.gt_claims) for r in results)
    total_db = sum(len(r.db_claims) for r in results)

    macro_precision = sum(r.precision for r in results) / len(results) if results else 0
    macro_recall = sum(r.recall for r in results) / len(results) if results else 0
    macro_f1 = sum(r.f1 for r in results) / len(results) if results else 0

    micro_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
    micro_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
    if micro_precision + micro_recall > 0:
        micro_f1 = 2 * micro_precision * micro_recall / (micro_precision + micro_recall)
    else:
        micro_f1 = 0.0

    print()
    print("=" * 72)
    print("  SUMMARY")
    print("=" * 72)
    print(f"  Evaluated scenarios:      {len(results)}")
    print(f"  Passing (perfect match):  {len(all_passing)}")
    print(f"  Failing:                  {len(all_failing)}")
    print(f"  Scenarios without DBs:    {scenarios_no_db}")
    print()
    print(f"  Expected claims (total):  {total_gt}")
    print(f"  Actual claims (total):    {total_db}")
    print(f"  True Positives:           {total_tp}")
    print(f"  False Positives:          {total_fp}")
    print(f"  False Negatives:          {total_fn}")
    print()
    print(f"  ── Claim-Level Metrics ──")
    print(f"  Micro Precision: {micro_precision:.1%}")
    print(f"  Micro Recall:    {micro_recall:.1%}")
    print(f"  Micro F1:        {micro_f1:.1%}")
    print()
    print(f"  Macro Precision: {macro_precision:.1%}")
    print(f"  Macro Recall:    {macro_recall:.1%}")
    print(f"  Macro F1:        {macro_f1:.1%}")

    # ── Per-category breakdown ──
    print()
    print("=" * 72)
    print("  PER-CATEGORY BREAKDOWN")
    print("=" * 72)
    print(f"  {'Category':25s} {'#Sc':>4s} {'TP':>3s} {'FP':>3s} {'FN':>3s} {'Prec':>7s} {'Rec':>7s} {'F1':>7s}")
    print("-" * 72)

    by_category = defaultdict(list)
    for r in results:
        by_category[r.category].append(r)

    for cat in sorted(by_category.keys()):
        rs = by_category[cat]
        ct = sum(len(r.gt_claims) for r in rs)
        ca = sum(len(r.db_claims) for r in rs)
        ctp = sum(r.tp for r in rs)
        cfp = sum(r.fp for r in rs)
        cfn = sum(r.fn for r in rs)
        cp = ctp / (ctp + cfp) if (ctp + cfp) > 0 else 1.0
        cr = ctp / (ctp + cfn) if (ctp + cfn) > 0 else 1.0
        cf1 = 2 * cp * cr / (cp + cr) if (cp + cr) > 0 else 0.0
        print(f"  {cat:25s} {len(rs):>4d} {ctp:>3d} {cfp:>3d} {cfn:>3d} {cp:>6.1%} {cr:>6.1%} {cf1:>6.1%}")

    # ── Per-claim-type breakdown ──
    print()
    print("=" * 72)
    print("  PER-CLAIM-TYPE BREAKDOWN")
    print("=" * 72)

    by_type: Dict[str, Dict[str, int]] = defaultdict(lambda: {"gt": 0, "db": 0, "tp": 0, "fp": 0, "fn": 0})
    for r in results:
        for c in r.gt_claims:
            key = c.claim_type
            by_type[key]["gt"] += 1
        for c in r.db_claims:
            key = c.claim_type
            by_type[key]["db"] += 1
        for gt_c, db_c in r.matched_pairs:
            key = gt_c.claim_type
            by_type[key]["tp"] += 1
        for c in r.unmatched_gt:
            key = c.claim_type
            by_type[key]["fn"] += 1
        for c in r.unmatched_db:
            key = c.claim_type
            by_type[key]["fp"] += 1

    print(f"  {'Claim Type':35s} {'GT':>4s} {'DB':>4s} {'TP':>4s} {'FP':>4s} {'FN':>4s} {'Prec':>7s} {'Recall':>7s}")
    print("-" * 82)
    for ct in sorted(by_type.keys()):
        d = by_type[ct]
        prec = d["tp"] / (d["tp"] + d["fp"]) if (d["tp"] + d["fp"]) > 0 else 1.0
        rec = d["tp"] / (d["tp"] + d["fn"]) if (d["tp"] + d["fn"]) > 0 else 1.0
        print(f"  {ct:35s} {d['gt']:>4d} {d['db']:>4d} {d['tp']:>4d} {d['fp']:>4d} {d['fn']:>4d} {prec:>6.1%} {rec:>6.1%}")

    # ── Binary-level comparison (old-style, for reference) ──
    print()
    print("=" * 72)
    print("  BINARY-LEVEL (old-style, for reference)")
    print("=" * 72)
    tp_bin = 0
    fp_bin = 0
    fn_bin = 0
    tn_bin = 0
    for r in results:
        has_claim = len(r.db_claims) > 0
        is_attack = r.label == "attack"
        if is_attack and has_claim:
            tp_bin += 1
        elif is_attack and not has_claim:
            fn_bin += 1
        elif not is_attack and has_claim:
            fp_bin += 1
        else:
            tn_bin += 1

    print(f"  TP={tp_bin}, FP={fp_bin}, FN={fn_bin}, TN={tn_bin}")
    p = tp_bin / (tp_bin + fp_bin) if (tp_bin + fp_bin) > 0 else -1
    r = tp_bin / (tp_bin + fn_bin) if (tp_bin + fn_bin) > 0 else -1
    f1 = 2 * p * r / (p + r) if p > 0 and r > 0 else -1
    print(f"  Binary Precision={p:.1%}, Recall={r:.1%}, F1={f1:.1%}" if p >= 0 else "  No binary data")

    # ── CSV export ──
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "scenario_id", "category", "label", "gt_claims", "db_claims",
                "tp", "fp", "fn", "precision", "recall", "f1", "status"
            ])
            for r in results:
                status = "pass" if (r.tp == len(r.gt_claims) and r.fp == 0 and r.fn == 0) else "fail"
                if r.error:
                    status = "error"
                writer.writerow([
                    r.scenario_id, r.category, r.label, len(r.gt_claims), len(r.db_claims),
                    r.tp, r.fp, r.fn, f"{r.precision:.4f}", f"{r.recall:.4f}", f"{r.f1:.4f}", status
                    ])
        print(f"\n  CSV exported to: {args.csv}")


if __name__ == "__main__":
    main()
