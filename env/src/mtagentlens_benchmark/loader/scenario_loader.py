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


def scenario_paths(scenarios_dir: Path) -> list[Path]:
    """Enumerate source configs, excluding assets, archives and unpublished drafts."""
    excluded = {"fixtures", "archive", "bench", "roles"}
    candidates = [*scenarios_dir.glob("*.json"), *scenarios_dir.glob("*/*.json"), *scenarios_dir.glob("*/*/*.json")]
    return sorted(path for path in candidates if not any(
        part.startswith("_") or part in excluded
        for part in path.relative_to(scenarios_dir).parts[:-1]
    ))


def load_all_scenarios(
    scenarios_dir: Path, fixtures_root: Path, *,
    invalid_scenarios: dict[Path, str] | None = None, **kwargs,
) -> list[ScenarioConfig]:
    """Load executable configs; reject incomplete descriptors by default.

    An inspection caller may supply ``invalid_scenarios`` to collect every
    rejected descriptor explicitly. Execution callers must keep the strict
    default: missing mode, roles or agents are never invented.
    """
    scenarios = []
    failures = {}
    seen = set()
    for path in scenario_paths(scenarios_dir):
        try:
            scenario = load_scenario(path, fixtures_root, **kwargs)
        except (jsonschema.ValidationError, json.JSONDecodeError, OSError) as error:
            failures[path] = str(error)
            continue
        if scenario.id in seen:
            raise ValueError(f"Duplicate executable scenario id: {scenario.id} ({path})")
        seen.add(scenario.id)
        scenarios.append(scenario)
    if failures:
        if invalid_scenarios is None:
            details = "; ".join(f"{path}: {message.splitlines()[0]}" for path, message in failures.items())
            raise ValueError(f"Non-executable scenario descriptors ({len(failures)}): {details}")
        invalid_scenarios.update(failures)
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
