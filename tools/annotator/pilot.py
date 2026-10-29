# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Batch-import verified native ledgers into a pilot annotation store.

The pilot needs one store shared by the three independent annotators and the
adjudicator, with every task bound to the same codebook version. This helper
imports all ledgers first (any failure removes the bundles it created and
leaves no store) and only then registers the tasks.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from tools.annotator.records import RecordStore
from tools.annotator.runner_import import import_runner_bundle

SCHEMA = "mtagentlens.pilot-preparation/v1"


def prepare_pilot(store_path, release_root, ledgers, codebook_version, *,
                  bundle_root=None) -> dict:
    """Import ledgers and register one unannotated task per frozen bundle."""
    if not isinstance(codebook_version, str) or not codebook_version.strip():
        raise ValueError("codebook version must be a non-empty string")
    paths = [Path(ledger) for ledger in ledgers]
    if not paths:
        raise ValueError("at least one native ledger is required")
    resolved = [path.resolve() for path in paths]
    if len(set(resolved)) != len(resolved):
        raise ValueError("duplicate native ledger")
    store_path = Path(store_path)
    release_root = Path(release_root)
    existing_tasks = 0
    if store_path.exists() and store_path.stat().st_size:
        existing = RecordStore(store_path).export()["tasks"]
        existing_tasks = len(existing)
        versions = {task["binding"]["codebook_version"] for task in existing}
        if versions and versions != {codebook_version}:
            raise ValueError(
                f"store already contains codebook {sorted(versions)!r}; "
                f"cannot add {codebook_version!r} tasks")
    root = Path(bundle_root) if bundle_root is not None else store_path.parent / "pilot-bundles"
    root.mkdir(parents=True, exist_ok=True)
    used = []
    for path in root.glob("run-*"):
        suffix = path.name.removeprefix("run-")
        if suffix.isdigit():
            used.append(int(suffix))
    next_index = max(used) + 1 if used else 0

    created: list[Path] = []
    try:
        for offset, ledger in enumerate(paths):
            out = root / f"run-{next_index + offset:04d}"
            if out.exists() or out.is_symlink():
                raise ValueError(f"bundle directory already exists: {out}")
            import_runner_bundle(out, release_root=release_root, ledger=ledger,
                                 codebook_version=codebook_version)
            created.append(out)
    except BaseException:
        # Imports are all-or-nothing; pre-existing directories are never touched.
        for path in created:
            shutil.rmtree(path, ignore_errors=True)
        raise

    store = RecordStore(store_path)
    tasks = [store.register_bundle(path) for path in created]
    return {
        "schema": SCHEMA,
        "codebook_version": codebook_version,
        "tasks": tasks,
        "bundles": [str(path) for path in created],
        "registered_total": existing_tasks + len(tasks),
        "paper_metrics_available": False,
        "note": ("annotation tasks only; no human labels. The codebook draft must be "
                 "approved by the team before a formal labeling round."),
    }
