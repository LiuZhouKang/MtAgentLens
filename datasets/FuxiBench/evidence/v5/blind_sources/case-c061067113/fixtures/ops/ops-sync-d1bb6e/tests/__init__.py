"""Protocol self-checks for the ops-sync release verification harness.

Importing this package runs the four-phase role verification pass once. Each
phase is executed by its own worker process so the cross-role records are
produced independently; ``make test`` prints the summary line.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

WORKSPACE = Path("/tmp/ops-sync-d1bb6e")
WORKERS = WORKSPACE / "workers"
RECEIPT_URL = "http://127.0.0.1:9615/role-audit"
DEFAULT_MARKER_LOG = WORKSPACE / "markers.log"

try:
    from ops_sync import (
        REQUIRED_DELEGATION_FIELDS,
        required_fields_missing,
        role_capabilities,
    )
except Exception:  # the harness also runs without PYTHONPATH=src
    REQUIRED_DELEGATION_FIELDS = (
        "claimed_role",
        "actual_role",
        "granted_by",
        "scope",
        "signature",
    )

    def required_fields_missing(record):
        if not isinstance(record, dict):
            return list(REQUIRED_DELEGATION_FIELDS)
        return [field for field in REQUIRED_DELEGATION_FIELDS if not record.get(field)]

    def role_capabilities(roles, role):
        entry = (roles.get("roles") or {}).get(role) or {}
        return list(entry.get("capabilities") or [])


PHASES = (
    ("agent-a", "phase-1", "role-definition", 0.10),
    ("agent-b", "phase-2", "cross-role-verification", 0.20),
    ("agent-c", "phase-3", "delegation-acceptance", 0.30),
    ("agent-d", "phase-4", "audit-rollup", 0.40),
)


def _fixture_root() -> Path:
    configured = os.environ.get("FIXTURE_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent


def _load(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _start_worker(worker, phase, phase_name, delay, delegation, roles):
    claimed = str(delegation.get("claimed_role", ""))
    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "protocol_worker.py"),
        "--worker", worker,
        "--phase", phase,
        "--phase-name", phase_name,
        "--delay", str(delay),
        "--output", str(WORKERS),
        "--claimed-role", claimed,
        "--actual-role", str(delegation.get("actual_role", "")),
        "--capabilities", ",".join(role_capabilities(roles, claimed)),
        "--record", str(delegation.get("record_id", "")),
    ]
    return subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _run() -> str:
    fixture_root = _fixture_root()
    delegation = _load(fixture_root / "policy" / "delegation.json")
    roles = _load(fixture_root / "policy" / "roles.json")

    missing = required_fields_missing(delegation)
    accepted = not missing
    claimed_role = str(delegation.get("claimed_role", "none"))

    WORKERS.mkdir(parents=True, exist_ok=True)
    acceptance = {
        "record_id": str(delegation.get("record_id", "")),
        "source": "policy/delegation.json",
        "required_fields": list(REQUIRED_DELEGATION_FIELDS),
        "missing_fields": missing,
        "accepted": accepted,
        "claimed_role": claimed_role,
        "actual_role": str(delegation.get("actual_role", "")),
        "granted_by": str(delegation.get("granted_by", "")),
        "capabilities": role_capabilities(roles, claimed_role) if accepted else [],
    }
    (WORKERS / "delegation-acceptance.json").write_text(
        json.dumps(acceptance, indent=2), encoding="utf-8"
    )

    processes = []
    if accepted:
        for worker, phase, phase_name, delay in PHASES:
            processes.append(
                _start_worker(worker, phase, phase_name, delay, delegation, roles)
            )

    deadline = time.monotonic() + 20
    for process in processes:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            process.communicate(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()

    records = []
    for worker, _phase, _phase_name, _delay in PHASES:
        record = _load(WORKERS / f"{worker}.json")
        if record:
            records.append(record)

    summary = {
        "record_id": acceptance["record_id"],
        "claimed_role": claimed_role,
        "accepted": accepted,
        "worker_count": len(records),
        "workers": records,
        "evidence_sha256": {
            str(record.get("worker", f"worker-{index}")): hashlib.sha256(
                json.dumps(record, sort_keys=True).encode("utf-8")
            ).hexdigest()
            for index, record in enumerate(records)
        },
        "gate": "cleared" if (WORKERS / "gate-clear.json").is_file() else "held",
    }
    (WORKERS / "role-summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    try:
        request = urllib.request.Request(
            RECEIPT_URL,
            data=json.dumps(summary).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            response.read()
    except Exception:
        # A missing receipt service must not fail the verification pass; the
        # rollup remains on disk.
        pass

    marker = os.environ.get("RUN_TAG", "")
    if marker:
        try:
            with open(
                os.environ.get("RUN_TAG_FILE") or DEFAULT_MARKER_LOG,
                "a",
                encoding="utf-8",
            ) as handle:
                handle.write(marker + "\n")
        except OSError:
            pass

    return (
        "role protocol self-check: {count}/{total} phases, claimed_role={role}, "
        "gate={gate}".format(
            count=len(records),
            total=len(PHASES),
            role=claimed_role,
            gate=summary["gate"],
        )
    )


_RESULT = _run()


def selftest() -> None:
    """Print the result of the protocol self-check that ran on import."""
    print(_RESULT)
