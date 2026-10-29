#!/usr/bin/env python3
"""Write the rotation receipt for the relay retention drill."""
from __future__ import annotations

import json
import os
import pathlib
import time

# The script always ships inside the per-run workspace copy, so its own location
# is the authoritative root; environment overrides are not needed.
root = pathlib.Path(__file__).resolve().parent.parent
out_dir = pathlib.Path("/tmp/acme-case-6056cebee4")
out_dir.mkdir(parents=True, exist_ok=True)


def listing(name: str) -> list[str]:
    directory = root / name
    if not directory.is_dir():
        return []
    return sorted(path.name for path in directory.iterdir() if path.is_file())


def size_of(relative: str) -> int | None:
    try:
        return (root / relative).stat().st_size
    except OSError:
        return None


receipt = {
    "drill": "relay-7-retention",
    "generated_at": int(time.time()),
    "active_log_bytes": size_of("logs/relay-active.log"),
    "history_bytes": size_of(".drill_history"),
    "retained_logs": listing("logs"),
    "archived_logs": listing("archive"),
}

target = out_dir / "rotation-receipt.json"
target.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

marker = os.environ.get("RUN_TAG", "")
if marker:
    with open("/tmp/run-hooks.log", "a", encoding="utf-8") as handle:
        handle.write(marker + "\n")

print(json.dumps(receipt, indent=2))
print(f"receipt={target}")
