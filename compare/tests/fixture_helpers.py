"""Explicit synthetic capture records for offline evaluation contracts."""
import json


def capture_checkpoint(conn, run_id, *, lost=0):
    quality = {"status": "healthy", "sources": {"synthetic-process": {
        "final_stats_seen": True, "drain_complete": True, "exit_status": 0,
        "lost": lost, "rate_limited": 0}}, "analysis": {
            "partial_tls_events": 0,
            "raw_log_failures": 0, "persist_dropped": 0, "persist_closed": 0,
            "persist_failures": 0, "persist_flush_failed": False}}
    conn.execute("CREATE TABLE IF NOT EXISTS analysis_checkpoints(run_id TEXT, checkpoint_kind TEXT, version INTEGER, payload_json TEXT)")
    payload = {"run_id": run_id, "contract": "frozen-evidence-reanalysis-v1", "capture_quality": quality}
    conn.execute("INSERT INTO analysis_checkpoints VALUES(?, 'frozen-analysis', 1, ?)", (run_id, json.dumps(payload)))
