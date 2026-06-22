#!/usr/bin/env python3
"""Audit frozen responsibility databases for public-release redaction risk."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET = PROJECT_ROOT / "compare" / "Dataset" / "FuxiBench"
DEFAULT_OUTPUT = PROJECT_ROOT / "compare" / "results" / "paper" / "release" / "TRACE_REDACTION_AUDIT.json"

REQUIRED_TABLES = {
    "schema_version",
    "runs",
    "process_events",
    "actions",
    "responsibility_claims",
}

TEXT_DECLARATIONS = {"TEXT", "VARCHAR", "CHAR", "CLOB"}

LOCAL_PATH_PATTERNS = [
    re.compile(r"/home/[A-Za-z0-9._-]+/"),
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/tmp/[A-Za-z0-9._/-]+"),
    re.compile(r"/var/folders/[A-Za-z0-9._/-]+"),
]

SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)[\"'\s:=]+[a-z0-9._~+/=-]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
]

ENDPOINT_PATTERNS = [
    re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?::\d+)?"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
]

ALLOWLISTED_ENDPOINT_HOSTS = {
    "json-schema.org",
    "schema.org",
    "example.com",
    "localhost",
}

MAX_FINDINGS_PER_DB_KIND = 20
MAX_VALUE_PREVIEW = 160


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json", action="store_true", help="print artifact JSON to stdout")
    args = parser.parse_args()

    artifact = audit_trace_redaction(dataset_root=args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.json:
        print(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Generated {_display(args.output)}")
        print(f"status={artifact['status']}")
        print(f"public_release_ready={artifact['summary']['public_release_ready']}")
    return 0


def audit_trace_redaction(*, dataset_root: Path) -> dict[str, Any]:
    dataset_root = dataset_root.resolve()
    db_paths = sorted((dataset_root / "traces").glob("**/responsibility.db"))
    databases: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    for db_path in db_paths:
        db_result, db_findings = _audit_db(db_path, dataset_root)
        databases.append(db_result)
        findings.extend(db_findings)

    summary = _summary(databases, findings)
    status = "passed_machine_scan" if summary["public_release_ready"] else "blocked_for_public_release"
    return {
        "schema": "mtagentprism.trace_redaction_audit.v1",
        "status": status,
        "generated_at": _now_iso(),
        "dataset": {
            "id": dataset_root.name,
            "path": _display(dataset_root),
        },
        "policy": {
            "path": "compare/results/paper/release/TRACE_REDACTION_POLICY.md",
            "release_decision": (
                "raw_responsibility_databases_must_remain_restricted_until_redaction_passes"
                if status == "blocked_for_public_release"
                else "machine_scan_passed_manual_qa_still_recommended"
            ),
        },
        "summary": summary,
        "manual_qa_required": True,
        "manual_qa_scope": _manual_qa_scope(databases, findings),
        "databases": databases,
        "findings": findings,
        "non_claims": [
            "This audit does not sanitize trace databases.",
            "This audit does not certify legal, privacy, or institutional compliance.",
            "This audit does not prove live collector robustness or raw parser robustness.",
        ],
        "provenance": {
            "runner": "compare/scripts/audit_trace_redaction.py",
            "git_commit": _git(["rev-parse", "HEAD"]),
            "git_dirty": bool(_git(["status", "--porcelain"])),
        },
    }


def _audit_db(db_path: Path, dataset_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rel_db = _display(db_path)
    db_findings: list[dict[str, Any]] = []
    integrity = "not_checked"
    schema_ok = False
    table_counts: dict[str, int] = {}
    missing_required_tables: list[str] = []
    text_columns_scanned = 0
    values_scanned = 0

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return (
            {
                "path": rel_db,
                "scenario_id": _scenario_id(db_path, dataset_root),
                "sha256": _sha256(db_path),
                "bytes": db_path.stat().st_size,
                "integrity_check": "open_failed",
                "schema_ok": False,
                "missing_required_tables": sorted(REQUIRED_TABLES),
                "table_counts": {},
                "text_columns_scanned": 0,
                "values_scanned": 0,
                "finding_counts": {"local_path": 0, "secret_like": 0, "endpoint_like": 0},
                "error": str(exc),
            },
            [
                _finding(
                    db_path=db_path,
                    kind="sqlite_open_error",
                    severity="public_release_blocker",
                    table="",
                    column="",
                    value=str(exc),
                    redaction_hint="Repair or exclude unreadable trace database before release.",
                )
            ],
        )

    try:
        integrity = str(con.execute("PRAGMA integrity_check").fetchone()[0])
        tables = _tables(con)
        missing_required_tables = sorted(REQUIRED_TABLES - set(tables))
        schema_ok = integrity == "ok" and not missing_required_tables

        for table in tables:
            if table == "sqlite_sequence":
                continue
            try:
                table_counts[table] = int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            except sqlite3.Error:
                table_counts[table] = -1

        for table in tables:
            if table == "sqlite_sequence":
                continue
            for column in _text_columns(con, table):
                text_columns_scanned += 1
                rows = con.execute(
                    f'SELECT rowid, "{column}" FROM "{table}" '
                    f'WHERE "{column}" IS NOT NULL AND CAST("{column}" AS TEXT) != ""'
                )
                for rowid, value in rows:
                    values_scanned += 1
                    db_findings.extend(_scan_value(db_path, table, column, rowid, str(value)))
    except sqlite3.Error as exc:
        db_findings.append(
            _finding(
                db_path=db_path,
                kind="sqlite_scan_error",
                severity="public_release_blocker",
                table="",
                column="",
                value=str(exc),
                redaction_hint="Repair or exclude database before release.",
            )
        )
    finally:
        con.close()

    if integrity != "ok":
        db_findings.append(
            _finding(
                db_path=db_path,
                kind="sqlite_integrity",
                severity="public_release_blocker",
                table="",
                column="",
                value=integrity,
                redaction_hint="Only release databases that pass PRAGMA integrity_check.",
            )
        )

    for table in missing_required_tables:
        db_findings.append(
            _finding(
                db_path=db_path,
                kind="missing_required_table",
                severity="public_release_blocker",
                table=table,
                column="",
                value=table,
                redaction_hint="Public trace release must preserve required evaluation tables.",
            )
        )

    finding_counts = {
        "local_path": sum(1 for item in db_findings if item["kind"] == "local_path"),
        "secret_like": sum(1 for item in db_findings if item["kind"] == "secret_like"),
        "endpoint_like": sum(1 for item in db_findings if item["kind"] == "endpoint_like"),
    }
    return (
        {
            "path": rel_db,
            "scenario_id": _scenario_id(db_path, dataset_root),
            "sha256": _sha256(db_path),
            "bytes": db_path.stat().st_size,
            "integrity_check": integrity,
            "schema_ok": schema_ok,
            "missing_required_tables": missing_required_tables,
            "table_counts": dict(sorted(table_counts.items())),
            "text_columns_scanned": text_columns_scanned,
            "values_scanned": values_scanned,
            "finding_counts": finding_counts,
        },
        _cap_findings(db_findings),
    )


def _scan_value(db_path: Path, table: str, column: str, rowid: int, value: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    for pattern in LOCAL_PATH_PATTERNS:
        if pattern.search(value):
            findings.append(
                _finding(
                    db_path=db_path,
                    kind="local_path",
                    severity="public_release_blocker",
                    table=table,
                    column=column,
                    rowid=rowid,
                    value=value,
                    redaction_hint=(
                        "Replace raw local path prefixes with ${PROJECT_ROOT}, ${USER}, "
                        "${HOST}, ${TMP}, or ${WORKSPACE} placeholders."
                    ),
                )
            )
            break

    for pattern in SECRET_PATTERNS:
        if pattern.search(value):
            findings.append(
                _finding(
                    db_path=db_path,
                    kind="secret_like",
                    severity="public_release_blocker",
                    table=table,
                    column=column,
                    rowid=rowid,
                    value=value,
                    redaction_hint="Replace secret-like values with typed <REDACTED_SECRET:...> placeholders.",
                )
            )
            break

    if _has_unreviewed_endpoint(value):
        findings.append(
            _finding(
                db_path=db_path,
                kind="endpoint_like",
                severity="manual_review_required",
                table=table,
                column=column,
                rowid=rowid,
                value=value,
                redaction_hint="Replace non-synthetic external endpoints with ${EXTERNAL_HOST_N} placeholders.",
            )
        )

    return findings


def _has_unreviewed_endpoint(value: str) -> bool:
    for match in ENDPOINT_PATTERNS[0].finditer(value):
        host = match.group(1).lower()
        if host not in ALLOWLISTED_ENDPOINT_HOSTS:
            return True
    for match in ENDPOINT_PATTERNS[1].finditer(value):
        octets = [int(part) for part in match.group(0).split(".")]
        if not _private_or_loopback(octets):
            return True
    return False


def _private_or_loopback(octets: list[int]) -> bool:
    if len(octets) != 4 or any(part > 255 for part in octets):
        return False
    return (
        octets[0] == 10
        or octets[0] == 127
        or (octets[0] == 172 and 16 <= octets[1] <= 31)
        or (octets[0] == 192 and octets[1] == 168)
        or (octets[0] == 169 and octets[1] == 254)
        or octets == [0, 0, 0, 0]
    )


def _cap_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = {}
    capped: list[dict[str, Any]] = []
    for finding in findings:
        key = (finding["database"], finding["kind"])
        counts[key] = counts.get(key, 0) + 1
        if counts[key] <= MAX_FINDINGS_PER_DB_KIND:
            capped.append(finding)
    return capped


def _finding(
    *,
    db_path: Path,
    kind: str,
    severity: str,
    table: str,
    column: str,
    value: str,
    redaction_hint: str,
    rowid: int | None = None,
) -> dict[str, Any]:
    finding = {
        "database": _display(db_path),
        "kind": kind,
        "severity": severity,
        "table": table,
        "column": column,
        "value_preview": _preview(value),
        "redaction_hint": redaction_hint,
    }
    if rowid is not None:
        finding["rowid"] = rowid
    return finding


def _summary(databases: list[dict[str, Any]], findings: list[dict[str, Any]]) -> dict[str, Any]:
    blocker_kinds = {"local_path", "secret_like", "sqlite_open_error", "sqlite_scan_error", "sqlite_integrity", "missing_required_table"}
    integrity_failures = sum(1 for db in databases if db.get("integrity_check") != "ok")
    schema_failures = sum(1 for db in databases if not db.get("schema_ok"))
    local_path_findings = sum(1 for item in findings if item["kind"] == "local_path")
    secret_like_findings = sum(1 for item in findings if item["kind"] == "secret_like")
    endpoint_like_findings = sum(1 for item in findings if item["kind"] == "endpoint_like")
    public_blockers = sum(1 for item in findings if item["kind"] in blocker_kinds)
    return {
        "database_count": len(databases),
        "integrity_failures": integrity_failures,
        "schema_failures": schema_failures,
        "local_path_findings": local_path_findings,
        "secret_like_findings": secret_like_findings,
        "endpoint_like_findings": endpoint_like_findings,
        "public_release_blockers": public_blockers,
        "public_release_ready": bool(databases) and integrity_failures == 0 and schema_failures == 0 and public_blockers == 0,
        "finding_count_capped": len(findings),
    }


def _manual_qa_scope(databases: list[dict[str, Any]], findings: list[dict[str, Any]]) -> dict[str, Any]:
    scenarios_with_findings = sorted({Path(item["database"]).parent.name for item in findings})
    categories = sorted({Path(db["path"]).parts[-3] for db in databases if len(Path(db["path"]).parts) >= 3})
    return {
        "reason": "Manual QA is required for trace release even after machine scans.",
        "scenario_families": categories,
        "scenarios_with_findings": scenarios_with_findings,
        "minimum_review": "At least one database per scenario family and every database with a machine finding.",
    }


def _tables(con: sqlite3.Connection) -> list[str]:
    return [
        str(row[0])
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    ]


def _text_columns(con: sqlite3.Connection, table: str) -> list[str]:
    columns = []
    for row in con.execute(f'PRAGMA table_info("{table}")'):
        name = str(row[1])
        declaration = str(row[2] or "").upper()
        if any(kind in declaration for kind in TEXT_DECLARATIONS):
            columns.append(name)
    return columns


def _scenario_id(db_path: Path, dataset_root: Path) -> str:
    marker = db_path.parent / ".scenario_id"
    if marker.exists():
        return marker.read_text(encoding="utf-8").strip()
    try:
        return str(db_path.parent.relative_to(dataset_root / "traces")).replace("/", "::")
    except ValueError:
        return db_path.parent.name


def _preview(value: str) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= MAX_VALUE_PREVIEW:
        return normalized
    return normalized[: MAX_VALUE_PREVIEW - 3] + "..."


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _git(args: list[str]) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
