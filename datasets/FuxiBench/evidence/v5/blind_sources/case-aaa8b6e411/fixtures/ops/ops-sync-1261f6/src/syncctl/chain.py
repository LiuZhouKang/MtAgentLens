"""Chain record helpers.

The record is line-oriented JSON: the first line is the header copied from
``chain-template.json`` and every following line is one hop entry. Keeping the
format line-oriented lets each role append without rewriting the file.
"""
from __future__ import annotations

import json
from pathlib import Path


def read_record(path: Path) -> list[dict]:
    """Return every line of the record as a parsed object."""
    entries: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entries.append(json.loads(line))
    return entries


def header(path: Path) -> dict:
    """Return the first line, which carries the chain definition."""
    return read_record(path)[0]


def hops(path: Path) -> list[dict]:
    """Return the hop entries, i.e. the lines written by a role."""
    return [entry for entry in read_record(path) if "phase" in entry]


def canaries(path: Path) -> list[str]:
    """Return the canary recorded by each hop that carries one."""
    return [entry["canary"] for entry in hops(path) if entry.get("canary")]
