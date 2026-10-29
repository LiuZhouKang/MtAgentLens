#!/usr/bin/env python3
"""Build evidence.json for the 2026-10-25 validation-run diagnosis (read-only).

Reads only:
  evidence/validation/legacy-run3/run3/<scenario>/results.jsonl + attempt run dirs
  ground_truth/...
Writes only:  <this dir>/evidence.json

Run: env/.venv/bin/python evidence/v5/validation/evidence_build.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path("")
HERE = Path(__file__).resolve().parent
RUN3 = ROOT / "evidence/validation/legacy-run3/run3"
SCEN = ("benign-api-doc", "credential-theft-direct", "atlas-recon-discovery")
KEYS = {"benign-api-doc": "benign", "credential-theft-direct": "credential",
        "atlas-recon-discovery": "atlas"}


def load_rows(scenario: str) -> list[dict]:
    path = RUN3 / scenario / "results.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def db(row: dict) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{row['db_path']}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def checkpoint_payload(row: dict) -> dict:
    con = db(row)
    try:
        rows = con.execute(
            "SELECT payload_json FROM analysis_checkpoints WHERE checkpoint_kind='frozen-analysis'"
        ).fetchall()
    finally:
        con.close()
    if len(rows) != 1:
        raise ValueError(f"expected one frozen-analysis checkpoint, got {len(rows)}")
    return json.loads(rows[0]["payload_json"])


def strip_diag(source: dict) -> dict:
    return {k: v for k, v in (source or {}).items() if k != "diagnostics"}


def health(row: dict) -> dict:
    path = Path(row["run_dir"]) / "collector/collector_health.json"
    return json.loads(path.read_text(encoding="utf-8"))


def summary(row: dict) -> dict:
    return json.loads((Path(row["run_dir"]) / "summary.json").read_text(encoding="utf-8"))


def event_timeline(row: dict) -> dict:
    """Per-source peak event rate, from the merged collector.log (timestamps)."""
    path = Path(row["run_dir"]) / "collector/collector.log"
    per = {src: Counter() for src in ("process", "file_access", "ssl")}
    comms = {"process": Counter(), "file_access": Counter()}
    for line in path.open(errors="replace"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        src = event.get("source")
        ts = event.get("timestamp")
        if src not in per or not ts:
            continue
        per[src][ts // 1000] += 1
        if src in comms:
            comm = (event.get("data") or {}).get("comm") or event.get("comm") or "?"
            comms[src][comm] += 1
    out = {}
    for src, counter in per.items():
        peak = counter.most_common(1)[0] if counter else (None, 0)
        out[src] = {"events": sum(counter.values()), "peak_events_per_s": peak[1],
                    "peak_second": peak[0], "seconds_with_events": len(counter)}
    out["top_file_access_comms"] = dict(comms["file_access"].most_common(8))
    out["top_process_comms"] = dict(comms["process"].most_common(8))
    return out


def claims(con: sqlite3.Connection) -> list[dict]:
    out = []
    for row in con.execute("SELECT * FROM responsibility_claims"):
        record = dict(row)
        record["evidence_refs"] = json.loads(record.pop("evidence_refs_json") or "[]")
        out.append(record)
    return out


def alerts(con: sqlite3.Connection) -> list[dict]:
    out = []
    for row in con.execute("SELECT * FROM responsibility_alerts"):
        record = dict(row)
        engine = json.loads(record.pop("engine_section_json") or "{}")
        record.pop("graph_metrics_json", None)
        record.pop("timeline_json", None)
        record.pop("involved_agents_json", None)
        record["engine_section"] = {
            key: engine.get(key) for key in (
                "claim_ids", "rule_verdicts", "classification_route",
                "classification_score", "score_breakdown", "observer_trigger_reason",
                "deberta_score")
        }
        out.append(record)
    return out


def policy_summary(con: sqlite3.Connection) -> dict:
    groups: Counter = Counter()
    for row in con.execute("SELECT boundary, judgment_json FROM policy_evaluations"):
        judgment = json.loads(row["judgment_json"])
        groups[(row["boundary"], judgment.get("result"), judgment.get("boundary_id"))] += 1
    return {
        "rows": sum(groups.values()),
        "groups": [{"boundary_kind": k[0], "result": k[1], "boundary_id": k[2], "count": n}
                   for k, n in groups.items()],
    }


def boundary_edges(con: sqlite3.Connection) -> list[dict]:
    out = []
    for row in con.execute(
        "SELECT edge_id, edge_type, source_node_id, target_node_id, evidence_refs_json, confidence "
        "FROM graph_edges WHERE edge_type IN ('PolicyBoundaryCrossing','AgentDelegatesTo')"
    ):
        record = dict(row)
        record["evidence_refs"] = json.loads(record.pop("evidence_refs_json") or "[]")
        target = record["target_node_id"]
        node = con.execute(
            "SELECT node_type, resource_json FROM graph_nodes WHERE node_id=?", (target,)
        ).fetchone()
        if node:
            record["target_node_type"] = node["node_type"]
            record["target_resource"] = json.loads(node["resource_json"] or "null")
        out.append(record)
    return out


def boundary_actions(con: sqlite3.Connection) -> list[dict]:
    out = []
    for row in con.execute(
        "SELECT action_id, pid, action_type, normalized_args_json, outcome, syscall_ret, timestamp_ns "
        "FROM actions WHERE normalized_args_json LIKE '%index.lock%' ORDER BY timestamp_ns"
    ):
        record = dict(row)
        record["normalized_args"] = json.loads(record.pop("normalized_args_json"))
        out.append(record)
    return out


def git_processes(con: sqlite3.Connection) -> list[dict]:
    return [dict(row) for row in con.execute(
        "SELECT pid, parent_pid, comm, timestamp_ns FROM process_events "
        "WHERE comm IN ('git','ps','dpkg-query','ss','ip','make') ORDER BY timestamp_ns LIMIT 40")]


def counts(con: sqlite3.Connection) -> dict:
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}


def recon_probe(con: sqlite3.Connection) -> dict:
    proc_opens = 0
    paths = Counter()
    for row in con.execute(
        "SELECT normalized_args_json FROM actions WHERE action_type='FileOpen' "
        "AND normalized_args_json LIKE '%/proc/%'"):
        try:
            args = json.loads(row[0])
        except ValueError:
            continue
        for arg in args if isinstance(args, list) else []:
            if isinstance(arg, dict) and "Path" in arg:
                proc_opens += 1
                parts = arg["Path"].split("/")
                paths["/".join(parts[:4])] += 1
    sensitive = {}
    for path in ("/etc/passwd", "config/.env", "/etc/shadow"):
        sensitive[path] = con.execute(
            "SELECT count(*) FROM actions WHERE normalized_args_json LIKE ?",
            (f"%{path}%",)).fetchone()[0]
    network = [dict(r) for r in con.execute(
        "SELECT normalized_args_json, outcome, syscall_ret FROM actions "
        "WHERE action_type='NetworkConnect'")]
    hosts = Counter()
    for item in network:
        args = json.loads(item["normalized_args_json"])
        for arg in args:
            if isinstance(arg, dict) and isinstance(arg.get("Url"), dict):
                hosts[f"{arg['Url'].get('host')}:{arg['Url'].get('port')}"] += 1
    return {"proc_file_opens": proc_opens,
            "proc_path_families": dict(paths.most_common(6)),
            "sensitive_path_actions": sensitive,
            "network_connect_actions": len(network),
            "network_connect_hosts": dict(hosts),
            "sink_host_connect_actions": sum(
                n for host, n in hosts.items() if host.startswith("93.184.216.34"))}


def observer_usage(row: dict) -> list[int]:
    log = Path(row["run_dir"]) / "collector/collector.log"
    out = []
    for line in log.open(errors="replace"):
        if "[observer:usage]" in line:
            token = line.split("input_tokens=")[1].split()[0]
            if token.isdigit():
                out.append(int(token))
    return out


def execution_classifier(row: dict) -> dict:
    sys.path.insert(0, str(ROOT))
    from scripts.evaluation.attack_execution_evidence import analyze, marker_from_summary
    return {"analyze": analyze(Path(row["db_path"])),
            "marker_from_summary": marker_from_summary(Path(row["run_dir"]))}


def witness_v2(row: dict) -> dict:
    sys.path.insert(0, str(ROOT))
    from scripts.evaluation.scenario_execution_witness import witness_rows
    rows = witness_rows([row])
    return rows[0] if rows else {}


def source_mtimes() -> dict:
    files = [
        "collector/src/responsibility/runtime/runtime.rs",
        "collector/src/responsibility/classification/engine.rs",
        "collector/src/responsibility/classification/candidates.rs",
        "collector/src/responsibility/classification/final_decision.rs",
        "collector/src/responsibility/claims/claim_builder.rs",
        "collector/src/responsibility/claims/alert_builder.rs",
        "collector/src/responsibility/claims/text.rs",
        "collector/src/responsibility/replay/checkpoint.rs",
        "collector/src/framework/runners/common.rs",
        "collector/src/responsibility/graph/graph_builder.rs",
        "bpftools/process/process.bpf.c",
        "bpftools/process/process.c",
        "scripts/evaluation/run_artifacts.py",
        "scripts/evaluation/attack_execution_evidence.py",
        "scripts/evaluation/scenario_execution_witness.py",
        "scripts/evaluation/final_verdict_metrics.py",
    ]
    out = {}
    for rel in files:
        path = ROOT / rel
        out[rel] = {"mtime_ns": path.stat().st_mtime_ns,
                    "mtime": path.stat().st_mtime and
                    __import__("datetime").datetime.fromtimestamp(
                        path.stat().st_mtime).isoformat(timespec="seconds")}
    return out


def main() -> int:
    evidence: dict = {
        "schema": "mtagentlens.validation-diagnosis-evidence/1",
        "generated_by": "evidence/v5/validation/evidence_build.py",
        "binary_sha256": "dda3f4b59a8c4cc91dc873bcbbd415e00bb7a945557bb172992b0e4f8afb72d5",
        "release_id": "71e10e45a3c051bfaaa992bc1c0ca0cd84d27daef41e852c86928438840f2e1a",
        "binary_mtime": None,
        "source_mtimes": source_mtimes(),
        "runs": {},
    }
    for scenario in SCEN:
        rows = load_rows(scenario)
        assert len(rows) == 1, f"{scenario}: expected 1 attempt row, got {len(rows)}"
        row = rows[0]
        cp = checkpoint_payload(row)
        quality = cp.get("capture_quality") or {}
        con = db(row)
        try:
            entry = {
                "scenario_id": scenario,
                "key": KEYS[scenario],
                "attempt_id": row["attempt_id"],
                "run_id": row["run_id"],
                "run_dir": row["run_dir"],
                "db_path": row["db_path"],
                "db_sha256": row.get("db_sha256"),
                "duration_seconds": row.get("duration_seconds"),
                "results_fields": {
                    key: row.get(key) for key in (
                        "final_decision", "final_severity", "decision_source", "claims",
                        "claims_by_type", "alerts_total", "alerts_warning_plus",
                        "alerts_by_severity", "alerts_by_decision",
                        "alerts_by_display_profile", "observer_calls", "intents",
                        "actions", "evidence", "capture_quality_status",
                        "evaluation_status", "decision_provenance", "observed")
                },
                "runs_row": dict(con.execute("SELECT * FROM runs").fetchone()),
                "capture_quality": {
                    "status": quality.get("status"),
                    "sources": {k: strip_diag(v) for k, v in (quality.get("sources") or {}).items()},
                    "analysis": quality.get("analysis"),
                },
                "health": {
                    "runtime_metrics": {
                        key: health(row)["runtime_metrics"].get(key) for key in (
                            "ring_buffer_drops", "ring_buffer_drop_events",
                            "capture_loss_free", "capture_loss_reasons",
                            "rate_limit_drops", "aggregation_table_full",
                            "capture_stats_complete", "ssl_events", "http_events",
                            "tls_sessions", "process_events", "graph_nodes",
                            "graph_edges", "pbc_edges", "responsibility_claims",
                            "responsibility_alerts", "policy_evaluations",
                            "observer_calls", "observer_used", "overall_healthy",
                            "startup_capture_verified")
                    },
                    "diagnostics": health(row)["runtime_metrics"].get("diagnostics"),
                },
                "checkpoint": {
                    "contract": cp.get("contract"),
                    "observations": cp.get("observations"),
                    "delegations": cp.get("delegations"),
                    "score_signals": cp.get("score_signals"),
                    "fallback_agent_id": cp.get("fallback_agent_id"),
                },
                "timeline": event_timeline(row),
                "observer_input_tokens": observer_usage(row),
                "claims": claims(con),
                "alerts": alerts(con),
                "policy_evaluations": policy_summary(con),
                "boundary_edges": boundary_edges(con),
                "boundary_actions": boundary_actions(con),
                "process_events_sample": git_processes(con),
                "recon_probe": recon_probe(con),
                "table_counts": counts(con),
                "summary": {
                    key: summary(row).get(key) for key in (
                        "run_marker", "marker_executed", "sink_received",
                        "sink_witness", "sink_capture_error", "success",
                        "agents_succeeded")
                },
                "marker_log_bytes": (Path(row["run_dir"]) / "marker.log").stat().st_size
                if (Path(row["run_dir"]) / "marker.log").is_file() else None,
                "collector_binary": row.get("collector_binary"),
                "derived": {
                    "process_events_delivered": (quality.get("analysis") or {}).get(
                        "event_counts", {}).get("process"),
                    "process_events_lost": next(
                        (src.get("lost") for name, src in (quality.get("sources") or {}).items()
                         if name.startswith("Process:")), None),
                    "file_access_events_delivered": (quality.get("analysis") or {}).get(
                        "event_counts", {}).get("file_access"),
                },
                "git_context_events": [
                    dict(ev) for ev in con.execute(
                        "SELECT pid, parent_pid, comm, timestamp_ns FROM process_events "
                        "WHERE comm='git' ORDER BY timestamp_ns LIMIT 12")
                ],
                "execution_classifier": execution_classifier(row),
                "witness_v2": witness_v2(row),
            }
        finally:
            con.close()
        d = entry["derived"]
        if d["process_events_delivered"] is not None and d["process_events_lost"] is not None:
            total = d["process_events_delivered"] + d["process_events_lost"]
            d["process_events_produced"] = total
            d["process_event_loss_fraction"] = (
                d["process_events_lost"] / total if total else None)
        evidence["runs"][KEYS[scenario]] = entry
    import datetime
    for key, run in evidence["runs"].items():
        binary = Path(run.get("collector_binary") or "")
        if not binary.is_file():
            continue
        evidence["binary_mtime"] = {
            "present": True, "path": str(binary),
            "mtime": datetime.datetime.fromtimestamp(
                binary.stat().st_mtime).isoformat(timespec="seconds"),
            "source_mtime_after_build": sorted(
                rel for rel, meta in evidence["source_mtimes"].items()
                if meta["mtime"] > datetime.datetime.fromtimestamp(
                    binary.stat().st_mtime).isoformat(timespec="seconds")),
        }
        break
    (HERE / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {HERE / 'evidence.json'} ({len(json.dumps(evidence))} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
