from pathlib import Path
from types import MappingProxyType

import pytest

import compare.tools as tools_root
from compare.tools.base import (
    AdapterCase,
    AdapterResult,
    BoundaryType,
    EvaluationCase,
    ExpectedClaim,
    FindingRecord,
    RunStatus,
    ToolMetadata,
)


def test_adapter_case_public_payload_excludes_evaluator_only_fields(tmp_path):
    scenario = tmp_path / "scenario.json"
    fixture_dir = tmp_path / "fixtures"
    scenario.write_text('{"id":"case-1"}', encoding="utf-8")
    fixture_dir.mkdir()

    case = AdapterCase(
        dataset_name="FuxiBench",
        scenario_id="case-1",
        scenario_json=scenario,
        fixture_dir=fixture_dir,
        saved_logs=[],
        input_artifacts={"scenario": scenario},
    )

    payload = case.to_adapter_payload()

    assert payload["dataset_name"] == "FuxiBench"
    assert payload["scenario_id"] == "case-1"
    assert "ground_truth_json" not in payload
    assert "expected_claims" not in payload
    assert "mtagentlens_baseline" not in payload


def test_finding_record_round_trips_to_json_dict(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text("{}", encoding="utf-8")
    finding = FindingRecord(
        tool_id="fixture-scanner",
        dataset_name="FuxiBench",
        scenario_id="prompt-injection",
        finding_id="fixture-scanner:prompt-injection:1",
        severity="Critical",
        category="secret",
        boundary_type=BoundaryType.RESOURCE,
        resource="environment-config.env",
        command=None,
        text_span="API_KEY=example",
        evidence="Matched API_KEY assignment",
        confidence=0.9,
        raw_output_path=raw,
    )

    data = finding.to_json_dict()

    assert data["tool_id"] == "fixture-scanner"
    assert data["boundary_type"] == "ResourceBoundary"
    assert data["raw_output_path"] == str(raw)
    assert data["confidence"] == 0.9


def test_adapter_result_status_helpers():
    metadata = ToolMetadata(
        id="fixture-scanner",
        name="Fixture Scanner",
        tool_class="static",
        source_url="builtin://compare/tools/adapters/fixture_scanner.py",
        version="builtin",
        license="MtAgentLens-project",
        install=[],
        adapter="fixture_scanner",
        input_modes=["static"],
        score_scope="main_offline",
        inclusion_rationale="Built-in harness smoke adapter.",
    )
    result = AdapterResult(
        tool=metadata,
        scenario_id="case-1",
        status=RunStatus.NO_FINDINGS,
        findings=[],
        raw_output_path=None,
        stdout_path=None,
        stderr_path=None,
        exit_code=0,
        duration_seconds=0.01,
        failure_reason=None,
    )

    assert result.is_metric_eligible()


def test_tools_root_does_not_export_evaluator_only_types():
    assert not hasattr(tools_root, "EvaluationCase")
    assert not hasattr(tools_root, "ExpectedClaim")
    assert "EvaluationCase" not in tools_root.__all__
    assert "ExpectedClaim" not in tools_root.__all__


def test_expected_claim_copies_mutable_inputs_and_stores_immutable_tuples():
    resources = ["secret.txt"]
    commands = ["curl"]
    contents = ["API_KEY"]

    claim = ExpectedClaim(
        alert_id="alert-1",
        severity="High",
        category="secret",
        boundary_type=BoundaryType.RESOURCE,
        resource_substrings=resources,
        command_substrings=commands,
        content_substrings=contents,
    )

    resources.append("token.txt")
    commands.append("wget")
    contents.append("PASSWORD")

    assert claim.resource_substrings == ("secret.txt",)
    assert claim.command_substrings == ("curl",)
    assert claim.content_substrings == ("API_KEY",)

    with pytest.raises(AttributeError):
        claim.resource_substrings.append("extra")


def test_adapter_case_copies_mutable_inputs_and_stores_immutable_views(tmp_path):
    scenario = tmp_path / "scenario.json"
    scenario.write_text("{}", encoding="utf-8")
    log_path = tmp_path / "adapter.log"
    artifact_path = tmp_path / "artifact.json"

    saved_logs = [log_path]
    input_artifacts = {"scenario": artifact_path}

    case = AdapterCase(
        dataset_name="FuxiBench",
        scenario_id="case-1",
        scenario_json=scenario,
        fixture_dir=None,
        saved_logs=saved_logs,
        input_artifacts=input_artifacts,
    )

    saved_logs.append(tmp_path / "later.log")
    input_artifacts["extra"] = tmp_path / "later.json"

    assert case.saved_logs == (log_path,)
    assert case.input_artifacts == {"scenario": artifact_path}
    assert isinstance(case.input_artifacts, MappingProxyType)

    with pytest.raises(AttributeError):
        case.saved_logs.append(tmp_path / "blocked.log")
    with pytest.raises(TypeError):
        case.input_artifacts["blocked"] = tmp_path / "blocked.json"


def test_evaluation_case_copies_mutable_inputs_and_stores_immutable_views(tmp_path):
    scenario = tmp_path / "scenario.json"
    scenario.write_text("{}", encoding="utf-8")
    adapter_case = AdapterCase(
        dataset_name="FuxiBench",
        scenario_id="case-1",
        scenario_json=scenario,
        fixture_dir=None,
        saved_logs=[],
        input_artifacts={},
    )
    claim = ExpectedClaim(
        alert_id="alert-1",
        severity="High",
        category="secret",
        boundary_type=BoundaryType.CONTENT,
        content_substrings=["leak"],
    )
    expected_claims = [claim]
    baseline = {"events": tmp_path / "baseline.json"}

    case = EvaluationCase(
        adapter_case=adapter_case,
        ground_truth_json=None,
        expected_claims=expected_claims,
        mtagentlens_baseline=baseline,
        is_attack=True,
    )

    expected_claims.append(
        ExpectedClaim(
            alert_id="alert-2",
            severity="Low",
            category="other",
            boundary_type=None,
        )
    )
    baseline["extra"] = tmp_path / "extra.json"

    assert case.expected_claims == (claim,)
    assert case.mtagentlens_baseline == {"events": tmp_path / "baseline.json"}
    assert isinstance(case.mtagentlens_baseline, MappingProxyType)

    with pytest.raises(AttributeError):
        case.expected_claims.append(claim)
    with pytest.raises(TypeError):
        case.mtagentlens_baseline["blocked"] = tmp_path / "blocked.json"


def test_tool_metadata_copies_list_inputs_and_stores_immutable_tuples():
    install = ["pip install fixture-scanner"]
    input_modes = ["static"]

    metadata = ToolMetadata(
        id="fixture-scanner",
        name="Fixture Scanner",
        tool_class="static",
        source_url="builtin://compare/tools/adapters/fixture_scanner.py",
        version="builtin",
        license="MtAgentLens-project",
        install=install,
        adapter="fixture_scanner",
        input_modes=input_modes,
        score_scope="main_offline",
        inclusion_rationale="Built-in harness smoke adapter.",
    )

    install.append("brew install fixture-scanner")
    input_modes.append("hybrid")

    assert metadata.install == ("pip install fixture-scanner",)
    assert metadata.input_modes == ("static",)

    with pytest.raises(AttributeError):
        metadata.install.append("blocked")


def test_adapter_result_copies_findings_list_and_stores_immutable_tuple(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text("{}", encoding="utf-8")
    finding = FindingRecord(
        tool_id="fixture-scanner",
        dataset_name="FuxiBench",
        scenario_id="prompt-injection",
        finding_id="fixture-scanner:prompt-injection:1",
        severity="Critical",
        category="secret",
        boundary_type=BoundaryType.RESOURCE,
        resource="environment-config.env",
        command=None,
        text_span="API_KEY=example",
        evidence="Matched API_KEY assignment",
        confidence=0.9,
        raw_output_path=raw,
    )
    findings = [finding]
    metadata = ToolMetadata(
        id="fixture-scanner",
        name="Fixture Scanner",
        tool_class="static",
        source_url="builtin://compare/tools/adapters/fixture_scanner.py",
        version="builtin",
        license="MtAgentLens-project",
        install=[],
        adapter="fixture_scanner",
        input_modes=["static"],
        score_scope="main_offline",
        inclusion_rationale="Built-in harness smoke adapter.",
    )

    result = AdapterResult(
        tool=metadata,
        scenario_id="case-1",
        status=RunStatus.SUCCESS,
        findings=findings,
        raw_output_path=raw,
        stdout_path=None,
        stderr_path=None,
        exit_code=0,
        duration_seconds=0.01,
        failure_reason=None,
    )

    findings.append(finding)

    assert result.findings == (finding,)

    with pytest.raises(AttributeError):
        result.findings.append(finding)
