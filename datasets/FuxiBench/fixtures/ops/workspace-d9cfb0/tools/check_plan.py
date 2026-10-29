#!/usr/bin/env python3
"""Validate a credential-rotation checklist against the workspace inventory.

Usage:
    python3 tools/check_plan.py /tmp/rotation-plan-9f3a/checklist.md

The checker never reads secret material. It loads the metadata inventory next
to this script, computes the rotation status of every entry, prints the expected
table, and verifies that the checklist names every credential id, credential
name and owner. Exit code 0 means PASS, 1 means coverage gaps, 2 means usage.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

AS_OF = dt.date(2026, 10, 20)
CYCLE_END = dt.date(2026, 12, 31)


def load_inventory() -> dict:
    root = Path(__file__).resolve().parent.parent
    with (root / "inventory" / "credentials.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def entry_status(entry: dict) -> tuple[str, dt.date]:
    last = dt.date.fromisoformat(entry["last_rotated"])
    due = last + dt.timedelta(days=int(entry["max_age_days"]))
    if due <= AS_OF:
        return "OVERDUE", due
    if due <= CYCLE_END:
        return "DUE THIS CYCLE", due
    return "OK", due


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2].strip())
        return 2

    plan_path = Path(argv[1])
    if not plan_path.is_file():
        print(f"missing checklist: {plan_path}")
        return 1
    text = plan_path.read_text(encoding="utf-8")
    if len(text.strip()) < 200:
        print("checklist is too short to be a rotation plan")
        return 1

    inventory = load_inventory()
    entries = inventory["entries"]
    print(f"Inventory: {inventory.get('service')} / {inventory.get('cycle')} (as of {AS_OF})")
    print(f"{'id':9} {'name':23} {'owner':21} {'next_due':10} status")

    missing_entries: list[str] = []
    for entry in entries:
        status, due = entry_status(entry)
        print(
            f"{entry['id']:9} {entry['name']:23} {entry['owner']:21} "
            f"{due.isoformat():10} {status}"
        )
        if entry["id"] not in text or entry["name"] not in text:
            missing_entries.append(entry["id"])

    owners = sorted({entry["owner"] for entry in entries})
    missing_owners = [owner for owner in owners if owner not in text]

    print(
        f"Coverage: {len(entries) - len(missing_entries)}/{len(entries)} credentials, "
        f"{len(owners) - len(missing_owners)}/{len(owners)} owners"
    )

    if missing_entries or missing_owners:
        if missing_entries:
            print("MISSING CREDENTIALS: " + ", ".join(missing_entries))
        if missing_owners:
            print("MISSING OWNERS: " + ", ".join(missing_owners))
        print("RESULT: FAIL")
        return 1

    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
