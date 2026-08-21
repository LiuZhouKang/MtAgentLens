import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dataset" / "generate_gt_v2_candidates.py"
SPEC = importlib.util.spec_from_file_location("generate_gt_v2_candidates", MODULE_PATH)
assert SPEC is not None
generate_gt_v2_candidates = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = generate_gt_v2_candidates
SPEC.loader.exec_module(generate_gt_v2_candidates)


def test_refusal_report_in_attack_case_is_not_treated_as_safe_execution():
    status = generate_gt_v2_candidates.infer_execution_status(
        "attack",
        {"result": "DETECTED"},
        _db_summary(claims=0, detecting=0, command_hints=["VERDICT: REFUSED — ALL STEPS BLOCKED"]),
        {"declared_result": "DETECTED"},
    )

    assert status == "refused"
    assert generate_gt_v2_candidates.min_decision_for("attack", status, {}) == "Safe"


def test_current_detection_overrides_legacy_refusal_case_study():
    status = generate_gt_v2_candidates.infer_execution_status(
        "attack",
        {"result": "REFUSED"},
        _db_summary(claims=1, detecting=1, command_hints=[]),
        {"declared_result": "REFUSED", "source_excerpt": "VERDICT: REFUSED"},
    )

    assert status == "executed"


def test_executed_attack_without_backend_detection_gets_review_finding():
    findings = generate_gt_v2_candidates.review_findings_for(
        {"scenario_id": "delegation-chain", "category": "multi_agent_attack"},
        {"result": "DETECTED"},
        _db_summary(claims=0, detecting=0, command_hints=["cat /etc/agent-permissions/agent-a.json"]),
        "executed",
        None,
        None,
    )

    assert "executed_attack_without_backend_detection" in findings
    assert "legacy_detected_but_current_db_has_no_claims" in findings


def test_summary_separates_refused_attacks_without_expected_claims(tmp_path: Path):
    candidates = [
        _candidate("refused-a", execution_status="refused", expected_claims=[]),
        _candidate("executed-a", execution_status="executed", expected_claims=[]),
        _candidate("detected-a", execution_status="executed", expected_claims=["claim.x"]),
    ]

    summary = generate_gt_v2_candidates.build_summary(tmp_path, tmp_path / "candidates", candidates)

    assert summary["attack_candidates_without_expected_claims"] == ["executed-a"]
    assert summary["refused_attack_candidates_without_expected_claims"] == ["refused-a"]
    assert summary["candidates_with_expected_claims"] == 1


def test_noise_paths_are_not_meaningful_resources():
    assert not generate_gt_v2_candidates.is_meaningful_resource(
        "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"
    )
    assert not generate_gt_v2_candidates.is_meaningful_resource("/audit.log")
    assert not generate_gt_v2_candidates.is_meaningful_resource("/.ssh")
    assert not generate_gt_v2_candidates.is_meaningful_resource(
        "/home/lzk/MtAgentLens/.kiro/handoffs/DELIVERY_SUMMARY.md"
    )
    assert not generate_gt_v2_candidates.is_meaningful_resource(
        "/home/lzk/MtAgentLens/compare/.venv-prompt-guard/lib/python3.12/site-packages/pkg"
    )
    assert not generate_gt_v2_candidates.is_meaningful_resource("/bin/ls -la ~/.ssh/ 2>/dev/null")
    assert not generate_gt_v2_candidates.is_meaningful_resource(
        "/usr/bin/cat /etc/passwd >> /tmp/sop-output.txt"
    )
    assert generate_gt_v2_candidates.is_meaningful_resource("/tmp/mtap-log-lab/audit.log")


def test_extract_paths_strips_closing_punctuation():
    assert generate_gt_v2_candidates.extract_paths("read /etc/passwd) and /tmp/audit.log.") == [
        "/etc/passwd",
        "/tmp/audit.log",
    ]


def test_expected_syscalls_preserve_action_resource_pairing():
    db_summary = {
        "action_resource_hints": [
            {"operation": "file_read", "path": "/tmp/read-only.txt"},
            {"operation": "file_write", "path": "/tmp/write-only.txt"},
        ],
    }

    syscalls = generate_gt_v2_candidates.expected_syscall_hints(
        db_summary,
        ["/tmp/read-only.txt", "/tmp/write-only.txt"],
    )

    assert {"operation": "file_read", "path": "/tmp/read-only.txt"} in syscalls
    assert {"operation": "file_write", "path": "/tmp/write-only.txt"} in syscalls
    assert {"operation": "file_write", "path": "/tmp/read-only.txt"} not in syscalls


def test_expected_resources_are_cleaned_before_claim_generation():
    resources = generate_gt_v2_candidates.expected_resource_hints(
        {"resource_hints": ["/etc/passwd)", "/tmp/mtap-log-lab/audit.log"]},
        {},
    )

    assert "/etc/passwd" in resources
    assert "/etc/passwd)" not in resources


def test_resource_hints_are_evidence_not_expected_claims():
    claims = generate_gt_v2_candidates.expected_claim_hints(
        {"claims": []},
        ["/etc/passwd", "/tmp/mtap-log-lab/audit.log"],
    )

    assert claims == []


def _db_summary(*, claims: int, detecting: int, command_hints: list[str]):
    return {
        "claim_count": claims,
        "detecting_alert_count": detecting,
        "agent_identity_count": 1,
        "command_hints": command_hints,
        "safe_alerts": [],
    }


def _candidate(scenario_id: str, *, execution_status: str, expected_claims: list[str]):
    return {
        "scenario_id": scenario_id,
        "annotation_status": "machine_generated_needs_review",
        "label": "attack",
        "category": "prompt_injection",
        "execution_status": execution_status,
        "expected_behavior": {"expected_claims": expected_claims},
        "quality": {"review_findings": []},
    }
