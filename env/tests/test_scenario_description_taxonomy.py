# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Scenario descriptions must not speak the retired Type A/B/C vocabulary.

FuxiBench moved to a two-type taxonomy (``A`` = indirect/poisoned instruction,
``B`` = multi-agent/handoff) and retired the old ``Type A/B/C`` labels. Several
descriptions kept the old marker, so a scenario declared ``A`` still opened with
"Type C ...". The record's labels are the evidence; the prose has to follow it,
which means naming the behaviour instead of a retired type.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = ROOT / "datasets/FuxiBench/scenarios"
LEGACY_MARKER = re.compile(r"^Type\s*[ABC]\b")


def scenarios() -> list[tuple[Path, dict]]:
    return [(path, json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(SCENARIO_DIR.glob("*.json"))]


def test_the_scenario_corpus_is_present():
    # A guard over an empty directory would pass while checking nothing.
    assert len(scenarios()) == 93


@pytest.mark.parametrize("path,scenario", scenarios(), ids=lambda value: getattr(value, "name", ""))
def test_description_does_not_use_the_retired_type_marker(path: Path, scenario: dict):
    description = scenario.get("description", "")
    match = LEGACY_MARKER.search(description)
    assert match is None, (
        f"{path.name}: description still opens with the retired taxonomy marker "
        f"{match.group(0)!r} while attack_type={scenario.get('attack_type')!r}; "
        "name the observed behaviour instead")
