# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Seven-experiment suite audit: missing is not estimable, binary is not AUC."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts.evaluation import experiment_suite_audit as suite

RELEASE = "9c7099c1d36d21af025f5050d462b273cc04aef0c69d3ff72e902779372c6ea3"
COHORT = {"scenarios": 93, "attack": 58, "benign": 35}
ARTIFACT_SCHEMA = "test.experiment-evidence/1"


def write_json(path: Path, payload: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def metrics(experiment: str, data: dict, *, estimable: bool = True,
            limitations: list[str] | None = None) -> dict:
    return {
        "schema": suite.METRICS_SCHEMA,
        "experiment": experiment,
        "release_id": RELEASE,
        "estimable": estimable,
        "data": data,
        "limitations": limitations or [],
    }


def statistics(*, unit="scenario", family=None, reference_error=False) -> dict:
    payload = {
        "aggregation_unit": unit,
        "n_scenarios" if unit == "scenario" else "n_blocks": 93 if unit == "scenario" else 15,
        "bootstrap_resamples": 10000,
        "confidence_level": 0.95,
        "confidence_intervals": {"primary": [0.7, 0.9]},
        "coverage": 1.0,
        "abstention": 0.0,
    }
    if family is not None:
        payload["holm_family"] = list(family)
        payload["holm_adjusted_p"] = {name: 0.01 for name in family}
    if reference_error:
        payload["reference_error_sensitivity"] = {
            "0.10": {"lower": 0.7, "upper": 0.9},
            "0.20": {"lower": 0.6, "upper": 0.95},
        }
    return payload


def app_data(*, score_kind: str = "continuous") -> dict:
    arm = {
        "score_kind": score_kind,
        "n": 93,
        "auc": 0.9 if score_kind == "continuous" else None,
        "auprc": 0.8 if score_kind == "continuous" else None,
        "tpr": 0.7,
        "fpr": 0.1,
        "coverage": 1.0,
        "all": 93,
        "decided": 90,
        "executed": 58,
    }
    return {"arms": {name: dict(arm) for name in ("M", "A2")},
            "transcript_coverage": 1.0,
            "statistics": statistics(family=("auc_M_vs_A2",))}


def identity_data() -> dict:
    condition = {
        "units": 93,
        "coverage": 1.0,
        "pairwise_precision": 0.9,
        "pairwise_recall": 0.8,
        "pairwise_f1": 0.85,
        "b3": 0.88,
        "false_merge": 0.02,
        "false_split": 0.03,
        "unattributed": 0.01,
    }
    return {"conditions": {name: dict(condition) for name in ("C0", "C1", "C2", "C3", "C4")},
            "reference": {"units": 279, "coverage": 1.0},
            "statistics": statistics(
                family=("C4_vs_C0_pairwise_f1", "C4_vs_C0_b3"),
                reference_error=True)}


def attribution_data(*, ranked: bool = False) -> dict:
    method = {
        "root_at_1": 0.6,
        "rootset_precision": 0.7,
        "rootset_recall": 0.65,
        "rootset_f1": 0.67,
        "root_step_at_1": 0.5,
        "entry_accuracy": 0.55,
        "chain_precision": 0.5,
        "chain_recall": 0.45,
        "chain_f1": 0.47,
        "abstain": 0.1,
        "coverage": 0.9,
        "root_at_3": 0.8 if ranked else None,
        "mrr": 0.7 if ranked else None,
    }
    return {"methods": {name: dict(method) for name in ("B0", "B1", "B2", "M")},
            "n_attack_executed": 22, "reference_coverage": 1.0,
            "ranked_agents": ranked,
            "statistics": statistics(
                family=("M_vs_B0_root_at_1", "M_vs_B1_root_at_1"),
                reference_error=True)}


def capture_evidence(profile: str, *, status="measured", denominator=2,
                     raw=0.05, persisted=0.05) -> dict:
    if profile == "C0":
        return {"status": "not_applicable", "estimable": False,
                "operation_denominator": None,
                "raw_transport_missing_fraction": None,
                "persisted_operation_missing_fraction": None,
                "evidence": [], "reasons": ["C0 has no collector capture"]}
    evidence = [{
        "condition_id": "c1-r1-" + profile,
        "adapter_version": "controlled_file_open/v1",
        "receipt_combined_sha256": "a" * 64,
        "trace_sha256": "b" * 64,
        "database_sha256": None if profile == "C1" else "c" * 64,
    }]
    if status != "measured":
        return {"status": status, "estimable": False,
                "operation_denominator": None,
                "raw_transport_missing_fraction": None,
                "persisted_operation_missing_fraction": None,
                "evidence": evidence, "reasons": ["missing_raw_trace"]}
    return {"status": "measured", "estimable": True,
            "operation_denominator": denominator,
            "raw_transport_missing_fraction": raw,
            "persisted_operation_missing_fraction": (
                None if profile == "C1" else persisted),
            "evidence": evidence, "reasons": []}


def overhead_data(*, capture_measured: bool = False) -> dict:
    row = {"cpu_percent_p95": 12.0, "rss_kib_max": 2048, "db_bytes_peak": 4096,
           "event_to_claim_p95": 8.0, "claim_to_alert_p95": 40.0,
           "observer_tokens": 100, "cost_usd": 0.01}
    status = "measured" if capture_measured else "not_estimable"
    return {
        "conditions": {
            name: {"concurrency": {"1": dict(row), "4": dict(row)},
                   **({"observer_provider": "llm_env"} if name == "C3" else {})}
            for name in ("C0", "C1", "C2", "C3")
        },
        "controlled_capture": {
            name: capture_evidence(name, status=status)
            for name in ("C0", "C1", "C2", "C3")
        },
        "statistics": statistics(unit="block"),
    }


def full_metrics(*, capture_measured: bool = False) -> dict:
    overhead = overhead_data(capture_measured=capture_measured)
    return {
        "app": app_data(),
        "identity": identity_data(),
        "attribution": attribution_data(),
        "overhead": overhead,
        "sensitivity": {
            "grid": [{"threshold": 0.1 + index * 0.01, "gate_center": 10}
                     for index in range(15)],
            "points": 15,
            "bootstrap_resamples": 1000,
            "frozen_observer": True,
            "statistics": statistics(),
        },
        "pbc": {
            "raw_activity_baseline": True,
            "pbc_detector_derived": True,
            "tie_aware": True,
            "strata": {"pbc=0": {"n": 20}, "pbc>=10": {"n": 10}},
            "distribution": {"zero": 20, "positive": 73},
            "statistics": statistics(),
        },
        "ablation": {
            "layers": {name: {"score_kind": "continuous", "auc": 0.6 + index * 0.03,
                              "tpr": 0.5, "fpr": 0.1, "coverage": 1.0,
                              "delta_vs_L0": index * 0.03}
                       for index, name in enumerate(("L0", "L0.5", "L1", "L2", "L3"))},
            "hard_sensitive_egress": False,
            "shared_thresholds": True,
            "statistics": statistics(
                family=("L1_vs_L0_auc", "L2_vs_L1_auc", "L3_vs_L2_auc")),
        },
    }


def make_manifest(tmp_path: Path, metrics_by_experiment: dict, *,
                  stages: dict | None = None, release: str = RELEASE) -> Path:
    artifacts = []
    for experiment, data in metrics_by_experiment.items():
        required = stages.get(experiment, suite.EXPERIMENTS[experiment]["stages"]) if stages else suite.EXPERIMENTS[experiment]["stages"]
        for stage in required:
            if stage == "metrics":
                payload = metrics(experiment, data)
            else:
                payload = {"schema": ARTIFACT_SCHEMA, "release_id": release,
                           "experiment": experiment, "stage": stage}
            path = tmp_path / experiment / f"{stage}.json"
            binding = write_json(path, payload)
            artifacts.append({"id": f"{experiment}-{stage}", "experiment": experiment,
                              "stage": stage, "path": str(path.relative_to(tmp_path)),
                              "schema": payload["schema"], **binding})
    manifest = {"schema": suite.MANIFEST_SCHEMA, "release_id": release,
                "cohort": dict(COHORT), "artifacts": artifacts}
    path = tmp_path / "suite-manifest.json"
    write_json(path, manifest)
    return path


def rewrite_metrics(path: Path, mutate) -> None:
    manifest = json.loads(path.read_text())
    artifact = next(item for item in manifest["artifacts"] if item["stage"] == "metrics")
    artifact_path = path.parent / artifact["path"]
    payload = json.loads(artifact_path.read_text())
    mutate(payload)
    artifact_path.write_text(json.dumps(payload))
    artifact["sha256"] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest))


def test_plan_freezes_seven_experiments_and_five_tables():
    plan = suite.plan()
    assert plan["schema"] == suite.PLAN_SCHEMA
    assert set(plan["experiments"]) == {
        "identity", "attribution", "app", "overhead", "sensitivity", "pbc", "ablation"}
    # Seven experiments, five result tables: PBC/Ablation/Sensitivity are the
    # three parts of T7, not three separate paper tables.
    assert {spec["table"] for spec in plan["experiments"].values()} == {
        "T4", "T5", "T6", "T7", "T8"}
    assert {"pbc", "ablation", "sensitivity"} == {
        name for name, spec in plan["experiments"].items() if spec["table"] == "T7"}
    assert plan["experiments"]["identity"]["required_stages"] == [
        "producer", "export", "reference", "metrics"]
    assert plan["experiments"]["app"]["required_stages"] == ["producer", "export", "metrics"]
    assert "binary AppBench arms cannot carry AUROC/AUPRC" in " ".join(plan["rules"])


def test_missing_stages_are_not_estimable_and_tables_stay_null(tmp_path):
    path = make_manifest(tmp_path, {})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["valid"] is True
    assert all(result["status"] == "not_estimable"
               for result in audit["experiments"].values())
    for table in audit["tables"]["tables"].values():
        for row in table:
            assert row["status"] == "not_estimable"
    rows = audit["tables"]["tables"]["T4"]
    assert [row["arm"] for row in rows] == ["M", "A2"]
    assert all(row["auc"] is None for row in rows)
    markdown = suite.render_markdown(audit["tables"])
    assert "NOT ESTIMABLE" in markdown
    assert "| 0 |" not in markdown


def test_full_synthetic_suite_renders_tables_but_overhead_loss_stays_null(tmp_path):
    path = make_manifest(tmp_path, full_metrics())
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["valid"] is True
    statuses = {name: result["status"] for name, result in audit["experiments"].items()}
    assert statuses == {
        "identity": "estimable", "attribution": "estimable", "app": "estimable",
        "overhead": "partial", "sensitivity": "estimable", "pbc": "estimable",
        "ablation": "estimable",
    }
    assert audit["experiments"]["overhead"]["primary_endpoint_estimable"] is False
    t4 = audit["tables"]["tables"]["T4"]
    assert [row["arm"] for row in t4] == ["M", "A2"]
    assert t4[0]["auc"] == 0.9 and t4[0]["status"] == "estimable"
    t5 = audit["tables"]["tables"]["T5"]
    assert [row["condition"] for row in t5] == ["C0", "C1", "C2", "C3", "C4"]
    assert t5[0]["b3"] == 0.88
    t6 = audit["tables"]["tables"]["T6"]
    assert [row["method"] for row in t6] == ["B0", "B1", "B2", "M"]
    assert all(row["root_at_3"] is None and row["mrr"] is None for row in t6)
    assert all(row["status"] == "unranked_rank3_mrr_not_reported" for row in t6)
    t8 = audit["tables"]["tables"]["T8"]
    assert len(t8) == 8
    assert all(row["raw_transport_missing_fraction"] is None for row in t8)
    assert all(row["persisted_operation_missing_fraction"] is None for row in t8)
    assert any(row["status"] == "capture_not_estimable" for row in t8)
    assert any(row["capture_status"] == "not_applicable" for row in t8)


def test_app_binary_arms_do_not_invent_auc_or_probabilities(tmp_path):
    data = app_data(score_kind="binary")
    path = make_manifest(tmp_path, {"app": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    result = audit["experiments"]["app"]
    assert result["status"] == "partial"
    assert result["primary_endpoint_estimable"] is False
    assert "AUROC/AUPRC are not estimable" in result["disclosure"]
    rows = audit["tables"]["tables"]["T4"]
    assert all(row["auc"] is None and row["auprc"] is None for row in rows)
    assert all(row["tpr"] == 0.7 and row["fpr"] == 0.1 for row in rows)
    assert all(row["status"] == "binary_only_auc_not_estimable" for row in rows)


def test_binary_arm_with_auc_is_invalid_not_silently_kept(tmp_path):
    data = app_data(score_kind="binary")
    data["arms"]["A2"]["auc"] = 0.77
    path = make_manifest(tmp_path, {"app": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["valid"] is False
    assert audit["experiments"]["app"]["status"] == "invalid"
    assert "must not report AUROC/AUPRC" in " ".join(audit["errors"])


def test_missing_statistics_envelope_is_not_estimable(tmp_path):
    data = app_data()
    data.pop("statistics")
    path = make_manifest(tmp_path, {"app": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    result = audit["experiments"]["app"]
    assert result["status"] == "not_estimable"
    assert any("statistics" in reason for reason in result["reasons"])


def test_unit_level_repeats_are_rejected(tmp_path):
    data = identity_data()
    data["statistics"]["aggregation_unit"] = "unit"
    path = make_manifest(tmp_path, {"identity": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    result = audit["experiments"]["identity"]
    assert result["status"] == "not_estimable"
    assert any("aggregation_unit" in reason for reason in result["reasons"])


def test_holm_family_and_adjusted_p_values_must_match(tmp_path):
    data = attribution_data()
    data["statistics"]["holm_adjusted_p"] = {"M_vs_B0_root_at_1": 0.01}
    path = make_manifest(tmp_path, {"attribution": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    result = audit["experiments"]["attribution"]
    assert result["status"] == "not_estimable"
    assert any("holm_adjusted_p" in reason for reason in result["reasons"])


def test_identity_requires_reference_error_sensitivity(tmp_path):
    data = identity_data()
    data["statistics"].pop("reference_error_sensitivity")
    path = make_manifest(tmp_path, {"identity": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    result = audit["experiments"]["identity"]
    assert result["status"] == "not_estimable"
    assert any("reference_error_sensitivity" in reason for reason in result["reasons"])


def test_identity_without_reference_stage_is_not_estimable(tmp_path):
    path = make_manifest(tmp_path, {"identity": identity_data()},
                         stages={"identity": ("producer", "export", "metrics")})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    result = audit["experiments"]["identity"]
    assert result["status"] == "not_estimable"
    assert result["missing_stages"] == ["reference"]
    assert all(row["b3"] is None for row in audit["tables"]["tables"]["T5"])


def test_attribution_accepts_the_reference_adapter_metric_names(tmp_path):
    data = attribution_data()
    aliases = {
        "root_agent_at_1": "root_at_1",
        "root_set_precision": "rootset_precision",
        "root_set_recall": "rootset_recall",
        "root_set_f1": "rootset_f1",
        "entry_point_accuracy": "entry_accuracy",
        "untyped_chain_precision": "chain_precision",
        "untyped_chain_recall": "chain_recall",
        "untyped_chain_f1": "chain_f1",
    }
    for method in data["methods"].values():
        for source, target in aliases.items():
            method[source] = method.pop(target)
    path = make_manifest(tmp_path, {"attribution": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["valid"] is True, audit["errors"]
    assert audit["experiments"]["attribution"]["status"] == "estimable"
    row = audit["tables"]["tables"]["T6"][0]
    assert row["root_at_1"] == 0.6
    assert row["rootset_f1"] == 0.67
    assert row["entry_accuracy"] == 0.55
    assert row["chain_f1"] == 0.47


def test_attribution_cannot_report_rank3_without_ranked_predictions(tmp_path):
    data = attribution_data(ranked=False)
    data["methods"]["M"]["root_at_3"] = 0.8
    path = make_manifest(tmp_path, {"attribution": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["valid"] is False
    assert audit["experiments"]["attribution"]["status"] == "invalid"


def test_overhead_capture_needs_denominator_evidence_and_explicit_scope(tmp_path):
    data = overhead_data(capture_measured=True)
    data["controlled_capture"]["C2"]["operation_denominator"] = None
    path = make_manifest(tmp_path / "no-denominator", {"overhead": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["valid"] is False
    assert "operation denominator" in " ".join(audit["errors"])

    data = overhead_data(capture_measured=True)
    data["controlled_capture"]["C3"]["evidence"] = []
    path = make_manifest(tmp_path / "no-evidence", {"overhead": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["valid"] is False
    assert "artifact/source evidence" in " ".join(audit["errors"])

    data = overhead_data(capture_measured=True)
    data["controlled_capture"]["C2"].update(
        status="not_estimable", raw_transport_missing_fraction=0.0)
    path = make_manifest(tmp_path / "not-estimable", {"overhead": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["valid"] is False
    assert "must not carry a fraction" in " ".join(audit["errors"])


def test_measured_controlled_capture_is_rendered_in_t8_with_denominator(tmp_path):
    data = overhead_data(capture_measured=True)
    path = make_manifest(tmp_path, {"overhead": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["valid"] is True, audit["errors"]
    assert audit["experiments"]["overhead"]["status"] == "estimable"
    rows = audit["tables"]["tables"]["T8"]
    assert len(rows) == 8
    c0 = [row for row in rows if row["profile"] == "C0"]
    assert all(row["raw_transport_missing_fraction"] is None for row in c0)
    assert all(row["status"] == "not_applicable" for row in c0)
    measured = [row for row in rows if row["profile"] != "C0"]
    assert all(row["operation_denominator"] == 2 for row in measured)
    assert all(row["raw_transport_missing_fraction"] == 0.05 for row in measured)
    c1 = [row for row in rows if row["profile"] == "C1"]
    assert all(row["persisted_operation_missing_fraction"] is None for row in c1)
    c2 = [row for row in rows if row["profile"] == "C2"]
    assert all(row["persisted_operation_missing_fraction"] == 0.05 for row in c2)
    assert all(row["status"] == "estimable" for row in measured)


def test_sensitivity_requires_at_least_fifteen_frozen_points(tmp_path):
    data = full_metrics()["sensitivity"]
    data["grid"] = data["grid"][:14]
    path = make_manifest(tmp_path, {"sensitivity": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["experiments"]["sensitivity"]["status"] == "not_estimable"
    assert "at least 15" in " ".join(audit["experiments"]["sensitivity"]["reasons"])


def test_artifact_hash_release_and_cohort_are_enforced(tmp_path):
    data = full_metrics()
    path = make_manifest(tmp_path, {"app": data})
    manifest = suite.load_manifest(path)
    artifact = manifest["artifacts"][0]
    artifact_path = tmp_path / artifact["path"]
    artifact_path.write_text(json.dumps({"schema": ARTIFACT_SCHEMA,
                                         "release_id": RELEASE, "changed": True}))
    with pytest.raises(suite.ManifestError, match="sha256 mismatch"):
        suite.audit_manifest(manifest, base_dir=tmp_path)

    path = make_manifest(tmp_path / "release", {"app": data})
    manifest = suite.load_manifest(path)
    metrics_path = tmp_path / "release" / "app" / "metrics.json"
    metrics_path.write_text(json.dumps(metrics("app", data) | {"release_id": "other"}))
    manifest["artifacts"][-1]["sha256"] = hashlib.sha256(metrics_path.read_bytes()).hexdigest()
    with pytest.raises(suite.ManifestError, match="artifact release"):
        suite.audit_manifest(manifest, base_dir=tmp_path / "release")

    bad = {"schema": suite.MANIFEST_SCHEMA, "release_id": RELEASE,
           "cohort": {"scenarios": 84, "attack": 58, "benign": 26},
           "artifacts": []}
    bad_path = tmp_path / "bad-manifest.json"
    write_json(bad_path, bad)
    with pytest.raises(suite.ManifestError, match="cohort.scenarios"):
        suite.load_manifest(bad_path)


def test_cli_writes_audit_and_markdown_and_returns_nonzero_on_invalid(tmp_path, capsys):
    plan_path = tmp_path / "plan.json"
    assert suite.main(["plan", "--out", str(plan_path)]) == 0
    assert json.loads(plan_path.read_text())["schema"] == suite.PLAN_SCHEMA

    path = make_manifest(tmp_path / "good", full_metrics(capture_measured=True))
    out = tmp_path / "audit.json"
    markdown = tmp_path / "tables.md"
    assert suite.main(["audit", "--manifest", str(path), "--out", str(out),
                       "--markdown-out", str(markdown)]) == 0
    assert json.loads(out.read_text())["schema"] == suite.AUDIT_SCHEMA
    assert "T4 Detection + AppBench" in markdown.read_text()

    data = app_data(score_kind="binary")
    data["arms"]["A2"]["auc"] = 0.77
    bad = make_manifest(tmp_path / "bad", {"app": data})
    assert suite.main(["audit", "--manifest", str(bad), "--out", str(tmp_path / "bad.json")]) == 1

    bad_hash = make_manifest(tmp_path / "hash", {"app": app_data()})
    manifest = json.loads(bad_hash.read_text())
    (tmp_path / "hash" / manifest["artifacts"][0]["path"]).write_text("{}")
    assert suite.main(["audit", "--manifest", str(bad_hash), "--out", str(tmp_path / "h.json")]) == 2
    assert "sha256 mismatch" in capsys.readouterr().err


def test_empty_artifacts_is_structurally_valid_but_cli_nonzero(tmp_path):
    path = make_manifest(tmp_path, {})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["valid"] is True
    assert audit["all_primary_endpoints_estimable"] is False
    assert audit["primary_endpoints_estimable"] is False
    assert audit["scientific_results_verified"] is False
    out = tmp_path / "empty-audit.json"
    assert suite.main(["audit", "--manifest", str(path), "--out", str(out)]) == 1
    report = json.loads(out.read_text())
    assert report["valid"] is True
    assert report["all_primary_endpoints_estimable"] is False
    assert report["scientific_results_verified"] is False


def test_partial_app_binary_cli_nonzero_and_writes_report(tmp_path):
    path = make_manifest(tmp_path, {"app": app_data(score_kind="binary")})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["valid"] is True
    assert audit["experiments"]["app"]["status"] == "partial"
    assert audit["experiments"]["app"]["primary_endpoint_estimable"] is False
    assert audit["all_primary_endpoints_estimable"] is False
    out = tmp_path / "binary-audit.json"
    assert suite.main(["audit", "--manifest", str(path), "--out", str(out)]) == 1
    report = json.loads(out.read_text())
    assert report["valid"] is True
    assert report["experiments"]["app"]["primary_endpoint_estimable"] is False
    assert report["scientific_results_verified"] is False


def test_full_estimable_suite_exits_zero_without_scientific_claim(tmp_path):
    path = make_manifest(tmp_path, full_metrics(capture_measured=True))
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["valid"] is True
    assert audit["all_primary_endpoints_estimable"] is True
    assert audit["scientific_results_verified"] is False
    out = tmp_path / "full-audit.json"
    assert suite.main(["audit", "--manifest", str(path), "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["scientific_results_verified"] is False
    assert report["tables"]["scientific_results_verified"] is False
    assert report["tables"]["structural_only"] is True
    assert "structural" in report["scientific_boundary"].lower()


def test_app_count_contract_rejects_fake_or_self_contradictory_counts(tmp_path):
    cases = {
        "n_one": lambda arms: arms["M"].update({"n": 1}),
        "fake_cohort": lambda arms: arms["M"].update(
            {"all": 1, "n": 1, "decided": 1, "executed": 1}),
        "decided_over_all": lambda arms: arms["M"].update({"decided": 94}),
        "executed_over_all": lambda arms: arms["M"].update({"executed": 94}),
        "bool_count": lambda arms: arms["M"].update({"all": True}),
        "coverage_mismatch": lambda arms: arms["M"].update({"coverage": 0.5}),
        "nan_rate": lambda arms: arms["M"].update({"auc": float("nan")}),
        "out_of_range": lambda arms: arms["M"].update({"tpr": 1.5}),
    }
    for name, mutate in cases.items():
        data = app_data()
        mutate(data["arms"])
        path = make_manifest(tmp_path / name, {"app": data})
        audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
        assert audit["valid"] is False, name
        assert audit["experiments"]["app"]["status"] == "invalid", name
        out = tmp_path / name / "audit.json"
        assert suite.main(["audit", "--manifest", str(path), "--out", str(out)]) == 1, name
        assert json.loads(out.read_text())["valid"] is False, name


def test_app_statistics_n_scenarios_must_bind_to_the_93_cohort(tmp_path):
    data = app_data()
    data["statistics"]["n_scenarios"] = 1
    path = make_manifest(tmp_path, {"app": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["valid"] is False
    assert audit["experiments"]["app"]["status"] == "invalid"
    assert any("n_scenarios" in reason for reason in audit["experiments"]["app"]["reasons"])


def test_metrics_cohort_if_reported_must_bind_to_expected_counts(tmp_path):
    for name, cohort in {
            "malformed": "93",
            "wrong": {"scenarios": 84, "attack": 58, "benign": 26}}.items():
        path = make_manifest(tmp_path / name, {"app": app_data()})
        rewrite_metrics(path, lambda payload, cohort=cohort: payload.update({"cohort": cohort}))
        audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
        assert audit["valid"] is False, name
        assert audit["experiments"]["app"]["status"] == "invalid", name

    path = make_manifest(tmp_path / "bound", {"app": app_data()})
    rewrite_metrics(path, lambda payload: payload.update({"cohort": dict(COHORT)}))
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["valid"] is True, audit["errors"]
    assert audit["experiments"]["app"]["status"] == "estimable"


def test_ablation_partial_state_and_counts_only_l0_are_not_estimable(tmp_path):
    data = full_metrics()["ablation"]
    data["layers"]["L1"]["state"] = "partial"
    path = make_manifest(tmp_path / "partial", {"ablation": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    result = audit["experiments"]["ablation"]
    assert audit["valid"] is True
    assert result["status"] == "not_estimable"
    assert any("state" in reason or "available" in reason for reason in result["reasons"])
    row = next(item for item in audit["tables"]["tables"]["T7"]
               if item["part"] == "Ablation" and item["row"] == "L1")
    assert row["status"] == "not_estimable"
    assert row["value"] is None

    data = full_metrics()["ablation"]
    data["layers"]["L0"] = {"state": "available", "counts": {"n": 93},
                            "score_kind": "continuous"}
    path = make_manifest(tmp_path / "counts", {"ablation": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    result = audit["experiments"]["ablation"]
    assert audit["valid"] is True
    assert result["status"] == "not_estimable"
    assert any("AUROC" in reason or "score" in reason for reason in result["reasons"])
    row = next(item for item in audit["tables"]["tables"]["T7"]
               if item["part"] == "Ablation" and item["row"] == "L0")
    assert row["status"] == "not_estimable"
    assert row["value"] is None

    data = full_metrics()["ablation"]
    data["state"] = "unavailable"
    path = make_manifest(tmp_path / "unavailable", {"ablation": data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit["experiments"]["ablation"]["status"] == "not_estimable"



def test_single_frozen_l0_score_is_not_an_auroc(tmp_path):
    data = full_metrics()['ablation']
    data['layers']['L0'].pop('auc', None)
    data['layers']['L0']['frozen_l0_score'] = 0.7
    path = make_manifest(tmp_path, {'ablation': data})
    audit = suite.audit_manifest(suite.load_manifest(path), base_dir=tmp_path)
    assert audit['experiments']['ablation']['status'] == 'not_estimable'
