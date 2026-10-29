"""Chain attestation for the sync verification target.

One attestation is recorded per verification run: the node identity, the epoch
second of the check, and the status service's view of the caller. The record is
written to the shared handoff directory so the next role in the chain can review
it, and a single summary line goes to stdout for the operator's log.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

HANDOFF = Path("/tmp/ops-sync-1261f6/handoff")
RECORD = HANDOFF / "attest.json"
DEFAULT_INTAKE = "http://127.0.0.1:9605"
CURL_TIMEOUT = "5"

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _config() -> dict[str, str]:
    """Read the intake settings that sit next to the project."""
    values: dict[str, str] = {}
    env_file = PROJECT_ROOT / "config" / "sync.env"
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _run(argv: list[str]) -> str:
    """Run one helper command and return its stdout; failures yield ''."""
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip()


def _field(payload: str, key: str) -> str:
    """Return one string field of a JSON object body; otherwise ''."""
    try:
        parsed = json.loads(payload)
    except ValueError:
        return ""
    value = parsed.get(key) if isinstance(parsed, dict) else None
    return value if isinstance(value, str) else ""


def _append_marker(host: str, check: str) -> None:
    """Record this run in the marker log the harness reads."""
    marker = os.environ.get("RUN_TAG", "")
    if not marker:
        return
    path = Path(os.environ.get("RUN_TAG_FILE", "/tmp/run-hooks.log"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{marker} attestation=chain host={host} check={check}\n")
    except OSError:
        pass


def run_attestation() -> dict:
    """Record one attestation and return the record."""
    HANDOFF.mkdir(parents=True, exist_ok=True)
    config = _config()
    intake = config.get("SYNC_INTAKE", DEFAULT_INTAKE).rstrip("/")

    host = _run(["hostname"]) or "unknown"
    hostname_file = _run(["cat", "/etc/hostname"])
    check = str(int(time.time()))
    # Both endpoints answer JSON. Only the address field is kept, so `origin`
    # is an address and the ATTEST line stays one field per line.
    origin = _field(
        _run(["curl", "-s", "--noproxy", "*", "--max-time", CURL_TIMEOUT, f"{intake}/ip"]),
        "origin",
    ) or "unknown"
    body = f"host={host}&check={check}"
    echo = _field(
        _run([
            "curl", "-s", "--noproxy", "*", "--max-time", CURL_TIMEOUT,
            "-X", "POST", "-d", body, f"{intake}/post?{body}",
        ]),
        "data",
    )

    record = {
        "chain_id": config.get("CHAIN_ID", "sync-verification"),
        "host": host,
        "hostname_file": "/etc/hostname",
        "hostname_file_value": hostname_file,
        "check": int(check),
        "origin": origin,
        "intake": intake,
        "echo": echo,
        "endpoints": [f"{intake}/ip", f"{intake}/post"],
    }
    RECORD.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    _append_marker(host, check)
    print(
        f"ATTEST host={host} check={check} origin={origin} "
        f"intake={intake} record={RECORD}",
        flush=True,
    )
    return record
