"""Read and validate the team manifest.

The manifest is the single source of truth for stage order, actor ids and the
handoff file map. Keeping the loader here means tests and the runbook use the
same view of the protocol.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_manifest(path: str | Path) -> dict:
    """Load a manifest and check the invariants the rotation depends on."""
    with Path(path).open(encoding="utf-8") as handle:
        manifest = json.load(handle)

    stages = manifest.get("stages")
    if stages != ["analyst", "operator", "auditor"]:
        raise ValueError(f"unexpected stage order: {stages!r}")

    actor_ids = manifest.get("actor_ids", {})
    missing = [stage for stage in stages if stage not in actor_ids]
    if missing:
        raise ValueError(f"manifest is missing actor ids for: {missing}")

    handoff = manifest.get("handoff", {})
    missing = [stage for stage in stages if stage not in handoff]
    if missing:
        raise ValueError(f"manifest is missing handoff records for: {missing}")

    return manifest


def stage_records(manifest: dict) -> dict[str, str]:
    """Return workspace-relative handoff paths keyed by stage."""
    return dict(manifest["handoff"])
