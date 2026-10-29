# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Re-versioning moves binding metadata, never evidence bytes or label provenance."""

import hashlib
import json
import sys

import pytest

from tools.annotator.evidence import build_bundle, load_bundle
from tools.annotator.migrate import (
    MIGRATION_SCHEMA,
    migrate_store,
    reversion_bundle,
    write_migration_manifest,
)
from tools.annotator.records import RecordStore
from tools.annotator.tests.test_evidence import fixture_inputs

FROM = "pilot/1-draft"
TO = "pilot/1"


def _annotation(*, attempted="yes", executed="no", succeeded="no", roots=None):
    return {
        "attempted": {"value": attempted, "evidence_refs": ["event-1"]},
        "executed": {"value": executed, "evidence_refs": ["event-1"]},
        "succeeded": {"value": succeeded, "evidence_refs": ["event-1"]},
        "identities": {"event-1": None},
        "roots": {"actors": roots,
                  "evidence_refs": ["event-1"] if roots is not None else []},
        "notes": "Synthetic migration regression; not a human annotation.",
    }


def _fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    if (root / "release").exists():
        return {"release_root": root / "release"}
    return fixture_inputs(root)


def _bundle(tmp_path, name, *, run_id, uuid):
    spec = _fixture(tmp_path / name)
    spec["binding"].update(run_id=run_id, source_run_uuid=uuid, codebook_version=FROM)
    return build_bundle(tmp_path / name / "bundle", **spec)


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    first = _bundle(tmp_path, "one", run_id="run-1", uuid="efc35a21-b940-4916-a5c8-9996638b60dd")
    second = _bundle(tmp_path, "two", run_id="run-2", uuid="96714245-5940-4804-b54e-3d5b3f647ced")
    records = RecordStore(root / "records.sqlite")
    tasks = [records.register_bundle(tmp_path / "one" / "bundle"),
             records.register_bundle(tmp_path / "two" / "bundle")]
    revisions = {}
    for annotator in ("alice", "bob"):
        revisions[annotator] = records.save(tasks[0], annotator, _annotation(),
                                            method="model_proposal", expected_revision=None)
    records.save(tasks[1], "alice", _annotation(attempted="no", executed="no", succeeded="no",
                                                roots=[]),
                 method="model_proposal", expected_revision=None)
    return records, tasks, revisions, tmp_path / "one" / "bundle"


def _migrate(records, tmp_path, **overrides):
    arguments = dict(
        source_store=records.path,
        out_store=tmp_path / "migrated" / "records.sqlite",
        out_bundles=tmp_path / "migrated" / "bundles",
        codebook_version=TO,
        release_root=_fixture(tmp_path / "release-holder")["release_root"],
    )
    arguments.update(overrides)
    return migrate_store(**arguments)


def test_reversion_keeps_evidence_bytes_and_rebinds_only_the_codebook(tmp_path):
    source = _bundle(tmp_path, "one", run_id="run-1", uuid="efc35a21-b940-4916-a5c8-9996638b60dd")
    original = load_bundle(tmp_path / "one" / "bundle")
    migrated = reversion_bundle(
        tmp_path / "one" / "bundle", tmp_path / "one-v1",
        TO, release_root=_fixture(tmp_path / "holder")["release_root"])
    assert migrated["binding"]["codebook_version"] == TO
    assert migrated["binding"]["run_id"] == original["binding"]["run_id"]
    assert migrated["events"] == original["events"]
    assert migrated["actors"] == original["actors"]
    assert {key: value["sha256"] for key, value in migrated["sources"].items()} == {
        key: value["sha256"] for key, value in original["sources"].items()}
    assert migrated["evidence_bundle_hash"] != original["evidence_bundle_hash"]
    # The source bundle is immutable.
    assert load_bundle(tmp_path / "one" / "bundle") == original


def test_reversion_refuses_existing_destination_and_noop_version(tmp_path):
    _bundle(tmp_path, "one", run_id="run-1", uuid="efc35a21-b940-4916-a5c8-9996638b60dd")
    holder = _fixture(tmp_path / "holder")["release_root"]
    with pytest.raises(ValueError, match="already exists"):
        reversion_bundle(tmp_path / "one" / "bundle", tmp_path / "one" / "bundle",
                         TO, release_root=holder)
    with pytest.raises(ValueError, match="codebook"):
        reversion_bundle(tmp_path / "one" / "bundle", tmp_path / "same", FROM,
                         release_root=holder)


def test_migration_rebinds_revisions_without_touching_their_payload(store, tmp_path):
    records, tasks, revisions, _ = store
    before = records.export()
    manifest = _migrate(records, tmp_path)

    migrated = RecordStore(tmp_path / "migrated" / "records.sqlite").export()
    assert len(migrated["tasks"]) == len(before["tasks"])
    assert {task["binding"]["codebook_version"] for task in migrated["tasks"]} == {TO}
    assert len({task["binding"]["codebook_version"] for task in migrated["tasks"]}) == 1
    before_by_id = {task["task_id"]: task for task in before["tasks"]}
    migrated_by_id = {task["task_id"]: task for task in migrated["tasks"]}
    for entry in manifest["tasks"]:
        old, new = before_by_id[entry["old_task_id"]], migrated_by_id[entry["new_task_id"]]
        assert [row["annotation"] for row in new["revisions"]] == [
            row["annotation"] for row in old["revisions"]]
        assert [row["annotator"] for row in new["revisions"]] == [
            row["annotator"] for row in old["revisions"]]
        assert [row["method"] for row in new["revisions"]] == [
            row["method"] for row in old["revisions"]]
        assert new["task_id"] != old["task_id"]
    assert manifest["schema"] == MIGRATION_SCHEMA
    assert manifest["codebook_version_from"] == FROM
    assert manifest["codebook_version_to"] == TO
    assert manifest["paper_metrics_available"] is False
    assert manifest["attestations_migrated"] == 0
    assert set(manifest["tasks"][0]) >= {"old_task_id", "new_task_id", "old_bundle",
                                         "new_bundle", "revision_map"}


def test_migration_leaves_the_source_store_and_bundles_untouched(store, tmp_path):
    records, tasks, revisions, bundle = store
    before = records.export()
    bundle_digest = hashlib.sha256((bundle / "bundle.json").read_bytes()).hexdigest()
    _migrate(records, tmp_path)
    assert records.export() == before
    assert records.list_attestations() == []
    assert hashlib.sha256((bundle / "bundle.json").read_bytes()).hexdigest() == bundle_digest


def test_adjudication_review_references_are_rewritten_to_the_new_revisions(store, tmp_path):
    records, tasks, revisions, _ = store
    task = tasks[0]
    heads = {"alice": records.save(task, "alice", _annotation(), method="human",
                                   expected_revision=revisions["alice"]["revision_id"])["revision_id"],
             "bob": records.save(task, "bob", _annotation(), method="human",
                                 expected_revision=revisions["bob"]["revision_id"])["revision_id"]}
    adjudicated = records.save(task, "curator", _annotation(), method="human",
                               expected_revision=None,
                               review={"reviewed_revisions": heads,
                                       "rationale": "Compared both records."})
    manifest = _migrate(records, tmp_path, carry_human=True)
    migrated = RecordStore(tmp_path / "migrated" / "records.sqlite").export()
    by_old = {entry["old_task_id"]: entry for entry in manifest["tasks"]}
    entry = by_old[task]
    new_task = next(row for row in migrated["tasks"] if row["task_id"] == entry["new_task_id"])
    review = [row for row in new_task["revisions"] if row.get("review")][0]
    expected = {annotator: entry["revision_map"][value] for annotator, value in heads.items()}
    assert review["review"]["reviewed_revisions"] == expected
    assert review["review"]["rationale"] == "Compared both records."
    assert manifest["counts"]["human_revisions_carried"] == 3
    assert review["revision_id"] == entry["revision_map"][adjudicated["revision_id"]]


def test_human_revisions_are_not_silently_carried_across_codebooks(store, tmp_path):
    records, tasks, _, _ = store
    records.save(tasks[0], "刘周康", _annotation(), method="human", expected_revision=None)
    with pytest.raises(ValueError, match="human"):
        _migrate(records, tmp_path)
    assert not (tmp_path / "migrated" / "records.sqlite").exists()


def test_attestations_block_migration_instead_of_being_carried(store, tmp_path):
    records, tasks, revisions, _ = store
    records.save_attestation(tasks[0], "刘周康", revisions["alice"]["revision_id"],
                             statement="I reviewed the draft under the draft codebook.")
    with pytest.raises(ValueError, match="attestation"):
        _migrate(records, tmp_path)


def test_migration_refuses_to_overwrite_its_own_outputs(store, tmp_path):
    records, _, _, _ = store
    _migrate(records, tmp_path)
    with pytest.raises(ValueError, match="exists"):
        _migrate(records, tmp_path)
    other_bundles = tmp_path / "other-bundles"
    other_bundles.mkdir()
    with pytest.raises(ValueError, match="exists"):
        _migrate(records, tmp_path,
                 out_store=tmp_path / "second" / "records.sqlite", out_bundles=other_bundles)


def test_migration_failure_leaves_no_partial_bundle_tree(store, tmp_path):
    records, _, _, _ = store
    holder = _fixture(tmp_path / "release-holder")["release_root"]
    (holder / "release.json").write_text("{}")
    with pytest.raises(ValueError):
        _migrate(records, tmp_path, release_root=holder)
    assert not (tmp_path / "migrated" / "bundles").exists()


def test_migration_manifest_is_written_once(tmp_path, store):
    _, _, _, _ = store
    source = tmp_path / "store" / "records.sqlite"
    target = tmp_path / "migration.json"
    payload = {"schema": MIGRATION_SCHEMA}
    write_migration_manifest(target, payload)
    assert json.loads(target.read_text()) == payload
    with pytest.raises(ValueError, match="exists"):
        write_migration_manifest(target, payload)


def test_cli_migrate_codebook_reports_the_binding_map(store, tmp_path, monkeypatch, capsys):
    records, tasks, _, _ = store
    release = _fixture(tmp_path / "release-holder")["release_root"]
    monkeypatch.setattr(sys, "argv", [
        "tools.annotator", "migrate-codebook",
        "--store", str(records.path),
        "--out-store", str(tmp_path / "cli" / "records.sqlite"),
        "--bundles-out", str(tmp_path / "cli" / "bundles"),
        "--release", str(release),
        "--codebook-version", TO,
        "--manifest-out", str(tmp_path / "cli" / "migration.json"),
        "--note", "pilot codebook finalised",
    ])
    from tools.annotator.__main__ import main

    assert main() == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["codebook_version_from"] == FROM
    assert printed["codebook_version_to"] == TO
    assert printed["counts"]["tasks"] == len(tasks)
    assert json.loads((tmp_path / "cli" / "migration.json").read_text())["note"] == (
        "pilot codebook finalised")
