"""Tests for scenario loader and schema validation."""
import json
import tempfile
from pathlib import Path
import pytest
from jsonschema import ValidationError

from mtagentlens_benchmark.loader.scenario_loader import load_scenario
from mtagentlens_benchmark.types import ScenarioMode


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

        with pytest.raises(ValidationError):
            load_scenario(scenario_path, fixtures)


def test_load_all_scenarios_finds_many():
    """Verify load_all_scenarios finds real scenarios."""
    from mtagentlens_benchmark.loader.scenario_loader import load_all_scenarios

    scenarios_dir = Path(__file__).resolve().parents[1] / "scenarios"
    fixtures_root = scenarios_dir / "fixtures"
    rejected = {}
    scenarios = load_all_scenarios(scenarios_dir, fixtures_root, invalid_scenarios=rejected)
    assert rejected == {}, "active source directories must contain executable configs only"
    assert len(scenarios) >= 17, f"Expected >=17, got {len(scenarios)}"
    ids = [s.id for s in scenarios]
    assert "prompt-injection" in ids
    assert "network-anomaly" in ids
    assert len(ids) == len(set(ids)), "draft or archived scenarios must not duplicate published ids"


def test_incomplete_descriptors_fail_closed_and_drafts_are_excluded(tmp_path):
    from mtagentlens_benchmark.loader.scenario_loader import load_all_scenarios
    (tmp_path / "category").mkdir()
    (tmp_path / "_drafts").mkdir()
    (tmp_path / "category" / "metadata.json").write_text('{"id":"metadata","behavioral_only":true}')
    (tmp_path / "_drafts" / "broken.json").write_text('not json')
    rejected = {}
    assert load_all_scenarios(tmp_path, tmp_path / "fixtures", invalid_scenarios=rejected) == []
    assert list(rejected) == [tmp_path / "category" / "metadata.json"]
    with pytest.raises(ValueError, match="Non-executable scenario descriptors"):
        load_all_scenarios(tmp_path, tmp_path / "fixtures")


def test_draft_move_preserves_bytes_and_published_scope():
    import hashlib
    root = Path(__file__).resolve().parents[2]
    receipt = json.loads((root / "env/scenarios/_drafts/MOVED-DESCRIPTORS-2026-10-21.json").read_text())
    published = json.loads((root / "datasets/FuxiBench/prompt_inputs/manifest.json").read_text())
    published_paths = {entry["source_scenario_path"] for entry in published["entries"]}
    gt_root = root / "datasets/FuxiBench/ground_truth"
    published_ids = {json.loads(path.read_text())["scenario_id"] for path in gt_root.rglob("*.json")
                     if not any(part.startswith("_") for part in path.relative_to(gt_root).parts)}
    assert len(receipt["moves"]) == 14
    for move in receipt["moves"]:
        assert move["previous_path"] not in published_paths
        assert Path(move["previous_path"]).stem not in published_ids
        assert not (root / move["previous_path"]).exists()
        assert hashlib.sha256((root / move["draft_path"]).read_bytes()).hexdigest() == move["sha256"]
