#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Adopt a deterministic sample of archived collector databases and report.

This is the falsifiable form of the v2 migration claim: real databases produced
before the version spine existed are copied, opened by the collector's own
opener, and checked for

  - positive adoption (user_version = 2, application_id = MTL1),
  - identity beliefs equal to the v1 identity rows (no invented beliefs),
  - the three compatibility views present,
  - a column-set fingerprint of every table before and after.

Usage: python3 scripts/schema/sweep_legacy_dbs.py [--count 20] [--out PATH]
"""

import argparse
import hashlib
import json
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = ROOT / "env" / "runs"
EXAMPLE = ROOT / "collector" / "build" / "debug" / "examples" / "schema_adopt"
APPLICATION_ID = 0x4D544C31
LATEST = 2


def find_databases() -> list[pathlib.Path]:
    return sorted(RUNS.glob("*/collector/collector.responsibility.db"))


def sample(paths: list[pathlib.Path], count: int) -> list[pathlib.Path]:
    if len(paths) <= count:
        return paths
    step = len(paths) / count
    return [paths[int(i * step)] for i in range(count)]


def column_fingerprint(structure: dict) -> str:
    payload = json.dumps(structure, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def v1_identity_rows(path: pathlib.Path) -> int | None:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='identity_process_map'"
        ).fetchone()
        if not row or row[0] != "table":
            return None
        return conn.execute("SELECT COUNT(*) FROM identity_process_map").fetchone()[0]
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=ROOT / "docs" / "evaluation" / "schema-adoption-sweep.json",
    )
    args = parser.parse_args()

    if not EXAMPLE.is_file():
        print(
            f"missing {EXAMPLE}; run: cd collector && cargo build --example schema_adopt",
            file=sys.stderr,
        )
        return 2

    databases = find_databases()
    if not databases:
        print("no archived databases found under env/runs", file=sys.stderr)
        return 2
    picked = sample(databases, args.count)

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="mtl-sweep-"))
    copies = []
    expected_identity = {}
    for index, source in enumerate(picked):
        copy = workdir / f"{index:02d}-{source.parents[1].name}.db"
        shutil.copy2(source, copy)
        wal = source.with_name(source.name + "-wal")
        if wal.is_file():
            shutil.copy2(wal, copy.with_name(copy.name + "-wal"))
        copies.append(copy)
        expected_identity[str(copy)] = v1_identity_rows(copy)

    result = subprocess.run(
        [str(EXAMPLE), *map(str, copies)],
        capture_output=True,
        text=True,
        check=True,
    )
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

    failures = []
    reports = []
    for row in rows:
        copy = row["path"]
        expected = expected_identity.get(copy)
        checks = {
            "opened": row.get("ok") is True,
            "user_version": row.get("user_version") == LATEST,
            "application_id": row.get("application_id") == APPLICATION_ID,
            "views": set(row.get("after", {}).get("views", {}))
            == {"identity_process_map", "identity_process_map_by_run", "identity_process_belief_windows"}
            if row.get("ok")
            else False,
            "identity_preserved": (
                expected is None or row.get("beliefs") == expected
            )
            if row.get("ok")
            else False,
        }
        fingerprint_before = (
            column_fingerprint(row["before"]) if row.get("before") else None
        )
        fingerprint_after = (
            column_fingerprint(row["after"]) if row.get("after") else None
        )
        reports.append(
            {
                "database": pathlib.Path(copy).name,
                "source": str(picked[int(pathlib.Path(copy).name.split("-")[0])].relative_to(ROOT)),
                "ok": all(checks.values()),
                "checks": checks,
                "identity_rows_before": expected,
                "identity_beliefs_after": row.get("beliefs"),
                "fingerprint_before": fingerprint_before,
                "fingerprint_after": fingerprint_after,
                "error": row.get("error"),
            }
        )
        if not all(checks.values()):
            failures.append(reports[-1])

    # Independent read-only verification of the post-migration files.
    for copy in copies:
        conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            conn.execute("SELECT * FROM identity_process_map LIMIT 1").fetchall()
            conn.execute("SELECT * FROM identity_process_belief_windows LIMIT 1").fetchall()
        finally:
            conn.close()

    report = {
        "generated_by": "scripts/schema/sweep_legacy_dbs.py",
        "sample_size": len(picked),
        "databases_available": len(databases),
        "latest_schema_version": LATEST,
        "application_id": hex(APPLICATION_ID),
        "all_adopted": not failures,
        "failures": failures,
        "results": reports,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"sweep: {len(picked)}/{len(databases)} databases, "
        f"{len(failures)} failures -> {args.out.relative_to(ROOT)}"
    )
    shutil.rmtree(workdir, ignore_errors=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
