# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Batch pilot preparation keeps the store empty when any ledger fails."""

import json
import uuid

import pytest

from tools.annotator.pilot import prepare_pilot
from tools.annotator.tests.test_evidence import fixture_inputs
from tools.annotator.tests.test_runner_import import native_rows, write_rows


def ledgers(tmp_path, count=2, prefix="run", spec=None):
    spec = spec or fixture_inputs(tmp_path)
    paths = []
    for index in range(count):
        binding = dict(spec["binding"], run_id=f"{prefix}-{index}",
                       source_run_uuid=str(uuid.uuid4()))
        path = tmp_path / f"{prefix}-{index}.jsonl"
        write_rows(path, native_rows(binding))
        paths.append(path)
    return spec, paths


def test_two_ledgers_become_annotation_tasks_with_the_requested_codebook(tmp_path):
    spec, paths = ledgers(tmp_path)
    store = tmp_path / "records.sqlite"
    result = prepare_pilot(store, spec["release_root"], paths, "pilot/1-draft")
    assert result["schema"] == "mtagentlens.pilot-preparation/v1"
    assert result["codebook_version"] == "pilot/1-draft"
    assert len(result["tasks"]) == 2
    assert result["paper_metrics_available"] is False
    from tools.annotator.records import RecordStore
    exported = RecordStore(store).export()
    assert len(exported["tasks"]) == 2
    assert {task["binding"]["codebook_version"] for task in exported["tasks"]} == {"pilot/1-draft"}
    assert all(task["revisions"] == [] for task in exported["tasks"])


def test_a_failed_import_leaves_no_partial_store_or_bundles(tmp_path):
    spec, paths = ledgers(tmp_path)
    paths[1].write_text("not a native ledger\n")
    store = tmp_path / "records.sqlite"
    bundle_root = tmp_path / "bundles"
    with pytest.raises(ValueError):
        prepare_pilot(store, spec["release_root"], paths, "pilot/1-draft",
                      bundle_root=bundle_root)
    assert not store.exists()
    assert not bundle_root.exists() or not any(bundle_root.iterdir())


def test_empty_ledger_list_and_blank_codebook_are_rejected(tmp_path):
    spec, paths = ledgers(tmp_path, count=1)
    with pytest.raises(ValueError):
        prepare_pilot(tmp_path / "records.sqlite", spec["release_root"], [], "pilot/1-draft")
    with pytest.raises(ValueError):
        prepare_pilot(tmp_path / "records.sqlite", spec["release_root"], paths, "  ")


def test_existing_bundle_directory_is_not_overwritten(tmp_path):
    spec, paths = ledgers(tmp_path, count=1)
    bundle_root = tmp_path / "bundles"
    (bundle_root / "run-0000").mkdir(parents=True)
    (bundle_root / "run-0000" / "keep").write_text("pre-existing")
    result = prepare_pilot(tmp_path / "records.sqlite", spec["release_root"], paths,
                           "pilot/1-draft", bundle_root=bundle_root)
    assert (bundle_root / "run-0000" / "keep").read_text() == "pre-existing"
    assert result["bundles"][0].endswith("run-0001")


def test_cli_reports_the_prepared_tasks(tmp_path, monkeypatch, capsys):
    spec, paths = ledgers(tmp_path, count=2)
    argv = ["annotator", "pilot", "--store", str(tmp_path / "records.sqlite"),
            "--release", str(spec["release_root"]), "--codebook-version", "pilot/1-draft"]
    for path in paths:
        argv.extend(["--ledger", str(path)])
    monkeypatch.setattr("sys.argv", argv)
    from tools.annotator.__main__ import main
    assert main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["codebook_version"] == "pilot/1-draft"
    assert len(output["tasks"]) == 2
    assert output["paper_metrics_available"] is False


def test_second_prepare_appends_tasks_to_the_same_store(tmp_path):
    spec, first = ledgers(tmp_path, count=1, prefix="a")
    store = tmp_path / "records.sqlite"
    prepare_pilot(store, spec["release_root"], first, "pilot/1-draft")
    _, second = ledgers(tmp_path, count=1, prefix="b", spec=spec)
    result = prepare_pilot(store, spec["release_root"], second, "pilot/1-draft")
    from tools.annotator.records import RecordStore
    exported = RecordStore(store).export()
    assert len(result["tasks"]) == 1
    assert result["registered_total"] == 2
    assert len(exported["tasks"]) == 2
    assert {task["binding"]["codebook_version"] for task in exported["tasks"]} == {"pilot/1-draft"}


def test_second_prepare_rejects_a_different_codebook_version(tmp_path):
    spec, first = ledgers(tmp_path, count=1, prefix="a")
    store = tmp_path / "records.sqlite"
    prepare_pilot(store, spec["release_root"], first, "pilot/1-draft")
    bundles = tmp_path / "pilot-bundles"
    before = sorted(path.name for path in bundles.iterdir())
    _, second = ledgers(tmp_path, count=1, prefix="b", spec=spec)
    with pytest.raises(ValueError, match="codebook"):
        prepare_pilot(store, spec["release_root"], second, "pilot/2")
    assert sorted(path.name for path in bundles.iterdir()) == before
    from tools.annotator.records import RecordStore
    assert len(RecordStore(store).export()["tasks"]) == 1
