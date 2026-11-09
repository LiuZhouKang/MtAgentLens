#!/usr/bin/env python3
"""Extract frozen root-agent predictions and blinded evidence packets from run DBs."""
import argparse, json, os, re, sqlite3
from collections import Counter
from pathlib import Path

ROOT_RE = re.compile(r"Root cause candidate:\s*Agent\(([^)]+)\)")

def q(con, sql, args=()):
    return con.execute(sql, args).fetchall()

def load_run(rec):
    run_dir = Path(rec["run_dir"])
    db = run_dir / "collector" / "collector.responsibility.db"
    scenario_path = Path("datasets/FuxiBench/scenarios") / f"{rec['scenario_id']}.json"
    scenario = json.loads(scenario_path.read_text())
    summary = json.loads((run_dir / "summary.json").read_text())
    behavior_path = run_dir / "behavior.json"
    behavior = json.loads(behavior_path.read_text()) if behavior_path.exists() else None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    agents = []
    for aid, pids_json, conf, refs_json in q(con, "select agent_id, process_ids_json, confidence, provisional_refs_json from agent_identities"):
        agents.append({"agent_id": aid, "process_ids": json.loads(pids_json or "[]"),
                       "confidence": conf, "provisional_refs": json.loads(refs_json or "[]")})
    pids_to_agent = {}
    for a in agents:
        for p in a["process_ids"]:
            pid = p.get("pid") if isinstance(p, dict) else p
            pids_to_agent[str(pid)] = a["agent_id"]
    # actor_id -> index from runner events; pid -> actor from process_started.
    idx_by_actor, actor_by_pid = {}, {}
    for line in (run_dir / "runner-events.jsonl").read_text().splitlines():
        e = json.loads(line)
        if e.get("event_type") == "launch_attempt":
            idx_by_actor[e.get("actor_id")] = e.get("details", {}).get("agent_index")
        if e.get("event_type") == "process_started":
            actor_by_pid[str(e.get("process_key", {}).get("pid"))] = e.get("actor_id")
    idx_by_agent = {}
    for a in agents:
        for p in a["process_ids"]:
            pid = p.get("pid") if isinstance(p, dict) else p
            actor = actor_by_pid.get(str(pid))
            if actor in idx_by_actor:
                idx_by_agent[a["agent_id"]] = idx_by_actor[actor]
    # Some final-v1 runs omit process_started; launch order + PID order is the
    # only frozen mapping available. Use ascending first PID as the fallback.
    if len(idx_by_agent) < len(agents):
        ordered = sorted(agents, key=lambda a: min(
            [(p.get("pid") if isinstance(p, dict) else p) for p in a["process_ids"]] or [10**18]))
        idx_by_agent = {a["agent_id"]: i for i, a in enumerate(ordered)}
    # Scenario roles by index.
    roles = {}
    for i, ag in enumerate(scenario.get("agents", [])):
        roles[i] = {"role_file": ag.get("role_file"), "task": ag.get("task", "")[:3000]}
    # claims + tracebacks.
    claims = []
    for cid, stype, subj, sev, conf, refs, tb in q(con, "select claim_id, claim_type, subject_agent_id, severity, confidence, evidence_refs_json, traceback_ref from responsibility_claims"):
        claims.append({"claim_id": cid, "claim_type": stype, "subject_agent_id": subj,
                       "severity": sev, "confidence": conf, "traceback_ref": tb,
                       "evidence_refs": json.loads(refs or "[]")})
    tb_rows = q(con, "select claim_id, root_cause_node_id, explanation_summary, confidence from responsibility_tracebacks")
    raw_roots = [{"claim_id": c, "raw_root": r, "confidence": conf,
                  "root_agent": (ROOT_RE.search(exp or "").group(1) if ROOT_RE.search(exp or "") else None)}
                 for c, r, exp, conf in tb_rows]
    con.close()
    aliases = {}
    canonical = [a["agent_id"] for a in agents]
    for a in agents:
        aliases[a["agent_id"]] = a["agent_id"]
        for ref in a["provisional_refs"]:
            aliases[ref] = a["agent_id"]
    def resolve(raw):
        if not raw:
            return raw
        if raw in aliases:
            return aliases[raw]
        matches = [c for c in canonical if c.startswith(raw)]
        if len(matches) == 1:
            return matches[0]
        ref_matches = [a["agent_id"] for a in agents
                       for r in a["provisional_refs"] if r.startswith(raw)]
        if len(set(ref_matches)) == 1:
            return ref_matches[0]
        return raw
    for x in raw_roots:
        x["agent_id"] = resolve(x["root_agent"])
    canonical_agents = {a["agent_id"] for a in agents}
    roots = Counter(x["agent_id"] for x in raw_roots
                    if x["agent_id"] in canonical_agents)
    # Blinded packet: no root predictions / no traceback roots.
    packet = {
        "scenario_id": rec["scenario_id"],
        "category": scenario.get("category"),
        "description": scenario.get("description", "")[:2000],
        "agents": [{"agent_id": a["agent_id"], "agent_index": idx_by_agent.get(a["agent_id"]),
                    "role_file": roles.get(idx_by_agent.get(a["agent_id"]), {}).get("role_file"),
                    "task": roles.get(idx_by_agent.get(a["agent_id"]), {}).get("task"),
                    "process_ids": a["process_ids"], "provisional_refs": a["provisional_refs"]}
                   for a in agents],
        "observed_files_by_index": (behavior or {}).get("agents", []),
        "claims": [{"claim_id": c["claim_id"], "claim_type": c["claim_type"],
                    "subject_agent_id": c["subject_agent_id"], "severity": c["severity"],
                    "confidence": c["confidence"]} for c in claims[:80]],
        "claim_count": len(claims),
        "final_decision": rec["final_decision"],
        "marker_executed": rec["marker_executed"],
    }
    return packet, {"scenario_id": rec["scenario_id"], "run_dir": str(run_dir),
                    "final_decision": rec["final_decision"], "marker_executed": rec["marker_executed"],
                    "tracebacks": len(tb_rows), "root_counts": dict(roots.most_common()),
                    "root_set": [k for k, _ in roots.most_common()],
                    "claims": len(claims), "agents": [a["agent_id"] for a in agents],
                    "raw_roots": raw_roots[:10]}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", nargs="*")
    ap.add_argument("--out", type=Path, default=Path("compare/results/attribution"))
    a = ap.parse_args()
    rows = {json.loads(l)["scenario_id"]: json.loads(l)
            for l in open("local/final-v1/results-promoted.jsonl")}
    if a.scenarios:
        selected = a.scenarios
    else:
        selected = sorted(rows)
    for sid in selected:
        packet, pred = load_run(rows[sid])
        (a.out / "packets" / f"{sid}.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2))
        (a.out / "predictions" / f"{sid}.json").write_text(json.dumps(pred, ensure_ascii=False, indent=2))
        print(sid, "roots", len(pred["root_set"]), "top", pred["root_set"][:3])

if __name__ == "__main__":
    main()
