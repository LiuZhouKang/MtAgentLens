from __future__ import annotations

from pathlib import Path

import pytest

from compare.tools.base import AdapterCase, BoundaryType, EvaluationCase, ExpectedClaim, FindingRecord, ToolMetadata
from compare.tools.evaluator import evaluate_tool_results
from compare.tools.normalize import normalize_boundary, normalize_severity, severity_rank


def _tool() -> ToolMetadata:
    return ToolMetadata(
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


def _case(
    scenario_id: str,
    expected_claims: tuple[ExpectedClaim, ...] = (),
    *,
    is_attack: bool = True,
    dataset_name: str = "FuxiBench",
) -> EvaluationCase:
    adapter_case = AdapterCase(
        dataset_name=dataset_name,
        scenario_id=scenario_id,
        scenario_json=Path(f"/tmp/{scenario_id}.json"),
        fixture_dir=None,
        saved_logs=[],
        input_artifacts={},
    )
    return EvaluationCase(
        adapter_case=adapter_case,
        ground_truth_json=None,
        expected_claims=expected_claims,
        mtagentlens_baseline={},
        is_attack=is_attack,
    )


def _claim(
    *,
    alert_id: str = "alert-1",
    severity: str = "High",
    boundary_type: BoundaryType | None = None,
    resource_substrings: tuple[str, ...] = (),
    command_substrings: tuple[str, ...] = (),
    content_substrings: tuple[str, ...] = (),
    category: str = "secret",
) -> ExpectedClaim:
    return ExpectedClaim(
        alert_id=alert_id,
        severity=severity,
        category=category,
        boundary_type=boundary_type,
        resource_substrings=resource_substrings,
        command_substrings=command_substrings,
        content_substrings=content_substrings,
    )


def _finding(
    scenario_id: str,
    *,
    boundary_type: BoundaryType | None = None,
    resource: str | None = None,
    command: str | None = None,
    text_span: str | None = None,
    evidence: str = "",
    category: str = "secret",
    severity: str = "Critical",
    dataset_name: str = "FuxiBench",
) -> FindingRecord:
    return FindingRecord(
        tool_id="fixture-scanner",
        dataset_name=dataset_name,
        scenario_id=scenario_id,
        finding_id=f"fixture-scanner:{scenario_id}:1",
        severity=severity,
        category=category,
        boundary_type=boundary_type,
        resource=resource,
        command=command,
        text_span=text_span,
        evidence=evidence,
        confidence=0.9,
        raw_output_path=None,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("critical", "Critical"),
        ("HIGH", "Critical"),
        ("error", "Critical"),
        ("warning", "Warning"),
        ("warn", "Warning"),
        ("medium", "Warning"),
        ("low", "Info"),
        (None, "Info"),
    ],
)
def test_normalize_severity(raw: str | None, expected: str):
    assert normalize_severity(raw) == expected


def test_severity_rank_uses_normalized_values():
    assert severity_rank("info") == 1
    assert severity_rank("warn") == 2
    assert severity_rank("error") == 3
    assert severity_rank(None) == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("resource", BoundaryType.RESOURCE),
        ("file", BoundaryType.RESOURCE),
        ("secret", BoundaryType.RESOURCE),
        ("network", BoundaryType.RESOURCE),
        ("network_boundary", BoundaryType.RESOURCE),
        ("network-boundary", BoundaryType.RESOURCE),
        ("network boundary", BoundaryType.RESOURCE),
        ("filesystem", BoundaryType.RESOURCE),
        ("filesystem_boundary", BoundaryType.RESOURCE),
        ("endpoint", BoundaryType.RESOURCE),
        ("audit-endpoint", BoundaryType.RESOURCE),
        ("audit_endpoint", BoundaryType.RESOURCE),
        ("audit endpoint", BoundaryType.RESOURCE),
        ("prompt", BoundaryType.CONTENT),
        ("injection", BoundaryType.CONTENT),
        ("handoff", BoundaryType.DELEGATION),
        ("delegation", BoundaryType.DELEGATION),
        ("command", BoundaryType.EXEC_ARG),
        ("bash", BoundaryType.EXEC_ARG),
        ("write", BoundaryType.EXEC_ARG),
        ("exec_arg", BoundaryType.EXEC_ARG),
        ("exec-arg", BoundaryType.EXEC_ARG),
        ("exec arg", BoundaryType.EXEC_ARG),
        ("ExecArg", BoundaryType.EXEC_ARG),
        ("exec_arg_boundary", BoundaryType.EXEC_ARG),
        ("execution", BoundaryType.EXEC_ARG),
        ("execution_argument", BoundaryType.EXEC_ARG),
        ("execution-argument", BoundaryType.EXEC_ARG),
        ("unknown", None),
        (None, None),
    ],
)
def test_normalize_boundary(raw: str | None, expected: BoundaryType | None):
    assert normalize_boundary(raw) == expected


def test_attack_only_metrics_mark_precision_like_values_not_applicable():
    cases = [_case("attack-1"), _case("attack-2")]
    findings = [_finding("attack-1")]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["dataset_has_benign_set"] is False
    assert result["tp"] == 1
    assert result["fn"] == 1
    assert result["detected_attack_scenarios"] == 1
    assert result["missed_attack_scenarios"] == 1
    assert result["scenario_recall"] == 0.5
    assert result["recall"] == 0.5
    assert result["precision"] == "not_applicable_no_benign_set"
    assert result["f1"] == "not_applicable_no_benign_set"
    assert result["fp"] == "not_applicable_no_benign_set"
    assert result["tn"] == "not_applicable_no_benign_set"


def test_mixed_dataset_reports_confusion_matrix_and_rates():
    cases = [
        _case("attack-hit", is_attack=True),
        _case("attack-miss", is_attack=True),
        _case("benign-fp", is_attack=False),
        _case("benign-tn", is_attack=False),
    ]
    findings = [_finding("attack-hit"), _finding("benign-fp")]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["dataset_has_benign_set"] is True
    assert result["tp"] == 1
    assert result["fp"] == 1
    assert result["fn"] == 1
    assert result["tn"] == 1
    assert result["precision"] == 0.5
    assert result["recall"] == 0.5
    assert result["f1"] == 0.5


def test_same_scenario_id_in_different_datasets_is_evaluated_independently():
    cases = [
        _case("shared", is_attack=True, dataset_name="dataset-a"),
        _case("shared", is_attack=False, dataset_name="dataset-b"),
    ]
    findings = [_finding("shared", dataset_name="dataset-a")]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["total_scenarios"] == 2
    assert result["tp"] == 1
    assert result["fp"] == 0
    assert result["fn"] == 0
    assert result["tn"] == 1
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0


def test_duplicate_dataset_scenario_case_keys_raise_value_error():
    cases = [
        _case("duplicate", dataset_name="dataset-a"),
        _case("duplicate", dataset_name="dataset-a"),
    ]

    with pytest.raises(ValueError, match="Duplicate evaluation case"):
        evaluate_tool_results(_tool(), cases, [])


def test_unknown_dataset_scenario_findings_are_ignored():
    cases = [_case("known", is_attack=False, dataset_name="dataset-a")]
    findings = [_finding("unknown", dataset_name="dataset-b")]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["fp"] == 0
    assert result["tn"] == 1
    assert result["tp"] == 0
    assert result["fn"] == 0
    assert result["precision"] == 0.0


def test_claim_matching_uses_boundary_and_resource_substrings():
    claim = _claim(boundary_type=BoundaryType.RESOURCE, resource_substrings=("/etc/shadow",))
    cases = [_case("attack-1", (claim,))]
    findings = [
        _finding(
            "attack-1",
            boundary_type=BoundaryType.RESOURCE,
            resource="/etc/shadow",
            evidence="scanner saw a sensitive file access",
        )
    ]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["claim_coverage"] == 1.0
    assert result["matched_claims"] == 1
    assert result["total_expected_claims"] == 1
    assert result["claim_coverage_by_boundary"]["ResourceBoundary"]["matched"] == 1


def test_boundary_mismatch_prevents_false_claim_match():
    claim = _claim(boundary_type=BoundaryType.RESOURCE, resource_substrings=("/etc/shadow",))
    cases = [_case("attack-1", (claim,))]
    findings = [
        _finding(
            "attack-1",
            boundary_type=BoundaryType.CONTENT,
            text_span="prompt mentions /etc/shadow",
            evidence="content contains /etc/shadow",
        )
    ]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["claim_coverage"] == 0.0
    assert result["matched_claims"] == 0
    assert result["claim_coverage_by_boundary"]["ResourceBoundary"]["matched"] == 0


def test_resource_substring_can_match_text_span_when_boundary_matches():
    claim = _claim(boundary_type=BoundaryType.RESOURCE, resource_substrings=("/etc/shadow",))
    cases = [_case("attack-1", (claim,))]
    findings = [_finding("attack-1", boundary_type=BoundaryType.RESOURCE, text_span="read /etc/shadow")]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["matched_claims"] == 1
    assert result["claim_coverage"] == 1.0


def test_typed_claim_does_not_match_untyped_finding_with_matching_substring():
    claim = _claim(boundary_type=BoundaryType.RESOURCE, resource_substrings=("/etc/shadow",))
    cases = [_case("attack-1", (claim,))]
    findings = [_finding("attack-1", boundary_type=None, resource="/etc/shadow")]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["matched_claims"] == 0
    assert result["claim_coverage"] == 0.0


def test_boundary_only_typed_claim_does_not_match_untyped_finding():
    claim = _claim(boundary_type=BoundaryType.RESOURCE, category="secret")
    cases = [_case("attack-1", (claim,))]
    findings = [_finding("attack-1", boundary_type=None, category="secret")]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["matched_claims"] == 0


def test_untyped_claim_can_match_typed_finding_with_matching_substring():
    claim = _claim(boundary_type=None, content_substrings=("ignore previous",))
    cases = [_case("attack-1", (claim,))]
    findings = [_finding("attack-1", boundary_type=BoundaryType.CONTENT, text_span="ignore previous instructions")]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["matched_claims"] == 1


def test_boundary_only_claim_requires_matching_category_when_category_is_set():
    claim = _claim(boundary_type=BoundaryType.RESOURCE, category="secret")
    cases = [_case("attack-1", (claim,))]
    findings = [_finding("attack-1", boundary_type=BoundaryType.RESOURCE, category="command")]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["matched_claims"] == 0


def test_boundary_only_claim_matches_same_category_case_insensitively():
    claim = _claim(boundary_type=BoundaryType.RESOURCE, category="Secret")
    cases = [_case("attack-1", (claim,))]
    findings = [_finding("attack-1", boundary_type=BoundaryType.RESOURCE, category="secret")]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["matched_claims"] == 1


def test_substring_claim_can_match_despite_different_category():
    claim = _claim(boundary_type=BoundaryType.RESOURCE, resource_substrings=("/etc/shadow",), category="secret")
    cases = [_case("attack-1", (claim,))]
    findings = [_finding("attack-1", boundary_type=BoundaryType.RESOURCE, text_span="read /etc/shadow", category="command")]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["matched_claims"] == 1


@pytest.mark.parametrize("expected_severity", ["Critical", "High"])
def test_info_finding_does_not_match_high_or_critical_claim(expected_severity: str):
    claim = _claim(
        severity=expected_severity,
        boundary_type=BoundaryType.RESOURCE,
        resource_substrings=("/etc/shadow",),
    )
    cases = [_case("attack-1", (claim,))]
    findings = [
        _finding(
            "attack-1",
            severity="Info",
            boundary_type=BoundaryType.RESOURCE,
            text_span="read /etc/shadow",
        )
    ]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["matched_claims"] == 0
    assert result["claim_coverage"] == 0.0


def test_critical_finding_matches_high_claim():
    claim = _claim(
        severity="High",
        boundary_type=BoundaryType.RESOURCE,
        resource_substrings=("/etc/shadow",),
    )
    cases = [_case("attack-1", (claim,))]
    findings = [
        _finding(
            "attack-1",
            severity="Critical",
            boundary_type=BoundaryType.RESOURCE,
            text_span="read /etc/shadow",
        )
    ]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["matched_claims"] == 1
    assert result["claim_coverage"] == 1.0


def test_warning_finding_matches_warning_claim():
    claim = _claim(severity="Warning", boundary_type=BoundaryType.RESOURCE, category="secret")
    cases = [_case("attack-1", (claim,))]
    findings = [_finding("attack-1", severity="Warning", boundary_type=BoundaryType.RESOURCE, category="secret")]

    result = evaluate_tool_results(_tool(), cases, findings)

    assert result["matched_claims"] == 1
    assert result["claim_coverage"] == 1.0


def test_no_findings_on_attack_only_has_zero_recall_and_claim_coverage():
    claim = _claim(boundary_type=BoundaryType.CONTENT, content_substrings=("ignore previous instructions",))
    cases = [_case("attack-1", (claim,))]

    result = evaluate_tool_results(_tool(), cases, [])

    assert result["scenario_recall"] == 0.0
    assert result["recall"] == 0.0
    assert result["detected_attack_scenarios"] == 0
    assert result["missed_attack_scenarios"] == 1
    assert result["claim_coverage"] == 0.0
    assert result["matched_claims"] == 0
