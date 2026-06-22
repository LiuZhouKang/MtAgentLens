from __future__ import annotations

import json
from pathlib import Path

from compare.scripts.audit.audit_paper_experiment_readiness import _prompt_text_label_leaks
from compare.scripts.build.build_prompt_input_bundle import build_prompt_input_bundle
from compare.tools.datasets import FrozenDatasetAdapter


DATASET_ROOT = Path("compare/Dataset/FuxiBench")
SCENARIOS_ROOT = Path("env/scenarios")


def test_build_prompt_input_bundle_covers_current_frozen_dataset(tmp_path: Path) -> None:
    output_dir = tmp_path / "prompt_inputs"

    manifest = build_prompt_input_bundle(
        dataset_dir=DATASET_ROOT,
        scenarios_root=SCENARIOS_ROOT,
        output_dir=output_dir,
    )

    entries = manifest["entries"]
    assert manifest["schema"] == "mtagentprism.prompt_inputs.v1"
    assert manifest["total_cases"] == 67
    assert len(entries) == 67
    assert manifest["no_ground_truth_leak_guarantee"] is True
    assert all(entry["prompt_text_sha256"] for entry in entries)
    assert all((output_dir / entry["prompt_text_path"]).exists() for entry in entries)
    assert all((output_dir / entry["bundle_json_path"]).exists() for entry in entries)
    assert all(
        entry["fixture_file_count"] > 0
        for entry in entries
        if entry["attack_type"] in {"B", "C"}
    )
    assert {entry["scenario_id"] for entry in entries} >= {
        "prompt-injection",
        "handoff-injection",
        "benign-api-doc",
    }


def test_prompt_input_text_does_not_expose_evaluator_only_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "prompt_inputs"
    manifest = build_prompt_input_bundle(
        dataset_dir=DATASET_ROOT,
        scenarios_root=SCENARIOS_ROOT,
        output_dir=output_dir,
    )

    forbidden = (
        "ground_truth",
        "expected_claim",
        "responsibility.db",
        "responsibility_claim",
        "responsibility_alert",
        "mtagentprism_baseline",
        "trace_db",
        "label=attack",
        "label: attack",
    )
    for entry in manifest["entries"]:
        text = (output_dir / entry["prompt_text_path"]).read_text(encoding="utf-8").lower()
        assert all(token not in text for token in forbidden), entry["scenario_id"]
        assert _prompt_text_label_leaks(text) == [], entry["scenario_id"]


def test_prompt_input_text_does_not_expose_structural_labels(tmp_path: Path) -> None:
    output_dir = tmp_path / "prompt_inputs"
    manifest = build_prompt_input_bundle(
        dataset_dir=DATASET_ROOT,
        scenarios_root=SCENARIOS_ROOT,
        output_dir=output_dir,
    )

    by_id = {entry["scenario_id"]: entry for entry in manifest["entries"]}
    attack_text = (output_dir / by_id["prompt-injection"]["prompt_text_path"]).read_text(encoding="utf-8")
    benign_text = (output_dir / by_id["benign-api-doc"]["prompt_text_path"]).read_text(encoding="utf-8")

    assert "Attack type:" not in attack_text
    assert "Category:" not in attack_text
    assert "Scenario ID:" not in attack_text
    assert "prompt-injection" not in attack_text.lower()
    assert "Attack type:" not in benign_text
    assert "Category:" not in benign_text
    assert "Scenario ID:" not in benign_text
    assert "benign" not in benign_text.lower()


def test_frozen_dataset_adapter_exposes_prompt_text_when_bundle_exists() -> None:
    case = next(
        case
        for case in FrozenDatasetAdapter(DATASET_ROOT).load_cases()
        if case.adapter_case.scenario_id == "prompt-injection"
    )

    assert "prompt_text" in case.adapter_case.input_artifacts
    prompt_text = case.adapter_case.input_artifacts["prompt_text"]
    assert prompt_text.exists()
    assert prompt_text.name == "prompt.txt"
    payload = json.dumps(case.adapter_case.to_adapter_payload(), sort_keys=True)
    assert "ground_truth" not in payload
    assert "responsibility.db" not in payload


def test_prompt_input_bundle_falls_back_from_stale_absolute_fixture_root(tmp_path: Path) -> None:
    scenarios_root = tmp_path / "env" / "scenarios"
    fixture_dir = scenarios_root / "fixtures" / "demo-fixture"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "docs.md").write_text("trusted task document\n", encoding="utf-8")
    scenario_dir = scenarios_root / "prompt_injection"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "demo.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "mode": "single_agent",
                "description": "demo",
                "agents": [{"role_file": "agent.md", "task": "Read {{FIXTURE_DIR}}/docs.md"}],
                "fixtures_dir": "/stale/MtAgentPrism/env/scenarios/fixtures",
                "fixture_dir": "demo-fixture",
                "category": "prompt_injection",
                "attack_type": "B",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    dataset = tmp_path / "dataset"
    (dataset / "quality").mkdir(parents=True)
    (dataset / "ground_truth" / "prompt_injection").mkdir(parents=True)
    (dataset / "quality" / "manifest.json").write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "scenario_id": "demo",
                        "paths": {"ground_truth": "ground_truth/prompt_injection/demo.json"},
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (dataset / "ground_truth" / "prompt_injection" / "demo.json").write_text(
        json.dumps({"scenario_id": "demo", "label": "attack", "attack_type": "B"}) + "\n",
        encoding="utf-8",
    )

    manifest = build_prompt_input_bundle(
        dataset_dir=dataset,
        scenarios_root=scenarios_root,
        output_dir=tmp_path / "prompt_inputs",
    )

    assert manifest["entries"][0]["fixture_file_count"] == 1


def test_prompt_input_bundle_rejects_type_b_attack_without_fixture_text(tmp_path: Path) -> None:
    scenarios_root = tmp_path / "env" / "scenarios"
    scenario_dir = scenarios_root / "prompt_injection"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "demo.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "mode": "single_agent",
                "description": "demo",
                "agents": [{"role_file": "agent.md", "task": "Read {{FIXTURE_DIR}}/docs.md"}],
                "fixture_dir": "missing-fixture",
                "category": "prompt_injection",
                "attack_type": "B",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    dataset = tmp_path / "dataset"
    (dataset / "quality").mkdir(parents=True)
    (dataset / "ground_truth" / "prompt_injection").mkdir(parents=True)
    (dataset / "quality" / "manifest.json").write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "scenario_id": "demo",
                        "paths": {"ground_truth": "ground_truth/prompt_injection/demo.json"},
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (dataset / "ground_truth" / "prompt_injection" / "demo.json").write_text(
        json.dumps({"scenario_id": "demo", "label": "attack", "attack_type": "B"}) + "\n",
        encoding="utf-8",
    )

    try:
        build_prompt_input_bundle(
            dataset_dir=dataset,
            scenarios_root=scenarios_root,
            output_dir=tmp_path / "prompt_inputs",
        )
    except ValueError as exc:
        assert "requires fixture text" in str(exc)
    else:
        raise AssertionError("Type B prompt input without fixture text should be rejected")
