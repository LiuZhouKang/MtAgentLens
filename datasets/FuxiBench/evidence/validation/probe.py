#!/usr/bin/env python3
"""Read-only probe of the 2026-10-25 validation runs (finalized.db + health json).

Writes nothing; prints normalized facts for the DIAGNOSIS evidence base.
Usage: env/.venv/bin/python probe.py <subcommand> [run-key]
"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path("evidence/validation/legacy-run3/run3")
RUNS = {
    "benign": ROOT / "benign-api-doc/attempts/7b585f34-c9af-49fb-94b3-dc139809a7b1/runs/20261025-233615-784ded18348f",
    "credential": ROOT / "credential-theft-direct/attempts/653c5456-3c79-463e-8136-59be8b04dd77/runs/20261025-233657-3815edfdef6a",
    "atlas": ROOT / "atlas-recon-discovery/attempts/9db5d98e-ba10-4da6-8b44-63ff6fb9fe6d/runs/20261025-233836-9fb74203bf7a",
}


def db(key):
    p = RUNS[key] / "collector/finalized.db"
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def checkpoint(con):
    rows = con.execute(
        "select version, payload_json from analysis_checkpoints where checkpoint_kind='frozen-analysis'"
    ).fetchall()
    assert len(rows) == 1, len(rows)
    return json.loads(rows[0]["payload_json"])


def quality(key):
    cp = checkpoint(db(key))
    q = cp.get("capture_quality") or {}
    return {
        "status": q.get("status"),
        "sources": {
            k: {kk: vv for kk, vv in (v or {}).items() if kk != "diagnostics"}
            for k, v in (q.get("sources") or {}).items()
        },
        "analysis": q.get("analysis"),
    }


def cmd_quality(key):
    print(json.dumps(quality(key), indent=1, sort_keys=True))


def cmd_checkpoint_keys(key):
    cp = checkpoint(db(key))
    print(sorted(cp.keys()))
    print("contract", cp.get("contract"))
    print("implementation", cp.get("implementation"))
    print("observations", len(cp.get("observations") or []))
    print("delegations", json.dumps(cp.get("delegations"), indent=1)[:4000])


def cmd_runs(key):
    con = db(key)
    for row in con.execute("select * from runs"):
        print(json.dumps({k: row[k] for k in row.keys()}, indent=1, default=str))


def cmd_alerts(key):
    con = db(key)
    for row in con.execute("select * from responsibility_alerts"):
        print(json.dumps({k: row[k] for k in row.keys()}, indent=1, default=str))


def cmd_claims(key):
    con = db(key)
    for row in con.execute("select * from responsibility_claims"):
        print(json.dumps({k: row[k] for k in row.keys()}, indent=1, default=str))


def cmd_policies(key):
    con = db(key)
    for row in con.execute("select * from policy_evaluations"):
        print(json.dumps({k: row[k] for k in row.keys()}, indent=1, default=str))


def cmd_intents(key):
    con = db(key)
    for row in con.execute(
        "select intent_id, pid, tool_name, intent_class, parse_state, primary_matching_eligible, authority_source from intents order by timestamp_ns"
    ):
        print(dict(row))


def cmd_counts(key):
    con = db(key)
    tabs = [r[0] for r in con.execute(
        "select name from sqlite_master where type='table' order by name")]
    out = {}
    for t in tabs:
        out[t] = con.execute(f"select count(*) from {t}").fetchone()[0]
    print(json.dumps(out, indent=1, sort_keys=True))


def cmd_delegations(key):
    con = db(key)
    for row in con.execute("select * from delegation_records"):
        print(json.dumps({k: row[k] for k in row.keys()}, indent=1, default=str))


def cmd_boundaries(key):
    con = db(key)
    for row in con.execute("select * from subagent_boundaries"):
        print(json.dumps({k: row[k] for k in row.keys()}, indent=1, default=str))


def cmd_nolink(key):
    con = db(key)
    for row in con.execute(
        "select reason, count(*) n from non_link_diagnostics group by reason order by n desc"
    ):
        print(dict(row))


def cmd_identities(key):
    con = db(key)
    for row in con.execute("select * from agent_identities"):
        print(json.dumps({k: row[k] for k in row.keys()}, indent=1, default=str))
    for row in con.execute("select * from provisional_identities"):
        print("PROVISIONAL", json.dumps({k: row[k] for k in row.keys()}, default=str))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    cmd, key = sys.argv[1], sys.argv[2]
    fn = globals().get(f"cmd_{cmd}")
    if not fn:
        raise SystemExit(f"unknown subcommand {cmd}")
    fn(key)


if __name__ == "__main__":
    main()
