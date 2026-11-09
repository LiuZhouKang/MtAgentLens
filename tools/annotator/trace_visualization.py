# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Pure structure extraction for the offline trace workbench.

The workbench never derives graph edges from human-readable summary strings.
This module reads the already checksum-verified raw event bytes and keeps only
structured fields that the collector froze: process identity (pid plus start
time), raw parent links, detector delegation records and subagent boundaries.
Every graph element carries the event id that a click must open.

Timestamps stay decimal nanosecond strings.  The browser uses ``BigInt`` for
offsets so 19-digit Unix values never pass through an IEEE-754 double.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from tools.annotator.blinding import blind_identifiers

SCHEMA = "mtagentlens.trace-visualization/1"
MAX_NODES = 400
MAX_EDGES = 800
MAX_POINTS = 1200
MAX_LANES = 40
MAX_ISSUE_EXAMPLES = 50

_TS_KEYS = ("timestamp_ns", "observed_at_ns", "created_at", "started_at_ns")
_OBSERVED_DOMAIN = re.compile(r"-(?:unix|monotonic)-ns$")
_INTEGER = re.compile(r"-?\d+")

_TABLE_LABELS = {
    "process_events": "进程事件",
    "delegation_records": "委派记录",
    "subagent_boundaries": "子代理边界",
    "agent_identities": "Agent 身份",
    "provisional_identities": "临时身份",
    "identity_process_beliefs": "身份进程信念",
    "responsibility_alerts": "责任告警",
    "responsibility_claims": "责任声明",
    "responsibility_tracebacks": "责任回溯",
    "policy_evaluations": "策略评估",
    "intents": "模型意图",
    "message_bindings": "消息绑定",
    "tls_sessions": "TLS 会话",
    "actions": "系统动作",
    "runs": "运行摘要",
}
_RUNNER_LABELS = {
    "run_started": "运行开始",
    "launch_attempt": "启动尝试",
    "process_started": "进程启动",
    "process_exited": "进程退出",
    "run_finished": "运行结束",
    "launch_failed": "启动失败",
    "process_timeout": "进程超时",
}
_EDGE_DEFINITIONS = {
    "process_parenthood": ("原始进程父子记录", "raw_process_row", False),
    "delegation_record": ("检测器委派记录（声明）", "detector_record", True),
    "subagent_boundary": ("子代理边界记录", "boundary_record", True),
}
_DOMAIN_LABELS = {
    "collector-unix-ns": "采集器 Unix 纳秒",
    "runner-monotonic-ns": "Runner 单调纳秒",
}


def _integer(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INTEGER.fullmatch(value):
        return int(value)
    return None


def _timestamp(row, domain):
    """Return an observed integer timestamp from raw fields, never a fallback."""
    if not isinstance(row, dict):
        return None
    for key in _TS_KEYS:
        value = _integer(row.get(key))
        if value is not None:
            return value
    if isinstance(domain, str) and domain.endswith("-ns"):
        return _integer(row.get("timestamp"))
    return None


def _text(value, limit=120):
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _even_sample(items, limit):
    """Deterministic, range-covering sample; never silently returns a prefix."""
    if limit <= 0:
        return []
    if len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[len(items) // 2]]
    step = (len(items) - 1) / (limit - 1)
    sample = [items[round(index * step)] for index in range(limit)]
    # Rounding can repeat an item; fill from the end so the sample stays exact.
    seen, unique = set(), []
    for item in sample:
        marker = id(item)
        if marker not in seen:
            seen.add(marker)
            unique.append(item)
    for item in items:
        if len(unique) >= limit:
            break
        if id(item) not in seen:
            seen.add(id(item))
            unique.append(item)
    return unique[:limit]


def _process_identity(row):
    pid = _integer(row.get("pid"))
    start = _integer(row.get("start_time_ns"))
    if start is None:
        start = _integer(row.get("process_start_time_ns"))
    return pid, start


def _process_key(pid, start):
    return f"process:{pid}:{start}" if start is not None else f"process:{pid}:unknown"


def _process_label(row, pid, start):
    comm = _text(row.get("comm") or row.get("process_name") or "进程", 48)
    return f"{comm} · PID {pid}" + (f" @ {start}" if start is not None else " @ 开始时间未知")


def _agent_key(agent_id):
    return f"agent:{agent_id}"


def _parse(raw):
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


class _Extractor:
    def __init__(self, identifiers, *, max_nodes, max_edges, max_points, max_lanes):
        self.identifiers = tuple(value for value in identifiers
                                if isinstance(value, str) and value)
        self.max_nodes, self.max_edges = max_nodes, max_edges
        self.max_points, self.max_lanes = max_points, max_lanes
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.process_rows: list[tuple[dict, dict]] = []
        self.points: dict[str, list[dict]] = {}
        self.events_total = 0
        self.missing_timestamp = 0
        self.missing_clock_domain = 0
        self.unsupported_domain = 0
        self.issue_counts: Counter[str] = Counter()
        self.issue_examples: list[dict] = []

    def blind(self, value):
        if not value or not self.identifiers:
            return value
        return blind_identifiers(str(value).encode("utf-8"), self.identifiers)[0]

    def issue(self, code, event_id, detail):
        self.issue_counts[code] += 1
        if len(self.issue_examples) < MAX_ISSUE_EXAMPLES:
            self.issue_examples.append({"code": code, "event_id": event_id,
                                        "detail": _text(detail, 160)})

    def add_event(self, event, raw):
        self.events_total += 1
        event_id = str(event.get("event_id") or "")
        parsed = _parse(raw)
        if parsed is None:
            self.issue("unparseable_event", event_id, "raw bytes are not a JSON object")
            self.missing_timestamp += 1
            return
        domain = event.get("clock_domain")
        table = parsed.get("table") if parsed.get("kind") == "row" else None
        row = parsed.get("row") if isinstance(parsed.get("row"), dict) else {}
        source = parsed if table is None else row
        timestamp = _timestamp(source, domain)

        if table == "process_events":
            self.process_rows.append((event, row))
        elif table == "delegation_records":
            self._delegation_edge(event, row)
        elif table == "subagent_boundaries":
            self._boundary_edge(event, row)

        self._timeline_point(event, parsed, table, row, timestamp, domain)

    def _timeline_point(self, event, parsed, table, row, timestamp, domain):
        if not isinstance(domain, str) or not domain:
            self.missing_clock_domain += 1
            self.missing_timestamp += 1
            return
        if not _OBSERVED_DOMAIN.search(domain):
            self.unsupported_domain += 1
            return
        if timestamp is None:
            self.missing_timestamp += 1
            return
        if table is not None:
            kind = table
            label = _TABLE_LABELS.get(table, table)
            actor = row.get("agent_id") or row.get("actor_id")
            pid, start = _process_identity(row)
            if pid is None:
                process = None
            elif start is None:
                process = f"process:{pid}:unknown"
            else:
                process = f"process:{pid}:{start}"
        else:
            kind = str(parsed.get("event_type") or parsed.get("kind") or "record")
            label = _RUNNER_LABELS.get(kind, "运行事件" if parsed.get("event_type") else "采集记录")
            actor = parsed.get("actor_id")
            process = parsed.get("process_key")
            if isinstance(process, dict):
                pid = _integer(process.get("pid"))
                process = f"process:{pid}:unknown" if pid is not None else None
            elif not isinstance(process, str) or not process:
                process = None
        point = {
            "event_id": str(event.get("event_id") or ""),
            "t_ns": str(timestamp),
            "kind": kind,
            "label": label,
            "type": (label + " · " + self.blind(_text(row["action_type"], 80))
                     if table == "actions" and isinstance(row.get("action_type"), str)
                     and row["action_type"] else label),
            "source": str(event.get("source_id") or ""),
            "actor": self.blind(actor) if isinstance(actor, (str, int)) and actor != "" else None,
            "process": process,
        }
        if point["actor"] is not None:
            point["actor"] = str(point["actor"])
        self.points.setdefault(domain, []).append(point)

    def _node(self, key, *, kind, label, event_id, row, domain, observed=True):
        node = self.nodes.get(key)
        if node is None:
            start_time = _integer(row.get("start_time_ns"))
            timestamp = _timestamp(row, domain)
            node = {
                "id": key, "kind": kind, "label": self.blind(label),
                "observed": observed, "missing": not observed,
                "event_ids": [], "event_count": 0,
                "pid": _integer(row.get("pid")),
                "start_time_ns": str(start_time) if start_time is not None else None,
                "clock_domain": domain,
                "timestamp_ns": str(timestamp) if timestamp is not None else None,
            }
            self.nodes[key] = node
        if event_id and event_id not in node["event_ids"] and len(node["event_ids"]) < 8:
            node["event_ids"].append(event_id)
        if event_id:
            node["event_count"] += 1
        return node

    def _placeholder(self, event_id, role, label):
        key = f"unresolved:{role}:{event_id}"
        return self._node(key, kind="unresolved", label=label, event_id=event_id,
                          row={}, domain=None, observed=False)

    def _agent_node(self, agent_id, event_id, domain, *, observed=True):
        agent_id = self.blind(_text(agent_id, 120))
        return self._node(_agent_key(agent_id), kind="agent", label=agent_id,
                          event_id=event_id, row={}, domain=domain, observed=observed)

    def _edge(self, *, event_id, source, target, kind, timestamp, domain,
              missing_endpoint=False, confidence=None):
        label, provenance, claim = _EDGE_DEFINITIONS[kind]
        self.edges.append({
            "id": f"{kind}:{event_id}:{source}->{target}",
            "event_id": event_id, "source": source, "target": target,
            "kind": kind, "provenance": provenance, "claim": claim,
            "label": label, "confidence": confidence,
            "timestamp_ns": str(timestamp) if timestamp is not None else None,
            "clock_domain": domain, "missing_endpoint": missing_endpoint,
        })

    def _delegation_edge(self, event, row):
        event_id = str(event.get("event_id") or "")
        domain = event.get("clock_domain")
        timestamp = _timestamp(row, domain)
        source_id, target_id = row.get("from_agent_id"), row.get("to_agent_id")
        missing = not (isinstance(source_id, str) and source_id and
                       isinstance(target_id, str) and target_id)
        if isinstance(source_id, str) and source_id:
            source = self._agent_node(source_id, event_id, domain)["id"]
        else:
            source = self._placeholder(event_id, "delegation-from",
                                       "委派方未记录")["id"]
            self.issue("missing_delegation_endpoint", event_id, "from_agent_id is absent")
        if isinstance(target_id, str) and target_id:
            target = self._agent_node(target_id, event_id, domain)["id"]
        else:
            target = self._placeholder(event_id, "delegation-to",
                                       "接收方未记录")["id"]
            self.issue("missing_delegation_endpoint", event_id, "to_agent_id is absent")
        self._edge(event_id=event_id, source=source, target=target,
                   kind="delegation_record", timestamp=timestamp, domain=domain,
                   missing_endpoint=missing, confidence=row.get("confidence"))

    def _boundary_edge(self, event, row):
        event_id = str(event.get("event_id") or "")
        domain = event.get("clock_domain")
        timestamp = _timestamp(row, domain)
        parent_id, child_id = row.get("parent_agent_id"), row.get("child_agent_id")
        missing = not (isinstance(parent_id, str) and parent_id and
                       isinstance(child_id, str) and child_id)
        if isinstance(parent_id, str) and parent_id:
            source = self._agent_node(parent_id, event_id, domain)["id"]
        else:
            source = self._placeholder(event_id, "boundary-parent",
                                       "父 Agent 未记录")["id"]
            self.issue("missing_boundary_endpoint", event_id, "parent_agent_id is absent")
        if isinstance(child_id, str) and child_id:
            target = self._agent_node(child_id, event_id, domain)["id"]
        else:
            target = self._placeholder(event_id, "boundary-child",
                                       "子 Agent 未记录")["id"]
            self.issue("missing_boundary_endpoint", event_id, "child_agent_id is absent")
        self._edge(event_id=event_id, source=source, target=target,
                   kind="subagent_boundary", timestamp=timestamp, domain=domain,
                   missing_endpoint=missing)

    def _finalize_processes(self):
        for event, row in self.process_rows:
            event_id = str(event.get("event_id") or "")
            domain = event.get("clock_domain")
            pid, start = _process_identity(row)
            if pid is None:
                self.issue("missing_process_pid", event_id, "process row has no integer pid")
                continue
            if start is None:
                # A pid alone is reusable; keep this row isolated instead of
                # merging unrelated processes.
                key = f"process:{pid}:unknown:{event_id}"
                self.issue("missing_process_start_time", event_id,
                           "process row has no integer start_time_ns")
            else:
                key = _process_key(pid, start)
            self._node(key, kind="process", label=_process_label(row, pid, start),
                       event_id=event_id, row=row, domain=domain)
        for event, row in self.process_rows:
            event_id = str(event.get("event_id") or "")
            pid, start = _process_identity(row)
            if pid is None:
                continue
            child = (f"process:{pid}:unknown:{event_id}" if start is None
                     else _process_key(pid, start))
            parent_pid = _integer(row.get("parent_pid"))
            if parent_pid is None:
                continue
            parent_start = _integer(row.get("parent_start_time_ns"))
            if parent_start is None:
                parent = self._placeholder(
                    event_id, "process-parent",
                    f"父进程 PID {parent_pid}（开始时间未记录）")["id"]
                self.issue("missing_parent_endpoint", event_id,
                           "parent_start_time_ns is absent; pid reuse cannot be excluded")
                missing = True
            else:
                parent = _process_key(parent_pid, parent_start)
                if parent not in self.nodes:
                    parent = self._placeholder(
                        event_id, "process-parent",
                        f"父进程 PID {parent_pid}（本次证据未捕获）")["id"]
                    self.issue("missing_parent_endpoint", event_id,
                               "parent process row is absent from this bundle")
                    missing = True
                else:
                    missing = False
            domain = event.get("clock_domain")
            self._edge(event_id=event_id, source=parent, target=child,
                       kind="process_parenthood",
                       timestamp=_timestamp(row, domain), domain=domain,
                       missing_endpoint=missing)

    def _cycles(self):
        adjacency = {key: [] for key in self.nodes}
        for edge in self.edges:
            adjacency.setdefault(edge["source"], []).append(edge["target"])
        index, low, on_stack, stack, components = {}, {}, set(), [], []
        counter = 0
        for root in adjacency:
            if root in index:
                continue
            work = [(root, 0)]
            while work:
                node, position = work[-1]
                if position == 0:
                    index[node] = low[node] = counter
                    counter += 1
                    stack.append(node)
                    on_stack.add(node)
                recurse = False
                neighbours = adjacency.get(node, [])
                while position < len(neighbours):
                    neighbour = neighbours[position]
                    position += 1
                    if neighbour not in index:
                        work[-1] = (node, position)
                        work.append((neighbour, 0))
                        recurse = True
                        break
                    if neighbour in on_stack:
                        low[node] = min(low[node], index[neighbour])
                if recurse:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    component = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    if len(component) > 1 or node in adjacency.get(node, []):
                        components.append(sorted(component))
        components.sort(key=lambda item: (-len(item), item))
        return components

    def finish(self):
        self._finalize_processes()
        cycles = self._cycles()
        edges_sorted = sorted(
            self.edges,
            key=lambda edge: (edge["timestamp_ns"] is None,
                              int(edge["timestamp_ns"] or 0), edge["id"]),
        )
        shown_edges, used_nodes = [], set()
        for edge in _even_sample(edges_sorted, self.max_edges):
            endpoints = used_nodes | {edge["source"], edge["target"]}
            if len(endpoints) > self.max_nodes:
                continue
            shown_edges.append(edge)
            used_nodes = endpoints
        node_order = sorted(self.nodes.values(),
                            key=lambda node: (node["kind"] != "process", node["id"]))
        for node in node_order:
            if len(used_nodes) >= self.max_nodes:
                break
            if node["id"] not in used_nodes:
                used_nodes.add(node["id"])
        shown_nodes = [self.nodes[key] for key in sorted(used_nodes)]
        edge_kinds = Counter(edge["kind"] for edge in self.edges)
        unresolved = sum(1 for edge in self.edges if edge["missing_endpoint"])
        relations = {
            "nodes": shown_nodes, "edges": shown_edges,
            "counts": {
                "nodes_total": len(self.nodes), "nodes_shown": len(shown_nodes),
                "edges_total": len(self.edges), "edges_shown": len(shown_edges),
                "by_kind": dict(sorted(edge_kinds.items())),
                "unresolved_edges": unresolved,
                "missing_timestamp_nodes": sum(1 for node in self.nodes.values()
                                               if node["timestamp_ns"] is None),
                "missing_timestamp_edges": sum(1 for edge in self.edges
                                               if edge["timestamp_ns"] is None),
            },
            "truncated": len(shown_edges) < len(self.edges) or len(shown_nodes) < len(self.nodes),
            "empty": not self.nodes and not self.edges,
            "cycles": {"count": len(cycles),
                       "examples": cycles[:10]},
            "issues": {"count": sum(self.issue_counts.values()),
                       "by_code": dict(sorted(self.issue_counts.items())),
                       "examples": self.issue_examples},
        }
        return {
            "schema": SCHEMA,
            "generated_from": "verified_bundle_bytes",
            "relations": relations,
            "timeline": self._timeline(),
        }

    def _timeline(self):
        domains = []
        timestamped = 0
        for domain in sorted(self.points):
            points = sorted(self.points[domain],
                            key=lambda point: (int(point["t_ns"]), point["event_id"]))
            timestamped += len(points)
            low, high = int(points[0]["t_ns"]), int(points[-1]["t_ns"])
            span = high - low
            shown = _even_sample(points, self.max_points)
            kind_counts = Counter(point["kind"] for point in points)
            kind_labels = {}
            for point in points:
                kind_labels.setdefault(point["kind"], point["label"])
            lanes = {}
            for dimension in ("source", "actor", "process", "type"):
                counts = Counter(point[dimension] for point in points if point.get(dimension))
                ordered = sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))
                selected = ordered[:self.max_lanes]
                lanes[dimension] = [{
                    "key": str(key), "label": _text(key, 80), "count": count,
                    "shown": sum(1 for point in shown if point.get(dimension) == key),
                } for key, count in selected]
            for point in shown:
                point["offset_ns"] = str(int(point["t_ns"]) - low)
                point["sampled"] = True
            domains.append({
                "id": domain,
                "label": _DOMAIN_LABELS.get(domain, domain),
                "min_ns": str(low), "max_ns": str(high), "span_ns": str(span),
                "points_total": len(points), "points_shown": len(shown),
                "truncated": len(shown) < len(points),
                "kinds": [{"kind": kind, "label": kind_labels.get(kind, kind),
                           "count": count}
                          for kind, count in sorted(kind_counts.items(),
                                                    key=lambda item: (-item[1], item[0]))],
                "lanes": lanes,
                "lanes_total": {dimension: len({point[dimension] for point in points
                                                if point.get(dimension)})
                                for dimension in ("source", "actor", "process", "type")},
                "points": shown,
            })
        return {
            "events_total": self.events_total,
            "timestamped_total": timestamped,
            "missing_timestamp": self.missing_timestamp,
            "missing_clock_domain": self.missing_clock_domain,
            "unsupported_domain": self.unsupported_domain,
            "domains_total": len(domains),
            "truncated": any(domain["truncated"] for domain in domains),
            "domains": domains,
        }


def build_visualization(records, *, identifiers=(), max_nodes=MAX_NODES,
                        max_edges=MAX_EDGES, max_points=MAX_POINTS,
                        max_lanes=MAX_LANES):
    """Build bounded graph and timeline metadata from verified raw records.

    ``records`` yields ``(event_metadata, raw_bytes)`` pairs.  Only structured
    JSON fields are interpreted; prose summaries are never inspected.
    """
    extractor = _Extractor(identifiers, max_nodes=max_nodes, max_edges=max_edges,
                           max_points=max_points, max_lanes=max_lanes)
    for event, raw in records:
        if isinstance(event, dict) and isinstance(raw, (bytes, bytearray)):
            extractor.add_event(event, bytes(raw))
    return extractor.finish()
