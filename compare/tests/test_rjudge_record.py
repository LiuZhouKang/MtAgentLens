"""Contract tests for ``app_trace.v1`` -> R-Judge record conversion."""

from __future__ import annotations

import json

import pytest

from compare.rjudge.protocol import PROFILES, build_judge_messages, validate_record_shape
from compare.rjudge.record import (
    RECORD_SCHEMA,
    MissingTraceError,
    RJudgeRecord,
    record_sha256,
    trace_to_record,
)
from compare.rjudge.trace_export import APP_TRACE_SCHEMA


def make_trace(*, events: list[dict], agents: list[dict] | None = None, profile: str | None = "You are a coding agent.") -> dict:
    return {
        "schema": APP_TRACE_SCHEMA,
        "exporter_version": "rjudge-app-trace/1",
        "trace_id": "20990101-000000-deadbeef",
        "created_at": "2026-10-24T00:00:00Z",
        "derivation": {"primary": "claude_session_transcript", "sources": []},
        "profile": profile,
        "profile_source": "transcript_api_request_shape" if profile else None,
        "model": "deepseek-v4-flash",
        "agents": agents if agents is not None else [{"agent_id": "agent_0"}],
        "events": events,
        "coverage": {
            "n_events": len(events),
            "n_agents": len(agents if agents is not None else [1]),
            "n_tool_use": 0,
            "n_tool_result": 0,
            "tool_result_truncated_count": 0,
            "tool_input_truncated_count": 0,
            "thinking_truncated_count": 0,
            "text_truncated_count": 0,
            "unparsable_lines": 0,
            "missing": [],
            "transcript_complete": True,
            "tls_rebuild_available": False,
        },
        "content_sha256": "0" * 64,
        "trace_complete": True,
    }


def user_event(seq: int, text: str, agent_id: str = "agent_0") -> dict:
    return {"seq": seq, "agent_id": agent_id, "kind": "user", "ts": "2026-10-20T14:01:10.000Z", "text": text}


def assistant_event(
    seq: int,
    *,
    thinking: str | None = None,
    text: str | None = None,
    tool_calls: list[dict] | None = None,
    agent_id: str = "agent_0",
) -> dict:
    return {
        "seq": seq,
        "agent_id": agent_id,
        "kind": "assistant",
        "ts": "2026-10-20T14:01:11.000Z",
        "thinking": thinking,
        "text": text,
        "tool_calls": tool_calls or [],
    }


def tool_call(tool_use_id: str, name: str, input_: dict, *, truncated: bool = False) -> dict:
    full = json.dumps(input_, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if truncated:
        head, tail = full[:40], full[-20:]
        dropped = len(full) - len(head) - len(tail)
        rendered = head + f"…[truncated {dropped} chars]…" + tail
    else:
        rendered = full
    return {
        "tool_use_id": tool_use_id,
        "name": name,
        "input": None if truncated else input_,
        "input_json": rendered,
        "input_sha256": "a" * 64,
        "input_truncated": truncated,
    }


def tool_result_event(seq: int, tool_use_id: str, content: str, *, is_error: bool = False, agent_id: str = "agent_0") -> dict:
    return {
        "seq": seq,
        "agent_id": agent_id,
        "kind": "tool_result",
        "ts": "2026-10-20T14:01:12.000Z",
        "tool_use_id": tool_use_id,
        "is_error": is_error,
        "content": content,
        "content_bytes": len(content.encode()),
        "content_truncated": False,
        "content_sha256": "b" * 64,
    }


BASIC_TRACE = make_trace(
    events=[
        user_event(0, "Run the project test suite with make test."),
        assistant_event(1, thinking="I should run the tests."),
        assistant_event(
            2,
            text="I'll run the test suite.",
            tool_calls=[tool_call("call_1", "Bash", {"command": "make test"})],
        ),
        tool_result_event(3, "call_1", "Ran 2 tests in 5.019s\n\nOK"),
        assistant_event(4, text="All tests passed."),
    ]
)


# ── 1. Round / turn structure ─────────────────────────────────────────────

def test_trace_to_record_builds_rjudge_rounds() -> None:
    record = trace_to_record(BASIC_TRACE)

    assert record.contents == [
        [
            {"role": "user", "content": "Run the project test suite with make test."},
            {
                "role": "agent",
                "thought": "I should run the tests.",
                "action": 'I\'ll run the test suite.\nBash: {"command":"make test"}',
            },
            {"role": "environment", "content": "Ran 2 tests in 5.019s\n\nOK"},
            {"role": "agent", "thought": None, "action": "All tests passed."},
        ]
    ]
    assert record.profile == "You are a coding agent."
    assert record.trace_id == "20990101-000000-deadbeef"
    assert record.arm == "A2_full_trace"


def test_consecutive_assistant_events_merge_into_one_agent_turn() -> None:
    trace = make_trace(
        events=[
            user_event(0, "go"),
            assistant_event(1, thinking="think"),
            assistant_event(2, text="speak", tool_calls=[tool_call("c1", "Read", {"file_path": "/a"})]),
        ]
    )

    record = trace_to_record(trace)

    turns = record.contents[0]
    assert len([turn for turn in turns if turn["role"] == "agent"]) == 1
    assert turns[1]["thought"] == "think"
    assert turns[1]["action"] == 'speak\nRead: {"file_path":"/a"}'


def test_assistant_turn_after_environment_is_a_new_agent_turn() -> None:
    record = trace_to_record(BASIC_TRACE)

    roles = [turn["role"] for turn in record.contents[0]]
    assert roles == ["user", "agent", "environment", "agent"]


def test_multiple_user_turns_become_multiple_rounds() -> None:
    trace = make_trace(
        events=[
            user_event(0, "first task"),
            assistant_event(1, text="done"),
            user_event(2, "second task"),
            assistant_event(3, text="done too"),
        ]
    )

    record = trace_to_record(trace)

    assert [round_[0]["content"] for round_ in record.contents] == ["first task", "second task"]
    assert len(record.contents) == 2


def test_tool_error_is_preserved_as_environment_content() -> None:
    trace = make_trace(
        events=[
            user_event(0, "go"),
            assistant_event(1, tool_calls=[tool_call("c1", "Bash", {"command": "false"})]),
            tool_result_event(2, "c1", "exit code 1", is_error=True),
        ]
    )

    record = trace_to_record(trace)

    assert record.contents[0][2] == {"role": "environment", "content": "exit code 1"}


def test_truncated_tool_input_is_rendered_with_its_marker() -> None:
    trace = make_trace(
        events=[
            user_event(0, "go"),
            assistant_event(
                1,
                tool_calls=[tool_call("c1", "Bash", {"command": "x" * 5000}, truncated=True)],
            ),
        ]
    )

    record = trace_to_record(trace)

    action = record.contents[0][1]["action"]
    assert action.startswith("Bash: {")
    assert '…[truncated' in action


# ── 2. Ablation variants (A1 / A2 / A3) ───────────────────────────────────

def test_prompt_only_variant_keeps_only_user_turns() -> None:
    record = trace_to_record(BASIC_TRACE, include_agent_turns=False, include_environment=False)

    assert record.arm == "A1_prompt_only"
    assert record.contents == [[{"role": "user", "content": "Run the project test suite with make test."}]]


def test_prompt_only_excludes_later_user_turns_and_subagent_instructions() -> None:
    trace = make_trace(agents=[{"agent_id":"root","parent_agent_id":None},
                               {"agent_id":"child","parent_agent_id":"root"}], events=[
        user_event(0,"delegated secret discovered after execution",agent_id="child"),
        user_event(1,"original authorized task",agent_id="root"),
        assistant_event(2,text="tool output seen",agent_id="root"),
        user_event(3,"follow-up includes runtime information",agent_id="root"),
    ])
    record=trace_to_record(trace,include_agent_turns=False,include_environment=False)
    assert record.contents==[[{"role":"user","content":"original authorized task"}]]
    assert "initial_root_user_turns_only" in record.notes


def test_prompt_only_retains_initial_tasks_of_separate_root_invocations() -> None:
    trace=make_trace(agents=[{"agent_id":"a"},{"agent_id":"b"}],events=[
        user_event(0,"task a",agent_id="a"),user_event(1,"task b",agent_id="b"),
        user_event(2,"later a",agent_id="a")])
    record=trace_to_record(trace,include_agent_turns=False,include_environment=False)
    assert record.contents==[[{"role":"user","content":"task a"}],
                             [{"role":"user","content":"task b"}]]


def test_no_tool_result_variant_drops_environment_turns() -> None:
    record = trace_to_record(BASIC_TRACE, include_agent_turns=True, include_environment=False)

    assert record.arm == "A3_no_tool_result"
    assert [turn["role"] for turn in record.contents[0]] == ["user", "agent", "agent"]


def test_dropping_agent_turns_while_keeping_environment_is_rejected() -> None:
    with pytest.raises(ValueError):
        trace_to_record(BASIC_TRACE, include_agent_turns=False, include_environment=True)


# ── 3. Multi-agent attribution ────────────────────────────────────────────

TWO_AGENT_TRACE = make_trace(
    events=[
        user_event(0, "Coordinate the team."),
        assistant_event(1, tool_calls=[tool_call("c1", "Bash", {"command": "id"})], agent_id="agent_1"),
        tool_result_event(2, "c1", "uid=1000", agent_id="agent_1"),
    ],
    agents=[{"agent_id": "agent_0"}, {"agent_id": "agent_1"}],
)


def test_multi_agent_trace_prefixes_agent_id_for_attribution() -> None:
    record = trace_to_record(TWO_AGENT_TRACE)

    assert record.variants["agent_attribution"] is True
    turn = record.contents[0][1]
    assert turn["role"] == "agent"
    assert turn["action"] == '[agent_1] Bash: {"command":"id"}'
    assert record.contents[0][2] == {"role": "environment", "content": "[agent_1] uid=1000"}


def test_single_agent_trace_has_no_attribution_prefix() -> None:
    record = trace_to_record(BASIC_TRACE)

    assert record.variants["agent_attribution"] is False
    assert record.contents[0][1]["action"].endswith('Bash: {"command":"make test"}')
    assert not record.contents[0][1]["action"].startswith("[")


def test_agent_attribution_can_be_forced_off() -> None:
    record = trace_to_record(TWO_AGENT_TRACE, agent_attribution=False)

    assert record.contents[0][1]["action"] == 'Bash: {"command":"id"}'


# ── 4. Profile handling ───────────────────────────────────────────────────

def test_profile_fallback_is_used_and_reported_when_trace_has_none() -> None:
    trace = make_trace(events=[user_event(0, "go")], profile=None)

    record = trace_to_record(trace)

    assert record.profile == PROFILES["fallback"]
    assert record.profile_source == "fallback"
    assert "profile_fallback" in record.notes


def test_profile_override_is_recorded() -> None:
    record = trace_to_record(BASIC_TRACE, profile="You are a mobile phone Agent.")

    assert record.profile == "You are a mobile phone Agent."
    assert record.profile_source == "override"


def test_profile_is_never_invented_from_the_task_text() -> None:
    trace = make_trace(events=[user_event(0, "my secret instructions")], profile=None)

    assert trace_to_record(trace).profile == PROFILES["fallback"]


# ── 5. Safety: no labels, no GT, protocol conformance ─────────────────────

def test_record_contains_no_evaluation_fields() -> None:
    payload = trace_to_record(BASIC_TRACE).judge_payload()

    assert set(payload) == {"profile", "contents"}
    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in ("label", "ground_truth", "final_decision", "scenario_id", "attack_type"):
        assert forbidden not in serialized


def test_record_is_protocol_conformant() -> None:
    record = trace_to_record(BASIC_TRACE)

    validate_record_shape(record.judge_payload())
    messages = build_judge_messages(record.judge_payload())
    assert messages[0] == {"role": "system", "content": "You are a coding agent."}
    assert any("make test" in message["content"] for message in messages)


def test_record_serialization_roundtrip_and_hash_are_deterministic() -> None:
    first = trace_to_record(BASIC_TRACE)
    second = trace_to_record(BASIC_TRACE)

    assert record_sha256(first) == record_sha256(second)
    assert first.to_json()["record_sha256"] == record_sha256(first)

    document = first.to_json()
    assert document["schema"] == RECORD_SCHEMA
    assert document["record"] == first.judge_payload()
    assert document["trace_id"] == "20990101-000000-deadbeef"
    assert document["arm"] == "A2_full_trace"
    assert document["trace_coverage"]["n_events"] == 5
    assert json.loads(json.dumps(document)) == document


def test_record_hash_changes_with_content_and_variant() -> None:
    base = trace_to_record(BASIC_TRACE)
    prompt_only = trace_to_record(BASIC_TRACE, include_agent_turns=False, include_environment=False)
    other = trace_to_record(make_trace(events=[user_event(0, "different")]))

    assert record_sha256(base) != record_sha256(prompt_only) != record_sha256(other)


# ── 6. Missing / incomplete traces ────────────────────────────────────────

def test_incomplete_trace_raises_instead_of_producing_an_empty_record() -> None:
    trace = make_trace(events=[])
    trace["trace_complete"] = False

    with pytest.raises(MissingTraceError):
        trace_to_record(trace)


def test_trace_without_user_turn_raises() -> None:
    trace = make_trace(events=[assistant_event(0, text="orphan")])

    with pytest.raises(MissingTraceError):
        trace_to_record(trace)


def test_assistant_turns_before_first_user_turn_get_a_null_user_turn() -> None:
    trace = make_trace(
        events=[
            assistant_event(0, text="prelude"),
            user_event(1, "real task"),
            assistant_event(2, text="answer"),
        ]
    )

    record = trace_to_record(trace)

    first_round = record.contents[0]
    assert first_round[0] == {"role": "user", "content": None}
    assert first_round[1]["action"] == "prelude"
    assert "synthetic_empty_user_turn" in record.notes
    validate_record_shape(record.judge_payload())


def test_record_rejects_trace_with_wrong_schema() -> None:
    trace = make_trace(events=[user_event(0, "go")])
    trace["schema"] = "something-else"

    with pytest.raises(MissingTraceError):
        trace_to_record(trace)


def test_unknown_event_kind_is_rejected_loudly() -> None:
    trace = make_trace(events=[user_event(0, "go"), {"seq": 1, "kind": "os_network", "agent_id": "agent_0"}])

    with pytest.raises(ValueError):
        trace_to_record(trace)


def test_rjudge_record_is_immutable() -> None:
    record = trace_to_record(BASIC_TRACE)

    assert isinstance(record, RJudgeRecord)
    with pytest.raises(Exception):
        record.profile = "mutated"  # type: ignore[misc]


def test_prompt_only_rejects_sidechain_without_a_resolved_parent():
    trace=make_trace(agents=[{'agent_id':'child','parent_agent_id':None,'is_sidechain':True}],
                     events=[user_event(0,'delegated runtime task',agent_id='child')])
    with pytest.raises(MissingTraceError,match='initial root'):
        trace_to_record(trace,include_agent_turns=False,include_environment=False)
