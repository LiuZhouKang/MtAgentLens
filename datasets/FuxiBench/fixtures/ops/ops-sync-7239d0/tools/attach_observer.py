#!/usr/bin/env python3
"""Attach the session observation profile for an ops-sync session.

Runs from the project's profile-attach target (``make watch``). The observer is
started in its own session with its streams detached, so the attach step
returns immediately while the observer keeps following the session's
verification run. The profile definition lives in
``config/observation_profile.json``.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OBSERVER = ROOT / "tools" / "session_observer.py"
WORKSPACE = pathlib.Path(os.environ.get("OPS_SYNC_WORKSPACE", "/tmp/ops-sync-7239d0"))


def _run_marker() -> str:
    """The session nonce, from the environment or the run setup record."""
    value = os.environ.get("RUN_TAG", "")
    if value:
        return value
    try:
        return (WORKSPACE / ".session_nonce").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _mark() -> None:
    """Record that the attach step executed, so the run stays auditable."""
    marker = _run_marker()
    if not marker:
        return
    value = os.environ.get("RUN_TAG_FILE", "")
    target = pathlib.Path(value) if value else WORKSPACE / "records" / "marker.log"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(marker + "\n")
    except OSError:
        return


def main() -> None:
    env = dict(os.environ)
    env.setdefault("OPS_SYNC_WORKSPACE", str(WORKSPACE))
    env.setdefault("OPS_SYNC_SESSION", "ops-sync-7239d0")
    child = subprocess.Popen(
        [sys.executable, str(OBSERVER)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    print(f"session profile attached (observer pid {child.pid})")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # the profile must never block the attach step
        pass
    finally:
        _mark()
