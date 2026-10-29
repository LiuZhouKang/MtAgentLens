"""Contract tests for ``app_trace.v1`` export (offline, deterministic).

Synthetic run directories reproduce the real Claude Code transcript shapes seen
under ``env/runs/<run>/.claude_benchmark`` and the collector ``http_parser``
event shape, so the exporter contract is pinned without reading live runs.
"""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path

import pytest

from compare.rjudge.trace_export import (
    APP_TRACE_SCHEMA,
    EXPORTER_VERSION,
    FORBIDDEN_TRACE_FIELDS,
    TraceExportError,
    TruncationPolicy,
    export_run_trace,
    export_traces,
    load_trace,
    sha256_text,
)


SESSION_ID = "4d6d7ed2-aa31-4cdb-9e76-2a63355da46d"
SLUG = "-home-lzk-MtAgentLens-env-runs-20990101-000000-deadbeef"


# ── helpers: synthetic transcript builders ────────────────────────────────

def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _base(kind: str, timestamp: str, *, sidechain: bool = False) -> dict:
    return {"type": kind, "timestamp": timestamp, "sessionId": SESSION_ID, "isSidechain": sidechain}


def user_text(text: str, timestamp: str) -> dict:
    row = _base("user", timestamp)
    row["message"] = {"role": "user", "content": text}
    return row


def user_tool_result(tool_use_id: str, content: str, timestamp: str, *, is_error: bool = False) -> dict:
    row = _base("user", timestamp)
    row["message"] = {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": is_error}
        ],
    }
    # A structural copy the exporter must NOT read (it is not the app-layer view).
    row["toolUseResult"] = {"stdout": content, "stderr": ""}
    return row


def assistant(blocks: list[dict], timestamp: str) -> dict:
    row = _base("assistant", timestamp)
    row["message"] = {"role": "assistant", "content": blocks}
    return row


def thinking(text: str) -> dict:
    return {"type": "thinking", "thinking": text, "signature": "sig"}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def tool_use(tool_use_id: str, name: str, input_: dict) -> dict:
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": input_}


def api_request_shape(system_texts: list[str]) -> dict:
    row = _base("api-request-shape", "2026-10-20T14:01:10.692Z")
    row["shape"] = {"system": [{"type": "text", "text": t} for t in system_texts], "tools": []}
    return row


def api_request(model: str) -> dict:
    row = _base("api-request", "2026-10-20T14:01:10.692Z")
    row["params"] = {"model": model}
    return row


def attachment_session_context() -> dict:
    row = _base("attachment", "2026-10-20T14:01:10.690Z")
    row["attachment"] = {"type": "session_context", "context": {"gitStatus": "M secret-file"}}
    row["rendered"] = "git status leaked"
    row["renderedRole"] = "user"
    return row


def make_run(tmp_path: Path, *, name: str = "20990101-000000-deadbeef") -> Path:
    return tmp_path / name


def transcript_path(run: Path, session_id: str = SESSION_ID) -> Path:
    return run / ".claude_benchmark" / "projects" / SLUG / f"{session_id}.jsonl"


def subagent_path(run: Path, agent_file: str, session_id: str = SESSION_ID) -> Path:
    return (
        run / ".claude_benchmark" / "projects" / SLUG / session_id / "subagents" / agent_file
    )


def standard_run(tmp_path: Path, *, name: str = "20990101-000000-deadbeef") -> Path:
    run = make_run(tmp_path, name=name)
    _write_jsonl(
        transcript_path(run),
        [
            {"type": "atis-latch", "atis": "", "sessionId": SESSION_ID},
            api_request(model="deepseek-v4-flash"),
            attachment_session_context(),
            api_request_shape(["You are a Claude agent, built on Anthropic's Claude Agent SDK."]),
            user_text("Run the project test suite with make test.", "2026-10-20T14:01:10.669Z"),
            assistant([thinking("I should run the tests.")], "2026-10-20T14:01:11.000Z"),
            assistant(
                [text_block("I'll run the test suite."), tool_use("call_1", "Bash", {"command": "make test"})],
                "2026-10-20T14:01:11.100Z",
            ),
            user_tool_result("call_1", "Ran 2 tests in 5.019s\n\nOK", "2026-10-20T14:01:16.000Z"),
            assistant([text_block("All tests passed.")], "2026-10-20T14:01:17.000Z"),
            {"type": "cost-state", "totalCostUSD": 0.05, "sessionId": SESSION_ID},
        ],
    )
    return run


ALLOWED_EVENT_KEYS = {
    "user": {"seq", "agent_id", "kind", "ts", "text"},
    "assistant": {"seq", "agent_id", "kind", "ts", "thinking", "text", "tool_calls"},
    "tool_result": {
        "seq",
        "agent_id",
        "kind",
        "ts",
        "tool_use_id",
        "is_error",
        "content",
        "content_bytes",
        "content_truncated",
        "content_sha256",
    },
}


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys |= set(value)
        for item in value.values():
            keys |= _all_keys(item)
    elif isinstance(value, list):
        for item in value:
            keys |= _all_keys(item)
    return keys


# ── 1. Schema, provenance, and information boundary ───────────────────────

def test_export_trace_has_schema_and_derivation_provenance(tmp_path: Path) -> None:
    run = standard_run(tmp_path)

    trace = export_run_trace(run, created_at="2026-10-24T00:00:00Z")

    assert trace["schema"] == APP_TRACE_SCHEMA == "mtagentlens.app_trace.v1"
    assert trace["exporter_version"] == EXPORTER_VERSION
    assert trace["trace_id"] == run.name
    assert trace["created_at"] == "2026-10-24T00:00:00Z"
    assert trace["derivation"]["primary"] == "claude_session_transcript"

    source = trace["derivation"]["sources"][0]
    assert source["kind"] == "session_transcript"
    assert source["path"].startswith(".claude_benchmark/projects/")
    assert len(source["sha256"]) == 64
    assert source["bytes"] > 0
    assert source["lines"] == 10


def test_export_uses_transcript_system_prompt_as_profile(tmp_path: Path) -> None:
    run = standard_run(tmp_path)

    trace = export_run_trace(run)

    assert trace["profile"] == "You are a Claude agent, built on Anthropic's Claude Agent SDK."
    assert trace["profile_source"] == "transcript_api_request_shape"
    assert trace["model"] == "deepseek-v4-flash"


def test_export_never_contains_ground_truth_os_or_collector_fields(tmp_path: Path) -> None:
    run = standard_run(tmp_path)
    # Evaluation-only artifacts placed next to the transcript must never leak.
    (run / ".scenario_id").write_text("exfil-env-secrets\n", encoding="utf-8")
    (run / "collector").mkdir()
    (run / "collector" / "collector.responsibility.db").write_bytes(b"SQLite format 3\x00")
    (run / "summary.json").write_text(
        json.dumps({"final_decision": "Unsafe", "marker_executed": True}), encoding="utf-8"
    )

    trace = export_run_trace(run)

    keys = _all_keys(trace)
    assert not (keys & FORBIDDEN_TRACE_FIELDS)
    serialized = json.dumps(trace, ensure_ascii=False)
    for forbidden in ("exfil-env-secrets", "final_decision", "Unsafe", "responsibility.db", "marker_executed"):
        assert forbidden not in serialized


def test_export_ignores_attachments_unknown_types_and_model_metadata(tmp_path: Path) -> None:
    run = standard_run(tmp_path)

    trace = export_run_trace(run)

    kinds = [event["kind"] for event in trace["events"]]
    assert kinds == ["user", "assistant", "assistant", "tool_result", "assistant"]
    assert "git status leaked" not in json.dumps(trace, ensure_ascii=False)
    assert "secret-file" not in json.dumps(trace, ensure_ascii=False)
    assert trace["coverage"]["unparsable_lines"] == 0


def test_export_event_key_sets_are_explicit(tmp_path: Path) -> None:
    run = standard_run(tmp_path)

    trace = export_run_trace(run)

    for event in trace["events"]:
        assert set(event) == ALLOWED_EVENT_KEYS[event["kind"]]
    assert [event["seq"] for event in trace["events"]] == [0, 1, 2, 3, 4]


def test_export_preserves_tool_call_identity_and_input(tmp_path: Path) -> None:
    run = standard_run(tmp_path)

    trace = export_run_trace(run)

    assistant_event = trace["events"][2]
    assert assistant_event["text"] == "I'll run the test suite."
    assert len(assistant_event["tool_calls"]) == 1
    call = assistant_event["tool_calls"][0]
    assert call["tool_use_id"] == "call_1"
    assert call["name"] == "Bash"
    assert call["input"] == {"command": "make test"}
    assert call["input_truncated"] is False
    assert call["input_json"] == '{"command":"make test"}'
    assert call["input_sha256"] == sha256_text('{"command":"make test"}')

    result_event = trace["events"][3]
    assert result_event["tool_use_id"] == "call_1"
    assert result_event["is_error"] is False
    assert result_event["content"] == "Ran 2 tests in 5.019s\n\nOK"
    assert result_event["content_truncated"] is False
    assert result_event["content_bytes"] == len("Ran 2 tests in 5.019s\n\nOK".encode("utf-8"))
    assert result_event["content_sha256"] == sha256_text("Ran 2 tests in 5.019s\n\nOK")


def test_export_is_deterministic_for_fixed_timestamp(tmp_path: Path) -> None:
    run = standard_run(tmp_path)

    first = json.dumps(export_run_trace(run, created_at="2026-10-24T00:00:00Z"), sort_keys=True)
    second = json.dumps(export_run_trace(run, created_at="2026-10-24T00:00:00Z"), sort_keys=True)

    assert first == second
    assert export_run_trace(run, created_at="2026-10-24T00:00:00Z")["content_sha256"] == (
        export_run_trace(run, created_at="2026-10-24T00:00:00Z")["content_sha256"]
    )


# ── 2. Truncation is deterministic, reported, and hashed ──────────────────

def test_tool_result_truncation_keeps_head_and_tail_and_counts_it(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    long_output = "HEAD" + ("x" * 200) + "TAIL"
    _write_jsonl(
        transcript_path(run),
        [
            user_text("run it", "2026-10-20T14:01:10.000Z"),
            assistant([tool_use("call_1", "Bash", {"command": "ls"})], "2026-10-20T14:01:11.000Z"),
            user_tool_result("call_1", long_output, "2026-10-20T14:01:12.000Z"),
        ],
    )
    policy = TruncationPolicy(tool_result_head=10, tool_result_tail=5)

    trace = export_run_trace(run, options=policy)

    event = trace["events"][2]
    dropped = len(long_output) - 15
    assert event["content_truncated"] is True
    assert event["content"] == long_output[:10] + f"…[truncated {dropped} chars]…" + long_output[-5:]
    assert event["content_bytes"] == len(long_output.encode("utf-8"))
    assert event["content_sha256"] == sha256_text(long_output)
    assert trace["coverage"]["tool_result_truncated_count"] == 1


def test_tool_input_truncation_keeps_full_original_hash(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    big_input = {"command": "echo " + "y" * 400}
    _write_jsonl(
        transcript_path(run),
        [
            user_text("run it", "2026-10-20T14:01:10.000Z"),
            assistant([tool_use("call_1", "Bash", big_input)], "2026-10-20T14:01:11.000Z"),
        ],
    )
    policy = TruncationPolicy(tool_input_head=50, tool_input_tail=20)

    trace = export_run_trace(run, options=policy)

    call = trace["events"][1]["tool_calls"][0]
    full_json = json.dumps(big_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert call["input"] is None
    assert call["input_truncated"] is True
    assert call["input_sha256"] == sha256_text(full_json)
    assert call["input_json"].startswith(full_json[:50])
    assert call["input_json"].endswith(full_json[-20:])
    assert "…[truncated " in call["input_json"]
    assert trace["coverage"]["tool_input_truncated_count"] == 1


def test_thinking_and_assistant_text_truncation_are_counted(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    _write_jsonl(
        transcript_path(run),
        [
            user_text("go", "2026-10-20T14:01:10.000Z"),
            assistant([thinking("t" * 100), text_block("a" * 100)], "2026-10-20T14:01:11.000Z"),
        ],
    )
    policy = TruncationPolicy(thinking_chars=10, assistant_text_chars=20)

    trace = export_run_trace(run, options=policy)

    event = trace["events"][1]
    assert event["thinking"] == "t" * 10 + "…[truncated 90 chars]…"
    assert event["text"] == "a" * 20 + "…[truncated 80 chars]…"
    assert trace["coverage"]["thinking_truncated_count"] == 1
    assert trace["coverage"]["text_truncated_count"] == 1


def test_user_task_text_is_never_truncated(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    prompt = "TASK " + ("z" * 5000)
    _write_jsonl(transcript_path(run), [user_text(prompt, "2026-10-20T14:01:10.000Z")])

    trace = export_run_trace(run, options=TruncationPolicy(assistant_text_chars=5))

    assert trace["events"][0]["text"] == prompt


def test_truncation_policy_describes_itself_for_reports() -> None:
    described = TruncationPolicy().describe()

    assert described["tool_result_head"] == 3000
    assert described["tool_result_tail"] == 1000
    assert described["tool_input_head"] == 6000
    assert described["tool_input_tail"] == 2000
    assert described["thinking_chars"] == 500
    assert described["assistant_text_chars"] == 2000
    assert described["user_text_chars"] is None
    assert "…[truncated" in described["marker_template"]


# ── 3. Multi-agent (subagent transcripts) ─────────────────────────────────

def test_export_merges_subagent_transcripts_as_separate_agents(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    _write_jsonl(
        transcript_path(run),
        [
            user_text("Coordinate the team.", "2026-10-20T14:01:10.000Z"),
            assistant([text_block("Delegating.")], "2026-10-20T14:01:04.000Z"),
        ],
    )
    _write_jsonl(
        subagent_path(run, "agent-aaa.jsonl"),
        [
            user_text("You are Agent B.", "2026-10-20T14:01:02.000Z"),
            assistant([tool_use("call_b", "Read", {"file_path": "/tmp/plan.md"})], "2026-10-20T14:01:03.000Z"),
        ],
    )
    _write_jsonl(
        subagent_path(run, "agent-bbb.jsonl"),
        [
            user_text("You are Agent C.", "2026-10-20T14:01:05.000Z"),
            assistant([text_block("Reporting.")], "2026-10-20T14:01:06.000Z"),
        ],
    )

    trace = export_run_trace(run)

    assert [agent["agent_id"] for agent in trace["agents"]] == ["agent_0", "agent_1", "agent_2"]
    assert trace["agents"][0]["parent_agent_id"] is None
    assert trace["agents"][1]["parent_agent_id"] == "agent_0"
    assert trace["agents"][2]["parent_agent_id"] == "agent_0"
    assert trace["agents"][1]["source_file"].endswith("subagents/agent-aaa.jsonl")
    assert trace["coverage"]["n_agents"] == 3
    # Global ordering is by timestamp, not by file.
    assert [(event["agent_id"], event["kind"]) for event in trace["events"]] == [
        ("agent_1", "user"),
        ("agent_1", "assistant"),
        ("agent_0", "assistant"),
        ("agent_2", "user"),
        ("agent_2", "assistant"),
        ("agent_0", "user"),
    ]
    timestamps = [event["ts"] for event in trace["events"]]
    assert timestamps == sorted(timestamps)


def test_export_multi_agent_ordering_is_global_and_stable(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    _write_jsonl(
        transcript_path(run),
        [
            user_text("main-1", "2026-10-20T14:01:01.000Z"),
            assistant([text_block("main-2")], "2026-10-20T14:01:04.000Z"),
        ],
    )
    _write_jsonl(
        subagent_path(run, "agent-aaa.jsonl"),
        [
            user_text("sub-1", "2026-10-20T14:01:02.000Z"),
            assistant([text_block("sub-2")], "2026-10-20T14:01:03.000Z"),
        ],
    )

    trace = export_run_trace(run)

    assert [(event["agent_id"], event["text"]) for event in trace["events"]] == [
        ("agent_0", "main-1"),
        ("agent_1", "sub-1"),
        ("agent_1", "sub-2"),
        ("agent_0", "main-2"),
    ]
    assert [event["seq"] for event in trace["events"]] == [0, 1, 2, 3]


# ── 4. Missing transcripts are explicit, never silent ─────────────────────

def test_missing_transcript_marks_trace_incomplete(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    run.mkdir(parents=True)
    (run / ".scenario_id").write_text("shadow-spawning\n", encoding="utf-8")

    trace = export_run_trace(run)

    assert trace["trace_complete"] is False
    assert trace["coverage"]["transcript_complete"] is False
    assert trace["derivation"]["primary"] == "none"
    assert trace["events"] == []
    assert "no_transcript" in trace["coverage"]["missing"]
    assert "shadow-spawning" not in json.dumps(trace)


def test_empty_transcript_marks_trace_incomplete(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    transcript_path(run).parent.mkdir(parents=True)
    transcript_path(run).write_text("", encoding="utf-8")

    trace = export_run_trace(run)

    assert trace["trace_complete"] is False
    assert "no_transcript_events" in trace["coverage"]["missing"]


def test_unparsable_lines_are_counted_not_silently_dropped(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    path = transcript_path(run)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(user_text("go", "2026-10-20T14:01:10.000Z")) + "\n{not json}\n",
        encoding="utf-8",
    )

    trace = export_run_trace(run)

    assert trace["coverage"]["unparsable_lines"] == 1
    assert "unparsable_lines" in trace["coverage"]["missing"]
    assert trace["trace_complete"] is True


def test_missing_tool_result_for_last_turn_is_reported(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    _write_jsonl(
        transcript_path(run),
        [
            user_text("go", "2026-10-20T14:01:10.000Z"),
            assistant([tool_use("call_1", "Bash", {"command": "ls"})], "2026-10-20T14:01:11.000Z"),
        ],
    )

    trace = export_run_trace(run)

    assert trace["trace_complete"] is True
    assert trace["coverage"]["n_tool_use"] == 1
    assert trace["coverage"]["n_tool_result"] == 0
    assert "no_tool_result_for_last_turn" in trace["coverage"]["missing"]


# ── 5. TLS rebuild fallback (S2) ──────────────────────────────────────────

def collector_event(body: dict, *, timestamp_ns: int = 1) -> dict:
    return {
        "comm": "HTTP Client",
        "pid": 1234,
        "source": "http_parser",
        "timestamp": timestamp_ns,
        "timestamp_ns": timestamp_ns,
        "data": {
            "method": "POST",
            "path": "/v1/messages",
            "message_type": "request",
            "body": json.dumps(body, ensure_ascii=False),
        },
    }


def test_export_falls_back_to_collector_tls_rebuild(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    run.mkdir(parents=True)
    shorter = {
        "model": "deepseek-v4-flash",
        "system": [{"type": "text", "text": "You are a Claude agent."}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "first"}]}],
    }
    fuller = {
        "model": "deepseek-v4-flash",
        "system": [
            {"type": "text", "text": "x-anthropic-billing-header: cc_version=1"},
            {"type": "text", "text": "You are a Claude agent."},
        ],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Run make test."}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Running."},
                    {"type": "tool_use", "id": "call_1", "name": "Bash", "input": {"command": "make test"}},
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "OK"}],
            },
        ],
    }
    (run / "collector").mkdir()
    log = run / "collector" / "collector.log"
    log.write_text(
        json.dumps({"source": "ssl", "data": {}}) + "\n"
        + json.dumps(collector_event(shorter), ensure_ascii=False) + "\n"
        + json.dumps(collector_event(fuller), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    trace = export_run_trace(run)

    assert trace["derivation"]["primary"] == "tls_http_parser"
    assert trace["coverage"]["transcript_complete"] is True
    assert trace["trace_complete"] is True
    assert [event["kind"] for event in trace["events"]] == ["user", "assistant", "tool_result"]
    assert trace["events"][0]["text"] == "Run make test."
    assert trace["events"][1]["tool_calls"][0]["name"] == "Bash"
    assert trace["events"][2]["content"] == "OK"
    assert trace["profile_source"] == "collector_http_parser"
    assert "x-anthropic-billing-header" not in trace["profile"]
    assert trace["derivation"]["sources"][0]["kind"] == "tls_http_parser"


def test_transcript_wins_over_tls_and_tls_availability_is_reported(tmp_path: Path) -> None:
    run = standard_run(tmp_path)
    (run / "collector").mkdir()
    (run / "collector" / "collector.log").write_text(
        json.dumps(collector_event({"messages": [], "system": []})) + "\n", encoding="utf-8"
    )

    trace = export_run_trace(run)

    assert trace["derivation"]["primary"] == "claude_session_transcript"
    assert trace["coverage"]["tls_rebuild_available"] is True


# ── 6. Safety: no network, no path escape ─────────────────────────────────

def test_export_never_opens_a_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args, **kwargs):  # pragma: no cover - only on regression
        raise AssertionError("export must not touch the network")

    monkeypatch.setattr(socket, "create_connection", _forbidden)
    monkeypatch.setattr(socket.socket, "connect", _forbidden)

    trace = export_run_trace(standard_run(tmp_path))

    assert trace["events"]


def test_export_rejects_transcript_symlinks_escaping_the_run_dir(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    outside = tmp_path / "outside.jsonl"
    outside.write_text(json.dumps(user_text("leak", "2026-10-20T14:01:10.000Z")) + "\n", encoding="utf-8")
    link = transcript_path(run)
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)

    with pytest.raises(TraceExportError):
        export_run_trace(run)


# ── 7. Batch export + loader ──────────────────────────────────────────────

def test_export_traces_writes_files_and_manifest(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    standard_run(runs_root, name="20990101-000000-aaaaaaaaaaaa")
    broken = runs_root / "20990102-000000-bbbbbbbbbbbb"
    broken.mkdir(parents=True)

    out = tmp_path / "traces"
    manifest = export_traces([runs_root / "20990101-000000-aaaaaaaaaaaa", broken], out)

    assert manifest["schema"] == "mtagentlens.app_trace_manifest.v1"
    assert manifest["n_traces"] == 2
    assert manifest["n_complete"] == 1
    assert manifest["n_incomplete"] == 1
    assert manifest["truncation_policy"]["tool_result_head"] == 3000
    entries = {entry["trace_id"]: entry for entry in manifest["traces"]}
    for entry in entries.values():
        path = out / entry["trace_file"]
        assert path.exists()
        assert entry["trace_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert entries["20990101-000000-aaaaaaaaaaaa"]["trace_complete"] is True
    assert entries["20990102-000000-bbbbbbbbbbbb"]["trace_complete"] is False


def test_load_trace_rejects_unknown_schema(tmp_path: Path) -> None:
    path = tmp_path / "trace.json"
    path.write_text(json.dumps({"schema": "something-else"}), encoding="utf-8")
    with pytest.raises(TraceExportError):
        load_trace(path)

    good = export_run_trace(standard_run(tmp_path / "runs"))
    path.write_text(json.dumps(good), encoding="utf-8")
    loaded = load_trace(path)
    assert loaded["schema"] == APP_TRACE_SCHEMA
    assert loaded["trace_id"] == good["trace_id"]


def test_truncated_tls_body_is_reported_as_unusable_not_silently_ignored(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    (run / "collector").mkdir(parents=True)
    truncated = collector_event({})
    truncated["data"]["body"] = '{"model":"m","messages":[{"role":"user","content":[{"type":"text"'
    (run / "collector" / "collector.log").write_text(
        json.dumps(truncated, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    trace = export_run_trace(run)

    assert trace["trace_complete"] is False
    assert trace["coverage"]["tls_rebuild_available"] is False
    assert trace["coverage"]["tls"]["log_present"] is True
    assert trace["coverage"]["tls"]["http_parser_lines"] == 1
    assert trace["coverage"]["tls"]["unparsable_bodies"] == 1
    assert "tls_rebuild_unusable" in trace["coverage"]["missing"]


def test_collector_log_without_http_parser_events_is_reported(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    (run / "collector").mkdir(parents=True)
    (run / "collector" / "collector.log").write_text(
        json.dumps({"source": "ssl", "data": {}}) + "\n", encoding="utf-8"
    )

    trace = export_run_trace(run)

    assert trace["coverage"]["tls"]["log_present"] is True
    assert trace["coverage"]["tls"]["http_parser_lines"] == 0
    assert "no_http_parser_events" in trace["coverage"]["missing"]


def test_usable_tls_diagnostics_are_exposed(tmp_path: Path) -> None:
    run = make_run(tmp_path)
    (run / "collector").mkdir(parents=True)
    body = {"model": "m", "system": [{"type": "text", "text": "You are an agent."}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "go"}]}]}
    (run / "collector" / "collector.log").write_text(
        json.dumps(collector_event(body), ensure_ascii=False) + "\n", encoding="utf-8"
    )

    trace = export_run_trace(run)

    assert trace["coverage"]["tls"] == {
        "log_present": True,
        "http_parser_lines": 1,
        "usable_requests": 1,
        "unparsable_bodies": 0,
    }
    assert trace["trace_complete"] is True
