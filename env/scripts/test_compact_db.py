#!/usr/bin/env python3
"""Contract tests for compact_db.py.

Run: python3 -m unittest env/scripts/test_compact_db.py -v

Why this exists: `compact_db.py` used to enumerate only `type='table'`, so a v2
database would have lost `identity_process_map` (a view) and
`identity_process_beliefs` (not in the essential set) while still exiting 0.
A compacted database silently missing the identity contract is exactly the
class of defect the single-schema work is meant to remove.
"""

import pathlib
import sqlite3
import tempfile
import unittest

import compact_db

ROOT = pathlib.Path(__file__).resolve().parents[2]
V1_FIXTURE = ROOT / "collector/tests/fixtures/schema/live-v1.db"


def compact(src: pathlib.Path) -> pathlib.Path:
    out_dir = tempfile.mkdtemp()
    dst = pathlib.Path(out_dir) / "compacted.db"
    compact_db.compact_db(str(src), str(dst))
    return dst


class CompactV1Test(unittest.TestCase):
    def test_v1_identity_table_survives_with_the_header(self):
        dst = compact(V1_FIXTURE)
        conn = sqlite3.connect(dst)
        kind = conn.execute(
            "SELECT type FROM sqlite_master WHERE name='identity_process_map'"
        ).fetchone()
        self.assertEqual(kind, ("table",))
        rows = conn.execute(
            "SELECT agent_id, pid FROM identity_process_map ORDER BY pid"
        ).fetchall()
        self.assertEqual(
            rows,
            [("agent-authoritative", 100), ("agent-authoritative", 200)],
        )
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone(), (1,))
        self.assertEqual(conn.execute("PRAGMA application_id").fetchone(), (0,))
        conn.close()


class CompactV2Test(unittest.TestCase):
    def _source(self) -> pathlib.Path:
        src = pathlib.Path(tempfile.mkdtemp()) / "v2.db"
        conn = sqlite3.connect(src)
        conn.executescript(
            """
            CREATE TABLE runs (run_id TEXT PRIMARY KEY);
            CREATE TABLE identity_process_beliefs (
                belief_id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                pid INTEGER NOT NULL,
                start_time_ns INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                belief_level TEXT NOT NULL,
                observed_at_ns INTEGER NOT NULL,
                source TEXT NOT NULL
            );
            CREATE VIEW identity_process_map AS
                SELECT agent_id, pid, start_time_ns, run_id
                FROM identity_process_beliefs;
            INSERT INTO runs VALUES ('r1');
            INSERT INTO identity_process_beliefs
                (agent_id, pid, start_time_ns, run_id, belief_level, observed_at_ns, source)
            VALUES ('agent-1', 10, 100, 'r1', 'Authoritative', 5, 'test');
            PRAGMA user_version = 2;
            PRAGMA application_id = 0x4D544C31;
            """
        )
        conn.commit()
        conn.close()
        return src

    def test_beliefs_and_views_survive(self):
        dst = compact(self._source())
        conn = sqlite3.connect(dst)
        self.assertEqual(
            conn.execute(
                "SELECT type FROM sqlite_master WHERE name='identity_process_map'"
            ).fetchone(),
            ("view",),
        )
        self.assertEqual(
            conn.execute("SELECT count(*) FROM identity_process_beliefs").fetchone(),
            (1,),
        )
        self.assertEqual(
            conn.execute("SELECT agent_id, pid FROM identity_process_map").fetchall(),
            [("agent-1", 10)],
        )
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone(), (2,))
        self.assertEqual(
            conn.execute("PRAGMA application_id").fetchone(), (0x4D544C31,)
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()
