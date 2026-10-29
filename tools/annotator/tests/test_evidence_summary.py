# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Evidence summaries make events human-readable without hiding raw bytes."""

import json

from tools.annotator.evidence_summary import summarize_event


def line(value):
    return json.dumps(value, ensure_ascii=False).encode()


def test_action_is_decoded_to_path_outcome_and_fields():
    summary = summarize_event(line({"kind": "row", "table": "actions", "primary_id": "a1",
        "row": {"action_type": "FileOpen", "outcome": "succeeded", "pid": 42,
                "normalized_args_json": '[{"Path":"/etc/passwd"}]', "syscall_name": "openat"}}),
        source_id="collector")
    assert summary["label"] == "文件读取"
    assert "/etc/passwd" in summary["summary"] and "succeeded" in summary["summary"]
    keys = {field["key"]: field["value"] for field in summary["fields"]}
    assert keys["路径"] == "/etc/passwd" and keys["结果"] == "succeeded"


def test_alerts_intents_briefs_and_trajectory_are_labelled():
    alert = summarize_event(line({"kind": "row", "table": "responsibility_alerts",
        "row": {"decision": "Unsafe", "severity": "Critical", "title": "shadow read"}}),
        source_id="collector")
    assert alert["label"] == "系统告警" and "Unsafe" in alert["summary"]
    intent = summarize_event(line({"kind": "row", "table": "intents",
        "row": {"tool_name": "Read", "tool_args_json": '{"file_path":"/etc/shadow"}'}}),
        source_id="collector")
    assert intent["label"] == "模型意图" and "Read" in intent["summary"]
    brief = summarize_event(line({"kind": "task_brief", "agent_index": 0,
        "text": '{"mode":"single_agent","task":"Read the docs"}'}), source_id="task_brief")
    assert brief["label"] == "任务简报" and "Read the docs" in brief["summary"]
    trajectory = summarize_event(line({"kind": "assistant_message", "text": "I will read.",
        "tool_calls": [{"name": "Read", "input": {"file_path": "/tmp/a"}}]}), source_id="trajectory")
    assert trajectory["label"] == "助手消息" and "I will read." in trajectory["summary"]


def test_unknown_and_malformed_events_fall_back_safely():
    assert summarize_event(b"not json", source_id="runner")["label"] == "原始记录"
    value = summarize_event(line({"event_type": "run_started", "actor_id": None, "details": {}}),
                            source_id="runner")
    assert value["label"] == "运行开始"
    assert any(field["value"] == "run_started" for field in value["fields"])


def test_run_summary_is_a_dictionary_summary():
    value = summarize_event(line({"kind": "run_summary", "table": "runs",
        "row": {"final_decision": "Uncertain", "final_severity": "Warning",
                "run_id": "run-1"}}), source_id="collector")
    assert value["label"] == "运行摘要"
    assert value["summary"] == "Uncertain · Warning"
    assert isinstance(value["fields"], list)
