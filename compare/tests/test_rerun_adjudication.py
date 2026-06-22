import importlib.util
import sqlite3
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "legacy" / "adjudicate_rerun_evidence.py"
SPEC = importlib.util.spec_from_file_location("adjudicate_rerun_evidence", MODULE_PATH)
assert SPEC is not None
adjudicate_rerun_evidence = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = adjudicate_rerun_evidence
SPEC.loader.exec_module(adjudicate_rerun_evidence)


def test_verifier_pass_with_no_claims_is_false_negative(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run"
    db_path = run_dir / "collector" / "trace.responsibility.db"
    log_path = run_dir / "logs" / "agent_0.log"
    db_path.parent.mkdir(parents=True)
    log_path.parent.mkdir(parents=True)
    log_path.write_text("Executed benchmark successfully\n", encoding="utf-8")
    _write_db(db_path, claims=0, detecting=0)
    monkeypatch.setattr(
        adjudicate_rerun_evidence,
        "run_verifier",
        lambda scenario_id, run_dir: {"status": "pass", "details": "executed"},
    )

    evidence = adjudicate_rerun_evidence.read_evidence("scenario", run_dir, db_path, log_path)

    assert evidence.recommended_status() == "executed_false_negative"
    assert evidence.recommended_tier() == "needs_relabel"
    assert evidence.to_json()["verifier_status"] == "pass"


def _write_db(path: Path, *, claims: int, detecting: int) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("create table responsibility_claims (claim_id text)")
        for index in range(claims):
            con.execute("insert into responsibility_claims values (?)", (f"claim-{index}",))
        con.execute("create table responsibility_alerts (decision text)")
        for _ in range(detecting):
            con.execute("insert into responsibility_alerts values ('Unsafe')")
        con.execute("insert into responsibility_alerts values ('Safe')")
        con.execute("create table agent_identities (id text)")
        con.execute("insert into agent_identities values ('agent-1')")
        con.execute("create table correlation_evidence (id text)")
        con.execute("insert into correlation_evidence values ('evidence-1')")
        con.execute("create table actions (id text)")
        con.execute("insert into actions values ('action-1')")
        con.commit()
    finally:
        con.close()
