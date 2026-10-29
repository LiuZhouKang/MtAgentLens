"""Native process identity observed through the local procfs sample."""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

SCHEMA = "mtagentlens.process-observation/v1"
RUNNER_SCHEMA = "mtagentlens.runner-context/v1"
NAMESPACE_NAMES = ("pid", "pid_for_children", "time", "time_for_children", "mnt")
OFFSET_NAMES = ("monotonic", "boottime")
_NSEC_PER_SEC = 1_000_000_000
_READ_LIMIT = 1 << 20


def _read_at(fd: int, name: str) -> str:
    """Read a small procfs file relative to an already-open directory."""
    child = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd
    )
    try:
        chunks = []
        total = 0
        while total < _READ_LIMIT:
            chunk = os.read(child, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total >= _READ_LIMIT:
            raise OSError("proc file exceeds observation read limit")
        return b"".join(chunks).decode("utf-8", "replace")
    finally:
        os.close(child)


def _unknown_namespaces() -> dict:
    return {name: None for name in NAMESPACE_NAMES}


def _unknown_offsets() -> dict:
    return {name: None for name in OFFSET_NAMES}


def _observer(procfs_pid=None, nspid=None, namespaces=None, offsets=None) -> dict:
    return {
        "pid": os.getpid(),
        "procfs_pid": procfs_pid,
        "nspid": nspid,
        "namespaces": _unknown_namespaces() if namespaces is None else namespaces,
        "time_offsets_ns": _unknown_offsets() if offsets is None else offsets,
    }


def _parse_stat(raw: str | None) -> tuple[int, int] | None:
    if not raw:
        return None
    line = raw.strip()
    lparen = line.find("(")
    rparen = line.rfind(")")
    if lparen <= 0 or rparen <= lparen:
        return None
    head = line[:lparen].split()
    tail = line[rparen + 1:].split()
    if len(head) != 1 or len(tail) < 20:
        return None
    try:
        pid, ticks = int(head[0], 10), int(tail[19], 10)
        return (pid, ticks) if pid > 0 and ticks >= 0 else None
    except ValueError:
        return None


def _parse_nspid(raw: str | None) -> list[int] | None:
    if raw is None:
        return None
    for line in raw.splitlines():
        if not line.startswith("NSpid:"):
            continue
        fields = line.split(":", 1)[1].split()
        if not fields:
            return None
        try:
            values = [int(field, 10) for field in fields]
        except ValueError:
            return None
        if any(value <= 0 for value in values):
            return None
        return values
    return None


def _read_namespaces(fd: int) -> dict:
    result = _unknown_namespaces()
    for name in NAMESPACE_NAMES:
        try:
            info = os.stat(f"ns/{name}", dir_fd=fd, follow_symlinks=True)
        except OSError:
            continue
        if info.st_dev >= 0 and info.st_ino > 0:
            result[name] = {
                "device": int(info.st_dev),
                "inode": int(info.st_ino),
            }
    return result


def _read_offsets(fd: int) -> dict:
    try:
        raw = _read_at(fd, "timens_offsets")
    except OSError:
        return _unknown_offsets()
    parsed = {}
    for line in raw.splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 3:
            return _unknown_offsets()
        clock, seconds_raw, nanoseconds_raw = fields
        if clock not in OFFSET_NAMES:
            continue
        try:
            seconds = int(seconds_raw, 10)
            nanoseconds = int(nanoseconds_raw, 10)
        except ValueError:
            return _unknown_offsets()
        if not 0 <= nanoseconds < _NSEC_PER_SEC:
            return _unknown_offsets()
        parsed[clock] = seconds * _NSEC_PER_SEC + nanoseconds
    return {name: parsed.get(name) for name in OFFSET_NAMES}


def _read_boot_id(proc_root: Path) -> str | None:
    try:
        raw = (proc_root / "sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        )
        return str(uuid.UUID(raw.strip()))
    except (OSError, ValueError):
        return None


def _boottime_ns() -> int | None:
    getter = getattr(time, "clock_gettime_ns", None)
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    if getter is None or clock is None:
        return None
    try:
        value = getter(clock)
    except (OSError, ValueError):
        return None
    return value if type(value) is int else None


def _sample_observer(proc_root: Path) -> tuple[str, dict]:
    self_path = proc_root / "self"
    try:
        procfs_pid = int(os.readlink(self_path).strip(), 10)
    except (OSError, ValueError):
        return "unavailable", _observer()
    if procfs_pid != os.getpid():
        return "pid_space_mismatch", _observer(procfs_pid=procfs_pid)

    try:
        fd = os.open(self_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        return "unavailable", _observer(procfs_pid=procfs_pid)
    try:
        try:
            status = _read_at(fd, "status")
        except OSError:
            return "unavailable", _observer(procfs_pid=procfs_pid)
        nspid = _parse_nspid(status)
        if nspid is None:
            return "unavailable", _observer(procfs_pid=procfs_pid)
        if nspid != [os.getpid()]:
            return "pid_space_mismatch", _observer(
                procfs_pid=procfs_pid, nspid=nspid
            )
        return "ok", _observer(
            procfs_pid=procfs_pid,
            nspid=nspid,
            namespaces=_read_namespaces(fd),
            offsets=_read_offsets(fd),
        )
    finally:
        os.close(fd)


def _sample_target(pid: int, proc_root: Path) -> tuple[dict | None, str, int | None]:
    path = proc_root / str(pid)
    try:
        fd = os.open(
            path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
    except OSError:
        return None, "unavailable", None
    try:
        try:
            stat_before_raw = _read_at(fd, "stat")
        except OSError:
            stat_before_raw = None
        try:
            status_raw = _read_at(fd, "status")
        except OSError:
            status_raw = None
        nspid = _parse_nspid(status_raw)
        namespaces = _read_namespaces(fd)
        offsets = _read_offsets(fd)
        try:
            stat_after_raw = _read_at(fd, "stat")
        except OSError:
            stat_after_raw = None
    finally:
        os.close(fd)

    before = _parse_stat(stat_before_raw)
    after = _parse_stat(stat_after_raw)
    target = {
        "procfs_pid": pid,
        "start_ticks_before": None if before is None else before[1],
        "start_ticks_after": None if after is None else after[1],
        "nspid": nspid,
        "namespaces": namespaces,
        "time_offsets_ns": offsets,
    }
    if before is None or after is None:
        return target, "unavailable", None
    if before[0] != pid or after[0] != pid or before[1] != after[1]:
        return target, "unstable", None
    if before[1] <= 0:
        return target, "unstable", None
    if nspid is None:
        return target, "unavailable", None
    if nspid[0] != pid:
        return target, "unstable", None
    return target, "observed", before[1]


def observe_process(pid: int, *, proc_root: Path = Path("/proc")) -> dict:
    """Observe one process only after proving the observer PID space."""
    if isinstance(pid, bool) or not isinstance(pid, int) or not 1 <= pid <= 0x7FFFFFFF:
        raise ValueError("pid must be a positive integer")
    root = Path(proc_root)
    sample_before_ns = time.monotonic_ns()
    process_key = {
        "pid": pid,
        "start_ticks": None,
        "boot_id": _read_boot_id(root),
        "ticks_per_second": int(os.sysconf("SC_CLK_TCK")),
    }

    try:
        status, observer = _sample_observer(root)
    except OSError:
        status, observer = "unavailable", _observer()

    target = None
    if status == "ok":
        try:
            target, status, start_ticks = _sample_target(pid, root)
        except OSError:
            target, status, start_ticks = None, "unavailable", None
        if status == "observed":
            process_key["start_ticks"] = start_ticks

    boottime_ns = _boottime_ns()
    observation = {
        "schema": SCHEMA,
        "sample_before_ns": sample_before_ns,
        "sample_after_ns": time.monotonic_ns(),
        "boottime_ns": boottime_ns,
        "status": status,
        "observer": observer,
        "target": target,
    }
    return {"process_key": process_key, "observation": observation}


def observe_runner(*, proc_root: Path = Path("/proc")) -> dict:
    """Sample the runner's own clock and namespace context before a launch.

    Two such samples (before the launch attempt and after the child starts) can
    only show that the endpoints agree; they cannot prove the runner never
    changed namespace in between.
    """
    root = Path(proc_root)
    sample_before_ns = time.monotonic_ns()
    try:
        status, observer = _sample_observer(root)
    except OSError:
        status, observer = "unavailable", _observer()
    boottime_ns = _boottime_ns()
    boot_id = _read_boot_id(root)
    return {
        "schema": RUNNER_SCHEMA,
        "sample_before_ns": sample_before_ns,
        "sample_after_ns": time.monotonic_ns(),
        "boottime_ns": boottime_ns,
        "boot_id": boot_id,
        "status": "observed" if status == "ok" else status,
        "observer": observer,
    }
