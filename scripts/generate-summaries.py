#!/usr/bin/env python3
"""Generate summary.json files for historical runs so the API can serve them."""
import json, sqlite3
from pathlib import Path

RUNS_DIR = Path("/home/lzk/MtAgentLens/env/runs")

for run_dir in sorted(RUNS_DIR.iterdir()):
    if not run_dir.is_dir():
        continue
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        continue  # already generated

    db_path = run_dir / "collector" / "collector.responsibility.db"
    behavior_path = run_dir / "behavior.json"

    run_id = run_dir.name
    scenario_id = None
    if behavior_path.exists():
        try:
            behavior = json.loads(behavior_path.read_text())
            scenario_id = behavior.get("scenario_id")
        except Exception:
            pass

    # Generate minimal summary
    summary = {
        "run_id": run_id,
        "scenario_ids": [scenario_id] if scenario_id else [],
        "state": "completed",
    }

    summary_path.write_text(json.dumps(summary))
    if db_path.exists():
        print(f"  ✓ {run_id} -> {scenario_id}")
    else:
        print(f"  - {run_id} -> {scenario_id} (no db)")

print("Done.")
