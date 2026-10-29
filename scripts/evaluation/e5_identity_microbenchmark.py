# SPDX-License-Identifier: Apache-2.0 OR MIT
"""E5 identity microbenchmark evaluator.

Consumes the JSONL produced by `collector/examples/identity_microbenchmark.rs`
(one line per trace, events carry a pre-declared truth actor and the resolver's
predicted actor) and reports the attribution metrics for each synthetic topology:

* pairwise precision/recall/F1 over event pairs (human-agreement definition),
* B-cubed precision/recall/F1,
* merged predicted clusters / split truth clusters,
* unattributed event rate (a resolver that refuses to merge is never scored as
  a wrong merge).

The ground truth is declared by the generator from the specification table
(`docs/paper/身份解析.md` T1–T6); this script never edits it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compare.tools.identity_metrics import (
    IdentityPartition,
    evaluate_identity_partition,
)

TRACE_SCHEMA = "mtagentlens.e5-identity-trace/1"
SUMMARY_SCHEMA = "mtagentlens.e5-identity-summary/1"

EXPECTED = {
    "T1_single_agent": "merge tool children into one agent",
    "T2_same_process_sequential": "split at the conversation switch",
    "T3_same_process_concurrent": "split or mark unattributed, never silently merge",
    "T4_connection_reuse": "keep agents separate; a shared connection is not identity",
    "T5_subagent_fork": "split at exec with an independent TLS session",
    "T6_delegated_handoff": "keep two agents despite shared context",
}


def load_traces(path: Path) -> list[dict]:
    traces = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{number}: {error}") from error
        if payload.get("schema") != TRACE_SCHEMA:
            raise ValueError(f"{path}:{number}: unexpected schema")
        traces.append(payload)
    if not traces:
        raise ValueError("no traces found")
    return traces


def evaluate_trace(trace: dict) -> dict:
    """Compare truth and prediction for one trace; unattributed events stay singletons."""
    events = trace["events"]
    if not events:
        raise ValueError(f"{trace['trace_id']}: trace has no events")
    truth: dict[str, str] = {}
    predicted: dict[str, str] = {}
    unattributed = 0
    for event in events:
        event_id = event["event_id"]
        truth[event_id] = event["truth_actor"]
        actor = event["predicted_actor"]
        if actor is None:
            unattributed += 1
            predicted[event_id] = f"unattributed:{event_id}"
        else:
            predicted[event_id] = actor
    digest = hashlib.sha256(f"e5:{trace['topology']}".encode()).hexdigest()
    scores = evaluate_identity_partition(
        IdentityPartition(run_id=trace["trace_id"], evidence_bundle_sha256=digest,
                          event_to_actor=truth),
        IdentityPartition(run_id=trace["trace_id"], evidence_bundle_sha256=digest,
                          event_to_actor=predicted),
    )
    return {
        "trace_id": trace["trace_id"],
        "topology": trace["topology"],
        "events": len(events),
        "unattributed_events": unattributed,
        "subagent_boundaries": trace.get("subagent_boundaries", 0),
        "metrics": scores,
    }


def _mean(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def summarize(rows: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["topology"]].append(row)
    topologies = []
    for topology in sorted(grouped):
        rows_for_topology = grouped[topology]
        metrics = [row["metrics"] for row in rows_for_topology]
        events = sum(row["events"] for row in rows_for_topology)
        unattributed = sum(row["unattributed_events"] for row in rows_for_topology)
        topologies.append({
            "topology": topology,
            "expected": EXPECTED.get(topology),
            "traces": len(metrics),
            "pairwise_precision": _mean([m["pairwise"]["precision"] for m in metrics]),
            "pairwise_recall": _mean([m["pairwise"]["recall"] for m in metrics]),
            "pairwise_f1": _mean([m["pairwise"]["f1"] for m in metrics]),
            "b3_precision": _mean([m["b_cubed"]["precision"] for m in metrics]),
            "b3_recall": _mean([m["b_cubed"]["recall"] for m in metrics]),
            "b3_f1": _mean([m["b_cubed"]["f1"] for m in metrics]),
            "merged_predicted_clusters": sum(m["merged_predicted_clusters"] for m in metrics),
            "split_truth_clusters": sum(m["split_truth_clusters"] for m in metrics),
            "wrong_merge_rate": (
                sum(m["merged_predicted_clusters"] for m in metrics) / len(metrics)
                if metrics else None),
            "events": events,
            "unattributed_events": unattributed,
            "unattributed_rate": (unattributed / events) if events else None,
        })
    total_events = sum(row["events"] for row in rows)
    unattributed = sum(row["unattributed_events"] for row in rows)
    return {
        "schema": SUMMARY_SCHEMA,
        "traces": len(rows),
        "events": total_events,
        "unattributed_events": unattributed,
        "unattributed_rate": (unattributed / total_events) if total_events else None,
        "topologies": topologies,
        "note": ("Synthetic microbenchmark of the identity resolver; ground truth is the "
                 "specification table, not observed agent behaviour. Unattributed events are "
                 "scored as singleton clusters so a refusal to merge is never a false merge."),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--details", action="store_true",
                        help="include per-trace rows in the output")
    args = parser.parse_args(argv)
    try:
        traces = load_traces(args.traces)
        rows = [evaluate_trace(trace) for trace in traces]
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    summary = summarize(rows)
    if args.details:
        summary["traces_detail"] = rows
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps({key: summary[key] for key in
                      ("traces", "events", "unattributed_rate")}, ensure_ascii=False))
    for topology in summary["topologies"]:
        f1 = topology["pairwise_f1"]
        f1_text = "n/a" if f1 is None else f"{f1:.3f}"
        print(f"  {topology['topology']}: pair F1={f1_text} "
              f"merged={topology['merged_predicted_clusters']} "
              f"split={topology['split_truth_clusters']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
