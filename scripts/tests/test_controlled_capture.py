# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Controlled file-open capture adapter: exact anchors, no scalar bypass."""
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from scripts.evaluation import controlled_capture as cc


def receipt(receipt_id="r1", path="/tmp/op", pid=101, start=500,
            started=1_000, completed=2_000, **extra):
    row = {"receipt_id": receipt_id, "path": path, "pid": pid,
           "process_start_time_ns": start, "started_ns": started,
           "completed_ns": completed}
    row.update(extra)
    return row


def trace_event(path="/tmp/op", pid=101, start=500, outcome="succeeded", count=1):
    return {"timestamp": 1, "timestamp_ns": 1_000, "source": "process",
            "pid": pid, "comm": "agent",
            "data": {"event": "FILE_OPEN", "filepath": path, "pid": pid,
                     "start_time_ns": start, "outcome": outcome, "count": count}}


def build(tmp_path, *, profile="C2", receipts=None, events=None, actions=None,
          health=None, database=True, actions_schema=None, user_version=5,
          file_receipts=None, wal=False, wal_bytes=b"wal"):
    receipts = [receipt()] if receipts is None else receipts
    events = [trace_event()] if events is None else events
    actions = [("a1", 101, 500, "FileOpen", "/tmp/op", "succeeded")] if actions is None else actions
    directory = Path(tempfile.mkdtemp(dir=tmp_path))
    receipt_path = directory / "workload-0.jsonl"
    written = receipts if file_receipts is None else file_receipts
    receipt_path.write_text("".join(json.dumps(row) + "\n" for row in written))
    trace_path = directory / "trace.log"
    trace_path.write_text("".join(json.dumps(row) + "\n" for row in events))
    database_path = None
    if database and profile in ("C2", "C3"):
        database_path = directory / "trace.log.responsibility.db"
        with sqlite3.connect(database_path) as conn:
            columns = actions_schema or (
                "action_id TEXT, pid INTEGER, process_start_time_ns INTEGER, "
                "action_type TEXT, normalized_args_json TEXT, outcome TEXT")
            conn.execute(f"CREATE TABLE actions ({columns})")
            conn.execute(f"PRAGMA user_version={user_version}")
            if actions_schema is None:
                for action_id, pid, start, action_type, path, outcome in actions:
                    conn.execute(
                        "INSERT INTO actions VALUES (?, ?, ?, ?, ?, ?)",
                        (action_id, pid, start, action_type,
                         json.dumps([{"Path": path}, {"OpenFlags": 0}]), outcome))
        if wal:
            database_path.with_name(database_path.name + "-wal").write_bytes(wal_bytes)
    if health is None:
        health = {"attachment_proven": True,
                  "final_flush_observed": profile in ("C2", "C3")}
    return cc.build_controlled_capture(
        profile=profile, condition_id="c1-r1-" + profile, receipts=receipts,
        receipt_paths=[str(receipt_path)], expected_receipts=len(receipts),
        trace_path=trace_path, database_path=database_path, health=health)


def test_exact_anchor_and_coalesced_count_is_one_operation(tmp_path):
    got = build(tmp_path, events=[trace_event(count=9)])
    assert got["adapter_version"] == "controlled_file_open/v1"
    assert got["status"] == "measured" and got["estimable"] is True
    assert got["denominator"] == {
        "kind": "unique_completed_controlled_operations", "expected": 1,
        "observed": 1, "complete": True}
    assert got["raw_transport_missing_fraction"] == 0.0
    assert got["persisted_operation_missing_fraction"] == 0.0
    assert got["matches"]["raw"]["r1"]["count"] == 9
    assert got["matches"]["persisted"] == {"r1": "a1"}
    assert got["empirical_validation"] == "pending_real_recording"
    assert got["scope"] == "controlled_file_open_operations_only"


def test_prefix_path_is_not_a_match(tmp_path):
    got = build(tmp_path, receipts=[receipt(path="/tmp/op")],
                events=[trace_event(path="/tmp/op-suffix")],
                actions=[("a1", 101, 500, "FileOpen", "/tmp/op-suffix", "succeeded")])
    assert got["status"] == "measured"
    assert got["matches"]["raw"] == {} and got["matches"]["persisted"] == {}
    assert got["raw_transport_missing_fraction"] == 1.0
    assert got["persisted_operation_missing_fraction"] == 1.0


@pytest.mark.parametrize("field,value", [
    ("pid", 202), ("start", 999), ("outcome", "failed")])
def test_wrong_process_anchor_or_outcome_is_missing_not_matched(tmp_path, field, value):
    expected = {"pid": 202, "start": 999, "outcome": "failed"}[field]
    got = build(tmp_path,
                events=[trace_event(**{field: expected})],
                actions=[("a1", 202 if field == "pid" else 101,
                          999 if field == "start" else 500, "FileOpen",
                          "/tmp/op", "failed" if field == "outcome" else "succeeded")])
    assert got["raw_transport_missing_fraction"] == 1.0
    assert got["persisted_operation_missing_fraction"] == 1.0


def test_workload_cli_emits_receipts_from_private_cwd(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cc.main(["workload", "--operations", "2", "--comm", "mtlwtest"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 2
    assert len({row["receipt_id"] for row in rows}) == 2
    assert len({row["path"] for row in rows}) == 2
    for row in rows:
        assert row["pid"] == os.getpid()
        assert Path(row["path"]).is_absolute() and Path(row["path"]).is_file()
        assert isinstance(row["process_start_time_ns"], int)
        assert row["process_start_resolution_ns"] > 0
        assert row["monotonic_started_ns"] <= row["monotonic_completed_ns"]
        assert row["started_ns"] <= row["completed_ns"]
        assert row["pid_namespace_assumption"] == "same_host_single_pid_namespace"
    assert Path("/proc/self/comm").read_text().strip() == "mtlwtest"


def test_tick_resolution_anchor_range_matches_only_within_birth_interval(tmp_path):
    got = build(tmp_path, receipts=[receipt(start=1_000, process_start_resolution_ns=100)],
                events=[trace_event(start=1_050)],
                actions=[("a1", 101, 1_050, "FileOpen", "/tmp/op", "succeeded")])
    assert got["raw_transport_missing_fraction"] == 0.0
    assert got["persisted_operation_missing_fraction"] == 0.0
    outside = build(tmp_path, receipts=[receipt(start=1_000, process_start_resolution_ns=100)],
                    events=[trace_event(start=1_100)],
                    actions=[("a1", 101, 1_100, "FileOpen", "/tmp/op", "succeeded")])
    assert outside["raw_transport_missing_fraction"] == 1.0
    assert outside["persisted_operation_missing_fraction"] == 1.0


def test_monotonic_interval_uses_raw_data_timestamp_not_outer_event_time(tmp_path):
    inside = build(
        tmp_path,
        receipts=[receipt(monotonic_started_ns=100, monotonic_completed_ns=200)],
        events=[{**trace_event(), "timestamp": 9_999_999,
                 "data": {**trace_event()["data"], "timestamp": 150}}])
    assert inside["raw_transport_missing_fraction"] == 0.0
    outside = build(
        tmp_path,
        receipts=[receipt(monotonic_started_ns=100, monotonic_completed_ns=200)],
        events=[{**trace_event(), "timestamp": 150,
                 "data": {**trace_event()["data"], "timestamp": 250}}])
    assert outside["raw_transport_missing_fraction"] == 1.0


def test_build_rejects_receipts_argument_that_differs_from_jsonl(tmp_path):
    got = build(tmp_path, receipts=[receipt(path="/tmp/forged")],
                file_receipts=[receipt(path="/tmp/actual")])
    assert got["status"] == "not_estimable"
    assert any("receipts_argument_mismatch" in reason for reason in got["reasons"])


def test_explicit_header_is_skipped_but_unknown_records_are_rejected(tmp_path):
    header = {"record_type": "header", "workload": "controlled_file_open/v1"}
    ok = build(tmp_path, receipts=[receipt()], file_receipts=[header, receipt()])
    assert ok["status"] == "measured"
    unknown = build(tmp_path, receipts=[receipt()],
                    file_receipts=[{"record_type": "summary"}, receipt()])
    assert unknown["status"] == "not_estimable"
    assert any("unknown_record" in reason for reason in unknown["reasons"])


def test_database_requires_schema5_regular_file_without_wal(tmp_path):
    old = build(tmp_path, user_version=4)
    assert old["status"] == "not_estimable"
    assert any("unsupported_schema" in reason for reason in old["reasons"])
    wal = build(tmp_path, wal=True)
    assert wal["status"] == "not_estimable"
    assert any("wal" in reason for reason in wal["reasons"])


def test_empty_wal_after_a_clean_checkpoint_is_accepted(tmp_path):
    # sqlite leaves a zero-byte -wal (and -shm) after a clean close; that is a
    # finalized database, not an unflushed one.
    got = build(tmp_path, wal=True, wal_bytes=b"")
    assert got["status"] == "measured", got["reasons"]


def test_unsupported_action_schema_is_not_estimable(tmp_path):
    got = build(tmp_path, actions_schema="action_id TEXT, pid INTEGER")
    assert got["status"] == "not_estimable"
    assert any("unsupported_schema" in reason for reason in got["reasons"])


def test_any_action_type_is_not_accepted(tmp_path):
    got = build(tmp_path, actions=[
        ("a1", 101, 500, "FileRead", "/tmp/op", "succeeded")])
    assert got["raw_transport_missing_fraction"] == 0.0
    assert got["persisted_operation_missing_fraction"] == 1.0


def test_c1_needs_no_database_and_reports_only_raw(tmp_path):
    got = build(tmp_path, profile="C1", database=False,
                health={"attachment_proven": True, "final_flush_observed": None})
    assert got["status"] == "measured"
    assert got["raw_transport_missing_fraction"] == 0.0
    assert got["persisted_operation_missing_fraction"] is None
    assert got["coverage"]["persisted_operation"] is None
    assert got["artifact_binding"]["database"] is None


def test_c0_is_not_applicable_without_zero(tmp_path):
    got = build(tmp_path, profile="C0", database=False)
    assert got["status"] == "not_applicable" and got["estimable"] is False
    assert got["raw_transport_missing_fraction"] is None
    assert got["persisted_operation_missing_fraction"] is None


def test_missing_anchor_fields_are_not_estimable(tmp_path):
    got = build(tmp_path, receipts=[{"receipt_id": "r1", "path": "relative"}])
    assert got["status"] == "not_estimable" and got["estimable"] is False
    assert any("invalid_receipt" in reason for reason in got["reasons"])


def test_incomplete_duplicate_id_and_duplicate_anchor_are_not_estimable(tmp_path):
    got = build(tmp_path, receipts=[receipt()], events=[trace_event()])
    assert got["status"] == "measured"
    # Incomplete expected count cannot be silently treated as zero loss.
    incomplete = cc.build_controlled_capture(
        profile="C1", condition_id="c", receipts=[], receipt_paths=[],
        expected_receipts=1, trace_path=tmp_path / "trace.log",
        health={"attachment_proven": True})
    assert incomplete["status"] == "not_estimable"
    assert any("incomplete_receipts" in reason for reason in incomplete["reasons"])
    duplicate_id = build(tmp_path, receipts=[
        receipt("r1"), receipt("r1", path="/tmp/other")])
    assert duplicate_id["status"] == "not_estimable"
    assert any("duplicate_receipt_id" in reason for reason in duplicate_id["reasons"])
    duplicate_anchor = build(tmp_path, receipts=[receipt("r1"), receipt("r2")])
    assert duplicate_anchor["status"] == "not_estimable"
    assert any("duplicate_occurrence_anchor" in reason
               for reason in duplicate_anchor["reasons"])
    overlapping = build(tmp_path, receipts=[
        receipt("r1", start=1_000, process_start_resolution_ns=100),
        receipt("r2", start=1_050, process_start_resolution_ns=100)])
    assert overlapping["status"] == "not_estimable"
    assert any("overlapping_occurrence_anchor" in reason
               for reason in overlapping["reasons"])


def test_missing_raw_trace_or_unproven_health_is_not_estimable(tmp_path):
    got = build(tmp_path, events=[])
    assert got["status"] == "measured"
    assert got["raw_transport_missing_fraction"] == 1.0
    missing = cc.build_controlled_capture(
        profile="C1", condition_id="c", receipts=[receipt()],
        receipt_paths=[str(tmp_path / "workload-0.jsonl")],
        expected_receipts=1, trace_path=tmp_path / "absent.log",
        health={"attachment_proven": True})
    assert missing["status"] == "not_estimable"
    assert "missing_raw_trace" in missing["reasons"]
    unhealthy = build(tmp_path, health={"attachment_proven": False})
    assert unhealthy["status"] == "not_estimable"
    assert "capture_health_unproven" in unhealthy["reasons"]
    flush = build(tmp_path, health={"attachment_proven": True,
                                    "final_flush_observed": False})
    assert flush["status"] == "not_estimable"
    assert "persisted_flush_unproven" in flush["reasons"]


def test_positive_missing_rate_from_valid_observations_stays_measured(tmp_path):
    got = build(tmp_path, receipts=[receipt("r1", path="/tmp/a")],
                events=[trace_event(path="/tmp/b")],
                actions=[("a1", 101, 500, "FileOpen", "/tmp/b", "succeeded")])
    assert got["status"] == "measured" and got["estimable"] is True
    assert got["raw_transport_missing_fraction"] == 1.0
    assert got["persisted_operation_missing_fraction"] == 1.0


def test_no_observation_reused_across_receipts(tmp_path):
    events = [trace_event(path="/tmp/a")]
    got = build(tmp_path, receipts=[
        receipt("r1", path="/tmp/a"), receipt("r2", path="/tmp/b")],
        events=events,
        actions=[("a1", 101, 500, "FileOpen", "/tmp/a", "succeeded")])
    assert got["status"] == "measured"
    assert got["unmatched_raw_receipt_ids"] == ["r2"]
    assert got["raw_transport_missing_fraction"] == 0.5
    assert got["persisted_operation_missing_fraction"] == 0.5


def _symlink_database_fixture(tmp_path, *, link_final_component):
    """Build receipts/trace by hand and a schema-5 DB reachable via a symlink.

    Only a parent directory is linked in the accepted case, so the database is
    still this run's regular artifact; linking the final path makes the
    recorded "database" a different file and must be rejected.
    """
    directory = tmp_path / "run"
    directory.mkdir()
    op = directory / "op"
    op.write_text("x")
    row = receipt(path=str(op.resolve()))
    receipt_path = directory / "workload-0.jsonl"
    receipt_path.write_text(json.dumps(row) + "\n")
    trace_path = directory / "trace.log"
    trace_path.write_text(json.dumps(trace_event(path=str(op.resolve()))) + "\n")
    real = tmp_path / "real"
    real.mkdir()
    database = real / "trace.log.responsibility.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TABLE actions (action_id TEXT, pid INTEGER, "
            "process_start_time_ns INTEGER, action_type TEXT, "
            "normalized_args_json TEXT, outcome TEXT)")
        conn.execute("PRAGMA user_version=5")
        conn.execute("INSERT INTO actions VALUES (?, ?, ?, ?, ?, ?)",
                     ("a1", 101, 500, "FileOpen",
                      json.dumps([{"Path": str(op.resolve())}]), "succeeded"))
    if link_final_component:
        database_path = directory / "trace.log.responsibility.db"
        database_path.symlink_to(database)
    else:
        parent_link = directory / "dbdir"
        parent_link.symlink_to(real, target_is_directory=True)
        database_path = parent_link / "trace.log.responsibility.db"
    return cc.build_controlled_capture(
        profile="C2", condition_id="c-symlink", receipts=[row],
        receipt_paths=[str(receipt_path)], expected_receipts=1,
        trace_path=trace_path, database_path=database_path,
        health={"attachment_proven": True, "final_flush_observed": True})


def test_symlinked_database_is_rejected_instead_of_silently_resolved(tmp_path):
    got = _symlink_database_fixture(tmp_path, link_final_component=True)
    assert got["status"] == "not_estimable"
    assert any("database_not_regular_file" in reason for reason in got["reasons"])
    assert got["persisted_operation_missing_fraction"] is None


def test_symlinked_parent_directory_is_still_a_regular_database(tmp_path):
    got = _symlink_database_fixture(tmp_path, link_final_component=False)
    assert got["status"] == "measured", got["reasons"]
    assert got["persisted_operation_missing_fraction"] == 0.0


def test_proc_birth_clock_converts_boottime_to_bpf_monotonic_after_suspend():
    # /proc start ticks include suspend; task->start_time/BPF ktime do not.
    lower, resolution = cc.monotonic_birth_interval(76552, 1_000_000,
        monotonic_before=75_300_000_000, boottime=76_549_830_000,
        monotonic_after=75_300_000_200)
    assert lower == 75_302_170_000
    assert resolution == 1_000_200
    assert lower <= 75_302_600_000 < lower+resolution
