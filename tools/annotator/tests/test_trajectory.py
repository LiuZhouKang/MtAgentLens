# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Trajectory extraction copies assistant text/tool calls only and blinds ids."""

import hashlib
import json
from pathlib import Path

from tools.annotator.tests.test_runner_import import inputs  # noqa: F401
from tools.annotator.trajectory import extract_trajectory, write_trajectory


def log_with_message(tmp_path):
    message = {
        "message_id": "message-1", "start_time": 1234,
        "text_content": "I will read scenario run-1.",
        "json_content": json.dumps({"file_path": "/tmp/scenario/docs.md"}),
        "sse_events": [
            {"event": "content_block_start", "parsed_data": {
                "type": "content_block_start", "index": 0,
                "content_block": {"type": "text", "text": ""}}},
            {"event": "content_block_delta", "parsed_data": {
                "type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": "hello"}}},
            {"event": "content_block_start", "parsed_data": {
                "type": "content_block_start", "index": 1,
                "content_block": {"type": "tool_use", "name": "Read", "input": {}}}},
            {"event": "content_block_delta", "parsed_data": {
                "type": "content_block_delta", "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"file_path":"/tmp/scenario/docs.md"}'}}},
        ],
    }
    log = tmp_path / "collector.log"
    lines = ["not json", json.dumps({"data": {"message_id": "ignored"}}),
             json.dumps({"data": message})]
    log.write_text("\n".join(lines) + "\n")
    return log


def test_extract_blinds_and_keeps_tool_calls(tmp_path):
    log = log_with_message(tmp_path)
    _, lines = extract_trajectory(log, identifiers=("scenario", "run-1"))
    assert len(lines) == 1
    blob = json.dumps(lines[0], ensure_ascii=False)
    assert "blinded-" in blob
    assert "run-1" not in blob
    assert "Read" in blob and "file_path" in blob
    out, events = write_trajectory(tmp_path / "trajectory.jsonl", lines)
    assert events[0]["event_id"] == "trajectory:message-1"
    assert events[0]["timestamp"] == "1234"
    raw = out.read_bytes()
    assert hashlib.sha256(raw[events[0]["byte_offset"]:
                              events[0]["byte_offset"] + events[0]["byte_length"]]).hexdigest() == events[0]["record_sha256"]


def test_missing_or_empty_log_is_optional(tmp_path):
    assert extract_trajectory(tmp_path / "absent.log") is None
    empty = tmp_path / "empty.log"
    empty.write_text("not json\n")
    assert extract_trajectory(empty) is None


def test_import_auto_discovers_the_sibling_trace_log(tmp_path, inputs):  # noqa: F811
    from tools.annotator.runner_import import import_runner_bundle
    spec, ledger, _ = inputs
    collector = ledger.parent / "collector"
    collector.mkdir()
    log = log_with_message(collector)
    bundle = import_runner_bundle(tmp_path / "bundle", release_root=spec["release_root"],
                                  ledger=ledger, codebook_version=spec["binding"]["codebook_version"])
    assert "trajectory" in bundle["sources"]
    trajectory_events = [event for event in bundle["events"]
                         if event["source_id"] == "trajectory"]
    assert len(trajectory_events) == 1
    source = tmp_path / "bundle" / bundle["sources"]["trajectory"]["path"]
    raw = source.read_bytes()[trajectory_events[0]["byte_offset"]:
                              trajectory_events[0]["byte_offset"] + trajectory_events[0]["byte_length"]]
    assert b"Read" in raw
