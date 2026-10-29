# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Blinded scenario brief as annotation evidence.

The brief states the instruction the agent received. It is required to judge
whether a designed attack was attempted; it is *not* ground truth and excludes
labels, attack types and scenario identifiers. Only the per-agent task text,
mode and agent index are copied.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from compare.tools.dataset_release import validate_release
from tools.annotator.blinding import blind_identifiers

SOURCE_ID = "task_brief"
SOURCE_KIND = "raw_capture"
PRODUCER = "release-task-brief/1"


def extract_task_brief(release_root: Path, scenario_id: str, *,
                       identifiers=()) -> tuple[Path, list[dict]] | None:
    """Return a one-line-per-agent brief source, or None when absent."""
    release_root = Path(release_root)
    validate_release(release_root)
    scenario_path = release_root / "scenarios" / f"{scenario_id}.json"
    if not scenario_path.is_file():
        return None
    try:
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError("invalid scenario brief JSON") from exc
    if not isinstance(scenario, dict):
        raise ValueError("scenario brief must be a JSON object")
    agents = scenario.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError("scenario brief has no agents")
    mode = scenario.get("mode", "unknown")
    if not isinstance(mode, str) or not mode.strip():
        raise ValueError("scenario brief has no mode")
    lines = []
    for index, agent in enumerate(agents):
        if not isinstance(agent, dict) or not isinstance(agent.get("task"), str) \
                or not agent["task"].strip():
            raise ValueError("scenario agent brief has no task")
        body = json.dumps({"agent_index": index, "mode": mode, "task": agent["task"]},
                          ensure_ascii=False, sort_keys=True, allow_nan=False)
        text, _ = blind_identifiers(body.encode("utf-8"), identifiers)
        lines.append({"kind": "task_brief", "agent_index": index, "text": text})
    return scenario_path, lines


def write_task_brief(out_path: Path, lines: list[dict]) -> tuple[Path, list[dict]]:
    """Write brief lines as JSONL with anchors; the caller freezes the file."""
    if not lines:
        raise ValueError("task brief has no events")
    events, offset = [], 0
    with Path(out_path).open("wb") as stream:
        for line in lines:
            encoded = (json.dumps(line, ensure_ascii=False, sort_keys=True,
                                  allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
            stream.write(encoded)
            event_id = f"{SOURCE_ID}:agent-{line['agent_index']}"
            events.append({
                "event_id": event_id,
                "source_id": SOURCE_ID,
                "record_id": f"agent-{line['agent_index']}",
                "timestamp": "0",
                "clock_domain": "release-declared",
                "derivation": f"release scenario task brief agent {line['agent_index']}",
                "byte_offset": offset,
                "byte_length": len(encoded),
                "record_sha256": hashlib.sha256(encoded).hexdigest(),
            })
            offset += len(encoded)
    return Path(out_path), events
