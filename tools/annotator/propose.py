# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Blinded task export and labelled model-proposal revisions.

A model proposal is a draft aid, never a human label: it is stored with
``method="model_proposal"``, excluded from agreement metrics, and cannot be
used as the source of an adjudication review (see records.py).
"""

from __future__ import annotations

from pathlib import Path

from tools.annotator.blinding import binding_identifiers, blind_identifiers
from tools.annotator.records import RecordStore

SCHEMA = "mtagentlens.proposal-task-export/v1"


def export_task(store: RecordStore, task_id: str, *, limit: int | None = None) -> dict:
    """Return blinded evidence for one task without any annotations."""
    data = store.read_task(task_id, annotators=())
    bundle = data["bundle"]
    binding = bundle["binding"]
    identifiers = binding_identifiers(binding)
    events = []
    for evidence in store.read_evidence_many(task_id, limit=limit):
        event = evidence["event"]
        text, redactions = blind_identifiers(evidence["bytes"], identifiers)
        events.append({
            "event_id": event["event_id"],
            "record_id": event.get("record_id"),
            "timestamp": event.get("timestamp"),
            "clock_domain": event.get("clock_domain"),
            "derivation": event.get("derivation"),
            "source_kind": evidence["source_kind"],
            "text": text,
            "redactions": redactions,
            "byte_length": evidence["byte_length"],
            "shown_bytes": len(evidence["bytes"]),
            "truncated": len(evidence["bytes"]) < evidence["byte_length"],
        })
    return {
        "schema": SCHEMA,
        "task_id": task_id,
        "evidence_bundle_hash": bundle.get("evidence_bundle_hash"),
        "codebook_version": binding.get("codebook_version"),
        "actors": list(bundle["actors"]),
        "events": events,
        "paper_metrics_available": False,
        "note": ("Blinded evidence for proposal drafting only; labels remain "
                 "model suggestions until a human review is recorded."),
    }


def save_proposal(store: RecordStore, task_id: str, annotation: dict, *,
                  annotator: str, codebook_version: str | None = None,
                  expected_revision: str | None = None) -> dict:
    """Validate and append a model proposal under the explicit method."""
    if not isinstance(annotator, str) or not annotator.strip():
        raise ValueError("annotator must be a non-empty identifier")
    data = store.read_task(task_id, annotators=())
    actual = data["bundle"]["binding"].get("codebook_version")
    if codebook_version is not None and actual != codebook_version:
        raise ValueError(
            f"task uses codebook {actual!r}, not the requested {codebook_version!r}")
    return store.save(task_id, annotator, annotation, method="model_proposal",
                      expected_revision=expected_revision)


def write_task_export(path: Path, payload: dict) -> None:
    Path(path).write_text(
        __import__("json").dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
