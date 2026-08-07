#!/usr/bin/env python3
"""检查 collector.alerts.db 中 responsibility_alerts 表的完整性和正确性."""
import json, sqlite3, sys
from pathlib import Path

REQUIRED_COLUMNS = [
    "alert_id", "agent_id", "timestamp_ns", "display_profile", "decision",
    "severity", "confidence", "authority", "reasoning", "title", "summary",
    "action_hint", "primary_agent_json", "involved_agents_json",
    "engine_section_json", "behavior_timeline_json", "causal_graph_json",
    "observer_output_json"
]

def check_db(run_dir: Path) -> dict:
    db_path = run_dir / "collector.alerts.db"
    if not db_path.exists():
        return {"status": "error", "reason": f"collector.alerts.db not found in {run_dir}"}

    conn = sqlite3.connect(str(db_path))
    tables = [t[0] for t in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

    if "responsibility_alerts" not in tables:
        conn.close()
        return {"status": "error", "reason": "responsibility_alerts table missing"}

    # Check new format: agent_id column (not claim_id from old V24 format)
    cols = [c[1] for c in conn.execute("PRAGMA table_info(responsibility_alerts)").fetchall()]
    if "agent_id" not in cols:
        conn.close()
        return {"status": "error", "reason": "OLD schema: no agent_id column (V29 migration failed?)"}

    # Check all required columns exist
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in cols]
    if missing_cols:
        conn.close()
        return {"status": "error", "reason": f"Missing columns: {missing_cols}"}

    alerts = conn.execute(
        "SELECT alert_id, agent_id, display_profile, decision, severity, "
        "confidence, authority, reasoning, title, summary, action_hint, "
        "primary_agent_json, involved_agents_json, engine_section_json, "
        "behavior_timeline_json, causal_graph_json, observer_output_json "
        "FROM responsibility_alerts ORDER BY timestamp_ns DESC"
    ).fetchall()

    issues = []
    alert_summaries = []
    for a in alerts:
        alert_id = a[0]
        # Check all NOT NULL scalar fields are non-empty
        scalar_fields = [
            (0, "alert_id"), (1, "agent_id"), (2, "display_profile"),
            (3, "decision"), (4, "severity"), (6, "authority"),
            (7, "reasoning"), (8, "title"), (9, "summary"), (10, "action_hint")
        ]
        for idx, name in scalar_fields:
            if not a[idx] or not str(a[idx]).strip():
                issues.append(f"Alert {alert_id}: {name} is empty")

        # Check confidence is valid
        if a[5] is None or not (0.0 <= a[5] <= 1.0):
            issues.append(f"Alert {alert_id}: confidence={a[5]} out of range")

        # Check JSON columns are valid JSON
        json_indices = [11, 12, 13, 14, 15]  # primary_agent through causal_graph
        json_names = ["primary_agent_json", "involved_agents_json",
                      "engine_section_json", "behavior_timeline_json",
                      "causal_graph_json"]
        for idx, name in zip(json_indices, json_names):
            try:
                json.loads(a[idx])
            except Exception:
                issues.append(f"Alert {alert_id}: {name} is invalid JSON")

        alert_summaries.append({
            "alert_id": alert_id,
            "agent_id": a[1],
            "decision": a[3],
            "severity": a[4],
            "authority": a[6],
            "title": a[8],
        })

    # Check for duplicate alert_ids
    alert_ids = [a[0] for a in alerts]
    dups = set(aid for aid in alert_ids if alert_ids.count(aid) > 1)
    if dups:
        issues.append(f"Duplicate alert_ids: {dups}")

    conn.close()

    return {
        "status": "pass" if not issues else "fail",
        "alert_count": len(alerts),
        "issues": issues,
        "alerts": alert_summaries,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: check_alerts_db.py <run_dir>", file=sys.stderr)
        sys.exit(2)
    result = check_db(Path(sys.argv[1]))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
