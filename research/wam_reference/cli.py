# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Read a finite synthetic witness fixture and emit canonical audit JSON."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .model import (ActionFact, BridgeFact, CaptureStatus, EvidenceTier,
                    IntentFact, NegativeWitness, ProcessKey, WitnessLedger,
                    canonical_result, reconstruct)


def _process(obj):
    if obj is None:
        return None
    return ProcessKey(int(obj["pid"]), int(obj["start_time_ns"]),
                      obj.get("namespace_id", "host"))


def _intent(obj):
    return IntentFact(intent_id=obj["intent_id"], timestamp_ns=int(obj["timestamp_ns"]),
                      process=_process(obj.get("process")), resource=obj.get("resource"),
                      tool_call_id=obj.get("tool_call_id"),
                      clock_domain=obj.get("clock_domain", "monotonic_boot"),
                      tier=EvidenceTier(obj.get("tier", "observed")),
                      capture=CaptureStatus(obj.get("capture", "healthy")))


def _action(obj):
    return ActionFact(action_id=obj["action_id"], timestamp_ns=int(obj["timestamp_ns"]),
                      process=_process(obj.get("process")), resource=obj.get("resource"),
                      tool_call_id=obj.get("tool_call_id"),
                      clock_domain=obj.get("clock_domain", "monotonic_boot"),
                      tier=EvidenceTier(obj.get("tier", "observed")),
                      capture=CaptureStatus(obj.get("capture", "healthy")))


def _witness(obj, cls):
    return cls(evidence_id=obj["evidence_id"], intent_id=obj["intent_id"],
               action_id=obj["action_id"], producer=obj["producer"],
               tier=EvidenceTier(obj.get("tier", "hypothesized")))


def from_dict(obj):
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    parents = tuple((_process(child), _process(parent))
                    for child, parent in obj.get("process_parent_edges", []))
    return WitnessLedger(
        intents=tuple(_intent(item) for item in obj.get("intents", [])),
        actions=tuple(_action(item) for item in obj.get("actions", [])),
        bridges=tuple(_witness(item, BridgeFact) for item in obj.get("bridges", [])),
        exclusions=tuple(_witness(item, NegativeWitness)
                         for item in obj.get("exclusions", [])),
        process_parent_edges=parents,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", type=Path, help="JSON fixture (not trusted telemetry)")
    group.add_argument("--example", action="store_true")
    args = parser.parse_args()
    if args.example:
        fixture = {
            "intents": [
                {"intent_id": "invoke-a", "timestamp_ns": 100, "process": {"pid": 10, "start_time_ns": 7}, "resource": "/tmp/report"},
                {"intent_id": "invoke-b", "timestamp_ns": 101, "process": {"pid": 10, "start_time_ns": 7}, "resource": "/tmp/report"}
            ],
            "actions": [
                {"action_id": "open-1", "timestamp_ns": 110, "process": {"pid": 10, "start_time_ns": 7}, "resource": "/tmp/report"},
                {"action_id": "open-2", "timestamp_ns": 120, "process": {"pid": 10, "start_time_ns": 7}, "resource": "/tmp/report"}
            ],
            "bridges": [{"evidence_id": "e-1", "intent_id": "invoke-a", "action_id": "open-1", "producer": "trusted_harness", "tier": "observed"}]
        }
    else:
        fixture = json.loads(args.input.read_text(encoding="utf-8"))
    print(canonical_result(reconstruct(from_dict(fixture))))


if __name__ == "__main__":
    main()
