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
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compare.tools.dataset_release import validate_release
from compare.tools.detection_scope import DEFAULT_DATASET_DIR
from scripts.evaluation import run_artifacts

PACKAGE_SCHEMA = "mtagentlens.trace-package/v1"
UNBOUND_PACKAGE_SCHEMA = "mtagentlens.trace-package/v2"
PUBLIC_PACKAGE_SCHEMA = "mtagentlens.trace-public-package/v1"
PUBLIC_DERIVATION_SCHEMA = "mtagentlens.trace-public-derivation/v1"
DERIVATION_MAPPING_SCHEMA = "mtagentlens.trace-public-mapping/v1"
VALIDATION_SCHEMA = "mtagentlens.recording-validation/v1"
COHORT_SCHEMA = "mtagentlens.trace-cohort-audit/v1"
SQLITE_SCHEMA_VERSION = 5
MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "recording.db"
VALIDATION_NAME = "validation.json"
PUBLIC_DERIVED_NAME = "derived.json"
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


def _bind_release(release: Path, scenario_id: str) -> dict[str, str | None]:
    if not scenario_id or scenario_id in (".", "..") or "/" in scenario_id or "\\" in scenario_id:
        raise ValueError("scenario id must be a release-relative name")
    release = Path(release)
    manifest_path = release / "release.json"
    if not manifest_path.exists() and not manifest_path.is_symlink():
        if release.is_symlink() or not release.is_dir():
            raise ValueError("dataset must be a non-symlink directory")
        root = release.resolve()
        scenario_path = _regular_file(release / "scenarios" / f"{scenario_id}.json", "dataset scenario")
        if (release / "scenarios").is_symlink() or not scenario_path.resolve().is_relative_to(root):
            raise ValueError("scenario path escapes dataset")
        truth_root = release / "ground_truth"
        if truth_root.is_symlink() or not truth_root.is_dir():
            raise ValueError("ground truth directory is missing or a symlink")
        candidates = {path.relative_to(release).as_posix(): ""
                      for path in truth_root.rglob(f"{scenario_id}.json")}
        truth_relative = _ground_truth_relative(candidates, scenario_id)
        truth_path = _regular_file(release / truth_relative, "dataset ground truth")
        if not truth_path.resolve().is_relative_to(root):
            raise ValueError("ground truth path escapes dataset")
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        if not isinstance(scenario, dict) or scenario.get("id") != scenario_id:
            raise ValueError("dataset scenario identity does not match requested scenario")
        if not isinstance(truth, dict) or truth.get("scenario_id") != scenario_id:
            raise ValueError("dataset ground truth identity does not match requested scenario")
        return {"release_id": None, "source_status": "unbound", "scenario_id": scenario_id,
                "scenario_sha256": _sha256_file(scenario_path),
                "ground_truth_sha256": _sha256_file(truth_path)}
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
                       release_id: str | None, expected_run_id: str, expected_db_sha: str,
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
    if "release_id" not in run_binding or run_binding["release_id"] != release_id:
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
        "schema": UNBOUND_PACKAGE_SCHEMA if binding.get("source_status") == "unbound" else PACKAGE_SCHEMA,
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
    if not isinstance(manifest, dict) or manifest.get("schema") not in (PACKAGE_SCHEMA, UNBOUND_PACKAGE_SCHEMA):
        raise ValueError("package manifest has an unexpected schema")
    binding = manifest.get("binding")
    if not isinstance(binding, dict):
        raise ValueError("package manifest has no binding")
    if manifest["schema"] == UNBOUND_PACKAGE_SCHEMA:
        if binding.get("source_status") != "unbound" or "release_id" not in binding or binding["release_id"] is not None:
            raise ValueError("unbound package must not claim a historical release identity")
    elif not isinstance(binding.get("release_id"), str) or not binding["release_id"]:
        raise ValueError("historical package requires a release identity")
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


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _table_count(conn: sqlite3.Connection, name: str) -> int | None:
    if not _table_exists(conn, name):
        return None
    return int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])


def _json_array_length(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return len(parsed) if isinstance(parsed, list) else None



_TOKEN_RE = re.compile(r"^[0-9a-f]{24}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PHASES = frozenset({
    "init", "initial", "pre", "during", "post", "final", "completed",
    "running", "pending", "unknown", "none",
})
_SEVERITIES = frozenset({"info", "low", "medium", "high", "critical"})
_DECISIONS = frozenset({"Unsafe", "Suspicious", "Safe", "Unknown"})
_CLAIM_STATES = frozenset({
    "asserted", "refuted", "weakened", "unresolved", "provisional", "confirmed",
})
_CLAIM_TYPES = frozenset({
    "exfiltration", "boundary_violation", "unsafe_action", "unauthorized_access",
    "policy_violation", "data_flow", "violation", "unknown",
})
_NODE_TYPES = frozenset({
    "action", "agent", "resource", "boundary", "event", "tool", "process",
    "network", "file", "unknown",
})
_EDGE_TYPES = frozenset({
    "read", "write", "send", "recv", "spawn", "call", "access", "delegate",
    "flow", "unknown",
})
_AUTHORITIES = frozenset({
    "collector", "agent", "policy", "observer", "rule", "system", "unknown",
})
_PUBLIC_TABLES = (
    "actions", "agent_identities", "claim_refinements", "classification_refinements",
    "context_pollution_events", "correlation_evidence", "delegation_records",
    "evidence_refinements", "graph_edges", "graph_nodes", "graph_refinements",
    "graph_snapshots", "identity_process_beliefs", "identity_refinements",
    "intents", "message_bindings", "non_link_diagnostics", "policy_evaluations",
    "process_events", "provisional_identities", "responsibility_alerts",
    "responsibility_claims", "responsibility_tracebacks", "runs",
    "subagent_boundaries", "tls_sessions",
)
_EXCLUDED_CONTENT = [
    "tool arguments and normalized command lines",
    "captured request contexts and TLS payloads",
    "resources, paths, evidence payloads and graph JSON",
    "claim/alert prose, explanations, titles, summaries and reasons",
    "decision provenance details",
]
_REPLAY_SCOPE = (
    "none: review-only projection; the private recording remains the evidence"
)


def _public_text(deriver: _Deriver, domain: str, value: object,
                 allowed: frozenset[str] = frozenset()) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value in allowed:
        return value
    return deriver.token(domain, value)


def _require_public_keys(payload: object, expected: set[str], label: str) -> None:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} is not a JSON object")
    actual = set(payload)
    if actual != expected:
        raise ValueError(
            f"{label} has unallowlisted keys: "
            f"extra={sorted(actual - expected)} missing={sorted(expected - actual)}"
        )


def _require_public_token(value: object, label: str, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not _TOKEN_RE.fullmatch(value):
        raise ValueError(f"{label} is not a valid opaque token")


def _require_public_digest(value: object, label: str) -> None:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{label} is not a valid sha256 digest")


def _require_public_number(value: object, label: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} is not a number")


def _require_public_int(value: object, label: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} is not an integer")


def _require_public_enum_or_token(value: object, allowed: frozenset[str],
                                  label: str) -> None:
    if value is None:
        return
    if isinstance(value, str) and (value in allowed or _TOKEN_RE.fullmatch(value)):
        return
    raise ValueError(f"{label} is not an allowed enum or opaque token")


def _validate_public_projection(payload: object, *, require_provenance: bool = True) -> None:
    """Reject any public projection that could carry raw free text or ids."""
    base = {
        "schema", "analysis_input_changed", "traces_replayable", "review_only",
        "raw_captured_content_included", "verdict_consistency_revalidated",
        "identity_namespace", "run", "agents", "claims", "alerts", "tracebacks",
        "policy_evaluations", "graph", "table_counts", "excluded_content",
        "replay_scope",
    }
    provenance = {
        "source_private_package_manifest_sha256",
        "source_private_database_sha256",
        "derivation_mapping_sha256",
    }
    if not isinstance(payload, dict):
        raise ValueError("public projection is not a JSON object")
    actual = set(payload)
    allowed = base | provenance
    if actual - allowed:
        raise ValueError(
            f"public projection has unallowlisted keys: {sorted(actual - allowed)}")
    if require_provenance and actual != allowed:
        raise ValueError("public projection is missing derivation provenance")
    if not require_provenance and actual != base:
        raise ValueError("public projection has unallowlisted or missing keys")

    if payload.get("schema") != PUBLIC_DERIVATION_SCHEMA:
        raise ValueError("public projection has an unexpected schema")
    for key, expected in (
        ("analysis_input_changed", True), ("traces_replayable", False),
        ("review_only", True), ("raw_captured_content_included", False),
        ("verdict_consistency_revalidated", False),
    ):
        if payload.get(key) is not expected:
            raise ValueError(f"public projection flag {key} must be {expected!r}")
    if payload.get("identity_namespace") != _Deriver.namespace:
        raise ValueError("public projection has an unexpected identity namespace")

    run = payload.get("run")
    _require_public_keys(run, {
        "run_id", "scenario_id", "started_at_ns", "ended_at_ns", "duration_ns",
        "collector_version", "final_decision", "final_severity", "final_authority",
    }, "public run")
    _require_public_token(run["run_id"], "public run.run_id")
    _require_public_token(run["scenario_id"], "public run.scenario_id")
    _require_public_int(run["started_at_ns"], "public run.started_at_ns")
    _require_public_int(run["ended_at_ns"], "public run.ended_at_ns")
    _require_public_int(run["duration_ns"], "public run.duration_ns")
    _require_public_token(
        run["collector_version"], "public run.collector_version", optional=True)
    _require_public_enum_or_token(
        run["final_decision"], _DECISIONS, "public run.final_decision")
    _require_public_enum_or_token(
        run["final_severity"], _SEVERITIES, "public run.final_severity")
    _require_public_enum_or_token(
        run["final_authority"], _AUTHORITIES, "public run.final_authority")

    agents = payload.get("agents")
    if not isinstance(agents, list):
        raise ValueError("public agents is not a list")
    for item in agents:
        _require_public_keys(item, {"agent_id", "confidence"}, "public agent")
        _require_public_token(item["agent_id"], "public agent.agent_id", optional=True)
        _require_public_number(item["confidence"], "public agent.confidence")

    claims = payload.get("claims")
    if not isinstance(claims, list):
        raise ValueError("public claims is not a list")
    for item in claims:
        _require_public_keys(item, {
            "claim_id", "claim_type", "subject_agent_id", "boundary_id",
            "claim_state", "severity", "confidence", "phase",
        }, "public claim")
        _require_public_token(item["claim_id"], "public claim.claim_id")
        _require_public_token(
            item["subject_agent_id"], "public claim.subject_agent_id", optional=True)
        _require_public_token(item["boundary_id"], "public claim.boundary_id")
        _require_public_enum_or_token(
            item["claim_type"], _CLAIM_TYPES, "public claim.claim_type")
        _require_public_enum_or_token(
            item["claim_state"], _CLAIM_STATES, "public claim.claim_state")
        _require_public_enum_or_token(
            item["severity"], _SEVERITIES, "public claim.severity")
        _require_public_number(item["confidence"], "public claim.confidence")
        _require_public_enum_or_token(
            item["phase"], _PHASES, "public claim.phase")

    alerts = payload.get("alerts")
    if not isinstance(alerts, list):
        raise ValueError("public alerts is not a list")
    for item in alerts:
        _require_public_keys(item, {
            "alert_id", "decision", "severity", "confidence", "authority",
            "phase", "primary_agent_id",
        }, "public alert")
        _require_public_token(item["alert_id"], "public alert.alert_id")
        _require_public_token(
            item["primary_agent_id"], "public alert.primary_agent_id", optional=True)
        _require_public_enum_or_token(
            item["decision"], _DECISIONS, "public alert.decision")
        _require_public_enum_or_token(
            item["severity"], _SEVERITIES, "public alert.severity")
        _require_public_number(item["confidence"], "public alert.confidence")
        _require_public_enum_or_token(
            item["authority"], _AUTHORITIES, "public alert.authority")
        _require_public_enum_or_token(
            item["phase"], _PHASES, "public alert.phase")

    tracebacks = payload.get("tracebacks")
    if not isinstance(tracebacks, list):
        raise ValueError("public tracebacks is not a list")
    for item in tracebacks:
        _require_public_keys(item, {
            "traceback_id", "claim_id", "root_cause_node_id", "confidence",
            "chain_node_count", "chain_edge_count",
        }, "public traceback")
        for key in ("traceback_id", "claim_id", "root_cause_node_id"):
            _require_public_token(item[key], f"public traceback.{key}", optional=True)
        _require_public_number(item["confidence"], "public traceback.confidence")
        _require_public_int(item["chain_node_count"], "public traceback.chain_node_count")
        _require_public_int(item["chain_edge_count"], "public traceback.chain_edge_count")

    policies = payload.get("policy_evaluations")
    if not isinstance(policies, list):
        raise ValueError("public policy_evaluations is not a list")
    for item in policies:
        _require_public_keys(item, {
            "policy_id", "boundary", "subject_agent_id", "confidence",
        }, "public policy evaluation")
        _require_public_token(item["policy_id"], "public policy.policy_id")
        _require_public_token(item["boundary"], "public policy.boundary")
        _require_public_token(
            item["subject_agent_id"], "public policy.subject_agent_id", optional=True)
        _require_public_number(item["confidence"], "public policy.confidence")

    graph = payload.get("graph")
    _require_public_keys(graph, {"nodes", "edges"}, "public graph")
    if not isinstance(graph["nodes"], list) or not isinstance(graph["edges"], list):
        raise ValueError("public graph members are not lists")
    for item in graph["nodes"]:
        _require_public_keys(item, {
            "node_id", "node_type", "agent_id", "phase",
        }, "public node")
        _require_public_token(item["node_id"], "public node.node_id")
        _require_public_enum_or_token(
            item["node_type"], _NODE_TYPES, "public node.node_type")
        _require_public_token(item["agent_id"], "public node.agent_id", optional=True)
        _require_public_enum_or_token(
            item["phase"], _PHASES, "public node.phase")
    for item in graph["edges"]:
        _require_public_keys(item, {
            "edge_id", "edge_type", "source_node_id", "target_node_id", "phase",
            "confidence",
        }, "public edge")
        _require_public_token(item["edge_id"], "public edge.edge_id")
        _require_public_enum_or_token(
            item["edge_type"], _EDGE_TYPES, "public edge.edge_type")
        _require_public_token(
            item["source_node_id"], "public edge.source_node_id", optional=True)
        _require_public_token(
            item["target_node_id"], "public edge.target_node_id", optional=True)
        _require_public_enum_or_token(
            item["phase"], _PHASES, "public edge.phase")
        _require_public_number(item["confidence"], "public edge.confidence")

    counts = payload.get("table_counts")
    _require_public_keys(counts, set(_PUBLIC_TABLES), "public table_counts")
    for name, value in counts.items():
        _require_public_int(value, f"public table_counts.{name}")
    if payload.get("excluded_content") != _EXCLUDED_CONTENT:
        raise ValueError("public projection has unexpected excluded_content")
    if payload.get("replay_scope") != _REPLAY_SCOPE:
        raise ValueError("public projection has an unexpected replay_scope")
    if require_provenance:
        for key in sorted(provenance):
            _require_public_digest(payload[key], f"public projection {key}")


class _Deriver:
    """Deterministic per-domain pseudonyms backed by a private secret."""

    namespace = "hmac-sha256/domain/v1"

    def __init__(self, secret: bytes):
        self.secret = secret
        self.tokens: dict[str, dict[str, str]] = {}

    def token(self, domain: str, value: object) -> str | None:
        if value is None:
            return None
        original = str(value)
        bucket = self.tokens.setdefault(domain, {})
        if original not in bucket:
            digest = hmac.new(
                self.secret, f"{domain}\x00{original}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            bucket[original] = digest[:24]
        return bucket[original]


def _public_projection(conn: sqlite3.Connection, deriver: _Deriver) -> dict:
    """Project structural analysis output, never captured content.

    Free-text, tool arguments, resources, evidence payloads and graph JSON are
    deliberately absent. Every identifier and unbounded scalar is either a
    validated fixed enum or an HMAC token, so paths and prompt-like text cannot
    survive into the public projection.
    """
    conn.row_factory = sqlite3.Row
    runs = list(conn.execute(
        "SELECT run_id, scenario_id, started_at_ns, ended_at_ns, collector_version, "
        "final_decision, final_severity, final_authority FROM runs ORDER BY run_id"
    ))
    if len(runs) != 1:
        raise ValueError("public derivation requires exactly one run")
    run = dict(runs[0])
    started, ended = run.get("started_at_ns"), run.get("ended_at_ns")
    duration = ended - started if isinstance(started, int) and isinstance(ended, int) else None

    claims = []
    if _table_exists(conn, "responsibility_claims"):
        for row in conn.execute(
            "SELECT claim_id, claim_type, subject_agent_id, boundary_id, claim_state, "
            "severity, confidence, phase FROM responsibility_claims ORDER BY claim_id"
        ):
            claims.append({
                "claim_id": deriver.token("claim", row["claim_id"]),
                "claim_type": _public_text(
                    deriver, "claim_type", row["claim_type"], _CLAIM_TYPES),
                "subject_agent_id": deriver.token("agent", row["subject_agent_id"]),
                "boundary_id": deriver.token("boundary", row["boundary_id"]),
                "claim_state": _public_text(
                    deriver, "claim_state", row["claim_state"], _CLAIM_STATES),
                "severity": _public_text(
                    deriver, "severity", row["severity"], _SEVERITIES),
                "confidence": row["confidence"],
                "phase": _public_text(deriver, "phase", row["phase"], _PHASES),
            })

    alerts = []
    if _table_exists(conn, "responsibility_alerts"):
        for row in conn.execute(
            "SELECT alert_id, decision, severity, confidence, authority, phase, "
            "primary_agent_id FROM responsibility_alerts ORDER BY alert_id"
        ):
            alerts.append({
                "alert_id": deriver.token("alert", row["alert_id"]),
                "decision": _public_text(
                    deriver, "decision", row["decision"], _DECISIONS),
                "severity": _public_text(
                    deriver, "severity", row["severity"], _SEVERITIES),
                "confidence": row["confidence"],
                "authority": _public_text(
                    deriver, "authority", row["authority"], _AUTHORITIES),
                "phase": _public_text(deriver, "phase", row["phase"], _PHASES),
                "primary_agent_id": deriver.token("agent", row["primary_agent_id"]),
            })

    tracebacks = []
    if _table_exists(conn, "responsibility_tracebacks"):
        for row in conn.execute(
            "SELECT traceback_id, claim_id, root_cause_node_id, confidence, "
            "chain_node_ids_json, chain_edge_ids_json FROM responsibility_tracebacks "
            "ORDER BY traceback_id"
        ):
            tracebacks.append({
                "traceback_id": deriver.token("traceback", row["traceback_id"]),
                "claim_id": deriver.token("claim", row["claim_id"]),
                "root_cause_node_id": deriver.token("node", row["root_cause_node_id"]),
                "confidence": row["confidence"],
                "chain_node_count": _json_array_length(row["chain_node_ids_json"]),
                "chain_edge_count": _json_array_length(row["chain_edge_ids_json"]),
            })

    actors = set()
    if _table_exists(conn, "agent_identities"):
        for row in conn.execute(
            "SELECT agent_id, confidence FROM agent_identities ORDER BY agent_id"
        ):
            actors.add((str(row["agent_id"]), row["confidence"]))
    agents = [{"agent_id": deriver.token("agent", agent_id), "confidence": confidence}
              for agent_id, confidence in sorted(actors)]

    nodes = []
    if _table_exists(conn, "graph_nodes"):
        for row in conn.execute(
            "SELECT node_id, node_type, agent_id, phase FROM graph_nodes ORDER BY node_id"
        ):
            nodes.append({
                "node_id": deriver.token("node", row["node_id"]),
                "node_type": _public_text(
                    deriver, "node_type", row["node_type"], _NODE_TYPES),
                "agent_id": deriver.token("agent", row["agent_id"]),
                "phase": _public_text(deriver, "phase", row["phase"], _PHASES),
            })

    edges = []
    if _table_exists(conn, "graph_edges"):
        for row in conn.execute(
            "SELECT edge_id, edge_type, source_node_id, target_node_id, phase, confidence "
            "FROM graph_edges ORDER BY edge_id"
        ):
            edges.append({
                "edge_id": deriver.token("edge", row["edge_id"]),
                "edge_type": _public_text(
                    deriver, "edge_type", row["edge_type"], _EDGE_TYPES),
                "source_node_id": deriver.token("node", row["source_node_id"]),
                "target_node_id": deriver.token("node", row["target_node_id"]),
                "phase": _public_text(deriver, "phase", row["phase"], _PHASES),
                "confidence": row["confidence"],
            })

    policies = []
    if _table_exists(conn, "policy_evaluations"):
        for row in conn.execute(
            "SELECT policy_id, boundary, subject_agent_id, confidence "
            "FROM policy_evaluations ORDER BY policy_id"
        ):
            policies.append({
                "policy_id": deriver.token("policy", row["policy_id"]),
                "boundary": deriver.token("boundary", row["boundary"]),
                "subject_agent_id": deriver.token("agent", row["subject_agent_id"]),
                "confidence": row["confidence"],
            })

    return {
        "schema": PUBLIC_DERIVATION_SCHEMA,
        "analysis_input_changed": True,
        "traces_replayable": False,
        "review_only": True,
        "raw_captured_content_included": False,
        "verdict_consistency_revalidated": False,
        "identity_namespace": deriver.namespace,
        "run": {
            "run_id": deriver.token("run", run["run_id"]),
            "scenario_id": deriver.token("scenario", run["scenario_id"]),
            "started_at_ns": started,
            "ended_at_ns": ended,
            "duration_ns": duration,
            "collector_version": deriver.token(
                "collector_version", run["collector_version"]),
            "final_decision": _public_text(
                deriver, "decision", run["final_decision"], _DECISIONS),
            "final_severity": _public_text(
                deriver, "severity", run["final_severity"], _SEVERITIES),
            "final_authority": _public_text(
                deriver, "authority", run["final_authority"], _AUTHORITIES),
        },
        "agents": agents,
        "claims": claims,
        "alerts": alerts,
        "tracebacks": tracebacks,
        "policy_evaluations": policies,
        "graph": {"nodes": nodes, "edges": edges},
        "table_counts": {name: _table_count(conn, name) for name in _PUBLIC_TABLES},
        "excluded_content": list(_EXCLUDED_CONTENT),
        "replay_scope": _REPLAY_SCOPE,
    }


def _write_private_exclusive(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _public_manifest(source_manifest: dict, source_manifest_sha: str,
                     source_database_sha: str, mapping_sha: str,
                     derived_sha: str) -> dict:
    binding = source_manifest.get("binding") if isinstance(source_manifest.get("binding"), dict) else {}
    return {
        "schema": PUBLIC_PACKAGE_SCHEMA,
        **({"source_status": "unbound"} if binding.get("source_status") == "unbound" else {}),
        "distribution": "public-derived-review-only",
        "analysis_input_changed": True,
        "traces_replayable": False,
        "review_only": True,
        "source_private_package": {
            "manifest_sha256": source_manifest_sha,
            "database_sha256": source_database_sha,
            "build_sha256": binding.get("build_sha256"),
            "database_contains_captured_content": True,
        },
        "binding": {
            key: binding.get(key)
            for key in ("release_id", "scenario_sha256", "ground_truth_sha256")
        },
        "derivation": {
            "schema": PUBLIC_DERIVATION_SCHEMA,
            "identity_namespace": _Deriver.namespace,
            "mapping_sha256": mapping_sha,
            "analysis_input_changed": True,
            "traces_replayable": False,
            "review_only": True,
            "raw_captured_content_included": False,
            "verdict_consistency_revalidated": False,
            "source_package_modified": False,
        },
        "members": {PUBLIC_DERIVED_NAME: derived_sha},
    }


def derive_public(package_root: Path, out: Path, mapping_out: Path) -> dict:
    """Create a review-only public projection from a private package.

    The source package is read, never rewritten. Pseudonyms come from a fresh
    secret stored only in ``mapping_out`` (0600); the public projection carries
    no original run/agent/node ids and no captured content. Because the
    analysis input is changed, the result is explicitly not replayable and no
    old hash, verdict run or result is presented as the original evidence.
    """
    root = Path(package_root)
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise ValueError("public package output already exists; refusing to overwrite")
    mapping_out = Path(mapping_out)
    if mapping_out.exists() or mapping_out.is_symlink():
        raise ValueError("derivation mapping already exists; refusing to overwrite")
    try:
        mapping_out.resolve().relative_to(out.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("derivation mapping must stay outside the public package")

    source_manifest_path = root / MANIFEST_NAME
    source_manifest, _members = _load_package(root)
    database = root / DATABASE_NAME
    source_manifest_sha = _sha256_file(source_manifest_path)
    source_database_sha = _sha256_file(database)
    if source_manifest.get("binding", {}).get("database_sha256") != source_database_sha:
        raise ValueError("private package database hash does not match its manifest binding")
    binding = source_manifest.get("binding") or {}
    scenario_id = binding.get("scenario_id")
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ValueError("private package has no scenario binding")
    _sqlite_binding(database, scenario_id)

    secret = secrets.token_bytes(32)
    deriver = _Deriver(secret)
    try:
        with closing(sqlite3.connect(
            database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
        )) as conn:
            projection = _public_projection(conn, deriver)
        if _sha256_file(database) != source_database_sha:
            raise ValueError("source database changed during public derivation")
        if _sha256_file(source_manifest_path) != source_manifest_sha:
            raise ValueError("private package manifest changed during public derivation")
        _load_package(root)

        mapping = {
            "schema": DERIVATION_MAPPING_SCHEMA,
            "identity_namespace": deriver.namespace,
            "source_private_package": {
                "manifest_sha256": source_manifest_sha,
                "database_sha256": source_database_sha,
            },
            "secret_hex": secret.hex(),
            "tokens": deriver.tokens,
            "distribution": "private-restricted",
        }
        mapping_text = json.dumps(mapping, indent=2, sort_keys=True) + "\n"
        _write_private_exclusive(mapping_out, mapping_text)
        mapping_sha = hashlib.sha256(mapping_text.encode()).hexdigest()
        projection.update({
            "source_private_package_manifest_sha256": source_manifest_sha,
            "source_private_database_sha256": source_database_sha,
            "derivation_mapping_sha256": mapping_sha,
        })
        _validate_public_projection(projection)
        derived_text = json.dumps(projection, indent=2, sort_keys=True) + "\n"
        derived_sha = hashlib.sha256(derived_text.encode()).hexdigest()
        manifest = _public_manifest(
            source_manifest, source_manifest_sha, source_database_sha,
            mapping_sha, derived_sha,
        )

        out.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".trace-public-", dir=out.parent))
        staging.chmod(0o700)
        committed = False
        try:
            (staging / PUBLIC_DERIVED_NAME).write_text(derived_text, encoding="utf-8")
            (staging / PUBLIC_DERIVED_NAME).chmod(0o644)
            (staging / MANIFEST_NAME).write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            (staging / MANIFEST_NAME).chmod(0o644)
            _load_public_package(staging)
            if out.exists() or out.is_symlink():
                raise ValueError("public package appeared while staging; refusing to overwrite")
            os.rename(staging, out)
            committed = True
        finally:
            if not committed and staging.exists():
                shutil.rmtree(staging)
        return manifest
    except Exception:
        if mapping_out.exists() and not mapping_out.is_symlink():
            mapping_out.unlink()
        raise


def _load_public_package(root: Path) -> tuple[dict, dict[str, str]]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("public package must be an existing regular directory")
    manifest_path = _regular_file(root / MANIFEST_NAME, "public package manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"public package manifest is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != PUBLIC_PACKAGE_SCHEMA:
        raise ValueError("public package manifest has an unexpected schema")
    binding = manifest.get("binding")
    if not isinstance(binding, dict):
        raise ValueError("public package has no source binding")
    if binding.get("release_id") is None:
        if manifest.get("source_status") != "unbound":
            raise ValueError("public package must disclose its unbound source")
    elif manifest.get("source_status") == "unbound":
        raise ValueError("unbound public package claims a release identity")
    if manifest.get("distribution") != "public-derived-review-only":
        raise ValueError("public package is not a derived review-only distribution")
    for key, expected in (("analysis_input_changed", True), ("traces_replayable", False),
                          ("review_only", True)):
        if manifest.get(key) is not expected:
            raise ValueError(f"public package flag {key} must be {expected!r}")
    derivation = manifest.get("derivation")
    if not isinstance(derivation, dict) or derivation.get("schema") != PUBLIC_DERIVATION_SCHEMA:
        raise ValueError("public package has no derivation contract")
    for key, expected in (("analysis_input_changed", True), ("traces_replayable", False),
                          ("review_only", True), ("raw_captured_content_included", False),
                          ("verdict_consistency_revalidated", False),
                          ("source_package_modified", False)):
        if derivation.get(key) is not expected:
            raise ValueError(f"public derivation flag {key} must be {expected!r}")
    source = manifest.get("source_private_package")
    if not isinstance(source, dict) or source.get("database_contains_captured_content") is not True:
        raise ValueError("public package does not disclose its private captured source")
    members = manifest.get("members")
    if not isinstance(members, dict) or set(members) != {PUBLIC_DERIVED_NAME}:
        raise ValueError("public package members are incomplete")
    digest = members[PUBLIC_DERIVED_NAME]
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("public package has a malformed member hash")
    actual = {path.name for path in root.iterdir()}
    if actual != {MANIFEST_NAME, PUBLIC_DERIVED_NAME}:
        raise ValueError("public package contains unlisted or missing files")
    derived_path = _regular_file(root / PUBLIC_DERIVED_NAME, "derived projection")
    if _sha256_file(derived_path) != digest:
        raise ValueError("public package derived projection was modified")
    try:
        derived = json.loads(derived_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"derived projection is not valid JSON: {exc}") from exc
    if not isinstance(derived, dict) or derived.get("schema") != PUBLIC_DERIVATION_SCHEMA:
        raise ValueError("derived projection has an unexpected schema")
    _validate_public_projection(derived)
    for key, expected in (("analysis_input_changed", True), ("traces_replayable", False),
                          ("review_only", True), ("raw_captured_content_included", False),
                          ("verdict_consistency_revalidated", False)):
        if derived.get(key) is not expected:
            raise ValueError(f"derived projection flag {key} must be {expected!r}")
    if derived.get("derivation_mapping_sha256") != derivation.get("mapping_sha256"):
        raise ValueError("derived projection mapping hash does not match the manifest")
    if derived.get("source_private_package_manifest_sha256") != source.get("manifest_sha256"):
        raise ValueError("derived projection source manifest hash does not match")
    return manifest, {PUBLIC_DERIVED_NAME: digest}


def validate_public(public_root: Path, *, source_package: Path | None = None,
                    mapping: Path | None = None) -> dict:
    """Rehash a review-only projection and optionally re-prove its derivation.

    Integrity-only validation cannot establish admission: the result explicitly
    reports ``source_verified=false`` and ``admission=false``. Supplying both the
    private source package and its mapping reruns the deterministic derivation
    byte-for-byte; it still never claims independent replay or origin proof.
    """
    root = Path(public_root)
    manifest, _members = _load_public_package(root)
    derived_text = (root / PUBLIC_DERIVED_NAME).read_text(encoding="utf-8")
    source_verified = False
    mapping_verified = False
    rederivation_verified = False
    source_manifest_sha = None
    source_database_sha = None
    payload = None
    if source_package is not None:
        source_root = Path(source_package)
        source_manifest_path = source_root / MANIFEST_NAME
        source_manifest_sha = _sha256_file(source_manifest_path)
        source_manifest, _ = _load_package(source_root)
        source_database_sha = _sha256_file(source_root / DATABASE_NAME)
        source = manifest["source_private_package"]
        if source.get("manifest_sha256") != source_manifest_sha:
            raise ValueError("source private package manifest differs from the derivation")
        if source.get("database_sha256") != source_database_sha:
            raise ValueError("source private package database differs from the derivation")
        if source_manifest.get("binding", {}).get("build_sha256") != \
                manifest.get("source_private_package", {}).get("build_sha256"):
            raise ValueError("source private package build hash differs from the derivation")
        source_verified = True
    if mapping is not None:
        mapping = Path(mapping)
        if mapping.is_symlink() or not mapping.is_file():
            raise ValueError("derivation mapping is missing or a symlink")
        if mapping.stat().st_mode & 0o077:
            raise ValueError("derivation mapping must be private (0600)")
        if _sha256_file(mapping) != manifest["derivation"].get("mapping_sha256"):
            raise ValueError("derivation mapping hash does not match the public manifest")
        try:
            payload = json.loads(mapping.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"derivation mapping is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema") != DERIVATION_MAPPING_SCHEMA:
            raise ValueError("derivation mapping has an unexpected schema")
        if payload.get("distribution") != "private-restricted":
            raise ValueError("derivation mapping is not private-restricted")
        secret = payload.get("secret_hex")
        if not isinstance(secret, str) or len(secret) != 64:
            raise ValueError("derivation mapping has no secret")
        try:
            bytes.fromhex(secret)
        except ValueError as exc:
            raise ValueError("derivation mapping secret is malformed") from exc
        mapping_verified = True
    if source_verified and mapping_verified:
        assert payload is not None and source_manifest_sha and source_database_sha
        deriver = _Deriver(bytes.fromhex(payload["secret_hex"]))
        source_db = Path(source_package) / DATABASE_NAME
        with closing(sqlite3.connect(
            source_db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
        )) as conn:
            expected = _public_projection(conn, deriver)
        expected.update({
            "source_private_package_manifest_sha256": source_manifest_sha,
            "source_private_database_sha256": source_database_sha,
            "derivation_mapping_sha256": manifest["derivation"]["mapping_sha256"],
        })
        _validate_public_projection(expected)
        expected_text = json.dumps(expected, indent=2, sort_keys=True) + "\n"
        if expected_text != derived_text:
            raise ValueError(
                "public projection is not reproducible from the private source and mapping")
        if payload.get("tokens") != deriver.tokens:
            raise ValueError("derivation mapping tokens do not match the rederivation")
        if _sha256_file(source_db) != source_database_sha:
            raise ValueError("source private package database changed during rederivation")
        rederivation_verified = True
    result = json.loads(json.dumps(manifest))
    result["validation"] = {
        "integrity_verified": True,
        "source_verified": source_verified,
        "mapping_verified": mapping_verified,
        "rederivation_verified": rederivation_verified,
        "admission": rederivation_verified,
        "independent_origin_proof": False,
        "traces_replayable": False,
        "review_only": True,
    }
    return result


def _manifest_scenario(manifest: dict) -> str:
    binding = manifest.get("binding")
    if not isinstance(binding, dict):
        raise ValueError("package manifest has no binding")
    scenario_id = manifest.get("scenario_id")
    if not isinstance(scenario_id, str) or not scenario_id:
        candidate = binding.get("scenario_id")
        scenario_id = candidate if isinstance(candidate, str) else None
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ValueError("package manifest has no scenario id")
    return scenario_id


def audit_cohort(packages_root: Path, replay_binary: Path, release: Path, *,
                 expected_scenarios: Sequence[str] | None = None,
                 run_records: Path | None = None) -> dict:
    """Audit a directory of private packages as one release cohort.

    Each immediate child must be a self-contained private package. The audit
    loads and rehashes it, proves one scenario binding against the release,
    pins the current replay build, and reruns strict replay. With
    ``run_records`` it additionally reruns the full run-binding validation for
    ``<run_records>/<scenario>.json`` (or ``<scenario>/run-record.json``).

    The summary distinguishes missing scenarios, duplicate scenario bindings,
    unexpected scenarios, and invalid packages. It never fills a missing
    package with an older recording and never treats a partial cohort as the
    93-scenario release.
    """
    root = Path(packages_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("cohort root must be an existing regular directory")
    replay_binary = _regular_file(Path(replay_binary), "replay binary")
    build_sha = _sha256_file(replay_binary)
    release_root = Path(release)
    manifest_path = release_root / "release.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        try:
            release_manifest = validate_release(release_root)
        except (OSError, ValueError) as exc:
            raise ValueError(f"dataset release is not canonical: {exc}") from exc
    else:
        scenarios = release_root / "scenarios"
        if scenarios.is_symlink() or not scenarios.is_dir():
            raise ValueError("dataset scenarios directory is missing or a symlink")
        release_manifest = {
            "release_id": None, "source_status": "unbound",
            "case_bindings": {path.stem: _bind_release(release_root, path.stem)
                              for path in sorted(scenarios.glob("*.json"))},
        }
    bindings = release_manifest.get("case_bindings")
    if not isinstance(bindings, dict) or not bindings:
        raise ValueError("release manifest has no case bindings")
    if expected_scenarios is not None:
        expected = sorted(set(expected_scenarios))
        unknown = [sid for sid in expected if sid not in bindings]
        if unknown:
            raise ValueError(
                f"expected scenario(s) are absent from the release: {unknown}")
    else:
        expected = sorted(bindings)

    entries: list[dict] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_dir():
            entries.append({
                "package": str(path), "scenario_id": None, "run_id": None,
                "status": "invalid", "expected": False,
                "reason": "cohort member is not a regular directory",
            })
            continue
        entry = {
            "package": str(path), "scenario_id": None, "run_id": None,
            "status": "invalid", "expected": False, "reason": None,
        }
        try:
            manifest, members = _load_package(path)
            scenario_id = _manifest_scenario(manifest)
            entry["scenario_id"] = scenario_id
            entry["expected"] = scenario_id in bindings
            binding = manifest["binding"]
            if binding.get("build_sha256") != build_sha:
                raise ValueError(
                    "package was validated by a different replay build than the "
                    "current binary")
            expected_binding = _bind_release(Path(release), scenario_id)
            for key, value in expected_binding.items():
                if binding.get(key) != value:
                    raise ValueError(f"package release binding changed: {key}")
            if run_records is not None:
                candidates = (
                    Path(run_records) / f"{scenario_id}.json",
                    Path(run_records) / scenario_id / "run-record.json",
                )
                record_path = next(
                    (candidate for candidate in candidates
                     if candidate.is_file() and not candidate.is_symlink()), None)
                if record_path is None:
                    raise ValueError("independent run record is missing")
                validate(path, replay_binary, Path(release), record_path)
                run_id = _sqlite_binding(path / DATABASE_NAME, scenario_id)
            else:
                run_id = _sqlite_binding(path / DATABASE_NAME, scenario_id)
                _run_replay(
                    replay_binary, path / DATABASE_NAME, members[DATABASE_NAME],
                    [run_id])
            entry.update({
                "run_id": run_id,
                "status": "valid",
                "database_sha256": members[DATABASE_NAME],
                "build_sha256": binding.get("build_sha256"),
            })
        except (OSError, ValueError, sqlite3.Error,
                subprocess.SubprocessError) as exc:
            entry["status"] = "invalid"
            entry["reason"] = str(exc)
        entries.append(entry)

    valid = [entry for entry in entries if entry["status"] == "valid"]
    invalid = [entry for entry in entries if entry["status"] != "valid"]
    by_scenario: dict[str, list[str]] = {}
    for entry in valid:
        by_scenario.setdefault(entry["scenario_id"], []).append(entry["package"])
    duplicates = {
        scenario_id: sorted(paths)
        for scenario_id, paths in sorted(by_scenario.items()) if len(paths) > 1
    }
    uniquely_covered = {
        scenario_id for scenario_id, paths in by_scenario.items()
        if len(paths) == 1
    }
    missing = sorted(set(expected) - uniquely_covered)
    unexpected = sorted(uniquely_covered - set(expected))
    complete = (not missing and not duplicates and not unexpected and not invalid)
    closure = {
        "strict_replay_inputs": [DATABASE_NAME],
        "pinned_replay_build_sha256": build_sha,
        "external_revalidation_inputs": (
            ["independent run record", "dataset scenario and ground truth"]
            if run_records is not None else ["dataset scenario and ground truth"])
            if release_manifest.get("source_status") == "unbound" else (
                ["independent run record", "release manifest"]
                if run_records is not None else ["release manifest"]),
        "package_member_names_are_flat": True,
        "valid_packages_replayed": len(valid),
        "portable_strict_replay": bool(valid) and not invalid,
        "note": (
            "strict replay needs only the packaged database plus the pinned "
            "replay build; the release and run record are required only to "
            "re-prove the external binding"
        ),
    }
    return {
        "schema": COHORT_SCHEMA,
        "validation_mode": (
            "full-run-binding" if run_records is not None
            else "structural-replay-only"),
        "release": {
            "path": str(release),
            "release_id": release_manifest.get("release_id"),
            "source_status": release_manifest.get("source_status", "release-bound"),
            "scenario_count": len(bindings),
        },
        "replay_binary": {"path": str(replay_binary), "sha256": build_sha},
        "expected": {"count": len(expected), "scenarios": expected},
        "packages": entries,
        "summary": {
            "packages_total": len(entries),
            "valid": len(valid),
            "invalid": len(invalid),
            "covered": sorted(by_scenario),
            "missing": missing,
            "duplicates": duplicates,
            "unexpected": unexpected,
            "cohort_complete": complete,
            "unique_scenario_binding": not duplicates,
        },
        "dependency_closure": closure,
        "status": "complete" if complete else "incomplete",
    }


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
    derive_cmd = commands.add_parser(
        "derive-public",
        help="write a review-only pseudonymous projection; the private package is read-only",
    )
    derive_cmd.add_argument("--package", type=Path, required=True)
    derive_cmd.add_argument("--out", type=Path, required=True)
    derive_cmd.add_argument(
        "--mapping", type=Path, required=True,
        help="private 0600 token mapping outside the public package",
    )
    validate_public_cmd = commands.add_parser(
        "validate-public", help="rehash a review-only projection",
    )
    validate_public_cmd.add_argument("--package", type=Path, required=True)
    validate_public_cmd.add_argument("--source-package", type=Path)
    validate_public_cmd.add_argument("--mapping", type=Path)
    audit_cmd = commands.add_parser(
        "audit-cohort",
        help="audit a directory of private packages against the release cohort",
    )
    audit_cmd.add_argument("--packages", type=Path, required=True)
    audit_cmd.add_argument("--replay-binary", type=Path, required=True)
    audit_cmd.add_argument("--release", type=Path, default=Path(DEFAULT_DATASET_DIR))
    audit_cmd.add_argument("--expected-scenario", action="append", dest="expected")
    audit_cmd.add_argument(
        "--run-records", type=Path,
        help="directory of independent run records for full binding revalidation",
    )
    audit_cmd.add_argument("--out", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "package":
            package(args.database, args.replay_binary, args.out,
                    args.scenario_id, args.release, args.run_record)
        elif args.command == "validate":
            validate(args.package, args.replay_binary, args.release, args.run_record)
        elif args.command == "derive-public":
            derive_public(args.package, args.out, args.mapping)
        elif args.command == "audit-cohort":
            report = audit_cohort(
                args.packages, args.replay_binary, args.release,
                expected_scenarios=args.expected, run_records=args.run_records)
            text = json.dumps(report, indent=2, sort_keys=True) + "\n"
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(text, encoding="utf-8")
            summary = {
                "schema": report["schema"],
                "status": report["status"],
                "validation_mode": report["validation_mode"],
                "release_id": report["release"]["release_id"],
                "expected": report["expected"]["count"],
                "valid": report["summary"]["valid"],
                "missing": report["summary"]["missing"],
                "duplicates": report["summary"]["duplicates"],
                "invalid": report["summary"]["invalid"],
                "out": str(args.out) if args.out else None,
            }
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0 if report["status"] == "complete" else 1
        else:
            result = validate_public(args.package, source_package=args.source_package,
                                     mapping=args.mapping)
            print(json.dumps(result["validation"], indent=2, sort_keys=True))
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f"trace packager: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
