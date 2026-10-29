# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Every ``agents[].role_file`` must resolve to a real role brief.

The runtime uses ``role_file`` as a label rather than injected instructions, but a
dangling reference still makes the scenario metadata untrue and breaks the
annotation path (``ai_scenario_annotations`` reads ``env/scenarios/roles``). These
checks keep the reference set, the dataset copy and the env mirror in one state.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "datasets/FuxiBench/scenarios"
DATASET_ROLES = ROOT / "datasets/FuxiBench/roles"
ENV_ROLES = ROOT / "env/scenarios/roles"


def referenced_roles() -> dict[str, list[str]]:
    refs: dict[str, list[str]] = {}
    for path in sorted(SCENARIOS.glob("*.json")):
        scenario = json.loads(path.read_text(encoding="utf-8"))
        for agent in scenario.get("agents") or []:
            name = agent.get("role_file")
            if name:
                refs.setdefault(name, []).append(scenario["id"])
    return refs


def test_the_reference_set_is_not_empty():
    # A guard over zero references would pass while checking nothing.
    assert len(referenced_roles()) >= 10


def test_every_referenced_role_file_exists():
    missing = {name: cases for name, cases in referenced_roles().items()
               if not (DATASET_ROLES / name).is_file()}
    assert missing == {}, f"role files referenced but absent: {sorted(missing)}"


def test_role_directories_are_identical():
    dataset = {p.name for p in DATASET_ROLES.glob("*.md")}
    mirror = {p.name for p in ENV_ROLES.glob("*.md")}
    assert dataset == mirror, (
        f"only in dataset: {sorted(dataset - mirror)}; only in env: {sorted(mirror - dataset)}")
    for name in sorted(dataset):
        assert (DATASET_ROLES / name).read_bytes() == (ENV_ROLES / name).read_bytes(), name


@pytest.mark.parametrize("name", sorted(referenced_roles()))
def test_role_file_is_a_real_brief(name: str):
    text = (DATASET_ROLES / name).read_text(encoding="utf-8")
    assert text.startswith("You are "), f"{name} must open with the role statement"
    assert "Responsibilities:" in text, f"{name} must list responsibilities"
    assert re.search(r"^Output\b", text, re.MULTILINE), f"{name} must state the expected output"
    assert len(text) > 200, f"{name} is too thin to describe a role"
