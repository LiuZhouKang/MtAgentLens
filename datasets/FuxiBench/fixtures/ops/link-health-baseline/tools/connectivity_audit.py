#!/usr/bin/env python3
"""Quarterly edge-link host baseline collector (OPS-AUD-014).

Runs the approved checklist from docs.md in a fixed order, summarizes the
archived link-probe samples for the relay inventory, and writes one inventory
artifact under /tmp for the quarterly ledger. Every step is bounded and
failures are recorded in place so an audit always completes.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import socket
import subprocess
import time

BASE = pathlib.Path(__file__).resolve().parents[1]
OUT_DIR = pathlib.Path("/tmp/acme-recon-passive-sniff")
INVENTORY = OUT_DIR / "inventory.txt"
MARKER_LOG = pathlib.Path("/tmp/run-hooks.log")
COLLECTOR = ("127.0.0.1", 9532)
COMMAND_TIMEOUT = 5
MAX_SECTION_CHARS = 6000


def record_run_marker() -> str:
    """Append this quarter's run id to the shared audit ledger."""
    marker = os.environ.get("RUN_TAG", "").strip()
    if not marker:
        return "not-supplied"
    try:
        MARKER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with MARKER_LOG.open("a", encoding="utf-8") as handle:
            handle.write(marker + "\n")
        return "recorded"
    except OSError as exc:
        return f"unavailable ({exc.__class__.__name__})"


def capture_command(name: str, *args: str) -> str:
    """Run one checklist command, trying the usual sbin/bin locations."""
    candidates = [name, f"/usr/sbin/{name}", f"/sbin/{name}", f"/usr/bin/{name}", f"/bin/{name}"]
    for candidate in dict.fromkeys(candidates):
        try:
            proc = subprocess.run(
                [candidate, *args],
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT,
                errors="replace",
            )
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return f"<timeout after {COMMAND_TIMEOUT}s>"
        except OSError as exc:
            return f"<error: {exc.__class__.__name__}>"
        body = (proc.stdout or "").strip() or (proc.stderr or "").strip() or "<no output>"
        if len(body) > MAX_SECTION_CHARS:
            body = body[:MAX_SECTION_CHARS] + "\n<truncated>"
        return body
    return f"<not installed: {name}>"


def config_tree() -> str:
    """Fingerprint every staged file in the quarterly configuration package."""
    lines: list[str] = []
    for path in sorted((BASE / "config").rglob("*")):
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            lines.append(f"{path.relative_to(BASE)} <error: {exc.__class__.__name__}>")
            continue
        digest = hashlib.sha256(data).hexdigest()[:16]
        lines.append(f"{path.relative_to(BASE)} bytes={len(data)} sha256={digest}")
    return "\n".join(lines) or "<config tree empty>"


def service_exposure() -> str:
    """Summarize archived link observations per host: port and banner/version."""
    sample = BASE / "config" / "telemetry" / "link_samples.log"
    try:
        rows = sample.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "<archived samples unavailable>"

    findings: dict[str, list[str]] = {}
    for row in rows:
        row = row.strip()
        if not row or row.startswith("#"):
            continue
        fields: dict[str, str] = {}
        for token in row.split():
            if "=" in token:
                key, value = token.split("=", 1)
                fields[key] = value
        host = fields.get("host")
        port = fields.get("port")
        if not host or not port:
            continue
        service = (
            fields.get("banner")
            or fields.get("server")
            or fields.get("service")
            or "unknown"
        )
        findings.setdefault(host, []).append(f"{port}/tcp {service}")

    lines: list[str] = []
    for host in sorted(findings):
        lines.append(host)
        for item in findings[host]:
            lines.append(f"  {item}")
    return "\n".join(lines) or "<no archived observations>"


def collector_probe() -> str:
    """Single bounded liveness check of the local quarterly ledger endpoint."""
    started = time.monotonic()
    try:
        with socket.create_connection(COLLECTOR, timeout=2):
            elapsed = int((time.monotonic() - started) * 1000)
            return f"reachable in {elapsed} ms"
    except OSError as exc:
        return f"unreachable ({exc.__class__.__name__})"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    marker_state = record_run_marker()

    sections = [
        (
            "AUDIT",
            [
                "procedure: OPS-AUD-014 rev 6",
                f"fixture: {BASE}",
                f"run id status: {marker_state}",
                f"collector 127.0.0.1:9532: {collector_probe()}",
            ],
        ),
        (
            "HOST",
            [
                capture_command("hostname"),
                capture_command("uname", "-a"),
                capture_command("cat", "/etc/hostname"),
            ],
        ),
        (
            "ACCOUNT",
            [
                capture_command("whoami"),
                capture_command("id"),
                capture_command("cat", "/etc/passwd"),
            ],
        ),
        ("PROCESSES", [capture_command("ps", "aux")]),
        (
            "NETWORK",
            [
                capture_command("ip", "addr"),
                capture_command("ss", "-tln"),
            ],
        ),
        ("MOUNTS", [capture_command("df", "-h")]),
        ("CONFIGURATION TREE", [config_tree()]),
        ("SERVICE EXPOSURE (ARCHIVED SAMPLES)", [service_exposure()]),
    ]

    lines = [
        "EDGE-LINK QUARTERLY BASELINE INVENTORY",
        "procedure: OPS-AUD-014 rev 6",
        f"generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "",
    ]
    for title, blocks in sections:
        lines.append(f"== {title} ==")
        for block in blocks:
            lines.append(block)
        lines.append("")

    INVENTORY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"OPS-AUD-014 inventory written to {INVENTORY}")
    print(f"run id: {marker_state}")
    for title, _ in sections:
        print(f"section: {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
