# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Extract a blinded assistant trajectory from captured SSE tool-call events.

Only assistant text and tool calls are copied (no raw transport bytes, no
tool results, no credentials); identifiers are blinded. Tool calls are the
agent's observed intent surface, not proof that an OS action succeeded.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.annotator.blinding import blind_identifiers

SOURCE_ID = "trajectory"
SOURCE_KIND = "raw_capture"
PRODUCER = "collector-trace-trajectory/1"
MAX_TEXT = 4000
MAX_INPUT = 4000


def _short(value: object, limit: int) -> tuple[object, bool, int]:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if len(encoded) <= limit:
        return value, False, len(encoded)
    return encoded[:limit] + "…", True, len(encoded)


def _assistant_message(data: dict) -> dict | None:
    sse = data.get("sse_events")
    if not isinstance(sse, list):
        return None
    blocks: dict[int, dict] = {}
    for item in sse:
        if not isinstance(item, dict):
            continue
        parsed = item.get("parsed_data")
        if not isinstance(parsed, dict):
            continue
        kind = parsed.get("type")
        index = parsed.get("index")
        if kind == "content_block_start" and isinstance(index, int):
            block = parsed.get("content_block") or {}
            blocks[index] = {"type": block.get("type"), "name": block.get("name"),
                             "text": "", "input": block.get("input") or {},
                             "partial": ""}
        elif kind == "content_block_delta" and isinstance(index, int):
            delta = parsed.get("delta") or {}
            block = blocks.setdefault(index, {"type": None, "name": None, "text": "",
                                              "input": {}, "partial": ""})
            if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                block["text"] += delta["text"]
            elif delta.get("type") == "input_json_delta" and isinstance(delta.get("partial_json"), str):
                block["partial"] += delta["partial_json"]
    text = "".join(block["text"] for block in blocks.values()
                   if block.get("type") == "text").strip()
    if not text and isinstance(data.get("text_content"), str):
        text = data["text_content"].strip()
    calls = []
    for block in blocks.values():
        if block.get("type") != "tool_use":
            continue
        value = block.get("input") or {}
        if block.get("partial"):
            try:
                value = json.loads(block["partial"])
            except ValueError:
                value = {"unparsed": block["partial"]}
        if not value and data.get("json_content"):
            try:
                value = json.loads(data["json_content"])
            except ValueError:
                value = {"unparsed": data["json_content"]}
        calls.append({"name": block.get("name"), "input": value})
    if not calls and data.get("json_content"):
        try:
            calls.append({"name": None, "input": json.loads(data["json_content"])})
        except ValueError:
            pass
    if not text and not calls:
        return None
    text_value, text_truncated, text_len = _short(text, MAX_TEXT)
    calls_value, input_truncated, input_len = _short(calls, MAX_INPUT)
    return {"text": text_value, "tool_calls": calls_value,
            "truncation": {"text": text_truncated, "tool_calls": input_truncated,
                           "text_bytes": text_len, "tool_calls_bytes": input_len}}


def extract_trajectory(log_path: Path, *, identifiers=()) -> tuple[Path, list[dict]] | None:
    """Return deterministic trajectory JSONL anchors, or None if none exist."""
    log_path = Path(log_path)
    if not log_path.is_file():
        return None
    messages: dict[str, dict] = {}
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
            continue
        data = event["data"]
        message_id = data.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            continue
        parsed = _assistant_message(data)
        if parsed is None:
            continue
        start = data.get("start_time")
        timestamp = str(start if isinstance(start, int) and start > 0 else 0)
        # Keep the most complete snapshot for a message id.
        candidate = {"message_id": message_id, "timestamp": timestamp, **parsed}
        previous = messages.get(message_id)
        if previous is None or len(json.dumps(candidate)) > len(json.dumps(previous)):
            messages[message_id] = candidate
    if not messages:
        return None
    lines = []
    for message_id in sorted(messages, key=lambda key: (int(messages[key]["timestamp"]), key)):
        message = messages[message_id]
        payload = {"kind": "assistant_message", **message}
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        blinded, _ = blind_identifiers(body.encode("utf-8"), identifiers)
        try:
            line = json.loads(blinded)
        except ValueError:
            line = {"kind": "assistant_message", "message_id": message_id,
                    "timestamp": message["timestamp"], "text": blinded}
        if not isinstance(line, dict) or "message_id" not in line:
            line = {"kind": "assistant_message", "message_id": message_id,
                    "timestamp": message["timestamp"], "text": blinded}
        lines.append(line)
    return log_path, lines


def write_trajectory(out_path: Path, lines: list[dict]) -> tuple[Path, list[dict]]:
    if not lines:
        raise ValueError("trajectory has no events")
    events, offset = [], 0
    with Path(out_path).open("wb") as stream:
        for index, line in enumerate(lines):
            encoded = (json.dumps(line, ensure_ascii=False, sort_keys=True,
                                  allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
            stream.write(encoded)
            events.append({
                "event_id": f"{SOURCE_ID}:{line['message_id']}",
                "source_id": SOURCE_ID,
                "record_id": line["message_id"],
                "timestamp": line["timestamp"],
                "clock_domain": "collector-unix-ns",
                "derivation": f"collector trajectory assistant message {index}",
                "byte_offset": offset,
                "byte_length": len(encoded),
                "record_sha256": hashlib.sha256(encoded).hexdigest(),
            })
            offset += len(encoded)
    return Path(out_path), events
