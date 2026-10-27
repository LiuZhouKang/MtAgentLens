# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Trace packager contract.

The fake replay executable and the monkeypatched
``run_artifacts.validate_run_binding`` are explicit driver fixtures for
lifecycle and rejection tests. They are not evidence that real run artifacts or
the Rust recorder validator accepted a recording. Existing real replay CLI
evidence remains ``local/readiness-2026-10-26/team/strict-recording-green.log``.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.evaluation import run_artifacts
from scripts.evaluation import trace_packager as tp

SCENARIO = "case-one"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _binding_digest(binding: dict) -> str:
    payload = json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _make_db(path: Path, *, scenario: str = SCENARIO, user_version: int = 5,
             recorded_version: int = 5, extra_run: bool = False) -> Path:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                started_at_ns INTEGER NOT NULL,
                ended_at_ns INTEGER,
                scenario_id TEXT,
                mode TEXT,
                collector_version TEXT
            );
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
            """
        )
        conn.execute("PRAGMA user_version = %d" % user_version)
        conn.execute("INSERT INTO schema_version(version) VALUES (?)", (recorded_version,))
        conn.execute(
            "INSERT INTO runs VALUES ('r1', 1, 2, ?, 'benchmark', '0.1.0')",
            (scenario,),
        )
        if extra_run:
            conn.execute(
                "INSERT INTO runs VALUES ('r2', 3, 4, ?, 'benchmark', '0.1.0')",
                (scenario,),
            )
        conn.commit()
    return path


def _make_release(root: Path, *, scenario_id: str = SCENARIO) -> Path:
    release = root / "release"
    (release / "scenarios").mkdir(parents=True)
    (release / "ground_truth").mkdir()
    scenario = release / "scenarios" / f"{scenario_id}.json"
    scenario.write_text(json.dumps({"id": scenario_id, "mode": "benchmark"}) + "\n")
    gt = release / "ground_truth" / f"{scenario_id}.json"
    gt.write_text(json.dumps({"scenario_id": scenario_id, "label": "benign"}) + "\n")
    binding = {
        "scenario_sha256": _sha(scenario),
        "ground_truth_sha256": _sha(gt),
        "fixture_sha256": "fixture-tree",
    }
    manifest = {
        "schema": "mtagentlens.dataset-release/1",
        "case_bindings": {scenario_id: binding},
        "files": {
            f"scenarios/{scenario_id}.json": _sha(scenario),
            f"ground_truth/{scenario_id}.json": _sha(gt),
        },
        "note": "input snapshot only",
    }
    manifest["release_id"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()
    ).hexdigest()
    (release / "release.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return release


FAKE_REPLAY = r'''#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
if "--validate-recording" not in args or "--sqlite" not in args:
    sys.exit(9)
db = Path(args[args.index("--sqlite") + 1])
mode = os.environ.get("FAKE_REPLAY_MODE", "ok")
if mode == "sleep":
    time.sleep(5)
if mode == "exit":
    sys.exit(3)
if mode == "invalid_json":
    print("not-json")
    sys.exit(0)
if mode == "mutate_copy":
    db.write_bytes(db.read_bytes() + b"copy-tamper")
if mode == "mutate_source":
    source = Path(os.environ["FAKE_REPLAY_SOURCE"])
    source.write_bytes(source.read_bytes() + b"source-tamper")
if mode == "journal_copy":
    db.with_name(db.name + "-journal").write_bytes(b"staging-journal")
if mode == "extra_member":
    db.with_name("staging-extra.bin").write_bytes(b"extra")
if mode == "source_wal":
    source = Path(os.environ["FAKE_REPLAY_SOURCE"])
    source.with_name(source.name + "-wal").write_bytes(b"source-wal")
report = {
    "schema": "mtagentlens.recording-validation/v1",
    "status": "ok",
    "read_only": True,
    "scope": "offline-quiescent structural and replay integrity only",
    "capture_quality": {},
    "primary_eligible": False,
    "reference_status": "unreviewed",
    "runs": [{
        "run_id": "r1",
        "status": "ok",
        "capture_quality": None,
        "evidence": None,
        "alerts": None,
        "failures": [],
    }],
    "failures": [],
}
if mode == "bad_status":
    report["status"] = "invalid"
    report["failures"] = ["schema mismatch"]
if mode == "bad_read_only":
    report["read_only"] = False
if mode == "empty_runs":
    report["runs"] = []
if mode == "run_failure":
    report["runs"][0]["status"] = "invalid"
    report["runs"][0]["failures"] = ["run failed"]
print(json.dumps(report))
'''


def _fake_replay(root: Path) -> Path:
    path = root / "fake-replay"
    path.write_text(FAKE_REPLAY)
    path.chmod(0o755)
    return path


def _row(db: Path, release: Path, *, run_id: str = "r1", scenario_id: str = SCENARIO,
         db_path: Path | None = None, release_id: str | None = None,
         binding_db_sha: str | None = None,
         binding_extra: dict | None = None) -> dict:
    manifest = json.loads((release / "release.json").read_text())
    binding = {
        "schema": "mtagentlens.run-binding/v4",
        "release_id": release_id or manifest["release_id"],
        "scenario_id": scenario_id,
        "collector_run_id": run_id,
        "database_sha256": binding_db_sha or _sha(db),
    }
    binding.update(binding_extra or {})
    return {
        "schema": "mtagentlens.run-binding/v4",
        "run_id": run_id,
        "scenario_id": scenario_id,
        "db_path": str(db_path or db),
        "run_dir": str(Path(db).parent / "run"),
        "run_binding": binding,
    }


def _setup(tmp_path: Path, monkeypatch, *, db_kwargs: dict | None = None,
           row_factory=None, validator=None) -> tuple[Path, Path, Path, Path, dict]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = _make_db(tmp_path / "source.db", **(db_kwargs or {}))
    release = _make_release(tmp_path)
    replay = _fake_replay(tmp_path)
    row = row_factory(db, release) if row_factory else _row(db, release)
    record = tmp_path / "run-record.json"
    record.write_text(json.dumps(row))

    def recompute(row_arg, release_arg):
        if validator is not None:
            validator(row_arg, release_arg, record)
        return None

    monkeypatch.setattr(run_artifacts, "validate_run_binding", recompute)
    return db, release, replay, record, row


def _package(monkeypatch, tmp_path: Path, **setup_kwargs):
    db, release, replay, record, row = _setup(tmp_path, monkeypatch, **setup_kwargs)
    out = tmp_path / "package"
    manifest = tp.package(db, replay, out, SCENARIO, release, record)
    return manifest, out, db, release, replay, record, row


def _expect(call, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        call()


def test_package_is_private_restricted_and_binds_run_record(tmp_path, monkeypatch):
    manifest, out, db, release, replay, record, row = _package(monkeypatch, tmp_path)
    manifest_text = (out / "manifest.json").read_text()
    assert manifest["schema"] == "mtagentlens.trace-package/v1"
    assert manifest["database_contains_captured_content"] is True
    assert manifest["distribution"] == "private-restricted"
    assert manifest["binding"]["release_id"] == json.loads(
        (release / "release.json").read_text())["release_id"]
    assert manifest["binding"]["scenario_id"] == SCENARIO
    assert manifest["binding"]["build_sha256"] == _sha(replay)
    assert manifest["binding"]["database_sha256"] == _sha(db)
    assert manifest["binding"]["run_record_sha256"] == _sha(record)
    assert manifest["binding"]["run_binding_sha256"] == _binding_digest(row["run_binding"])
    assert manifest["members"]["recording.db"] == _sha(db)
    assert manifest["validation"]["status"] == "ok"
    assert manifest["validation"]["run_count"] == 1
    assert manifest["validation"]["failure_count"] == 0
    assert (out / "recording.db").read_bytes() == db.read_bytes()
    assert (out.stat().st_mode & 0o777) == 0o700
    for name in ("manifest.json", "recording.db", "validation.json"):
        assert ((out / name).stat().st_mode & 0o777) == 0o600
    lowered = manifest_text.lower()
    for forbidden in ("content-free", "sanitized", "public"):
        assert forbidden not in lowered
    assert str(tmp_path) not in manifest_text
    assert "run_dir" not in manifest_text
    assert tp.main(["package", "--database", str(db), "--replay-binary", str(replay),
                    "--out", str(tmp_path / "cli-package"), "--scenario-id", SCENARIO,
                    "--release", str(release), "--run-record", str(record)]) == 0


def test_package_refuses_to_overwrite_existing_output(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(tmp_path, monkeypatch)
    out = tmp_path / "package"
    out.mkdir()
    sentinel = out / "keep"
    sentinel.write_text("owned")
    before = _sha(db)
    _expect(lambda: tp.package(db, replay, out, SCENARIO, release, record), "already exists")
    assert sentinel.read_text() == "owned"
    assert _sha(db) == before
    assert not list(tmp_path.glob(".trace-package-*"))


def test_package_rejects_nonempty_wal(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(tmp_path, monkeypatch)
    db.with_name(db.name + "-wal").write_bytes(b"pending")
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "sidecar")
    assert not (tmp_path / "package").exists()


def test_package_rejects_symlink_database(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(tmp_path, monkeypatch)
    linked = tmp_path / "linked.db"
    linked.symlink_to(db)
    _expect(lambda: tp.package(linked, replay, tmp_path / "package", SCENARIO, release, record),
            "regular non-symlink")
    assert not (tmp_path / "package").exists()


def test_package_rejects_non_v5_schema_before_replay(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(
        tmp_path, monkeypatch,
        db_kwargs={"user_version": 4, "recorded_version": 4})
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "schema")
    assert not (tmp_path / "package").exists()


def test_package_rejects_scenario_mismatch(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(
        tmp_path, monkeypatch, db_kwargs={"scenario": "other-case"})
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "scenario")


def test_package_rejects_missing_replay_binary(tmp_path, monkeypatch):
    db, release, _, record, _ = _setup(tmp_path, monkeypatch)
    _expect(lambda: tp.package(db, tmp_path / "missing-replay", tmp_path / "package",
                               SCENARIO, release, record), "replay binary")
    assert not (tmp_path / "package").exists()


def test_package_rejects_replay_exit_and_cleans_staging(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_REPLAY_MODE", "exit")
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "exit 3")
    assert not (tmp_path / "package").exists()
    assert not list(tmp_path.glob(".trace-package-*"))


def test_package_rejects_replay_report_with_run_failure(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_REPLAY_MODE", "run_failure")
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "not ok")
    assert not (tmp_path / "package").exists()


@pytest.mark.parametrize("mode", ["bad_read_only", "empty_runs", "invalid_json"])
def test_package_rejects_nonconforming_replay_report(tmp_path, monkeypatch, mode):
    db, release, replay, record, _ = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_REPLAY_MODE", mode)
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "replay validation")
    assert not (tmp_path / "package").exists()


def test_package_detects_source_mutation_and_replay_mutating_copy(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_REPLAY_MODE", "mutate_source")
    monkeypatch.setenv("FAKE_REPLAY_SOURCE", str(db))
    _expect(lambda: tp.package(db, replay, tmp_path / "package-a", SCENARIO, release, record),
            "source database changed")
    assert not (tmp_path / "package-a").exists()
    db2, release2, replay2, record2, _ = _setup(tmp_path / "second", monkeypatch)
    monkeypatch.setenv("FAKE_REPLAY_MODE", "mutate_copy")
    _expect(lambda: tp.package(db2, replay2, tmp_path / "second/package", SCENARIO,
                               release2, record2), "modified the packaged database")
    assert not (tmp_path / "second/package").exists()


def test_package_rejects_release_without_case_binding(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(tmp_path, monkeypatch)
    manifest = json.loads((release / "release.json").read_text())
    manifest["case_bindings"] = {}
    manifest["release_id"] = hashlib.sha256(
        json.dumps({k: v for k, v in manifest.items() if k != "release_id"},
                   sort_keys=True).encode()
    ).hexdigest()
    (release / "release.json").write_text(json.dumps(manifest, sort_keys=True))
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "binding")


def test_package_rejects_multi_run_database(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(
        tmp_path, monkeypatch, db_kwargs={"extra_run": True})
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "exactly one run")


def test_package_rejects_wrong_release_binding(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(
        tmp_path, monkeypatch,
        row_factory=lambda d, r: _row(d, r, release_id="0" * 64))
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "release_id")


def test_package_rejects_wrong_database_path_or_hash(tmp_path, monkeypatch):
    other = _make_db(tmp_path / "other.db")
    db, release, replay, record, _ = _setup(
        tmp_path, monkeypatch,
        row_factory=lambda d, r: _row(d, r, db_path=other))
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "source database")
    db2, release2, replay2, record2, _ = _setup(
        tmp_path / "second", monkeypatch,
        row_factory=lambda d, r: _row(d, r, binding_db_sha="0" * 64))
    _expect(lambda: tp.package(db2, replay2, tmp_path / "second/package", SCENARIO,
                               release2, record2), "database_sha256")


def test_package_rejects_wrong_run_id(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(
        tmp_path, monkeypatch,
        row_factory=lambda d, r: _row(d, r, run_id="r2"))
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "run")


def test_package_rejects_missing_run_record_proof(tmp_path, monkeypatch):
    def missing(row, release, record):
        raise ValueError("run directory artifacts are missing")

    db, release, replay, record, _ = _setup(tmp_path, monkeypatch, validator=missing)
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "independently verified")
    assert not (tmp_path / "package").exists()


def test_package_detects_run_record_source_mutation(tmp_path, monkeypatch):
    def mutate(row, release, record):
        record.write_text(json.dumps({**row, "extra": "changed"}))

    db, release, replay, record, _ = _setup(tmp_path, monkeypatch, validator=mutate)
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "run record changed")


def test_package_times_out_hung_replay(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(tp, "REPLAY_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setenv("FAKE_REPLAY_MODE", "sleep")
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "timed out")
    assert not (tmp_path / "package").exists()


def test_validate_rehashes_members_and_reruns_replay(tmp_path, monkeypatch):
    manifest, out, _, release, replay, record, _ = _package(monkeypatch, tmp_path)
    assert tp.validate(out, replay, release, record) == manifest
    assert tp.main(["validate", "--package", str(out), "--replay-binary", str(replay),
                    "--release", str(release), "--run-record", str(record)]) == 0
    (out / "recording.db").write_bytes((out / "recording.db").read_bytes() + b"tamper")
    _expect(lambda: tp.validate(out, replay, release, record), "modified")


def test_validate_rejects_tampered_report_or_manifest(tmp_path, monkeypatch):
    _, out, _, release, replay, record, _ = _package(monkeypatch, tmp_path)
    (out / "validation.json").write_text("{}\n")
    _expect(lambda: tp.validate(out, replay, release, record), "modified")
    _, out2, _, release2, replay2, record2, _ = _package(monkeypatch, tmp_path / "second")
    manifest = json.loads((out2 / "manifest.json").read_text())
    manifest["members"]["recording.db"] = "0" * 64
    (out2 / "manifest.json").write_text(json.dumps(manifest))
    _expect(lambda: tp.validate(out2, replay2, release2, record2), "modified")


def test_validate_rejects_release_drift_and_rerun_mismatch(tmp_path, monkeypatch):
    _, out, _, release, replay, record, _ = _package(monkeypatch, tmp_path)
    (release / "scenarios" / f"{SCENARIO}.json").write_text('{"id": "drifted"}')
    _expect(lambda: tp.validate(out, replay, release, record), "not canonical")
    _, out2, _, release2, replay2, record2, _ = _package(monkeypatch, tmp_path / "second")
    monkeypatch.setenv("FAKE_REPLAY_MODE", "bad_status")
    _expect(lambda: tp.validate(out2, replay2, release2, record2), "status")


def test_validate_rejects_changed_run_record_binding(tmp_path, monkeypatch):
    _, out, _, release, replay, record, row = _package(monkeypatch, tmp_path)
    changed = {**row, "run_binding": {**row["run_binding"], "note": "changed"}}
    record.write_text(json.dumps(changed))
    _expect(lambda: tp.validate(out, replay, release, record), "run record")


def test_package_rejects_nonempty_source_journal(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(tmp_path, monkeypatch)
    db.with_name(db.name + "-journal").write_bytes(b"pending-journal")
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "sidecar")
    assert not (tmp_path / "package").exists()


def test_package_rejects_unlisted_sidecar_after_replay(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_REPLAY_MODE", "journal_copy")
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "unexpected staging member")
    assert not (tmp_path / "package").exists()
    assert not list(tmp_path.glob(".trace-package-*"))


def test_package_rejects_replay_modifying_source_wal(tmp_path, monkeypatch):
    db, release, replay, record, _ = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_REPLAY_MODE", "source_wal")
    monkeypatch.setenv("FAKE_REPLAY_SOURCE", str(db))
    _expect(lambda: tp.package(db, replay, tmp_path / "package", SCENARIO, release, record),
            "sidecar")
    assert not (tmp_path / "package").exists()
    assert not list(tmp_path.glob(".trace-package-*"))



def test_gt_binding_ignores_auxiliary_review_and_causal_sidecars():
    from scripts.evaluation.trace_packager import _ground_truth_relative
    files = {name: '0' * 64 for name in (
        'ground_truth/benign/case-one.json',
        'ground_truth/_reviewers/case-one.json',
        'ground_truth/_causal/case-one.json')}
    assert _ground_truth_relative(files, 'case-one') == 'ground_truth/benign/case-one.json'


def test_canonical_release_binding_uses_the_real_ground_truth_layout():
    from scripts.evaluation.trace_packager import _bind_release
    binding = _bind_release(Path(__file__).resolve().parents[2] / 'datasets/FuxiBench', 'benign-multi-handoff')
    assert binding['scenario_id'] == 'benign-multi-handoff'
    assert len(binding['ground_truth_sha256']) == 64
