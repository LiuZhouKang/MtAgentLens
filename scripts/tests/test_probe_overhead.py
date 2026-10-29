# SPDX-License-Identifier: Apache-2.0 OR MIT
"""TLS microbenchmark target and probe-overhead diagnostic contracts.

The C target is compiled and exercised for real. The Python collector/bpftool
paths are exercised with fake executables so these tests never need root.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SSL_LOOP_SRC = ROOT / "compare" / "scripts" / "perf" / "bench_targets" / "ssl_loop.c"


def compile_ssl_loop(tmp_path: Path) -> Path:
    cc = shutil.which("cc") or shutil.which("gcc")
    if not cc:
        pytest.skip("C compiler not available")
    exe = tmp_path / "ssl_loop"
    proc = subprocess.run(
        [cc, "-O2", "-Wall", "-Wextra", "-o", str(exe), str(SSL_LOOP_SRC),
         "-lssl", "-lcrypto"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        stderr = proc.stderr
        missing_dependency = any(marker in stderr for marker in (
            "openssl/ssl.h", "openssl/evp.h", "cannot find -lssl",
            "cannot find -lcrypto",
        ))
        if missing_dependency:
            pytest.skip(f"OpenSSL development headers/libraries unavailable: {stderr[-400:]}")
        pytest.fail(f"ssl_loop.c failed to compile:\n{stderr[-1200:]}")
    return exe


def run_ssl_loop(exe: Path, *args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(exe), *map(str, args)], capture_output=True, text=True,
                          timeout=30)


def final_json(proc: subprocess.CompletedProcess[str]) -> dict:
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert lines, f"no stdout JSON; rc={proc.returncode} stderr={proc.stderr[-500:]}"
    return json.loads(lines[-1])


def test_ssl_loop_completes_real_memory_bio_handshake_and_counts_payload(tmp_path):
    exe = compile_ssl_loop(tmp_path)
    proc = run_ssl_loop(exe, 37)
    assert proc.returncode == 0, proc.stderr[-800:]
    data = final_json(proc)

    assert data["schema"] == "mtagentlens.ssl-loop/1"
    assert data["status"] == "ok"
    assert data["handshake"] == "ok"
    assert data["iterations"] == 37
    # Full-duplex echo: each iteration is one write+read on each endpoint.
    assert data["ssl_write_count"] == 2 * 37
    assert data["ssl_read_count"] == 2 * 37
    assert data["ssl_operation_count"] == 4 * 37
    assert data["payload_bytes"] == 256
    assert data["bytes_written"] == 2 * 37 * 256
    assert data["bytes_read"] == 2 * 37 * 256
    assert data["client"] == {"write_count": 37, "read_count": 37,
                              "bytes_written": 37 * 256, "bytes_read": 37 * 256}
    assert data["server"] == {"write_count": 37, "read_count": 37,
                              "bytes_written": 37 * 256, "bytes_read": 37 * 256}
    assert isinstance(data["elapsed_ns"], int) and data["elapsed_ns"] >= 0


def test_ssl_loop_gate_waits_for_go_and_excludes_handshake_and_warmup(tmp_path):
    exe = compile_ssl_loop(tmp_path)
    proc = subprocess.Popen(
        [str(exe), "5", "--warmup", "3", "--gate"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        ready_line = proc.stdout.readline()
        assert ready_line, f"no readiness line; stderr={proc.stderr.read()[-500:]}"
        ready = json.loads(ready_line)
        assert ready["ready"] is True
        assert ready["handshake"] == "ok"
        assert ready["warmup_iterations"] == 3
        assert "elapsed_ns" not in ready
        proc.stdin.write("GO\n")
        proc.stdin.flush()
        stdout, stderr = proc.communicate(timeout=30)
        assert proc.returncode == 0, stderr[-800:]
        data = json.loads(stdout.strip().splitlines()[-1])
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert data["iterations"] == 5
    assert data["warmup_iterations"] == 3
    assert data["ssl_write_count"] == 10
    assert data["ssl_read_count"] == 10
    assert data["ssl_operation_count"] == 20


@pytest.mark.parametrize("args", [
    ("0",),
    ("-5",),
    ("abc",),
    ("5", "--warmup", "-1"),
    ("5", "--bogus"),
])
def test_ssl_loop_rejects_invalid_arguments(tmp_path, args):
    exe = compile_ssl_loop(tmp_path)
    proc = run_ssl_loop(exe, *args)
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# probe_overhead.py: pure accounting and plan/diagnostic contracts
# ---------------------------------------------------------------------------
from scripts.evaluation import probe_overhead as po  # noqa: E402


def fake_executable(path: Path, marker: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('ran')\n"
    )
    path.chmod(0o755)
    return path


def fake_bpftool(tmp_path: Path, *, snapshots, links=None, fail=False,
                 sysctl_path: Path | None = None) -> Path:
    data_path = tmp_path / "bpftool-data.json"
    counter = tmp_path / "bpftool-prog-calls"
    seen = tmp_path / "bpftool-sysctl-seen"
    data_path.write_text(json.dumps({
        "snapshots": snapshots,
        "links": links or [],
        "fail": fail,
        "sysctl": str(sysctl_path) if sysctl_path else "",
        "seen": str(seen),
    }))
    script = tmp_path / "fake-bpftool"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"data = json.loads(pathlib.Path({str(data_path)!r}).read_text())\n"
        "if data['fail']:\n"
        "    print('simulated bpftool failure', file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "prog = len(sys.argv) >= 3 and sys.argv[1] == 'prog' and sys.argv[2] == 'show'\n"
        "link = len(sys.argv) >= 3 and sys.argv[1] == 'link' and sys.argv[2] == 'show'\n"
        f"counter = pathlib.Path({str(counter)!r})\n"
        "if prog:\n"
        "    index = int(counter.read_text()) if counter.exists() else 0\n"
        "    snapshot = data['snapshots'][min(index, len(data['snapshots']) - 1)]\n"
        "    counter.write_text(str(index + 1))\n"
        "    if data['sysctl']:\n"
        "        value = pathlib.Path(data['sysctl']).read_text().strip()\n"
        "        pathlib.Path(data['seen']).write_text(value)\n"
        "    print(json.dumps(snapshot))\n"
        "elif link:\n"
        "    print(json.dumps(data['links']))\n"
        "else:\n"
        "    print('unexpected arguments', file=sys.stderr)\n"
        "    raise SystemExit(2)\n"
    )
    script.chmod(0o755)
    return script


def program(pid: int, name: str = "probe_SSL_read", run_cnt: int = 0,
            run_time_ns: int = 0, kind: str = "uprobe") -> dict:
    return {"id": pid, "name": name, "type": kind,
            "run_cnt": run_cnt, "run_time_ns": run_time_ns, "bytes_xlated": 128}


def fake_collector(path: Path) -> Path:
    marker = ('{"event":"TLS_CAPTURE_PROBES","attached":2,"failed":0,'
              '"core_read":true,"core_write":true}')
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        f"sys.stderr.write({marker!r} + '\\n')\n"
        "sys.stderr.flush()\n"
        "while True:\n"
        "    time.sleep(0.1)\n"
    )
    path.chmod(0o755)
    return path


def fake_target(path: Path) -> Path:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "iterations = int(args[0])\n"
        "warmup = int(args[args.index('--warmup') + 1])\n"
        "payload = 256\n"
        "print(json.dumps({\n"
        "    'schema': 'mtagentlens.ssl-loop/1', 'ready': True,\n"
        "    'handshake': 'ok', 'pid': 1, 'payload_bytes': payload,\n"
        "    'warmup_iterations': warmup,\n"
        "}), flush=True)\n"
        "if sys.stdin.readline().strip() != 'GO':\n"
        "    raise SystemExit(2)\n"
        "ops = iterations * 4\n"
        "print(json.dumps({\n"
        "    'schema': 'mtagentlens.ssl-loop/1', 'status': 'ok',\n"
        "    'handshake': 'ok', 'iterations': iterations,\n"
        "    'warmup_iterations': warmup, 'payload_bytes': payload,\n"
        "    'client': {'write_count': iterations, 'read_count': iterations,\n"
        "               'bytes_written': iterations * payload,\n"
        "               'bytes_read': iterations * payload},\n"
        "    'server': {'write_count': iterations, 'read_count': iterations,\n"
        "               'bytes_written': iterations * payload,\n"
        "               'bytes_read': iterations * payload},\n"
        "    'ssl_write_count': iterations * 2, 'ssl_read_count': iterations * 2,\n"
        "    'ssl_operation_count': ops,\n"
        "    'bytes_written': iterations * 2 * payload,\n"
        "    'bytes_read': iterations * 2 * payload, 'elapsed_ns': 1,\n"
        "}), flush=True)\n"
    )
    path.chmod(0o755)
    return path


def test_select_run_programs_uses_ids_not_names_and_requires_attach():
    before = {7}
    after = [
        program(7, "probe_SSL_read"),
        program(8, "probe_SSL_read"),
        program(9, "probe_SSL_writ"),
        program(10, "probe_SSL_read"),
    ]
    selected = po.select_run_programs(before, after, {8, 9})
    assert [row["id"] for row in selected] == [8, 9]

    with pytest.raises(ValueError, match="no bpftool programs"):
        po.select_run_programs(set(), [], {1})
    with pytest.raises(ValueError, match="attached"):
        po.select_run_programs(set(), [program(5)], set())


def test_counter_deltas_track_each_id_and_mark_zero_count_uncovered():
    start = [
        program(8, run_cnt=10, run_time_ns=100),
        program(9, run_cnt=90, run_time_ns=900),
        program(10, run_cnt=0, run_time_ns=0),
    ]
    end = [
        program(8, run_cnt=20, run_time_ns=200),
        program(9, run_cnt=180, run_time_ns=2700),
        program(10, run_cnt=0, run_time_ns=0),
    ]
    deltas = po.counter_deltas(start, end, [8, 9, 10])

    assert deltas[0]["run_cnt_delta"] == 10
    assert deltas[0]["run_time_ns_delta"] == 100
    assert deltas[0]["ns_per_call"] == 10.0
    assert deltas[1]["run_cnt_delta"] == 90
    assert deltas[1]["ns_per_call"] == 20.0
    assert deltas[2]["covered"] is False
    assert deltas[2]["run_cnt_delta"] == 0
    assert deltas[2]["ns_per_call"] is None

    # Sum of run time over all successful operations, not a sum of means.
    assert po.total_ns_per_operation(deltas, 200) == 9.5
    assert po.total_ns_per_operation([deltas[2]], 200) is None


def test_counter_deltas_reject_missing_or_decreasing_counters():
    start = [program(8, run_cnt=10, run_time_ns=100)]
    end = [program(8, run_cnt=20, run_time_ns=200)]
    with pytest.raises(ValueError, match="missing"):
        po.counter_deltas(start, [], [8])
    with pytest.raises(ValueError, match="missing"):
        po.counter_deltas([], end, [8])
    with pytest.raises(ValueError, match="decreased"):
        po.counter_deltas([program(8, run_cnt=2)], [program(8, run_cnt=1)], [8])


def test_stats_gate_restores_original_value_on_error(tmp_path):
    path = tmp_path / "bpf_stats_enabled"
    path.write_text("0\n")
    with pytest.raises(RuntimeError, match="boom"):
        with po.StatsGate(path) as gate:
            assert path.read_text().strip() == "1"
            assert gate.changed is True
            raise RuntimeError("boom")
    assert path.read_text().strip() == "0"

    path.write_text("1\n")
    with po.StatsGate(path) as gate:
        assert gate.changed is False
        assert path.read_text().strip() == "1"
    assert path.read_text().strip() == "1"


def test_stats_gate_rejects_unknown_original_value(tmp_path):
    path = tmp_path / "bpf_stats_enabled"
    path.write_text("nonsense\n")
    with pytest.raises(ValueError, match="bpf_stats_enabled"):
        with po.StatsGate(path):
            pass


def test_plan_only_writes_plan_and_runs_nothing(tmp_path):
    marker = tmp_path / "executed"
    collector = fake_executable(tmp_path / "collector", marker)
    target = fake_executable(tmp_path / "target", marker)
    bpftool_marker = tmp_path / "bpftool-ran"
    bpftool = fake_executable(tmp_path / "bpftool", bpftool_marker)
    out = tmp_path / "plan.json"

    rc = po.main([
        "--collector", str(collector), "--target", str(target), "--out", str(out),
        "--bpftool", str(bpftool), "--libssl", str(target), "--no-sudo",
        "--iterations", "10", "--warmup", "2", "--repeats", "3",
    ])
    assert rc == 0
    plan = json.loads(out.read_text())
    assert plan["schema"] == "mtagentlens.probe-overhead-plan/1"
    assert plan["mode"] == "tls_probe_microbenchmark"
    assert plan["execution"]["execute"] is False
    assert plan["repeats"] == 3
    assert plan["payload_bytes"] == 256
    assert plan["target_argv"][0] == str(target.resolve())
    assert "--gate" in plan["target_argv"]
    assert plan["collector_argv"][-2:] == ["-u", str(plan["target"]["uid"])]
    assert not marker.exists()
    assert not bpftool_marker.exists()


def test_diagnose_existing_plan_only_does_not_query_bpftool(tmp_path):
    marker = tmp_path / "bpftool-ran"
    bpftool = fake_executable(tmp_path / "bpftool", marker)
    out = tmp_path / "diagnose-plan.json"
    rc = po.main([
        "--diagnose-existing", "--program-id", "42", "--out", str(out),
        "--bpftool", str(bpftool), "--no-sudo",
    ])
    assert rc == 0
    plan = json.loads(out.read_text())
    assert plan["mode"] == "existing_programs_diagnostic"
    assert plan["program_ids"] == [42]
    assert plan["execution"]["execute"] is False
    assert not marker.exists()


def test_diagnose_existing_execute_reports_deltas_and_restores_stats(tmp_path):
    sysctl = tmp_path / "bpf_stats_enabled"
    sysctl.write_text("0\n")
    start = [program(42, "probe_SSL_read", run_cnt=100, run_time_ns=1000)]
    end = [program(42, "probe_SSL_read", run_cnt=300, run_time_ns=5000)]
    bpftool = fake_bpftool(tmp_path, snapshots=[start, end], sysctl_path=sysctl)
    out = tmp_path / "diagnose.json"

    rc = po.main([
        "--diagnose-existing", "--program-id", "42", "--window-seconds", "0",
        "--execute", "--out", str(out), "--bpftool", str(bpftool),
        "--sysctl", str(sysctl), "--no-sudo",
    ])
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["schema"] == "mtagentlens.probe-overhead-diagnostic/1"
    assert data["programs"][0]["run_cnt_delta"] == 200
    assert data["programs"][0]["run_time_ns_delta"] == 4000
    assert data["programs"][0]["ns_per_call"] == 20.0
    assert data["totals"]["run_cnt"] == 200
    assert data["start_snapshot"][0]["run_cnt"] == 100
    assert data["end_snapshot"][0]["run_cnt"] == 300
    assert data["stats_gate"]["restored"] is True
    assert sysctl.read_text().strip() == "0"
    assert (tmp_path / "bpftool-sysctl-seen").read_text().strip() == "1"


def test_diagnose_existing_missing_id_fails_and_restores_stats(tmp_path):
    sysctl = tmp_path / "bpf_stats_enabled"
    sysctl.write_text("0\n")
    bpftool = fake_bpftool(
        tmp_path,
        snapshots=[[program(42, run_cnt=10, run_time_ns=100)],
                   [program(42, run_cnt=20, run_time_ns=200)]],
        sysctl_path=sysctl,
    )
    out = tmp_path / "missing.json"
    rc = po.main([
        "--diagnose-existing", "--program-id", "99", "--window-seconds", "0",
        "--execute", "--out", str(out), "--bpftool", str(bpftool),
        "--sysctl", str(sysctl), "--no-sudo",
    ])
    assert rc == 1
    data = json.loads(out.read_text())
    assert data["status"] == "error"
    assert "99" in data["error"]
    assert sysctl.read_text().strip() == "0"


def test_diagnose_existing_bpftool_failure_saves_stderr_and_restores_stats(tmp_path):
    sysctl = tmp_path / "bpf_stats_enabled"
    sysctl.write_text("0\n")
    bpftool = fake_bpftool(tmp_path, snapshots=[[]], fail=True, sysctl_path=sysctl)
    out = tmp_path / "failure.json"
    rc = po.main([
        "--diagnose-existing", "--program-id", "42", "--window-seconds", "0",
        "--execute", "--out", str(out), "--bpftool", str(bpftool),
        "--sysctl", str(sysctl), "--no-sudo",
    ])
    assert rc == 1
    data = json.loads(out.read_text())
    assert data["status"] == "error"
    assert "simulated bpftool failure" in data["stderr"]
    assert sysctl.read_text().strip() == "0"


def test_find_libssl_uses_explicit_path_and_rejects_missing(tmp_path):
    fake = tmp_path / "libssl.so.3"
    fake.write_bytes(b"not a real library")
    assert po.find_libssl(fake) == fake.resolve()
    with pytest.raises(ValueError, match="does not exist"):
        po.find_libssl(tmp_path / "missing-libssl.so.3")


def test_stats_gate_does_not_claim_restore_when_write_fails(tmp_path):
    path = tmp_path / "bpf_stats_enabled"
    path.write_text("0\n")
    gate = po.StatsGate(path)

    def fail_restore(value):
        raise OSError("restore denied")

    with pytest.raises(OSError, match="restore denied"):
        with gate:
            gate._write = fail_restore
    assert gate.restored is False
    assert path.read_text().strip() == "1"


@pytest.mark.parametrize("loss_counts", [None, (0, 0), (0, 3)])
def test_execute_measurement_gates_loop_selects_ids_and_uses_total_denominator(tmp_path, monkeypatch, loss_counts):
    sysctl = tmp_path / "bpf_stats_enabled"
    sysctl.write_text("0\n")
    old = program(7, "probe_SSL_read")
    snapshots = [
        [old],
        [old, program(8, "probe_SSL_rw_enter"), program(9, "probe_SSL_writ")],
        [program(8, "probe_SSL_rw_enter"), program(9, "probe_SSL_writ")],
        [program(8, "probe_SSL_rw_enter", run_cnt=10, run_time_ns=100),
         program(9, "probe_SSL_writ", run_cnt=10, run_time_ns=200)],
        [program(8, "probe_SSL_rw_enter", run_cnt=10, run_time_ns=100),
         program(9, "probe_SSL_writ", run_cnt=10, run_time_ns=200)],
        [program(8, "probe_SSL_rw_enter", run_cnt=20, run_time_ns=200),
         program(9, "probe_SSL_writ", run_cnt=20, run_time_ns=400)],
    ]
    links = [{"id": 100, "prog_id": 8, "type": "uprobe"},
             {"id": 101, "prog_id": 9, "type": "uprobe"}]
    bpftool = fake_bpftool(tmp_path, snapshots=snapshots, links=links,
                           sysctl_path=sysctl)
    collector = fake_collector(tmp_path / "collector")
    target = fake_target(tmp_path / "target")
    libssl = tmp_path / "libssl.so.3"
    libssl.write_bytes(b"\x7fELF")
    out = tmp_path / "measure.json"

    if loss_counts is not None:
        snapshots = iter(loss_counts * 2)
        monkeypatch.setattr(po, "snapshot_drop_maps", lambda *args, **kwargs: {
            "counts": {"21": next(snapshots)}, "complete": True, "errors": []})
    rc = po.main([
        "--collector", str(collector), "--target", str(target), "--out", str(out),
        "--libssl", str(libssl), "--bpftool", str(bpftool),
        "--sysctl", str(sysctl), "--no-sudo", "--execute", "--repeats", "2",
        "--iterations", "5", "--warmup", "1",
    ])
    assert rc == 0
    data = json.loads(out.read_text())
    assert data["status"] == ("degraded" if loss_counts == (0, 3) else "ok")
    assert data["schema"] == "mtagentlens.probe-overhead-measurement/1"
    assert data["attach"]["selected_program_ids"] == [8, 9]
    assert data["attach"]["before_program_ids"] == [7]
    assert len(data["repeats"]) == 2

    first = data["repeats"][0]
    assert first["target"]["ssl_operation_count"] == 20
    assert [row["run_cnt_delta"] for row in first["programs"]] == [10, 10]
    assert first["programs"][0]["ns_per_call"] == 10.0
    assert first["programs"][1]["ns_per_call"] == 20.0
    # (100 + 200) / 20 successful SSL operations, not sum of per-program means.
    assert first["totals"]["ns_per_successful_ssl_operation"] == 15.0
    assert data["summary"]["ns_per_successful_ssl_operation"]["mean"] == (
        15.0 if loss_counts == (0, 0) else None)
    assert data["summary"]["all_observed_ns_per_successful_ssl_operation"]["mean"] == 15.0
    assert first["capture_quality"]["quality"] == (
        "unknown" if loss_counts is None else "no_drops" if loss_counts == (0, 0) else "degraded")
    assert first["capture_quality"]["dropped_events_delta"] == (
        None if loss_counts is None else loss_counts[1]-loss_counts[0])
    assert sysctl.read_text().strip() == "0"
    assert (tmp_path / "bpftool-sysctl-seen").read_text().strip() == "1"
    assert Path(data["artifacts"]["collector_stderr"]).exists()
    assert Path(data["artifacts"]["target_stderr"]).exists()


def test_drop_map_parser_handles_raw_percpu_and_formatted_values():
    assert po.parse_drop_map(json.dumps([
        {"key": ["0x00"], "value": ["0x05", *["0x00"]*7]},
        {"key": ["0x01"], "values": [
            {"cpu": 0, "value": ["0x02", *["0x00"]*7]},
            {"cpu": 1, "value": ["0x03", *["0x00"]*7]}]},
    ])) == 10
    assert po.parse_drop_map(json.dumps([
        {"formatted": {"key": 0, "value": 7}},
        {"key": 1, "values": [{"cpu": 0, "value": 4}, {"cpu": 1, "value": 6}]},
    ])) == 17
    with pytest.raises(ValueError):
        po.parse_drop_map('[]')
    with pytest.raises(ValueError):
        po.parse_drop_map('[{"key":0,"value":null}]')


def test_drop_map_delta_preserves_unknown_and_degraded_samples():
    before = {"counts": {"21": 4}, "complete": True, "errors": []}
    after = {"counts": {"21": 7}, "complete": True, "errors": []}
    loss = po.drop_map_delta(before, after)
    assert loss["dropped_events_delta"] == 3
    assert loss["quality"] == "degraded"
    assert loss["eligible_for_loss_free_cost"] is False
    zero = po.drop_map_delta(before, before)
    assert zero["quality"] == "no_drops"
    assert zero["dropped_events_delta"] == 0
    assert zero["eligible_for_loss_free_cost"] is True
    missing = po.drop_map_delta({"counts": {}, "complete": False}, after)
    assert missing["quality"] == "unknown"
    assert missing["dropped_events_delta"] is None
    reset = po.drop_map_delta(after, before)
    assert reset["quality"] == "unknown"
    assert reset["dropped_events_delta"] is None
    partial = po.drop_map_delta(before, {**after, "complete": False})
    assert partial["quality"] == "degraded"
    assert partial["dropped_events_delta"] is None
    assert partial["observed_dropped_events_delta"] == 3


def test_drop_snapshot_reads_only_maps_owned_by_selected_programs(monkeypatch):
    calls = []
    def fake(tool, args, *, sudo):
        calls.append(args)
        if args[:3] == ["map", "show", "id"]:
            return json.dumps({"id": 21, "name": "dropped_events", "type": "array"})
        return '[{"key":0,"value":9}]'
    monkeypatch.setattr(po, "run_bpftool", fake)
    snapshot = po.snapshot_drop_maps("unused", [{"id": 8, "map_ids": [21]}], sudo=False)
    assert snapshot["counts"] == {"21": 9}
    assert snapshot["complete"] is True
    assert calls == [["map", "show", "id", "21", "--json"],
                     ["map", "dump", "id", "21", "--json"]]
    def failed(*args, **kwargs):
        raise po.ProbeError("map permission denied")
    monkeypatch.setattr(po, "run_bpftool", failed)
    unavailable = po.snapshot_drop_maps("unused", [{"id": 8, "map_ids": [21]}], sudo=False)
    assert unavailable["counts"] == {}
    assert unavailable["complete"] is False
    assert unavailable["errors"]
