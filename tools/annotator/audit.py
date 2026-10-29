# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Audit whether a store's tasks have enough evidence for human annotation.

This reports evidence presence only. It never counts model proposals as human
labels and never reports agreement or paper metrics.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from tools.annotator.records import RecordStore

SCHEMA = "mtagentlens.annotation-readiness/v1"


def audit_store(store_path: Path, *, codebook_version: str | None = None) -> dict:
    store = RecordStore(Path(store_path))
    tasks = []
    for task in store.export()["tasks"]:
        task_id = task["task_id"]
        bundle = task["evidence_manifest"]
        sources = set(bundle["sources"])
        counts = Counter(event["source_id"] for event in bundle["events"])
        collector_actions = sum(
            1 for event in bundle["events"]
            if event["source_id"] == "collector"
            and "actions" in (event.get("derivation") or ""))
        actual_codebook = bundle["binding"].get("codebook_version")
        checks = {
            "runner": "runner" in sources,
            "task_brief": "task_brief" in sources,
            "trajectory": "trajectory" in sources,
            "collector_actions": collector_actions > 0,
            "codebook_matches": (codebook_version is None
                                 or actual_codebook == codebook_version),
        }
        blockers = []
        if not checks["runner"]:
            blockers.append("missing runner ledger evidence")
        if not checks["task_brief"]:
            blockers.append("missing task brief evidence")
        if not checks["trajectory"]:
            blockers.append("missing assistant trajectory evidence")
        if not checks["collector_actions"]:
            blockers.append("missing collector action evidence")
        if not checks["codebook_matches"]:
            blockers.append(f"task codebook {actual_codebook!r} does not match {codebook_version!r}")
        proposal = store.latest_model_proposal(task_id)
        attestations = store.list_attestations(task_id)
        human = [record for record in task["revisions"] if record.get("method") == "human"]
        tasks.append({
            "task_id": task_id,
            "codebook_version": actual_codebook,
            "evidence_counts": dict(sorted(counts.items())),
            "collector_action_events": collector_actions,
            "checks": checks,
            "blockers": blockers,
            "model_proposal": proposal is not None,
            "model_proposal_revision": proposal.get("revision_id") if proposal else None,
            "human_annotations": len(human),
            "attestations": len(attestations),
        })
    ready = [task for task in tasks if not task["blockers"]]
    return {
        "schema": SCHEMA,
        "codebook_version": codebook_version,
        "tasks": tasks,
        "summary": {
            "tasks": len(tasks),
            "ready_tasks": len(ready),
            "blocked_tasks": len(tasks) - len(ready),
            "model_proposals": sum(1 for task in tasks if task["model_proposal"]),
            "human_annotations": sum(task["human_annotations"] for task in tasks),
            "attestations": sum(task["attestations"] for task in tasks),
            "ready_for_independent_annotation": len(tasks) > 0 and len(ready) == len(tasks),
        },
        "paper_metrics_available": False,
        "note": ("Evidence sufficiency only. Model proposals are not human labels; "
                 "agreement and paper metrics require independent human annotations."),
    }


def write_audit(path: Path, payload: dict) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
