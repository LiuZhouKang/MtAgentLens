#!/usr/bin/env python3
"""Backfill Type C sibling-agent topology boundaries in frozen DBs.

This is a dataset repair tool, not a detector shortcut. It only inserts
InfrastructureSpawn boundaries when a Type C sample already has multiple
agent_identities and the process table proves that several agent processes
were launched by the same non-agent runner process.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AGENT_COMMS = {"claude", "claude-code", "claude.exe"}


@dataclass(frozen=True)
class AgentProcess:
    agent_id: str
    pid: int
    start_time_ns: int
    parent_pid: int


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("compare/Dataset/FuxiBench"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    report = backfill_dataset(args.dataset.resolve(), dry_run=args.dry_run)
    out_path = args.dataset / "quality" / "type_c_boundary_backfill_report.json"
    write_json(out_path, report)
    print(f"Wrote {out_path}")
    print(f"Updated DBs: {report['summary']['updated_dbs']}")
    print(f"Inserted boundaries: {report['summary']['inserted_boundaries']}")
    return 0


def backfill_dataset(dataset: Path, *, dry_run: bool = False) -> dict[str, Any]:
    manifest = read_json(dataset / "quality" / "manifest.json")
    samples = manifest.get("samples", [])
    records = []
    for sample in samples:
        if not is_type_c_sample(sample):
            continue
        db_rel = sample.get("paths", {}).get("responsibility_db")
        if not isinstance(db_rel, str) or not db_rel:
            continue
        db_path = dataset_path(dataset, db_rel)
        records.append(backfill_db(dataset, sample, db_path, dry_run=dry_run))

    return {
        "schema": "mtagentlens.type_c_boundary_backfill.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset.as_posix(),
        "dry_run": dry_run,
        "summary": {
            "type_c_samples": len(records),
            "updated_dbs": sum(1 for record in records if record["inserted_boundaries"] > 0),
            "inserted_boundaries": sum(record["inserted_boundaries"] for record in records),
        },
        "samples": records,
    }


def backfill_db(dataset: Path, sample: dict[str, Any], db_path: Path, *, dry_run: bool) -> dict[str, Any]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        tables = {
            row[0]
            for row in con.execute("select name from sqlite_master where type='table'")
            if not row[0].startswith("sqlite_")
        }
        if not {"agent_identities", "process_events", "subagent_boundaries"}.issubset(tables):
            return sample_record(dataset, sample, db_path, 0, "missing_required_tables")

        existing = int(con.execute("select count(*) from subagent_boundaries").fetchone()[0])
        if existing > 0:
            return sample_record(dataset, sample, db_path, 0, "already_has_boundaries")

        run_id = current_run_id(con)
        agent_processes = load_agent_processes(con)
        candidates = infer_sibling_boundaries(con, agent_processes)
        if not candidates:
            return sample_record(dataset, sample, db_path, 0, "no_sibling_agent_candidates")

        if not dry_run:
            for parent, child in candidates:
                con.execute(
                    """
                    insert or ignore into subagent_boundaries (
                      boundary_id, parent_agent_id, child_agent_id,
                      parent_pid, parent_start_time_ns,
                      child_pid, child_start_time_ns, reason, run_id
                    ) values (?, ?, ?, ?, ?, ?, ?, 'InfrastructureSpawn', ?)
                    """,
                    (
                        boundary_id(parent, child),
                        parent.agent_id,
                        child.agent_id,
                        parent.pid,
                        parent.start_time_ns,
                        child.pid,
                        child.start_time_ns,
                        run_id,
                    ),
                )
            con.commit()

        return sample_record(
            dataset,
            sample,
            db_path,
            len(candidates),
            "inserted" if not dry_run else "dry_run",
            candidates=[
                {
                    "parent_agent_id": parent.agent_id,
                    "child_agent_id": child.agent_id,
                    "parent_pid": parent.pid,
                    "child_pid": child.pid,
                    "reason": "InfrastructureSpawn",
                }
                for parent, child in candidates
            ],
        )
    finally:
        con.close()


def infer_sibling_boundaries(
    con: sqlite3.Connection,
    agent_processes: list[AgentProcess],
) -> list[tuple[AgentProcess, AgentProcess]]:
    comm_by_pid = {
        int(row["pid"]): str(row["comm"] or "")
        for row in con.execute("select pid, comm from process_events")
    }
    by_parent: dict[int, list[AgentProcess]] = {}
    for process in agent_processes:
        if process.parent_pid <= 1:
            continue
        if comm_by_pid.get(process.parent_pid) in AGENT_COMMS:
            continue
        by_parent.setdefault(process.parent_pid, []).append(process)

    inferred: list[tuple[AgentProcess, AgentProcess]] = []
    for siblings in by_parent.values():
        unique_agents = {process.agent_id for process in siblings}
        if len(unique_agents) < 2:
            continue
        siblings.sort(key=lambda p: (p.start_time_ns, p.pid))
        parent = siblings[0]
        seen_children: set[str] = set()
        for child in siblings[1:]:
            if child.agent_id == parent.agent_id or child.agent_id in seen_children:
                continue
            inferred.append((parent, child))
            seen_children.add(child.agent_id)
    return inferred


def load_agent_processes(con: sqlite3.Connection) -> list[AgentProcess]:
    process_rows = {
        int(row["pid"]): row
        for row in con.execute("select pid, start_time_ns, parent_pid, comm from process_events")
        if str(row["comm"] or "") in AGENT_COMMS
    }
    result: list[AgentProcess] = []
    for row in con.execute("select agent_id, process_ids_json from agent_identities"):
        try:
            process_ids = json.loads(row["process_ids_json"] or "[]")
        except json.JSONDecodeError:
            process_ids = []
        for item in process_ids:
            if not isinstance(item, dict) or "pid" not in item:
                continue
            pid = int(item["pid"])
            process_row = process_rows.get(pid)
            if process_row is None:
                continue
            result.append(
                AgentProcess(
                    agent_id=str(row["agent_id"]),
                    pid=pid,
                    start_time_ns=int(process_row["start_time_ns"] or item.get("start_time_ns") or 0),
                    parent_pid=int(process_row["parent_pid"] or 0),
                )
            )
    return result


def current_run_id(con: sqlite3.Connection) -> str | None:
    tables = {
        row[0]
        for row in con.execute("select name from sqlite_master where type='table'")
        if not row[0].startswith("sqlite_")
    }
    if "runs" not in tables:
        return None
    columns = {row[1] for row in con.execute("pragma table_info(runs)")}
    if "run_id" not in columns:
        return None
    row = con.execute("select run_id from runs limit 1").fetchone()
    return str(row["run_id"]) if row and row["run_id"] is not None else None


def sample_record(
    dataset: Path,
    sample: dict[str, Any],
    db_path: Path,
    inserted: int,
    status: str,
    *,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "scenario_id": sample.get("scenario_id"),
        "category": sample.get("category"),
        "responsibility_db": rel(dataset, db_path),
        "status": status,
        "inserted_boundaries": inserted,
        "candidates": candidates or [],
    }


def is_type_c_sample(sample: dict[str, Any]) -> bool:
    gt = sample.get("ground_truth_summary", {})
    return isinstance(gt, dict) and gt.get("attack_type") == "C"


def boundary_id(parent: AgentProcess, child: AgentProcess) -> str:
    return f"{parent.agent_id}-{child.agent_id}-{parent.pid}-{child.pid}"


def dataset_path(dataset: Path, relpath: str) -> Path:
    path = (dataset / relpath).resolve()
    path.relative_to(dataset.resolve())
    return path


def rel(dataset: Path, path: Path) -> str:
    return path.resolve().relative_to(dataset.resolve()).as_posix()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
