#!/usr/bin/env python3
"""Print a concise, recording-friendly MtAgentLens run summary."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "env" / "runs"


def latest_run() -> Path:
    runs = sorted(p for p in RUNS_DIR.iterdir() if p.is_dir())
    if not runs:
        raise SystemExit("No run directories found under env/runs")
    return runs[-1]


def shorten(value: object, width: int = 96) -> str:
    text = "" if value is None else str(value)
    text = text.replace(str(ROOT), "<repo>")
    text = text.replace(str(Path.home()), "~")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= width else text[: width - 1] + "..."


def query(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params))


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def table_count(conn: sqlite3.Connection, name: str) -> int:
    if not table_exists(conn, name):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])


def edge_count(conn: sqlite3.Connection, edge_type: str) -> int:
    if not table_exists(conn, "graph_edges"):
        return 0
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE edge_type=?",
            (edge_type,),
        ).fetchone()[0]
    )


def parse_json(value: object, fallback: object) -> object:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return fallback


def compact_args(value: object, width: int = 110) -> str:
    parsed = parse_json(value, [])
    if not isinstance(parsed, list):
        return shorten(value, width)
    parts: list[str] = []
    for item in parsed[:4]:
        if not isinstance(item, dict):
            parts.append(shorten(item, 30))
            continue
        if "Path" in item:
            parts.append(f"Path={shorten(item['Path'], 42)}")
        elif "Command" in item:
            parts.append(f"Command={shorten(item['Command'], 24)}")
        elif "Content" in item:
            parts.append(f"Content={shorten(item['Content'], 42)}")
        elif "Url" in item and isinstance(item["Url"], dict):
            url = item["Url"]
            parts.append(f"Url={url.get('host', '')}:{url.get('port') or ''}")
        else:
            key, val = next(iter(item.items()))
            parts.append(f"{key}={shorten(val, 36)}")
    if len(parsed) > 4:
        parts.append(f"+{len(parsed) - 4} more")
    return shorten("; ".join(parts), width)


def format_score(value: object) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "n/a"


def print_title(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def print_table(headers: list[str], rows: list[list[object]]) -> None:
    if not rows:
        print("(no rows)")
        return
    widths = [
        max(len(str(headers[i])), *(len(str(row[i])) for row in rows))
        for i in range(len(headers))
    ]
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for row in rows:
        print(fmt.format(*[str(item) for item in row]))


def print_bullets(items: list[str]) -> None:
    if not items:
        print("(no details)")
        return
    for item in items:
        print(f"- {item}")


def health_lines(run: Path) -> list[str]:
    path = run / "collector" / "trace.err.log"
    if not path.exists():
        return []
    patterns = (
        "tls_capture",
        "SSL monitoring enabled",
        "Process monitoring enabled",
        "Observer: LLM provider",
        "DeBERTa classifier",
        "Responsibility runtime enabled",
        "Multi-agent anomaly detection enabled",
        "Handoff injection detection enabled",
        "File access audit analyzer enabled",
        "Security signoff tracking enabled",
        "Responsibility Observer LLM enabled",
        "Observer calls:",
    )
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if any(pattern in line for pattern in patterns):
            lines.append(shorten(line, 140))
    return lines


def runtime_diagnostics(run: Path) -> dict:
    path = run / "collector" / "trace.err.log"
    diagnostics = {
        "observer_calls": None,
        "observer_classify_lines": 0,
        "route": None,
        "final_score": None,
        "observer_forced": 0,
        "observer_failures": 0,
        "observer_failure_reason": "",
        "ringbuf_drops": None,
    }
    if not path.exists():
        return diagnostics
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "[classify] Observer LLM:" in line:
            diagnostics["observer_classify_lines"] += 1
        if "[runtime:trigger] observer forced" in line:
            diagnostics["observer_forced"] += 1
        if "Observer LLM call failed" in line:
            diagnostics["observer_failures"] += 1
            if not diagnostics["observer_failure_reason"]:
                if "HTTP 401" in line or "authentication_error" in line:
                    diagnostics["observer_failure_reason"] = "HTTP 401 authentication failure"
                else:
                    diagnostics["observer_failure_reason"] = shorten(line, 110)
        if "RINGBUF_STATS" in line and "dropped_events" in line:
            match = re.search(r'"dropped_events":(\d+)', line)
            if match:
                diagnostics["ringbuf_drops"] = int(match.group(1))
        match = re.search(
            r"Observer calls:\s*(\d+)\s*\|\s*Route:\s*([^\s|]+)\s*\|\s*Final score:\s*([0-9.]+)",
            line,
        )
        if match:
            diagnostics["observer_calls"] = int(match.group(1))
            diagnostics["route"] = match.group(2)
            diagnostics["final_score"] = float(match.group(3))
    if diagnostics["observer_calls"] is None and diagnostics["observer_classify_lines"]:
        diagnostics["observer_calls"] = diagnostics["observer_classify_lines"]
    return diagnostics


def load_status(run: Path) -> dict:
    path = run / "status" / "agent_0.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Show a clean, recording-friendly MtAgentLens run summary.",
    )
    parser.add_argument("--run", type=Path, help="Run directory. Defaults to latest env/runs/*")
    parser.add_argument("--evidence-limit", type=int, default=8)
    parser.add_argument("--alert-limit", type=int, default=5)
    parser.add_argument("--detail-limit", type=int, default=2)
    parser.add_argument("--compact", action="store_true", help="Skip expanded reasoning sections")
    parser.add_argument("--show-drops", action="store_true", help="Show ring-buffer drop diagnostics")
    args = parser.parse_args()

    run = args.run or latest_run()
    run = run.resolve()
    db = run / "collector" / "trace.responsibility.db"
    if not db.exists():
        raise SystemExit(f"Responsibility database not found: {db}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    scenario = (run / ".scenario_id").read_text(encoding="utf-8").strip() if (run / ".scenario_id").exists() else "unknown"
    status = load_status(run)

    print_title("MtAgentLens 运行概览")
    print(f"Scenario     : {scenario}")
    print(f"Run          : {run.relative_to(ROOT)}")
    print(f"Database     : {db.relative_to(ROOT)}")
    if status:
        duration = int(status.get("end_epoch", 0)) - int(status.get("start_epoch", 0))
        print(f"Agent status : {status.get('state', 'unknown')} | exit={status.get('exit_code', 'n/a')} | duration={duration}s")

    print_title("采集与分析链路")
    lines = health_lines(run)
    diagnostics = runtime_diagnostics(run)
    if lines:
        for line in lines:
            print(line)
    else:
        print("(collector startup lines not found)")

    print_title("组件启用与实际数据")
    component_rows = [
        ["tls_capture 明文采集", "active" if table_count(conn, "tls_sessions") else "none", table_count(conn, "tls_sessions")],
        ["Process Monitor", "active" if table_count(conn, "process_events") else "none", table_count(conn, "process_events")],
        ["Responsibility Runtime", "active" if table_count(conn, "graph_edges") else "none", table_count(conn, "graph_edges")],
        ["Policy Boundary", "active" if edge_count(conn, "PolicyBoundaryCrossing") else "none", edge_count(conn, "PolicyBoundaryCrossing")],
        ["Evidence Correlator", "active" if table_count(conn, "correlation_evidence") else "none", table_count(conn, "correlation_evidence")],
        ["Handoff Injection", "enabled" if "Handoff injection detection enabled" in "\n".join(lines) else "n/a", 1 if "Handoff injection detection enabled" in "\n".join(lines) else 0],
        ["File Access Audit", "enabled" if "File access audit analyzer enabled" in "\n".join(lines) else "n/a", 1 if "File access audit analyzer enabled" in "\n".join(lines) else 0],
        ["Security Signoff", "enabled" if "Security signoff tracking enabled" in "\n".join(lines) else "n/a", 1 if "Security signoff tracking enabled" in "\n".join(lines) else 0],
        ["Observer LLM", "used" if diagnostics["observer_calls"] else "enabled", diagnostics["observer_calls"] or 0],
    ]
    print_table(["component", "state", "records"], component_rows)

    print_title("Observer 仲裁状态")
    observer_rows = [[
        diagnostics["observer_calls"] if diagnostics["observer_calls"] is not None else "n/a",
        diagnostics["observer_forced"],
        diagnostics["observer_failures"],
        diagnostics["route"] or "n/a",
        diagnostics["final_score"] if diagnostics["final_score"] is not None else "n/a",
    ]]
    print_table(["calls", "forced", "failures", "runtime_route", "final_score"], observer_rows)
    if diagnostics["observer_failures"]:
        print()
        print_bullets([
            f"Observer 调用失败原因: {diagnostics['observer_failure_reason'] or 'unknown'}",
            "含义: 本次 run 曾进入语义仲裁路径，但 LLM Provider 未完成有效响应，系统转入 fallback/fail-closed 路线。",
        ])
    elif diagnostics["observer_calls"]:
        print()
        print_bullets(["Observer 已被触发并完成仲裁。"])
    else:
        print()
        print_bullets(["本场景由本地规则、图模式和评分链条直接完成判定，没有消耗 Observer 调用。"])
    if args.show_drops and diagnostics["ringbuf_drops"]:
        print_bullets([f"采集端出现 ring buffer drop: {diagnostics['ringbuf_drops']} events，录制前建议重跑以获得更干净的素材。"])

    print_title("责任图规模")
    rows = query(
        conn,
        """
        SELECT node_type AS kind, count(*) AS count
        FROM graph_nodes
        GROUP BY node_type
        ORDER BY count DESC
        """,
    )
    print_table(["node_type", "count"], [[r["kind"], r["count"]] for r in rows])
    rows = query(
        conn,
        """
        SELECT edge_type AS kind, count(*) AS count
        FROM graph_edges
        GROUP BY edge_type
        ORDER BY count DESC
        """,
    )
    print()
    print_table(["edge_type", "count"], [[r["kind"], r["count"]] for r in rows])

    print_title("上层意图与 OS 行为")
    rows = query(
        conn,
        """
        SELECT intent_class, tool_name, count(*) AS count
        FROM intents
        GROUP BY intent_class, tool_name
        ORDER BY count DESC
        LIMIT 10
        """,
    )
    print_table(
        ["intent_class", "tool", "count"],
        [[r["intent_class"], r["tool_name"], r["count"]] for r in rows],
    )
    rows = query(
        conn,
        """
        SELECT action_type, signal_class, count(*) AS count
        FROM actions
        GROUP BY action_type, signal_class
        ORDER BY count DESC
        LIMIT 10
        """,
    )
    print()
    print_table(
        ["action_type", "signal", "count"],
        [[r["action_type"], r["signal_class"], r["count"]] for r in rows],
    )

    print_title("跨层证据关联")
    rows = query(
        conn,
        """
        SELECT match_level,
               count(*) AS count,
               round(avg(confidence), 3) AS avg_confidence
        FROM correlation_evidence
        GROUP BY match_level
        ORDER BY count DESC
        """,
    )
    print_table(
        ["match_level", "count", "avg_conf"],
        [[r["match_level"], r["count"], r["avg_confidence"]] for r in rows],
    )
    rows = query(
        conn,
        """
        SELECT match_level,
               round(confidence, 3) AS confidence,
               substr(intent_id, 1, 8) AS intent,
               substr(action_id, 1, 8) AS action
        FROM correlation_evidence
        ORDER BY confidence DESC
        LIMIT ?
        """,
        (args.evidence_limit,),
    )
    print()
    print_table(
        ["level", "conf", "intent", "action"],
        [[r["match_level"], r["confidence"], r["intent"], r["action"]] for r in rows],
    )

    print_title("策略判断与告警结论")
    rows = query(
        conn,
        """
        SELECT claim_type,
               claim_state,
               severity,
               round(confidence, 3) AS confidence,
               substr(subject_agent_id, 1, 12) AS agent
        FROM responsibility_claims
        ORDER BY confidence DESC
        """,
    )
    print_table(
        ["claim", "state", "severity", "conf", "agent"],
        [[r["claim_type"], r["claim_state"], r["severity"], r["confidence"], r["agent"]] for r in rows],
    )
    rows = query(
        conn,
        """
        SELECT decision,
               severity,
               round(confidence, 3) AS confidence,
               substr(primary_agent_id, 1, 12) AS agent,
               title,
               summary
        FROM responsibility_alerts
        ORDER BY confidence DESC
        LIMIT ?
        """,
        (args.alert_limit,),
    )
    print()
    alert_rows = []
    for r in rows:
        alert_rows.append([
            r["decision"],
            r["severity"],
            r["confidence"],
            r["agent"],
            shorten(r["title"], 58),
            shorten(r["summary"], 76),
        ])
    print_table(["decision", "severity", "conf", "agent", "title", "summary"], alert_rows)

    if not args.compact:
        print_title("关键判断理由")
        rows = query(
            conn,
            """
            SELECT decision,
                   severity,
                   round(confidence, 3) AS confidence,
                   substr(primary_agent_id, 1, 12) AS agent,
                   reasoning,
                   title,
                   summary,
                   action_hint,
                   engine_section_json
            FROM responsibility_alerts
            ORDER BY confidence DESC
            LIMIT ?
            """,
            (args.detail_limit,),
        )
        if not rows:
            print("(no alerts)")
        for idx, r in enumerate(rows, 1):
            engine = parse_json(r["engine_section_json"], {})
            breakdown = engine.get("score_breakdown", {}) if isinstance(engine, dict) else {}
            route = engine.get("classification_route", "n/a") if isinstance(engine, dict) else "n/a"
            observer_text = engine.get("observer_trigger_reason", "") if isinstance(engine, dict) else ""
            if not observer_text:
                if diagnostics["observer_failures"]:
                    observer_text = "流式阶段曾尝试 Observer，但调用失败，系统使用 fallback/fail-closed 结果"
                elif diagnostics["observer_calls"]:
                    observer_text = "流式阶段曾触发 Observer；当前 alert 来自后续落库结果"
                else:
                    observer_text = "本地证据充分，未触发语义仲裁"
            print(f"[Alert {idx}] {r['decision']} / {r['severity']} / confidence={r['confidence']} / agent={r['agent']}")
            print_bullets([
                f"标题: {shorten(r['title'], 120)}",
                f"摘要: {shorten(r['summary'], 180)}",
                f"判断理由: {shorten(r['reasoning'], 160)}",
                f"处置提示: {shorten(r['action_hint'], 160) if r['action_hint'] else '无需额外处置'}",
                f"分类路由: {route}",
                "分数分解: "
                + ", ".join(
                    f"{key}={format_score(value)}"
                    for key, value in breakdown.items()
                    if key in ("boundary_score", "evidence_score", "role_score", "repetition_score", "behavioral_score", "total")
                ),
                f"DeBERTa score: {format_score(engine.get('deberta_score') if isinstance(engine, dict) else None)}",
                f"Observer trigger: {shorten(observer_text, 140)}",
            ])
            print()

        print_title("运行一致性检查")
        checks: list[str] = []
        rows = query(
            conn,
            """
            SELECT decision,
                   severity,
                   json_extract(engine_section_json, '$.classification_route') AS route
            FROM responsibility_alerts
            """,
        )
        for r in rows:
            route = r["route"] or ""
            if route.endswith("unsafe") and r["decision"] == "Safe":
                checks.append(
                    f"告警展示不一致: route={route}, decision={r['decision']}, severity={r['severity']}"
                )
        if diagnostics["observer_failures"]:
            checks.append(
                f"Observer Provider 调用失败 {diagnostics['observer_failures']} 次，原因: {diagnostics['observer_failure_reason'] or 'unknown'}"
            )
        if args.show_drops and diagnostics["ringbuf_drops"]:
            checks.append(f"采集端 dropped_events={diagnostics['ringbuf_drops']}")
        if not checks:
            checks.append("未发现影响演示解读的一致性问题。")
        print_bullets(checks)

        print_title("责任回溯链")
        rows = query(
            conn,
            """
            SELECT round(confidence, 3) AS confidence,
                   explanation_summary
            FROM responsibility_tracebacks
            ORDER BY confidence DESC
            LIMIT ?
            """,
            (args.detail_limit,),
        )
        if not rows:
            print("(no tracebacks)")
        for idx, r in enumerate(rows, 1):
            print(f"[Traceback {idx}] confidence={r['confidence']}")
            for line in str(r["explanation_summary"]).splitlines()[:8]:
                print(f"  {shorten(line, 120)}")
            print()

        print_title("可读证据样例")
        rows = query(
            conn,
            """
            SELECT ce.match_level,
                   round(ce.confidence, 3) AS confidence,
                   i.tool_name,
                   i.intent_class,
                   i.tool_args_json,
                   a.action_type,
                   a.signal_class,
                   a.normalized_args_json
            FROM correlation_evidence ce
            JOIN intents i ON ce.intent_id = i.intent_id
            JOIN actions a ON ce.action_id = a.action_id
            ORDER BY ce.confidence DESC
            LIMIT ?
            """,
            (args.detail_limit,),
        )
        if not rows:
            print("(no evidence)")
        for idx, r in enumerate(rows, 1):
            print(f"[Evidence {idx}] {r['match_level']} | confidence={r['confidence']}")
            print_bullets([
                f"上层意图: {r['intent_class']} / tool={r['tool_name']} / {compact_args(r['tool_args_json'])}",
                f"OS 行为: {r['signal_class']} / action={r['action_type']} / {compact_args(r['normalized_args_json'])}",
                "含义: 上层工具调用与内核侧动作被同一条证据记录关联，可用于责任图建边和后续归因。",
            ])
            print()

        print_title("策略边界样例")
        rows = query(
            conn,
            """
            SELECT boundary,
                   substr(subject_agent_id, 1, 12) AS agent,
                   round(confidence, 3) AS confidence,
                   reason,
                   judgment_json
            FROM policy_evaluations
            ORDER BY confidence DESC
            LIMIT ?
            """,
            (args.detail_limit,),
        )
        if not rows:
            print("(no policy evaluations)")
        for idx, r in enumerate(rows, 1):
            judgment = parse_json(r["judgment_json"], {})
            print(f"[Policy {idx}] boundary={r['boundary']} | confidence={r['confidence']} | agent={r['agent']}")
            print_bullets([
                f"系统原因: {shorten(r['reason'], 120)}",
                f"判定结果: {judgment.get('result', 'n/a') if isinstance(judgment, dict) else 'n/a'}",
                f"判定理由: {shorten(judgment.get('reason', '') if isinstance(judgment, dict) else '', 160)}",
            ])
            print()

    print_title("本段展示重点")
    print("真实多 Agent 执行被系统级采集链路捕获。")
    print("MtAgentLens 将 Agent 意图、OS 行为、策略边界、证据、责任声明和告警结论落入同一条链路。")
    if diagnostics["observer_failures"]:
        print("本次 run 曾触发 Observer 仲裁，但 Provider 认证失败；正式录制建议修复配置后重跑，或使用 Observer 未触发且语义一致的 run。")
    elif diagnostics["observer_calls"]:
        print("Observer LLM 已参与仲裁，结果与本地责任图一起进入最终判断。")
    else:
        print("Observer LLM 已启用；本场景证据链充分，因此由本地责任图直接完成判定。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
