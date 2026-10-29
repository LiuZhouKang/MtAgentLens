"""Synthetic raw-source bundles, never detector-derived gold fixtures."""

import copy
import hashlib
import json
import pytest

from tools.annotator.evidence import build_bundle, load_bundle


def fixture_inputs(root):
    release = root / "release"
    release.mkdir()
    manifest = {"schema": "mtagentlens.dataset-release/1", "files": {},
                "case_bindings": {"scenario": {}}}
    manifest["release_id"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    (release / "release.json").write_text(json.dumps(manifest))
    raw = root / "runner.jsonl"
    raw.write_text('{"native_id":"r1","pid":42,"starttime":123}\n')
    return {
        "release_root": release,
        "binding": {"data_release_id": manifest["release_id"], "scenario_id": "scenario",
                    "run_id": "run-1", "source_run_uuid": "efc35a21-b940-4916-a5c8-9996638b60dd",
                    "codebook_version": "pilot/1"},
        "sources": {"runner": {"path": raw, "kind": "controlled_runner", "producer": "fixture-v1"}},
        "actors": ["actor-a", "actor-b"],
        "events": [{"event_id": "event-1", "source_id": "runner", "record_id": "r1",
                    "timestamp": "123", "clock_domain": "runner-monotonic-ns",
                    "derivation": "native record id", "byte_offset": 0,
                    "byte_length": raw.stat().st_size,
                    "record_sha256": hashlib.sha256(raw.read_bytes()).hexdigest()}],
    }


def test_freeze_roundtrip_and_source_change_does_not_change_frozen_bundle(tmp_path):
    inputs = fixture_inputs(tmp_path)
    manifest = build_bundle(tmp_path / "bundle", **inputs)
    assert manifest == load_bundle(tmp_path / "bundle")
    assert manifest["binding"] == inputs["binding"]
    assert len(manifest["evidence_bundle_hash"]) == 64
    source = manifest["sources"]["runner"]
    frozen = tmp_path / "bundle" / source["path"]
    assert frozen.read_bytes() == inputs["sources"]["runner"]["path"].read_bytes()
    inputs["sources"]["runner"]["path"].write_text("changed upstream")
    assert load_bundle(tmp_path / "bundle") == manifest


@pytest.mark.parametrize("change", ["raw", "binding", "extra", "symlink", "path_escape", "digest"])
def test_tampered_or_escaping_bundle_is_rejected(tmp_path, change):
    inputs = fixture_inputs(tmp_path)
    root = tmp_path / "bundle"
    manifest = build_bundle(root, **inputs)
    raw = root / manifest["sources"]["runner"]["path"]
    if change == "raw":
        raw.write_text("changed")
    elif change == "extra":
        (root / "unlisted").write_text("not inventoried")
    elif change == "symlink":
        raw.unlink()
        raw.symlink_to(inputs["sources"]["runner"]["path"])
    else:
        if change == "binding":
            manifest["binding"]["run_id"] = "different-run"
        elif change == "path_escape":
            manifest["sources"]["runner"]["path"] = "../runner.jsonl"
        else:
            manifest["evidence_bundle_hash"] = "0" * 64
        (root / "bundle.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_bundle(root)


@pytest.mark.parametrize("field,value", [
    ("scenario_id", "absent"), ("data_release_id", "0" * 64),
    ("source_run_uuid", "not-a-uuid"), ("run_id", ""), ("codebook_version", ""),
])
def test_bad_binding_is_rejected_without_leaving_output(tmp_path, field, value):
    inputs = fixture_inputs(tmp_path)
    inputs["binding"][field] = value
    with pytest.raises(ValueError):
        build_bundle(tmp_path / "bundle", **inputs)
    assert not (tmp_path / "bundle").exists()


@pytest.mark.parametrize("change", ["duplicate_event", "unknown_source", "duplicate_actor",
                                    "missing_clock", "detector_output", "empty_producer"])
def test_invalid_observation_contract_rejected(tmp_path, change):
    inputs = fixture_inputs(tmp_path)
    if change == "duplicate_event":
        inputs["events"].append(copy.deepcopy(inputs["events"][0]))
    elif change == "unknown_source":
        inputs["events"][0]["source_id"] = "other"
    elif change == "duplicate_actor":
        inputs["actors"].append("actor-a")
    elif change == "missing_clock":
        del inputs["events"][0]["clock_domain"]
    elif change == "detector_output":
        inputs["sources"]["runner"]["kind"] = "detector_output"
    else:
        inputs["sources"]["runner"]["producer"] = ""
    with pytest.raises(ValueError):
        build_bundle(tmp_path / "bundle", **inputs)
    assert not (tmp_path / "bundle").exists()


def test_existing_bundle_is_never_overwritten(tmp_path):
    inputs = fixture_inputs(tmp_path)
    root = tmp_path / "bundle"
    first = build_bundle(root, **inputs)
    with pytest.raises(ValueError):
        build_bundle(root, **inputs)
    assert load_bundle(root) == first


def test_changed_release_is_not_accepted(tmp_path):
    inputs = fixture_inputs(tmp_path)
    (inputs["release_root"] / "unexpected").write_text("changed release")
    with pytest.raises(ValueError):
        build_bundle(tmp_path / "bundle", **inputs)


def test_duplicate_json_keys_are_rejected(tmp_path):
    inputs = fixture_inputs(tmp_path)
    root = tmp_path / "bundle"
    build_bundle(root, **inputs)
    text = (root / "bundle.json").read_text()
    (root / "bundle.json").write_text(text.replace('"run_id":', '"run_id":"other","run_id":', 1))
    with pytest.raises(ValueError):
        load_bundle(root)


@pytest.mark.parametrize("change", ["blank_actor", "blank_clock", "blank_run", "invalid_kind"])
def test_blank_identifiers_and_non_string_kind_are_rejected(tmp_path, change):
    inputs = fixture_inputs(tmp_path)
    if change == "blank_actor":
        inputs["actors"] = [" "]
    elif change == "blank_clock":
        inputs["events"][0]["clock_domain"] = " "
    elif change == "blank_run":
        inputs["binding"]["run_id"] = " "
    else:
        inputs["sources"]["runner"]["kind"] = []
    with pytest.raises(ValueError):
        build_bundle(tmp_path / "bundle", **inputs)
    assert not (tmp_path / "bundle").exists()


@pytest.mark.parametrize("field,value", [("byte_offset", -1), ("byte_offset", True),
    ("byte_length", 0), ("byte_length", 10000), ("record_sha256", "0" * 64)])
def test_record_anchors_must_resolve_to_the_actual_source_bytes(tmp_path, field, value):
    inputs = fixture_inputs(tmp_path)
    inputs["events"][0][field] = value
    with pytest.raises(ValueError):
        build_bundle(tmp_path / "bundle", **inputs)
    assert not (tmp_path / "bundle").exists()


def test_load_checks_record_anchor_even_when_manifest_digest_is_recomputed(tmp_path):
    inputs = fixture_inputs(tmp_path)
    root = tmp_path / "bundle"
    manifest = build_bundle(root, **inputs)
    manifest["events"][0]["record_sha256"] = "0" * 64
    body = {key: value for key, value in manifest.items() if key != "evidence_bundle_hash"}
    manifest["evidence_bundle_hash"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()).hexdigest()
    (root / "bundle.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_bundle(root)


def test_nonzero_record_range_hashes_only_the_anchored_bytes(tmp_path):
    inputs = fixture_inputs(tmp_path)
    path = inputs["sources"]["runner"]["path"]
    record = path.read_bytes()
    prefix = b'{"native_id":"earlier"}\n'
    path.write_bytes(prefix + record + b'{"native_id":"later"}\n')
    inputs["events"][0]["byte_offset"] = len(prefix)
    manifest = build_bundle(tmp_path / "bundle", **inputs)
    assert manifest["events"][0]["record_sha256"] == hashlib.sha256(record).hexdigest()
    assert load_bundle(tmp_path / "bundle") == manifest


def test_new_bundle_keeps_private_native_inputs_owner_only(tmp_path):
    spec = fixture_inputs(tmp_path)
    output = tmp_path / 'bundle'
    result = build_bundle(output, **spec)
    assert output.stat().st_mode & 0o077 == 0
    assert (output / 'bundle.json').stat().st_mode & 0o077 == 0
    for source in result['sources'].values():
        assert (output / source['path']).stat().st_mode & 0o077 == 0
