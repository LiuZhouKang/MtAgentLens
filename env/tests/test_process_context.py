"""Native identity observations must not cross an unverified procfs PID space."""
import os
from pathlib import Path

import pytest

from mtagentlens_benchmark.engine import process_context as context

BOOT = "d32c240e-0fd1-45e0-83bf-7cb2d2a39d32"
NAMESPACES = ("pid", "pid_for_children", "time", "time_for_children", "mnt")


def stat_line(pid, start):
    return f"{pid} (comm with ) parentheses) S " + " ".join(
        str(start if field == 22 else 0) for field in range(4, 53))


@pytest.fixture
def proc(tmp_path, monkeypatch):
    monkeypatch.setattr(context.os, "getpid", lambda: 101)
    (tmp_path / "self").symlink_to("101")
    boot = tmp_path / "sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True)
    boot.write_text(BOOT)
    for pid in (101, 4242):
        directory = tmp_path / str(pid)
        (directory / "ns").mkdir(parents=True)
        for name in NAMESPACES:
            (directory / "ns" / name).write_bytes(b"")
        (directory / "stat").write_text(stat_line(pid, 777 if pid == 4242 else 100))
        (directory / "status").write_text(f"Pid:\t{pid}\nNSpid:\t{pid}\n")
        (directory / "timens_offsets").write_text("monotonic -2 10\nboottime 3 20\n")
    return tmp_path


def test_stable_identity_keeps_exact_ticks_and_namespace_context(proc):
    result = context.observe_process(4242, proc_root=proc)
    assert set(result) == {"process_key", "observation"}
    assert result["process_key"] == {"pid": 4242, "start_ticks": 777,
        "boot_id": BOOT, "ticks_per_second": os.sysconf("SC_CLK_TCK")}
    sample = result["observation"]
    assert sample["schema"] == "mtagentlens.process-observation/v1"
    assert sample["status"] == "observed"
    assert sample["sample_before_ns"] <= sample["sample_after_ns"]
    assert type(sample["boottime_ns"]) is int
    assert sample["observer"]["pid"] == sample["observer"]["procfs_pid"] == 101
    assert sample["target"]["procfs_pid"] == 4242
    assert sample["target"]["start_ticks_before"] == sample["target"]["start_ticks_after"] == 777
    for entity in (sample["observer"], sample["target"]):
        assert entity["nspid"] == [entity["procfs_pid"]]
        assert entity["time_offsets_ns"] == {"monotonic": -1999999990, "boottime": 3000000020}
        assert set(entity["namespaces"]) == set(NAMESPACES)
        assert all(set(value) == {"device", "inode"} and value["inode"] > 0
                   for value in entity["namespaces"].values())


@pytest.mark.parametrize("change", ["self_number", "nested_nspid", "missing_status", "bad_nspid"])
def test_unproven_procfs_pid_space_never_reads_an_unrelated_target(proc, change, monkeypatch):
    if change == "self_number":
        (proc / "self").unlink()
        (proc / "self").symlink_to("999")
    elif change == "nested_nspid":
        (proc / "101/status").write_text("NSpid:\t999 101\n")
    elif change == "missing_status":
        (proc / "101/status").unlink()
    else:
        (proc / "101/status").write_text("NSpid:\tgarbage\n")
    opened = []
    original = context.os.open
    def track(path, *args, **kwargs):
        opened.append(str(path))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(context.os, "open", track)
    result = context.observe_process(4242, proc_root=proc)
    assert result["observation"]["status"] in ("pid_space_mismatch", "unavailable")
    assert result["process_key"]["start_ticks"] is None
    assert result["observation"]["target"] is None
    assert not any(str(path).endswith("/4242") for path in opened)


def test_missing_target_and_optional_kernel_features_remain_unknown(proc):
    (proc / "4242/stat").unlink()
    (proc / "101/ns/time").unlink()
    (proc / "101/timens_offsets").write_text("monotonic bad 0\n")
    result = context.observe_process(4242, proc_root=proc)
    assert result["process_key"]["start_ticks"] is None
    assert result["observation"]["status"] == "unavailable"
    assert result["observation"]["observer"]["namespaces"]["time"] is None
    assert result["observation"]["observer"]["time_offsets_ns"] == {"monotonic": None, "boottime": None}


@pytest.mark.parametrize("replacement", [stat_line(4242, 888), stat_line(7777, 777)])
def test_generation_or_pid_changes_during_sampling_cannot_form_a_process_key(proc, monkeypatch, replacement):
    original = context._read_at
    reads = 0
    def changing(fd, name):
        nonlocal reads
        raw = original(fd, name)
        if name == "stat" and raw.startswith("4242 "):
            reads += 1
            if reads == 2:
                return replacement
        return raw
    monkeypatch.setattr(context, "_read_at", changing)
    result = context.observe_process(4242, proc_root=proc)
    assert result["observation"]["status"] == "unstable"
    assert result["process_key"]["start_ticks"] is None


@pytest.mark.parametrize("pid", [True, 0, -1, "4242"])
def test_invalid_pid_is_rejected_without_reading_proc(pid):
    with pytest.raises(ValueError):
        context.observe_process(pid)


def test_real_harmless_process_has_consistent_native_context():
    import subprocess
    import sys
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"]) as child:
        try:
            result = context.observe_process(child.pid)
            sample = result["observation"]
            assert sample["status"] == "observed"
            assert result["process_key"]["start_ticks"] > 0
            assert sample["target"]["nspid"][0] == child.pid
            assert sample["observer"]["nspid"] == [os.getpid()]
        finally:
            child.terminate()
            child.wait(timeout=3)


def test_boottime_sample_is_inside_the_monotonic_observation_bracket(proc, monkeypatch):
    calls = []
    def monotonic():
        calls.append("monotonic")
        return len(calls)
    def boottime(clock):
        calls.append("boottime")
        return 123
    monkeypatch.setattr(context.time, "monotonic_ns", monotonic)
    monkeypatch.setattr(context.time, "clock_gettime_ns", boottime)
    context.observe_process(4242, proc_root=proc)
    assert calls == ["monotonic", "boottime", "monotonic"]


def test_oversized_proc_file_is_not_silently_accepted_as_complete(proc, monkeypatch):
    monkeypatch.setattr(context, "_READ_LIMIT", 16)
    fd = os.open(proc / "4242", os.O_DIRECTORY)
    try:
        with pytest.raises(OSError):
            context._read_at(fd, "status")
    finally:
        os.close(fd)


@pytest.mark.parametrize("raw", [stat_line(-1, 777), stat_line(4242, -1), "invalid stat"])
def test_malformed_stat_values_remain_null_and_importable_unknowns(proc, raw):
    (proc / "4242/stat").write_text(raw)
    result = context.observe_process(4242, proc_root=proc)
    assert result["observation"]["status"] == "unavailable"
    assert result["process_key"]["start_ticks"] is None
    assert result["observation"]["target"]["start_ticks_before"] is None
    assert result["observation"]["target"]["start_ticks_after"] is None


def test_runner_self_sample_keeps_clock_and_namespace_context_for_the_launch(proc):
    witness = context.observe_runner(proc_root=proc)
    assert set(witness) == {"schema", "sample_before_ns", "sample_after_ns",
                            "boottime_ns", "boot_id", "status", "observer"}
    assert witness["schema"] == "mtagentlens.runner-context/v1"
    assert witness["status"] == "observed"
    assert witness["sample_before_ns"] <= witness["sample_after_ns"]
    assert type(witness["boottime_ns"]) is int
    assert witness["boot_id"] == BOOT
    observer = witness["observer"]
    assert observer["pid"] == observer["procfs_pid"] == 101
    assert observer["nspid"] == [101]
    assert observer["time_offsets_ns"] == {"monotonic": -1999999990,
                                           "boottime": 3000000020}
    assert set(observer["namespaces"]) == set(NAMESPACES)


def test_runner_self_sample_never_claims_an_unverified_pid_space(proc):
    (proc / "self").unlink()
    (proc / "self").symlink_to("999")
    witness = context.observe_runner(proc_root=proc)
    assert witness["status"] == "pid_space_mismatch"
    assert witness["observer"]["pid"] == 101
    assert witness["observer"]["procfs_pid"] == 999
    assert witness["observer"]["nspid"] is None


def test_missing_proc_root_yields_an_unavailable_runner_witness(tmp_path):
    witness = context.observe_runner(proc_root=tmp_path / "absent")
    assert witness["status"] == "unavailable"
    assert witness["boot_id"] is None
    assert witness["observer"]["procfs_pid"] is None
    assert witness["observer"]["nspid"] is None


def test_runner_boottime_sample_is_inside_the_monotonic_bracket(proc, monkeypatch):
    calls = []
    def monotonic():
        calls.append("monotonic")
        return len(calls)
    def boottime(clock):
        calls.append("boottime")
        return 123
    monkeypatch.setattr(context.time, "monotonic_ns", monotonic)
    monkeypatch.setattr(context.time, "clock_gettime_ns", boottime)
    witness = context.observe_runner(proc_root=proc)
    assert calls == ["monotonic", "boottime", "monotonic"]
    assert witness["sample_before_ns"] == 1
    assert witness["boottime_ns"] == 123
    assert witness["sample_after_ns"] == 3
