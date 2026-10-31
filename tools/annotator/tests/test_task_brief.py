# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Scenario briefs carry task text only; labels and identifiers stay out."""

import hashlib
import json
from pathlib import Path

import pytest

from tools.annotator.task_brief import extract_task_brief, write_task_brief
from tools.annotator.tests.test_runner_import import inputs  # noqa: F401


def release_with_brief(tmp_path):
    release = tmp_path / "release"
    scenarios = release / "scenarios"
    scenarios.mkdir(parents=True)
    scenario = scenarios / "scenario.json"
    scenario.write_text(json.dumps({
        "id": "scenario", "mode": "single_agent",
        "attack_type": "credential_theft", "label": "attack",
        "agents": [{"role_file": "developer.md",
                    "task": "Read the docs for scenario run-1 then summarize."}],
    }), encoding="utf-8")
    files = {"scenarios/scenario.json": hashlib.sha256(scenario.read_bytes()).hexdigest()}
    manifest = {"schema": "mtagentlens.dataset-release/1", "files": files,
                "case_bindings": {"scenario": {}}, "paper_metrics_available": False}
    manifest["release_id"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    (release / "release.json").write_text(json.dumps(manifest), encoding="utf-8")
    return release


def test_brief_contains_task_but_no_label_or_identifier(tmp_path):
    release = release_with_brief(tmp_path)
    scenario_path, lines = extract_task_brief(release, "scenario",
                                              identifiers=("scenario", "run-1"))
    assert scenario_path.name == "scenario.json"
    assert len(lines) == 1
    text = lines[0]["text"]
    assert "Read the docs" in text
    assert "blinded-" in text
    assert '"label"' not in text and '"attack_type"' not in text
    assert "credential_theft" not in text
    out, events = write_task_brief(tmp_path / "brief.jsonl", lines)
    assert events[0]["event_id"] == "task_brief:agent-0"
    assert events[0]["source_id"] == "task_brief"
    raw = out.read_bytes()
    assert hashlib.sha256(raw[events[0]["byte_offset"]:
                              events[0]["byte_offset"] + events[0]["byte_length"]]).hexdigest() == events[0]["record_sha256"]


def test_missing_brief_is_optional_but_invalid_is_not(tmp_path):
    release = release_with_brief(tmp_path)
    assert extract_task_brief(release, "absent") is None
    (release / "scenarios" / "scenario.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError):
        extract_task_brief(release, "scenario")


def test_unbound_directory_extracts_blinded_brief(tmp_path):
    release = release_with_brief(tmp_path)
    (release / "release.json").unlink()
    _, lines = extract_task_brief(release, "scenario", identifiers=("scenario",))
    assert "Read the docs" in lines[0]["text"]
    assert "blinded-" in lines[0]["text"]


@pytest.mark.parametrize("scenario_id", ["../scenario", "/scenario", "a/b", "a\\b", ".", "..", ""])
def test_unbound_brief_rejects_unsafe_scenario_id(tmp_path, scenario_id):
    release = release_with_brief(tmp_path)
    (release / "release.json").unlink()
    with pytest.raises(ValueError):
        extract_task_brief(release, scenario_id)


def test_unbound_brief_rejects_symlink_and_id_mismatch(tmp_path):
    release = release_with_brief(tmp_path)
    (release / "release.json").unlink()
    scenario = release / "scenarios" / "scenario.json"
    scenario.write_text(json.dumps({"id": "other", "agents": [{"task": "read"}]}))
    with pytest.raises(ValueError, match="identity"):
        extract_task_brief(release, "scenario")
    external = tmp_path / "external.json"
    external.write_text(scenario.read_text())
    scenario.unlink()
    scenario.symlink_to(external)
    with pytest.raises(ValueError, match="symlink"):
        extract_task_brief(release, "scenario")


def test_existing_release_manifest_is_still_verified(tmp_path):
    release = release_with_brief(tmp_path)
    (release / "scenarios" / "scenario.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        extract_task_brief(release, "scenario")


def test_import_freezes_the_blinded_brief_when_the_release_has_it(tmp_path, inputs):  # noqa: F811
    spec, ledger, rows = inputs
    release = Path(spec["release_root"])
    scenarios = release / "scenarios"
    scenarios.mkdir()
    scenario = scenarios / "scenario.json"
    scenario.write_text(json.dumps({
        "id": "scenario", "mode": "single_agent", "label": "attack",
        "agents": [{"task": "Read scenario files for run-1."}],
    }), encoding="utf-8")
    manifest = json.loads((release / "release.json").read_text(encoding="utf-8"))
    manifest["files"] = {"scenarios/scenario.json": hashlib.sha256(scenario.read_bytes()).hexdigest()}
    manifest["release_id"] = hashlib.sha256(
        json.dumps({key: value for key, value in manifest.items() if key != "release_id"},
                   sort_keys=True).encode()).hexdigest()
    (release / "release.json").write_text(json.dumps(manifest), encoding="utf-8")
    from tools.annotator.runner_import import import_runner_bundle
    bundle = import_runner_bundle(tmp_path / "bundle", release_root=release, ledger=ledger,
                                  codebook_version=spec["binding"]["codebook_version"])
    assert "task_brief" in bundle["sources"]
    briefs = [event for event in bundle["events"] if event["source_id"] == "task_brief"]
    assert len(briefs) == 1
    source = tmp_path / "bundle" / bundle["sources"]["task_brief"]["path"]
    raw = source.read_bytes()[briefs[0]["byte_offset"]:briefs[0]["byte_offset"] + briefs[0]["byte_length"]]
    text = raw.decode("utf-8")
    assert "Read" in text and "blinded-" in text
    assert "blinded-" in text
