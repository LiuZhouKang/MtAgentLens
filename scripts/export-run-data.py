#!/usr/bin/env python3
"""Export the latest graph snapshot + alerts from a MtAgentLens run to JSON.

Usage: python3 scripts/export-run-data.py <run_dir> <output.json>
Example:
  python3 scripts/export-run-data.py \
    env/runs/20260716-214402-9b8e48fd \
    frontend/public/data/sample-run.json
"""

import json
import sqlite3
import sys
from pathlib import Path


def export_run(run_dir: str, output_path: str) -> None:
    db_path = Path(run_dir) / "collector" / "collector.responsibility.db"
    if not db_path.exists():
        print(f"ERROR: database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    db = sqlite3.connect(str(db_path))

    # --- latest graph snapshot ---
    row = db.execute(
        "SELECT snapshot_id, timestamp_ns, nodes_json, edges_json, metrics_json "
        "FROM graph_snapshots ORDER BY timestamp_ns DESC LIMIT 1"
    ).fetchone()
    if not row:
        print("ERROR: no graph snapshots found", file=sys.stderr)
        sys.exit(1)

    snapshot_id, ts_ns, nodes_json, edges_json, metrics_json = row
    nodes = json.loads(nodes_json)
    edges = json.loads(edges_json)
    metrics = json.loads(metrics_json) if metrics_json else {}

    # --- all alerts ---
    alert_rows = db.execute(
        "SELECT alert_id, timestamp_ns, display_profile, decision, severity, "
        "confidence, authority, reasoning, title, summary, action_hint, "
        "primary_agent_id, involved_agents_json, engine_section_json, "
        "timeline_json, graph_metrics_json, phase, provisional_ref "
        "FROM responsibility_alerts ORDER BY timestamp_ns DESC"
    ).fetchall()

    alerts = []
    for r in alert_rows:
        engine = json.loads(r[13]) if r[13] else {}
        timeline_raw = r[14]
        timeline = json.loads(timeline_raw) if timeline_raw else {"steps": [], "total_duration_ms": 0}
        gmetrics = json.loads(r[15]) if r[15] else {}
        involved = json.loads(r[12]) if r[12] else []

        # Phase is stored in Rust debug format, e.g. "Provisional { timestamp_ns: 123 }"
        phase_raw = r[16]
        if phase_raw and phase_raw.startswith("Provisional"):
            import re
            m = re.search(r'timestamp_ns:\s*(\d+)', phase_raw)
            phase = {"Provisional": {"timestamp_ns": int(m.group(1)) if m else r[1]}}
        elif phase_raw and phase_raw.startswith("Authoritative"):
            import re
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
                "snapshot_id": snapshot_id,
                "timestamp_ns": ts_ns,
                "nodes": nodes,
                "edges": edges,
                "metrics": metrics,
            },
            "phase": phase,
            "provisional_ref": r[17],
        })

    db.close()

    output = {
        "exported_at_ns": ts_ns,
        "snapshot_id": snapshot_id,
        "graph": {"nodes": nodes, "edges": edges},
        "metrics": metrics,
        "alerts": alerts,
    }

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"Exported {len(nodes)} nodes, {len(edges)} edges, {len(alerts)} alerts → {out}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <run_dir> <output.json>", file=sys.stderr)
        sys.exit(1)
    export_run(sys.argv[1], sys.argv[2])
