"""Load and validate scenario JSON files."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from mtagentlens_benchmark.loader.schema import SCENARIO_SCHEMA_V2
from mtagentlens_benchmark.types import (
    AgentSpec,
    BypassDimensions,
    BypassStrategy,
    RiskLevel,
    ScenarioConfig,
    ScenarioMode,
)

FIXTURE_PLACEHOLDER = "{{FIXTURE_DIR}}"


def load_scenario(scenario_path: Path, fixtures_root: Path, timeout_seconds: int = 300) -> ScenarioConfig:
    """Load and validate a single scenario JSON file."""
    raw = json.loads(scenario_path.read_text(encoding="utf-8"))
    jsonschema.validate(raw, SCENARIO_SCHEMA_V2)

    scenario_id = raw["id"]
    # Allow scenarios to specify a neutral fixture directory name
    # (prevents Claude from inferring the scenario type from the path)
    fixture_dir_name = raw.get("fixture_dir", scenario_id)
    fixtures_dir = fixtures_root / fixture_dir_name
    agents = [_resolve_agent(a, fixtures_dir) for a in raw["agents"]]

    bypass = None
    if "bypass" in raw and raw["bypass"].get("technique") != "none":
        bp = raw["bypass"]
        dims_raw = bp.get("dimensions", {})
        dims = BypassDimensions(**dims_raw) if dims_raw else None
        bypass = BypassStrategy(
            technique=bp.get("technique", "none"),
            framework=bp.get("framework", ""),
            dimensions=dims,
            prompt_template=bp.get("prompt_template", ""),
        )

    tmux = raw.get("tmux", {})

    return ScenarioConfig(
        id=scenario_id,
        mode=ScenarioMode(raw["mode"]),
        description=raw.get("description", ""),
        agents=agents,
        fixtures_dir=fixtures_dir,
        tmux_layout=tmux.get("layout", "even-horizontal"),
        leader_role_file=tmux.get("leader_role_file", ""),
        pre_run_script=raw.get("pre_run_script"),
        timeout_seconds=raw.get("timeout", timeout_seconds),
        permission_mode=raw.get("permission_mode", "default"),
        model=raw.get("model", "haiku"),
        effort=raw.get("effort", "low"),
        bypass_strategy=bypass,
        category=raw.get("category", ""),
        is_baseline=raw.get("is_baseline", False),
        stability_score=raw.get("stability", {}).get("score", 0.0),
        risk_level=_parse_risk_level(raw.get("risk_level")),
    )


def load_all_scenarios(scenarios_dir: Path, fixtures_root: Path, **kwargs) -> list[ScenarioConfig]:
    """Load all scenario JSON files from categorized subdirectories."""
    scenarios = []
    for cat_dir in sorted(scenarios_dir.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name in ("fixtures", "archive", "_archive"):
            continue
        # Flat files in category dir
        for p in sorted(cat_dir.glob("*.json")):
            scenarios.append(load_scenario(p, fixtures_root, **kwargs))
        # Nested subdirectories (e.g. multi_agent_attack/agent_teams/)
        for sub_dir in sorted(cat_dir.iterdir()):
            if sub_dir.is_dir() and not sub_dir.name.startswith("_"):
                for p in sorted(sub_dir.glob("*.json")):
                    scenarios.append(load_scenario(p, fixtures_root, **kwargs))
    # Fallback: also try flat structure for backward compatibility
    if not scenarios:
        for p in sorted(scenarios_dir.glob("*.json")):
            scenarios.append(load_scenario(p, fixtures_root, **kwargs))
    return scenarios


def _resolve_agent(raw_agent: dict, fixtures_dir: Path) -> AgentSpec:
    task = raw_agent["task"]
    fixtures_abs = str(fixtures_dir.resolve())
    task = task.replace(FIXTURE_PLACEHOLDER, fixtures_abs)
    return AgentSpec(role_file=raw_agent["role_file"], task=task)


def _parse_risk_level(raw: str | None) -> RiskLevel | None:
    """Parse risk_level from JSON, defaulting to None (unclassified)."""
    if not raw:
        return None
    try:
        return RiskLevel(raw)
    except ValueError:
        return None
