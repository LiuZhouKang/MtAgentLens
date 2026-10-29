"""Convert an ``app_trace.v1`` document into an R-Judge judge-input record.

The record is exactly what the R-Judge protocol consumes: a ``profile`` (system
message) plus ``contents`` (rounds of ``user`` / ``agent{thought,action}`` /
``environment`` turns). It deliberately carries no labels, no ground truth and
no collector judgement -- those live in the evaluation layer that pairs judge
outputs with the frozen dataset.

Two adaptations over the raw protocol are explicit and switchable:

* consecutive ``assistant`` events of the same agent merge into one ``agent``
  turn (Claude Code emits thinking / text / tool_use as separate lines);
* multi-agent traces get a ``[agent_id]`` prefix inside turn text so a reader
  can attribute actions; single-agent traces are byte-identical to the plain
  protocol rendering.

Ablation arms are derived from the same trace, never re-collected:
``A1_prompt_only`` (user turns only), ``A2_full_trace`` (default),
``A3_no_tool_result`` (agent turns but no environment output).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .protocol import (
    AGENT_ROLE,
    ENVIRONMENT_ROLE,
    PROFILES,
    USER_ROLE,
    validate_record_shape,
)
from .trace_export import APP_TRACE_SCHEMA, canonical_json, sha256_text

RECORD_SCHEMA = "mtagentlens.rjudge_record.v1"

ARM_PROMPT_ONLY = "A1_prompt_only"
ARM_FULL_TRACE = "A2_full_trace"
ARM_NO_TOOL_RESULT = "A3_no_tool_result"

# app_trace.v1 event kinds (application-layer vocabulary) vs record turn roles.
EVENT_KIND_USER = "user"
EVENT_KIND_ASSISTANT = "assistant"
EVENT_KIND_TOOL_RESULT = "tool_result"


class MissingTraceError(ValueError):
    """Raised when there is not enough application-layer evidence to judge."""


@dataclass(frozen=True)
class RJudgeRecord:
    """An R-Judge judge input plus the metadata needed to audit it."""

    profile: str
    contents: list[list[dict[str, Any]]]
    trace_id: str
    trace_sha256: str
    trace_complete: bool
    profile_source: str
    agent_ids: tuple[str, ...] = ()
    arm: str = ARM_FULL_TRACE
    variants: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    trace_coverage: dict[str, Any] = field(default_factory=dict)

    def judge_payload(self) -> dict[str, Any]:
        """The protocol-conformant record; validated and deep-copied on read."""
        payload = {
            "profile": self.profile,
            "contents": [
                [dict(turn) for turn in round_] for round_ in self.contents
            ],
        }
        validate_record_shape(payload)
        return payload

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": RECORD_SCHEMA,
            "record": self.judge_payload(),
            "record_sha256": record_sha256(self),
            "trace_id": self.trace_id,
            "trace_sha256": self.trace_sha256,
            "trace_complete": self.trace_complete,
            "profile_source": self.profile_source,
            "agent_ids": list(self.agent_ids),
            "arm": self.arm,
            "variants": dict(self.variants),
            "notes": list(self.notes),
            "trace_coverage": copy.deepcopy(self.trace_coverage),
        }


def _record_of(record: RJudgeRecord | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(record, RJudgeRecord):
        return record.judge_payload()
    if isinstance(record, Mapping):
        payload = {"profile": record.get("profile"), "contents": record.get("contents")}
        validate_record_shape(payload)
        return payload
    raise TypeError(f"unsupported record type: {type(record).__name__}")


def record_sha256(record: RJudgeRecord | Mapping[str, Any]) -> str:
    """Stable hash of exactly what the judge would see (profile + contents)."""
    return sha256_text(canonical_json(_record_of(record)))


def _render_agent_turn(
    event: Mapping[str, Any], *, agent_attribution: bool, agent_id: str
) -> tuple[str | None, str | None]:
    thought = event.get("thinking") or None
    parts: list[str] = []
    text = event.get("text")
    if text:
        parts.append(str(text))
    for call in event.get("tool_calls") or ():
        if not isinstance(call, Mapping):
            continue
        name = str(call.get("name") or "")
        payload = call.get("input_json")
        if not isinstance(payload, str):
            payload = canonical_json(call.get("input"))
        parts.append(f"{name}: {payload}")
    action = "\n".join(parts) or None
    if agent_attribution:
        if thought:
            thought = f"[{agent_id}] {thought}"
        if action:
            action = f"[{agent_id}] {action}"
    return thought, action


def _merge_agent_turn(turn: dict[str, Any], thought: str | None, action: str | None) -> None:
    if thought:
        turn["thought"] = f"{turn['thought']}\n{thought}" if turn.get("thought") else thought
    if action:
        turn["action"] = f"{turn['action']}\n{action}" if turn.get("action") else action


def trace_to_record(
    trace: Mapping[str, Any],
    *,
    include_agent_turns: bool = True,
    include_environment: bool = True,
    agent_attribution: bool | None = None,
    profile: str | None = None,
) -> RJudgeRecord:
    """Build an R-Judge record from one application trace.

    Raises :class:`MissingTraceError` when the trace is incomplete or carries no
    user turn, so a missing transcript can never silently become a judgement.
    """
    if not isinstance(trace, Mapping):
        raise MissingTraceError("trace must be a mapping")
    if trace.get("schema") != APP_TRACE_SCHEMA:
        raise MissingTraceError(
            f"trace schema must be {APP_TRACE_SCHEMA!r}, got {trace.get('schema')!r}"
        )
    if trace.get("trace_complete") is not True:
        coverage = trace.get("coverage") or {}
        reason = ", ".join(coverage.get("missing") or []) or "trace_complete=false"
        raise MissingTraceError(
            f"trace {trace.get('trace_id')!r} is incomplete ({reason}); refusing to judge"
        )

    events = list(trace.get("events") or [])
    if not events:
        raise MissingTraceError(f"trace {trace.get('trace_id')!r} has no events")
    if include_environment and not include_agent_turns:
        raise ValueError(
            "include_environment=True requires include_agent_turns=True "
            "(environment output with no action is not a meaningful arm)"
        )

    agents = list(trace.get("agents") or [])
    agent_ids = tuple(
        str(agent.get("agent_id"))
        for agent in agents
        if isinstance(agent, Mapping) and agent.get("agent_id")
    )
    if agent_attribution is None:
        agent_attribution = len(agent_ids) > 1

    notes: list[str] = []
    if profile is None:
        trace_profile = trace.get("profile")
        if isinstance(trace_profile, str) and trace_profile.strip():
            profile = trace_profile
            profile_source = str(trace.get("profile_source") or "trace")
        else:
            profile = PROFILES["fallback"]
            profile_source = "fallback"
            notes.append("profile_fallback")
    else:
        profile_source = "override"

    arm = ARM_FULL_TRACE
    if not include_agent_turns:
        arm = ARM_PROMPT_ONLY
    elif not include_environment:
        arm = ARM_NO_TOOL_RESULT
    if agent_attribution:
        notes.append("agent_attribution_applied")

    coverage = dict(trace.get("coverage") or {})
    if any(
        coverage.get(key)
        for key in (
            "tool_result_truncated_count",
            "tool_input_truncated_count",
            "thinking_truncated_count",
            "text_truncated_count",
        )
    ):
        notes.append("trace_truncated")

    ordered = sorted(events, key=lambda event: event.get("seq", 0))
    if not any(event.get("kind") == EVENT_KIND_USER for event in ordered):
        raise MissingTraceError(
            f"trace {trace.get('trace_id')!r} has no user turn; refusing to judge"
        )

    if not include_agent_turns:
        # A1 boundary: root/child status comes only from ``agents`` metadata.
        # User text from a child (or from an agent absent from metadata) is not
        # initial prompt evidence, and only the earliest turn per root counts.
        root_agent_ids = {
            str(agent.get("agent_id"))
            for agent in agents
            if isinstance(agent, Mapping)
            and agent.get("agent_id")
            and agent.get("parent_agent_id") in (None, "")
            and not agent.get("is_sidechain", False)
        }
        initial_root_user_turns: list[Mapping[str, Any]] = []
        seen_root_agents: set[str] = set()
        for event in ordered:
            if event.get("kind") != EVENT_KIND_USER:
                continue
            event_agent_id = str(event.get("agent_id") or "agent_0")
            if event_agent_id not in root_agent_ids or event_agent_id in seen_root_agents:
                continue
            seen_root_agents.add(event_agent_id)
            initial_root_user_turns.append(event)
        if not initial_root_user_turns:
            raise MissingTraceError(
                f"trace {trace.get('trace_id')!r} has no eligible initial root "
                "user turn; refusing to synthesize a task"
            )
        ordered = initial_root_user_turns
        notes.append("initial_root_user_turns_only")

    rounds: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] | None = None
    last_agent_turn: dict[str, Any] | None = None
    last_agent_id: str | None = None

    for event in ordered:
        if not isinstance(event, Mapping):
            raise ValueError("trace event must be a mapping")
        kind = event.get("kind")
        agent_id = str(event.get("agent_id") or "agent_0")

        if kind == EVENT_KIND_USER:
            if current is not None:
                rounds.append(current)
            current = [{"role": USER_ROLE, "content": event.get("text") or ""}]
            last_agent_turn = None
            last_agent_id = None
            continue

        if kind == EVENT_KIND_ASSISTANT:
            if not include_agent_turns:
                continue
            if current is None:
                current = [{"role": USER_ROLE, "content": None}]
                notes.append("synthetic_empty_user_turn")
                last_agent_turn = None
                last_agent_id = None
            thought, action = _render_agent_turn(
                event, agent_attribution=agent_attribution, agent_id=agent_id
            )
            if thought is None and action is None:
                continue
            if last_agent_turn is not None and last_agent_id == agent_id:
                _merge_agent_turn(last_agent_turn, thought, action)
            else:
                turn = {"role": AGENT_ROLE, "thought": thought, "action": action}
                current.append(turn)
                last_agent_turn = turn
                last_agent_id = agent_id
            continue

        if kind == EVENT_KIND_TOOL_RESULT:
            # An environment boundary always ends the current agent turn, even
            # when the environment output itself is dropped (A3 ablation).
            last_agent_turn = None
            last_agent_id = None
            if not include_environment:
                continue
            if current is None:
                notes.append("tool_result_before_first_user_turn")
                continue
            content = str(event.get("content") or "")
            if agent_attribution:
                content = f"[{agent_id}] {content}"
            current.append({"role": ENVIRONMENT_ROLE, "content": content})
            continue

        raise ValueError(f"unknown trace event kind: {kind!r}")

    if current is not None:
        rounds.append(current)
    if not rounds:
        raise MissingTraceError(f"trace {trace.get('trace_id')!r} produced no rounds")

    record = RJudgeRecord(
        profile=profile,
        contents=rounds,
        trace_id=str(trace.get("trace_id") or ""),
        trace_sha256=str(trace.get("content_sha256") or ""),
        trace_complete=bool(trace.get("trace_complete")),
        profile_source=profile_source,
        agent_ids=agent_ids,
        arm=arm,
        variants={
            "include_agent_turns": include_agent_turns,
            "include_environment": include_environment,
            "agent_attribution": bool(agent_attribution),
        },
        notes=tuple(dict.fromkeys(notes)),
        trace_coverage=coverage,
    )
    # Fail at build time if the record cannot carry an R-Judge judgement.
    record.judge_payload()
    return record


def records_to_jsonl(records: Sequence[RJudgeRecord]) -> str:
    return "".join(json.dumps(record.to_json(), ensure_ascii=False, sort_keys=True) + "\n" for record in records)
