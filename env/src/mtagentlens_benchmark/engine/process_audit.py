"""Bounded native process lifecycle audit ledger."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import time
import uuid
from pathlib import Path

from .process_context import observe_process, observe_runner

SCHEMA = "mtagentlens.runner-event/v4"
CLOCK_DOMAIN = "runner-monotonic-ns"
# This fingerprints the native ledger and context sampler, not the full build.
PRODUCER = "process-audit/4@sha256:" + hashlib.sha256(
    Path(__file__).read_bytes() + Path(__file__).with_name("process_context.py").read_bytes()
).hexdigest()


def _identity(st: os.stat_result) -> tuple:
    """Stat fingerprint for changes observed during capture."""
    return (
        st.st_dev, st.st_ino, st.st_size,
        st.st_mtime_ns, st.st_ctime_ns, stat.S_IMODE(st.st_mode),
    )


def _reject_unsafe(path: Path, st: os.stat_result) -> None:
    if stat.S_ISLNK(st.st_mode):
        raise ValueError(f"symlink not allowed in launch inputs: {path}")
    if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
        raise ValueError(f"special file not allowed in launch inputs: {path}")


def _read_file(path: Path) -> bytes:
    # A path can be replaced after inventory; never follow a link or block on a FIFO.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"input changed to a non-regular file: {path}")
        data = stream.read()
        if _identity(os.fstat(stream.fileno())) != _identity(before):
            raise ValueError(f"file changed while reading: {path}")
        return data


def _capture_tree(root: Path) -> dict:
    """Snapshot regular files and directories without following links."""
    files: dict[str, dict] = {}
    directories: dict[str, int] = {}
    seen: dict[Path, tuple] = {}

    def visit(path: Path, relative: str) -> None:
        st = os.stat(path, follow_symlinks=False)
        _reject_unsafe(path, st)
        if not stat.S_ISDIR(st.st_mode):
            raise ValueError(f"not a directory: {path}")
        directories[relative] = stat.S_IMODE(st.st_mode)
        seen[path] = _identity(st)
        with os.scandir(path) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                child = path / entry.name
                child_relative = entry.name if relative == "." else f"{relative}/{entry.name}"
                child_stat = entry.stat(follow_symlinks=False)
                _reject_unsafe(child, child_stat)
                if stat.S_ISDIR(child_stat.st_mode):
                    visit(child, child_relative)
                    continue
                before = _identity(child_stat)
                data = _read_file(child)
                after = os.stat(child, follow_symlinks=False)
                if _identity(after) != before:
                    raise ValueError(f"file changed while reading: {child}")
                files[child_relative] = {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "content_base64": base64.b64encode(data).decode("ascii"),
                    "mode": stat.S_IMODE(after.st_mode),
                }
                seen[child] = _identity(after)

    visit(root, ".")
    for path, before in seen.items():
        if _identity(os.stat(path, follow_symlinks=False)) != before:
            raise ValueError(f"inventory changed during capture: {path}")
    return {"files": files, "directories": directories}


def capture_launch_inputs(
    prompt, claude_args, command, cwd, fixtures_dir,
) -> dict:
    """Snapshot prompt, argv and fixture bytes immediately before launch."""
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    for name, values in (("claude_args", claude_args), ("command", command)):
        if not isinstance(values, (list, tuple)) or not values or any(
                not isinstance(value, str) for value in values):
            raise ValueError(f"{name} must be a non-empty argv sequence")
    if len(claude_args) < 2 or claude_args[0] != "-p" or claude_args[1] != prompt:
        raise ValueError("prompt must match client argv")
    fixtures = None
    if fixtures_dir is not None:
        root = Path(fixtures_dir)
        try:
            st = os.stat(root, follow_symlinks=False)
        except FileNotFoundError:
            st = None
        if st is not None:
            _reject_unsafe(root, st)
            if not stat.S_ISDIR(st.st_mode):
                raise ValueError(f"fixtures is not a directory: {root}")
            fixtures = _capture_tree(root)
    snapshot = {
        "schema": "mtagentlens.launch-inputs/v1",
        "cwd": str(Path(cwd).resolve()),
        "prompt": prompt,
        "claude_args": list(claude_args),
        "command": list(command),
        "fixtures": fixtures,
    }
    payload = json.dumps(
        snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()
    return {
        "input_snapshot": snapshot,
        "input_sha256": hashlib.sha256(payload).hexdigest(),
    }


def process_key(pid: int) -> dict:
    """Compatibility view of a context-checked native observation."""
    return observe_process(pid)["process_key"]


class ProcessAudit:
    """Record launch-loop lifecycle in memory, then freeze it once."""

    def __init__(self, path, *, run_id: str, scenario_id: str, input_reference: dict | None = None):
        if any(not isinstance(value, str) or not value.strip() for value in (run_id, scenario_id)):
            raise ValueError("run_id and scenario_id must be non-empty strings")
        self.path = Path(path)
        self.run_id = run_id
        self.scenario_id = scenario_id
        self.input_reference = input_reference
        self.source_run_uuid = str(uuid.uuid4())
        self._records: list[dict] = []
        self._created = False

    def __enter__(self):
        if self.path.exists() or self.path.is_symlink():
            raise ValueError(f"refusing existing audit output: {self.path}")
        self.record("run_started", details={"input_reference": self.input_reference}
                    if self.input_reference is not None else {})
        return self

    def __exit__(self, exc_type, exc, tb):
        self.record("run_finished", details={
            "state": "completed" if exc_type is None else "interrupted",
        })
        # Serialize fully before touching the filesystem.
        payload = "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in self._records
        )
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        self._created = True
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
        except BaseException:
            if self._created:
                try:
                    self.path.unlink()
                except OSError:
                    pass
            raise
        return False

    def record(
        self, event_type: str, *, actor_id=None, process_key=None, details=None,
    ) -> None:
        self._records.append({
            "schema": SCHEMA,
            "producer": PRODUCER,
            "source_run_uuid": self.source_run_uuid,
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "event_id": uuid.uuid4().hex,
            "event_type": event_type,
            "timestamp_ns": time.monotonic_ns(),
            "clock_domain": CLOCK_DOMAIN,
            "actor_id": actor_id,
            "process_key": process_key,
            "details": details if details is not None else {},
        })
