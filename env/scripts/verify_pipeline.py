#!/usr/bin/env python3
"""MtAgentLens Pipeline E2E Verification — runs multiple scenarios and produces a report card."""
import sqlite3, json, os, subprocess, sys, time, glob
from pathlib import Path

ENV_DIR = Path(__file__).resolve().parent.parent
SCENARIOS = ["dev-readme-check", "dev-pbc-trigger", "subagents-collaborative"]
TIMEOUT = 600

def run_scenario(name):
    t0 = time.time()
    proc = subprocess.run(
        ["make", "env-run-fast", f"SCENARIO={name}"],
        cwd=ENV_DIR, capture_output=True, text=True, timeout=TIMEOUT,
        env={**os.environ, "SUDO_ASKPASS": "/bin/true"}
    )
    elapsed = time.time() - t0
    return proc.returncode == 0, proc.stdout, elapsed

def find_db(run_dir_stem):
    runs = sorted(glob.glob(str(ENV_DIR / "runs" / run_dir_stem)))
    for run in runs:
        for f in os.listdir(run):
            if f.endswith(".db"):
                return os.path.join(run, f)
    return None

def audit_scenario(name, db_path):
    db = sqlite3.connect(db_path)

    result = {}
    result["name"] = name

    # Core pipeline
    result["intents"] = db.execute("SELECT COUNT(*) FROM intents").fetchone()[0]
    result["actions"] = db.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
    result["evidence"] = db.execute("SELECT COUNT(*) FROM correlation_evidence").fetchone()[0]
    result["pbc_edges"] = db.execute(
        "SELECT COUNT(*) FROM graph_edges WHERE edge_type='PolicyBoundaryCrossing'"
    ).fetchone()[0]
    result["claims"] = db.execute("SELECT COUNT(*) FROM responsibility_claims").fetchone()[0]
    result["tracebacks"] = db.execute("SELECT COUNT(*) FROM responsibility_tracebacks").fetchone()[0]
    result["alerts"] = db.execute("SELECT COUNT(*) FROM responsibility_alerts").fetchone()[0]

    # Identity
    result["agent_identities"] = db.execute("SELECT COUNT(*) FROM agent_identities").fetchone()[0]
    result["identity_refinements"] = db.execute("SELECT COUNT(*) FROM identity_refinements").fetchone()[0]

    # Identity map overlap (Phase 1↔Phase 2 consistency)
    ipm_agents = set(r[0] for r in db.execute("SELECT DISTINCT agent_id FROM identity_process_map"))
    id_agents = set(r[0] for r in db.execute("SELECT DISTINCT agent_id FROM agent_identities"))
    result["identity_map_overlap"] = len(ipm_agents & id_agents)

    # Evidence quality
    ev = db.execute(
        "SELECT match_level, COUNT(*) FROM correlation_evidence GROUP BY match_level"
    ).fetchall()
    result["evidence_by_level"] = {r[0]: r[1] for r in ev}
    total = sum(result["evidence_by_level"].values())
    result["l1_l3_pct"] = round(
        sum(result["evidence_by_level"].get(l, 0) for l in ["L1Exact","L2Normalized","L3Containment"])
        / max(total, 1) * 100, 1
    )

    # Alert quality: check for real agent IDs (not "unknown")
    if result["alerts"] > 0:
        unknown = db.execute(
            "SELECT COUNT(*) FROM responsibility_alerts WHERE primary_agent_id='unknown'"
        ).fetchone()[0]
        result["alerts_unknown_pct"] = round(unknown / result["alerts"] * 100, 1)
        result["alerts_high_severity"] = db.execute(
            "SELECT COUNT(*) FROM responsibility_alerts WHERE severity IN ('Warning','Critical')"
        ).fetchone()[0]

    # Empty tables
    empty_tables = []
    for t in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        count = db.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
        if count == 0:
            empty_tables.append(t[0])
    result["empty_tables"] = empty_tables

    db.close()
    return result

def grade(result):
    """Score a scenario result from 0-100."""
    score = 100

    # Must have intents (basic functionality)
    if result["intents"] == 0: score -= 30

    # Evidence quality
    if result["l1_l3_pct"] < 20: score -= 10
    if result["l1_l3_pct"] < 50: score -= 5

    # Identity consistency
    if result["agent_identities"] > 0 and result["identity_map_overlap"] == 0: score -= 20

    # Alert quality
    if result.get("alerts_unknown_pct", 0) > 10: score -= 10

    return max(score, 0)

def main():
    print("=" * 70)
    print("MtAgentLens Pipeline E2E Verification Report")
    print("=" * 70)

    results = {}
    for name in SCENARIOS:
        print(f"\nRunning: {name} ... ", end="", flush=True)
        ok, stdout, elapsed = run_scenario(name)
        status = "OK" if ok else "FAILED"
        print(f"{status} ({elapsed:.0f}s)")

        # Find the DB
        time.sleep(1)
        db_path = find_db(f"*{name}*")
        if not db_path:
            # Try latest run
            all_runs = sorted(glob.glob(str(ENV_DIR / "runs" / "*")), key=os.path.getmtime, reverse=True)
            for run in all_runs:
                for f in os.listdir(run):
                    if f.endswith(".db"):
                        db_path = os.path.join(run, f)
                        break
                if db_path: break

        if not db_path:
            print(f"  ⚠ No DB found for {name}")
            continue

        result = audit_scenario(name, db_path)
        score = grade(result)
        results[name] = (result, score)

    # Report card
    print("\n" + "=" * 70)
    print("REPORT CARD")
    print("=" * 70)

    total_score = 0
    for name, (r, score) in results.items():
        total_score += score
        print(f"\n{'─' * 50}")
        print(f"Scenario: {name}  |  Score: {score}/100")
        print(f"{'─' * 50}")
        print(f"  Pipeline: intents={r['intents']} → evidence={r['evidence']} → PBC={r['pbc_edges']} → claims={r['claims']} → tracebacks={r['tracebacks']} → alerts={r['alerts']}")
        print(f"  Identity: agents={r['agent_identities']} refinements={r['identity_refinements']} map_overlap={r['identity_map_overlap']}")

        if r['evidence'] > 0:
            print(f"  Evidence quality: L1-L3={r['l1_l3_pct']}%")
            for l, c in sorted(r['evidence_by_level'].items()):
                pct = round(c / max(r['evidence'], 1) * 100)
                print(f"    {l}: {c} ({pct}%)")

        if r['alerts'] > 0:
            unknown = r.get('alerts_unknown_pct', 0)
            high_sev = r.get('alerts_high_severity', 0)
            print(f"  Alerts: total={r['alerts']} unknown_agent={unknown}% high_severity={high_sev}")

        if r['empty_tables']:
            print(f"  Empty tables: {', '.join(r['empty_tables'][:8])}")

    avg = total_score // len(results) if results else 0
    print(f"\n{'=' * 70}")
    print(f"OVERALL SCORE: {avg}/100")
    print(f"{'=' * 70}")

if __name__ == "__main__":
    main()
