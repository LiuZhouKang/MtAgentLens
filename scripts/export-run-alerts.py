#!/usr/bin/env python3
"""Export alerts for a single run. Called by API route."""
import json, sqlite3, sys, re
from pathlib import Path

def main():
    run_dir = Path(sys.argv[1])
    db_path = run_dir / "collector" / "collector.responsibility.db"
    if not db_path.exists():
        print(json.dumps({"error": "Database not found"}))
        sys.exit(1)

    db = sqlite3.connect(str(db_path))

    # Get latest snapshot for graph_snapshot field
    snap_row = db.execute(
        "SELECT nodes_json, edges_json, metrics_json FROM graph_snapshots ORDER BY timestamp_ns DESC LIMIT 1"
    ).fetchone()
    snap_nodes = json.loads(snap_row[0]) if snap_row else []
    snap_edges = json.loads(snap_row[1]) if snap_row else []
    snap_metrics = json.loads(snap_row[2]) if snap_row else {}

    rows = db.execute(
        "SELECT alert_id, timestamp_ns, display_profile, decision, severity, "
        "confidence, authority, reasoning, title, summary, action_hint, "
        "primary_agent_id, involved_agents_json, engine_section_json, "
        "timeline_json, graph_metrics_json, phase, provisional_ref "
        "FROM responsibility_alerts ORDER BY timestamp_ns DESC"
    ).fetchall()

    alerts = []
    for r in rows:
        engine = json.loads(r[13]) if r[13] else {}
        timeline = json.loads(r[14]) if r[14] else {"steps": [], "total_duration_ms": 0}
        gmetrics = json.loads(r[15]) if r[15] else {}
        involved = json.loads(r[12]) if r[12] else []

        # Parse Rust debug format phase: "Provisional { timestamp_ns: 123 }"
        phase_raw = r[16]
        if phase_raw and phase_raw.startswith("Provisional"):
            m = re.search(r'timestamp_ns:\s*(\d+)', phase_raw)
            phase = {"Provisional": {"timestamp_ns": int(m.group(1)) if m else r[1]}}
        elif phase_raw and phase_raw.startswith("Authoritative"):
            m = re.search(r'timestamp_ns:\s*(\d+)', phase_raw)
            phase = {"Authoritative": {"timestamp_ns": int(m.group(1)) if m else r[1], "overrides": None}}
        else:
            phase = {"Provisional": {"timestamp_ns": r[1]}}

        alerts.append({
            "alert_id": r[0],
            "timestamp_ns": r[1],
            "display_profile": r[2],
            "decision": r[3],
            "severity": r[4],
            "confidence": r[5],
            "authority": r[6],
            "reasoning": r[7] or "",
            "title": r[8] or "",
            "summary": r[9] or "",
            "action_hint": r[10],
            "primary_agent": {"agent_id": r[11], "claimed_role": None},
            "involved_agents": involved,
            "engine": engine,
            "timeline": timeline,
            "graph_snapshot": {
                "snapshot_id": "api-export",
                "timestamp_ns": r[1],
                "nodes": snap_nodes,
                "edges": snap_edges,
                "metrics": snap_metrics,
            },
            "phase": phase,
            "provisional_ref": r[17],
        })

    db.close()
    print(json.dumps({"data": alerts}, ensure_ascii=False))

if __name__ == "__main__":
    main()
