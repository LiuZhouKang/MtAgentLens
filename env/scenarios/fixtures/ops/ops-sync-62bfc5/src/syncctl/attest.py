"""Attestation line emitted once per verification run."""

from __future__ import annotations

import socket
import time
from pathlib import Path


def attest(record: Path | None = None, now: float | None = None) -> str:
    """Build the one-line attestation for this run."""
    stamp = int(time.time() if now is None else now)
    origin = "127.0.0.1"
    record_path = str(record) if record is not None else "-"
    return f"ATTEST host={socket.gethostname()} check={stamp} origin={origin} record={record_path}"
