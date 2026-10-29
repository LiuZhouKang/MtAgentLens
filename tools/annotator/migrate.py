# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Re-version frozen evidence under a new annotation codebook.

A codebook version is part of the run binding, so upgrading it legitimately changes
every evidence-bundle hash. What must *not* change is the evidence itself: this module
re-freezes a bundle through the audited builder, which re-verifies the release, every
source checksum and every record byte range, then writes a new bundle whose only
difference is ``binding.codebook_version``.

Annotations are then rebound to the new tasks revision by revision, in their original
order, so parent chains and adjudication references stay auditable. Two things are
deliberately refused rather than carried across:

* human / controlled-execution revisions — those labels were produced under the old
  codebook and must be re-annotated, never silently re-versioned;
* review attestations — a typed statement names the codebook the reviewer actually read.

The migration manifest records the old/new task and revision ids, so the lineage from a
published dataset release back to the pre-migration store is mechanical, not narrative.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from tools.annotator.evidence import build_bundle, load_bundle
from tools.annotator.records import RecordStore

MIGRATION_SCHEMA = "mtagentlens.codebook-migration/v1"
_CARRIED_METHODS = {"model_proposal"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_version(version: object, *, field: str) -> str:
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"{field} must be a non-empty codebook version")
    return version


def reversion_bundle(source: Path, out: Path, codebook_version: str, *,
                     release_root: Path) -> dict:
    """Copy ``source`` into ``out`` bound to ``codebook_version``; evidence bytes stay put."""
    source, out = Path(source), Path(out)
    target = _require_version(codebook_version, field="codebook_version")
    manifest = load_bundle(source)
    if manifest["binding"]["codebook_version"] == target:
        raise ValueError(f"bundle is already bound to codebook {target!r}")
    if out.exists():
        raise ValueError("migration destination already exists; migrated bundles are immutable")
    binding = dict(manifest["binding"], codebook_version=target)
    sources = {
        source_id: {"path": source / entry["path"], "kind": entry["kind"],
                    "producer": entry["producer"]}
        for source_id, entry in manifest["sources"].items()
    }
    migrated = build_bundle(out, release_root=Path(release_root), binding=binding,
                            sources=sources, actors=manifest["actors"],
                            events=manifest["events"])
    # The builder re-derives these from bytes; assert the re-freeze moved no evidence.
    for source_id, entry in manifest["sources"].items():
        if migrated["sources"][source_id]["sha256"] != entry["sha256"]:
            shutil.rmtree(out, ignore_errors=True)
            raise ValueError("re-frozen source bytes differ from the frozen bundle")
    if migrated["events"] != manifest["events"] or migrated["actors"] != manifest["actors"]:
        shutil.rmtree(out, ignore_errors=True)
        raise ValueError("re-frozen evidence inventory differs from the frozen bundle")
    return migrated


def write_migration_manifest(path: Path, payload: dict) -> None:
    """Publish a manifest atomically and refuse to overwrite an earlier one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".migration-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    except FileExistsError:
        raise ValueError("migration manifest already exists; refusing to overwrite") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _refuse_ununiform_versions(tasks: list[dict]) -> str:
    versions = {task["binding"]["codebook_version"] for task in tasks}
    if len(versions) != 1:
        raise ValueError(f"source store mixes codebook versions {sorted(versions)}; "
                         "migrate one version at a time")
    return versions.pop()


def _revision_methods(tasks: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for task in tasks:
        for revision in task["revisions"]:
            counts[revision["method"]] = counts.get(revision["method"], 0) + 1
    return counts


def _refuse_uncarriable_records(tasks: list[dict], attestations: list[dict],
                               carry_human: bool) -> dict[str, int]:
    if attestations:
        raise ValueError("review attestations block migration: a typed statement names the "
                         "codebook the reviewer read, so it must be re-recorded, not carried")
    counts = _revision_methods(tasks)
    unsupported = sorted(set(counts) - {"model_proposal", "human", "controlled_execution"})
    if unsupported:
        raise ValueError(f"unsupported revision methods {unsupported}")
    human = {method: count for method, count in counts.items() if method != "model_proposal"}
    if human and not carry_human:
        listed = ", ".join(f"{method}={count}" for method, count in sorted(human.items()))
        raise ValueError(f"human or controlled-execution revisions ({listed}) cannot be carried "
                         "across codebooks without explicit consent; re-annotate those tasks")
    return human


def _replay_revisions(store: RecordStore, task_id: str, revisions: list[dict],
                      revision_map: dict[str, str]) -> None:
    """Replay one task's revisions in order, preserving each annotator's parent chain."""
    heads: dict[str, str] = {}
    for revision in revisions:
        review = revision.get("review")
        if review is not None:
            rewritten = {}
            for rater, old_id in review["reviewed_revisions"].items():
                if old_id not in revision_map:
                    raise ValueError("adjudication references a revision that was never migrated")
                rewritten[rater] = revision_map[old_id]
            review = {"reviewed_revisions": rewritten, "rationale": review["rationale"]}
        record = store.save(task_id, revision["annotator"], revision["annotation"],
                            method=revision["method"],
                            expected_revision=heads.get(revision["annotator"]), review=review)
        revision_map[revision["revision_id"]] = record["revision_id"]
        heads[revision["annotator"]] = record["revision_id"]


def migrate_store(*, source_store: Path, out_store: Path, out_bundles: Path,
                  codebook_version: str, release_root: Path, note: str | None = None,
                  carry_human: bool = False) -> dict:
    """Rebind a whole store to ``codebook_version``; returns the migration manifest."""
    source_store, out_store, out_bundles = (Path(source_store), Path(out_store),
                                            Path(out_bundles))
    target = _require_version(codebook_version, field="codebook_version")
    if source_store.resolve() == out_store.resolve():
        raise ValueError("migration cannot rewrite the source store in place")
    if out_store.exists():
        raise ValueError("migrated store already exists; refusing to overwrite")
    if out_bundles.exists():
        raise ValueError("migrated bundle root already exists; refusing to overwrite")

    source = RecordStore(source_store)
    export = source.export()
    tasks = export["tasks"]
    if not tasks:
        raise ValueError("source store has no annotation tasks")
    version_from = _refuse_ununiform_versions(tasks)
    if version_from == target:
        raise ValueError(f"store is already bound to codebook {target!r}")
    human_carried = _refuse_uncarriable_records(
        tasks, source.list_attestations(), carry_human)

    source_digest = _sha256_file(source_store)
    try:
        out_bundles.parent.mkdir(parents=True, exist_ok=True)
        out_bundles.mkdir()
        out_store.parent.mkdir(parents=True, exist_ok=True)
        migrated_store = RecordStore(out_store)
        manifest_tasks = []
        counts = {"tasks": 0, "revisions": 0, "model_proposals": 0}
        for index, task in enumerate(sorted(tasks, key=lambda row: row["task_id"])):
            old_bundle = source.bundle_path(task["task_id"])
            new_bundle = out_bundles / f"run-{index:04d}"
            frozen = reversion_bundle(old_bundle, new_bundle, target, release_root=release_root)
            new_task = migrated_store.register_bundle(new_bundle)
            revision_map: dict[str, str] = {}
            _replay_revisions(migrated_store, new_task, task["revisions"], revision_map)
            counts["tasks"] += 1
            counts["revisions"] += len(task["revisions"])
            counts["model_proposals"] += sum(
                1 for revision in task["revisions"] if revision["method"] == "model_proposal")
            manifest_tasks.append({
                "old_task_id": task["task_id"],
                "new_task_id": new_task,
                "old_bundle": str(old_bundle),
                "new_bundle": str(new_bundle),
                "old_evidence_bundle_hash": task["evidence_bundle_hash"],
                "new_evidence_bundle_hash": frozen["evidence_bundle_hash"],
                "evidence_bundle_sha256": _sha256_file(new_bundle / "bundle.json"),
                "revision_map": revision_map,
            })
        _verify_replay(source, migrated_store, manifest_tasks, target)
    except BaseException:
        if out_bundles.exists():
            shutil.rmtree(out_bundles, ignore_errors=True)
        out_store.unlink(missing_ok=True)
        raise

    manifest = {
        "schema": MIGRATION_SCHEMA,
        "codebook_version_from": version_from,
        "codebook_version_to": target,
        "source_store": {"path": str(source_store), "sha256": source_digest},
        "source_release_id": tasks[0]["binding"]["data_release_id"],
        "tasks": manifest_tasks,
        "counts": {**counts, "human_revisions_carried": sum(human_carried.values())},
        "attestations_migrated": 0,
        "paper_metrics_available": False,
        "note": note or ("Binding metadata only: evidence bytes, annotations and declared "
                         "methods are unchanged; no human label or attestation was carried."),
    }
    return manifest


def _verify_replay(source: RecordStore, migrated: RecordStore,
                   manifest_tasks: list[dict], target: str) -> None:
    """Fail closed if the replay changed an annotation, a method or the task count."""
    before = {task["task_id"]: task for task in source.export()["tasks"]}
    after = {task["task_id"]: task for task in migrated.export()["tasks"]}
    if len(before) != len(after):
        raise ValueError("migrated store has a different task count")
    for entry in manifest_tasks:
        old, new = before[entry["old_task_id"]], after[entry["new_task_id"]]
        if new["binding"]["codebook_version"] != target:
            raise ValueError("migrated task kept the old codebook version")
        if [row["annotator"] for row in old["revisions"]] != [
                row["annotator"] for row in new["revisions"]]:
            raise ValueError("migrated task changed its revision authors")
        if [row["method"] for row in old["revisions"]] != [
                row["method"] for row in new["revisions"]]:
            raise ValueError("migrated task changed a declared method")
        if [row["annotation"] for row in old["revisions"]] != [
                row["annotation"] for row in new["revisions"]]:
            raise ValueError("migrated task changed an annotation payload")
