# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Overhead T8 aggregation: null is not zero, mock C3 is not a real condition."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.evaluation import controlled_capture as cc
from scripts.evaluation import experiment_suite_audit as suite
from scripts.evaluation import overhead_metrics as om

RELEASE = "9c7099c1d36d21af025f5050d462b273cc04aef0c69d3ff72e902779372c6ea3"
CONCURRENCIES = (1, 4, 8, 16, 32)
REPEATS = 3
PROFILES = ("C0", "C1", "C2", "C3")
MATRIX = {1: PROFILES, 4: ("C0", "C3"), 8: ("C0", "C3"),
          16: ("C0", "C3"), 32: ("C0", "C3")}
OPERATIONS = 2


def bind_plan(plan: dict) -> dict:
    """Hash a plan the same way the driver builder does before signing it."""
    plan = dict(plan)
    plan.pop("plan_sha256", None)
    plan["plan_sha256"] = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return plan


def synthetic_plan(root: Path) -> dict:
    """The frozen 36-run matrix: four arms at concurrency 1, C0/C3 elsewhere."""
    conditions = []
    blocks = []
    for concurrency in CONCURRENCIES:
        profiles = MATRIX[concurrency]
        for repeat in range(1, REPEATS + 1):
            block_id = f"c{concurrency}-r{repeat}"
            block = {"block_id": block_id, "concurrency": concurrency,
                     "repeat": repeat, "profiles": list(profiles),
                     "condition_order": list(profiles), "condition_ids": []}
            for profile in profiles:
                condition_id = f"{block_id}-{profile}"
                block["condition_ids"].append(condition_id)
                conditions.append({
                    "condition_id": condition_id,
                    "block_id": block_id,
                    "concurrency": concurrency,
                    "repeat": repeat,
                    "profile": profile,
                    "workload_instances": concurrency,
                    "expected_receipts": concurrency * OPERATIONS,
                })
            blocks.append(block)
    return bind_plan({
        "schema": om.PLAN_SCHEMA,
        "protocol": "EXPERIMENT-SUITE-7-REVISED-2026-10-26 §6",
        "profiles": list(PROFILES),
        "concurrency": list(CONCURRENCIES),
        "repeats": REPEATS,
        "matrix": {
            "concurrency_profiles": {str(level): list(names)
                                     for level, names in MATRIX.items()},
            "four_profile_blocks": 3,
            "two_profile_blocks": 12,
            "order": {"seed": om.ORDER_SEED,
                      "method": "seeded_shuffle_within_block",
                      "archived_in": "blocks[].condition_order"},
        },
        "blocks": blocks,
        "condition_runs": conditions,
        "operations_per_instance": OPERATIONS,
        "block_count": 15,
        "condition_run_count": 36,
        "workload_instances": 372,
        "workload_instance_count": 372,
        "out": str(root),
    })


def capture_record(root: Path, condition: dict) -> dict:
    profile = condition["profile"]
    if profile == "C0":
        return cc.build_controlled_capture(
            profile=profile, condition_id=condition["condition_id"],
            receipts=[], receipt_paths=[],
            expected_receipts=condition["expected_receipts"],
            trace_path=None)
    directory = root / condition["condition_id"]
    directory.mkdir(parents=True, exist_ok=True)
    receipts = []
    events = []
    actions = []
    for index in range(condition["expected_receipts"]):
        path = directory / f"op-{index}"
        path.write_text("controlled\n")
        monotonic = 1_000_000 + index * 100
        receipts.append({
            "receipt_id": f"r{index}", "path": str(path.resolve()),
            "pid": 4242, "process_start_time_ns": 1_000,
            "process_start_resolution_ns": 1,
            "started_ns": 1_000_000 + index * 100,
            "completed_ns": 1_000_050 + index * 100,
            "monotonic_started_ns": monotonic,
            "monotonic_completed_ns": monotonic + 50,
            "pid_namespace_assumption": "same_host_single_pid_namespace",
        })
        events.append({
            "timestamp": 1, "timestamp_ns": 1,
            "source": "process", "pid": 4242, "comm": "agent",
            "data": {"event": "FILE_OPEN", "filepath": str(path.resolve()),
                     "pid": 4242, "start_time_ns": 1_000,
                     "outcome": "succeeded", "count": 1,
                     "timestamp": monotonic + 25},
        })
        actions.append((f"a{index}", 4242, 1_000, "FileOpen",
                        str(path.resolve()), "succeeded"))
    receipt = directory / "workload-0.jsonl"
    receipt.write_text("".join(json.dumps(row) + "\n" for row in receipts))
    trace = directory / "trace.log"
    # C1 capture-only proves attachment from this terminal health event; the
    # metrics adapter re-derives the proof instead of trusting the record.
    trace.write_text(json.dumps(_capture_health_event(2)) + "\n"
                     + "".join(json.dumps(row) + "\n" for row in events))
    database = None
    if profile in ("C2", "C3"):
        database = directory / "trace.log.responsibility.db"
        with sqlite3.connect(database) as conn:
            conn.execute(
                "CREATE TABLE actions (action_id TEXT, pid INTEGER, "
                "process_start_time_ns INTEGER, action_type TEXT, "
                "normalized_args_json TEXT, outcome TEXT)")
            conn.execute("PRAGMA user_version=5")
            # C2/C3 prove attachment and final flush from this checkpoint.
            conn.execute("CREATE TABLE analysis_checkpoints "
                         "(checkpoint_kind TEXT, payload_json TEXT)")
            conn.execute("INSERT INTO analysis_checkpoints VALUES (?, ?)",
                         ("frozen-analysis", json.dumps({"capture_quality": {
                             "sources": {"SSL:1": _health_row(),
                                         "Process:1": _health_row()},
                             "analysis": {"persist_flush_failed": False}}})))
            for action_id, pid, start, action_type, path, outcome in actions:
                conn.execute(
                    "INSERT INTO actions VALUES (?, ?, ?, ?, ?, ?)",
                    (action_id, pid, start, action_type,
                     json.dumps([{"Path": path}, {"OpenFlags": 0}]), outcome))
    return cc.build_controlled_capture(
        profile=profile, condition_id=condition["condition_id"],
        receipts=receipts, receipt_paths=[str(receipt)],
        expected_receipts=condition["expected_receipts"],
        trace_path=trace, database_path=database,
        health={"attachment_proven": True,
                "final_flush_observed": None if profile == "C1" else True})


def write_results(root: Path, plan: dict, *, mutate=None) -> None:
    for condition in plan["condition_runs"]:
        profile = condition["profile"]
        collected = profile != "C0"
        result = {
            "condition_id": condition["condition_id"],
            "block_id": condition["block_id"],
            "profile": profile,
            "concurrency": condition["concurrency"],
            "repeat": condition["repeat"],
            "status": "completed",
            "duration_s": 2.0 + condition["concurrency"] / 100,
            "collector": {
                "started": collected,
                "attachment_proven": collected,
                "observer_provider": ("llm_env" if profile == "C3"
                                      else "disabled" if collected else "unknown"),
            },
            "resources": {
                "available": True,
                "collector": {
                    "cpu_percent": {"p95": 10 + condition["concurrency"]},
                    "rss_kib": {"max": 1000 + condition["concurrency"]},
                    "cpu_seconds": {"max": 1.5 if collected else 0.0},
                },
                "workloads": {
                    "cpu_percent": {"p95": 5},
                    "rss_kib": {"max": 500 + condition["concurrency"]},
                    "cpu_seconds": {"max": 0.25 + condition["concurrency"] / 100},
                },
                "database_bytes": ({
                    "peak": 4096, "final": {
                        "main": 3072, "wal": 1024, "total": 4096},
                } if collected else {
                    "peak": None, "final": {"main": None, "wal": None,
                                            "total": None}}),
                "trace_bytes": ({"final": 2048, "peak": 2048}
                                if collected else {"final": None, "peak": None}),
            },
            "receipts": {
                "workload_expected_receipts": condition["expected_receipts"],
                "workload_observed_receipts": condition["expected_receipts"],
                "workload_missing_receipts": 0,
                "latency_ms": {
                    "event_to_claim": {"p95": 1.5},
                    "claim_to_alert": {"p95": 2.5},
                },
                "token_usage": {
                    "known": True,
                    "total_tokens": 100 if profile == "C3" else 0,
                },
                "cost_usd": 0.01 if profile == "C3" else 0.0,
            },
            "controlled_capture": capture_record(root, condition),
        }
        if mutate is not None:
            mutate(condition, result)
        path = root / condition["condition_id"] / "result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result))


def test_complete_cohort_aggregates_profile_by_concurrency_and_bootstraps(tmp_path):
    plan = synthetic_plan(tmp_path)
    write_results(tmp_path, plan)
    payload = om.build_metrics(plan, tmp_path, release_id=RELEASE)
    assert payload["schema"] == om.METRICS_SCHEMA
    assert payload["experiment"] == "overhead"
    assert payload["estimable"] is True
    data = payload["data"]
    assert set(data["conditions"]) == set(PROFILES)
    assert set(data["conditions"]["C3"]["concurrency"]) == {
        "1", "4", "8", "16", "32"}
    assert set(data["conditions"]["C1"]["concurrency"]) == {"1"}
    assert set(data["conditions"]["C2"]["concurrency"]) == {"1"}
    assert data["conditions"]["C1"]["not_planned_concurrency"] == [4, 8, 16, 32]
    assert data["conditions"]["C2"]["not_planned_concurrency"] == [4, 8, 16, 32]
    assert data["conditions"]["C1"]["runs_expected"] == 3
    assert data["conditions"]["C2"]["runs_expected"] == 3
    assert data["conditions"]["C3"]["runs_expected"] == 15
    cell = data["conditions"]["C3"]["concurrency"]["4"]
    assert cell["cpu_percent_p95"] == 14
    assert cell["cpu_percent_p95_values"] == [14, 14, 14]
    assert cell["cpu_percent_p95_mean"] == 14
    assert cell["cpu_percent_p95_min"] == 14
    assert cell["cpu_percent_p95_max"] == 14
    assert cell["cpu_percent_p95_range"] == 0
    assert cell["rss_kib_max"] == 1004
    assert cell["db_bytes_peak"] == 4096
    # Workload receipts are not real runtime usage or alert-latency evidence.
    assert cell["event_to_claim_p95"] is None
    assert cell["claim_to_alert_p95"] is None
    assert cell["observer_tokens"] is None
    assert cell["cost_usd"] is None
    assert cell["receipt_claims"]["source"] == "workload_stdout_receipts"
    assert cell["receipt_claims"]["event_to_claim_p95"] == 1.5
    assert cell["receipt_claims"]["observer_tokens"] == 100
    assert cell["receipt_claims"]["trust"] == "not_a_runtime_measurement"
    assert cell["duration_s"] == pytest.approx(2.04)
    assert cell["duration_s_values"] == pytest.approx([2.04, 2.04, 2.04])
    assert cell["duration_s_range"] == 0
    assert cell["work_completion_rate"] == 1.0
    assert cell["work_completion_rate_values"] == [1.0, 1.0, 1.0]
    assert cell["collector_cpu_seconds"] == pytest.approx(1.5)
    assert cell["workload_cpu_seconds"] == pytest.approx(0.29)
    assert cell["total_cpu_seconds"] == pytest.approx(1.79)
    assert cell["trace_bytes"] == 2048
    assert cell["db_main_bytes"] == 3072
    assert cell["db_wal_bytes"] == 1024
    assert cell["db_total_bytes"] == 4096
    assert cell["cpu_basis"] == "collector_tree"
    c0 = data["conditions"]["C0"]["concurrency"]["4"]
    assert c0["cpu_percent_p95"] == 5
    assert c0["rss_kib_max"] == 504
    assert c0["cpu_basis"] == "workload_tree"
    assert c0["db_main_bytes"] is None and c0["db_wal_bytes"] is None
    assert list(cell)[0] == "concurrency"
    assert "capture_loss" not in data
    capture = data["controlled_capture"]
    assert capture["C0"]["status"] == "not_applicable"
    assert capture["C1"]["raw_transport_missing_fraction"] == 0.0
    assert capture["C1"]["persisted_operation_missing_fraction"] is None
    assert capture["C2"]["raw_transport_missing_fraction"] == 0.0
    assert capture["C2"]["persisted_operation_missing_fraction"] == 0.0
    assert capture["C3"]["operation_denominator"] == 366
    assert capture["C1"]["operation_denominator"] == 6
    assert capture["C2"]["operation_denominator"] == 6
    assert capture["C2"]["evidence"][0]["adapter_version"] == "controlled_file_open/v1"
    assert capture["C2"]["empirical_validation"] == "pending_real_recording"
    stats = data["statistics"]
    assert stats["aggregation_unit"] == "block"
    assert stats["n_blocks"] == 15
    assert stats["bootstrap_resamples"] >= 1000
    assert "C3_minus_C0_total_cpu_percent" in stats["confidence_intervals"]
    assert stats["contrast_blocks"]["C3_minus_C0_total_cpu_percent"] == 15
    assert stats["contrast_blocks"]["C2_minus_C1_total_cpu_percent"] == 3
    assert stats["contrast_blocks"]["C3_minus_C2_total_cpu_percent"] == 3
    assert stats["coverage"] == 1.0
    assert stats["abstention"] == 0.0


def test_missing_condition_run_is_not_estimable_and_has_no_statistics(tmp_path):
    plan = synthetic_plan(tmp_path)
    write_results(tmp_path, plan)
    (tmp_path / "c4-r1-C3" / "result.json").unlink()
    payload = om.build_metrics(plan, tmp_path, release_id=RELEASE)
    assert payload["estimable"] is False
    assert "missing" in payload["not_estimable_reason"]
    assert payload["data"]["missing_condition_runs"] == ["c4-r1-C3"]
    assert "statistics" not in payload["data"]
    assert payload["data"]["coverage"] < 1.0


def test_high_concurrency_c1_c2_are_not_planned_and_never_missing(tmp_path):
    plan = synthetic_plan(tmp_path)
    write_results(tmp_path, plan)
    payload = om.build_metrics(plan, tmp_path, release_id=RELEASE)
    assert payload["estimable"] is True
    data = payload["data"]
    assert data["condition_runs_expected"] == 36
    assert data["condition_runs_observed"] == 36
    assert data["missing_condition_runs"] == []
    assert data["not_planned_condition_runs"] == 24
    assert data["not_planned"] == {
        "C1": {"4": "not_planned_by_protocol", "8": "not_planned_by_protocol",
               "16": "not_planned_by_protocol", "32": "not_planned_by_protocol"},
        "C2": {"4": "not_planned_by_protocol", "8": "not_planned_by_protocol",
               "16": "not_planned_by_protocol", "32": "not_planned_by_protocol"},
    }
    assert data["blocks_expected"] == 15
    assert data["blocks_complete"] == 15
    assert data["coverage"] == 1.0
    assert "statistics" in data


def test_missing_planned_high_level_c3_run_is_missing_not_not_planned(tmp_path):
    plan = synthetic_plan(tmp_path)
    write_results(tmp_path, plan)
    (tmp_path / "c4-r1-C3" / "result.json").unlink()
    payload = om.build_metrics(plan, tmp_path, release_id=RELEASE)
    data = payload["data"]
    assert payload["estimable"] is False
    assert data["missing_condition_runs"] == ["c4-r1-C3"]
    assert data["not_planned_condition_runs"] == 24
    assert data["conditions"]["C3"]["concurrency"]["4"]["runs"] == 2
    assert data["conditions"]["C3"]["concurrency"]["4"]["status"] == "incomplete"
    assert data["blocks_complete"] == 14


def test_old_60_run_plan_schema_is_rejected(tmp_path):
    plan = synthetic_plan(tmp_path)
    old = json.loads(json.dumps(plan))
    old["schema"] = "mtagentlens.overhead-plan/1"
    with pytest.raises(ValueError, match="overhead-plan/2"):
        om.validate_plan(bind_plan(old))


@pytest.mark.parametrize("provider", ["mock", "unknown", "real-local", "disabled", None])
def test_c3_provider_must_be_a_known_real_configuration_marker(tmp_path, provider):
    plan = synthetic_plan(tmp_path)

    def mutate(condition, result):
        if condition["profile"] == "C3":
            result["collector"]["observer_provider"] = provider

    write_results(tmp_path, plan, mutate=mutate)
    payload = om.build_metrics(plan, tmp_path, release_id=RELEASE)
    assert payload["estimable"] is False
    invalid = payload["data"]["invalid_condition_runs"]
    assert [row["condition_id"] for row in invalid] == [
        condition["condition_id"] for condition in plan["condition_runs"]
        if condition["profile"] == "C3"]
    assert "known real" in invalid[0]["reason"]
    assert payload["data"]["conditions"]["C3"]["observer_provider"] == "unknown"
    assert "statistics" not in payload["data"]


def test_c3_llm_config_marker_is_accepted(tmp_path):
    plan = synthetic_plan(tmp_path)

    def mutate(condition, result):
        if condition["profile"] == "C3":
            result["collector"]["observer_provider"] = "llm_config"

    write_results(tmp_path, plan, mutate=mutate)
    payload = om.build_metrics(plan, tmp_path, release_id=RELEASE)
    assert payload["estimable"] is True
    assert payload["data"]["conditions"]["C3"]["observer_provider"] == "llm_config"


def test_plan_sha256_binds_the_supplied_plan_content(tmp_path):
    plan = synthetic_plan(tmp_path)
    tampered = dict(plan)
    tampered["repeats"] = 2
    with pytest.raises(ValueError, match="plan_sha256"):
        om.validate_plan(tampered)

    short = dict(plan)
    short["condition_runs"] = short["condition_runs"][:4]
    short["blocks"] = short["blocks"][:1]
    short["condition_run_count"] = 4
    short["block_count"] = 1
    short["workload_instances"] = 4
    short["workload_instance_count"] = 4
    with pytest.raises(ValueError, match="36 condition"):
        om.validate_plan(bind_plan(short))


def test_plan_rejects_duplicate_profile_repeat_and_count_mismatch(tmp_path):
    plan = synthetic_plan(tmp_path)

    duplicate = json.loads(json.dumps(plan))
    duplicate["condition_runs"][0]["profile"] = "C1"
    with pytest.raises(ValueError, match="duplicate"):
        om.validate_plan(bind_plan(duplicate))

    repeat_mismatch = json.loads(json.dumps(plan))
    repeat_mismatch["condition_runs"][0]["repeat"] = 2
    with pytest.raises(ValueError, match="repeat"):
        om.validate_plan(bind_plan(repeat_mismatch))

    wrong_repeats = json.loads(json.dumps(plan))
    wrong_repeats["repeats"] = 2
    with pytest.raises(ValueError, match="repeats"):
        om.validate_plan(bind_plan(wrong_repeats))

    wrong_counts = json.loads(json.dumps(plan))
    wrong_counts["workload_instance_count"] = 731
    with pytest.raises(ValueError, match="workload_instance"):
        om.validate_plan(bind_plan(wrong_counts))

    wrong_receipts = json.loads(json.dumps(plan))
    wrong_receipts["condition_runs"][0]["expected_receipts"] = 1
    with pytest.raises(ValueError, match="expected_receipts"):
        om.validate_plan(bind_plan(wrong_receipts))


@pytest.mark.parametrize("mutation", [
    lambda result: result.update({"repeat": 2}),
    lambda result: result.update({"concurrency": 4}),
    lambda result: result.update({"condition_id": "forged"}),
    lambda result: result["receipts"].update({"workload_expected_receipts": 1}),
    lambda result: result["receipts"].update({"workload_observed_receipts": 1}),
])
def test_result_must_match_planned_identity_and_workload(tmp_path, mutation):
    plan = synthetic_plan(tmp_path)

    def mutate(condition, result):
        if condition["condition_id"] == "c1-r1-C1":
            mutation(result)

    write_results(tmp_path, plan, mutate=mutate)
    payload = om.build_metrics(plan, tmp_path, release_id=RELEASE)
    assert payload["estimable"] is False
    invalid = payload["data"]["invalid_condition_runs"]
    assert [row["condition_id"] for row in invalid] == ["c1-r1-C1"]
    assert "statistics" not in payload["data"]


def test_short_workload_receipts_report_completion_rate_not_zero(tmp_path):
    plan = synthetic_plan(tmp_path)
    write_results(tmp_path, plan)
    condition = next(row for row in plan["condition_runs"]
                     if row["condition_id"] == "c1-r1-C1")
    result = json.loads((tmp_path / "c1-r1-C1" / "result.json").read_text())
    result["status"] = "degraded"
    result["receipts"]["workload_observed_receipts"] = 1
    result["receipts"]["workload_missing_receipts"] = 1
    run = om._run_values(result, condition)
    assert run["work_completion_rate"] == 0.5
    assert run["work_completion_observed"] == 1
    assert run["work_completion_expected"] == 2
    assert run["run_status"] == "degraded"


def test_missing_run_metrics_stay_null_not_zero(tmp_path):
    plan = synthetic_plan(tmp_path)
    write_results(tmp_path, plan)
    condition = next(row for row in plan["condition_runs"]
                     if row["condition_id"] == "c1-r1-C1")
    result = json.loads((tmp_path / "c1-r1-C1" / "result.json").read_text())
    result["resources"]["trace_bytes"] = {"final": None, "peak": None}
    result["resources"]["collector"]["cpu_seconds"] = {"max": None}
    run = om._run_values(result, condition)
    assert run["trace_bytes"] is None
    assert run["collector_cpu_seconds"] is None
    assert run["total_cpu_seconds"] is None


def test_unproven_attachment_invalidates_the_condition_run(tmp_path):
    plan = synthetic_plan(tmp_path)

    def mutate(condition, result):
        if condition["condition_id"] == "c1-r1-C1":
            result["collector"]["attachment_proven"] = False

    write_results(tmp_path, plan, mutate=mutate)
    payload = om.build_metrics(plan, tmp_path, release_id=RELEASE)
    assert payload["estimable"] is False
    assert [item["condition_id"] for item in payload["data"]["invalid_condition_runs"]] == [
        "c1-r1-C1"]
    assert payload["data"]["coverage"] < 1.0


def test_capture_scalar_and_boolean_api_is_rejected(tmp_path):
    plan = synthetic_plan(tmp_path)
    write_results(tmp_path, plan)
    with pytest.raises(TypeError):
        om.build_metrics(plan, tmp_path, release_id=RELEASE, capture_loss=0.1)
    with pytest.raises(TypeError):
        om.build_metrics(plan, tmp_path, release_id=RELEASE,
                         adapter_validated=True)


def test_capture_fraction_without_denominator_or_evidence_is_invalid(tmp_path):
    plan = synthetic_plan(tmp_path)
    def no_denominator(condition, result):
        if condition["profile"] == "C2":
            result["controlled_capture"]["denominator"] = None
    write_results(tmp_path, plan, mutate=no_denominator)
    payload = om.build_metrics(plan, tmp_path, release_id=RELEASE)
    assert payload["estimable"] is False
    assert any("recomput" in row["reason"] or "denominator" in row["reason"]
               for row in payload["data"]["invalid_condition_runs"])


def test_consistent_count_and_fraction_tamper_is_rejected(tmp_path):
    plan = synthetic_plan(tmp_path)
    def tamper(condition, result):
        if condition["profile"] == "C2" and condition["concurrency"] == 1:
            capture = result["controlled_capture"]
            capture["raw_transport_missing_count"] = 1
            capture["raw_transport_missing_fraction"] = 0.5
            capture["persisted_operation_missing_count"] = 1
            capture["persisted_operation_missing_fraction"] = 0.5
    write_results(tmp_path, plan, mutate=tamper)
    payload = om.build_metrics(plan, tmp_path, release_id=RELEASE)
    assert payload["estimable"] is False
    assert any("recomput" in row["reason"] or "differs" in row["reason"]
               for row in payload["data"]["invalid_condition_runs"])


def test_capture_source_tamper_invalidates_the_run(tmp_path):
    plan = synthetic_plan(tmp_path)
    def tamper(condition, result):
        if condition["profile"] == "C1":
            path = Path(result["controlled_capture"]["artifact_binding"]
                        ["receipt_files"][0]["path"])
            path.write_text("tampered\n")
    write_results(tmp_path, plan, mutate=tamper)
    payload = om.build_metrics(plan, tmp_path, release_id=RELEASE)
    assert payload["estimable"] is False
    assert any("sha256 changed" in row["reason"]
               or "receipt sha256" in row["reason"]
               or "recomput" in row["reason"]
               or "cannot be read" in row["reason"]
               for row in payload["data"]["invalid_condition_runs"])


def test_t8_audit_requires_measured_scoped_capture_with_evidence(tmp_path):
    plan = synthetic_plan(tmp_path)
    write_results(tmp_path, plan)
    payload = om.build_metrics(plan, tmp_path, release_id=RELEASE)
    status, reasons, _ = suite._audit_overhead(payload["data"], {})
    assert status == "estimable", reasons


def test_capture_not_estimable_makes_t8_partial_not_zero(tmp_path):
    plan = synthetic_plan(tmp_path)
    def unavailable(condition, result):
        if condition["profile"] == "C2" and condition["concurrency"] == 1:
            capture = result["controlled_capture"]
            binding = capture["artifact_binding"]
            trace = Path(binding["trace_file"]["path"])
            trace.unlink()
            receipts, _ = cc.load_receipts(
                [entry["path"] for entry in binding["receipt_files"]])
            result["controlled_capture"] = cc.build_controlled_capture(
                profile="C2", condition_id=condition["condition_id"],
                receipts=receipts,
                receipt_paths=[entry["path"] for entry in binding["receipt_files"]],
                expected_receipts=condition["expected_receipts"],
                trace_path=trace, database_path=binding["database"]["path"],
                health=capture["health"],
                ringbuf_diagnostics=capture.get("ringbuf_diagnostics"))
    write_results(tmp_path, plan, mutate=unavailable)
    payload = om.build_metrics(plan, tmp_path, release_id=RELEASE)
    status, reasons, _ = suite._audit_overhead(payload["data"], {})
    assert status == "partial"
    assert any("C2" in reason and "not estimable" in reason for reason in reasons)
    assert payload["data"]["controlled_capture"]["C2"][
        "raw_transport_missing_fraction"] is None


def test_blocks_must_pair_the_same_concurrency_and_repeat(tmp_path):
    plan = synthetic_plan(tmp_path)
    for block in plan['blocks']:
        block.pop('concurrency'); block.pop('repeat')
    first, other = plan['condition_runs'][0], plan['condition_runs'][12]
    first['block_id'], other['block_id'] = other['block_id'], first['block_id']
    plan = bind_plan(plan)
    with pytest.raises(ValueError, match='block.*concurrency|paired'):
        om.validate_plan(plan)


def test_bootstrap_keeps_c3_c0_when_c1_cpu_is_unknown(tmp_path):
    plan = synthetic_plan(tmp_path)

    def drop_c1_cpu(condition, result):
        if condition["profile"] == "C1":
            result["resources"]["collector"]["cpu_percent"]["p95"] = None

    write_results(tmp_path, plan, mutate=drop_c1_cpu)
    payload = om.build_metrics(plan, tmp_path, release_id=RELEASE)
    assert payload["estimable"] is True
    stats = payload["data"]["statistics"]
    assert "C3_minus_C0_total_cpu_percent" in stats["confidence_intervals"]
    assert stats["contrast_blocks"]["C3_minus_C0_total_cpu_percent"] == 15
    assert "C1_total_cpu_percent" not in stats["confidence_intervals"]
    assert "C1_minus_C0_total_cpu_percent" in stats["unavailable_contrasts"]
    assert payload["data"]["conditions"]["C1"]["concurrency"]["1"][
        "cpu_percent_p95"] is None


def test_bootstrap_conditions_on_fixed_concurrency_strata(tmp_path):
    plan = synthetic_plan(tmp_path)
    def fixed_concurrency(condition, result):
        c = condition['concurrency']
        result['resources']['workloads']['cpu_percent']['p95'] = c
        result['resources']['collector']['cpu_percent']['p95'] = c * 10
    write_results(tmp_path, plan, mutate=fixed_concurrency)
    result = om.build_metrics(plan, tmp_path, release_id=RELEASE)
    ci = result['data']['statistics']['confidence_intervals']['C3_minus_C0_total_cpu_percent']
    # With no within-stratum repeat noise the fixed-design contrast is constant.
    expected = sum(CONCURRENCIES) * 10 / len(CONCURRENCIES)
    assert ci == [expected, expected]


def _health_row(**overrides):
    row = {"started": True, "attached": 1, "attach_error": None,
           "initial_snapshot_complete": True, "final_stats_seen": True,
           "drain_complete": True, "exit_status": 0, "lost": 0,
           "rate_limited": 0, "stdout_skipped": 0, "stdout_error": False,
           "stderr_error": False}
    row.update(overrides)
    return row


def _capture_health_event(ssl_attached, status="healthy"):
    return {"timestamp": 1, "timestamp_ns": 1, "source": "collector",
            "pid": 1, "comm": "mtagentlens",
            "data": {"event": "CAPTURE_HEALTH",
                     "health": {"status": status, "sources": {
                         "SSL:1": _health_row(attached=ssl_attached),
                         "Process:1": _health_row()}}}}


def _file_open_event(path, pid=101, start=500):
    return {"timestamp": 1, "timestamp_ns": 1, "source": "process",
            "pid": pid, "comm": "w",
            "data": {"event": "FILE_OPEN", "filepath": path, "pid": pid,
                     "start_time_ns": start, "outcome": "succeeded", "count": 1}}


def _regenerated_record(tmp_path, profile, *, trace_attached=None,
                        checkpoint_attached=None):
    """Build a measured record the way anyone writing result.json could: the
    health booleans are supplied by the writer, not derived by the artifacts."""
    directory = tmp_path / profile
    directory.mkdir()
    op = directory / "op"
    op.write_text("x")
    row = {"receipt_id": "r0", "path": str(op.resolve()), "pid": 101,
           "process_start_time_ns": 500, "started_ns": 1_000,
           "completed_ns": 2_000}
    receipt_path = directory / "workload-0.jsonl"
    receipt_path.write_text(json.dumps(row) + "\n")
    trace_path = directory / "trace.log"
    events = []
    if trace_attached is not None:
        events.append(_capture_health_event(
            trace_attached,
            status="healthy" if trace_attached > 0 else "unknown"))
    events.append(_file_open_event(str(op.resolve())))
    trace_path.write_text("".join(json.dumps(event) + "\n" for event in events))
    database_path = None
    if profile in ("C2", "C3"):
        database_path = directory / "trace.log.responsibility.db"
        with sqlite3.connect(database_path) as conn:
            conn.execute(
                "CREATE TABLE actions (action_id TEXT, pid INTEGER, "
                "process_start_time_ns INTEGER, action_type TEXT, "
                "normalized_args_json TEXT, outcome TEXT)")
            conn.execute(
                "CREATE TABLE analysis_checkpoints "
                "(checkpoint_kind TEXT, payload_json TEXT)")
            conn.execute("PRAGMA user_version=5")
            conn.execute("INSERT INTO actions VALUES (?, ?, ?, ?, ?, ?)",
                         ("a1", 101, 500, "FileOpen",
                          json.dumps([{"Path": str(op.resolve())}]), "succeeded"))
            quality = {"sources": {"SSL:1": _health_row(attached=checkpoint_attached),
                                   "Process:1": _health_row()},
                       "analysis": {"persist_flush_failed": False}}
            conn.execute("INSERT INTO analysis_checkpoints VALUES (?, ?)",
                         ("frozen-analysis",
                          json.dumps({"capture_quality": quality})))
    record = cc.build_controlled_capture(
        profile=profile, condition_id="c-" + profile, receipts=[row],
        receipt_paths=[str(receipt_path)], expected_receipts=1,
        trace_path=trace_path, database_path=database_path,
        health={"attachment_proven": True,
                "final_flush_observed": None if profile == "C1" else True})
    assert record["status"] == "measured", record["reasons"]
    return record, {"condition_id": "c-" + profile, "profile": profile,
                    "expected_receipts": 1}


def test_measured_c1_capture_requires_artifact_proven_attachment(tmp_path):
    """The trace's own CAPTURE_HEALTH (TLS attached=0) disproves the writer's
    health bool; a 0.0 capture fraction over it must not be accepted."""
    record, condition = _regenerated_record(tmp_path, "C1", trace_attached=0)
    with pytest.raises(ValueError, match="attachment"):
        om._verify_controlled_capture({"controlled_capture": record}, condition)


def test_measured_c1_capture_with_healthy_trace_health_is_accepted(tmp_path):
    record, condition = _regenerated_record(tmp_path, "C1", trace_attached=2)
    out = om._verify_controlled_capture({"controlled_capture": record}, condition)
    assert out["status"] == "measured"
    assert out["raw_transport_missing_fraction"] == 0.0


def test_measured_persisted_capture_requires_checkpoint_proven_attachment(tmp_path):
    """Same rule for C2/C3: the persisted checkpoint is the artifact that
    proves attachment and final flush, not a self-declared bool."""
    record, condition = _regenerated_record(tmp_path, "C2", checkpoint_attached=0)
    with pytest.raises(ValueError, match="attachment"):
        om._verify_controlled_capture({"controlled_capture": record}, condition)
