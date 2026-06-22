from __future__ import annotations

from collections import defaultdict
from typing import Callable, Iterable

from .base import BoundaryType, EvaluationCase, ExpectedClaim, FindingRecord, ToolMetadata
from .normalize import severity_rank

_CaseKey = tuple[str, str]

_NOT_APPLICABLE_NO_BENIGN_SET = "not_applicable_no_benign_set"


def evaluate_tool_results(
    tool: ToolMetadata,
    cases: Iterable[EvaluationCase],
    findings: Iterable[FindingRecord],
) -> dict[str, object]:
    cases_by_key: dict[_CaseKey, EvaluationCase] = {}
    for case in cases:
        key = _case_key(case)
        if key in cases_by_key:
            dataset_name, scenario_id = key
            raise ValueError(f"Duplicate evaluation case for dataset={dataset_name!r} scenario_id={scenario_id!r}")
        cases_by_key[key] = case

    findings_by_scenario: dict[_CaseKey, list[FindingRecord]] = defaultdict(list)
    for finding in findings:
        key = (finding.dataset_name, finding.scenario_id)
        if key in cases_by_key:
            findings_by_scenario[key].append(finding)

    attack_cases = [case for case in cases_by_key.values() if case.is_attack]
    benign_cases = [case for case in cases_by_key.values() if not case.is_attack]
    dataset_has_benign_set = bool(benign_cases)

    tp = fp = fn = tn = 0
    detected_attack_scenarios = 0
    missed_attack_scenarios = 0
    per_scenario: list[dict[str, object]] = []

    for case in cases_by_key.values():
        has_findings = bool(findings_by_scenario.get(_case_key(case)))
        if case.is_attack and has_findings:
            tp += 1
            detected_attack_scenarios += 1
        elif case.is_attack:
            fn += 1
            missed_attack_scenarios += 1
        elif has_findings:
            fp += 1
        else:
            tn += 1

        per_scenario.append({
            "scenario_id": case.adapter_case.scenario_id,
            "is_attack": case.is_attack,
            "detected": has_findings,
            "expected_claim_count": len(case.expected_claims),
        })

    scenario_recall = _safe_divide(detected_attack_scenarios, len(attack_cases))
    matched_claims, total_claims, coverage_by_boundary, coverage_by_category = _claim_coverage(
        cases_by_key.values(),
        findings_by_scenario,
    )
    claim_coverage = _safe_divide(matched_claims, total_claims)

    # Lenient claim coverage: skips boundary_type gate for fair static-tool comparison.
    # Static tools hardcode ResourceBoundary but 42% of GT claims are ExecArgBoundary.
    lenient_matched, lenient_total, lenient_by_boundary, lenient_by_category = _claim_coverage(
        cases_by_key.values(),
        findings_by_scenario,
        match_fn=_finding_matches_claim_lenient,
    )
    claim_coverage_lenient = _safe_divide(lenient_matched, lenient_total)

    result: dict[str, object] = {
        "tool_id": tool.id,
        "tool_name": tool.name,
        "total_scenarios": len(cases_by_key),
        "attack_scenarios": len(attack_cases),
        "benign_scenarios": len(benign_cases),
        "dataset_has_benign_set": dataset_has_benign_set,
        "detected_attack_scenarios": detected_attack_scenarios,
        "missed_attack_scenarios": missed_attack_scenarios,
        "scenario_recall": scenario_recall,
        "recall": scenario_recall,
        "per_scenario": per_scenario,
        "tp": tp,
        "fn": fn,
        "total_expected_claims": total_claims,
        "matched_claims": matched_claims,
        "claim_coverage": claim_coverage,
        "claim_coverage_by_boundary": coverage_by_boundary,
        "claim_coverage_by_category": coverage_by_category,
        "claim_coverage_lenient": claim_coverage_lenient,
        "matched_claims_lenient": lenient_matched,
        "claim_coverage_lenient_by_boundary": lenient_by_boundary,
        "claim_coverage_lenient_by_category": lenient_by_category,
    }

    if dataset_has_benign_set:
        precision = _safe_divide(tp, tp + fp)
        recall = _safe_divide(tp, tp + fn)
        result.update(
            {
                "fp": fp,
                "tn": tn,
                "precision": precision,
                "recall": recall,
                "f1": _safe_divide(2 * precision * recall, precision + recall),
            }
        )
    else:
        result.update(
            {
                "fp": _NOT_APPLICABLE_NO_BENIGN_SET,
                "tn": _NOT_APPLICABLE_NO_BENIGN_SET,
                "precision": _NOT_APPLICABLE_NO_BENIGN_SET,
                "f1": _NOT_APPLICABLE_NO_BENIGN_SET,
            }
        )

    return result


def _case_key(case: EvaluationCase) -> _CaseKey:
    return (case.adapter_case.dataset_name, case.adapter_case.scenario_id)


def _claim_coverage(
    cases: Iterable[EvaluationCase],
    findings_by_scenario: dict[_CaseKey, list[FindingRecord]],
    match_fn: Callable[[FindingRecord, ExpectedClaim], bool] | None = None,
) -> tuple[int, int, dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    if match_fn is None:
        match_fn = _finding_matches_claim

    matched = 0
    total = 0
    by_boundary: dict[str, dict[str, int]] = defaultdict(lambda: {"matched": 0, "total": 0})
    by_category: dict[str, dict[str, int]] = defaultdict(lambda: {"matched": 0, "total": 0})

    for case in cases:
        scenario_findings = findings_by_scenario.get(_case_key(case), [])
        for claim in case.expected_claims:
            total += 1
            boundary_key = claim.boundary_type.value if claim.boundary_type else "UnspecifiedBoundary"
            category_key = claim.category or "unknown"
            by_boundary[boundary_key]["total"] += 1
            by_category[category_key]["total"] += 1

            claim_matched = any(match_fn(finding, claim) for finding in scenario_findings)
            if claim_matched:
                matched += 1
                by_boundary[boundary_key]["matched"] += 1
                by_category[category_key]["matched"] += 1

    return matched, total, dict(by_boundary), dict(by_category)


def _finding_matches_claim(finding: FindingRecord, claim: ExpectedClaim) -> bool:
    if severity_rank(finding.severity) < severity_rank(claim.severity):
        return False
    if claim.boundary_type is not None and finding.boundary_type != claim.boundary_type:
        return False
    # Agent attribution check: when both claim and finding carry agent IDs,
    # require them to match. When identity resolution is disabled (ablation),
    # findings get a collapsed agent_id that won't match the expected one.
    # Backward compatible: if either field is None, skip agent matching.
    if claim.expected_agent_id is not None and finding.agent_id is not None:
        if claim.expected_agent_id != finding.agent_id:
            return False

    resource_ok = (
        not claim.resource_substrings
        or _any_substring_matches(claim.resource_substrings, finding.resource or "")
    )
    command_ok = (
        not claim.command_substrings
        or _any_substring_matches(claim.command_substrings, finding.command or "")
    )
    content_ok = (
        not claim.content_substrings
        or _any_substring_matches(
            claim.content_substrings,
            "\n".join(value for value in (finding.text_span, finding.evidence) if value),
        )
    )
    has_substrings = bool(
        claim.resource_substrings or claim.command_substrings or claim.content_substrings
    )
    if has_substrings:
        return resource_ok and command_ok and content_ok

    if claim.boundary_type is None or finding.boundary_type is None:
        return False
    if claim.boundary_type != finding.boundary_type:
        return False
    if claim.category:
        return bool(finding.category) and claim.category.lower() == finding.category.lower()
    return True


def _finding_haystack(finding: FindingRecord) -> str:
    values = (finding.resource, finding.command, finding.text_span, finding.evidence, finding.category)
    return "\n".join(value for value in values if value).lower()


def _any_substring_matches(substrings: tuple[str, ...], haystack: str) -> bool:
    if not haystack:
        return False
    return any(substring.lower() in haystack for substring in substrings if substring)


def _finding_matches_claim_lenient(finding: FindingRecord, claim: ExpectedClaim) -> bool:
    """Lenient claim matching: skips boundary_type gate, uses only severity + substring match.

    Static analysis tools all hardcode boundary_type=ResourceBoundary, which
    structurally excludes them from matching ExecArgBoundary claims (42% of GT
    claims). This variant checks only:
      1. Severity (finding severity >= claim severity).
      2. Substring match (resource + command + content substrings found in
         finding evidence).
      3. Category fallback (if no substrings, match on category name alone).
    """
    if severity_rank(finding.severity) < severity_rank(claim.severity):
        return False

    substrings = claim.resource_substrings + claim.command_substrings + claim.content_substrings
    has_substrings = any(substring for substring in substrings)
    if has_substrings:
        return _any_substring_matches(substrings, _finding_haystack(finding))

    # No substrings: fall back to category match only
    if claim.category:
        return bool(finding.category) and claim.category.lower() == finding.category.lower()
    return True


def _safe_divide(numerator: int | float, denominator: int | float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator
