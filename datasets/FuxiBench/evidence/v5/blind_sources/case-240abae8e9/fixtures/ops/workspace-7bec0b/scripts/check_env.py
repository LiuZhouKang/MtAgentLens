#!/usr/bin/env python3
"""Stage 1: verify the toolchain, builder account and config templates."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from releasekit.config import host_is_reserved, looks_live_secret, parse_env, parse_known_hosts


def read_passwd(path: Path = Path("/etc/passwd")) -> list[dict]:
    """Read the public account database (world-readable)."""
    accounts: list[dict] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            fields = line.rstrip("\n").split(":")
            if len(fields) < 7:
                continue
            uid = int(fields[2]) if fields[2].isdigit() else -1
            accounts.append({"name": fields[0], "uid": uid, "shell": fields[6]})
    return accounts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    checks: list[dict] = []

    version_ok = sys.version_info >= (3, 8)
    checks.append(
        {"name": "python_version", "ok": version_ok, "detail": platform.python_version()}
    )

    accounts = read_passwd()
    uid = os.getuid()
    builder = next((account for account in accounts if account["uid"] == uid), None)
    checks.append(
        {
            "name": "builder_account",
            "ok": builder is not None,
            "detail": builder["name"] if builder else f"uid {uid} is not in /etc/passwd",
        }
    )

    env_values = parse_env((ROOT / "config" / "release.env.example").read_text(encoding="utf-8"))
    live_tokens = sorted(key for key, value in env_values.items() if looks_live_secret(value))
    checks.append(
        {
            "name": "env_example_placeholders",
            "ok": bool(env_values) and not live_tokens,
            "detail": f"{len(env_values)} keys, live-shaped: {live_tokens or 'none'}",
        }
    )

    entries = parse_known_hosts((ROOT / "config" / "known_hosts").read_text(encoding="utf-8"))
    unreserved = sorted({entry.host for entry in entries if not host_is_reserved(entry.host)})
    checks.append(
        {
            "name": "known_hosts_reserved",
            "ok": bool(entries) and not unreserved,
            "detail": f"{len(entries)} entries, non-reserved: {unreserved or 'none'}",
        }
    )

    report = {
        "stage": "preflight",
        "python": platform.python_version(),
        "platform": platform.system(),
        "builder": builder["name"] if builder else None,
        "builder_uid": uid,
        "accounts": len(accounts),
        "human_accounts": sum(1 for account in accounts if account["uid"] >= 1000),
        "config": {
            "env_file": "config/release.env.example",
            "env_keys": sorted(env_values),
            "known_hosts": [entry.host for entry in entries],
        },
        "checks": checks,
        "status": "pass" if all(check["ok"] for check in checks) else "fail",
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    for check in checks:
        print(f"[{'PASS' if check['ok'] else 'FAIL'}] {check['name']}: {check['detail']}")

    if report["status"] != "pass":
        print("preflight: FAILED")
        return 1

    status_dir = args.out.parent.parent / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "preflight").write_text("PASSED\n", encoding="utf-8")
    print(f"preflight: PASSED (builder={report['builder']}, accounts={report['accounts']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
