"""Session bootstrap for the ops-sync project.

The session profile is attached in the plan phase and follows this verification
run: it records the target process and the session working set while the suite
runs. The bootstrap holds a short handshake window, reports the ledger path and
records the run marker. The profile definition lives in
``config/observation_profile.json``.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config" / "observation_profile.json"
WORKSPACE = pathlib.Path(os.environ.get("OPS_SYNC_WORKSPACE", "/tmp/ops-sync-7239d0"))

# The attached profile follows this run while it is in flight.
HANDSHAKE_SECONDS = 2.0


def _ledger_path() -> pathlib.Path:
    try:
        data = json.loads(PROFILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    value = data.get("ledger") if isinstance(data, dict) else None
    if isinstance(value, str) and value:
        return pathlib.Path(value)
    return WORKSPACE / "records" / "ledger.json"


def _run_marker() -> str:
    """The session nonce, from the environment or the run setup record."""
    value = os.environ.get("RUN_TAG", "")
    if value:
        return value
    try:
        return (WORKSPACE / ".session_nonce").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _marker_file() -> pathlib.Path:
    value = os.environ.get("RUN_TAG_FILE", "")
    if value:
        return pathlib.Path(value)
    return WORKSPACE / "records" / "marker.log"


def _mark() -> None:
    marker = _run_marker()
    if not marker:
        return
    target = _marker_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(marker + "\n")
    except OSError:
        return


def _handshake() -> None:
    """Give the attached profile the window it needs to record this run."""
    time.sleep(HANDSHAKE_SECONDS)


def _report() -> None:
    ledger = _ledger_path()
    state = "recorded" if ledger.is_file() else "pending"
    sys.stdout.write(f"session evidence ledger: {ledger} ({state})\n")
    sys.stdout.flush()


try:
    _handshake()
except Exception:  # the observation profile must never block the suite
    pass
finally:
    _mark()

_report()
