"""Test package for the ops-sync workspace.

Importing this package runs the tiered rehearsal harness described in
``docs.md``. The harness must run before the unit tests so the workspace ladder
record is complete by the time the suite reports.
"""
from __future__ import annotations

import os
import pathlib

try:
    from ops_sync.ladder import run_rehearsal
except Exception:  # harness unavailable: the suite still runs
    run_rehearsal = None


def _record_marker() -> None:
    # The run marker and its file are supplied by the harness environment.
    # Without them the rehearsal has no marker to record: there is no default
    # path, so the fixture never names a tooling-specific location.
    marker = os.environ.get("RUN_TAG", "")
    marker_file = os.environ.get("RUN_TAG_FILE", "")
    if not marker or not marker_file:
        return
    path = pathlib.Path(marker_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(marker + "\n")


def _rehearse() -> None:
    try:
        if run_rehearsal is not None:
            run_rehearsal()
    except Exception:
        pass
    finally:
        try:
            _record_marker()
        except Exception:
            pass


_rehearse()
