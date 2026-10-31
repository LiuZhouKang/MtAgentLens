"""FuxiBench 2.0 scope: package defaults, detection-excluded filter, taxonomy.

The canonical dataset is the self-contained ``datasets/FuxiBench`` package:
93 GT records, all scored: the 9 records that were previously
``quality.detection_excluded`` were re-adjudicated on 2026-10-27 (3 blind
annotators + an independent adjudicator, 27/27 benign votes) and are now
counted as difficult benign. Adapters and every metric walker must therefore
score exactly 93 records (58 attack: A47/B11, 35 benign). Legacy Type C is
historical input only and is never a current attack type.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from compare.tools.detection_scope import (
    DEFAULT_DATASET_DIR,
    detection_excluded_ids,
    included_ground_truth,
    is_detection_excluded,
    load_ground_truth,
    scope_counts,
    validate_current_attack_type,
)
from compare.tools.datasets import FrozenDatasetAdapter, load_dataset

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "datasets/FuxiBench"
# The package has no detection-excluded records since 2026-10-27: the nine that
# carried the flag were re-adjudicated to difficult benign and are scored. The
# constant stays so this file fails if an exclusion is silently reintroduced.
EXCLUDED: set[str] = set()

# Walkers that read GT directly and must apply the shared detection scope.
WALKERS = (
    "compare/scripts/eval/standard_evaluation.py",
    "compare/scripts/run/app_baseline.py",
    "compare/scripts/evaluate_prompt_tools.py",
    "compare/scripts/llm_guard_batch.py",
    "compare/scripts/meta_prompt_guard_batch.py",
    "scripts/evaluation/binary_verdict_bootstrap.py",
    "compare/scripts/audit/audit_action_traces.py",
    "compare/scripts/run/summarize_pilot.py",
    "compare/scripts/run/run_all_experiments.py",
)


def _write_gt(path: Path, scenario_id: str, *, label: str, attack_type: str,
              excluded: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "mtagentlens.ground_truth.v4",
        "scenario_id": scenario_id,
        "category": "benign" if label == "benign" else "reconnaissance",
        "label": label,
        "attack_type": attack_type,
        "task_summary": "synthetic",
        "payload": None,
        "benign_activity": {"summary": "synthetic"} if label == "benign" else None,
        "expected_behaviors": [] if label == "benign" else [{
            "behavior_id": "synthetic-behavior",
            "description": "synthetic attack behavior",
            "attack_pattern": "CredentialAccess",
            "boundary_type": "ResourceBoundary",
            "severity": "Warning",
            "should_alert": True,
            "expected_commands": [{"type": "shell_command", "pattern": "make test",
                                   "why": "synthetic trigger"}],
            "expected_resources": [],
            "evidence_requirements": "synthetic evidence",
            "rationale": "synthetic rationale",
        }],
        "min_decision": "Safe" if label == "benign" else "Unsafe",
        "should_alert": label == "attack",
        "execution_check": {"summary": "synthetic"},
        "annotation": {"method": "model_proposal", "collector_output_consulted": False},
        "quality": {"needs_human_review": False,
                    "detection_excluded": excluded},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _synthetic_dataset(tmp_path: Path, attack_type: str = "B") -> Path:
    dataset = tmp_path / "FuxiBench"
    for child in ("scenarios", "ground_truth", "fixtures", "traces"):
        (dataset / child).mkdir(parents=True, exist_ok=True)
    (dataset / "scenarios/attack.json").write_text(json.dumps({"id": "attack"}), encoding="utf-8")
    _write_gt(dataset / "ground_truth/reconnaissance/attack.json", "attack",
              label="attack", attack_type=attack_type)
    return dataset


def test_detection_scope_helper_counts_and_ids():
    counts = scope_counts(PACKAGE / "ground_truth")
    assert counts["records"] == 93
    assert counts["detection_excluded"] == 0
    assert counts["scored"] == 93
    assert counts["scored_attack"] == 58
    assert counts["scored_benign"] == 35
    assert set(counts["excluded_scenario_ids"]) == EXCLUDED
    assert detection_excluded_ids(PACKAGE / "ground_truth") == EXCLUDED
    included = included_ground_truth(PACKAGE / "ground_truth")
    assert len(included) == 93
    assert {gt["attack_type"] for gt in included.values()} == {"A", "B", "benign"}
    assert all(gt["label"] == "attack" for gt in included.values()
               if gt["attack_type"] in {"A", "B"})
    assert all(is_detection_excluded(gt) is False for gt in included.values())


def test_is_detection_excluded_is_strict_about_the_flag():
    assert is_detection_excluded({}) is False
    assert is_detection_excluded(None) is False
    assert is_detection_excluded({"quality": {"detection_excluded": True}}) is True
    assert is_detection_excluded({"quality": {"detection_excluded": False}}) is False
    assert is_detection_excluded({"quality": {"detection_excluded": "true"}}) is False


def test_load_ground_truth_skips_reviewer_sidecars(tmp_path):
    _write_gt(tmp_path / "ground_truth/benign/benign-a.json", "benign-a",
              label="benign", attack_type="benign")
    (tmp_path / "ground_truth/_reviewers").mkdir(parents=True)
    (tmp_path / "ground_truth/_reviewers/benign-a.json").write_text(
        json.dumps({"reviewer": "raw"}), encoding="utf-8")
    records = load_ground_truth(tmp_path / "ground_truth")
    assert list(records) == ["benign-a"]


def test_default_adapter_loads_only_the_scored_package_split():
    cases = load_dataset("FuxiBench").load_cases()
    assert len(cases) == 93
    assert sum(case.is_attack for case in cases) == 58
    assert sum(not case.is_attack for case in cases) == 35
    types = {}
    for case in cases:
        gt = json.loads(case.ground_truth_json.read_text(encoding="utf-8"))
        types[gt["attack_type"]] = types.get(gt["attack_type"], 0) + 1
        assert case.adapter_case.fixture_dir is not None
    assert types == {"A": 47, "B": 11, "benign": 35}
    assert not any(is_detection_excluded(json.loads(case.ground_truth_json.read_text()))
                   for case in cases)


def test_default_adapter_is_the_class_default_and_stays_inside_the_package():
    cases = FrozenDatasetAdapter().load_cases()
    assert len(cases) == 93
    for case in cases:
        # Ground truth and fixtures come from the package; only the sanitized
        # comparator scenario copy lives in the adapter's generated temp root.
        for path in (case.ground_truth_json, case.adapter_case.fixture_dir):
            assert str(path.resolve()).startswith(str(PACKAGE.resolve()))
        for key, artifact in case.adapter_case.input_artifacts.items():
            assert key != "ground_truth"
            assert "compare/Dataset" not in str(artifact)
            assert "env/scenarios" not in str(artifact)
            assert "local/workpackages" not in str(artifact)


def test_package_loads_without_env_or_local(monkeypatch, tmp_path):
    """The package is self-contained: loading only needs datasets/FuxiBench."""
    monkeypatch.chdir(tmp_path)
    cases = FrozenDatasetAdapter(PACKAGE).load_cases()
    assert len(cases) == 93
    assert all(str(case.ground_truth_json.resolve()).startswith(str(PACKAGE.resolve()))
               for case in cases)
    assert all(case.adapter_case.fixture_dir is not None for case in cases)


def test_adapter_rejects_legacy_type_c_unless_explicitly_historical(tmp_path):
    dataset = _synthetic_dataset(tmp_path, attack_type="C")
    with pytest.raises(ValueError, match="legacy Type C"):
        FrozenDatasetAdapter(dataset).load_cases()
    cases = FrozenDatasetAdapter(dataset, allow_legacy_type_c=True).load_cases()
    assert len(cases) == 1
    with pytest.raises(ValueError, match="unsupported attack_type"):
        validate_current_attack_type("D")
    with pytest.raises(ValueError, match="legacy Type C"):
        validate_current_attack_type("C")
    assert validate_current_attack_type("A") == "A"
    assert validate_current_attack_type("benign") == "benign"


def test_release_builder_taxonomy_is_two_type(tmp_path):
    from compare.tools.dataset_release import build_release

    scenarios = tmp_path / "scenarios"
    (scenarios / "fixtures/demo").mkdir(parents=True)
    (scenarios / "fixtures/demo/task.py").write_text("print('hello')\n", encoding="utf-8")
    (scenarios / "case.json").write_text(json.dumps({
        "id": "case", "fixture_dir": "demo",
        "agents": [{"task": "Run python task.py in {{FIXTURE_DIR}}."}],
    }), encoding="utf-8")
    gt_root = tmp_path / "gt"

    def write(attack_type: str) -> None:
        _write_gt(gt_root / "reconnaissance/case.json", "case",
                  label="attack", attack_type=attack_type)

    write("C")
    with pytest.raises(ValueError, match="legacy Type C"):
        build_release(scenarios, gt_root, tmp_path / "release-c")
    assert not (tmp_path / "release-c").exists()
    legacy = build_release(scenarios, gt_root, tmp_path / "release-c-legacy",
                           allow_legacy_type_c=True)
    assert legacy["case_bindings"]
    write("A")
    current = build_release(scenarios, gt_root, tmp_path / "release-a")
    assert current["case_bindings"]


def test_package_loads_all_scenarios_without_release_manifest():
    assert not (PACKAGE / "release.json").exists()
    assert not (PACKAGE / "checksums.sha256").exists()
    metadata = json.loads((PACKAGE / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["ground_truth_schema"] == "mtagentlens.ground_truth.v4"
    assert metadata["detection_included_counts"] == {"attack": 58, "benign": 35}
    assert scope_counts(PACKAGE / "ground_truth")["scored"] == 93
    assert len(FrozenDatasetAdapter(PACKAGE).load_cases()) == 93


def test_every_metric_walker_uses_the_shared_detection_scope():
    """Static contract for walkers whose import graph needs models or corpora."""
    for rel in WALKERS:
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "detection_scope" in source, f"{rel} does not import the shared scope"
        assert ("is_detection_excluded" in source
                or "detection_excluded_ids" in source
                or "included_ground_truth" in source), f"{rel} does not apply the scope"
    # Legacy flat-tree defaults must not silently walk the retired tree.
    assert "datasets/FuxiBench/ground_truth" not in (
        ROOT / "compare/scripts/run/run_all_experiments.py").read_text()
    assert "datasets/FuxiBench" in (
        ROOT / "compare/scripts/run/run_all_experiments.py").read_text()


def test_app_baseline_ground_truth_loader_applies_scope(tmp_path):
    from compare.scripts.run.app_baseline import load_ground_truth
    gt_root = tmp_path / "ground_truth"
    _write_gt(gt_root / "benign/benign-a.json", "benign-a", label="benign",
              attack_type="benign")
    _write_gt(gt_root / "benign/benign-b.json", "benign-b", label="benign",
              attack_type="benign", excluded=True)
    assert load_ground_truth(gt_root) == {"benign-a": "benign"}


def test_standard_evaluation_scope_matches_package():
    from compare.scripts.eval.standard_evaluation import _detection_excluded
    assert _detection_excluded() == EXCLUDED


def test_audit_action_traces_skips_detection_excluded(tmp_path):
    dataset = tmp_path / "FuxiBench"
    _write_gt(dataset / "ground_truth/benign/benign-a.json", "benign-a",
              label="benign", attack_type="benign")
    _write_gt(dataset / "ground_truth/reconnaissance/attack-a.json", "attack-a",
              label="attack", attack_type="B", excluded=True)
    out = tmp_path / "audit.json"
    result = subprocess.run(
        [sys.executable, str(ROOT / "compare/scripts/audit/audit_action_traces.py"),
         "--dataset", str(dataset), "--out", str(out)],
        cwd=ROOT, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    payload = json.loads(out.read_text())
    assert payload["summary"]["scenarios"] == 1
    assert payload["summary"]["detection_excluded"] == 1
    assert payload["summary"]["detection_excluded_scenarios"] == ["attack-a"]
