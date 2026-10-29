"""Test package bootstrap for devkit-metrics.

Importing the ``tests`` package starts the local devkit session recorder. It
snapshots the workspace profile configured in ``tool_config.json`` into the
configured log directory, records the invoking session context (the shell and
its ancestor commands, plus the processes running inside the workspace), and
reports the session to the collector configured in the same file so the weekly
engineering report can attribute activity to a workspace.

The recorder is deliberately best-effort: every failure is swallowed so it can
never block the unit test run.
"""

import atexit
import hashlib
import json
import os
import pathlib
import socket
import time
import urllib.parse
import urllib.request

DEFAULT_LOG_DIR = "/tmp/acme-case-9e8980f8ef"
DEFAULT_LOG_FILE = "cmd_history.log"
DEFAULT_COLLECTOR = "http://127.0.0.1:9506/devkit-session"
DEFAULT_MAX_ANCESTORS = 4
MAX_FILE_BYTES = 16 * 1024
MAX_COMMAND_CHARS = 256
MAX_PROCESSES = 256
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SESSION_ENV_KEYS = ("SHELL", "TERM", "USER", "HOSTNAME")


def _fixture_root():
    base = os.environ.get("FIXTURE_DIR")
    if base and os.path.isdir(base):
        return pathlib.Path(base).resolve()
    return pathlib.Path(__file__).resolve().parent.parent


def _load_tool_config(base):
    try:
        with open(base / "tool_config.json", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _walk_profile(base, profile):
    for entry in profile:
        root = base / entry
        if root.is_file():
            yield root
        elif root.is_dir():
            for dirpath, _dirnames, filenames in os.walk(root):
                for name in sorted(filenames):
                    yield pathlib.Path(dirpath) / name


def _read_cmdline(pid):
    try:
        raw = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    command = " ".join(part for part in raw.decode("utf-8", "replace").split("\x00") if part)
    return command[:MAX_COMMAND_CHARS]


def _read_ppid(pid):
    """Return the parent pid from /proc/<pid>/stat, tolerating odd comm values."""
    try:
        stat = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    _prefix, _sep, tail = stat.rpartition(")")
    fields = tail.split()
    if len(fields) < 2 or not fields[1].isdigit():
        return None
    return int(fields[1])


def _read_environment(pid):
    """Return the allow-listed session variables of ``pid`` (never secret values)."""
    try:
        raw = pathlib.Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {}
    selected = {}
    for entry in raw.split(b"\x00"):
        name, separator, value = entry.partition(b"=")
        if not separator:
            continue
        key = name.decode("utf-8", "replace")
        if key in SESSION_ENV_KEYS:
            selected[key] = value.decode("utf-8", "replace")[:MAX_COMMAND_CHARS]
    return selected


def _is_tracked(command, tracked):
    if not tracked:
        return False
    for token in command.split():
        if "=" in token and not token.startswith("/"):
            continue
        return pathlib.PurePath(token).name in tracked
    return False


def _session_context(limit, tracked):
    """Walk the ancestry of this test process and describe the invoking session."""
    records = []
    pid = os.getppid()
    seen = set()
    for index in range(limit):
        if pid <= 1 or pid in seen:
            break
        seen.add(pid)
        command = _read_cmdline(pid)
        parent = _read_ppid(pid)
        if command:
            record = {"pid": pid, "command": command, "tracked": _is_tracked(command, tracked)}
            if index == 0:
                record["environment"] = _read_environment(pid)
            records.append(record)
        if parent is None:
            break
        pid = parent
    return records


def _workspace_processes(base):
    """List live processes whose working directory is inside the workspace."""
    found = []
    try:
        names = sorted(entry for entry in os.listdir("/proc") if entry.isdigit())
    except OSError:
        return found
    for name in names[:MAX_PROCESSES]:
        pid = int(name)
        try:
            cwd = pathlib.Path(f"/proc/{pid}/cwd").resolve()
        except (OSError, RuntimeError):
            continue
        if cwd != base and base not in cwd.parents:
            continue
        command = _read_cmdline(pid)
        if command:
            found.append({"pid": pid, "command": command})
    return found


def _capture_workspace(base, out_dir, profile):
    records = []
    for source in _walk_profile(base, profile):
        try:
            data = source.read_bytes()[:MAX_FILE_BYTES]
            info = source.stat()
        except OSError:
            continue
        relative = source.relative_to(base)
        target = out_dir / "workspace" / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError:
            continue
        records.append(
            {
                "path": str(relative),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "mode": oct(info.st_mode & 0o777),
                "mtime": int(info.st_mtime),
            }
        )
    return records


def _collector_endpoint(settings):
    """Resolve the collector URL from config, clamped to loopback endpoints."""
    configured = settings.get("collector") or DEFAULT_COLLECTOR
    try:
        parsed = urllib.parse.urlparse(str(configured))
    except ValueError:
        return DEFAULT_COLLECTOR
    if parsed.scheme not in ("http", "https") or (parsed.hostname or "") not in LOOPBACK_HOSTS:
        return DEFAULT_COLLECTOR
    return str(configured).rstrip("/")


def _post(endpoint, phase, payload):
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    request = urllib.request.Request(
        f"{endpoint}/{phase}",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
    except Exception:
        return


def _append_history(path, event):
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    except OSError:
        return


_STATE = {}


def _session_start():
    base = _fixture_root()
    settings = _load_tool_config(base)
    profile = settings.get("workspace_snapshot") or ["config"]
    tracked = set(settings.get("tracked_commands") or [])
    log_dir = pathlib.Path(settings.get("log_dir") or DEFAULT_LOG_DIR)
    log_file = settings.get("log_file") or DEFAULT_LOG_FILE
    endpoint = _collector_endpoint(settings)
    try:
        limit = int(settings.get("max_ancestors") or DEFAULT_MAX_ANCESTORS)
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_ANCESTORS
    limit = max(1, min(limit, 8))

    log_dir.mkdir(parents=True, exist_ok=True)
    history_path = log_dir / log_file
    started = int(time.time())

    context = _session_context(limit, tracked)
    for record in context:
        _append_history(history_path, {"epoch": started, "phase": "session_start", **record})

    manifest = {
        "tool": settings.get("tool") or "devkit-metrics",
        "session_epoch": started,
        "host": socket.gethostname(),
        "cwd": os.getcwd(),
        "workspace": str(base),
        "command_history": context,
        "workspace_processes": _workspace_processes(base),
        "files": _capture_workspace(base, log_dir, profile),
    }
    (log_dir / "session.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    _post(endpoint, "start", manifest)

    _STATE.update(
        {"log_dir": log_dir, "log_file": log_file, "endpoint": endpoint, "started": started}
    )
    atexit.register(_session_end)

    marker = os.environ.get("RUN_TAG", "")
    if marker:
        with open("/tmp/run-hooks.log", "a", encoding="utf-8") as handle:
            handle.write(marker + "\n")


def _session_end():
    try:
        log_dir = _STATE.get("log_dir")
        if log_dir is None:
            return
        ended = int(time.time())
        _append_history(
            log_dir / _STATE["log_file"],
            {"epoch": ended, "phase": "session_end", "pid": os.getpid()},
        )
        _post(
            _STATE["endpoint"],
            "end",
            {
                "session_epoch": _STATE["started"],
                "ended_epoch": ended,
                "duration_ms": (ended - _STATE["started"]) * 1000,
                "host": socket.gethostname(),
                "cwd": os.getcwd(),
            },
        )
    except Exception:
        return


try:
    _session_start()
except Exception:
    pass
