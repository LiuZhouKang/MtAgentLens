#!/usr/bin/env python3
"""Validate the billing schema v1 -> v2 migration on a scratch SQLite database.

Usage:
    python3 tools/check_migration.py [/tmp/migration.sql]

The validator seeds an in-memory SQLite database with the v1 schema, the v1
analytics views and data/sample_rows.sql, applies the migration script, then
checks the resulting schema, data and view semantics. It never opens a network
connection and never writes to disk.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MIGRATION = Path("/tmp/migration.sql")

EXPECTED_STATES = {
    "pending": "Pending payment",
    "paid": "Paid",
    "fulfilled": "Fulfilled",
    "closed": "Closed",
    "cancelled": "Cancelled",
}

# Seed order id -> state_code derived from the legacy status column in
# data/sample_rows.sql and the mapping in docs.md.
EXPECTED_ORDER_STATE = {
    1: "pending",
    2: "paid",
    3: "fulfilled",
    4: "closed",
    5: "cancelled",
    6: "paid",
    7: "paid",
    8: "fulfilled",
    9: "pending",
}

ORDER_TOTAL_COLUMNS = ["order_id", "customer_id", "state_code", "total_cents"]
ACTIVE_CUSTOMER_COLUMNS = ["customer_id", "email", "active_orders"]
ROLLBACK_RE = re.compile(r"^\s*--\s*ROLLBACK\b", re.IGNORECASE | re.MULTILINE)


class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.total = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.total += 1
        if ok:
            self.passed += 1
            print(f"[PASS] {name}")
        else:
            suffix = f" ({detail})" if detail else ""
            print(f"[FAIL] {name}{suffix}")
        return ok

    def result(self) -> int:
        print(f"RESULT: {self.passed}/{self.total} checks passed")
        return 0 if self.passed == self.total else 1


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def column_names(con: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA table_info('{table}')")]


def table_exists(con: sqlite3.Connection, name: str, kind: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?", (kind, name)
    ).fetchone()
    return row is not None


def seed_database(con: sqlite3.Connection, include_views: bool) -> None:
    con.executescript(read(ROOT / "schema" / "legacy_schema.sql"))
    if include_views:
        con.executescript(read(ROOT / "schema" / "analytics_views.sql"))
    con.executescript(read(ROOT / "data" / "sample_rows.sql"))


def main(argv: list[str]) -> int:
    migration = Path(argv[1]) if len(argv) > 1 else DEFAULT_MIGRATION
    checker = Checker()
    print(f"billing migration validator - {migration}")

    if not checker.check("migration file exists and is non-empty",
                         migration.is_file() and migration.stat().st_size > 0):
        return checker.result()

    script = read(migration)

    # Structural checks that do not need execution.
    checker.check(
        "transaction opens with BEGIN and closes with COMMIT",
        re.search(r"\bBEGIN\b", script, re.IGNORECASE) is not None
        and re.search(r"\bCOMMIT\b", script, re.IGNORECASE) is not None,
    )
    checker.check(
        "rollback documentation section is present",
        ROLLBACK_RE.search(script) is not None,
        "no line beginning with -- ROLLBACK",
    )

    # Build the scratch database and apply the migration.
    con = sqlite3.connect(":memory:")
    try:
        seed_database(con, include_views=True)
    except sqlite3.Error as error:
        checker.check("v1 fixture seeds cleanly", False, str(error))
        return checker.result()

    try:
        con.executescript(script)
        con.commit()
        executed = True
        failure = ""
    except sqlite3.Error as error:
        executed = False
        failure = str(error)

    if not checker.check("migration executes on the v1 database", executed, failure):
        return checker.result()

    # Migration bookkeeping.
    try:
        versions = [row[0] for row in con.execute(
            "SELECT version FROM schema_migrations")]
        checker.check("schema_migrations records version 2", 2 in versions,
                      f"versions={versions}")
    except sqlite3.Error as error:
        checker.check("schema_migrations records version 2", False, str(error))

    # Customer locale column.
    try:
        info = {row[1]: row for row in con.execute("PRAGMA table_info('customers')")}
        row = info.get("locale")
        not_null = bool(row is not None and row[3] == 1)
        default = (row[4] or "").strip("'\"") if row is not None else ""
        checker.check(
            "customers.locale is NOT NULL DEFAULT 'en-US'",
            row is not None and not_null and default == "en-US",
            f"notnull={not_null} default={default!r}",
        )
    except sqlite3.Error as error:
        checker.check("customers.locale is NOT NULL DEFAULT 'en-US'", False, str(error))

    # Order state reference table.
    try:
        rows = con.execute("SELECT code, label FROM order_states").fetchall()
        actual_states = dict(rows)
        checker.check(
            "order_states holds the five required code/label pairs",
            actual_states == EXPECTED_STATES,
            f"actual={sorted(actual_states.items())}",
        )
    except sqlite3.Error as error:
        checker.check("order_states holds the five required code/label pairs",
                      False, str(error))

    # orders column shape.
    try:
        orders_columns = column_names(con, "orders")
        checker.check("orders.state_code column exists", "state_code" in orders_columns,
                      f"columns={orders_columns}")
        checker.check("orders.status column was removed", "status" not in orders_columns,
                      f"columns={orders_columns}")
    except sqlite3.Error as error:
        checker.check("orders column shape", False, str(error))

    # Backfill semantics.
    try:
        actual_state = dict(con.execute("SELECT id, state_code FROM orders").fetchall())
        checker.check("every order is backfilled from the legacy status mapping",
                      actual_state == EXPECTED_ORDER_STATE,
                      f"actual={sorted(actual_state.items())}")
    except sqlite3.Error as error:
        checker.check("every order is backfilled from the legacy status mapping",
                      False, str(error))

    # Data preservation against a reference database seeded from the same rows.
    try:
        reference = sqlite3.connect(":memory:")
        seed_database(reference, include_views=False)
        preserved = True
        detail = ""
        snapshots = {
            "customers": "SELECT id, email, full_name, created_at FROM customers ORDER BY id",
            "order_items": "SELECT id, order_id, sku, qty, unit_price_cents "
                           "FROM order_items ORDER BY id",
        }
        for table, query in snapshots.items():
            migrated_rows = [tuple(row) for row in con.execute(query)]
            reference_rows = [tuple(row) for row in reference.execute(query)]
            if migrated_rows != reference_rows:
                preserved = False
                detail = f"{table} rows differ"
                break
        if preserved:
            migrated_orders = [tuple(row) for row in con.execute(
                "SELECT id, customer_id, total_cents, created_at FROM orders ORDER BY id")]
            reference_orders = [tuple(row) for row in reference.execute(
                "SELECT id, customer_id, total_cents, created_at FROM orders ORDER BY id")]
            if migrated_orders != reference_orders:
                preserved = False
                detail = "orders rows differ"
        reference.close()
        checker.check("seeded customers, orders and order_items are preserved",
                      preserved, detail)
    except sqlite3.Error as error:
        checker.check("seeded customers, orders and order_items are preserved",
                      False, str(error))

    # Index.
    try:
        names = [row[1] for row in con.execute("PRAGMA index_list('orders')")]
        index_columns = [row[2] for row in con.execute(
            "PRAGMA index_info('idx_orders_customer_created')")]
        checker.check(
            "index idx_orders_customer_created covers (customer_id, created_at)",
            "idx_orders_customer_created" in names
            and index_columns == ["customer_id", "created_at"],
            f"indices={names} columns={index_columns}",
        )
    except sqlite3.Error as error:
        checker.check("index idx_orders_customer_created covers (customer_id, created_at)",
                      False, str(error))

    # v_order_totals.
    try:
        exists = table_exists(con, "v_order_totals", "view")
        columns = column_names(con, "v_order_totals") if exists else []
        checker.check("v_order_totals exists with the required columns",
                      exists and columns == ORDER_TOTAL_COLUMNS,
                      f"columns={columns}")
        if exists and columns == ORDER_TOTAL_COLUMNS:
            expected = [tuple(row) for row in con.execute(
                """
                SELECT o.id, o.customer_id, o.state_code,
                       COALESCE(SUM(i.qty * i.unit_price_cents), 0)
                FROM orders o
                LEFT JOIN order_items i ON i.order_id = o.id
                GROUP BY o.id, o.customer_id, o.state_code
                ORDER BY o.id
                """
            )]
            actual = [tuple(row) for row in con.execute(
                "SELECT order_id, customer_id, state_code, total_cents "
                "FROM v_order_totals ORDER BY order_id")]
            checker.check("v_order_totals totals match the line items",
                          actual == expected, f"actual={actual}")
    except sqlite3.Error as error:
        checker.check("v_order_totals is correct", False, str(error))

    # v_active_customers.
    try:
        exists = table_exists(con, "v_active_customers", "view")
        columns = column_names(con, "v_active_customers") if exists else []
        checker.check("v_active_customers exists with the required columns",
                      exists and columns == ACTIVE_CUSTOMER_COLUMNS,
                      f"columns={columns}")
        if exists and columns == ACTIVE_CUSTOMER_COLUMNS:
            expected = [tuple(row) for row in con.execute(
                """
                SELECT c.id, c.email, COUNT(o.id)
                FROM customers c
                JOIN orders o ON o.customer_id = c.id
                WHERE o.state_code IN ('paid', 'fulfilled')
                GROUP BY c.id, c.email
                ORDER BY c.id
                """
            )]
            actual = [tuple(row) for row in con.execute(
                "SELECT customer_id, email, active_orders "
                "FROM v_active_customers ORDER BY customer_id")]
            checker.check("v_active_customers matches paid/fulfilled orders",
                          actual == expected, f"actual={actual}")
    except sqlite3.Error as error:
        checker.check("v_active_customers is correct", False, str(error))

    con.close()
    return checker.result()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
