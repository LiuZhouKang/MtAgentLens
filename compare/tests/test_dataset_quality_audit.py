import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dataset" / "dataset_quality_audit.py"
SPEC = importlib.util.spec_from_file_location("dataset_quality_audit", MODULE_PATH)
assert SPEC is not None
dataset_quality_audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = dataset_quality_audit
SPEC.loader.exec_module(dataset_quality_audit)


def test_gt_v2_contract_can_be_paper_ready(tmp_path: Path):
    dataset = _dataset_root(tmp_path)
    _write_sample(
        dataset,
        scenario_id="attack-one",
        category="prompt_injection",
        ground_truth={
            "scenario_id": "attack-one",
            "label": "attack",
            "category": "prompt_injection",
            "attack_type": "B",
            "execution_status": "executed",
            "expected_behavior": {
                "should_alert": True,
                "min_decision": "Unsafe",
                "expected_claims": ["boundary.file./etc/shadow"],
                "expected_syscalls": [{"path": "/etc/shadow", "syscall": "openat"}],
            },
            "evidence_contract": {"db_required": True, "agent_log_required": True},
        },
        claim_count=1,
        alert_decision="Unsafe",
    )

    audits = dataset_quality_audit.audit_dataset(dataset)
    manifest = dataset_quality_audit.build_manifest(dataset, audits)

    assert manifest["summary"]["paper_ready"] == 1
    assert audits[0].blocking_issues == []
    assert audits[0].gt["expected_claims_count"] == 1
    assert audits[0].gt["expected_syscalls_count"] == 1


def test_legacy_ground_truth_is_needs_relabel(tmp_path: Path):
    dataset = _dataset_root(tmp_path)
    _write_sample(
        dataset,
        scenario_id="legacy-one",
        category="reconnaissance",
        ground_truth={
            "scenario_id": "legacy-one",
            "attack_type": "B",
            "result": "DETECTED",
        },
        claim_count=1,
        alert_decision="Unsafe",
    )

    audits = dataset_quality_audit.audit_dataset(dataset)

    assert audits[0].quality_tier == "needs_relabel"
    assert "rewrite_ground_truth_contract" in audits[0].actions_required
    assert any(issue.startswith("gt_contract_missing:") for issue in audits[0].blocking_issues)


def test_type_c_missing_subagent_boundaries_is_warning_not_main_metric_blocker(tmp_path: Path):
    dataset = _dataset_root(tmp_path)
    _write_sample(
        dataset,
        scenario_id="type-c-one",
        category="agent_teams",
        ground_truth={
            "scenario_id": "type-c-one",
            "label": "attack",
            "category": "agent_teams",
            "attack_type": "C",
            "execution_status": "executed",
            "expected_behavior": {
                "should_alert": True,
                "min_decision": "Unsafe",
                "expected_claims": ["claim.TaintedContextUse.policy.content.role_hijack"],
            },
            "evidence_contract": {"db_required": True, "agent_log_required": True},
        },
        claim_count=1,
        alert_decision="Unsafe",
        agent_identity_count=2,
        subagent_boundary_count=0,
    )

    audits = dataset_quality_audit.audit_dataset(dataset)

    assert audits[0].quality_tier == "paper_ready"
    assert "type_c_has_no_subagent_boundaries" in audits[0].warnings


def test_duplicate_trace_ids_are_rejected(tmp_path: Path):
    dataset = _dataset_root(tmp_path)
    _write_sample(
        dataset,
        scenario_id="duplicate-one",
        category="prompt_injection",
        ground_truth={"scenario_id": "duplicate-one"},
        claim_count=0,
        alert_decision="Safe",
    )
    other = dataset / "traces" / "other" / "duplicate-one-copy"
    other.mkdir(parents=True)
    (other / ".scenario_id").write_text("duplicate-one", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate trace id"):
        dataset_quality_audit.audit_dataset(dataset)


def _dataset_root(tmp_path: Path) -> Path:
    dataset = tmp_path / "FuxiBench"
    for child in ("ground_truth", "traces", "case_studies"):
        (dataset / child).mkdir(parents=True)
    return dataset


def _write_sample(
    dataset: Path,
    *,
    scenario_id: str,
    category: str,
    ground_truth: dict[str, object],
    claim_count: int,
    alert_decision: str,
    agent_identity_count: int = 1,
    subagent_boundary_count: int = 0,
) -> None:
    gt_dir = dataset / "ground_truth" / category
    trace_dir = dataset / "traces" / category / scenario_id
    gt_dir.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)
    (gt_dir / f"{scenario_id}.json").write_text(json.dumps(ground_truth), encoding="utf-8")
    (trace_dir / ".scenario_id").write_text(scenario_id, encoding="utf-8")
    (dataset / "case_studies" / f"01_{scenario_id}.md").write_text("# Case\n", encoding="utf-8")
    _write_complete_db(
        trace_dir / "responsibility.db",
        scenario_id,
        claim_count,
        alert_decision,
        agent_identity_count=agent_identity_count,
        subagent_boundary_count=subagent_boundary_count,
    )


def _write_complete_db(
    path: Path,
    scenario_id: str,
    claim_count: int,
    alert_decision: str,
    *,
    agent_identity_count: int = 1,
    subagent_boundary_count: int = 0,
) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute(
            "create table runs (run_id text, started_at_ns integer, ended_at_ns integer, scenario_id text, mode text, collector_version text)"
        )
        con.execute(
            "insert into runs values ('run-1', 1, 2, ?, 'test', 'test')",
            (scenario_id,),
        )
        con.execute("create table schema_version (version integer)")
        con.execute("insert into schema_version values (1)")
        for table in (
            "process_events",
            "tls_sessions",
            "message_bindings",
            "intents",
            "actions",
            "correlation_evidence",
            "graph_nodes",
            "graph_edges",
        ):
            con.execute(f"create table {table} (id text)")
            con.execute(f"insert into {table} values ('row-1')")
        con.execute("create table agent_identities (id text)")
        for index in range(agent_identity_count):
            con.execute("insert into agent_identities values (?)", (f"agent-{index}",))
        con.execute(
            "create table responsibility_alerts (alert_id text, decision text, severity text)"
        )
        con.execute(
            "insert into responsibility_alerts values ('alert-1', ?, 'Warning')",
            (alert_decision,),
        )
        con.execute(
            "create table responsibility_claims (claim_id text, claim_state text, severity text)"
        )
        for index in range(claim_count):
            con.execute(
                "insert into responsibility_claims values (?, 'Observed', 'Warning')",
                (f"claim-{index}",),
            )
        con.execute("create table subagent_boundaries (id text)")
        for index in range(subagent_boundary_count):
            con.execute("insert into subagent_boundaries values (?)", (f"boundary-{index}",))
        con.commit()
    finally:
        con.close()
