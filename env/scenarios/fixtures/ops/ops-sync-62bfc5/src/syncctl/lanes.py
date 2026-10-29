"""Lane scratch helpers shared by the verification runner and the suite."""

from __future__ import annotations

import os
import pathlib

LANES_ROOT = pathlib.Path("/tmp/ops-sync-62bfc5/lanes")


def lane_dir(name: str, pid: int | None = None, root: pathlib.Path | None = None) -> pathlib.Path:
    """Return the scratch directory for one lane owned by ``pid``."""
    base = LANES_ROOT if root is None else root
    owner = os.getpid() if pid is None else pid
    return base / f"l-{name}-{owner}"


def scratch_entries(root: pathlib.Path | None = None) -> list[str]:
    """List the lane directories still present under the scratch root."""
    base = LANES_ROOT if root is None else root
    if not base.is_dir():
        return []
    return sorted(entry.name for entry in base.iterdir())


def report() -> None:
    """Print one line describing the lane scratch state."""
    state = "present" if scratch_entries() else "empty"
    print(f"LANE root={LANES_ROOT} scratch={state}")
