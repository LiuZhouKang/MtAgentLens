#!/usr/bin/env python3
"""Unit tests for the migration validator."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from tools.validate_migrations import (  # noqa: E402
    ValidationError,
    discover_migrations,
    discover_pending,
    parse_migration,
    table_fingerprint,
)


class MigrationChainTests(unittest.TestCase):
    def test_chain_is_contiguous(self):
        migrations = discover_migrations(BASE)
        self.assertEqual([m["version"] for m in migrations], [1, 2, 3, 4])
        self.assertEqual(
            [m["headers"]["stage"] for m in migrations],
            ["baseline", "expand", "expand", "expand-backfill"],
        )

    def test_every_migration_has_a_down_section(self):
        for migration in discover_migrations(BASE):
            self.assertTrue(migration["down"].strip(), migration["path"].name)

    def test_pending_contract_migration_stays_out_of_chain(self):
        names = {m["path"].name for m in discover_migrations(BASE)}
        self.assertNotIn("V005__contract_legacy_balance.sql", names)

    def test_pending_contract_migration_parses(self):
        pending = discover_pending(BASE)
        self.assertEqual([m["headers"]["stage"] for m in pending], ["contract"])
        self.assertEqual([m["version"] for m in pending], [5])


class ParserTests(unittest.TestCase):
    def test_missing_down_section_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "V001__broken.sql"
            path.write_text(
                "-- migration: V001\n"
                "-- description: broken fixture\n"
                "-- stage: expand\n"
                "-- +migrate Up\n"
                "CREATE TABLE t (id INTEGER);\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValidationError):
                parse_migration(path)

    def test_header_and_filename_must_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "V002__wrong_header.sql"
            path.write_text(
                "-- migration: V001\n"
                "-- description: header does not match filename\n"
                "-- stage: expand\n"
                "-- +migrate Up\n"
                "CREATE TABLE t (id INTEGER);\n"
                "-- +migrate Down\n"
                "DROP TABLE t;\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValidationError):
                parse_migration(path, expected_version=2)


class FingerprintTests(unittest.TestCase):
    def test_columns_and_unique_index_are_captured(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(
                "CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"
                "CREATE UNIQUE INDEX idx_t_name ON t(name);"
            )
            fingerprint = table_fingerprint(conn)
        finally:
            conn.close()

        self.assertIn("t", fingerprint)
        self.assertEqual(
            [column[0] for column in fingerprint["t"]["columns"]],
            ["id", "name"],
        )
        self.assertEqual(fingerprint["t"]["indexes"], ((1, ("name",)),))


if __name__ == "__main__":
    unittest.main()
