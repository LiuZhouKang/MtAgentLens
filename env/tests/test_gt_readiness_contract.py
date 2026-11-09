# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Check executable inputs and consistency of the current review declarations.

These checks validate metadata structure and cross-record consistency. They do
not independently establish that human review took place.
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

    annotation = gt.get("annotation") or {}
    if not annotation.get("reviewed_at") or not annotation.get("codebook_version"):
        problems.append("missing structured review metadata")
    if annotation.get("human_review_status") != "completed":
        problems.append("review declaration is incomplete")
    if not annotation.get("human_annotators"):
        problems.append("missing declared reviewers")
    if quality.get("reviewers") != annotation.get("human_annotators"):
        problems.append("reviewer declarations disagree")
    if annotation.get("collector_output_consulted") is not False:
        problems.append("missing independent-input declaration")
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


def test_annotation_metadata_matches_record_declarations():
    metadata = json.loads((DATASET / "metadata.json").read_text(encoding="utf-8"))
    assert isinstance(metadata.get("human_annotation"), bool)
    assert metadata.get("human_annotators")
    for gt in CASES.values():
        annotation = gt.get("annotation") or {}
        assert annotation.get("human_annotation") == metadata["human_annotation"]
        assert annotation.get("human_annotators") == metadata["human_annotators"]


@pytest.mark.parametrize("field", ["reviewed_at", "codebook_version", "human_annotators",
                                  "human_review_status", "collector_output_consulted"])
def test_missing_review_declarations_are_reported(field):
    import copy
    sid = next(iter(CASES))
    gt = copy.deepcopy(CASES[sid])
    gt["annotation"].pop(field)
    assert readiness_problems(sid, gt, ENTRIES[sid])
