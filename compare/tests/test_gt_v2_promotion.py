import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dataset" / "promote_gt_v2.py"
SPEC = importlib.util.spec_from_file_location("promote_gt_v2", MODULE_PATH)
assert SPEC is not None
promote_gt_v2 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = promote_gt_v2
SPEC.loader.exec_module(promote_gt_v2)


def test_rejects_attack_candidate_without_expected_claims(tmp_path: Path):
    dataset = _dataset_root(tmp_path)
    _write_case(dataset, "attack-one", "prompt_injection", expected_claims=[])

    with pytest.raises(promote_gt_v2.PromotionError, match="expected_claims"):
        promote_gt_v2.promote_dataset(dataset)


def test_rejects_command_like_resource_expected_claim(tmp_path: Path):
    dataset = _dataset_root(tmp_path)
    _write_case(
        dataset,
        "attack-one",
        "prompt_injection",
        expected_claims=["boundary.resource./bin/ls -la ~/.ssh/ 2>/dev/null"],
    )

    with pytest.raises(promote_gt_v2.PromotionError, match="noisy expected_claim"):
        promote_gt_v2.promote_dataset(dataset)


def test_promotes_candidate_with_auditable_provenance(tmp_path: Path):
    dataset = _dataset_root(tmp_path)
    _write_case(
        dataset,
        "attack-one",
        "prompt_injection",
        expected_claims=["claim.PolicyViolation.policy.content.prompt.boundary.content.prompt.High"],
    )
    _write_case(dataset, "benign-one", "benign", label="benign", attack_type="benign", expected_claims=[])

    report = promote_gt_v2.promote_dataset(dataset)

    promoted = json.loads((dataset / "ground_truth" / "prompt_injection" / "attack-one.json").read_text())
    benign = json.loads((dataset / "ground_truth" / "benign" / "benign-one.json").read_text())
    assert report["summary"]["promoted"] == 2
    assert promoted["schema"] == "mtagentlens.ground_truth.v2"
    assert promoted["annotation_status"] == "auto_adjudicated_with_db_evidence"
    assert promoted["quality"]["paper_ready"] is True
    assert promoted["provenance"]["candidate_ground_truth"] == (
        "quality/ground_truth_v2_candidate/attack-one.json"
    )
    assert "promotion" in promoted["annotation"]
    assert benign["expected_behavior"]["should_alert"] is False
    assert benign["expected_behavior"]["expected_claims"] == []

    written_report = json.loads((dataset / "quality" / "gt_v2_promotion_report.json").read_text())
    assert written_report["summary"]["promoted"] == 2


def _dataset_root(tmp_path: Path) -> Path:
    dataset = tmp_path / "FuxiBench"
    for child in ("ground_truth", "traces", "quality/ground_truth_v2_candidate"):
        (dataset / child).mkdir(parents=True, exist_ok=True)
    return dataset


def _write_case(
    dataset: Path,
    scenario_id: str,
    category: str,
    *,
    label: str = "attack",
    attack_type: str = "B",
    expected_claims: list[str],
) -> None:
    gt_rel = f"ground_truth/{category}/{scenario_id}.json"
    db_rel = f"traces/{category}/{scenario_id}/responsibility.db"
    trace_dir = dataset / "traces" / category / scenario_id
    (dataset / "ground_truth" / category).mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / ".scenario_id").write_text(scenario_id, encoding="utf-8")
    (dataset / gt_rel).write_text(
        json.dumps({"scenario_id": scenario_id, "attack_type": attack_type, "result": "DETECTED"}),
        encoding="utf-8",
    )
    _write_db(dataset / db_rel, scenario_id, claim_count=0 if label == "benign" else 1)

    candidate = {
        "schema": "mtagentlens.ground_truth.v2.candidate",
        "annotation_status": "machine_generated_needs_review",
        "scenario_id": scenario_id,
        "label": label,
        "category": category,
        "attack_type": attack_type,
        "execution_status": "executed",
        "expected_behavior": {
            "should_alert": label == "attack",
            "min_decision": "Unsafe" if label == "attack" else "Safe",
            "expected_claims": expected_claims,
            "expected_syscalls": [],
            "expected_boundaries": [],
            "expected_topology": {
                "min_agents": 1,
                "requires_subagent_boundary": False,
                "observed_agent_identities": 1,
                "observed_subagent_boundaries": 0,
            },
            "derivation": {
                "source": "test",
                "confidence": "medium",
                "requires_human_review": True,
            },
        },
        "observed_behavior": {
            "claim_count": 0 if label == "benign" else 1,
            "detecting_alert_count": 0 if label == "benign" else 1,
            "agent_identity_count": 1,
            "table_counts": {"correlation_evidence": 1},
        },
        "evidence_contract": {
            "db_required": True,
            "minimum_db_evidence": {
                "responsibility_claims": 0 if label == "benign" else 1,
                "detecting_alerts": 0 if label == "benign" else 1,
                "agent_identities": 1,
                "correlation_evidence": 1,
            },
        },
        "provenance": {
            "legacy_ground_truth": gt_rel,
            "responsibility_db": db_rel,
            "trace": f"traces/{category}/{scenario_id}",
        },
        "quality": {
            "review_findings": ["machine_generated_candidate_requires_review"],
            "ready_for_manual_adjudication": True,
            "paper_ready": False,
        },
        "annotation": {
            "annotators": ["codex-auto-evidence-extractor"],
            "adjudication": "pending",
            "notes": "test candidate",
        },
    }
    (dataset / "quality" / "ground_truth_v2_candidate" / f"{scenario_id}.json").write_text(
        json.dumps(candidate),
        encoding="utf-8",
    )

    sample = {
        "scenario_id": scenario_id,
        "category": category,
        "quality_tier": "needs_relabel",
        "paths": {
            "ground_truth": gt_rel,
            "trace": f"traces/{category}/{scenario_id}",
            "responsibility_db": db_rel,
        },
    }
    _append_manifest_sample(dataset, sample)


def _append_manifest_sample(dataset: Path, sample: dict[str, object]) -> None:
    path = dataset / "quality" / "manifest.json"
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "schema": "mtagentlens.dataset_quality.v1",
            "dataset": dataset.name,
            "summary": {},
            "samples": [],
        }
    manifest["samples"].append(sample)
    manifest["scenario_ids"] = [item["scenario_id"] for item in manifest["samples"]]
    manifest["summary"]["total_samples"] = len(manifest["samples"])
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _write_db(path: Path, scenario_id: str, claim_count: int) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("create table runs (run_id text, scenario_id text)")
        con.execute("insert into runs values ('run-1', ?)", (scenario_id,))
        con.execute("create table responsibility_claims (claim_id text)")
        for index in range(claim_count):
            con.execute("insert into responsibility_claims values (?)", (f"claim-{index}",))
        con.execute("create table responsibility_alerts (alert_id text, decision text)")
        if claim_count:
            con.execute("insert into responsibility_alerts values ('alert-1', 'Unsafe')")
        else:
            con.execute("insert into responsibility_alerts values ('alert-1', 'Safe')")
        con.execute("create table agent_identities (id text)")
        con.execute("insert into agent_identities values ('agent-1')")
        con.execute("create table correlation_evidence (id text)")
        con.execute("insert into correlation_evidence values ('evidence-1')")
        con.commit()
    finally:
        con.close()
