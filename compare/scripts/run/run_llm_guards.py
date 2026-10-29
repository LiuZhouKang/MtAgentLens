#!/usr/bin/env python3
"""运行 LLM Guard 系列工具（LLM Guard, Meta Prompt Guard, Vigil）在 30 个冻结场景上的评估。

用法: cd <project_root> && python3 compare/scripts/run_llm_guards.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]  # compare/scripts/ → mtagentlens/
COMPARE = PROJECT / "compare"
sys.path.insert(0, str(PROJECT))
RESULTS = COMPARE / "results/paper"
FINDINGS_PATH = RESULTS / "findings.jsonl"
FROZEN_DIR = PROJECT / "datasets/FuxiBench/scenarios"

GUARD_TOOLS = ["llm-guard", "meta-prompt-guard", "vigil"]

# Load tool metadata from lockfile
LOCKFILE = COMPARE / "tool-lock.json"
with LOCKFILE.open() as f:
    lock_data = json.load(f)

# Find our guard tools
guard_tools = [t for t in lock_data["tools"] if t["id"] in GUARD_TOOLS]
print(f"Found {len(guard_tools)} guard tools: {[t['id'] for t in guard_tools]}")

# Import adapter loading
from compare.tools.base import AdapterCase, ToolMetadata
from compare.tools.registry import instantiate_adapter

# Get scenario list from tool_status.csv
import csv
scenarios = []
with (RESULTS / "tool_status.csv").open(newline="") as f:
    for row in csv.DictReader(f):
        scenarios.append(row["scenario_id"])
print(f"Scenarios: {len(scenarios)}")

# Load existing findings to avoid duplicates
existing_keys = set()
if FINDINGS_PATH.exists():
    with FINDINGS_PATH.open() as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                existing_keys.add((rec["tool_id"], rec["scenario_id"]))

# Run each guard tool
all_findings = []
for tool_def in guard_tools:
    tool_id = tool_def["id"]
    print(f"\n{'='*60}")
    print(f"Running {tool_def['name']} ({tool_id})...")

    metadata = ToolMetadata(
        id=tool_id,
        name=tool_def["name"],
        tool_class=tool_def["class"],
        source_url=tool_def["source_url"],
        version=tool_def["version"],
        license=tool_def["license"],
        install=tuple(tool_def["install"]),
        adapter=tool_def["adapter"],
        input_modes=tuple(tool_def["input_modes"]),
        score_scope=tool_def["score_scope"],
        inclusion_rationale=tool_def["inclusion_rationale"],
    )

    try:
        adapter = instantiate_adapter(metadata)
        print(f"  Adapter loaded: {type(adapter).__module__}.{type(adapter).__name__}")
    except Exception as e:
        print(f"  ✗ Failed to load adapter: {e}")
        import traceback
        traceback.print_exc()
        continue

    detected = 0
    for i, sid in enumerate(scenarios):
        # Skip if already in findings
        if (tool_id, sid) in existing_keys:
            continue

        scenario_dir = FROZEN_DIR / sid
        fixture_dir = scenario_dir / "fixture" if (scenario_dir / "fixture").is_dir() else scenario_dir

        if not fixture_dir.is_dir():
            continue

        case = AdapterCase(
            scenario_id=sid,
            dataset_name="FuxiBench",
            fixture_dir=fixture_dir,
        )

        output_dir = COMPARE / "results" / "raw" / tool_id / sid
        try:
            result = adapter.run(case, output_dir)
            if result.findings:
                detected += 1
                for finding in result.findings:
                    finding_dict = {
                        "tool_id": tool_id,
                        "tool_name": tool_def["name"],
                        "scenario_id": sid,
                        "dataset_name": "FuxiBench",
                        "finding_id": finding.finding_id,
                        "category": finding.category,
                        "severity": finding.severity,
                        "confidence": finding.confidence,
                        "resource": finding.resource,
                        "command": finding.command,
                        "text_span": finding.text_span,
                        "evidence": finding.evidence,
                        "boundary_type": finding.boundary_type,
                    }
                    all_findings.append(finding_dict)
                    existing_keys.add((tool_id, sid))
        except Exception as e:
            print(f"  ⚠ {sid}: {e}")

    print(f"  → {detected}/{len(scenarios)} scenarios with findings ({len(all_findings)} total findings)")

# Append new findings to findings.jsonl
if all_findings:
    with FINDINGS_PATH.open("a") as f:
        for fd in all_findings:
            f.write(json.dumps(fd, ensure_ascii=False) + "\n")
    print(f"\n✓ {len(all_findings)} new findings appended to {FINDINGS_PATH}")
else:
    print("\n✓ No new findings (all already present)")

# Summary
from collections import Counter
tool_counts = Counter(f["tool_id"] for f in all_findings)
for tid, cnt in tool_counts.items():
    scenarios_hit = len(set(f["scenario_id"] for f in all_findings if f["tool_id"] == tid))
    print(f"  {tid}: {scenarios_hit} scenarios, {cnt} findings")
