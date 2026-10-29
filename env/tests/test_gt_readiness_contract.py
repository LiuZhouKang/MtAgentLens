# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Every ground-truth record must be usable, and say so truthfully.

``quality.paper_ready`` is a record-level claim: the labels agree with the
two-type taxonomy, nothing is excluded from the detection denominators, every
``role_file`` resolves, multi-agent scenarios declare a tmux layout, the
description speaks the current taxonomy, the scenario bytes still match the
frozen prompt manifest, and the annotation carries an evidence note.

It is **not** a human sign-off claim: ``metadata.json`` still records
``human_annotation: false`` and the AI-consensus provenance. These checks make
the mechanical part reproducible instead of a remembered assertion.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "datasets/FuxiBench"
SCENARIOS = DATASET / "scenarios"
GROUND_TRUTH = DATASET / "ground_truth"
ROLES = DATASET / "roles"
LAYOUTS = {"even-horizontal", "even-vertical", "main-horizontal", "main-vertical", "tiled"}
LEGACY_MARKER = re.compile(r"^Type\s*[ABC]\b")


def records() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(GROUND_TRUTH.glob("*/*.json")):
        if path.parent.name.startswith("_"):
            continue
        gt = json.loads(path.read_text(encoding="utf-8"))
        out[gt["scenario_id"]] = gt
    return out


def manifest() -> dict[str, dict]:
    entries = json.loads((DATASET / "prompt_inputs/manifest.json").read_text(encoding="utf-8"))
    return {entry["scenario_id"]: entry for entry in entries["entries"]}


def readiness_problems(scenario_id: str, gt: dict, entry: dict | None) -> list[str]:
    problems: list[str] = []
    quality = gt.get("quality") or {}
    if quality.get("detection_excluded") is True:
        problems.append("detection_excluded")
    label, attack_type = gt.get("label"), gt.get("attack_type")
    if not ((label == "benign" and attack_type == "benign")
            or (label == "attack" and attack_type in {"A", "B"})):
        problems.append("label/attack_type disagree")
    if gt.get("taxonomy_version") != "fuxibench-2type/1":
        problems.append("missing taxonomy marker")

    scenario = json.loads((SCENARIOS / f"{scenario_id}.json").read_text(encoding="utf-8"))
    for agent in scenario.get("agents") or []:
        name = agent.get("role_file")
        if name and not (ROLES / name).is_file():
            problems.append(f"role_file missing: {name}")
    if scenario.get("mode") == "agent_teams":
        if (scenario.get("tmux") or {}).get("layout") not in LAYOUTS:
            problems.append("no tmux layout")
    if LEGACY_MARKER.match(scenario.get("description", "")):
        problems.append("retired Type marker in description")

    if entry is None:
        problems.append("no prompt-manifest entry")
    else:
        digest = hashlib.sha256((DATASET / entry["source_scenario_path"]).read_bytes()).hexdigest()
        if digest != entry.get("source_scenario_sha256"):
            problems.append("scenario bytes drifted from the frozen manifest")

    if not quality.get("notes"):
        problems.append("no annotation evidence note")
    behaviors = gt.get("expected_behaviors") or []
    if label == "benign" and behaviors:
        problems.append("benign record carries expected_behaviors")
    if label == "attack" and not behaviors:
        problems.append("attack record has no expected_behaviors")
    return problems


CASES = records()
ENTRIES = manifest()


def test_corpus_is_complete():
    assert len(CASES) == 93
    assert set(CASES) == set(ENTRIES)


@pytest.mark.parametrize("scenario_id", sorted(CASES))
def test_record_satisfies_the_readiness_contract(scenario_id: str):
    assert readiness_problems(scenario_id, CASES[scenario_id], ENTRIES.get(scenario_id)) == []


@pytest.mark.parametrize("scenario_id", sorted(CASES))
def test_record_declares_itself_ready(scenario_id: str):
    quality = CASES[scenario_id].get("quality") or {}
    assert quality.get("paper_ready") is True, (
        f"{scenario_id}: passes the readiness contract but does not say so")


def test_human_signoff_is_still_not_claimed():
    metadata = json.loads((DATASET / "metadata.json").read_text(encoding="utf-8"))
    assert metadata.get("human_annotation") is False
