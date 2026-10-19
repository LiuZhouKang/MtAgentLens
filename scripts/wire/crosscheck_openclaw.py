#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Export OpenClaw's own tool-call assertions from its SQLite database.

OpenClaw writes `trajectory_runtime_events` rows itself. That record is the only
source of truth in this repository that is independent of MtAgentLens: it says
what the agent *asserts* it did, while the eBPF side sees what the process
*actually* sent. This script freezes the asserted side so the Rust cross-check
test can run offline.

Read-only. Never writes to the agent database.

Usage:
    python3 scripts/wire/crosscheck_openclaw.py \
        --sqlite ~/.openclaw/agents/main/agent/openclaw-agent.sqlite \
        --session-key p05-crosscheck-20261017-230843 \
        --since 2026-10-17T23:08:00 --until 2026-10-17T23:09:30 \
        --out collector/tests/fixtures/wire/openclaw-intent-crosscheck/asserted-tools.json
"""
from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def parse_ts(value: str) -> int:
    """Accept ISO-8601 (local time if no offset) and return epoch milliseconds."""
    parsed = datetime.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return int(parsed.timestamp() * 1000)


def normalized_key(value: str) -> str:
    return value if value.startswith("agent:") else f"agent:main:{value}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", required=True)
    parser.add_argument("--session-key", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    db_path = Path(args.sqlite).expanduser()
    if not db_path.exists():
        print(f"ERROR: sqlite not found: {db_path}", file=sys.stderr)
        return 2

    since = parse_ts(args.since)
    until = parse_ts(args.until)
    key = normalized_key(args.session_key)

    # Read-only URI; the agent keeps this database open.
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    records: list[dict] = []
    seen_ids: set[str] = set()
    try:
        # 1. The trajectory table carries the asserted sessionKey and tool id,
        #    but its `args` field is ellipsized ("text(\"…CK\")"). Use it for
        #    identity, never for arguments.
        session_ids: set[str] = set()
        trajectory_calls: dict[str, dict] = {}
        for row in conn.execute(
            "SELECT event_json, created_at FROM trajectory_runtime_events "
            "ORDER BY created_at"
        ):
            created_at = int(row["created_at"])
            if created_at < since or created_at > until:
                continue
            try:
                event = json.loads(row["event_json"])
            except ValueError:
                continue
            if event.get("sessionKey") != key:
                continue
            if event.get("sessionId"):
                session_ids.add(event["sessionId"])
            if event.get("type") == "tool.call":
                data = event.get("data") or {}
                trajectory_calls[data.get("toolCallId")] = {
                    "name": data.get("name"),
                    "args_elided": data.get("args"),
                }

        if not session_ids:
            print(f"ERROR: no trajectory rows for {key} in window", file=sys.stderr)
            return 1

        # 2. The transcript stores the complete assistant message, with the
        #    tool-call arguments in full. Some rows are zstd-compressed.
        placeholders = ",".join("?" for _ in session_ids)
        rows = conn.execute(
            "SELECT session_id, seq, event_json, event_zstd, created_at "
            f"FROM transcript_events WHERE session_id IN ({placeholders}) "
            "ORDER BY created_at, seq",
            tuple(session_ids),
        )
        for row in rows:
            created_at = int(row["created_at"])
            if created_at < since or created_at > until:
                continue
            payload = row["event_json"]
            if payload is None and row["event_zstd"] is not None:
                decoded = subprocess.run(
                    ["zstd", "-d", "-c"],
                    input=row["event_zstd"],
                    capture_output=True,
                )
                if decoded.returncode != 0:
                    print(
                        f"ERROR: cannot decompress transcript row "
                        f"{row['session_id']}:{row['seq']}",
                        file=sys.stderr,
                    )
                    return 1
                payload = decoded.stdout.decode("utf-8")
            if not payload:
                continue
            try:
                event = json.loads(payload)
            except ValueError:
                continue
            message = event.get("message") or {}
            if message.get("role") != "assistant":
                continue
            for item in message.get("content") or []:
                if item.get("type") != "toolCall":
                    continue
                call_id = item.get("id")
                if call_id in seen_ids:
                    continue
                seen_ids.add(call_id)
                trajectory = trajectory_calls.get(call_id, {})
                records.append(
                    {
                        "toolCallId": call_id,
                        "name": item.get("name"),
                        "args": item.get("arguments"),
                        "ts": event.get("timestamp"),
                        "sessionKey": key,
                        "source": "openclaw-agent.sqlite:transcript_events",
                        "trajectory_name": trajectory.get("name"),
                        "trajectory_args_elided": trajectory.get("args_elided"),
                    }
                )
    finally:
        conn.close()

    if not records:
        print(
            f"ERROR: no complete toolCall rows for {key} between "
            f"{args.since} and {args.until}",
            file=sys.stderr,
        )
        return 1

    # The two agent-owned tables must agree on identity. If they do not, the
    # asserted side is not trustworthy and the cross-check must not proceed.
    for record in records:
        if record["trajectory_name"] and record["trajectory_name"] != record["name"]:
            print(
                f"ERROR: name mismatch between transcript and trajectory for "
                f"{record['toolCallId']}: {record['name']} vs "
                f"{record['trajectory_name']}",
                file=sys.stderr,
            )
            return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
    print(f"wrote {len(records)} asserted tool call(s) to {out}")
    for record in records:
        print(f"  {record['ts']} {record['name']} id={record['toolCallId']}")
        print(f"    args={json.dumps(record['args'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
