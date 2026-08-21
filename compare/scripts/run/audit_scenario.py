#!/usr/bin/env python3
"""每场景跑完后立即执行的深度内容审查。检查数据质量 + 逻辑一致性 + 基线对比。
用法: python3 compare/scripts/run/audit_scenario.py reconnaissance/atlas-recon-discovery
"""
import sqlite3, json, sys, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TRACES = ROOT / "compare" / "Dataset" / "FuxiBench" / "traces"
BASELINE = ROOT / "compare" / "results" / "paper" / "main_evaluation.json"

def audit(db_path: Path, scenario_id: str):
    """深度审查一个场景的数据库"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    baseline = json.loads(BASELINE.read_text())
    old = next((s for s in baseline["per_scenario"] if s["sid"] == scenario_id), None)

    report = {"scenario": scenario_id, "pass": True, "issues": [], "warnings": [], "tables": {}, "metrics": {}}

    # ── 1. 逐表行数 ──
    for t in ["runs","process_events","tls_sessions","message_bindings","intents","actions",
              "correlation_evidence","agent_identities","graph_nodes","graph_edges",
              "responsibility_alerts","responsibility_claims","responsibility_tracebacks",
              "delegation_records","context_pollution_events","policy_evaluations",
              "subagent_boundaries","graph_snapshots","non_link_diagnostics",
              "provisional_identities","identity_process_map"]:
        try:
            report["tables"][t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except:
            report["tables"][t] = -1

    # ── 2. 运行元数据 ──
    r = con.execute("SELECT * FROM runs").fetchone()
    if r["ended_at_ns"] is None:
        report["warnings"].append("runs.ended_at_ns 为空 — shutdown 不完整（数据仍然有效）")
    if r["scenario_id"] is None:
        report["warnings"].append("runs.scenario_id 为空")
    report["metrics"]["duration_s"] = (r["ended_at_ns"] - r["started_at_ns"]) / 1e9 if r["ended_at_ns"] else None

    # ── 3. Agent 身份 ──
    agents = con.execute("SELECT * FROM agent_identities").fetchall()
    report["metrics"]["agents"] = len(agents)
    for a in agents:
        pids = json.loads(a["process_ids_json"]) if a["process_ids_json"] else []
        msgs = json.loads(a["message_ids_json"]) if a["message_ids_json"] else []
        # 检查是否有 claude 进程在列表中
        claude_pids = [p for p in pids if con.execute(
            "SELECT COUNT(*) FROM process_events WHERE pid=? AND start_time_ns=? AND comm LIKE '%claude%'",
            (p["pid"], p["start_time_ns"])
        ).fetchone()[0] > 0]
        if not claude_pids and len(agents) > 1:
            report["warnings"].append(f"Agent {a['agent_id'][:20]}... 无 claude 进程关联（可能是基础设施进程）")

    # ── 4. 工具调用 ──
    intent_tools = [r[0] for r in con.execute("SELECT DISTINCT tool_name FROM intents").fetchall()]
    report["metrics"]["intent_tools"] = intent_tools
    side_effect = report["tables"]["intents"] > 0 and con.execute(
        "SELECT COUNT(*) FROM intents WHERE intent_class='side_effect'"
    ).fetchone()[0] or 0
    report["metrics"]["side_effect_ratio"] = side_effect / max(report["tables"]["intents"], 1)

    # ── 5. 图结构 ──
    node_types = {r[0]: r[1] for r in con.execute(
        "SELECT node_type, COUNT(*) FROM graph_nodes GROUP BY node_type"
    ).fetchall()}
    edge_types = {r[0]: r[1] for r in con.execute(
        "SELECT edge_type, COUNT(*) FROM graph_edges GROUP BY edge_type"
    ).fetchall()}
    report["metrics"]["node_types"] = node_types
    report["metrics"]["edge_types"] = edge_types
    report["metrics"]["pbc"] = edge_types.get("PolicyBoundaryCrossing", 0)

    # ── 6. 告警 ──
    alerts = con.execute("SELECT decision, COUNT(*) FROM responsibility_alerts GROUP BY decision").fetchall()
    report["metrics"]["alerts"] = {r[0]: r[1] for r in alerts}

    # ── 7. Evidence 完整性 ──
    total_ev = report["tables"]["correlation_evidence"]
    valid_ev = con.execute("""
        SELECT COUNT(*) FROM correlation_evidence e
        JOIN intents i ON e.intent_id = i.intent_id
        JOIN actions a ON e.action_id = a.action_id
    """).fetchone()[0]
    if total_ev > 0 and valid_ev != total_ev:
        report["issues"].append(f"Evidence 不完整: {valid_ev}/{total_ev} — {total_ev-valid_ev} 条孤立证据")
    report["metrics"]["evidence_integrity"] = valid_ev / max(total_ev, 1)

    # ── 8. 基线对比 ──
    if old:
        for key, label in [("pbc","PBC"), ("edges","Edges"), ("evidence","Evidence"), ("agents","Agents")]:
            old_val = old.get(key, 0)
            new_val = edge_types.get("PolicyBoundaryCrossing", 0) if key == "pbc" else \
                      report["tables"].get("graph_edges", 0) if key == "edges" else \
                      report["tables"].get("correlation_evidence", 0) if key == "evidence" else \
                      len(agents)
            pct = (new_val - old_val) / old_val * 100 if old_val > 0 else 0
            if abs(pct) > 80 and max(old_val, new_val) > 10:
                report["warnings"].append(f"{label} 偏差大: {old_val}→{new_val} ({pct:+.0f}%)")

    # ── 9. 数据逻辑性 ──
    if report["tables"]["process_events"] == 0:
        report["issues"].append("无进程事件 — collector 未捕获任何进程")
    if report["tables"]["intents"] == 0 and report["tables"]["tls_sessions"] > 0:
        report["warnings"].append("有 TLS 流量但无 intent — Agent 可能被 LLM 拒绝")
    if report["tables"]["tls_sessions"] == 0 and report["tables"]["process_events"] > 0:
        report["warnings"].append("有进程事件但无 TLS — Agent 可能纯本地执行")

    # ── 10. 判定 ──
    report["pass"] = len(report["issues"]) == 0
    con.close()
    return report

def print_report(r):
    """打印人类可读的审查报告"""
    is_attack = r.get("attack_type", "?") not in ("benign",)
    print(f"\n{'='*70}")
    print(f"📊 {r['scenario']}")
    print(f"{'='*70}")

    # 逐表
    for t, c in sorted(r["tables"].items()):
        if c < 0: continue
        icon = "✅" if c > 0 or t in ("delegation_records","context_pollution_events","subagent_boundaries","graph_snapshots","non_link_diagnostics","provisional_identities","identity_process_map") else ("⚠️" if t not in ("responsibility_alerts","responsibility_claims","responsibility_tracebacks","graph_snapshots") else "·")
        print(f"  {t:30s} {c:6d}  {icon}")

    m = r["metrics"]
    print(f"\n  Agent: {m['agents']} | 工具: {m.get('intent_tools',[])}")
    print(f"  图: {m.get('node_types',{})} | 边: {m.get('edge_types',{})}")

    alerts = m.get("alerts", {})
    if alerts:
        print(f"  告警: {alerts}")

    if m.get("duration_s"):
        print(f"  时长: {m['duration_s']:.0f}s")

    if r["warnings"]:
        print(f"\n  ⚠️ 注意:")
        for w in r["warnings"]:
            print(f"     - {w}")
    if r["issues"]:
        print(f"\n  ❌ 问题:")
        for i in r["issues"]:
            print(f"     - {i}")

    print(f"\n  {'✅ 通过' if r['pass'] else '❌ 未通过'}")
    return r["pass"]

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: audit_scenario.py <scenario_id>")
        print("  e.g. audit_scenario.py reconnaissance/atlas-recon-discovery")
        sys.exit(1)

    sid = sys.argv[1]
    db_path = TRACES / sid / "responsibility.db" if "/" in sid else None

    # 尝试在分类目录中查找
    if db_path is None or not db_path.exists():
        for root, dirs, files in os.walk(str(TRACES)):
            if os.path.basename(root) == sid and "responsibility.db" in files:
                db_path = Path(root) / "responsibility.db"
                break

    if db_path is None or not db_path.exists():
        print(f"❌ 找不到数据库: {sid}")
        sys.exit(1)

    r = audit(db_path, sid)
    print_report(r)
    sys.exit(0 if r["pass"] else 1)
