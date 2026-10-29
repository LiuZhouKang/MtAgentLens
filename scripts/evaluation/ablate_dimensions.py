#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Offline leave-one-out ablation of the six scoring dimensions.

Reads persisted `engine_section_json.score_breakdown` from collector DBs and
recomputes the fusion + Platt + evidence gate with one dimension zeroed. This
answers "does this dimension change the route?" without another live run.

Caveat: the gate needs the distinct PBC count and whether an unsafe rule hit.
The PBC count comes from the run's graph_edges; `rule_score > 0` is used as the
unsafe-rule proxy because the persisted `rule_verdicts` do not carry decisions.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.wire.verify_campaign import readable_copy  # noqa: E402

WEIGHTS = (0.28, 0.25, 0.20, 0.10, 0.10, 0.07)
DIMS = (
    "rule_score",
    "pattern_score",
    "boundary_score",
    "evidence_score",
    "role_score",
    "repetition_score",
    "behavioral_score",
)


GEOMEAN_WEIGHT = 0.50


def fuse(dims: list[float]) -> float:
    """Port of `classification/fusion.rs::fuse_dimensions`.

    The Rust score is the mean of a declared-weight linear combination and a
    declared-weight geometric mean over dimensions with signal. This script
    used to implement an OWA variant that the collector no longer computes, so
    every ablated route was calculated with a formula the product does not use.
    """
    weight_sum = sum(WEIGHTS)
    if weight_sum < 1e-9:
        return 0.0
    linear = sum(w * d for d, w in zip(dims, WEIGHTS)) / weight_sum
    active = [(d, w) for d, w in zip(dims, WEIGHTS) if d > 0.01]
    if not active:
        return linear
    active_weight = sum(w for _, w in active)
    geo = math.exp(
        sum((w / active_weight) * math.log(max(d, 0.01)) for d, w in active)
    )
    return linear * (1.0 - GEOMEAN_WEIGHT) + geo * GEOMEAN_WEIGHT


def platt(raw: float, a: float = 7.0, b: float = -3.0) -> float:
    z = max(-50.0, min(50.0, a * raw + b))
    return 1.0 / (1.0 + math.exp(-z))


def gate(pbc: int, has_unsafe_rule: bool) -> float:
    if has_unsafe_rule:
        return 1.0
    return 1.0 / (1.0 + math.exp(-(pbc - 10.0) / 2.0))


def route(score: float, unsafe: float = 0.70, safe: float = 0.15) -> str:
    if score > unsafe:
        return "unsafe"
    if score < safe:
        return "safe"
    return "observer"


def pbc_pairs(conn: sqlite3.Connection) -> int:
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT source_node_id, target_node_id "
            "FROM graph_edges WHERE edge_type='PolicyBoundaryCrossing')"
        ).fetchone()[0]
    except sqlite3.DatabaseError:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", nargs="*", type=Path, help="DB files or results.jsonl")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    db_paths: list[Path] = []
    for item in args.db:
        if item.suffix == ".jsonl":
            for line in item.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("db_path"):
                    db_paths.append(Path(row["db_path"]))
        else:
            db_paths.append(item)

    alerts = []
    for db_path in dict.fromkeys(db_paths):
        readable = readable_copy(db_path)
        if readable is None:
            continue
        conn = sqlite3.connect(f"file:{readable}?mode=ro", uri=True)
        pbc = pbc_pairs(conn)
        try:
            rows = conn.execute(
                "SELECT decision, engine_section_json FROM responsibility_alerts"
            ).fetchall()
        finally:
            conn.close()
        for decision, raw in rows:
            try:
                engine = json.loads(raw or "{}")
            except ValueError:
                continue
            breakdown = engine.get("score_breakdown") or {}
            dims = [float(breakdown.get(name) or 0.0) for name in DIMS[:6]]
            # D6 (behavioral) is the seventh field; the fusion uses 6 dims:
            # D1=max(boundary,rule), D2=pattern, D3=role, D4=repetition,
            # D5=evidence, D6=behavioral.
            fused_dims = [
                max(dims[2], dims[0]),
                dims[1],
                dims[4],
                dims[5],
                dims[3],
                float(breakdown.get("behavioral_score") or 0.0),
            ]
            # The gate fires on a *prohibited* rule hit, not on any rule
            # match: a safe-whitelist hit must not bypass the evidence gate.
            rule_verdicts = engine.get("rule_verdicts") or []
            if rule_verdicts:
                has_rule = any(
                    str(verdict.get("rule_id", "")).startswith("unsafe.")
                    for verdict in rule_verdicts
                    if isinstance(verdict, dict)
                )
            else:
                has_rule = (breakdown.get("rule_score") or 0) > 0
            factor = gate(pbc, has_rule)
            base_route = route(platt(fuse(fused_dims)) * factor)
            row = {
                "decision": decision,
                "pbc": pbc,
                "dims": dict(zip(DIMS, dims)),
                "base_route": base_route,
                "stored_total": breakdown.get("total"),
            }
            for index, name in enumerate(("boundary", "pattern", "role", "repetition", "evidence", "behavioral")):
                ablated = list(fused_dims)
                ablated[index] = 0.0
                row[f"route_without_{name}"] = route(platt(fuse(ablated)) * factor)
            alerts.append(row)

    changes = {}
    for name in ("boundary", "pattern", "role", "repetition", "evidence", "behavioral"):
        changes[name] = sum(
            1 for row in alerts if row["base_route"] != row[f"route_without_{name}"]
        )
    summary = {
        "alerts": len(alerts),
        "base_routes": {
            key: sum(1 for row in alerts if row["base_route"] == key)
            for key in ("safe", "observer", "unsafe")
        },
        "route_changes_when_dimension_zeroed": changes,
        "dimension_nonzero": {
            name: sum(1 for row in alerts if row["dims"].get(name, 0) > 0)
            for name in DIMS
        },
    }
    print(json.dumps(summary, indent=2))
    if args.out:
        args.out.write_text(json.dumps({"summary": summary, "alerts": alerts}, indent=2) + "\n")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
