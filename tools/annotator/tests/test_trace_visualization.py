# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Structured trace visualization is extracted from raw bytes, never prose."""

import hashlib
import json

from tools.annotator.trace_visualization import SCHEMA, build_visualization

COLLECTOR = "collector-unix-ns"
RUNNER = "runner-monotonic-ns"


def record(table, primary, row, *, domain=COLLECTOR, event_id=None, source="collector",
           timestamp=None):
    event_id = event_id or f"{table}:{primary}"
    line = {"kind": "row", "table": table, "primary_id": primary, "row": row}
    raw = (json.dumps(line, sort_keys=True) + "\n").encode()
    return {
        "event_id": event_id, "source_id": source, "record_id": primary,
        "timestamp": str(timestamp if timestamp is not None else 0),
        "clock_domain": domain, "derivation": "fixture",
        "byte_offset": 0, "byte_length": len(raw),
        "record_sha256": hashlib.sha256(raw).hexdigest(),
    }, raw


def runner_event(payload, *, domain=RUNNER, event_id="runner:1"):
    raw = (json.dumps(payload, sort_keys=True) + "\n").encode()
    return {
        "event_id": event_id, "source_id": "runner", "record_id": event_id,
        "timestamp": str(payload.get("timestamp_ns", 0)), "clock_domain": domain,
        "derivation": "fixture", "byte_offset": 0, "byte_length": len(raw),
        "record_sha256": hashlib.sha256(raw).hexdigest(),
    }, raw


def process(pid, start, parent_pid=None, parent_start=None, *, comm="node",
            timestamp=None, key=None):
    return record("process_events", key or f"p-{pid}-{start}", {
        "pid": pid, "start_time_ns": start, "parent_pid": parent_pid,
        "parent_start_time_ns": parent_start, "comm": comm,
        "timestamp_ns": timestamp,
    })


def test_action_lanes_keep_observed_types_and_process_lifetimes():
    records = [record("actions", "a1", {"pid": 12, "process_start_time_ns": 100,
                "timestamp_ns": 200, "action_type": "FileRead"}),
               record("actions", "a2", {"pid": 12, "process_start_time_ns": 101,
                "timestamp_ns": 201, "action_type": "NetworkConnect"})]
    domain = build_visualization(records)["timeline"]["domains"][0]
    points = domain["points"]
    assert {point["process"] for point in points} == {"process:12:100", "process:12:101"}
    assert {point["type"] for point in points} == {"系统动作 · FileRead", "系统动作 · NetworkConnect"}
    assert domain["lanes_total"]["type"] == 2
    assert sum(lane["count"] for lane in domain["lanes"]["type"]) == 2


def test_process_and_detector_edges_keep_distinct_provenance():
    parent = process(100, 4000, comm="bash", timestamp=8000)
    child = process(200, 5000, 100, 4000, comm="node", timestamp=9000)
    delegation = record("delegation_records", "d-1", {
        "from_agent_id": "agent-a", "to_agent_id": "agent-b",
        "timestamp_ns": 9500, "confidence": "high",
    })
    boundary = record("subagent_boundaries", "b-1", {
        "parent_agent_id": "agent-b", "child_agent_id": "agent-c",
        "reason": "spawn",
    })
    view = build_visualization([parent, child, delegation, boundary])
    assert view["schema"] == SCHEMA
    relations = view["relations"]
    assert relations["counts"]["by_kind"] == {
        "delegation_record": 1, "process_parenthood": 1, "subagent_boundary": 1}
    by_kind = {edge["kind"]: edge for edge in relations["edges"]}
    assert by_kind["process_parenthood"]["provenance"] == "raw_process_row"
    assert by_kind["process_parenthood"]["claim"] is False
    assert by_kind["delegation_record"]["provenance"] == "detector_record"
    assert by_kind["delegation_record"]["claim"] is True
    assert by_kind["subagent_boundary"]["provenance"] == "boundary_record"
    node_ids = {node["id"] for node in relations["nodes"]}
    assert {"process:100:4000", "process:200:5000", "agent:agent-a",
            "agent:agent-b", "agent:agent-c"} <= node_ids
    for edge in relations["edges"]:
        assert edge["event_id"] in {
            parent[0]["event_id"], child[0]["event_id"], delegation[0]["event_id"],
            boundary[0]["event_id"]}
    timeline = view["timeline"]
    assert timeline["domains_total"] == 1
    domain = timeline["domains"][0]
    assert domain["id"] == COLLECTOR
    assert domain["points_total"] == 3
    assert domain["min_ns"] == "8000" and domain["max_ns"] == "9500"
    assert timeline["missing_timestamp"] == 1  # boundary has no observed timestamp


def test_summary_prose_never_creates_an_edge():
    event, raw = runner_event({"kind": "assistant_message", "timestamp_ns": 10,
                               "text": "agent-a → agent-b and parent_pid=7"})
    event["label"] = "Agent 委派"
    event["summary"] = "agent-a → agent-b"
    view = build_visualization([(event, raw)])
    assert view["relations"]["edges"] == []
    assert view["relations"]["nodes"] == []
    assert view["relations"]["empty"] is True


def test_missing_endpoints_are_explicit_placeholders():
    delegation = record("delegation_records", "d-1", {
        "from_agent_id": "agent-a", "to_agent_id": None})
    child = process(200, 5000, parent_pid=100, parent_start=None, timestamp=9000)
    boundary = record("subagent_boundaries", "b-1", {"parent_agent_id": "agent-a"})
    view = build_visualization([delegation, child, boundary])
    relations = view["relations"]
    assert relations["counts"]["unresolved_edges"] == 3
    assert all(edge["missing_endpoint"] for edge in relations["edges"])
    placeholders = [node for node in relations["nodes"] if not node["observed"]]
    assert len(placeholders) == 3
    assert all(node["missing"] for node in placeholders)
    codes = relations["issues"]["by_code"]
    assert codes["missing_delegation_endpoint"] == 1
    assert codes["missing_parent_endpoint"] == 1
    assert codes["missing_boundary_endpoint"] == 1
    assert view["timeline"]["missing_timestamp"] == 2


def test_process_cycle_is_reported_and_kept():
    one = process(1, 10, 2, 20, timestamp=100)
    two = process(2, 20, 1, 10, timestamp=200)
    view = build_visualization([one, two])
    relations = view["relations"]
    assert relations["cycles"]["count"] == 1
    assert len(relations["edges"]) == 2
    assert len(relations["cycles"]["examples"][0]) == 2


def test_pid_reuse_uses_start_time_not_pid_alone():
    first = process(7, 100, timestamp=1000)
    second = process(7, 200, timestamp=2000)
    child = process(8, 300, 7, 200, timestamp=3000)
    view = build_visualization([first, second, child])
    node_ids = {node["id"] for node in view["relations"]["nodes"]}
    assert {"process:7:100", "process:7:200", "process:8:300"} <= node_ids
    edge = next(edge for edge in view["relations"]["edges"]
                if edge["kind"] == "process_parenthood")
    assert edge["source"] == "process:7:200"
    assert edge["target"] == "process:8:300"


def test_missing_start_time_does_not_merge_reused_pids():
    one = record("process_events", "p1", {"pid": 7, "timestamp_ns": 10, "comm": "a"},
                 event_id="process_events:p1")
    two = record("process_events", "p2", {"pid": 7, "timestamp_ns": 20, "comm": "b"},
                 event_id="process_events:p2")
    view = build_visualization([one, two])
    process_nodes = [node for node in view["relations"]["nodes"]
                     if node["kind"] == "process"]
    assert len(process_nodes) == 2
    assert all(node["start_time_ns"] is None for node in process_nodes)
    assert all("event_ids" in node and node["event_ids"] for node in process_nodes)
    assert view["relations"]["issues"]["by_code"]["missing_process_start_time"] == 2


def test_clock_domains_are_separated_and_never_normalized():
    collector = record("actions", "a1", {"action_type": "FileOpen", "pid": 1,
                                         "timestamp_ns": 100})
    runner = runner_event({"event_type": "run_started", "timestamp_ns": 50,
                           "actor_id": "top"})
    view = build_visualization([collector, runner])
    domains = {domain["id"]: domain for domain in view["timeline"]["domains"]}
    assert set(domains) == {COLLECTOR, RUNNER}
    assert domains[COLLECTOR]["points"][0]["t_ns"] == "100"
    assert domains[RUNNER]["points"][0]["t_ns"] == "50"
    assert domains[COLLECTOR]["min_ns"] == domains[COLLECTOR]["max_ns"] == "100"
    assert domains[RUNNER]["min_ns"] == domains[RUNNER]["max_ns"] == "50"


def test_nanosecond_precision_survives_above_ieee754_integer_range():
    base = 2**53
    first = record("actions", "a1", {"timestamp_ns": base}, event_id="a1")
    second = record("actions", "a2", {"timestamp_ns": base + 1}, event_id="a2")
    view = build_visualization([first, second])
    domain = view["timeline"]["domains"][0]
    assert domain["min_ns"] == str(base)
    assert domain["max_ns"] == str(base + 1)
    assert domain["span_ns"] == "1"
    assert [(point["t_ns"], point["offset_ns"]) for point in domain["points"]] == [
        (str(base), "0"), (str(base + 1), "1")]
    assert isinstance(domain["points"][0]["t_ns"], str)


def test_release_declared_placeholder_timestamp_is_not_plotted():
    event, raw = runner_event({"kind": "task_brief", "agent_index": 0, "text": "brief"},
                              domain="release-declared")
    event["timestamp"] = "0"
    view = build_visualization([(event, raw)])
    timeline = view["timeline"]
    assert timeline["domains"] == []
    assert timeline["timestamped_total"] == 0
    assert timeline["unsupported_domain"] == 1


def test_large_task_is_bounded_with_explicit_totals():
    records = []
    for index in range(13000):
        records.append(runner_event(
            {"kind": "assistant_message", "timestamp_ns": 10**15 + index},
            event_id=f"runner:{index}"))
    view = build_visualization(records, max_points=50)
    timeline = view["timeline"]
    domain = timeline["domains"][0]
    assert timeline["timestamped_total"] == 13000
    assert domain["points_total"] == 13000
    assert domain["points_shown"] == 50
    assert domain["truncated"] is True
    assert domain["points"][0]["offset_ns"] == "0"
    assert int(domain["points"][-1]["t_ns"]) == 10**15 + 12999
    assert domain["kinds"] == [{"kind": "assistant_message", "label": "采集记录",
                                "count": 13000}]

    rows = [process(1000 + index, 10 + index, 999 + index, 9 + index, timestamp=index)
            for index in range(1000)]
    graph = build_visualization(rows, max_nodes=20, max_edges=10)["relations"]
    assert graph["counts"]["nodes_total"] == 1001
    assert graph["counts"]["edges_total"] == 1000  # root parent is a placeholder edge
    assert graph["counts"]["nodes_shown"] <= 20
    assert graph["counts"]["edges_shown"] <= 10
    assert graph["truncated"] is True
    assert len(graph["nodes"]) == graph["counts"]["nodes_shown"]


def test_scenario_identifiers_are_blinded_in_display_labels():
    event = process(1, 2, comm="scenario-secret", timestamp=3)
    edge = record("delegation_records", "d1", {
        "from_agent_id": "scenario-secret", "to_agent_id": "agent-b",
        "timestamp_ns": 4})
    view = build_visualization([event, edge], identifiers=["scenario-secret"])
    serialized = json.dumps(view, ensure_ascii=False)
    assert "scenario-secret" not in serialized
    assert "blinded-" in serialized


def test_empty_input_has_safe_zero_counts():
    view = build_visualization([])
    assert view["relations"]["empty"] is True
    assert view["relations"]["counts"]["nodes_total"] == 0
    assert view["timeline"]["events_total"] == 0
    assert view["timeline"]["domains"] == []
