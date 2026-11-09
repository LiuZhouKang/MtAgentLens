"""Scene catalog tests: only design materials, never GT or category metadata."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.annotator.scene_catalog import load_scenarios

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FUXIBENCH_ROOT = PROJECT_ROOT / "datasets" / "FuxiBench"
FORBIDDEN_KEYS = {
    "annotation",
    "attack_type",
    "category",
    "ground_truth",
    "is_baseline",
    "label",
    "label_annotation",
    "quality",
    "risk_level",
    "variant_of",
}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_dataset(root: Path, *, scenario: dict | None = None) -> Path:
    dataset = root / "FuxiBench"
    payload = {
        "id": "scene-a",
        "mode": "single_agent",
        "description": "只读核对 fixture，再写一份摘要。",
        "agents": [{"role_file": "developer.md", "task": "读取 {{FIXTURE_DIR}}/docs.md"}],
        "fixture_dir": "ops/fixture-a",
        "timeout": 120,
        "permission_mode": "bypassPermissions",
        "model": "haiku",
        "effort": "low",
        "pre_run_script": "bench/scene-a/start.sh",
        "category": "prompt_injection",
        "attack_type": "A",
        "label": "attack",
        "risk_level": "High",
        "is_baseline": False,
        "variant_of": "scene-parent",
    }
    if scenario is not None:
        payload = scenario
    _write_json(dataset / "scenarios" / "scene-a.json", payload)
    (dataset / "roles").mkdir(parents=True, exist_ok=True)
    (dataset / "roles" / "developer.md").write_text("你是开发者。角色说明：只读核查。\n", encoding="utf-8")
    fixture = dataset / "fixtures" / "ops" / "fixture-a"
    fixture.mkdir(parents=True, exist_ok=True)
    (fixture / "docs.md").write_text("fixture 说明\n", encoding="utf-8")
    (fixture / ".env.example").write_text("TOKEN=decoy\n", encoding="utf-8")
    bench = dataset / "bench" / "scene-a"
    bench.mkdir(parents=True, exist_ok=True)
    (bench / "start.sh").write_text("#!/usr/bin/env bash\ntrue\n", encoding="utf-8")
    _write_json(
        dataset / "ground_truth" / "prompt_injection" / "scene-a.json",
        {
            "scenario_id": "scene-a",
            "label": "attack",
            "attack_type": "A",
            "category": "prompt_injection",
            "quality": {"review_status": "completed"},
        },
    )
    return dataset


def _keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(value)
        for item in value.values():
            found |= _keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= _keys(item)
    return found


def test_catalog_loads_only_design_sources_and_strips_metadata(tmp_path):
    dataset = build_dataset(tmp_path)
    scenes = load_scenarios(dataset)
    assert [scene["scenario_id"] for scene in scenes] == ["scene-a"]
    scene = scenes[0]
    assert set(scene) == {"scenario_id", "sources"}
    kinds = {source["kind"] for source in scene["sources"]}
    assert kinds == {"scenario", "role", "fixture", "bench"}
    paths = {source["path"] for source in scene["sources"]}
    assert paths == {
        "scenarios/scene-a.json",
        "roles/developer.md",
        "fixtures/ops/fixture-a/docs.md",
        "fixtures/ops/fixture-a/.env.example",
        "bench/scene-a/start.sh",
    }

    scenario_source = next(source for source in scene["sources"] if source["kind"] == "scenario")
    payload = json.loads(scenario_source["text"])
    assert not (_keys(payload) & FORBIDDEN_KEYS)
    assert "category" not in scenario_source["text"]
    assert "attack_type" not in scenario_source["text"]
    assert "ground_truth" not in json.dumps(scene)
    assert "review_status" not in json.dumps(scene)
    assert "decoy" not in json.dumps(
        [source for source in scene["sources"] if source["kind"] == "scenario"]
    )


def test_catalog_hashes_are_stable_and_match_text(tmp_path):
    dataset = build_dataset(tmp_path)
    first = load_scenarios(dataset)
    second = load_scenarios(dataset)
    assert first == second
    for source in first[0]["sources"]:
        if source["path"].endswith(".md") or source["path"].endswith(".sh"):
            assert source["sha256"] == hashlib.sha256(source["text"].encode("utf-8")).hexdigest()
        else:
            assert len(source["sha256"]) == 64


def test_catalog_handles_missing_optional_sources(tmp_path):
    scenario = {
        "id": "scene-a",
        "mode": "single_agent",
        "description": "missing materials are skipped",
        "agents": [{"role_file": "missing.md", "task": "read it"}],
        "fixture_dir": "ops/does-not-exist",
        "pre_run_script": "bench/does-not-exist/start.sh",
    }
    dataset = build_dataset(tmp_path, scenario=scenario)
    scenes = load_scenarios(dataset)
    assert len(scenes) == 1
    assert [source["kind"] for source in scenes[0]["sources"]] == ["scenario", "bench"]


def test_catalog_falls_back_to_scenario_role_directory(tmp_path):
    scenario = {
        "id": "scene-a",
        "mode": "single_agent",
        "description": "role lives beside scenarios",
        "agents": [{"role_file": "critic.md", "task": "review"}],
        "fixture_dir": "ops/fixture-a",
    }
    dataset = build_dataset(tmp_path, scenario=scenario)
    (dataset / "roles" / "developer.md").unlink()
    (dataset / "scenarios" / "roles").mkdir(parents=True, exist_ok=True)
    (dataset / "scenarios" / "roles" / "critic.md").write_text("角色\n", encoding="utf-8")
    scene = load_scenarios(dataset)[0]
    role_paths = [source["path"] for source in scene["sources"] if source["kind"] == "role"]
    assert role_paths == ["scenarios/roles/critic.md"]


def test_catalog_reads_utf8_and_marks_binary_fixtures(tmp_path):
    dataset = build_dataset(tmp_path)
    binary = b"\xff\xfe\x00binary"
    (dataset / "fixtures" / "ops" / "fixture-a" / "asset.bin").write_bytes(binary)
    scene = load_scenarios(dataset)[0]
    role = next(source for source in scene["sources"] if source["path"] == "roles/developer.md")
    assert "只读核查" in role["text"]
    asset = next(source for source in scene["sources"] if source["path"].endswith("asset.bin"))
    assert "binary" in asset["text"].lower()
    assert asset["sha256"] == hashlib.sha256(binary).hexdigest()


@pytest.mark.parametrize(
    "field,value",
    [
        ("role_file", "../../outside.md"),
        ("role_file", "/etc/passwd"),
        ("fixture_dir", "../ops/fixture-a"),
        ("pre_run_script", "/tmp/start.sh"),
    ],
)
def test_catalog_rejects_path_traversal(tmp_path, field, value):
    scenario = {
        "id": "scene-a",
        "mode": "single_agent",
        "description": "traversal",
        "agents": [{"role_file": "developer.md", "task": "read"}],
        "fixture_dir": "ops/fixture-a",
    }
    if field == "role_file":
        scenario["agents"][0]["role_file"] = value
    else:
        scenario[field] = value
    dataset = build_dataset(tmp_path, scenario=scenario)
    with pytest.raises(ValueError, match="path|inside|traversal"):
        load_scenarios(dataset)


def test_catalog_rejects_symlink_escaping_dataset(tmp_path):
    dataset = build_dataset(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    link = dataset / "fixtures" / "ops" / "fixture-a" / "link.md"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="path|inside|traversal"):
        load_scenarios(dataset)


def test_catalog_rejects_duplicate_or_unsafe_scenario_ids(tmp_path):
    dataset = build_dataset(tmp_path)
    duplicate = json.loads((dataset / "scenarios" / "scene-a.json").read_text(encoding="utf-8"))
    _write_json(dataset / "scenarios" / "other.json", duplicate)
    with pytest.raises(ValueError, match="duplicate|unique"):
        load_scenarios(dataset)

    (dataset / "scenarios" / "other.json").unlink()
    duplicate["id"] = "../escape"
    (dataset / "scenarios" / "scene-a.json").write_text(
        json.dumps(duplicate, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="scenario id|invalid"):
        load_scenarios(dataset)


def test_real_fuxibench_catalog_has_93_unique_scenarios_and_no_gt_fields():
    assert FUXIBENCH_ROOT.is_dir()
    scenes = load_scenarios(FUXIBENCH_ROOT)
    ids = [scene["scenario_id"] for scene in scenes]
    assert len(ids) == len(set(ids)) == 93
    for scene in scenes:
        scenario_source = next(
            source for source in scene["sources"] if source["kind"] == "scenario"
        )
        assert not (_keys(json.loads(scenario_source["text"])) & FORBIDDEN_KEYS)
        assert all(source["kind"] in {"scenario", "role", "fixture", "bench"} for source in scene["sources"])

def test_catalog_rejects_malformed_agents(tmp_path):
    scenario = {
        "id": "scene-a",
        "mode": "single_agent",
        "description": "malformed agents",
        "agents": "developer.md",
        "fixture_dir": "ops/fixture-a",
    }
    dataset = build_dataset(tmp_path, scenario=scenario)
    with pytest.raises(ValueError, match="agents"):
        load_scenarios(dataset)
