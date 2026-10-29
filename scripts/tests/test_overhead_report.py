# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Focused tests for the frozen OverheadBench T8/F2 report."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sqlite3

import pytest

from scripts.evaluation import overhead_report as report

LEVELS = (1, 4, 8, 16, 32)
MATRIX = {1: ("C0", "C1", "C2", "C3"), 4: ("C0", "C3"), 8: ("C0", "C3"),
          16: ("C0", "C3"), 32: ("C0", "C3")}
LABELS = {"C0": "agent_alone", "C1": "collector_trace_capture_only",
          "C2": "collector_trace_disable_responsibility_observer",
          "C3": "collector_trace_default"}
PRICES = {"input": .30, "cached_input": .006, "output": 1.20}


def test_layer_counters_use_final_snapshot_and_preserve_unknown(tmp_path):
    source = tmp_path / "collector.stderr"
    source.write_text(
        'prefix {"event":"CAPTURE_HEALTH","health":{"status":"unknown","sources":{}}}\n'
        'prefix {"event":"CAPTURE_HEALTH","health":{"status":"healthy","sources":'
        '{"Process:1":{"lost":2,"drain_complete":true,"loss_reasons":{"ringbuf_full":2}},'
        '"SSL:2":{"lost":0,"drain_complete":true}}}}\n'
        '[diagnostics:json] {"actions_dropped_no_agent":3,"partial_tls_events":7}\n'
        'persistence: flushed cleanly (1 dropped, 0 write failures, 0 closed-channel)\n'
    )
    counters = report.read_layer_counters(source)
    assert counters["capture_status"] == "healthy"
    assert counters["sources"]["Process:1"]["lost"] == 2
    assert counters["persistence"] == {"dropped": 1, "write_failures": 0,
                                       "closed_channel": 0}
    assert counters["runtime_semantic_counters"]["partial_tls_events"] == 7
    assert report.read_layer_counters(tmp_path / "absent")["persistence"] is None


def test_cost_requires_explicit_cache_usage():
    assert report._usage_cost({"input_tokens": 100, "output_tokens": 5},
                              "deepseek-flash") is None


def test_graph_size_reads_final_persisted_rows_only(tmp_path):
    database=tmp_path/'graph.db'
    connection=sqlite3.connect(database)
    connection.executescript('CREATE TABLE graph_nodes(id); CREATE TABLE graph_edges(id); '
                             'INSERT INTO graph_nodes VALUES(1),(2); INSERT INTO graph_edges VALUES(1);')
    connection.close()
    assert report.read_graph_size(database)==(2,1)
    assert report.read_graph_size(None)==(None,None)


def test_graph_size_reads_final_snapshot_when_node_tables_are_empty(tmp_path):
    database=tmp_path/'snapshot.db'
    connection=sqlite3.connect(database)
    connection.executescript(
        'CREATE TABLE graph_nodes(id); CREATE TABLE graph_edges(id); '
        'CREATE TABLE graph_snapshots(snapshot_id,timestamp_ns,nodes_json,edges_json,metrics_json,run_id);')
    connection.execute('INSERT INTO graph_snapshots VALUES(?,?,?,?,?,?)',
        ('old', 10, '[{"id":"a"}]', '[]', '{}', 'run'))
    connection.execute('INSERT INTO graph_snapshots VALUES(?,?,?,?,?,?)',
        ('new', 20, '[{"id":"a"},{"id":"b"}]', '[{"id":"e"}]', '{}', 'run'))
    connection.commit()
    connection.close()
    assert report.read_graph_size(database)==(2,1)


def test_paired_overhead_uses_matching_block_and_successful_work():
    runs = [
        {"profile": p, "concurrency": 1, "block_id": b, "included": True,
         "work_completion_rate": 1.0, "agent_duration_s": duration,
         "agent_wall_span_s": duration}
        for b, p, duration in [("r1", "C0", 2), ("r1", "C3", 3),
                               ("r2", "C0", 4), ("r2", "C3", 4)]
    ]
    rows = report.paired_overheads(runs)
    assert [row["agent_duration_increase_percent"] for row in rows] == [50.0, 0.0]
    runs[-1]["work_completion_rate"] = .5
    assert report.paired_overheads(runs)[-1]["agent_duration_increase_percent"] is None


def _plan():
    rows, blocks = [], []
    for concurrency in LEVELS:
        for repeat in (1, 2, 3):
            block_id = f"c{concurrency}-r{repeat}"
            order = list(MATRIX[concurrency])
            blocks.append({"block_id": block_id, "concurrency": concurrency,
                           "repeat": repeat, "profiles": order,
                           "condition_order": order, "condition_ids": []})
            for profile in order:
                condition_id = f"{block_id}-{profile}"
                blocks[-1]["condition_ids"].append(condition_id)
                rows.append({
                    "condition_id": condition_id, "block_id": block_id,
                    "concurrency": concurrency, "repeat": repeat,
                    "profile": profile, "profile_label": LABELS[profile],
                    "collector": profile != "C0", "collector_flags": [],
                    "collector_command": None if profile == "C0" else ["collector"],
                    "collector_potentially_paid": profile in ("C2", "C3"),
                    "workload_instances": concurrency,
                    "expected_receipts": concurrency * 16,
                    "output": f"/synthetic/{condition_id}",
                })
    return {
        "schema": "mtagentlens.overhead-plan/2",
        "scope": "overhead36_condition_matrix_execution_plan",
        "protocol": "EXPERIMENT-SUITE-7-REVISED-2026-10-26 §6",
        "matrix": {"concurrency_profiles": {str(key): list(value)
                                            for key, value in MATRIX.items()}},
        "repeats": 3, "profiles": ["C0", "C1", "C2", "C3"],
        "blocks": blocks, "condition_runs": rows, "block_count": 15,
        "condition_run_count": 36, "plan_sha256": "a" * 64,
    }


def _execution(plan, *, complete=False, completed=0, phase="formal"):
    return {
        "schema": "mtagentlens.overhead-execution/v1", "phase": phase,
        "plan_sha256": plan["plan_sha256"], "conditions_expected": 36,
        "conditions_completed": completed, "complete": complete,
        "results": [],
    }


def _study(tmp_path, plan, *, phase="formal", complete=False, completed=0):
    study = tmp_path / "study"
    study.mkdir()
    name = "formal-plan.json" if phase == "formal" else "pilot-plan.json"
    (study / name).write_text(json.dumps(plan))
    run_dir = study / phase
    run_dir.mkdir(parents=True)
    (run_dir / "execution-report.json").write_text(
        json.dumps(_execution(plan, complete=complete, completed=completed, phase=phase)))
    return study, run_dir


def _row(plan, concurrency, repeat, profile):
    return next(row for row in plan["condition_runs"]
                if row["concurrency"] == concurrency and row["repeat"] == repeat
                and row["profile"] == profile)


def _usage(input_tokens=1000, output_tokens=100, cache_read=0, cache_creation=0,
           reported=999.0, bound=999.0):
    return {
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
        "cost_upper_bound_usd": bound,
        "reported_cost_usd": reported,
        "pricing_source": "https://api-docs.deepseek.com/quick_start/pricing/",
        "pricing_date": "2026-10-28",
        "pricing_basis": "peak_rate_upper_bound_not_provider_invoice",
    }


def _peak_cost(usage):
    return ((usage["input_tokens"] + usage["cache_creation_input_tokens"])
            * PRICES["input"] + usage["cache_read_input_tokens"]
            * PRICES["cached_input"] + usage["output_tokens"]
            * PRICES["output"]) / 1e6


def _observer(attempts=0, records=None, cost=999.0):
    records = [] if records is None else records
    return {
        "attempts": attempts, "requests": len({row.get("request_id")
                                               for row in records}),
        "failed_attempts": sum(row.get("outcome") != "success" for row in records),
        "usage_missing_attempts": sum(not isinstance(row.get("usage"), dict)
                                      for row in records),
        "known_cost_upper_bound_usd": cost,
        "cost_upper_bound_usd": cost, "attempt_records": records,
        "latency_ms": [row["elapsed_ms"] for row in records
                       if isinstance(row.get("elapsed_ms"), (int, float))],
        "pricing_basis": "DeepSeek peak-rate upper bound; missing usage stays unknown",
        "pricing_source": "https://api-docs.deepseek.com/quick_start/pricing/",
        "pricing_date": "2026-10-28",
    }


def _write_run(run_dir, plan_row, *, status="completed", cpu_mean=10.0,
               cpu_max=100.0, rss_kib=1024, agent_durations=(2.0,),
               agent_usage=None, observer=None, graph=None, raw=0.0,
               persisted=0.0, alert=None, db_main=2048, db_wal_peak=512,
               db_wal_final=None, trace_final=1024, agent_count=None):
    run_dir.mkdir(parents=True)
    concurrency = plan_row["concurrency"]
    profile = plan_row["profile"]
    if agent_usage is None:
        agent_usage = _usage()
    agents = []
    for index in range(concurrency if agent_count is None else agent_count):
        duration = agent_durations[index % len(agent_durations)]
        agents.append({
            "schema": "mtagentlens.overhead-agent/v1", "model": "deepseek-flash",
            "started_ns": 1000 + index, "completed_ns": 1000 + int(duration * 1e9),
            "duration_s": duration, "usage": agent_usage, "operations": 16,
        })
    if observer is None:
        observer = _observer()
    if alert is None:
        alert = {"available": profile in ("C2", "C3"), "linked_operations": 0,
                 "total_operations": concurrency * 16, "samples": [],
                 "unlinked_operations": concurrency * 16}
    evidence = {
        "schema": "mtagentlens.overhead-supplement/v1",
        "condition_id": plan_row["condition_id"], "agents": agents,
        "observer": observer, "cost_upper_bound_usd": None,
        "alert_latency": alert, "receipt_errors": [],
        "graph_update": graph,
    }
    (run_dir / "measurement-evidence.json").write_text(json.dumps(evidence))

    if profile == "C0":
        controlled = {"status": "not_applicable", "estimable": False,
                      "raw_transport_missing_fraction": None,
                      "persisted_operation_missing_fraction": None,
                      "raw_transport_missing_count": None,
                      "persisted_operation_missing_count": None}
        raw, persisted = None, None
    else:
        controlled = {"status": "measured", "estimable": True,
                      "raw_transport_missing_fraction": raw,
                      "persisted_operation_missing_fraction": persisted,
                      "raw_transport_missing_count": 0 if raw is not None else None,
                      "persisted_operation_missing_count": (
                          0 if persisted is not None else None)}
    if profile == "C1":
        controlled["persisted_operation_missing_fraction"] = None
        controlled["persisted_operation_missing_count"] = None
        persisted = None
    total_peak = (db_main or 0) + (db_wal_peak or 0)
    result = {
        "condition_id": plan_row["condition_id"], "block_id": plan_row["block_id"],
        "profile": profile, "concurrency": concurrency, "repeat": plan_row["repeat"],
        "status": status, "work_completion_rate": 1.0,
        "receipts": {"workload_completion_rate": 1.0, "receipts_complete": True},
        "duration_s": 2.5,
        "collector": {"started": profile != "C0", "attachment_proven": profile != "C0",
                      "observer_provider": ("llm_env" if profile == "C3" else
                                            "disabled" if profile == "C2" else "unknown")},
        "controlled_capture": controlled,
        "database_path": None if profile in ("C0", "C1") else "trace.log.responsibility.db",
        "resources": {
            "available": True,
            "collector": {
                "cpu_percent": {"mean": cpu_mean, "max": cpu_max, "min": 0.0,
                                "p50": cpu_mean, "p95": cpu_max},
                "rss_kib": {"max": rss_kib, "mean": rss_kib / 2, "min": 0},
            },
            "database_bytes": {
                "main": {"final": db_main if profile in ("C2", "C3") else None},
                "wal": {"final": db_wal_final, "peak": db_wal_peak},
                "total": {"final": (db_main if profile in ("C2", "C3") else None),
                          "peak": total_peak},
            },
            "trace_bytes": {"final": trace_final if profile != "C0" else None,
                            "peak": trace_final if profile != "C0" else None},
        },
    }
    (run_dir / "result.json").write_text(json.dumps(result))


def _generate(study, out, **kwargs):
    return report.generate_report(study, out, **kwargs)


def _csv_rows(out):
    with (out / "T8.csv").open(newline="") as handle:
        return list(csv.DictReader(handle))


def _cell(out, profile, concurrency):
    return next(row for row in _csv_rows(out)
                if row["profile"] == profile and row["concurrency"] == str(concurrency))


def test_strict_refuses_incomplete_formal_cohort(tmp_path):
    plan = _plan()
    study, run_dir = _study(tmp_path, plan, complete=True, completed=36)
    _write_run(run_dir / _row(plan, 1, 1, "C3")["condition_id"],
               _row(plan, 1, 1, "C3"))
    out = tmp_path / "out"
    with pytest.raises(report.ReportError, match="36") as error:
        _generate(study, out)
    assert "missing" in str(error.value).lower()
    assert not (out / "T8.csv").exists()
    assert not (out / "F2.png").exists()


def test_partial_is_labeled_not_final_and_missing_stays_none(tmp_path):
    plan = _plan()
    study, run_dir = _study(tmp_path, plan, phase="pilot")
    row = _row(plan, 1, 1, "C3")
    _write_run(run_dir / row["condition_id"], row, cpu_mean=None, raw=None,
               persisted=None)
    out = tmp_path / "out"
    _generate(study, out, allow_partial=True)
    text = (out / "T8.md").read_text()
    assert "PARTIAL — NOT FINAL" in text
    assert "FINAL — 36/36" not in text
    cell = _cell(out, "C3", 1)
    assert cell["raw_missing_fraction_mean"] == ""
    assert cell["persisted_operation_missing_fraction_mean"] == ""
    assert cell["collector_cpu_mean_percent_mean"] == ""
    assert cell["n_runs"] == "1"


def test_cell_aggregation_uses_per_run_mean_not_max(tmp_path):
    plan = _plan()
    study, run_dir = _study(tmp_path, plan)
    for repeat, (cpu_mean, cpu_max, rss_kib) in enumerate(
            ((10.0, 100.0, 1024), (30.0, 300.0, 3072)), start=1):
        row = _row(plan, 1, repeat, "C3")
        _write_run(run_dir / row["condition_id"], row, cpu_mean=cpu_mean,
                   cpu_max=cpu_max, rss_kib=rss_kib)
    out = tmp_path / "out"
    _generate(study, out, allow_partial=True)
    cell = _cell(out, "C3", 1)
    assert float(cell["collector_cpu_mean_percent_mean"]) == pytest.approx(20.0)
    assert float(cell["collector_cpu_mean_percent_max"]) == pytest.approx(30.0)
    assert float(cell["collector_rss_peak_mib_mean"]) == pytest.approx(2.0)
    assert float(cell["collector_rss_peak_mib_max"]) == pytest.approx(3.0)
    assert cell["n_runs"] == "2"


def test_strict_complete_36_is_final_and_a_failed_run_is_refused(tmp_path):
    plan = _plan()
    study, run_dir = _study(tmp_path, plan, complete=True, completed=36)
    for row in plan["condition_runs"]:
        _write_run(run_dir / row["condition_id"], row,
                   raw=0.0 if row["profile"] != "C0" else None,
                   persisted=0.0 if row["profile"] in ("C2", "C3") else None)
    out = tmp_path / "out"
    result = _generate(study, out)
    assert result["status"] == "final_complete"
    assert "FINAL — 36/36" in (out / "T8.md").read_text()
    assert "Condition elapsed (s)" in (out / "T8.md").read_text()
    assert _cell(out, "C3", 32)["cell_status"] == "complete"

    failed = run_dir / _row(plan, 1, 1, "C3")["condition_id"] / "result.json"
    payload = json.loads(failed.read_text())
    payload["status"] = "failed"
    failed.write_text(json.dumps(payload))
    refused = tmp_path / "refused"
    with pytest.raises(report.ReportError, match="failed"):
        _generate(study, refused)
    assert not (refused / "T8.csv").exists()


def test_partial_agent_set_does_not_fake_duration(tmp_path):
    plan = _plan()
    study, run_dir = _study(tmp_path, plan)
    row = _row(plan, 4, 1, "C3")
    _write_run(run_dir / row["condition_id"], row, agent_count=2)
    out = tmp_path / "out"
    _generate(study, out, allow_partial=True)
    cell = _cell(out, "C3", 4)
    assert cell["agent_duration_s_mean"] == ""
    raw = json.loads((out / "raw-per-run.json").read_text())
    assert raw["runs"][0]["agent_instances_observed"] == 2
    assert raw["runs"][0]["agent_duration_s"] is None


def test_cost_is_usage_peak_upper_bound_not_reported_cost(tmp_path):
    plan = _plan()
    study, run_dir = _study(tmp_path, plan)
    row = _row(plan, 1, 1, "C3")
    agent_usage = _usage(input_tokens=1000, output_tokens=100, reported=999.0,
                         bound=999.0)
    observer_usage = _usage(input_tokens=100, output_tokens=10, reported=999.0,
                            bound=999.0)
    observer = _observer(attempts=1, cost=999.0, records=[{
        "attempt_id": "a-1", "request_id": "a", "outcome": "success",
        "elapsed_ms": 12.0, "model": "deepseek-flash", "usage": observer_usage}])
    _write_run(run_dir / row["condition_id"], row, agent_usage=agent_usage,
               observer=observer)
    out = tmp_path / "out"
    _generate(study, out, allow_partial=True)
    cell = _cell(out, "C3", 1)
    assert float(cell["agent_cost_upper_bound_usd_mean"]) == pytest.approx(
        _peak_cost(agent_usage))
    assert float(cell["observer_cost_upper_bound_usd_mean"]) == pytest.approx(
        _peak_cost(observer_usage))
    assert float(cell["total_cost_upper_bound_usd_mean"]) == pytest.approx(
        _peak_cost(agent_usage) + _peak_cost(observer_usage))
    assert float(cell["total_cost_upper_bound_usd_mean"]) != pytest.approx(999.0)
    assert cell["cost_basis"] == "deepseek_peak_rate_upper_bound_not_invoice"
    raw = json.loads((out / "raw-per-run.json").read_text())
    assert raw["runs"][0]["observer_known_cost_upper_bound_usd"] == pytest.approx(
        _peak_cost(observer_usage))
    text = (out / "T8.md").read_text().lower()
    assert "upper bound" in text
    assert "not an invoice" in text
    assert "reported_cost_usd" in text


def test_plan_validation_rejects_high_concurrency_c1_c2(tmp_path):
    plan = _plan()
    plan["matrix"]["concurrency_profiles"]["4"] = ["C0", "C1", "C2", "C3"]
    with pytest.raises(report.ReportError, match="C1|C2|not planned"):
        report.validate_plan(plan)

    plan = _plan()
    rogue = dict(_row(plan, 1, 1, "C2"))
    rogue.update({"condition_id": "c4-r1-C2", "block_id": "c4-r1",
                  "concurrency": 4, "profile": "C2"})
    plan["condition_runs"].append(rogue)
    with pytest.raises(report.ReportError, match="36|planned|unexpected"):
        report.validate_plan(plan)


def test_pilot_requires_allow_partial_and_is_labelled(tmp_path):
    plan = _plan()
    study, run_dir = _study(tmp_path, plan, phase="pilot")
    row = _row(plan, 1, 1, "C3")
    _write_run(run_dir / row["condition_id"], row)
    out = tmp_path / "out"
    with pytest.raises(report.ReportError, match="formal-plan.json"):
        _generate(study, out)
    _generate(study, out, allow_partial=True)
    text = (out / "T8.md").read_text()
    assert "PARTIAL — NOT FINAL" in text
    assert "pilot" in text.lower()


def test_partial_figure_exports_headless_nonempty(tmp_path):
    import matplotlib
    plan = _plan()
    study, run_dir = _study(tmp_path, plan)
    for concurrency in (1, 4):
        for repeat in (1, 2, 3):
            row = _row(plan, concurrency, repeat, "C3")
            _write_run(run_dir / row["condition_id"], row,
                       agent_durations=(2.0, 3.0, 4.0),
                       raw=0.1 * repeat, persisted=0.2 * repeat,
                       graph={"count": 10, "total_ns": 1000})
    out = tmp_path / "out"
    result = _generate(study, out, allow_partial=True)
    assert result["status"] == "partial_not_final"
    assert matplotlib.get_backend().lower() == "agg"
    for name in ("F2.pdf", "F2.svg", "F2.png"):
        path = out / name
        assert path.is_file() and path.stat().st_size > 0
    assert (out / "F2.pdf").read_bytes().startswith(b"%PDF")
    assert b"<svg" in (out / "F2.svg").read_bytes()
    assert (out / "F2.png").read_bytes().startswith(b"\x89PNG")
    caption = (out / "T8.md").read_text().lower()
    assert "individual repeats" in caption
    assert "no ci" in caption
    assert "p99" in caption


def test_probe_and_diagnostic_tables_mark_coverage_and_filtering(tmp_path):
    plan = _plan()
    study, run_dir = _study(tmp_path, plan)
    row = _row(plan, 1, 1, "C3")
    _write_run(run_dir / row["condition_id"], row)
    probes = tmp_path / "tls.json"
    probes.write_text(json.dumps({
        "schema": "mtagentlens.probe-overhead-measurement/1", "status": "ok",
        "mode": "tls_probe_microbenchmark", "repeats_requested": 3,
        "repeats": [{"index": 1, "programs": [
            {"id": 1, "name": "tls_capture", "covered": True,
             "run_cnt_delta": 10, "run_time_ns_delta": 1000, "ns_per_call": 100.0},
            {"id": 2, "name": "unused_ssl", "covered": False,
             "run_cnt_delta": 0, "run_time_ns_delta": 0, "ns_per_call": None}],
            "totals": {"successful_ssl_operations": 20,
                       "ns_per_successful_ssl_operation": 50.0}}],
        "summary": {"repeats": 1, "ns_per_successful_ssl_operation": {
            "n": 1, "mean": 50.0, "min": 50.0, "max": 50.0},
            "covered_program_ids": [1]},
    }))
    diagnostics = tmp_path / "diagnostics.json"
    diagnostics.write_text(json.dumps({
        "schema": "mtagentlens.probe-overhead-diagnostic/1",
        "mode": "existing_programs_diagnostic", "status": "ok",
        "programs": [{"id": 7, "name": "syscall_probe", "covered": True,
                      "run_cnt_delta": 5, "run_time_ns_delta": 750,
                      "ns_per_call": 150.0}],
        "totals": {"run_cnt": 5, "run_time_ns": 750},
    }))
    out = tmp_path / "out"
    _generate(study, out, allow_partial=True, probes=probes,
              diagnostics=diagnostics)
    text = (out / "T8.md").read_text().lower()
    assert "tls probe" in text
    assert "per successful ssl operation" in text
    assert "tls_capture" in text
    assert "uncovered" in text
    assert "syscall_probe" in text
    assert "diagnostic" in text
    assert "filtered" in text


def test_probe_and_diagnostics_are_pending_without_files(tmp_path):
    plan = _plan()
    study, run_dir = _study(tmp_path, plan)
    row = _row(plan, 1, 1, "C3")
    _write_run(run_dir / row["condition_id"], row)
    out = tmp_path / "out"
    _generate(study, out, allow_partial=True)
    text = (out / "T8.md").read_text().lower()
    assert "probe supplement: pending" in text
    assert "diagnostics: pending" in text


def test_summary_export_targets_cell_metrics_for_claims(tmp_path):
    plan = _plan()
    study, run_dir = _study(tmp_path, plan)
    row = _row(plan, 1, 1, "C3")
    _write_run(run_dir / row["condition_id"], row)
    out = tmp_path / "out"
    _generate(study, out, allow_partial=True)
    summary = json.loads((out / "summary.json").read_text())
    assert summary["schema"] == "mtagentlens.overhead-summary/1"
    cell = summary["cells"]["C3/1"]
    assert cell["condition_elapsed_s"]["n"] == 1
    assert cell["condition_elapsed_s"]["mean"] is not None


def test_final_t8_discloses_recovery_of_truncated_attempt(tmp_path):
    plan = _plan()
    study, run_dir = _study(tmp_path, plan, complete=True, completed=36)
    for row in plan["condition_runs"]:
        _write_run(run_dir / row["condition_id"], row,
                   raw=0.0 if row["profile"] != "C0" else None,
                   persisted=0.0 if row["profile"] in ("C2", "C3") else None)
    report_path = run_dir / "execution-report.json"
    execution = json.loads(report_path.read_text())
    execution["recovery"] = {"archived_attempts": [{"condition_id": "c32-r3-C3"}]}
    report_path.write_text(json.dumps(execution))
    out = tmp_path / "out"
    _generate(study, out)
    text = (out / "T8.md").read_text()
    assert "Recovery:" in text and "c32-r3-C3" in text
    assert "archived" in text
