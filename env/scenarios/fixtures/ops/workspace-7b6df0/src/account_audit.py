#!/usr/bin/env python3
"""Inventory human accounts from the world-readable account databases.

Quarterly access review helper: parses passwd(5) and group(5) input, keeps the
accounts whose uid falls inside the configured review range, resolves each
account's primary group and marks shells that cannot start an interactive
session. Password material is never read.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Account:
    account: str
    uid: int
    gid: int
    gecos: str
    home: str
    shell: str
    primary_group: str | None
    interactive: bool


def parse_passwd(text: str) -> list[dict]:
    """Parse passwd(5) text into account records with integer uid/gid."""
    records: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(":")
        if len(fields) < 7:
            continue
        name, _password, uid, gid, gecos, home, shell = fields[:7]
        try:
            records.append(
                {
                    "account": name,
                    "uid": int(uid),
                    "gid": int(gid),
                    "gecos": gecos,
                    "home": home,
                    "shell": shell or "/bin/sh",
                }
            )
        except ValueError:
            continue
    return records


def parse_group(text: str) -> dict[int, str]:
    """Map gid to the first group name that declares it."""
    groups: dict[int, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(":")
        if len(fields) < 3:
            continue
        name, _password, gid = fields[:3]
        try:
            groups.setdefault(int(gid), name)
        except ValueError:
            continue
    return groups


def parse_scope(text: str) -> dict:
    """Parse the flat review-scope YAML (scalars plus one string list)."""
    scope: dict = {"uid_min": 1000, "uid_max": 60000, "non_interactive_shells": []}
    active_list: list[str] | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if active_list is not None:
                active_list.append(stripped[2:].strip().strip("'\""))
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key in ("uid_min", "uid_max"):
            try:
                scope[key] = int(value)
            except ValueError:
                pass
            active_list = None
        elif key == "non_interactive_shells":
            active_list = scope["non_interactive_shells"]
            if value:
                active_list.append(value)
        else:
            active_list = None
    return scope


def build_accounts(
    passwd_records: list[dict], groups: dict[int, str], scope: dict
) -> tuple[list[Account], int]:
    """Return in-range accounts sorted by uid, plus the count skipped."""
    low, high = scope["uid_min"], scope["uid_max"]
    non_interactive = set(scope["non_interactive_shells"])
    accounts: list[Account] = []
    skipped = 0
    for record in passwd_records:
        if not low <= record["uid"] <= high:
            skipped += 1
            continue
        accounts.append(
            Account(
                account=record["account"],
                uid=record["uid"],
                gid=record["gid"],
                gecos=record["gecos"],
                home=record["home"],
                shell=record["shell"],
                primary_group=groups.get(record["gid"]),
                interactive=record["shell"] not in non_interactive,
            )
        )
    accounts.sort(key=lambda account: account.uid)
    return accounts, skipped


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--passwd", type=Path, required=True)
    parser.add_argument("--group", type=Path, required=True)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    scope = parse_scope(read_text(args.scope))
    records = parse_passwd(read_text(args.passwd))
    groups = parse_group(read_text(args.group))
    accounts, skipped = build_accounts(records, groups, scope)

    inventory = {
        "generated_from": {"passwd": str(args.passwd), "group": str(args.group)},
        "scope": scope,
        "counts": {
            "total_records": len(records),
            "human_accounts": len(accounts),
            "skipped_out_of_range": skipped,
            "missing_primary_group": sum(
                1 for account in accounts if account.primary_group is None
            ),
        },
        "accounts": [asdict(account) for account in accounts],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(accounts)} human accounts to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
