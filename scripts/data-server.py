#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Lightweight data API server for MtAgentLens frontend development.
Reads SQLite responsibility databases from env/runs/ and serves JSON.

Usage: python3 scripts/data-server.py [--port PORT]
"""

import json, sqlite3, re, sys
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

PROJECT = Path(__file__).resolve().parent.parent
RUNS_DIR = PROJECT / "env" / "runs"


def get_runs():
    runs = []
    for d in sorted(RUNS_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        summary_path = d / "summary.json"
        scenario = None
        if summary_path.exists():
            try:
                s = json.loads(summary_path.read_text())
                scenario = s.get("scenario_ids", [None])[0]
            except Exception:
                pass
        db_path = d / "collector" / "collector.responsibility.db"
        node_count = 0
        edge_count = 0
        if db_path.exists():
            try:
                db = sqlite3.connect(str(db_path))
                node_count = db.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
                edge_count = db.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
                db.close()
            except Exception:
                pass
        runs.append({
            "run_id": d.name,
            "scenario": scenario,
            "has_data": db_path.exists(),
            "node_count": node_count,
            "edge_count": edge_count,
        })
    return runs


def get_graph(run_id: str):
    db_path = RUNS_DIR / run_id / "collector" / "collector.responsibility.db"
    if not db_path.exists():
        return None
    db = sqlite3.connect(str(db_path))
    row = db.execute(
        "SELECT nodes_json, edges_json, metrics_json "
        "FROM graph_snapshots ORDER BY timestamp_ns DESC LIMIT 1"
    ).fetchone()
    db.close()
    if not row:
        return None
    return {
        "data": {
            "nodes": json.loads(row[0]),
            "edges": json.loads(row[1]),
        }
    }


def get_alerts(run_id: str, lean: bool = False):
    db_path = RUNS_DIR / run_id / "collector" / "collector.responsibility.db"
    if not db_path.exists():
        return None
    db = sqlite3.connect(str(db_path))

    # The per-alert graph_snapshot duplicates the full graph (1000+ nodes) into
    # every alert, ballooning the payload to tens of MB. The web client strips
    # snapshots anyway, so `?lean=1` skips reading/embedding them entirely.
    snap_nodes, snap_edges, snap_metrics = [], [], {}
    if not lean:
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
        involved = json.loads(r[12]) if r[12] else []

        phase_raw = r[16]
        if phase_raw and phase_raw.startswith("Provisional"):
            m = re.search(r'timestamp_ns:\s*(\d+)', phase_raw)
            phase = {"Provisional": {"timestamp_ns": int(m.group(1)) if m else r[1]}}
        elif phase_raw and phase_raw.startswith("Authoritative"):
            m = re.search(r'timestamp_ns:\s*(\d+)', phase_raw)
            phase = {"Authoritative": {"timestamp_ns": int(m.group(1)) if m else r[1], "overrides": None}}
        else:
            phase = {"Provisional": {"timestamp_ns": r[1]}}

        alert = {
            "alert_id": r[0], "timestamp_ns": r[1],
            "display_profile": r[2], "decision": r[3], "severity": r[4],
            "confidence": r[5], "authority": r[6],
            "reasoning": r[7] or "", "title": r[8] or "", "summary": r[9] or "",
            "action_hint": r[10],
            "primary_agent": {"agent_id": r[11], "claimed_role": None},
            "involved_agents": involved, "engine": engine, "timeline": timeline,
            "phase": phase, "provisional_ref": r[17],
        }
        if not lean:
            alert["graph_snapshot"] = {
                "snapshot_id": "api", "timestamp_ns": r[1],
                "nodes": snap_nodes, "edges": snap_edges, "metrics": snap_metrics,
            }
        alerts.append(alert)

    db.close()
    return {"data": alerts}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        lean = (query.get("lean") or ["0"])[0] in ("1", "true", "yes")
        try:
            if path == "/api/runs":
                self.send_json({"runs": get_runs()})
            elif path.startswith("/api/runs/") and path.endswith("/graph"):
                run_id = path.split("/")[3]
                data = get_graph(run_id)
                if data:
                    self.send_json(data)
                else:
                    self.send_error(404, "Not found")
            elif path.startswith("/api/runs/") and path.endswith("/alerts"):
                run_id = path.split("/")[3]
                data = get_alerts(run_id, lean=lean)
                if data:
                    self.send_json(data)
                else:
                    self.send_error(404, "Not found")
            elif path == "/api/health":
                self.send_json({"status": "ok"})
            else:
                self.send_error(404, "Not found")
        except Exception as e:
            self.send_error(500, str(e))

    def send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[data-server] {args[0]}", flush=True)


def main():
    port = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--port" else 7397
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"MtAgentLens data server on http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
