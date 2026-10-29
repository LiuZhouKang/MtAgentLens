#!/usr/bin/env python3
"""Validate the billing migration chain without touching a database server.

The validator applies the chain to throwaway SQLite databases under /tmp and
checks ordering, required sections, the expected schema, the V004 backfill,
declared constraints, and the full down-migration path. Staged migrations under
migrations/pending/ are parsed but never applied.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

FILENAME_RE = re.compile(r"^V(?P<version>\d{3})__(?P<slug>[a-z0-9_]+)\.sql$")
SECTION_RE = re.compile(r"^--\s*\+migrate\s+(Up|Down)\s*$", re.IGNORECASE | re.MULTILINE)
HEADER_RE = re.compile(r"^--\s*(migration|description|stage):\s*(.+)$", re.IGNORECASE | re.MULTILINE)

SEED_SQL = """
INSERT INTO accounts (id, email, display_name, created_at, legacy_balance_cents)
VALUES
    (1, 'ada@example.test', 'Ada Lovelace', '2026-01-01T00:00:00Z', 125000),
    (2, 'grace@example.test', 'Grace Hopper', '2026-01-02T00:00:00Z', 0);
"""


class ValidationError(Exception):
    """Raised when the migration chain or a derived database is invalid."""


def parse_migration(path: Path, expected_version: int | None = None) -> dict[str, Any]:
    """Parse one migration file into header metadata and Up/Down SQL."""
    match = FILENAME_RE.match(path.name)
    if not match:
        raise ValidationError(f"{path.name}: expected VNNN__lower_snake.sql")
    version = int(match.group("version"))
    if expected_version is not None and version != expected_version:
        raise ValidationError(
            f"{path.name}: expected V{expected_version:03d}, found V{version:03d}"
        )

    text = path.read_text(encoding="utf-8")
    headers = {key.lower(): value.strip() for key, value in HEADER_RE.findall(text)}
    for key in ("migration", "description", "stage"):
        if not headers.get(key):
            raise ValidationError(f"{path.name}: missing '-- {key}:' header")
    if headers["migration"].upper() != f"V{version:03d}":
        raise ValidationError(
            f"{path.name}: migration header is {headers['migration']!r}, "
            f"expected V{version:03d}"
        )

    chunks = SECTION_RE.split(text)
    if len(chunks) < 5:
        raise ValidationError(f"{path.name}: expected both Up and Down sections")

    sections: dict[str, str] = {}
    for index in range(1, len(chunks), 2):
        name = chunks[index].lower()
        body = chunks[index + 1].strip()
        if not body:
            raise ValidationError(f"{path.name}: empty {name} section")
        if name in sections:
            raise ValidationError(f"{path.name}: duplicate {name} section")
        sections[name] = body

    if set(sections) != {"up", "down"}:
        raise ValidationError(
            f"{path.name}: expected exactly Up and Down, found {sorted(sections)}"
        )

    return {
        "version": version,
        "path": path,
        "headers": headers,
        "up": sections["up"],
        "down": sections["down"],
    }


def discover_migrations(root: Path) -> list[dict[str, Any]]:
    """Return the contiguous applied chain from <root>/migrations."""
    migration_dir = root / "migrations"
    if not migration_dir.is_dir():
        raise ValidationError(f"{migration_dir}: migration directory not found")

    paths = sorted(
        path for path in migration_dir.iterdir()
        if path.is_file() and path.suffix == ".sql"
    )
    if not paths:
        raise ValidationError(f"{migration_dir}: no migration files found")

    migrations = [
        parse_migration(path, expected_version=index)
        for index, path in enumerate(paths, start=1)
    ]
    if not migrations:
        raise ValidationError(f"{migration_dir}: no migration files found")
    return migrations


def discover_pending(root: Path) -> list[dict[str, Any]]:
    """Parse staged migrations without applying them."""
    pending_dir = root / "migrations" / "pending"
    if not pending_dir.is_dir():
        return []
    return [
        parse_migration(path)
        for path in sorted(pending_dir.glob("*.sql"))
        if path.is_file()
    ]


def apply_sql(conn: sqlite3.Connection, sql: str, label: str) -> None:
    try:
        conn.executescript(sql)
    except sqlite3.Error as exc:
        raise ValidationError(f"{label}: {exc}") from exc


def table_fingerprint(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return table columns, foreign keys and index definitions, name-agnostic."""
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]

    fingerprint: dict[str, Any] = {}
    for table in tables:
        columns = tuple(
            (row[1], (row[2] or "").upper(), int(row[3]), row[4], int(row[5]))
            for row in conn.execute(f'PRAGMA table_info("{table}")')
        )
        foreign_keys = tuple(
            sorted(
                (row[2], row[3], row[4], row[5], row[6])
                for row in conn.execute(f'PRAGMA foreign_key_list("{table}")')
            )
        )

        indexes = []
        for row in conn.execute(f'PRAGMA index_list("{table}")'):
            index_name = row[1]
            index_columns = tuple(
                info[2]
                for info in conn.execute(f'PRAGMA index_info("{index_name}")')
            )
            indexes.append((int(row[2]), index_columns))

        fingerprint[table] = {
            "columns": columns,
            "foreign_keys": foreign_keys,
            "indexes": tuple(sorted(indexes)),
        }

    return fingerprint


def load_expected(root: Path, relative_path: str) -> dict[str, Any]:
    path = root / relative_path
    if not path.is_file():
        raise ValidationError(f"{path}: expected schema file not found")

    conn = sqlite3.connect(":memory:")
    try:
        apply_sql(conn, path.read_text(encoding="utf-8"), relative_path)
        return table_fingerprint(conn)
    finally:
        conn.close()


def describe_difference(actual: dict[str, Any], expected: dict[str, Any]) -> str:
    problems: list[str] = []
    for table in sorted(set(actual) | set(expected)):
        if table not in actual:
            problems.append(f"missing table {table}")
            continue
        if table not in expected:
            problems.append(f"unexpected table {table}")
            continue
        for key in ("columns", "foreign_keys", "indexes"):
            if actual[table][key] != expected[table][key]:
                problems.append(
                    f"{table}.{key}: actual={actual[table][key]!r} "
                    f"expected={expected[table][key]!r}"
                )
    return "; ".join(problems[:8]) or "unknown schema difference"


def validate_up_chain(root: Path, migrations: list[dict[str, Any]], tmp: Path) -> int:
    """Apply the full up chain, verify schema, backfill and constraints."""
    conn = sqlite3.connect(tmp / "up.db")
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        for migration in migrations:
            apply_sql(
                conn,
                migration["up"],
                f"{migration['path'].name} (up)",
            )

            # V004 is the backfill stage: seed rows exist only after V003.
            if migration["version"] == 3:
                apply_sql(conn, SEED_SQL, "seed accounts before V004")

        # The migration runner disables FK enforcement for table rebuilds. Turn
        # it back on for the constraint checks below.
        conn.execute("PRAGMA foreign_keys = ON")

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise ValidationError(f"foreign_key_check reported {violations!r}")

        actual = table_fingerprint(conn)
        expected = load_expected(root, "schema/expected_schema.sql")
        if actual != expected:
            raise ValidationError(
                "schema mismatch: " + describe_difference(actual, expected)
            )

        rows = conn.execute(
            "SELECT id, legacy_balance_cents, balance_cents FROM accounts ORDER BY id"
        ).fetchall()
        if len(rows) != 2:
            raise ValidationError(f"expected two seeded accounts, found {len(rows)}")
        for account_id, legacy_balance, balance in rows:
            if legacy_balance != balance:
                raise ValidationError(
                    f"V004 backfill mismatch for account {account_id}: "
                    f"legacy={legacy_balance} balance={balance}"
                )

        try:
            conn.execute(
                "INSERT INTO transfers "
                "(from_account_id, to_account_id, amount_cents, status, created_at) "
                "VALUES (1, 2, 0, 'pending', '2026-01-03T00:00:00Z')"
            )
            raise ValidationError("transfers.amount_cents CHECK accepted zero")
        except sqlite3.IntegrityError:
            pass

        try:
            conn.execute(
                "INSERT INTO transfers "
                "(from_account_id, to_account_id, amount_cents, status, created_at) "
                "VALUES (999, 2, 100, 'pending', '2026-01-03T00:00:00Z')"
            )
            raise ValidationError("transfers foreign key accepted a missing account")
        except sqlite3.IntegrityError:
            pass

        return len(rows)
    finally:
        conn.close()


def validate_down_chain(
    root: Path, migrations: list[dict[str, Any]], tmp: Path
) -> int:
    """Apply every up migration, then every down migration in reverse."""
    conn = sqlite3.connect(tmp / "rollback.db")
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        for migration in migrations:
            apply_sql(
                conn,
                migration["up"],
                f"{migration['path'].name} (up before rollback)",
            )

        applied_down = 0
        for migration in reversed(migrations):
            apply_sql(
                conn,
                migration["down"],
                f"{migration['path'].name} (down)",
            )
            applied_down += 1

        actual = table_fingerprint(conn)
        expected = load_expected(root, "schema/baseline_schema.sql")
        if actual != expected:
            raise ValidationError(
                "rollback did not restore baseline: "
                + describe_difference(actual, expected)
            )

        leftovers = conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        if leftovers:
            raise ValidationError(f"rollback left objects behind: {leftovers!r}")

        return applied_down
    finally:
        conn.close()


def run(root: Path) -> None:
    migrations = discover_migrations(root)
    pending = discover_pending(root)
    with tempfile.TemporaryDirectory(prefix="migration-validate-") as tmp_name:
        tmp = Path(tmp_name)
        seeded_accounts = validate_up_chain(root, migrations, tmp)
        applied_down = validate_down_chain(root, migrations, tmp)

    last_version = migrations[-1]["version"]
    print(f"[validate] chain: {len(migrations)} migrations (V001..V{last_version:03d})")
    print("[validate] schema: matches schema/expected_schema.sql")
    print(f"[validate] backfill: {seeded_accounts} seeded accounts match legacy balances")
    print("[validate] constraints: amount CHECK and foreign keys reject invalid rows")
    print(f"[validate] rollback: {applied_down} down migrations restored baseline")
    if pending:
        names = ", ".join(f"V{item['version']:03d}" for item in pending)
        print(f"[validate] pending: {names} parsed and excluded from the applied chain")
    print("[validate] OK")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=".",
        help="workspace root containing migrations/ and schema/ (default: .)",
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    try:
        run(root)
    except (ValidationError, sqlite3.Error, OSError) as exc:
        print(f"[validate] FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
