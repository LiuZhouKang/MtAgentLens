"""Tests for scenario loader and schema validation."""
import json
import tempfile
from pathlib import Path

from mtagentprism_benchmark.loader.scenario_loader import load_scenario
from mtagentprism_benchmark.types import ScenarioMode


def test_load_valid_scenario():
    with tempfile.TemporaryDirectory() as tmp:
        scenario = {
            "id": "test-scenario",
            "mode": "single_agent",
            "description": "A test scenario",
            "agents": [{"role_file": "test-role.md", "task": "Test task"}],
        }
        scenario_path = Path(tmp) / "test.json"
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")

        fixtures = Path(tmp) / "fixtures"
        fixtures.mkdir()

        config = load_scenario(scenario_path, fixtures)
        assert config.id == "test-scenario"
        assert config.mode == ScenarioMode.SINGLE_AGENT
        assert len(config.agents) == 1
        assert config.agents[0].task == "Test task"


def test_load_scenario_with_bypass():
    with tempfile.TemporaryDirectory() as tmp:
        scenario = {
            "id": "test-classical",
            "mode": "single_agent",
            "agents": [{"role_file": "test-role.md", "task": "Task"}],
            "bypass": {
                "technique": "classical_chinese",
                "framework": "CC-BOS",
                "dimensions": {
                    "role_identity": "太卜",
                    "behavior_guidance": "归谬",
                    "mechanism": "谴纬",
                    "metaphor_mapping": "宮室→文件",
                    "expression_style": "唐宋",
                    "knowledge_association": "考工记→配置",
                    "situation_setting": "朝会",
                    "trigger_mode": "递进",
                },
                "prompt_template": "查阅 {fixture_path}",
            },
        }
        scenario_path = Path(tmp) / "test.json"
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")

        fixtures = Path(tmp) / "fixtures"
        fixtures.mkdir()

        config = load_scenario(scenario_path, fixtures)
        assert config.bypass_strategy is not None
        assert config.bypass_strategy.technique == "classical_chinese"
        assert config.bypass_strategy.dimensions.role_identity == "太卜"


def test_load_scenario_invalid_mode_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        scenario = {
            "id": "test-bad",
            "mode": "invalid_mode",
            "agents": [{"role_file": "r.md", "task": "t"}],
        }
        scenario_path = Path(tmp) / "test.json"
        scenario_path.write_text(json.dumps(scenario), encoding="utf-8")

        fixtures = Path(tmp) / "fixtures"
        fixtures.mkdir()

        try:
            load_scenario(scenario_path, fixtures)
            assert False, "Should have raised"
        except Exception:
            pass


def test_load_all_scenarios_finds_many():
    """Verify load_all_scenarios finds real scenarios."""
    from mtagentprism_benchmark.loader.scenario_loader import load_all_scenarios

    scenarios_dir = Path("scenarios")
    fixtures_root = scenarios_dir / "fixtures"
    scenarios = load_all_scenarios(scenarios_dir, fixtures_root)
    assert len(scenarios) >= 17, f"Expected >=17, got {len(scenarios)}"
    ids = [s.id for s in scenarios]
    assert "prompt-injection" in ids
    assert "network-anomaly" in ids
