#!/usr/bin/env python3
"""Structural summary helper for an SSH client config and known_hosts copy.

Reads only the two text files named on the command line. It never resolves or
opens IdentityFile paths, never reads key material and never touches the
network.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

DEFAULT_PORT = "22"
WILDCARD_CHARS = set("*?!")


def _split_directive(line: str) -> tuple[str, str]:
    parts = line.split(None, 1)
    if not parts:
        return "", ""
    return parts[0], (parts[1].strip() if len(parts) > 1 else "")


def parse_config(text: str) -> dict:
    """Return host blocks and defaults blocks parsed from ssh_config text."""
    host_blocks: list[dict] = []
    defaults_blocks: list[dict] = []
    current: dict | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keyword, value = _split_directive(line)
        if keyword.lower() == "host":
            aliases = value.split()
            current = {
                "aliases": aliases,
                "options": {},
                "is_defaults": bool(aliases)
                and all(
                    any(ch in WILDCARD_CHARS for ch in alias) for alias in aliases
                ),
            }
            (defaults_blocks if current["is_defaults"] else host_blocks).append(current)
        elif current is not None:
            current["options"][keyword.lower()] = value

    return {"host_blocks": host_blocks, "defaults_blocks": defaults_blocks}


def alias_rows(parsed: dict) -> list[dict]:
    """Flatten host blocks to one row per alias, retaining the block options."""
    rows: list[dict] = []
    for block in parsed["host_blocks"]:
        options = block["options"]
        for alias in block["aliases"]:
            rows.append(
                {
                    "alias": alias,
                    "hostname": options.get("hostname", ""),
                    "user": options.get("user", ""),
                    "port": options.get("port", DEFAULT_PORT),
                    "identity_file": options.get("identityfile", ""),
                }
            )
    return rows


def parse_known_hosts(text: str) -> list[dict]:
    """Return known_hosts entries with marker, hashed and port information."""
    entries: list[dict] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        marker = ""
        if fields and fields[0].startswith("@"):
            marker = fields[0]
            fields = fields[1:]
        if len(fields) < 2:
            continue
        host_field, key_type = fields[0], fields[1]
        entries.append(
            {
                "line": lineno,
                "marker": marker,
                "host_field": host_field,
                "hosts": host_field.split(","),
                "key_type": key_type,
                "hashed": host_field.startswith("|1|"),
                "port_qualified": host_field.startswith("["),
            }
        )
    return entries


def summarize(config_text: str, known_hosts_text: str) -> str:
    parsed = parse_config(config_text)
    rows = alias_rows(parsed)
    entries = parse_known_hosts(known_hosts_text)

    plain = [entry for entry in entries if not entry["marker"] and not entry["hashed"]]
    hashed = [entry for entry in entries if entry["hashed"]]
    revoked = [entry for entry in entries if entry["marker"]]

    key_types = Counter(entry["key_type"] for entry in entries)
    key_type_text = (
        ", ".join(f"{name}={count}" for name, count in sorted(key_types.items()))
        or "none"
    )
    identity_count = sum(1 for row in rows if row["identity_file"])

    lines = [
        f"config: {len(parsed['host_blocks']) + len(parsed['defaults_blocks'])} blocks "
        f"({len(parsed['host_blocks'])} host, {len(parsed['defaults_blocks'])} defaults)",
        f"aliases: {len(rows)} ({identity_count} with IdentityFile)",
        f"known_hosts: {len(entries)} entries "
        f"({len(plain)} plain, {len(hashed)} hashed, {len(revoked)} revoked)",
        f"key types: {key_type_text}",
    ]
    for row in rows:
        identity = row["identity_file"] or "none"
        lines.append(
            f"alias: {row['alias']} -> {row['hostname']} "
            f"user={row['user']} port={row['port']} identity={identity}"
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: inspect_ssh.py CONFIG KNOWN_HOSTS", file=sys.stderr)
        return 2
    config_text = Path(argv[1]).read_text(encoding="utf-8")
    known_hosts_text = Path(argv[2]).read_text(encoding="utf-8")
    print(summarize(config_text, known_hosts_text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
