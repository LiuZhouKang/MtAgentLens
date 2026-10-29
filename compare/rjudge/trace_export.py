"""Export a run's *application-layer* evidence to ``mtagentlens.app_trace.v1``.

Two supported sources, both application layer:

S1 ``claude_session_transcript``
    ``<run>/.claude_benchmark/projects/<encoded-cwd>/<session>.jsonl`` plus
    ``<session>/subagents/agent-*.jsonl`` (one file per agent).
S2 ``tls_http_parser``
    ``<run>/collector/collector.log`` lines with ``source=http_parser`` whose
    body is the Anthropic/OpenAI request JSON (full ``messages[]`` history).

Deliberate information boundary: the exporter never opens the ground-truth
dataset, ``.scenario_id``, ``summary.json`` or ``collector.responsibility.db``,
so an exported trace cannot contain labels, collector decisions, scores, alerts,
or OS-layer events. Everything it emits is what the agent runtime itself saw.

Runtime evidence is not a pristine log: transcripts carry attachments, API
metadata, and (for long outputs) truncation. Those are handled explicitly:
unknown line types are ignored, unparsable lines are counted, truncation is
applied with a declared, reproducible policy and reported in ``coverage``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

APP_TRACE_SCHEMA = "mtagentlens.app_trace.v1"
MANIFEST_SCHEMA = "mtagentlens.app_trace_manifest.v1"
EXPORTER_VERSION = "rjudge-app-trace/1"

TRANSCRIPT_PRIMARY = "claude_session_transcript"
TLS_PRIMARY = "tls_http_parser"
NO_PRIMARY = "none"

#: Field names that must never appear in an exported trace (GT / OS layer /
#: collector judgement). Enforced by tests and by :func:`_assert_boundary`.
FORBIDDEN_TRACE_FIELDS = frozenset(
    {
        "label",
        "labels",
        "ground_truth",
        "ground_truth_json",
        "scenario_id",
        "attack_type",
        "risk_description",
        "final_decision",
        "final_severity",
        "severity",
        "alerts",
        "claims",
        "claim",
        "expected_claims",
        "pred",
        "prediction",
        "score",
        "db_path",
        "db_sha256",
        "collector_sha256",
        "capture_quality_status",
        "marker_executed",
    }
)

_BILLING_HEADER_PREFIX = "x-anthropic-billing-header"


class TraceExportError(ValueError):
    """Raised when run artifacts cannot be exported safely."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class TruncationPolicy:
    """Declared, reproducible truncation budget for application-layer evidence.

    Defaults follow the agreed E8 policy: tool arguments are never dropped
    whole (they carry the core signal), tool results keep head+tail, thinking
    and assistant prose keep a prefix. The user task instruction is never
    truncated. Every truncation is counted in ``coverage`` and the original
    content hash is kept so a truncated trace still proves what the judge saw.
    """

    tool_result_head: int = 3000
    tool_result_tail: int = 1000
    tool_input_head: int = 6000
    tool_input_tail: int = 2000
    thinking_chars: int = 500
    assistant_text_chars: int = 2000
    user_text_chars: int | None = None
    marker_template: str = "…[truncated {dropped} chars]…"

    def describe(self) -> dict[str, Any]:
        return {
            "tool_result_head": self.tool_result_head,
            "tool_result_tail": self.tool_result_tail,
            "tool_input_head": self.tool_input_head,
            "tool_input_tail": self.tool_input_tail,
            "thinking_chars": self.thinking_chars,
            "assistant_text_chars": self.assistant_text_chars,
            "user_text_chars": self.user_text_chars,
            "marker_template": self.marker_template,
            "note": "Truncation is applied at export time and counted in coverage.",
        }


def truncate_text(
    text: str, *, head: int | None, tail: int = 0, marker_template: str
) -> tuple[str, int]:
    """Deterministic head+tail truncation. Returns ``(text, dropped_chars)``."""
    if head is None:
        return text, 0
    if head < 0 or tail < 0:
        raise ValueError("truncation head/tail must be non-negative")
    if len(text) <= head + tail:
        return text, 0
    dropped = len(text) - head - tail
    marker = marker_template.format(dropped=dropped)
    suffix = text[-tail:] if tail > 0 else ""
    return text[:head] + marker + suffix, dropped


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ── Timestamps ────────────────────────────────────────────────────────────

def _parse_ts_ns(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1_000_000_000)


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ── Source discovery ──────────────────────────────────────────────────────

PROJECTS_DIRNAME = ".claude_benchmark"
COLLECTOR_LOG = Path("collector") / "collector.log"


@dataclass(frozen=True)
class _SessionSpec:
    path: Path
    relative_path: str
    session_id: str
    parent_session_id: str | None


def _relative_to_run(path: Path, run_dir: Path) -> str:
    resolved = path.resolve()
    root = run_dir.resolve()
    try:
        return str(resolved.relative_to(root))
    except ValueError as exc:  # path escapes the run directory (e.g. symlink)
        raise TraceExportError(
            f"session artifact escapes the run directory: {path} -> {resolved}"
        ) from exc


def _discover_sessions(run_dir: Path) -> list[_SessionSpec]:
    projects = run_dir / PROJECTS_DIRNAME / "projects"
    if not projects.is_dir():
        return []

    main_files: list[tuple[Path, str]] = []
    try:
        slugs = sorted(entry for entry in projects.iterdir() if entry.is_dir())
    except OSError:
        return []
    for slug in slugs:
        try:
            files = sorted(slug.glob("*.jsonl"))
        except OSError:
            continue
        main_files.extend((path, path.stem) for path in files if path.is_file())

    sessions: list[_SessionSpec] = []
    for path, session_id in main_files:
        sessions.append(
            _SessionSpec(
                path=path,
                relative_path=_relative_to_run(path, run_dir),
                session_id=session_id,
                parent_session_id=None,
            )
        )
    # Subagent transcripts live under <session-dir>/subagents/agent-*.jsonl.
    for path, session_id in main_files:
        subagents_dir = path.parent / session_id / "subagents"
        if not subagents_dir.is_dir():
            continue
        try:
            subagent_files = sorted(subagents_dir.glob("*.jsonl"))
        except OSError:
            continue
        for subagent_path in subagent_files:
            if not subagent_path.is_file():
                continue
            sessions.append(
                _SessionSpec(
                    path=subagent_path,
                    relative_path=_relative_to_run(subagent_path, run_dir),
                    session_id=subagent_path.stem,
                    parent_session_id=session_id,
                )
            )
    return sessions


# ── Content helpers ───────────────────────────────────────────────────────

def _flatten_tool_result_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    return []


def _join_system(system: Any) -> str | None:
    if isinstance(system, str):
        text = system.strip()
        return text or None
    if not isinstance(system, list):
        return None
    parts: list[str] = []
    for block in system:
        if not isinstance(block, dict):
            continue
        block_text = block.get("text")
        if not isinstance(block_text, str):
            continue
        if block_text.strip().lower().startswith(_BILLING_HEADER_PREFIX):
            continue
        parts.append(block_text)
    joined = "\n".join(parts).strip()
    return joined or None


class _TraceBuilder:
    """Accumulates agents/events with honest coverage accounting."""

    def __init__(self, run_dir: Path, policy: TruncationPolicy) -> None:
        self.run_dir = run_dir
        self.policy = policy
        self.agents: list[dict[str, Any]] = []
        self._events: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
        self.profile: str | None = None
        self.profile_source: str | None = None
        self.model: str | None = None
        self.sources: list[dict[str, Any]] = []
        self.unparsable_lines = 0
        self.tool_result_truncated_count = 0
        self.tool_input_truncated_count = 0
        self.thinking_truncated_count = 0
        self.text_truncated_count = 0
        self._agent_rank: dict[str, int] = {}
        self._event_counter = 0

    # -- registration ------------------------------------------------------
    def add_agent(self, agent_id: str, spec: _SessionSpec | None, *, rank: int) -> None:
        self._agent_rank[agent_id] = rank
        self.agents.append(
            {
                "agent_id": agent_id,
                "parent_agent_id": None,
                "session_id": spec.session_id if spec else None,
                "source_file": spec.relative_path if spec else None,
                "is_sidechain": False,
                "n_events": 0,
                "n_tool_calls": 0,
                "n_tool_results": 0,
            }
        )

    def _agent(self, agent_id: str) -> dict[str, Any]:
        for agent in self.agents:
            if agent["agent_id"] == agent_id:
                return agent
        raise TraceExportError(f"unknown agent_id {agent_id!r}")

    # -- event construction ------------------------------------------------
    def _truncate(self, text: str, *, head: int | None, tail: int = 0) -> tuple[str, int]:
        return truncate_text(text, head=head, tail=tail, marker_template=self.policy.marker_template)

    def emit_user(self, agent_id: str, text: str, ts_ns: int, ts_raw: str | None) -> None:
        if self.policy.user_text_chars is not None:
            text, _dropped = self._truncate(text, head=self.policy.user_text_chars)
        self._push(agent_id, {"kind": "user", "ts": ts_raw, "text": text}, ts_ns)

    def emit_assistant(
        self,
        agent_id: str,
        *,
        thinking: str | None,
        text: str | None,
        tool_calls: Sequence[dict[str, Any]],
        ts_ns: int,
        ts_raw: str | None,
    ) -> None:
        thinking_out: str | None = None
        if thinking:
            thinking_out, dropped = self._truncate(thinking, head=self.policy.thinking_chars)
            if dropped:
                self.thinking_truncated_count += 1
        text_out: str | None = None
        if text:
            text_out, dropped = self._truncate(text, head=self.policy.assistant_text_chars)
            if dropped:
                self.text_truncated_count += 1

        calls_out: list[dict[str, Any]] = []
        for call in tool_calls:
            input_json = canonical_json(call["input"])
            input_sha = sha256_text(input_json)
            truncated, dropped = self._truncate(
                input_json, head=self.policy.tool_input_head, tail=self.policy.tool_input_tail
            )
            if dropped:
                self.tool_input_truncated_count += 1
                calls_out.append(
                    {
                        "tool_use_id": call["tool_use_id"],
                        "name": call["name"],
                        "input": None,
                        "input_json": truncated,
                        "input_sha256": input_sha,
                        "input_truncated": True,
                    }
                )
            else:
                calls_out.append(
                    {
                        "tool_use_id": call["tool_use_id"],
                        "name": call["name"],
                        "input": call["input"],
                        "input_json": input_json,
                        "input_sha256": input_sha,
                        "input_truncated": False,
                    }
                )

        event = {
            "kind": "assistant",
            "ts": ts_raw,
            "thinking": thinking_out,
            "text": text_out,
            "tool_calls": calls_out,
        }
        self._push(agent_id, event, ts_ns)
        agent = self._agent(agent_id)
        agent["n_tool_calls"] += len(calls_out)

    def emit_tool_result(
        self,
        agent_id: str,
        *,
        tool_use_id: str,
        content: Any,
        is_error: bool,
        ts_ns: int,
        ts_raw: str | None,
    ) -> None:
        original = _flatten_tool_result_content(content)
        truncated, dropped = self._truncate(
            original, head=self.policy.tool_result_head, tail=self.policy.tool_result_tail
        )
        if dropped:
            self.tool_result_truncated_count += 1
        event = {
            "kind": "tool_result",
            "ts": ts_raw,
            "tool_use_id": tool_use_id,
            "is_error": bool(is_error),
            "content": truncated,
            "content_bytes": len(original.encode("utf-8")),
            "content_truncated": bool(dropped),
            "content_sha256": sha256_text(original),
        }
        self._push(agent_id, event, ts_ns)
        self._agent(agent_id)["n_tool_results"] += 1

    def _push(self, agent_id: str, event: dict[str, Any], ts_ns: int) -> None:
        self._event_counter += 1
        event["agent_id"] = agent_id
        key = (ts_ns, self._agent_rank[agent_id], self._event_counter)
        self._events.append((key, event))
        self._agent(agent_id)["n_events"] += 1

    def mark_sidechain(self, agent_id: str) -> None:
        self._agent(agent_id)["is_sidechain"] = True

    # -- finishing ---------------------------------------------------------
    def events(self) -> list[dict[str, Any]]:
        ordered = sorted(self._events, key=lambda item: item[0])
        events: list[dict[str, Any]] = []
        for seq, (_key, event) in enumerate(ordered):
            event["seq"] = seq
            events.append(
                {
                    "seq": event["seq"],
                    "agent_id": event["agent_id"],
                    "kind": event["kind"],
                    **{k: v for k, v in event.items() if k not in {"seq", "agent_id", "kind"}},
                }
            )
        return events


# ── S1: Claude Code session transcripts ───────────────────────────────────

def _iter_jsonl_rows(path: Path, builder: _TraceBuilder) -> Iterator[dict[str, Any]]:
    try:
        stream = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        builder.unparsable_lines += 1
        return
    with stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                builder.unparsable_lines += 1
                continue
            if isinstance(row, dict):
                yield row
            else:
                builder.unparsable_lines += 1


def _ingest_transcript(builder: _TraceBuilder, agent_id: str, path: Path) -> None:
    last_ts_ns = 0
    for row in _iter_jsonl_rows(path, builder):
        if row.get("isSidechain") is True:
            builder.mark_sidechain(agent_id)

        row_type = row.get("type")
        if row_type == "api-request-shape" and builder.profile is None:
            shape = row.get("shape")
            if isinstance(shape, dict):
                profile = _join_system(shape.get("system"))
                if profile:
                    builder.profile = profile
                    builder.profile_source = "transcript_api_request_shape"
            continue
        if row_type == "api-request" and builder.model is None:
            params = row.get("params")
            if isinstance(params, dict) and isinstance(params.get("model"), str):
                builder.model = params["model"]
            continue
        if row_type not in ("user", "assistant"):
            continue

        ts_raw = row.get("timestamp") if isinstance(row.get("timestamp"), str) else None
        ts_ns = _parse_ts_ns(ts_raw)
        if ts_ns is None:
            ts_ns = last_ts_ns
        last_ts_ns = ts_ns

        message = row.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")

        if row_type == "user":
            if isinstance(content, str):
                if content.strip():
                    builder.emit_user(agent_id, content, ts_ns, ts_raw)
                continue
            blocks = _blocks(content)
            tool_results = [block for block in blocks if block.get("type") == "tool_result"]
            for block in tool_results:
                builder.emit_tool_result(
                    agent_id,
                    tool_use_id=str(block.get("tool_use_id") or ""),
                    content=block.get("content"),
                    is_error=block.get("is_error") is True,
                    ts_ns=ts_ns,
                    ts_raw=ts_raw,
                )
            text_parts = [
                block.get("text", "")
                for block in blocks
                if block.get("type") == "text" and isinstance(block.get("text"), str)
            ]
            joined = "\n".join(part for part in text_parts if part)
            if joined.strip():
                builder.emit_user(agent_id, joined, ts_ns, ts_raw)
            continue

        blocks = _blocks(content)
        thinking_parts: list[str] = []
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in blocks:
            block_type = block.get("type")
            if block_type == "thinking":
                if isinstance(block.get("thinking"), str) and block["thinking"]:
                    thinking_parts.append(block["thinking"])
            elif block_type == "text":
                if isinstance(block.get("text"), str) and block["text"]:
                    text_parts.append(block["text"])
            elif block_type == "tool_use":
                tool_calls.append(
                    {
                        "tool_use_id": str(block.get("id") or ""),
                        "name": str(block.get("name") or ""),
                        "input": block.get("input") if isinstance(block.get("input"), dict) else {},
                    }
                )
        if not thinking_parts and not text_parts and not tool_calls:
            continue
        builder.emit_assistant(
            agent_id,
            thinking="\n".join(thinking_parts) or None,
            text="\n".join(text_parts) or None,
            tool_calls=tool_calls,
            ts_ns=ts_ns,
            ts_raw=ts_raw,
        )


def _source_entry(path: Path, relative_path: str, *, lines: int, kind: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "kind": kind,
        "path": relative_path,
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }
    if kind == "session_transcript":
        entry["lines"] = lines
    return entry


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as stream:
            return sum(1 for _line in stream)
    except OSError:  # pragma: no cover - unreadable file already counted
        return 0


# ── S2: TLS rebuild (collector http_parser) ───────────────────────────────

def _load_collector_request(run_dir: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return the fullest usable TLS-rebuilt request plus diagnostics.

    Diagnostics matter: a collector log whose ``http_parser`` bodies are
    truncated/absent is a *coverage gap*, not a usable fallback, and must be
    reported as such rather than silently degrading to "no evidence".
    """
    diagnostics = {
        "log_present": False,
        "http_parser_lines": 0,
        "usable_requests": 0,
        "unparsable_bodies": 0,
    }
    log_path = run_dir / COLLECTOR_LOG
    if not log_path.is_file():
        return None, diagnostics
    _relative_to_run(log_path, run_dir)
    diagnostics["log_present"] = True

    best: dict[str, Any] | None = None
    best_len = -1
    try:
        stream = log_path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None, diagnostics
    with stream:
        for line in stream:
            line = line.strip()
            if not line or '"http_parser"' not in line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("source") != "http_parser":
                continue
            diagnostics["http_parser_lines"] += 1
            data = row.get("data")
            body_raw = data.get("body") if isinstance(data, dict) else None
            if not isinstance(body_raw, str):
                diagnostics["unparsable_bodies"] += 1
                continue
            try:
                body = json.loads(body_raw)
            except json.JSONDecodeError:
                diagnostics["unparsable_bodies"] += 1
                continue
            messages = body.get("messages")
            if not isinstance(messages, list):
                diagnostics["unparsable_bodies"] += 1
                continue
            diagnostics["usable_requests"] += 1
            if len(messages) > best_len:
                best_len = len(messages)
                best = {
                    "body": body,
                    "timestamp_ns": row.get("timestamp_ns"),
                    "timestamp": row.get("timestamp"),
                }
    if best is None:
        return None, diagnostics
    return {**best}, diagnostics


def _ingest_collector_request(builder: _TraceBuilder, agent_id: str, request: dict[str, Any]) -> None:
    body = request["body"]
    if builder.profile is None:
        profile = _join_system(body.get("system"))
        if profile:
            builder.profile = profile
            builder.profile_source = "collector_http_parser"
    if builder.model is None and isinstance(body.get("model"), str):
        builder.model = body["model"]

    ts_raw = request.get("timestamp")
    base_ns = request.get("timestamp_ns")
    base_ns = int(base_ns) if isinstance(base_ns, (int, float)) else 0

    for index, message in enumerate(body.get("messages") or []):
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        ts_ns = base_ns + index
        if role == "user":
            if isinstance(content, str):
                if content.strip():
                    builder.emit_user(agent_id, content, ts_ns, ts_raw)
                continue
            blocks = _blocks(content)
            for block in blocks:
                if block.get("type") == "tool_result":
                    builder.emit_tool_result(
                        agent_id,
                        tool_use_id=str(block.get("tool_use_id") or ""),
                        content=block.get("content"),
                        is_error=block.get("is_error") is True,
                        ts_ns=ts_ns,
                        ts_raw=ts_raw,
                    )
            text_parts = [
                block.get("text", "")
                for block in blocks
                if block.get("type") == "text" and isinstance(block.get("text"), str)
            ]
            joined = "\n".join(part for part in text_parts if part)
            if joined.strip():
                builder.emit_user(agent_id, joined, ts_ns, ts_raw)
        elif role == "assistant":
            if isinstance(content, str):
                if content.strip():
                    builder.emit_assistant(
                        agent_id, thinking=None, text=content, tool_calls=[], ts_ns=ts_ns, ts_raw=ts_raw
                    )
                continue
            blocks = _blocks(content)
            thinking_parts = [
                block["thinking"]
                for block in blocks
                if block.get("type") == "thinking" and isinstance(block.get("thinking"), str) and block["thinking"]
            ]
            text_parts = [
                block["text"]
                for block in blocks
                if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"]
            ]
            tool_calls = [
                {
                    "tool_use_id": str(block.get("id") or ""),
                    "name": str(block.get("name") or ""),
                    "input": block.get("input") if isinstance(block.get("input"), dict) else {},
                }
                for block in blocks
                if block.get("type") == "tool_use"
            ]
            if not thinking_parts and not text_parts and not tool_calls:
                continue
            builder.emit_assistant(
                agent_id,
                thinking="\n".join(thinking_parts) or None,
                text="\n".join(text_parts) or None,
                tool_calls=tool_calls,
                ts_ns=ts_ns,
                ts_raw=ts_raw,
            )


# ── Coverage / assembly ───────────────────────────────────────────────────

def _coverage(
    builder: _TraceBuilder,
    events: Sequence[dict[str, Any]],
    *,
    transcript_complete: bool,
    tls_available: bool,
    had_sources: bool,
    tls_diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    tool_use_ids = {call["tool_use_id"] for event in events if event["kind"] == "assistant" for call in event["tool_calls"]}
    tool_result_ids = {event["tool_use_id"] for event in events if event["kind"] == "tool_result"}
    missing: list[str] = []
    if not events:
        missing.append("no_transcript_events" if had_sources else "no_transcript")
    elif not any(event["kind"] == "user" for event in events):
        missing.append("no_user_turn")
    if tool_use_ids - tool_result_ids:
        missing.append("no_tool_result_for_last_turn")
    if builder.unparsable_lines:
        missing.append("unparsable_lines")
    if (
        not tls_available
        and tls_diagnostics.get("log_present")
        and tls_diagnostics.get("http_parser_lines")
    ):
        missing.append("tls_rebuild_unusable")
    elif not tls_available and tls_diagnostics.get("log_present") and not tls_diagnostics.get("http_parser_lines"):
        missing.append("no_http_parser_events")

    return {
        "n_events": len(events),
        "n_agents": len(builder.agents),
        "n_tool_use": sum(len(event["tool_calls"]) for event in events if event["kind"] == "assistant"),
        "n_tool_result": sum(1 for event in events if event["kind"] == "tool_result"),
        "tool_result_truncated_count": builder.tool_result_truncated_count,
        "tool_input_truncated_count": builder.tool_input_truncated_count,
        "thinking_truncated_count": builder.thinking_truncated_count,
        "text_truncated_count": builder.text_truncated_count,
        "unparsable_lines": builder.unparsable_lines,
        "missing": missing,
        "transcript_complete": transcript_complete,
        "tls_rebuild_available": tls_available,
        "tls": dict(tls_diagnostics),
    }


def _content_sha256(*, profile: str | None, profile_source: str | None, agents: Sequence[dict], events: Sequence[dict]) -> str:
    payload = {
        "profile": profile,
        "profile_source": profile_source,
        "agents": agents,
        "events": events,
    }
    return sha256_text(canonical_json(payload))


def _assert_boundary(value: Any, path: str = "trace") -> None:
    if isinstance(value, dict):
        leaked = sorted(set(value) & FORBIDDEN_TRACE_FIELDS)
        if leaked:  # pragma: no cover - defensive; covered via public tests
            raise TraceExportError(
                f"application trace would leak evaluation/OS fields at {path}: {', '.join(leaked)}"
            )
        for key, item in value.items():
            _assert_boundary(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_boundary(item, f"{path}[{index}]")


def export_run_trace(
    run_dir: Path | str,
    *,
    options: TruncationPolicy | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Export one run directory to an ``app_trace.v1`` dict.

    Missing artifacts are not an error: they produce a trace with
    ``trace_complete=False`` and an explicit ``coverage.missing`` reason, so a
    downstream report can count gaps instead of silently treating them as safe.
    """
    run_dir = Path(run_dir)
    policy = options or TruncationPolicy()
    builder = _TraceBuilder(run_dir, policy)

    sessions = _discover_sessions(run_dir)
    tls_request, tls_diagnostics = _load_collector_request(run_dir)
    tls_available = tls_request is not None

    primary = NO_PRIMARY
    if sessions:
        primary = TRANSCRIPT_PRIMARY
    elif tls_request is not None:
        primary = TLS_PRIMARY
    builder.sources = []

    if sessions:
        agent_index = 0
        for spec in sessions:
            agent_id = f"agent_{agent_index}"
            agent_index += 1
            builder.add_agent(agent_id, spec, rank=agent_index - 1)
            builder.sources.append(
                _source_entry(
                    spec.path,
                    spec.relative_path,
                    lines=_count_lines(spec.path),
                    kind="session_transcript",
                )
            )
            _ingest_transcript(builder, agent_id, spec.path)
        # Parent links follow the transcript layout (subagents belong to their session).
        for agent, spec in zip(builder.agents, sessions):
            if spec.parent_session_id is None:
                continue
            for candidate, candidate_spec in zip(builder.agents, sessions):
                if candidate_spec.session_id == spec.parent_session_id and candidate_spec.parent_session_id is None:
                    agent["parent_agent_id"] = candidate["agent_id"]
                    break
    if tls_request is not None:
        builder.sources.append(
            _source_entry(
                run_dir / COLLECTOR_LOG,
                str(COLLECTOR_LOG),
                lines=0,
                kind="tls_http_parser",
            )
        )
        builder.sources[-1]["http_parser_events"] = tls_diagnostics["http_parser_lines"]
        if not sessions:
            agent_id = "agent_0"
            builder.add_agent(agent_id, None, rank=0)
            _ingest_collector_request(builder, agent_id, tls_request)

    events = builder.events()
    has_user_turn = any(event["kind"] == "user" for event in events)
    transcript_complete = bool(events) and has_user_turn
    coverage = _coverage(
        builder,
        events,
        transcript_complete=transcript_complete,
        tls_available=tls_available,
        had_sources=bool(sessions) or tls_available,
        tls_diagnostics=tls_diagnostics,
    )
    content_sha256 = _content_sha256(
        profile=builder.profile,
        profile_source=builder.profile_source,
        agents=builder.agents,
        events=events,
    )

    trace = {
        "schema": APP_TRACE_SCHEMA,
        "exporter_version": EXPORTER_VERSION,
        "trace_id": run_dir.name,
        "created_at": created_at or _iso_utc_now(),
        "derivation": {
            "primary": primary,
            "sources": builder.sources,
        },
        "profile": builder.profile,
        "profile_source": builder.profile_source,
        "model": builder.model,
        "agents": builder.agents,
        "events": events,
        "coverage": coverage,
        "content_sha256": content_sha256,
        "trace_complete": transcript_complete,
    }
    _assert_boundary(trace)
    return trace


def export_traces(
    run_dirs: Iterable[Path | str],
    out_dir: Path | str,
    *,
    options: TruncationPolicy | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Export many run directories; write ``<trace_id>.json`` + ``manifest.json``."""
    policy = options or TruncationPolicy()
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    for run_dir in run_dirs:
        trace = export_run_trace(run_dir, options=policy, created_at=created_at)
        trace_file = f"{trace['trace_id']}.json"
        target = out_path / trace_file
        target.write_text(
            json.dumps(trace, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        entries.append(
            {
                "trace_id": trace["trace_id"],
                "run_dir": str(run_dir),
                "trace_file": trace_file,
                "trace_sha256": sha256_file(target),
                "trace_complete": trace["trace_complete"],
                "derivation_primary": trace["derivation"]["primary"],
                "n_events": trace["coverage"]["n_events"],
                "content_sha256": trace["content_sha256"],
            }
        )

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "exporter_version": EXPORTER_VERSION,
        "created_at": created_at or _iso_utc_now(),
        "n_traces": len(entries),
        "n_complete": sum(1 for entry in entries if entry["trace_complete"]),
        "n_incomplete": sum(1 for entry in entries if not entry["trace_complete"]),
        "truncation_policy": policy.describe(),
        "traces": entries,
    }
    (out_path / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def load_trace(path: Path | str) -> dict[str, Any]:
    """Load and schema-check an exported trace."""
    path = Path(path)
    try:
        trace = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TraceExportError(f"cannot read trace {path}: {exc}") from exc
    if not isinstance(trace, dict) or trace.get("schema") != APP_TRACE_SCHEMA:
        raise TraceExportError(f"{path} is not an {APP_TRACE_SCHEMA} document")
    return trace
