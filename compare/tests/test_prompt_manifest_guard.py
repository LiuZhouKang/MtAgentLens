from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from compare.tools.prompt_manifest import (
    PromptManifestDrift,
    find_drift,
    require_current,
)


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path, list[dict]]:
    """A three-way consistent scenario/GT/manifest triple."""
    dataset = tmp_path / "dataset"
    scenario = tmp_path / "scenarios" / "case-one.json"
    _write(
        scenario,
        {"id": "case-one", "mode": "single_agent", "fixture_dir": "ops/case-one",
         "agents": [{"role_file": "developer.md", "task": "Run make build"}]},
    )
    _write(
        dataset / "ground_truth" / "case" / "case-one.json",
        {"scenario_id": "case-one", "label": "attack", "attack_type": "B"},
    )
    entries = [
        {
            "scenario_id": "case-one",
            "source_scenario_path": "scenarios/case-one.json",
            "source_scenario_sha256": hashlib.sha256(scenario.read_bytes()).hexdigest(),
            "label": "attack",
            "attack_type": "B",
        }
    ]
    return dataset, scenario, entries


def test_consistent_inputs_report_no_drift(tmp_path: Path) -> None:
    dataset, _scenario, entries = _fixture(tmp_path)

    assert find_drift(entries, repo_root=tmp_path, dataset_root=dataset) == []


def test_dataset_relative_scenario_path_is_resolved(tmp_path: Path) -> None:
    """Frozen bundles record paths relative to the dataset root."""
    dataset = tmp_path / "dataset"
    scenario = dataset / "scenarios" / "case-one.json"
    _write(
        scenario,
        {"id": "case-one", "mode": "single_agent", "fixture_dir": "ops/case-one",
         "agents": [{"role_file": "developer.md", "task": "Run make build"}]},
    )
    _write(
        dataset / "ground_truth" / "case" / "case-one.json",
        {"scenario_id": "case-one", "label": "attack", "attack_type": "B"},
    )
    entries = [{
        "scenario_id": "case-one",
        "source_scenario_path": "scenarios/case-one.json",
        "source_scenario_sha256": hashlib.sha256(scenario.read_bytes()).hexdigest(),
        "label": "attack",
        "attack_type": "B",
    }]
    assert find_drift(entries, repo_root=tmp_path, dataset_root=dataset) == []


def test_scenario_bytes_changed_is_reported(tmp_path: Path) -> None:
    dataset, scenario, entries = _fixture(tmp_path)
    scenario.write_text(scenario.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    problems = find_drift(entries, repo_root=tmp_path, dataset_root=dataset)

    assert len(problems) == 1
    assert "source_scenario_sha256" in problems[0]


def test_attack_type_mismatch_is_reported(tmp_path: Path) -> None:
    dataset, _scenario, entries = _fixture(tmp_path)
    _write(
        dataset / "ground_truth" / "case" / "case-one.json",
        {"scenario_id": "case-one", "label": "attack", "attack_type": "C"},
    )

    problems = find_drift(entries, repo_root=tmp_path, dataset_root=dataset)

    assert any("attack_type" in problem for problem in problems)


def test_missing_ground_truth_is_reported(tmp_path: Path) -> None:
    dataset, _scenario, entries = _fixture(tmp_path)
    (dataset / "ground_truth" / "case" / "case-one.json").unlink()

    problems = find_drift(entries, repo_root=tmp_path, dataset_root=dataset)

    assert any("ground truth" in problem for problem in problems)


def test_require_current_refuses_stale_inputs(tmp_path: Path) -> None:
    dataset, scenario, entries = _fixture(tmp_path)
    scenario.write_text("{}\n", encoding="utf-8")

    with pytest.raises(PromptManifestDrift) as excinfo:
        require_current(entries, repo_root=tmp_path, dataset_root=dataset)

    assert "case-one" in str(excinfo.value)
    assert "rebuild" in str(excinfo.value).lower()


def test_require_current_can_be_told_to_proceed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dataset, scenario, entries = _fixture(tmp_path)
    scenario.write_text("{}\n", encoding="utf-8")

    require_current(entries, repo_root=tmp_path, dataset_root=dataset, allow_stale=True)

    assert "stale" in capsys.readouterr().err.lower()
