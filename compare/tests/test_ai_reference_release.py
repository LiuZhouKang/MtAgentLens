import hashlib
import json
from pathlib import Path

import pytest

from compare.tools.ai_reference_release import (
    build_release,
    evaluate_states,
    main,
    sha256_file,
    validate_components,
    verify_release,
)


def components():
    binding = {"data_release_id": "release-one", "scenario_id": "one",
               "run_id": "run-one", "source_run_uuid": "uuid-one"}
    scenario = {"schema": "mtagentlens.ai-scenario-silver/1", "scope": "scenario_design",
                "human_review": False, "eligible_for_runtime_metrics": False,
                "records": [{"scenario_id": "one", "label": "attack", "attack_type": "B"}]}
    runtime = {"schema": "mtagentlens.ai-runtime-silver/v1", "scope": "observed_run",
               "method": "model_proposal", "human_review": False,
               "records": [{"binding": binding, "annotation": {
                   field: {"value": "yes", "evidence_refs": ["e1"]}
                   for field in ("attempted", "executed", "succeeded")}}]}
    families = {"schema": "mtagentlens.dataset-families/1", "human_review": False,
                "assignments": {"one": {"family_id": "f1", "split": "test"}}}
    return scenario, runtime, families


def test_component_contract_preserves_scopes_and_exact_coverage():
    scenario, runtime, families = components()
    validate_components(scenario, runtime, families, expected_ids={"one"})
    scenario["eligible_for_runtime_metrics"] = True
    with pytest.raises(ValueError, match="scope"):
        validate_components(scenario, runtime, families, expected_ids={"one"})


def test_human_rebranding_duplicate_runs_and_family_leakage_rejected():
    scenario, runtime, families = components()
    runtime["human_review"] = True
    with pytest.raises(ValueError, match="human"):
        validate_components(scenario, runtime, families, expected_ids={"one"})
    runtime["human_review"] = False
    runtime["records"] *= 2
    with pytest.raises(ValueError, match="duplicate"):
        validate_components(scenario, runtime, families, expected_ids={"one"})
    runtime["records"] = runtime["records"][:1]
    scenario["records"].append({"scenario_id": "two", "label": "benign", "attack_type": "benign"})
    families["assignments"]["two"] = {"family_id": "f1", "split": "dev"}
    with pytest.raises(ValueError, match="split"):
        validate_components(scenario, runtime, families, expected_ids={"one", "two"})


def test_unknown_reference_never_counts_as_negative_and_predictions_can_abstain():
    _, runtime, _ = components()
    first = runtime["records"][0]
    second = json.loads(json.dumps(first))
    second["binding"]["run_id"] = "run-two"
    second["annotation"]["attempted"]["value"] = "unknown"
    third = json.loads(json.dumps(first))
    third["binding"]["run_id"] = "run-three"
    third["annotation"]["attempted"]["value"] = "no"
    predictions = [{"binding": r["binding"], "value": v}
                   for r, v in zip([first, second, third], ["yes", "no", "unknown"])]
    report = evaluate_states([first, second, third], predictions, field="attempted")
    assert report["reference_quality"] == "ai_silver"
    assert report["total_runs"] == 3
    assert report["reference_known"] == 2
    assert report["reference_unknown"] == 1
    assert report["prediction_abstentions_on_known"] == 1
    assert report["confusion"] == {"tp": 1, "fp": 0, "tn": 0, "fn": 0}
    assert report["binary_coverage_on_known"] == 0.5


def test_no_known_reference_is_not_a_perfect_score_and_run_binding_is_strict():
    _, runtime, _ = components()
    reference = runtime["records"][0]
    reference["annotation"]["attempted"]["value"] = "unknown"
    prediction = {"binding": reference["binding"].copy(), "value": "no"}
    report = evaluate_states([reference], [prediction], field="attempted")
    assert report["accuracy_on_binary_known"] is None
    assert report["binary_coverage_on_known"] is None
    prediction["binding"]["data_release_id"] = "other-release"
    with pytest.raises(ValueError, match="binding"):
        evaluate_states([reference], [prediction], field="attempted")


def test_design_annotation_carrying_runtime_identity_is_rejected():
    scenario, runtime, families = components()
    scenario["records"][0]["run_id"] = "run-one"
    with pytest.raises(ValueError, match="cross-sample"):
        validate_components(scenario, runtime, families, expected_ids={"one"})


def test_runtime_cohorts_scope_benchmark_bindings_but_release_diagnostics():
    scenario, runtime, families = components()
    diagnostic = json.loads(json.dumps(runtime["records"][0]))
    diagnostic["cohort"] = "engineering_diagnostic_run"
    diagnostic["binding"]["run_id"] = "run-diagnostic"
    diagnostic["binding"]["scenario_id"] = "legacy-scenario"
    runtime["records"].append(diagnostic)
    report = validate_components(scenario, runtime, families, expected_ids={"one"})
    assert report["runtime_records"] == 2
    assert report["runtime_benchmark_records"] == 1
    assert report["runtime_diagnostic_records"] == 1

    foreign = json.loads(json.dumps(runtime["records"][0]))
    foreign["binding"]["run_id"] = "run-foreign"
    foreign["binding"]["scenario_id"] = "legacy-scenario"
    runtime["records"].append(foreign)
    with pytest.raises(ValueError, match="benchmark"):
        validate_components(scenario, runtime, families, expected_ids={"one"})
    foreign["cohort"] = "unknown_cohort"
    with pytest.raises(ValueError, match="cohort"):
        validate_components(scenario, runtime, families, expected_ids={"one"})


def write_components(base):
    scenario, runtime, families = components()
    paths = {}
    for name, value in {"scenario": scenario, "runtime": runtime, "families": families}.items():
        path = base / f"{name}-component.json"
        path.write_text(json.dumps(value))
        paths[name] = path
    return paths


def test_release_build_copies_components_and_verifies(tmp_path):
    components = write_components(tmp_path)
    release = build_release(components, tmp_path / "release", expected_ids={"one"})
    assert release["schema"] == "mtagentlens.ai-reference-release/1"
    assert release["quality_tier"] == "ai_silver"
    assert release["ai_reference_available"] is True
    assert release["human_gold_available"] is False
    assert release["validation"]["expected_scenario_ids"] == ["one"]
    assert release["components"]["runtime"]["sha256"] == sha256_file(components["runtime"])
    assert set(release["files"]) == {"components/scenario.json",
                                     "components/runtime.json", "components/families.json"}
    assert verify_release(tmp_path / "release") == release


def test_release_accepts_and_verifies_directory_component(tmp_path):
    causal = tmp_path / "causal"
    causal.mkdir()
    (causal / "one.json").write_text(json.dumps({"scenario_id": "one", "human_review": False}))
    (causal / "two.json").write_text(json.dumps({"scenario_id": "two", "human_review": False}))
    components = write_components(tmp_path)
    components["causal_design"] = causal
    release = build_release(components, tmp_path / "release", expected_ids={"one"})
    assert release["components"]["causal_design"]["kind"] == "directory"
    assert release["components"]["causal_design"]["file_count"] == 2
    assert verify_release(tmp_path / "release") == release
    (tmp_path / "release/components/causal_design/one.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        verify_release(tmp_path / "release")


def test_release_rejects_changed_missing_and_extra_files(tmp_path):
    write_components(tmp_path)
    build_release(write_components(tmp_path), tmp_path / "changed", expected_ids={"one"})
    (tmp_path / "changed/components/runtime.json").write_bytes(
        (tmp_path / "changed/components/runtime.json").read_bytes() + b"\n")
    with pytest.raises(ValueError, match="checksum"):
        verify_release(tmp_path / "changed")
    build_release(write_components(tmp_path), tmp_path / "missing", expected_ids={"one"})
    (tmp_path / "missing/components/families.json").unlink()
    with pytest.raises(ValueError, match="checksum"):
        verify_release(tmp_path / "missing")
    build_release(write_components(tmp_path), tmp_path / "extra", expected_ids={"one"})
    (tmp_path / "extra/components/planted.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        verify_release(tmp_path / "extra")


def test_release_rejects_human_gold_claim_and_coverage_gap(tmp_path):
    build_release(write_components(tmp_path), tmp_path / "release", expected_ids={"one"})
    manifest = tmp_path / "release/release.json"
    release = json.loads(manifest.read_text())
    release["human_gold_available"] = True
    manifest.write_text(json.dumps(release))
    with pytest.raises(ValueError, match="human"):
        verify_release(tmp_path / "release")

    build_release(write_components(tmp_path), tmp_path / "gap", expected_ids={"one"})
    scenario_path = tmp_path / "gap/components/scenario.json"
    scenario = json.loads(scenario_path.read_text())
    scenario["records"] = []
    scenario_path.write_text(json.dumps(scenario))
    manifest = tmp_path / "gap/release.json"
    release = json.loads(manifest.read_text())
    release["components"]["scenario"]["sha256"] = sha256_file(scenario_path)
    release["components"]["scenario"]["size"] = scenario_path.stat().st_size
    release["files"]["components/scenario.json"] = sha256_file(scenario_path)
    unsigned = {key: value for key, value in release.items() if key != "release_id"}
    release["release_id"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    manifest.write_text(json.dumps(release))
    with pytest.raises(ValueError, match="coverage"):
        verify_release(tmp_path / "gap")


def test_build_release_requires_exact_expected_coverage(tmp_path):
    components = write_components(tmp_path)
    with pytest.raises(ValueError, match="coverage"):
        build_release(components, tmp_path / "release", expected_ids={"one", "two"})
    assert not (tmp_path / "release").exists()
    build_release(components, tmp_path / "release", expected_ids={"one"})
    with pytest.raises(ValueError, match="immutable"):
        build_release(components, tmp_path / "release", expected_ids={"one"})


def test_cli_builds_and_verifies_release(tmp_path, capsys):
    components = write_components(tmp_path)
    argv = ["build", "--scenario", str(components["scenario"]),
            "--runtime", str(components["runtime"]), "--families", str(components["families"]),
            "--out", str(tmp_path / "release")]
    assert main(argv) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["release_id"] and built["validation"]["runtime_records"] == 1
    assert main(["verify", "--release", str(tmp_path / "release")]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["valid"] is True and verified["release_id"] == built["release_id"]
