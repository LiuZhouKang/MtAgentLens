#!/usr/bin/env python3
"""Read-only concise probes of the 2026-10-25 validation runs (finalized.db).

Prints only the columns needed for DIAGNOSIS; never dumps graph blobs.
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


def short(v, n=240):
    if v is None:
        return None
    s = str(v)
    return s if len(s) <= n else s[:n] + "…"


def cmd_claims(key):
    con = db(key)
    for r in con.execute("select * from responsibility_claims"):
        d = dict(r)
        d["evidence_refs_count"] = len(json.loads(d.pop("evidence_refs_json") or "[]"))
        d["competing_explanations"] = short(d.pop("competing_explanations_json"), 400)
        print(json.dumps(d, indent=1))


def cmd_alerts(key):
    con = db(key)
    for r in con.execute("select * from responsibility_alerts"):
        d = dict(r)
        for k in ("engine_section_json", "graph_metrics_json", "timeline_json",
                  "involved_agents_json"):
            v = d.pop(k, None)
            if k == "engine_section_json":
                d["engine_section"] = short(v, 1200)
            elif k == "timeline_json":
                try:
                    t = json.loads(v or "[]")
                    d["timeline_len"] = len(t)
                    d["timeline_last"] = t[-1] if t else None
                except Exception:
                    d["timeline"] = short(v, 300)
            else:
                d[k] = short(v, 300)
        print(json.dumps(d, indent=1, default=str))


def cmd_policies(key):
    con = db(key)
    for r in con.execute("select * from policy_evaluations"):
        d = dict(r)
        d["judgment"] = short(d.pop("judgment_json"), 500)
        d["resource"] = short(d.pop("resource_json"), 300)
        d["evidence_refs_count"] = len(json.loads(d.pop("evidence_refs_json") or "[]"))
        print(json.dumps(d, indent=1, default=str))


def cmd_runs(key):
    con = db(key)
    for r in con.execute("select * from runs"):
        print(json.dumps(dict(r), indent=1, default=str))


def cmd_intents(key):
    con = db(key)
    for r in con.execute(
        "select intent_id, tool_name, intent_class, parse_state, primary_matching_eligible,"
        " authority_source, tool_args_json, timestamp_ns from intents order by timestamp_ns"
    ):
        d = dict(r)
        d["tool_args"] = short(d.pop("tool_args_json"), 160)
        print(json.dumps(d, default=str))


def cmd_refinements(key):
    con = db(key)
    print("-- classification_refinements --")
    for r in con.execute("select * from classification_refinements"):
        print(json.dumps(dict(r), indent=1, default=str))
    print("-- claim_refinements --")
    for r in con.execute("select * from claim_refinements"):
        print(json.dumps(dict(r), indent=1, default=str))


def cmd_counts(key):
    con = db(key)
    tabs = [r[0] for r in con.execute(
        "select name from sqlite_master where type='table' order by name")]
    print(json.dumps({t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in tabs},
                     indent=1, sort_keys=True))


def cmd_delegations(key):
    con = db(key)
    for r in con.execute("select * from delegation_records"):
        print("DELEGATION", json.dumps(dict(r), default=str))
    for r in con.execute("select * from subagent_boundaries"):
        print("BOUNDARY", json.dumps(dict(r), default=str))


def cmd_nolink(key):
    con = db(key)
    for r in con.execute("select reason, count(*) n from non_link_diagnostics group by reason order by n desc"):
        print(dict(r))


def cmd_checkpoint(key):
    con = db(key)
    for r in con.execute("select version, payload_json, checkpoint_kind from analysis_checkpoints"):
        p = json.loads(r["payload_json"])
        print(json.dumps({
            "checkpoint_kind": r["checkpoint_kind"], "version": r["version"],
            "contract": p.get("contract"), "run_id": p.get("run_id"),
            "observations": len(p.get("observations") or []),
            "delegations": p.get("delegations"),
            "score_signals": p.get("score_signals"),
            "identity_count": len(p.get("identities") or []),
            "fallback_agent_id": p.get("fallback_agent_id"),
        }, indent=1, default=str))


def cmd_identities(key):
    con = db(key)
    for r in con.execute("select * from agent_identities"):
        print("IDENTITY", json.dumps(dict(r), default=str))
    for r in con.execute("select * from provisional_identities"):
        print("PROVISIONAL", json.dumps(dict(r), default=str))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return
    fn = globals().get(f"cmd_{sys.argv[1]}")
    if not fn:
        raise SystemExit(f"unknown subcommand {sys.argv[1]}")
    fn(sys.argv[2])


if __name__ == "__main__":
    main()
