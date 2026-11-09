# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Workbench API exposes bounded structured graph/timeline metadata."""

import hashlib
import json
import re
from pathlib import Path

from tools.annotator.evidence import build_bundle
from tools.annotator.records import RecordStore
from tools.annotator.tests.test_evidence import fixture_inputs
from tools.annotator.tests.test_records import setup  # noqa: F401
from tools.annotator.tests.test_workbench import serve, task_id  # noqa: F401


def add_collector_source(tmp_path, spec, rows):
    payload = b"".join((json.dumps(row, sort_keys=True) + "\n").encode() for row in rows)
    path = tmp_path / "collector.jsonl"
    path.write_bytes(payload)
    events, offset = [], 0
    for row in rows:
        encoded = (json.dumps(row, sort_keys=True) + "\n").encode()
        events.append({
            "event_id": f"{row['table']}:{row['primary_id']}", "source_id": "collector",
            "record_id": str(row["primary_id"]), "timestamp": str(
                row["row"].get("timestamp_ns", 0)),
            "clock_domain": "collector-unix-ns",
            "derivation": "collector sqlite " + row["table"] + " row",
            "byte_offset": offset, "byte_length": len(encoded),
            "record_sha256": hashlib.sha256(encoded).hexdigest(),
        })
        offset += len(encoded)
    spec["sources"]["collector"] = {
        "path": path, "kind": "raw_capture", "producer": "collector-sqlite-export/1"}
    spec["events"] = spec["events"] + events
    spec["actors"] = ["agent-a", "agent-b"]
    return spec


def test_view_exposes_structured_relations_without_filling_annotation(setup, serve, tmp_path):
    root = tmp_path / "visual"
    root.mkdir()
    spec = fixture_inputs(root)
    rows = [
        {"kind": "row", "table": "process_events", "primary_id": "p1",
         "row": {"pid": 100, "start_time_ns": 4000, "comm": "bash",
                 "timestamp_ns": 8000}},
        {"kind": "row", "table": "process_events", "primary_id": "p2",
         "row": {"pid": 200, "start_time_ns": 5000, "parent_pid": 100,
                 "parent_start_time_ns": 4000, "comm": "node", "timestamp_ns": 9000}},
        {"kind": "row", "table": "delegation_records", "primary_id": "d1",
         "row": {"from_agent_id": "agent-a", "to_agent_id": "agent-b",
                 "timestamp_ns": 9500, "confidence": "high"}},
    ]
    spec = add_collector_source(root, spec, rows)
    build_bundle(root / "bundle", **spec)
    store_path = root / "records.sqlite"
    RecordStore(store_path).register_bundle(root / "bundle")
    with serve(store_path=store_path) as request:
        name = task_id(request)
        status, view = request("/api/tasks/" + name)
        assert status == 200
        assert view["own"] is None  # graph metadata never pre-fills annotations
        visualization = view["visualization"]
        assert visualization["generated_from"] == "verified_bundle_bytes"
        relations = visualization["relations"]
        assert relations["counts"]["by_kind"] == {
            "delegation_record": 1, "process_parenthood": 1}
        kinds = {edge["kind"]: edge for edge in relations["edges"]}
        assert kinds["process_parenthood"]["provenance"] == "raw_process_row"
        assert kinds["delegation_record"]["provenance"] == "detector_record"
        assert kinds["delegation_record"]["claim"] is True
        assert kinds["process_parenthood"]["event_id"] == "process_events:p2"
        # Event summaries remain the existing API surface.
        assert all(event["label"] and event["summary"] for event in view["events"])
        # Every graph click target resolves through the existing evidence API.
        for edge in relations["edges"]:
            status, preview = request(
                "/api/tasks/" + name + "/events/" + edge["event_id"])
            assert status == 200
            assert preview["event"]["event_id"] == edge["event_id"]
        domain = visualization["timeline"]["domains"][0]
        assert domain["id"] == "collector-unix-ns"
        assert domain["min_ns"] == "8000" and domain["max_ns"] == "9500"
        assert all(isinstance(point["t_ns"], str) for point in domain["points"])
        status, vendor = request("/vendor/dagre.min.js", raw=True)
        assert status == 200 and vendor.startswith(b"(function") and len(vendor) > 100_000


def test_thirteen_thousand_events_are_bounded_but_counted(setup, serve, tmp_path):
    root = tmp_path / "visual"
    root.mkdir()
    spec = fixture_inputs(root)
    raw = spec["sources"]["runner"]["path"]
    lines, events, offset = [], [], 0
    count = 13000
    for index in range(count):
        line = (json.dumps({"kind": "assistant_message", "timestamp_ns": 10**15 + index,
                            "text": "step %d" % index}) + "\n").encode()
        lines.append(line)
        events.append({
            "event_id": "event-%d" % index, "source_id": "runner",
            "record_id": "r%d" % index, "timestamp": str(10**15 + index),
            "clock_domain": "runner-monotonic-ns", "derivation": "fixture",
            "byte_offset": offset, "byte_length": len(line),
            "record_sha256": hashlib.sha256(line).hexdigest(),
        })
        offset += len(line)
    raw.write_bytes(b"".join(lines))
    spec["events"] = events
    build_bundle(root / "bundle", **spec)
    store_path = root / "records.sqlite"
    RecordStore(store_path).register_bundle(root / "bundle")
    with serve(store_path=store_path) as request:
        status, view = request("/api/tasks/" + task_id(request))
        assert status == 200
        assert len(view["events"]) == count
        timeline = view["visualization"]["timeline"]
        assert timeline["events_total"] == count
        assert timeline["timestamped_total"] == count
        domain = timeline["domains"][0]
        assert domain["points_total"] == count
        assert domain["points_shown"] <= 1200
        assert domain["truncated"] is True
        assert domain["points"][0]["t_ns"] == str(10**15)
        assert domain["points"][-1]["t_ns"] == str(10**15 + count - 1)


def test_vendored_bundles_carry_version_license_and_hashes():
    vendor = Path(__file__).resolve().parents[1] / "static" / "vendor"
    notice = (vendor / "THIRD-PARTY.md").read_text(encoding="utf-8")
    for name, size, license_name, marker in [
        ("dagre.min.js", 100_000, "LICENSE.dagre", "Copyright (c) 2012-2014 Chris Pettitt"),
        ("cytoscape.min.js", 300_000, "LICENSE.cytoscape", "Cytoscape Consortium"),
        ("echarts.min.js", 900_000, "LICENSE.echarts", "Apache License"),
    ]:
        bundle = vendor / name
        assert not bundle.is_symlink()
        assert len(bundle.read_bytes()) > size
        assert marker in (vendor / license_name).read_text(encoding="utf-8")
        assert name in notice
        assert hashlib.sha256(bundle.read_bytes()).hexdigest() in notice
    assert "dagre 0.8.5" in notice and "MIT" in notice
    assert "Cytoscape.js 3.30.4" in notice and "MIT" in notice
    assert "Apache ECharts 5.5.1" in notice and "Apache-2.0" in notice
    # The browser must never depend on a runtime CDN for these bundles.
    page = (Path(__file__).resolve().parents[1] / "static" / "workbench.html")
    text = page.read_text(encoding="utf-8")
    assert "/vendor/cytoscape.min.js" in text
    assert "/vendor/echarts.min.js" in text
    assert not re.search(r'src="https?://', text)
