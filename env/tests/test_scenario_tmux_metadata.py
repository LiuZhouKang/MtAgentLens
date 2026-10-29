# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Multi-agent scenarios must declare their tmux layout in the current shape.

The loader reads ``raw["tmux"]["layout"]``; a top-level ``tmux_layout`` string is the
retired v1 shape and is silently ignored, so a scenario could carry a layout that
never takes effect. These checks pin one explicit form for every multi-agent
scenario and forbid the retired key anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "datasets/FuxiBench/scenarios"
LAYOUTS = {"even-horizontal", "even-vertical", "main-horizontal", "main-vertical", "tiled"}


def scenarios() -> list[tuple[Path, dict]]:
    return [(path, json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(SCENARIOS.glob("*.json"))]


def test_the_corpus_is_present():
    assert len(scenarios()) == 93


@pytest.mark.parametrize("path,scenario", scenarios(), ids=lambda value: getattr(value, "name", ""))
def test_retired_top_level_layout_key_is_gone(path: Path, scenario: dict):
    assert "tmux_layout" not in scenario, (
        f"{path.name}: top-level tmux_layout is ignored by the loader; "
        "declare tmux.layout instead")


@pytest.mark.parametrize("path,scenario", scenarios(), ids=lambda value: getattr(value, "name", ""))
def test_multi_agent_scenarios_declare_a_layout(path: Path, scenario: dict):
    if scenario.get("mode") != "agent_teams":
        return
    tmux = scenario.get("tmux")
    assert isinstance(tmux, dict), f"{path.name}: multi-agent scenario needs a tmux object"
    layout = tmux.get("layout")
    assert layout in LAYOUTS, f"{path.name}: unsupported tmux layout {layout!r}"
