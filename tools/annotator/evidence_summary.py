# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Human-readable summaries for frozen evidence events.

Annotators should not have to read hashes or raw JSON to know what an event
is. This module decodes the known source formats into a short label, one-line
summary, and a small field list; raw bytes remain available for audit.
"""

from __future__ import annotations

import json

_MAX = 180

# Machine tokens stay visible (audit fidelity); a bracketed Chinese gloss is
# added for annotators. Fields keep the raw value only, so exports and hashes
# remain comparable with the collector records.
# List labels keep the historical vocabulary (stable for reviewers/tests);
# the summary line glosses the machine action separately.
_ACTION_LABELS = {
    "FileOpen": "文件读取", "FileRead": "读取文件", "FileWrite": "写入文件",
    "FileOverwrite": "覆盖文件", "FileDelete": "文件删除", "FileRename": "文件重命名",
    "FileTruncate": "文件截断", "NetworkConnect": "网络连接", "NetworkSend": "发送网络数据",
    "ProcessExec": "进程执行", "ProcessSpawn": "派生进程",
}
_ACTION_GLOSS = {
    "FileOpen": "打开文件描述符", "FileRead": "读取文件", "FileWrite": "写入文件",
    "FileOverwrite": "覆盖文件", "FileDelete": "删除文件", "FileRename": "重命名文件",
    "FileTruncate": "截断文件", "NetworkConnect": "网络连接", "NetworkSend": "发送网络数据",
    "ProcessExec": "执行进程", "ProcessSpawn": "派生进程",
}
_OUTCOMES = {"unknown": "未知", "attempted": "已尝试", "pending": "等待中",
             "succeeded": "成功", "failed": "失败"}
_DECISIONS = {"Safe": "安全", "Unsafe": "不安全", "Uncertain": "不确定"}
_SEVERITIES = {"Info": "信息", "Warning": "警告", "Critical": "严重",
               "Low": "低", "Medium": "中", "High": "高"}
_CLAIM_TYPES = {"PolicyViolation": "策略违规", "ResponsibilityAnomaly": "责任异常",
                "SuspiciousDelegation": "可疑委派", "TaintedContextUse": "污染上下文使用",
                "UnauthorizedCapabilityUse": "未授权能力使用", "BlockedAttempt": "被拦截尝试"}
_CLAIM_STATES = {"Observed": "已观测", "Inferred": "推断", "Ambiguous": "不确定",
                 "Unproven": "未证实", "Confirmed": "已确认", "BlockedAttempt": "被拦截"}
_POLICY_BOUNDARIES = {"Delegation": "委派", "Capability": "能力",
                      "ContextTrust": "上下文信任", "Taint": "污点"}
_JUDGMENT_RESULTS = {"Allowed": "允许", "Violation": "违规", "Ambiguous": "不确定",
                     "Unproven": "未证实", "BlockedAttempt": "被拦截"}
_TABLE_LABELS = {
    "actions": "系统动作", "responsibility_alerts": "责任告警",
    "responsibility_claims": "责任声明", "policy_evaluations": "策略评估",
    "intents": "模型意图", "identity_process_beliefs": "身份进程信念",
    "agent_identities": "Agent 身份", "process_events": "进程事件",
    "tls_sessions": "TLS 会话", "runs": "运行摘要",
}


def _gloss(mapping: dict[str, str], value: object) -> str:
    """Raw token plus Chinese gloss, e.g. ``Unsafe（不安全）``."""
    if value is None:
        return "未知"
    if not isinstance(value, str) or not value:
        return _text(value)
    translated = mapping.get(value)
    return f"{value}（{translated}）" if translated else value


def _text(value: object, limit: int = _MAX) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _args(row: dict) -> dict:
    raw = row.get("normalized_args_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = []
    args = {}
    for item in raw or []:
        if isinstance(item, dict):
            args.update(item)
    return args


def _action_line(row: dict) -> tuple[str, str, list[dict]]:
    action = row.get("action_type") or "Action"
    args = _args(row)
    outcome = row.get("outcome") or "unknown"
    path = args.get("Path") or args.get("path")
    command = args.get("Command") or args.get("command")
    label = _ACTION_LABELS.get(action, "系统动作")
    opening = f"{action}（{_ACTION_GLOSS[action]}）" if action in _ACTION_GLOSS else action
    closing = _gloss(_OUTCOMES, outcome)
    if path:
        summary = f"{opening} · {path} · {closing}"
    elif command:
        summary = f"{opening} · {command} · {closing}"
    else:
        summary = f"{opening} · {closing}"
    fields = [{"key": "动作", "value": action}, {"key": "结果", "value": outcome}]
    for name, value in (("路径", path), ("命令", command),
                        ("syscall", row.get("syscall_name")),
                        ("pid", row.get("pid")), ("时间", row.get("timestamp_ns"))):
        if value is not None:
            fields.append({"key": name, "value": _text(value)})
    return label, summary, fields


def _collector_row(line: dict) -> tuple[str, str, list[dict]]:
    table = line.get("table")
    row = line.get("row") if isinstance(line.get("row"), dict) else {}
    if table == "actions":
        return _action_line(row)
    if table == "responsibility_alerts":
        summary = " · ".join((_gloss(_DECISIONS, row.get("decision")),
                              _gloss(_SEVERITIES, row.get("severity")),
                              _text(row.get("title") or ""))).strip(" ·")
        return "系统告警", _text(summary), [
            {"key": "裁决", "value": _text(row.get("decision"))},
            {"key": "严重度", "value": _text(row.get("severity"))},
            {"key": "摘要", "value": _text(row.get("summary") or row.get("title"))},
        ]
    if table == "responsibility_claims":
        summary = f"{_gloss(_CLAIM_TYPES, row.get('claim_type'))} · {_gloss(_CLAIM_STATES, row.get('claim_state'))}"
        return "系统声明", _text(summary), [
            {"key": "类型", "value": _text(row.get("claim_type"))},
            {"key": "状态", "value": _text(row.get("claim_state"))},
            {"key": "严重度", "value": _text(row.get("severity"))},
        ]
    if table == "policy_evaluations":
        result = _gloss(_JUDGMENT_RESULTS, row.get("result")) if row.get("result") else None
        parts = [_gloss(_POLICY_BOUNDARIES, row.get("boundary")), _text(row.get("confidence"))]
        if result:
            parts.append(result)
        return "策略评估", _text(" · ".join(parts)), [
            {"key": "边界", "value": _text(row.get("boundary"))},
            {"key": "结果", "value": _text(row.get("result"))},
            {"key": "置信度", "value": _text(row.get("confidence"))},
            {"key": "理由", "value": _text(row.get("reason"))},
        ]
    if table == "intents":
        args = row.get("tool_args_json")
        return "模型意图", _text(f"{row.get('tool_name')} · {args}"), [
            {"key": "工具", "value": _text(row.get("tool_name"))},
            {"key": "参数", "value": _text(args)},
            {"key": "来源", "value": _text(row.get("authority_source"))},
        ]
    if table in ("identity_process_beliefs", "agent_identities", "process_events", "tls_sessions"):
        subject = row.get("agent_id") or row.get("comm") or ""
        return "身份/进程证据", _text(
            f"{_TABLE_LABELS.get(table, table)}（{table}） · {subject}"), [
            {"key": "类型", "value": table},
            {"key": "主体", "value": _text(row.get("agent_id") or row.get("comm") or row.get("pid"))},
        ]
    if table == "runs":
        # ``runs`` keeps the exact raw pair: it is the frozen verdict surface.
        return "运行摘要", _text(f"{row.get('final_decision')} · {row.get('final_severity')}"), [
            {"key": "最终裁决", "value": _text(row.get("final_decision"))},
            {"key": "严重度", "value": _text(row.get("final_severity"))},
        ]
    fields = [{"key": "表", "value": _text(table)}]
    for key in list(row)[:8]:
        value = row.get(key)
        if value is not None and not isinstance(value, (dict, list)):
            fields.append({"key": key, "value": _text(value)})
    table_label = _TABLE_LABELS.get(table, "采集记录")
    return table_label, _text(f"{table_label}（{table}） · {line.get('primary_id')}"), fields


def shorten(value: object, limit: int = _MAX) -> str:
    return _text(value, limit)


def summarize_event(raw: bytes, *, source_id: str, derivation: str = "") -> dict:
    """Decode one event line; unknown formats fall back to a bounded preview."""
    try:
        line = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return {"label": "原始记录", "summary": _text(raw.decode("utf-8", "replace")),
                "fields": [{"key": "来源", "value": source_id}]}
    if not isinstance(line, dict):
        return {"label": "原始记录", "summary": _text(line),
                "fields": [{"key": "来源", "value": source_id}]}
    if line.get("table") == "runs" and isinstance(line.get("row"), dict):
        row = line["row"]
        return {"label": "运行摘要",
                "summary": _text(f"{row.get('final_decision')} · {row.get('final_severity')}"),
                "fields": [
                    {"key": "最终裁决", "value": _text(row.get("final_decision"))},
                    {"key": "严重度", "value": _text(row.get("final_severity"))},
                    {"key": "运行", "value": _text(row.get("run_id"))},
                ]}
    if line.get("kind") == "row" and line.get("table"):
        label, summary, fields = _collector_row(line)
    elif line.get("kind") == "task_brief":
        task = line.get("text")
        try:
            inner = json.loads(task) if isinstance(task, str) else {}
        except ValueError:
            inner = {}
        summary = inner.get("task") if isinstance(inner, dict) else task
        label, fields = "任务简报", [
            {"key": "模式", "value": _text(inner.get("mode") if isinstance(inner, dict) else None)},
            {"key": "任务", "value": _text(summary)},
        ]
    elif line.get("kind") == "assistant_message":
        calls = line.get("tool_calls")
        names = []
        if isinstance(calls, list):
            names = [str(call.get("name")) for call in calls if isinstance(call, dict) and call.get("name")]
        summary = line.get("text") or ("工具调用: " + ", ".join(names) if names else "助手消息")
        label, fields = "助手消息", [
            {"key": "文本", "value": _text(line.get("text"))},
            {"key": "工具调用", "value": _text(calls)},
        ]
    elif "event_type" in line:
        event_type = str(line.get("event_type") or "运行事件")
        label = {
            "run_started": "运行开始", "launch_attempt": "启动尝试",
            "process_started": "进程启动", "process_exited": "进程退出",
            "run_finished": "运行结束", "launch_failed": "启动失败",
            "process_timeout": "进程超时",
        }.get(event_type, "运行事件")
        actor = line.get("actor_id") or "top-level"
        summary = f"{label} · {actor}"
        fields = [{"key": "类型", "value": event_type},
                  {"key": "主体", "value": _text(actor)}]
        process = line.get("process_key")
        if isinstance(process, dict) and process.get("pid") is not None:
            fields.append({"key": "PID", "value": str(process.get("pid"))})
        details = line.get("details")
        if isinstance(details, dict):
            for key in list(details)[:6]:
                value = details[key]
                if not isinstance(value, (dict, list)):
                    fields.append({"key": key, "value": _text(value)})
        elif details:
            fields.append({"key": "详情", "value": _text(details)})
    else:
        label = "采集记录"
        summary = f"{derivation or source_id} · {line.get('kind') or line.get('schema') or 'record'}"
        fields = [{"key": "来源", "value": source_id},
                  {"key": "类型", "value": _text(line.get("kind") or line.get("schema"))}]
    return {"label": label, "summary": shorten(summary), "fields": fields}
