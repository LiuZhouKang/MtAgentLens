#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Descriptive agreement among independent *agent* annotators.

Agent records are model proposals, not human labels. This report is explicitly
labelled `agreement_kind=agent` and must never be cited as inter-annotator
agreement for human annotators or used in paper metrics.

Usage:
    python3 scripts/evaluation/agent_agreement.py \
        --export annotations.json \
        --annotators lzk-agent thy-agent ljy-agent --out agent-agreement.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluation.gt_agreement import fleiss_kappa, percent_agreement

SCHEMA = "mtagentlens.agent-agreement/v1"
FIELDS = ("attempted", "executed", "succeeded", "identities", "roots")


def _value(annotation: dict, field: str):
    if field in ("attempted", "executed", "succeeded"):
        judgment = annotation.get(field)
        return judgment.get("value") if isinstance(judgment, dict) else None
    if field == "identities":
        identities = annotation.get("identities")
        return "|".join(f"{event}:{actor if actor is not None else 'unknown'}"
                        for event, actor in sorted(identities.items())) \
            if isinstance(identities, dict) else None
    roots = annotation.get("roots")
    actors = roots.get("actors") if isinstance(roots, dict) else None
    return "<unknown>" if actors is None else "|".join(sorted(actors))


def agent_agreement(export: dict, annotators: list[str]) -> dict:
    if export.get("schema") != "mtagentlens.annotation-export/v2":
        raise ValueError("unsupported annotation export schema")
    if export.get("paper_metrics_available") is not False:
        raise ValueError("agent agreement requires an unreleased annotation export")
    rows = {field: [] for field in FIELDS}
    task_rows = []
    for task in export.get("tasks", []):
        latest = {}
        for revision in task.get("revisions", []):
            if revision.get("method") == "model_proposal" and revision.get("annotator") in annotators:
                latest[revision["annotator"]] = revision["annotation"]
        if set(latest) != set(annotators):
            continue
        values = {field: [_value(latest[name], field) for name in annotators]
                  for field in FIELDS}
        for field in FIELDS:
            rows[field].append(values[field])
        task_rows.append({"task_id": task["task_id"], "values": values})
    report = {
        "schema": SCHEMA,
        "agreement_kind": "agent",
        "annotators": list(annotators),
        "tasks": task_rows,
        "fields": {},
        "paper_metrics_available": False,
        "note": ("Descriptive agreement among AI agent runs, not human "
                 "inter-annotator agreement; κ undefined here is not a pass."),
    }
    for field in FIELDS:
        report["fields"][field] = {
            "n_tasks": len(rows[field]),
            "percent_agreement": percent_agreement(rows[field]),
            "fleiss_kappa": fleiss_kappa(rows[field]),
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", required=True, type=Path)
    parser.add_argument("--annotators", nargs="+", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = agent_agreement(json.loads(args.export.read_text(encoding="utf-8")),
                            args.annotators)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(json.dumps({"schema": report["schema"], "out": str(args.out),
                          "fields": report["fields"]}, ensure_ascii=False))
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
