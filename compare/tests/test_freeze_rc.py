# SPDX-License-Identifier: Apache-2.0 OR MIT
"""A release candidate records hashes or fails; it never invents an input."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.evaluation.freeze_rc import SCHEMA, freeze, write_record

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _release(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    release = {
        "schema": "mtagentlens.dataset-release/1", "files": {},
        "case_bindings": {"s1": {}}, "paper_metrics_available": True,
        "publication_blockers": [],
    }
    release["release_id"] = hashlib.sha256(
        json.dumps(release, sort_keys=True).encode()).hexdigest()
    (root / "release.json").write_text(json.dumps(release))
    return root


@pytest.fixture
def rc_inputs(tmp_path):
    binary = tmp_path / "mtagentlens"
    binary.write_bytes(b"\x7fELF synthetic release binary")
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    for name in ("tls_capture", "process", "stdiocap"):
        (helpers / name).write_bytes(name.encode() * 4)
    return binary, _release(tmp_path / "release"), helpers


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_record_binds_binary_helpers_dataset_and_toolchain(rc_inputs):
    binary, release, helpers = rc_inputs
    record = freeze(binary, release, helper_root=helpers)
    assert record["schema"] == SCHEMA
    assert record["binary"]["sha256"] == sha(binary)
    assert record["dataset"]["release_id"]
    assert record["dataset"]["cases"] == ["s1"]
    assert set(record["ebpf_helpers"]) == {"tls_capture", "process", "stdiocap"}
    assert record["ebpf_helpers"]["process"]["sha256"] == sha(helpers / "process")
    assert record["toolchain"]["python"]
    assert record["paper_metrics_available"] is False
    assert record["verification"]["gate_log"] is None


def test_missing_binary_or_helper_is_an_error_not_a_null(rc_inputs, tmp_path):
    binary, release, helpers = rc_inputs
    with pytest.raises(ValueError, match="collector binary"):
        freeze(tmp_path / "absent", release, helper_root=helpers)
    (helpers / "stdiocap").unlink()
    with pytest.raises(ValueError, match="stdiocap"):
        freeze(binary, release, helper_root=helpers)


def test_dataset_without_manifest_is_rejected(rc_inputs, tmp_path):
    binary, _, helpers = rc_inputs
    empty = tmp_path / "empty-release"
    empty.mkdir()
    with pytest.raises(ValueError):
        freeze(binary, empty, helper_root=helpers)


def test_gate_log_is_hashed_and_read_for_verdicts(rc_inputs, tmp_path):
    binary, release, helpers = rc_inputs
    log = tmp_path / "verify.log"
    log.write_text("── 1/17 rustfmt\n  PASS clean\n══ verify: all gates passed ══\n")
    record = freeze(binary, release, helper_root=helpers, verify_log=log)
    assert record["verification"]["gates_passed"] is True
    assert record["verification"]["gate_log_sha256"] == sha(log)
    log.write_text("FAIL one gate\n")
    failed = freeze(binary, release, helper_root=helpers, verify_log=log)
    assert failed["verification"]["gates_passed"] is False


def test_record_is_published_once(tmp_path, rc_inputs):
    binary, release, helpers = rc_inputs
    record = freeze(binary, release, helper_root=helpers)
    target = tmp_path / "rc" / "rc.json"
    write_record(target, record)
    assert json.loads(target.read_text()) == record
    with pytest.raises(ValueError, match="exists"):
        write_record(target, record)
