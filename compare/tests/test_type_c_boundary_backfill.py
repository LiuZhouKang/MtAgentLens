import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dataset" / "backfill_type_c_boundaries.py"
SPEC = importlib.util.spec_from_file_location("backfill_type_c_boundaries", MODULE_PATH)
assert SPEC is not None
backfill_type_c_boundaries = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = backfill_type_c_boundaries
SPEC.loader.exec_module(backfill_type_c_boundaries)


def test_backfills_sibling_agents_under_non_agent_runner(tmp_path: Path):
    dataset = _dataset_root(tmp_path)
    _write_manifest(dataset)
    _write_db(dataset / "traces" / "agent_teams" / "role-hijack" / "responsibility.db")

    report = backfill_type_c_boundaries.backfill_dataset(dataset)

    assert report["summary"]["updated_dbs"] == 1
    assert report["summary"]["inserted_boundaries"] == 2
    con = sqlite3.connect(dataset / "traces" / "agent_teams" / "role-hijack" / "responsibility.db")
    try:
        rows = con.execute(
            "select parent_agent_id, child_agent_id, reason from subagent_boundaries order by child_pid"
        ).fetchall()
    finally:
        con.close()
    assert rows == [
        ("agent-a", "agent-b", "InfrastructureSpawn"),
        ("agent-a", "agent-c", "InfrastructureSpawn"),
    ]


def test_dry_run_does_not_write_boundaries(tmp_path: Path):
    dataset = _dataset_root(tmp_path)
    _write_manifest(dataset)
    _write_db(dataset / "traces" / "agent_teams" / "role-hijack" / "responsibility.db")

    report = backfill_type_c_boundaries.backfill_dataset(dataset, dry_run=True)

    assert report["summary"]["inserted_boundaries"] == 2
    con = sqlite3.connect(dataset / "traces" / "agent_teams" / "role-hijack" / "responsibility.db")
    try:
        assert con.execute("select count(*) from subagent_boundaries").fetchone()[0] == 0
    finally:
        con.close()


def _dataset_root(tmp_path: Path) -> Path:
    dataset = tmp_path / "FuxiBench"
    (dataset / "quality").mkdir(parents=True)
    (dataset / "traces" / "agent_teams" / "role-hijack").mkdir(parents=True)
    return dataset


def _write_manifest(dataset: Path) -> None:
    (dataset / "quality" / "manifest.json").write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "scenario_id": "role-hijack",
                        "category": "agent_teams",
                        "paths": {
                            "responsibility_db": "traces/agent_teams/role-hijack/responsibility.db"
                        },
                        "ground_truth_summary": {"attack_type": "C"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _write_db(path: Path) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("create table runs (run_id text)")
        con.execute("insert into runs values ('run-1')")
        con.execute(
            """
            create table agent_identities (
              agent_id text,
              process_ids_json text
            )
            """
        )
        for agent_id, pid in (("agent-a", 100), ("agent-b", 200), ("agent-c", 300)):
            con.execute(
                "insert into agent_identities values (?, ?)",
                (agent_id, json.dumps([{"pid": pid, "start_time_ns": pid}])),
            )
        con.execute(
            """
            create table process_events (
              pid integer,
              start_time_ns integer,
              parent_pid integer,
              comm text
            )
            """
        )
        con.execute("insert into process_events values (50, 50, 1, 'bash')")
        for pid in (100, 200, 300):
            con.execute("insert into process_events values (?, ?, 50, 'claude')", (pid, pid))
        con.execute(
            """
            create table subagent_boundaries (
              boundary_id text primary key,
              parent_agent_id text,
              child_agent_id text,
              parent_pid integer,
              parent_start_time_ns integer,
              child_pid integer,
              child_start_time_ns integer,
              reason text,
              run_id text
            )
            """
        )
        con.commit()
    finally:
        con.close()
