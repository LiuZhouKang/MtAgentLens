# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Offline admission and packaging for a current schema-5 SQLite recording.

The packaged database is copied verbatim and remains private: captured traces
may contain sensitive content. The manifest therefore carries only hashes and
counts; it never claims the database is content-free, sanitized, or public.
Admission also requires the independent run row and recomputes its binding from
the release, runner ledger, summary, and source database instead of trusting a
caller-supplied scenario id or a self-declared hash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compare.tools.dataset_release import validate_release
from compare.tools.detection_scope import DEFAULT_DATASET_DIR
from scripts.evaluation import run_artifacts

PACKAGE_SCHEMA = "mtagentlens.trace-package/v1"
VALIDATION_SCHEMA = "mtagentlens.recording-validation/v1"
SQLITE_SCHEMA_VERSION = 5
MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "recording.db"
VALIDATION_NAME = "validation.json"
REPLAY_TIMEOUT_SECONDS = 120


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _binding_digest(binding: dict) -> str:
    payload = json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be an existing regular non-symlink file")
    return path


def _require_quiescent(database: Path, label: str) -> Path:
    """A live SQLite database must not carry an active WAL or journal."""
    database = _regular_file(database, label)
    for suffix in ("-wal", "-journal"):
        sidecar = database.with_name(database.name + suffix)
        if sidecar.exists() and (sidecar.is_symlink() or sidecar.stat().st_size):
            raise ValueError(
                f"{label} has an active/nonempty {suffix} sidecar; "
                "stop the collector and finalize the recording"
            )
    return database


def _require_quiescent_source(database: Path) -> Path:
    return _require_quiescent(database, "source database")


def _require_staging_members(staging: Path, expected: set[str]) -> None:
    actual = {path.name for path in staging.iterdir()}
    if actual != expected:
        raise ValueError(f"unexpected staging members: {sorted(actual - expected)}")
    for name in expected:
        _regular_file(staging / name, f"staging member {name}")


def _sqlite_binding(database: Path, scenario_id: str) -> str:
    """Enforce SQLite schema 5 and exactly one run bound to this scenario."""
    uri = database.resolve().as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        recorded = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        ).fetchone()[0] if table else None
        if user_version != SQLITE_SCHEMA_VERSION or recorded != SQLITE_SCHEMA_VERSION:
            raise ValueError(
                "SQLite schema is not current v5: "
                f"user_version={user_version} schema_version={recorded}"
            )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        if not {"run_id", "scenario_id"} <= columns:
            raise ValueError("source database has no canonical runs table")
        rows = list(conn.execute("SELECT run_id, scenario_id FROM runs ORDER BY run_id"))
    if not rows:
        raise ValueError("source database has no run row")
    if len(rows) != 1:
        raise ValueError("source database must contain exactly one run/scenario")
    run_id, run_scenario = rows[0]
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("source database has a blank run id")
    if run_scenario != scenario_id:
        raise ValueError(
            f"run {run_id} is bound to scenario {run_scenario!r}, "
            f"not requested scenario {scenario_id!r}"
        )
    return run_id


def _ground_truth_relative(files: dict[str, str], scenario_id: str) -> str:
    matches = [
        relative for relative in files
        if relative.startswith("ground_truth/")
        and relative.endswith(f"/{scenario_id}.json")
        and not any(part.startswith("_") for part in Path(relative).parts[1:])
    ]
    if len(matches) != 1:
        raise ValueError(f"release must contain exactly one ground truth for {scenario_id!r}")
    return matches[0]


def _bind_release(release: Path, scenario_id: str) -> dict[str, str]:
    if not scenario_id or scenario_id in (".", "..") or "/" in scenario_id or "\\" in scenario_id:
        raise ValueError("scenario id must be a release-relative name")
    release = Path(release)
    try:
        manifest = validate_release(release)
    except (OSError, ValueError) as exc:
        raise ValueError(f"dataset release is not canonical: {exc}") from exc
    bindings = manifest.get("case_bindings")
    if not isinstance(bindings, dict) or not isinstance(bindings.get(scenario_id), dict):
        raise ValueError(f"release has no case binding for scenario {scenario_id!r}")
    binding = bindings[scenario_id]
    scenario_relative = f"scenarios/{scenario_id}.json"
    ground_truth_relative = _ground_truth_relative(manifest.get("files") or {}, scenario_id)
    scenario_path = _regular_file(release / scenario_relative, "release scenario")
    ground_truth_path = _regular_file(release / ground_truth_relative, "release ground truth")
    scenario_sha = _sha256_file(scenario_path)
    ground_truth_sha = _sha256_file(ground_truth_path)
    if binding.get("scenario_sha256") != scenario_sha:
        raise ValueError("release scenario does not match its case binding")
    if binding.get("ground_truth_sha256") != ground_truth_sha:
        raise ValueError("release ground truth does not match its case binding")
    try:
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"release scenario is not valid JSON: {exc}") from exc
    if not isinstance(scenario, dict) or scenario.get("id") != scenario_id:
        raise ValueError("release scenario id does not match the requested scenario")
    release_id = manifest.get("release_id")
    if not isinstance(release_id, str) or not release_id:
        raise ValueError("release manifest has no release_id")
    return {
        "release_id": release_id,
        "scenario_id": scenario_id,
        "scenario_sha256": scenario_sha,
        "ground_truth_sha256": ground_truth_sha,
    }


def _run_record_source(source: object) -> tuple[bytes, Path | None]:
    """Accept a path to the independent run row or an inline JSON object."""
    if isinstance(source, Path):
        path = _regular_file(source, "run record")
        return path.read_bytes(), path
    text = str(source)
    path = Path(text)
    if path.is_symlink() or path.exists():
        path = _regular_file(path, "run record")
        return path.read_bytes(), path
    return text.encode(), None


def _run_record_digest(source: object) -> str:
    raw, _ = _run_record_source(source)
    return hashlib.sha256(raw).hexdigest()


def _require_run_proof(run_record: object, release: Path, scenario_id: str,
                       release_id: str, expected_run_id: str, expected_db_sha: str,
                       source_db: Path | None) -> tuple[dict, str]:
    """Recompute the existing run binding and tie it to the recording bytes.

    A caller-provided scenario id or database hash is not proof. The run row is
    only accepted after ``run_artifacts.validate_run_binding`` independently
    rebuilds it from the release, runner ledger, summary, and source database.
    """
    record_sha_before = _run_record_digest(run_record)
    raw, record_path = _run_record_source(run_record)
    try:
        row = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"run record is not a JSON object: {exc}") from exc
    if not isinstance(row, dict):
        raise ValueError("run record must be a JSON object")
    try:
        run_artifacts.validate_run_binding(row, Path(release))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        raise ValueError(f"run record binding is not independently verified: {exc}") from exc
    if _run_record_digest(run_record) != record_sha_before:
        raise ValueError("run record changed while its binding was verified")
    run_binding = row.get("run_binding")
    if not isinstance(run_binding, dict):
        raise ValueError("run record has no recomputed run_binding")
    db_path = row.get("db_path")
    if not isinstance(db_path, str) or not db_path:
        raise ValueError("run record has no db_path")
    resolved_db = _regular_file(Path(db_path), "run record source database")
    if source_db is not None and resolved_db.resolve() != Path(source_db).resolve():
        raise ValueError("run record db_path does not resolve to the source database")
    if _sha256_file(resolved_db) != expected_db_sha:
        raise ValueError("run record source database does not match the packaged database hash")
    if row.get("run_id") != expected_run_id:
        raise ValueError("run record run_id does not match the single database run")
    if run_binding.get("collector_run_id") != expected_run_id:
        raise ValueError("run binding collector_run_id does not match the single database run")
    if row.get("scenario_id") != scenario_id or run_binding.get("scenario_id") != scenario_id:
        raise ValueError("run record scenario does not match the requested scenario")
    if run_binding.get("release_id") != release_id:
        raise ValueError("run record binding release_id does not match the current release")
    if run_binding.get("database_sha256") != expected_db_sha:
        raise ValueError("run binding database_sha256 does not match the packaged database")
    if record_path is not None and _run_record_digest(run_record) != record_sha_before:
        raise ValueError("run record changed while it was being admitted")
    return row, record_sha_before


def _validate_replay_report(report: object, expected_runs: list[str]) -> dict:
    if not isinstance(report, dict):
        raise ValueError("replay validation report is not a JSON object")
    if report.get("schema") != VALIDATION_SCHEMA:
        raise ValueError("replay validation report has an unexpected schema")
    if report.get("status") != "ok":
        raise ValueError(f"replay validation status is {report.get('status')!r}, not 'ok'")
    if report.get("read_only") is not True:
        raise ValueError("replay validation did not report read_only=true")
    failures = report.get("failures")
    if not isinstance(failures, list) or failures:
        raise ValueError("replay validation reported top-level failures")
    runs = report.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("replay validation reported no runs")
    observed = []
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("replay validation run entry is not an object")
        if run.get("status") != "ok":
            raise ValueError(f"replay validation run {run.get('run_id')!r} is not ok")
        run_failures = run.get("failures")
        if not isinstance(run_failures, list) or run_failures:
            raise ValueError(f"replay validation run {run.get('run_id')!r} reported failures")
        observed.append(run.get("run_id"))
    if sorted(observed) != sorted(expected_runs):
        raise ValueError("replay validation run set does not match the packaged database")
    return report


def _run_replay(replay_binary: Path, database: Path, expected_sha: str,
                expected_runs: list[str]) -> tuple[dict, str]:
    replay_binary = _regular_file(replay_binary, "replay binary")
    if not os.access(replay_binary, os.X_OK):
        raise ValueError("replay binary is not executable")
    build_sha = _sha256_file(replay_binary)
    try:
        proc = subprocess.run(
            [str(replay_binary), "--sqlite", str(database), "--validate-recording"],
            capture_output=True, text=True, timeout=REPLAY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            f"replay validation timed out after {REPLAY_TIMEOUT_SECONDS}s"
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        raise ValueError(
            f"replay validation failed with exit {proc.returncode}: "
            f"{detail[-1] if detail else 'no output'}"
        )
    try:
        report = json.loads(proc.stdout)
    except ValueError as exc:
        raise ValueError("replay validation did not print a JSON report") from exc
    report = _validate_replay_report(report, expected_runs)
    if _sha256_file(replay_binary) != build_sha:
        raise ValueError("replay binary changed while validating the recording")
    if _sha256_file(database) != expected_sha:
        raise ValueError("replay validation modified the packaged database")
    return report, build_sha


def _write_private(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(text)
    path.chmod(0o600)


def _manifest(binding: dict[str, str], build_sha: str, database_sha: str,
              report: dict, member_shas: dict[str, str], run_record_sha: str,
              run_binding_sha: str) -> dict:
    failures = len(report.get("failures") or []) + sum(
        len(run.get("failures") or []) for run in report.get("runs") or []
    )
    return {
        "schema": PACKAGE_SCHEMA,
        "database_contains_captured_content": True,
        "distribution": "private-restricted",
        "binding": {
            **binding,
            "build_sha256": build_sha,
            "database_sha256": database_sha,
            "run_record_sha256": run_record_sha,
            "run_binding_sha256": run_binding_sha,
        },
        "validation": {
            "schema": report["schema"],
            "status": report["status"],
            "read_only": report["read_only"],
            "run_count": len(report["runs"]),
            "failure_count": failures,
        },
        "members": member_shas,
    }


def package(database: Path, replay_binary: Path, out: Path, scenario_id: str,
            release: Path, run_record: object) -> dict:
    source = _require_quiescent_source(Path(database))
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise ValueError("output package already exists; refusing to overwrite")
    binding = _bind_release(Path(release), scenario_id)
    run_id = _sqlite_binding(source, scenario_id)
    source_sha = _sha256_file(source)
    row, run_record_sha = _require_run_proof(
        run_record, Path(release), scenario_id, binding["release_id"],
        run_id, source_sha, source,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".trace-package-", dir=out.parent))
    staging.chmod(0o700)
    committed = False
    try:
        copied = staging / DATABASE_NAME
        shutil.copyfile(source, copied)
        copied.chmod(0o600)
        if _sha256_file(source) != source_sha:
            raise ValueError("source database changed while it was copied")
        if _sha256_file(copied) != source_sha:
            raise ValueError("copied database does not match the source hash")
        report, build_sha = _run_replay(replay_binary, copied, source_sha, [run_id])
        _require_staging_members(staging, {DATABASE_NAME})
        _require_quiescent(copied, "packaged database")
        _require_quiescent(source, "source database")
        if _sha256_file(source) != source_sha:
            raise ValueError("source database changed during replay validation")
        if _run_record_digest(run_record) != run_record_sha:
            raise ValueError("run record changed during packaging")
        validation_path = staging / VALIDATION_NAME
        _write_private(
            validation_path, json.dumps(report, indent=2, sort_keys=True) + "\n"
        )
        _require_staging_members(staging, {DATABASE_NAME, VALIDATION_NAME})
        members = {
            DATABASE_NAME: _sha256_file(copied),
            VALIDATION_NAME: _sha256_file(validation_path),
        }
        manifest = _manifest(
            binding, build_sha, source_sha, report, members,
            run_record_sha, _binding_digest(row["run_binding"]),
        )
        _write_private(
            staging / MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        _require_staging_members(
            staging, {DATABASE_NAME, VALIDATION_NAME, MANIFEST_NAME}
        )
        if out.exists() or out.is_symlink():
            raise ValueError("output package appeared while staging; refusing to overwrite")
        os.rename(staging, out)
        committed = True
        return manifest
    finally:
        if not committed and staging.exists():
            shutil.rmtree(staging)


def _load_package(root: Path) -> tuple[dict, dict[str, str]]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("package must be an existing regular directory")
    manifest_path = _regular_file(root / MANIFEST_NAME, "package manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"package manifest is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != PACKAGE_SCHEMA:
        raise ValueError("package manifest has an unexpected schema")
    if manifest.get("database_contains_captured_content") is not True:
        raise ValueError("package manifest does not declare private captured content")
    if manifest.get("distribution") != "private-restricted":
        raise ValueError("package manifest is not private-restricted")
    members = manifest.get("members")
    if not isinstance(members, dict) or not members:
        raise ValueError("package manifest has no member hashes")
    clean: dict[str, str] = {}
    for name, digest in members.items():
        if not isinstance(name, str) or Path(name).name != name or name in (".", ".."):
            raise ValueError("package manifest has an unsafe member name")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("package manifest has a malformed member hash")
        clean[name] = digest
    if set(clean) != {DATABASE_NAME, VALIDATION_NAME}:
        raise ValueError("package manifest members are incomplete")
    actual = {path.name for path in root.iterdir()}
    if actual != set(clean) | {MANIFEST_NAME}:
        raise ValueError("package contains unlisted or missing files")
    for name, digest in clean.items():
        member = _regular_file(root / name, f"package member {name}")
        if _sha256_file(member) != digest:
            raise ValueError(f"package member {name} was modified")
    return manifest, clean


def validate(package_root: Path, replay_binary: Path, release: Path,
             run_record: object) -> dict:
    root = Path(package_root)
    manifest, members = _load_package(root)
    binding = manifest.get("binding")
    if not isinstance(binding, dict):
        raise ValueError("package manifest has no binding")
    scenario_id = manifest.get("scenario_id")
    if not isinstance(scenario_id, str):
        binding_scenario = binding.get("scenario_id")
        scenario_id = binding_scenario if isinstance(binding_scenario, str) else None
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ValueError("package manifest has no scenario id")
    expected = _bind_release(Path(release), scenario_id)
    for key, value in expected.items():
        if binding.get(key) != value:
            raise ValueError(f"package release binding changed: {key}")
    database_sha = members[DATABASE_NAME]
    if binding.get("database_sha256") != database_sha:
        raise ValueError("package database hash does not match its binding")
    _require_quiescent(root / DATABASE_NAME, "packaged database")
    run_id = _sqlite_binding(root / DATABASE_NAME, scenario_id)
    row, run_record_sha = _require_run_proof(
        run_record, Path(release), scenario_id, expected["release_id"],
        run_id, database_sha, None,
    )
    if binding.get("run_record_sha256") != run_record_sha:
        raise ValueError("run record hash does not match the package manifest")
    if binding.get("run_binding_sha256") != _binding_digest(row["run_binding"]):
        raise ValueError("run binding digest does not match the package manifest")
    replay_binary = _regular_file(Path(replay_binary), "replay binary")
    build_sha = _sha256_file(replay_binary)
    if binding.get("build_sha256") != build_sha:
        raise ValueError("current replay build hash does not match the package binding")
    report, _ = _run_replay(replay_binary, root / DATABASE_NAME, database_sha, [run_id])
    _require_staging_members(
        root, {MANIFEST_NAME, DATABASE_NAME, VALIDATION_NAME}
    )
    _require_quiescent(root / DATABASE_NAME, "packaged database")
    for name, digest in members.items():
        if _sha256_file(root / name) != digest:
            raise ValueError(f"package member {name} changed during validation")
    recorded = json.loads((root / VALIDATION_NAME).read_text(encoding="utf-8"))
    if recorded != report:
        raise ValueError("replay validation report differs from the packaged report")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    package_cmd = commands.add_parser("package", help="copy, validate, and seal a recording")
    package_cmd.add_argument("--database", type=Path, required=True)
    package_cmd.add_argument("--replay-binary", type=Path, required=True)
    package_cmd.add_argument("--out", type=Path, required=True)
    package_cmd.add_argument("--scenario-id", required=True)
    package_cmd.add_argument("--release", type=Path, default=Path(DEFAULT_DATASET_DIR))
    package_cmd.add_argument(
        "--run-record", required=True,
        help="independent run row JSON file or inline JSON object",
    )
    validate_cmd = commands.add_parser("validate", help="rehash a package and rerun strict replay")
    validate_cmd.add_argument("--package", type=Path, required=True)
    validate_cmd.add_argument("--replay-binary", type=Path, required=True)
    validate_cmd.add_argument("--release", type=Path, default=Path(DEFAULT_DATASET_DIR))
    validate_cmd.add_argument(
        "--run-record", required=True,
        help="independent run row JSON file or inline JSON object",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "package":
            package(args.database, args.replay_binary, args.out,
                    args.scenario_id, args.release, args.run_record)
        else:
            validate(args.package, args.replay_binary, args.release, args.run_record)
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f"trace packager: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
