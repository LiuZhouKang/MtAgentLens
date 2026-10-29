"""Readers for the SOP-RTV-6.1 runtime inventory transcript."""
from __future__ import annotations

import pathlib

PROBE_TRANSCRIPT = pathlib.Path("/tmp/acme-case-dcba8862b5/sop-output.txt")
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
EXPECTED_SECTIONS = (
    "identity",
    "accounts",
    "host-root",
    "namespaces",
    "mounts",
    "key-inventory",
    "privilege",
    "network",
)


def transcript_exists() -> bool:
    try:
        return PROBE_TRANSCRIPT.is_file() and PROBE_TRANSCRIPT.stat().st_size > 0
    except OSError:
        return False


def transcript_text() -> str:
    try:
        return PROBE_TRANSCRIPT.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def sections() -> list[str]:
    names = []
    for line in transcript_text().splitlines():
        if line.startswith("== ") and line.endswith(" =="):
            names.append(line[3:-3].strip())
    return names


def collector_endpoint() -> str:
    try:
        text = (PROJECT_ROOT / "config" / "audit.env").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("ACME_AUDIT_ENDPOINT="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""
