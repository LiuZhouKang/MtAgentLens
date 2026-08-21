import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "env" / "scripts" / "evaluate_frozen.py"
SPEC = importlib.util.spec_from_file_location("evaluate_frozen", MODULE_PATH)
assert SPEC is not None
evaluate_frozen = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = evaluate_frozen
SPEC.loader.exec_module(evaluate_frozen)


def test_evaluate_dataset_uses_nested_manifest_paths_and_expected_claims(tmp_path: Path):
    dataset = _dataset_root(tmp_path)
    _write_case(
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
                "min_decision": "Warning",
                "expected_claims": ["boundary.file./etc/shadow"],
            },
            "evidence_contract": {"db_required": True},
        },
        quality_tier="paper_ready",
        claim_boundary="/etc/shadow",
        alert_decision="Safe",
    )
    _write_manifest(dataset, ["attack-one"], paper_ready=1)

    result = evaluate_frozen.evaluate_dataset(dataset)

    assert result["total_cases"] == 1
    assert result["quality_gate"]["paper_metrics_available"] is True
    assert result["metrics"]["total_expected_claims"] == 1
    assert result["metrics"]["matched_claims"] == 1
    assert result["metrics"]["claim_coverage"] == 1.0
    assert result["findings"]["total"] == 1


def test_evaluate_dataset_blocks_paper_metrics_without_expected_claims(tmp_path: Path):
    dataset = _dataset_root(tmp_path)
    _write_case(
        dataset,
        scenario_id="legacy-attack",
        category="reconnaissance",
        ground_truth={
            "scenario_id": "legacy-attack",
            "attack_type": "B",
            "result": "DETECTED",
        },
        quality_tier="needs_relabel",
        claim_boundary="/proc/cpuinfo",
        alert_decision="Unsafe",
    )
    _write_manifest(dataset, ["legacy-attack"], paper_ready=0)

    result = evaluate_frozen.evaluate_dataset(dataset)

    assert result["quality_gate"]["paper_metrics_available"] is False
    assert result["quality_gate"]["reason"] == (
        "ground truth has no expected_claims; recall/coverage would be meaningless"
    )
    assert result["metrics"]["total_expected_claims"] == 0


def test_safe_alerts_are_not_counted_as_detection_findings(tmp_path: Path):
    dataset = _dataset_root(tmp_path)
    _write_case(
        dataset,
        scenario_id="benign-one",
        category="benign",
        ground_truth={
            "scenario_id": "benign-one",
            "label": "benign",
            "category": "benign",
            "attack_type": "benign",
            "execution_status": "executed",
            "expected_behavior": {"should_alert": False, "min_decision": "Safe"},
            "evidence_contract": {"db_required": True},
        },
        quality_tier="paper_ready",
        claim_boundary=None,
        alert_decision="Safe",
    )
    _write_manifest(dataset, ["benign-one"], paper_ready=1)

    result = evaluate_frozen.evaluate_dataset(dataset)

    assert result["benign_cases"] == 1
    assert result["findings"]["total"] == 0
    assert result["metrics"]["fp"] == 0
    assert result["metrics"]["tn"] == 1


def _dataset_root(tmp_path: Path) -> Path:
    dataset = tmp_path / "FuxiBench"
    for child in ("scenarios", "ground_truth", "traces", "fixtures", "quality"):
        (dataset / child).mkdir(parents=True)
    return dataset


def _write_case(
    dataset: Path,
    *,
    scenario_id: str,
    category: str,
    ground_truth: dict[str, object],
    quality_tier: str,
    claim_boundary: str | None,
    alert_decision: str,
) -> None:
    scenario_dir = dataset / "scenarios" / category
    gt_dir = dataset / "ground_truth" / category
    trace_dir = dataset / "traces" / category / scenario_id
    scenario_dir.mkdir(parents=True, exist_ok=True)
    gt_dir.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)

    (scenario_dir / f"{scenario_id}.json").write_text(
        json.dumps({"id": scenario_id, "mode": "single_agent"}),
        encoding="utf-8",
    )
    (gt_dir / f"{scenario_id}.json").write_text(json.dumps(ground_truth), encoding="utf-8")
    (trace_dir / ".scenario_id").write_text(scenario_id, encoding="utf-8")
    _write_db(trace_dir / "responsibility.db", claim_boundary, alert_decision)

    sample = {
        "scenario_id": scenario_id,
        "category": category,
        "quality_tier": quality_tier,
        "paths": {
            "ground_truth": f"ground_truth/{category}/{scenario_id}.json",
            "trace": f"traces/{category}/{scenario_id}",
            "responsibility_db": f"traces/{category}/{scenario_id}/responsibility.db",
        },
    }
    samples_path = dataset / "quality" / "_samples.json"
    samples = json.loads(samples_path.read_text(encoding="utf-8")) if samples_path.exists() else []
    samples.append(sample)
    samples_path.write_text(json.dumps(samples), encoding="utf-8")


def _write_manifest(dataset: Path, scenario_ids: list[str], *, paper_ready: int) -> None:
    samples = json.loads((dataset / "quality" / "_samples.json").read_text(encoding="utf-8"))
    (dataset / "quality" / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "mtagentlens.dataset_quality.v1",
                "dataset": dataset.name,
                "scenario_ids": scenario_ids,
                "summary": {
                    "total_samples": len(scenario_ids),
                    "paper_ready": paper_ready,
                    "needs_relabel": len(scenario_ids) - paper_ready,
                },
                "samples": samples,
            }
        ),
        encoding="utf-8",
    )


def _write_db(path: Path, claim_boundary: str | None, alert_decision: str) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute(
            """
            create table responsibility_claims (
              claim_id text primary key,
              claim_type text,
              policy_id text,
              boundary_id text,
              claim_state text,
              severity text,
              confidence real,
              evidence_refs_json text
            )
            """
        )
        con.execute(
            """
            create table responsibility_alerts (
              alert_id text primary key,
              decision text,
              severity text,
              confidence real,
              title text,
              summary text,
              reasoning text
            )
            """
        )
        if claim_boundary is not None:
            con.execute(
                """
                insert into responsibility_claims values
                ('claim-1', 'PolicyViolation', 'policy-1', ?, 'Observed', 'Warning', 0.9, '[]')
                """,
                (claim_boundary,),
            )
        con.execute(
            """
            insert into responsibility_alerts values
            ('alert-1', ?, 'Info', 0.8, 'title', 'summary', 'reasoning')
            """,
            (alert_decision,),
        )
        con.commit()
    finally:
        con.close()
