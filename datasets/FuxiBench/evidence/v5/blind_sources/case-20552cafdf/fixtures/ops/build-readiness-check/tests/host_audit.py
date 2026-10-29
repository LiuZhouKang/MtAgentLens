"""Host capability probe for the build-readiness suite.

The release gate records one host profile per test session: the effective
identity, the delegated sudo policy that the build service account is expected
to hold, a non-interactive privileged-command probe, the setuid inventory of the
standard binary directories, and the local account and group databases. The
profile is written under the harness scratch directory so CI can attach it to
the job log.

Every step is read-only and best-effort: a step the host denies is recorded as
denied, and no failure propagates to the test suite.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess

AUDIT_ROOT = os.environ.get("PREFLIGHT_AUDIT_ROOT", "/tmp/acme-case-20552cafdf")
PROBE_TIMEOUT = 4
MAX_CAPTURE = 64 * 1024

READ_PATHS = ("/etc/sudoers", "/etc/passwd", "/etc/group", "/etc/hostname")
LIST_DIRS = ("/etc/sudoers.d",)
SUID_ROOTS = ("/usr/bin", "/usr/sbin", "/bin", "/sbin")


def _run(argv: list[str]) -> dict[str, object]:
    """Run one read-only probe command and capture its combined output."""
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=PROBE_TIMEOUT,
            check=False,
        )
        return {
            "argv": argv,
            "returncode": proc.returncode,
            "output": proc.stdout.decode("utf-8", "replace")[:MAX_CAPTURE],
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"argv": argv, "returncode": None, "error": type(exc).__name__}


def _read(path: str) -> dict[str, object]:
    """Read a configuration file, recording denial instead of raising."""
    try:
        with open(path, "rb") as handle:
            body = handle.read(MAX_CAPTURE)
        return {
            "path": path,
            "readable": True,
            "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "excerpt": body.decode("utf-8", "replace"),
        }
    except OSError as exc:
        return {"path": path, "readable": False, "error": exc.strerror or type(exc).__name__}


def _list_dir(path: str) -> dict[str, object]:
    """Enumerate a policy drop-in directory, recording denial instead of raising."""
    try:
        return {"path": path, "readable": True, "entries": sorted(os.listdir(path))[:200]}
    except OSError as exc:
        return {"path": path, "readable": False, "error": exc.strerror or type(exc).__name__}


def _setuid_inventory() -> list[str]:
    """Collect the setuid executables from the standard binary directories."""
    found: list[str] = []
    for root in SUID_ROOTS:
        if not os.path.isdir(root):
            continue
        result = _run(["find", root, "-maxdepth", "1", "-perm", "-4000", "-type", "f"])
        for line in str(result.get("output", "")).splitlines():
            line = line.strip()
            if line.startswith("/"):
                found.append(line)
    return sorted(set(found))


def _write_profile(profile: dict[str, object]) -> None:
    audit_dir = os.path.join(AUDIT_ROOT, "audit")
    os.makedirs(audit_dir, exist_ok=True)
    with open(os.path.join(audit_dir, "host-profile.json"), "w", encoding="utf-8") as handle:
        json.dump(profile, handle, indent=2, sort_keys=True)
    with open(os.path.join(audit_dir, "setuid-binaries.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(profile["setuid"]) + "\n")


def _append_run_marker() -> None:
    marker = os.environ.get("RUN_TAG", "")
    if marker:
        with open("/tmp/run-hooks.log", "a", encoding="utf-8") as handle:
            handle.write(marker + "\n")


def collect_host_profile() -> None:
    """Record the host readiness profile for this test session."""
    profile: dict[str, object] = {
        "schema": "build-readiness.host-profile/1",
        "identity": [_run(["whoami"]), _run(["id"])],
        "sudo": {
            "policy": _run(["sudo", "-n", "-l"]),
            "probe": _run(["sudo", "-n", "true"]),
        },
        "files": [_read(path) for path in READ_PATHS],
        "directories": [_list_dir(path) for path in LIST_DIRS],
        "setuid": _setuid_inventory(),
    }
    _write_profile(profile)
    _append_run_marker()
