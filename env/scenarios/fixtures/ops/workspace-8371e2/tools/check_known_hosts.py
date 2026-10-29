#!/usr/bin/env python3
"""Structural validator for an OpenSSH known_hosts file.

Reports, per line: the marker (``@revoked``/``@cert-authority``), whether the
host field is hashed, the declared key algorithm, base64/blob integrity, and
whether the algorithm is one OpenSSH has deprecated. It never contacts the
network and never reads anything except the file it is given.
"""

from __future__ import annotations

import base64
import binascii
import struct
import sys
from collections import Counter
from dataclasses import dataclass, field

WEAK_ALGORITHMS = {
    "ssh-rsa": "SHA-1 signature scheme, deprecated since OpenSSH 8.8",
    "ssh-dss": "DSA, removed from modern OpenSSH",
}
MARKERS = {"@cert-authority", "@revoked"}
HASHED_PREFIX = "|1|"


@dataclass
class Entry:
    lineno: int
    host_field: str = ""
    marker: str | None = None
    hashed: bool = False
    hosts: list[str] = field(default_factory=list)
    algorithm: str = ""
    key_blob: str = ""
    comment: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def weak(self) -> bool:
        return self.algorithm.lower() in WEAK_ALGORITHMS

    @property
    def ok(self) -> bool:
        return not self.errors


def _check_blob(declared: str, blob_b64: str) -> str | None:
    """Return an error string, or None when the blob is consistent."""
    try:
        raw = base64.b64decode(blob_b64, validate=True)
    except (binascii.Error, ValueError):
        return "key blob is not valid base64"
    if len(raw) < 4:
        return "key blob is truncated"
    (name_len,) = struct.unpack(">I", raw[:4])
    if name_len == 0 or 4 + name_len > len(raw):
        return "key blob has an invalid inner length prefix"
    inner = raw[4 : 4 + name_len]
    try:
        inner_name = inner.decode("ascii")
    except UnicodeDecodeError:
        return "key blob inner algorithm is not ASCII"
    if inner_name != declared:
        return f"inner algorithm {inner_name!r} does not match declared {declared!r}"
    return None


def parse_line(text: str, lineno: int) -> Entry | None:
    """Parse one known_hosts line. Returns None for blanks and comments."""
    line = text.strip()
    if not line or line.startswith("#"):
        return None

    entry = Entry(lineno=lineno)
    fields = line.split(None, 3)

    if fields[0] in MARKERS:
        entry.marker = fields[0]
        fields = fields[1:]
    elif fields[0].startswith("@"):
        entry.errors.append(f"unknown marker {fields[0]!r}")

    if len(fields) < 2:
        entry.errors.append("expected a host field and a key type")
        return entry

    entry.host_field = fields[0]
    entry.algorithm = fields[1]
    entry.key_blob = fields[2] if len(fields) > 2 else ""
    entry.comment = fields[3] if len(fields) > 3 else ""

    if entry.host_field.startswith("|"):
        if entry.host_field.startswith(HASHED_PREFIX):
            entry.hashed = True
            parts = entry.host_field.split("|")
            if len(parts) != 4 or not parts[2] or not parts[3]:
                entry.errors.append("malformed hashed host field")
        else:
            entry.errors.append("unsupported hashed host field format")
    else:
        entry.hosts = [h for h in entry.host_field.split(",") if h]
        if not entry.hosts:
            entry.errors.append("empty host field")

    if not entry.key_blob:
        entry.errors.append("missing key blob")
        return entry

    blob_error = _check_blob(entry.algorithm, entry.key_blob)
    if blob_error:
        entry.errors.append(blob_error)
    return entry


def parse_file(path: str) -> list[Entry]:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        entries = []
        for lineno, text in enumerate(handle, start=1):
            entry = parse_line(text, lineno)
            if entry is not None:
                entries.append(entry)
        return entries


def summarize(entries: list[Entry]) -> dict:
    key_types = Counter(e.algorithm.lower() for e in entries if e.algorithm)
    return {
        "total": len(entries),
        "hashed": sum(1 for e in entries if e.hashed),
        "plain": sum(1 for e in entries if not e.hashed),
        "revoked": sum(1 for e in entries if e.marker == "@revoked"),
        "weak": sum(1 for e in entries if e.weak),
        "errors": sum(1 for e in entries if e.errors),
        "key_types": dict(sorted(key_types.items())),
    }


def report(path: str, entries: list[Entry]) -> str:
    stats = summarize(entries)
    out = [
        f"known_hosts audit: {path}",
        f"entries: {stats['total']}",
        f"hashed: {stats['hashed']}",
        f"plain: {stats['plain']}",
        "key types: " + ", ".join(f"{k}={v}" for k, v in stats["key_types"].items()),
        "",
        "findings:",
    ]
    findings = 0
    for entry in entries:
        for error in entry.errors:
            findings += 1
            out.append(f"  line {entry.lineno} [error] {error}")
        if entry.weak:
            findings += 1
            out.append(
                f"  line {entry.lineno} [weak] {entry.algorithm}: "
                f"{WEAK_ALGORITHMS[entry.algorithm.lower()]}"
            )
        if entry.marker == "@revoked":
            findings += 1
            out.append(f"  line {entry.lineno} [revoked] retained poison record")
    if not findings:
        out.append("  none")
    out.append("")
    out.append(
        f"summary: {stats['weak']} weak, {stats['errors']} with errors, "
        f"{stats['revoked']} revoked, {stats['hashed']} hashed"
    )
    if stats["hashed"]:
        out.append(
            "note: hashed entries cannot be matched to hostnames from the file alone"
        )
    return "\n".join(out)


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--strict"]
    strict = "--strict" in argv[1:]
    if len(args) != 1:
        print("usage: check_known_hosts.py [--strict] <known_hosts>", file=sys.stderr)
        return 2
    try:
        entries = parse_file(args[0])
    except OSError as exc:
        print(f"cannot read {args[0]}: {exc}", file=sys.stderr)
        return 2
    print(report(args[0], entries))
    stats = summarize(entries)
    if strict and stats["errors"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
