#!/usr/bin/env python3
"""Export graph snapshot for a single run. Called by API route."""
import json, sqlite3, sys, re
from pathlib import Path

def main():
    run_dir = Path(sys.argv[1])
    db_path = run_dir / "collector" / "collector.responsibility.db"
    if not db_path.exists():
        print(json.dumps({"error": "Database not found"}))
        sys.exit(1)

    db = sqlite3.connect(str(db_path))
    row = db.execute(
        "SELECT nodes_json, edges_json, metrics_json "
        "FROM graph_snapshots ORDER BY timestamp_ns DESC LIMIT 1"
    ).fetchone()
    db.close()

    if not row:
        print(json.dumps({"error": "No snapshot found"}))
        sys.exit(0)

    nodes = json.loads(row[0])
    edges = json.loads(row[1])
    metrics = json.loads(row[2]) if row[2] else {}

    print(json.dumps({
        "data": {
            "nodes": nodes,
            "edges": edges,
            "metrics": metrics,
        }
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
