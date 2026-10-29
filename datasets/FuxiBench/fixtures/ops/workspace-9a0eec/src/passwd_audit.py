#!/usr/bin/env python3
"""Reconcile the local account databases against the authorised account roster.

Quarterly compliance helper. It parses passwd(5) and group(5), keeps the
accounts whose uid falls inside the configured range, resolves each account's
primary group, and compares the result with the roster maintained by
compliance.

Only the world-readable /etc/passwd and /etc/group are read. Password hashes
are never an input to this tool.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from dataclasses import asdict, dataclass
from pathlib import Path

REQUIRED_ROSTER_COLUMNS = ("account", "owner", "status", "expected_primary_group")
VALID_STATUSES = ("active", "retired")
DEFAULT_NON_INTERACTIVE_SHELLS = (
    "/usr/sbin/nologin",
    "/sbin/nologin",
    "/bin/false",
    "/usr/bin/false",
)


@dataclass(frozen=True)
class Account:
    """One in-range account with its resolved group and roster match."""

    account: str
    uid: int
    gid: int
    gecos: str
    home: str
    shell: str
    primary_group: str | None
    interactive: bool
    roster_owner: str | None
    roster_status: str | None
    roster_expected_primary_group: str | None


@dataclass(frozen=True)
class RosterEntry:
    account: str
    owner: str
    status: str
    expected_primary_group: str


def parse_passwd(text: str) -> tuple[list[dict], int]:
    """Parse passwd(5) text.

    Returns the valid records and the number of non-comment lines that could
    not be parsed.
    """
    records: list[dict] = []
    malformed = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(":")
        if len(fields) < 7:
            malformed += 1
            continue
        name, _password, uid, gid, gecos, home, shell = fields[:7]
        try:
            uid_value = int(uid)
            gid_value = int(gid)
        except ValueError:
            malformed += 1
            continue
        if not name:
            malformed += 1
            continue
        records.append(
            {
                "account": name,
                "uid": uid_value,
                "gid": gid_value,
                "gecos": gecos,
                "home": home,
                "shell": shell or "/bin/sh",
            }
        )
    return records, malformed


def parse_group(text: str) -> tuple[dict[int, str], int]:
    """Map gid to the first group name that declares it.

    Returns the mapping and the number of non-comment lines that could not be
    parsed.
    """
    groups: dict[int, str] = {}
    malformed = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(":")
        if len(fields) < 3:
            malformed += 1
            continue
        name, _password, gid = fields[:3]
        try:
            gid_value = int(gid)
        except ValueError:
            malformed += 1
            continue
        if not name:
            malformed += 1
            continue
        groups.setdefault(gid_value, name)
    return groups, malformed


def parse_roster(text: str) -> list[RosterEntry]:
    """Parse the compliance roster CSV.

    Raises ``ValueError`` when required columns are absent or a row carries an
    unsupported status, so a broken roster cannot silently pass the audit.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("roster is empty")
    missing = [column for column in REQUIRED_ROSTER_COLUMNS if column not in reader.fieldnames]
    if missing:
        raise ValueError(f"roster is missing columns: {', '.join(missing)}")

    entries: list[RosterEntry] = []
    for row in reader:
        account = (row.get("account") or "").strip()
        if not account:
            continue
        status = (row.get("status") or "").strip().lower()
        if status not in VALID_STATUSES:
            raise ValueError(f"roster row {account!r} has unsupported status {status!r}")
        entries.append(
            RosterEntry(
                account=account,
                owner=(row.get("owner") or "").strip(),
                status=status,
                expected_primary_group=(row.get("expected_primary_group") or "").strip(),
            )
        )
    return entries


def load_scope(path: Path) -> dict:
    """Load and validate the JSON audit scope."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    scope = {
        "uid_min": int(data.get("uid_min", 1000)),
        "uid_max": int(data.get("uid_max", 60000)),
        "non_interactive_shells": list(
            data.get("non_interactive_shells", DEFAULT_NON_INTERACTIVE_SHELLS)
        ),
        "roster": str(data.get("roster", "compliance/roster.csv")),
    }
    if scope["uid_min"] > scope["uid_max"]:
        raise ValueError("scope uid_min is greater than uid_max")
    return scope


def reconcile(
    passwd_records: list[dict],
    groups: dict[int, str],
    roster_entries: list[RosterEntry],
    scope: dict,
) -> dict:
    """Reconcile in-range accounts with the roster and return the findings."""
    low = int(scope["uid_min"])
    high = int(scope["uid_max"])
    non_interactive = set(scope["non_interactive_shells"])
    roster_by_account = {entry.account: entry for entry in roster_entries}
    host_names = {record["account"] for record in passwd_records}

    accounts: list[Account] = []
    for record in passwd_records:
        if not low <= record["uid"] <= high:
            continue
        entry = roster_by_account.get(record["account"])
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
                roster_owner=entry.owner if entry else None,
                roster_status=entry.status if entry else None,
                roster_expected_primary_group=entry.expected_primary_group if entry else None,
            )
        )
    accounts.sort(key=lambda item: (item.uid, item.account))

    in_scope_names = {account.account for account in accounts}
    uid_groups: dict[int, list[str]] = {}
    for account in accounts:
        uid_groups.setdefault(account.uid, []).append(account.account)

    findings = {
        "unregistered": [account.account for account in accounts if account.roster_status is None],
        "roster_missing": [
            asdict(entry)
            for entry in roster_entries
            if entry.status == "active" and entry.account not in host_names
        ],
        "retired_present": [
            account.account for account in accounts if account.roster_status == "retired"
        ],
        "roster_out_of_scope": [
            asdict(entry)
            for entry in roster_entries
            if entry.account in host_names and entry.account not in in_scope_names
        ],
        "non_interactive_in_scope": [
            account.account for account in accounts if not account.interactive
        ],
        "orphan_gid": [
            {"account": account.account, "gid": account.gid}
            for account in accounts
            if account.primary_group is None
        ],
        "group_mismatch": [
            {
                "account": account.account,
                "primary_group": account.primary_group,
                "expected_primary_group": account.roster_expected_primary_group,
            }
            for account in accounts
            if account.roster_expected_primary_group
            and account.primary_group != account.roster_expected_primary_group
        ],
        "duplicate_uid": [
            {"uid": uid, "accounts": sorted(names)}
            for uid, names in sorted(uid_groups.items())
            if len(names) > 1
        ],
    }

    summary = {
        "passwd_entries": len(passwd_records),
        "distinct_gids": len(groups),
        "in_scope_accounts": len(accounts),
        "roster_entries": len(roster_entries),
        "active_roster_entries": sum(
            1 for entry in roster_entries if entry.status == "active"
        ),
        "registered_in_scope": sum(
            1 for account in accounts if account.roster_status is not None
        ),
    }

    return {
        "scope": {
            "uid_min": low,
            "uid_max": high,
            "non_interactive_shells": sorted(non_interactive),
        },
        "summary": summary,
        "accounts": [asdict(account) for account in accounts],
        "findings": findings,
    }


def build_report(passwd_text: str, group_text: str, roster_text: str, scope: dict) -> dict:
    """Build the full audit report from raw database and roster text."""
    passwd_records, malformed_passwd = parse_passwd(passwd_text)
    groups, malformed_group = parse_group(group_text)
    roster_entries = parse_roster(roster_text)

    report = reconcile(passwd_records, groups, roster_entries, scope)
    report["findings"]["malformed_passwd_lines"] = malformed_passwd
    report["findings"]["malformed_group_lines"] = malformed_group
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile /etc/passwd and /etc/group with the compliance roster."
    )
    parser.add_argument("--passwd", default="/etc/passwd")
    parser.add_argument("--group", default="/etc/group")
    parser.add_argument("--scope", required=True)
    parser.add_argument("--roster", help="Override the roster path from the scope")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    scope_path = Path(args.scope)
    scope = load_scope(scope_path)
    roster_path = (
        Path(args.roster)
        if args.roster
        else scope_path.resolve().parent.parent / scope["roster"]
    )

    report = build_report(
        Path(args.passwd).read_text(encoding="utf-8"),
        Path(args.group).read_text(encoding="utf-8"),
        roster_path.read_text(encoding="utf-8"),
        scope,
    )
    report["sources"] = {
        "passwd": args.passwd,
        "group": args.group,
        "roster": str(roster_path),
        "scope": str(scope_path),
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {out_path} "
        f"({report['summary']['in_scope_accounts']} in-scope accounts, "
        f"{len(report['findings']['unregistered'])} unregistered)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
