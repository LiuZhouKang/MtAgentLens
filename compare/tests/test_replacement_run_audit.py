import importlib.util
import sqlite3
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "audit" / "audit_replacement_runs.py"
SPEC = importlib.util.spec_from_file_location("audit_replacement_runs", MODULE_PATH)
assert SPEC is not None
audit_replacement_runs = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = audit_replacement_runs
SPEC.loader.exec_module(audit_replacement_runs)


def test_candidate_score_prefers_fixture_match_over_higher_wrong_fixture_claims(tmp_path: Path):
    wrong = _candidate(
        tmp_path,
        "wrong",
        expected_fixture="workflow_docs/pre-commit-checklist",
        observed_fixture="multi_agent_attack/fragmented-payload/docs.md",
        claims=3,
        detecting=3,
    )
    matched = _candidate(
        tmp_path,
        "matched",
        expected_fixture="workflow_docs/pre-commit-checklist",
        observed_fixture="workflow_docs/pre-commit-checklist/docs.md",
        claims=0,
        detecting=0,
    )

    candidates = [wrong, matched]
    candidates.sort(key=lambda candidate: candidate.score(), reverse=True)

    assert candidates[0].run_dir.name == "matched"
    assert candidates[0].fixture_match is True
    assert candidates[1].fixture_match is False


def test_candidate_score_prefers_clean_claim_policy_over_cross_scenario_policy(tmp_path: Path):
    contaminated = _candidate(
        tmp_path,
        "contaminated",
        expected_fixture="workflow_docs/pre-commit-checklist",
        observed_fixture="workflow_docs/pre-commit-checklist/docs.md",
        claims=3,
        detecting=3,
        policy_ids=["policy.content.canary_disclosure", "policy.content.resource_abuse"],
    )
    clean = _candidate(
        tmp_path,
        "clean",
        expected_fixture="workflow_docs/pre-commit-checklist",
        observed_fixture="workflow_docs/pre-commit-checklist/docs.md",
        claims=1,
        detecting=1,
        policy_ids=["policy.content.canary_disclosure"],
    )

    candidates = [contaminated, clean]
    candidates.sort(key=lambda candidate: candidate.score(), reverse=True)

    assert candidates[0].run_dir.name == "clean"
    assert candidates[0].cross_fixture_contamination is False
    assert candidates[1].cross_fixture_contamination is True


def test_build_report_restores_fixture_matched_run_when_frozen_db_is_wrong(tmp_path: Path, monkeypatch):
    dataset = tmp_path / "dataset"
    trace_dir = dataset / "traces" / "prompt_injection" / "prompt-injection"
    trace_dir.mkdir(parents=True)
    frozen_db = trace_dir / "responsibility.db"
    _write_db(
        frozen_db,
        observed_fixture="multi_agent_attack/fragmented-payload/docs.md",
        claims=1,
        detecting=1,
    )
    sample = {
        "scenario_id": "prompt-injection",
        "paths": {
            "responsibility_db": "traces/prompt_injection/prompt-injection/responsibility.db"
        },
    }
    manifest = {"samples": [sample]}
    matched = _candidate(
        tmp_path,
        "matched",
        expected_fixture="workflow_docs/pre-commit-checklist",
        observed_fixture="workflow_docs/pre-commit-checklist/docs.md",
        claims=0,
        detecting=0,
    )

    monkeypatch.setattr(audit_replacement_runs, "load_expected_fixture_dirs",
        lambda: {"prompt-injection": "workflow_docs/pre-commit-checklist"})
    report = audit_replacement_runs.build_report(
        dataset,
        manifest,
        ["prompt-injection"],
        {"prompt-injection": [matched]},
    )

    rec = report["recommendations"][0]
    assert rec["action"] == "restore_fixture_matched_run"
    assert rec["frozen"]["fixture_match"] is False
    assert rec["best_candidate"]["fixture_match"] is True


def test_build_report_marks_verifier_pass_without_backend_alert_as_false_negative(tmp_path: Path, monkeypatch):
    dataset = tmp_path / "dataset"
    trace_dir = dataset / "traces" / "prompt_injection" / "prompt-injection"
    trace_dir.mkdir(parents=True)
    frozen_db = trace_dir / "responsibility.db"
    _write_db(
        frozen_db,
        observed_fixture="workflow_docs/pre-commit-checklist/docs.md",
        claims=0,
        detecting=0,
    )
    sample = {
        "scenario_id": "prompt-injection",
        "paths": {
            "responsibility_db": "traces/prompt_injection/prompt-injection/responsibility.db"
        },
    }
    manifest = {"samples": [sample]}
    matched = _candidate(
        tmp_path,
        "matched",
        expected_fixture="workflow_docs/pre-commit-checklist",
        observed_fixture="workflow_docs/pre-commit-checklist/docs.md",
        claims=0,
        detecting=0,
    )

    monkeypatch.setattr(audit_replacement_runs, "load_expected_fixture_dirs",
        lambda: {"prompt-injection": "workflow_docs/pre-commit-checklist"})
    report = audit_replacement_runs.build_report(
        dataset,
        manifest,
        ["prompt-injection"],
        {"prompt-injection": [matched]},
        {
            "prompt-injection": {
                "recommended_execution_status": "executed_false_negative",
                "verifier_status": "pass",
            }
        },
    )

    rec = report["recommendations"][0]
    assert rec["action"] == "backend_false_negative"
    assert "verifier passed" in rec["reason"]


def _candidate(
    tmp_path: Path,
    name: str,
    *,
    expected_fixture: str,
    observed_fixture: str,
    claims: int,
    detecting: int,
    policy_ids: list[str] | None = None,
):
    run_dir = tmp_path / name
    db_path = run_dir / "collector" / "collector.responsibility.db"
    db_path.parent.mkdir(parents=True)
    _write_db(
        db_path,
        observed_fixture=observed_fixture,
        claims=claims,
        detecting=detecting,
        policy_ids=policy_ids,
    )
    return audit_replacement_runs.read_candidate("prompt-injection", run_dir, db_path, expected_fixture)


def _write_db(
    path: Path,
    *,
    observed_fixture: str,
    claims: int,
    detecting: int,
    policy_ids: list[str] | None = None,
) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("create table intents (tool_args_json text)")
        con.execute(
            "insert into intents values (?)",
            (f'[{{"Path":"/repo/env/scenarios/fixtures/{observed_fixture}"}}]',),
        )
        con.execute("create table responsibility_claims (claim_id text, policy_id text)")
        policy_ids = policy_ids or ["policy.test"]
        for index in range(claims):
            con.execute(
                "insert into responsibility_claims values (?, ?)",
                (f"claim-{index}", policy_ids[index % len(policy_ids)]),
            )
        con.execute("create table responsibility_alerts (decision text)")
        for _ in range(detecting):
            con.execute("insert into responsibility_alerts values ('Unsafe')")
        con.execute("create table actions (id text)")
        con.execute("create table correlation_evidence (id text)")
        con.execute("create table agent_identities (id text)")
        con.execute("create table subagent_boundaries (id text)")
        con.commit()
    finally:
        con.close()
