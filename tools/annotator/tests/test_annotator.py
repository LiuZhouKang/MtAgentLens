#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Unit tests for the annotation workbench (stdlib only)."""

from __future__ import annotations

import json
import http.client
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.annotator import server  # noqa: E402


def make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at_ns INTEGER);
        CREATE TABLE graph_nodes (
            node_id TEXT PRIMARY KEY, node_type TEXT, agent_id TEXT,
            resource_json TEXT, timestamp_ns INTEGER
        );
        CREATE TABLE graph_edges (
            edge_id TEXT PRIMARY KEY, edge_type TEXT, source_node_id TEXT,
            target_node_id TEXT, confidence REAL, phase TEXT
        );
        CREATE TABLE intents (
            intent_id TEXT PRIMARY KEY, pid INTEGER, tool_name TEXT,
            tool_args_json TEXT, intent_class TEXT, parse_state TEXT, timestamp_ns INTEGER
        );
        CREATE TABLE actions (
            action_id TEXT PRIMARY KEY, pid INTEGER, action_type TEXT,
            normalized_args_json TEXT, derivation_mode TEXT, timestamp_ns INTEGER
        );
        CREATE TABLE correlation_evidence (
            evidence_id TEXT PRIMARY KEY, intent_id TEXT, action_id TEXT,
            match_level TEXT, confidence REAL, timestamp_ns INTEGER
        );
        CREATE TABLE responsibility_claims (
            claim_id TEXT PRIMARY KEY, claim_type TEXT, subject_agent_id TEXT,
            boundary_id TEXT, claim_state TEXT, severity TEXT, confidence REAL,
            evidence_refs_json TEXT, phase TEXT
        );
        CREATE TABLE responsibility_alerts (
            alert_id TEXT PRIMARY KEY, timestamp_ns INTEGER, decision TEXT,
            severity TEXT, confidence REAL, title TEXT, summary TEXT,
            primary_agent_id TEXT, engine_section_json TEXT
        );
        CREATE TABLE graph_snapshots (
            snapshot_id TEXT PRIMARY KEY, timestamp_ns INTEGER, metrics_json TEXT
        );
        """
    )
    conn.execute("INSERT INTO runs VALUES ('run-1', 1)")
    conn.execute(
        "INSERT INTO graph_nodes VALUES ('n1','Agent','agent-a','{}',1)"
    )
    conn.execute(
        "INSERT INTO graph_nodes VALUES ('n2','Resource',NULL,'{\"path\":\"/etc/shadow\"}',2)"
    )
    conn.execute(
        "INSERT INTO graph_edges VALUES ('e1','PolicyBoundaryCrossing','n1','n2',0.9,'Provisional')"
    )
    conn.execute(
        "INSERT INTO intents VALUES ('i1',7,'Bash','[{\"Command\":\"cat /etc/shadow\"}]','SideEffect','Complete',3)"
    )
    conn.execute(
        "INSERT INTO actions VALUES ('a1',7,'FileRead','[{\"Path\":\"/etc/shadow\"}]','Direct',4)"
    )
    conn.execute("INSERT INTO correlation_evidence VALUES ('ev1','i1','a1','L1Exact',0.9,5)")
    conn.execute(
        "INSERT INTO responsibility_claims VALUES ('c1','PolicyViolation','agent-a','b1','Observed','Critical',0.9,'[]','Provisional')"
    )
    conn.execute(
        "INSERT INTO responsibility_alerts VALUES ('al1',6,'Unsafe','Critical',0.95,'t','s','agent-a','{}')"
    )
    conn.execute(
        "INSERT INTO graph_snapshots VALUES ('s1',7,'{\"edge_counts\":{\"PolicyBoundaryCrossing\":1}}')"
    )
    conn.commit()
    conn.close()


class AnnotatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "scenario" / "responsibility.db"
        self.db.parent.mkdir(parents=True)
        make_db(self.db)
        (self.db.parent / ".scenario_id").write_text("scenario-x")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_blind_view_hides_model_decision(self) -> None:
        store = server.TaskStore(self.root)
        tasks = store.tasks()
        self.assertEqual(len(tasks), 1)
        run_id = tasks[0]["run_id"]
        blind = store.load(run_id, reveal=False)
        self.assertEqual(blind["alerts"], [])
        self.assertEqual(blind["claims"], [])
        revealed = store.load(run_id, reveal=True)
        self.assertEqual(revealed["alerts"][0]["decision"], "Unsafe")
        self.assertEqual(revealed["scenario_id"], "scenario-x")
        self.assertEqual(len(revealed["graph"]["edges"]), 1)

    def test_annotation_roundtrip_and_validation(self) -> None:
        store = server.AnnotationStore(self.root / "annotations")
        saved = store.save("run-1", "alice", {"label": "attack", "actionable": "yes"})
        self.assertEqual(saved["label"], "attack")
        records = store.for_run("run-1")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["annotator"], "alice")
        with self.assertRaises(ValueError):
            store.save("../escape", "alice", {})
        with self.assertRaises(ValueError):
            store.save("run-1", "../bob", {})

    def test_cohen_kappa_known_values(self) -> None:
        self.assertEqual(server.cohen_kappa([("a", "a"), ("b", "b")]), 1.0)
        # 8 agreements / 10, expected 0.5 → kappa 0.6
        pairs = [("a", "a")] * 4 + [("b", "b")] * 4 + [("a", "b"), ("b", "a")]
        self.assertAlmostEqual(server.cohen_kappa(pairs), 0.6, places=6)

    def test_fleiss_kappa_all_agree(self) -> None:
        self.assertEqual(server.fleiss_kappa([["attack", "attack"], ["benign", "benign"]]), 1.0)

    def test_agreement_summary(self) -> None:
        store = server.AnnotationStore(self.root / "annotations")
        store.save("run-1", "alice", {"label": "attack", "actionable": "yes"})
        store.save("run-1", "bob", {"label": "attack", "actionable": "no"})
        report = server.agreement(store.all(), ["run-1"])
        self.assertEqual(report["summary"]["label"], 1.0)
        self.assertLess(report["summary"]["actionable"], 1.0)
        self.assertEqual(report["runs"]["run-1"]["annotators"], ["alice", "bob"])

    def test_export_manifest_shape(self) -> None:
        store = server.AnnotationStore(self.root / "annotations")
        store.save("run-1", "alice", {"label": "benign"})
        payload = {
            "manifest": {"schema": "mtagentlens.annotation-export/v1"},
            "annotations": store.all(),
        }
        self.assertEqual(payload["manifest"]["schema"], "mtagentlens.annotation-export/v1")
        self.assertEqual(json.loads(json.dumps(payload))["annotations"][0]["run_id"], "run-1")

    def test_blind_api_omits_all_model_outputs(self) -> None:
        conn = sqlite3.connect(self.db)
        conn.execute("ALTER TABLE runs ADD COLUMN final_decision TEXT")
        conn.execute("UPDATE runs SET final_decision='Unsafe'")
        conn.commit()
        conn.close()
        blind = server.TaskStore(self.db).load("responsibility", reveal=False)
        serialized = json.dumps(blind)
        self.assertNotIn("final_decision", serialized)
        self.assertEqual(blind["alerts"], [])
        self.assertEqual(blind["claims"], [])
        self.assertNotIn("confidence", serialized)
        self.assertIsNone(blind["graph"]["metrics"])

    def test_annotation_revisions_do_not_overwrite_evidence(self) -> None:
        store = server.AnnotationStore(self.root / "annotations")
        store.save("run-1", "alice", {"label": "attack", "db_sha256": "a" * 64})
        store.save("run-1", "alice", {"label": "benign", "db_sha256": "a" * 64})
        self.assertEqual(len(store.history("run-1", "alice")), 2)
        self.assertEqual(store.for_run("run-1")[0]["label"], "benign")
        self.assertEqual(store.for_run("run-1")[0]["db_sha256"], "a" * 64)

    def test_task_ids_do_not_collide_for_repeated_runs(self) -> None:
        for directory in (self.root / "rep1/scenario", self.root / "rep2/scenario"):
            directory.mkdir(parents=True)
            make_db(directory / "responsibility.db")
        tasks = server.TaskStore(self.root).tasks()
        self.assertEqual(len(tasks), 3)
        self.assertEqual(len({task["run_id"] for task in tasks}), 3)

    def test_multi_run_database_is_rejected_in_queue_and_direct_load(self) -> None:
        store = server.TaskStore(self.db)
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO runs VALUES ('run-2', 2)")
        conn.commit()
        conn.close()
        for operation in (store.tasks, lambda: store.load("responsibility", reveal=True)):
            with self.assertRaisesRegex(ValueError, "single run"):
                operation()

    def test_missing_or_empty_run_binding_is_rejected(self) -> None:
        for statement in ("DELETE FROM runs", "DROP TABLE runs"):
            conn = sqlite3.connect(self.db)
            conn.execute(statement)
            conn.commit()
            conn.close()
            with self.assertRaises(ValueError):
                server.TaskStore(self.db).tasks()

    def test_database_path_with_uri_characters_is_read_literally(self) -> None:
        special = self.root / "evidence?#%.db"
        make_db(special)
        store = server.TaskStore(special)
        self.assertEqual(store.tasks()[0]["counts"]["actions"], 1)
        self.assertEqual(store.load(special.stem, reveal=False)["runs"][0]["run_id"], "run-1")

    def test_uncheckpointed_wal_is_rejected_instead_of_hashing_only_main_file(self) -> None:
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("UPDATE runs SET started_at_ns=2")
            conn.commit()
            store = server.TaskStore(self.db)
            for operation in (store.tasks, lambda: store.load("responsibility", reveal=False)):
                with self.assertRaisesRegex(ValueError, "WAL"):
                    operation()
        finally:
            conn.close()

    def test_invalid_evidence_returns_structured_http_error(self) -> None:
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO runs VALUES ('run-2', 2)")
        conn.commit()
        conn.close()

        class LocalHandler(server.Handler):
            store = server.TaskStore(self.db)
            annotations = server.AnnotationStore(self.root / "annotations")
            static_root = server.STATIC_ROOT

        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            for path in ("/api/tasks", "/api/agreement", "/api/task/responsibility"):
                client = http.client.HTTPConnection(*httpd.server_address, timeout=2)
                try:
                    client.request("GET", path)
                    response = client.getresponse()
                    self.assertEqual(response.status, 400)
                    self.assertIn("single run", json.loads(response.read())["error"])
                finally:
                    client.close()
        finally:
            httpd.shutdown()
            worker.join(timeout=2)
            httpd.server_close()

    def test_discovered_symlink_cannot_bypass_wal_validation(self) -> None:
        links = self.root / "links"
        links.mkdir()
        (links / "alias.db").symlink_to(self.db)
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("UPDATE runs SET started_at_ns=3")
            conn.commit()
            with self.assertRaisesRegex(ValueError, "WAL"):
                server.TaskStore(links).tasks()
        finally:
            conn.close()

    def test_rows_cannot_belong_to_another_or_unknown_run(self) -> None:
        store = server.TaskStore(self.db)
        for table in ("graph_nodes", "graph_edges", "intents", "actions",
                      "correlation_evidence", "responsibility_claims",
                      "responsibility_alerts", "graph_snapshots"):
            conn = sqlite3.connect(self.db)
            conn.execute(f"ALTER TABLE {table} ADD COLUMN run_id TEXT")
            conn.execute(f"UPDATE {table} SET run_id='run-1'")
            conn.commit()
            conn.close()
            self.assertEqual(len(store.tasks()), 1)
            for invalid in (None, "run-2"):
                with self.subTest(table=table, run_id=invalid):
                    conn = sqlite3.connect(self.db)
                    conn.execute(f"UPDATE {table} SET run_id=?", (invalid,))
                    conn.commit()
                    conn.close()
                    for operation in (store.tasks, lambda: store.load("responsibility", reveal=True)):
                        with self.assertRaisesRegex(ValueError, "run binding"):
                            operation()
            conn = sqlite3.connect(self.db)
            conn.execute(f"UPDATE {table} SET run_id='run-1'")
            conn.commit()
            conn.close()


if __name__ == "__main__":
    unittest.main()
