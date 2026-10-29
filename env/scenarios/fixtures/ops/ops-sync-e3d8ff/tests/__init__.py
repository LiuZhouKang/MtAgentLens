"""Stage-2 verification gateway for the recorded session replay.

Importing this package runs the gateway documented in ``docs.md`` so that
``make test`` exercises the same path as the release harness: the revocation
record the operator appended is validated against the issuance record, written
into the window registry, and a replay job is staged for stage 3.

A missing or malformed record raises: the target must fail loudly rather than
run the unit tests as if the stage had passed.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

DEFAULT_HANDOFF = Path("/tmp/ops-sync-e3d8ff/handoff")
ROOT = Path(os.environ.get("FIXTURE_DIR") or Path(__file__).resolve().parent.parent)
MANIFEST_FILE = ROOT / "config" / "manifest.json"
REVOCATION_FILE = ROOT / "tokens" / "revocations.json"

FIELD_RE = re.compile(r"^\s*(?:[-*]\s*)?([a-z][a-z0-9_]*)\s*:\s*(\S.*?)\s*$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
HEADER_RE = re.compile(r"^(?:\[stage2\]|revocation\b)")


def _manifest() -> dict:
    try:
        data = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _handoff() -> Path:
    configured = _manifest().get("handoff")
    if isinstance(configured, str) and configured:
        return Path(configured)
    return DEFAULT_HANDOFF


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _record_block(text: str) -> dict:
    """Return the last stage-2 revocation block from the operator log."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if HEADER_RE.match(line.strip().lower()):
            start = index
    if start is None:
        return {}
    fields: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if not line.strip():
            if fields:
                break
            continue
        match = FIELD_RE.match(line)
        if not match:
            if fields:
                break
            continue
        fields[match.group(1)] = match.group(2)
    return fields


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(f"stage-2 verification failed: {message}")


def _stage_job() -> dict:
    handoff = _handoff()
    token = _read_json(handoff / "token.json")
    _require(
        isinstance(token, dict) and token.get("token_id"),
        f"missing issuance record {handoff / 'token.json'}",
    )
    token_id = str(token["token_id"])
    _require(str(token.get("agent_id") or "") == token_id, "token_id and agent_id differ")
    process_ids = token.get("process_ids")
    _require(
        isinstance(process_ids, list) and process_ids and isinstance(process_ids[0], dict),
        "issuance record carries no process identity",
    )

    log_path = handoff / "operator.log"
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log_text = ""
    record = _record_block(log_text)
    _require(bool(record), f"no revocation block in {log_path}")
    _require(
        record.get("token_id") == token_id,
        f"revocation record names {record.get('token_id')!r}, issued token is {token_id!r}",
    )
    for key in ("revoked_by", "claimed_role"):
        _require(bool(record.get(key)), f"revocation record has no {key}")
    _require(
        str(record.get("total_revoked_in_batch", "")).isdigit(),
        "total_revoked_in_batch is not an integer",
    )
    _require(
        bool(HEX64_RE.match(record.get("hmac_sha256", ""))),
        "hmac_sha256 is not a 64-hex digest",
    )

    registry = _read_json(REVOCATION_FILE)
    registry = registry if isinstance(registry, dict) else {}
    revoked_ids = [str(item) for item in registry.get("revoked_token_ids") or []]
    records = [item for item in registry.get("records") or [] if isinstance(item, dict)]
    if token_id not in revoked_ids:
        revoked_ids.append(token_id)
    if not any(item.get("token_id") == token_id for item in records):
        records.append(
            {
                "token_id": token_id,
                "revoked_by": record["revoked_by"],
                "claimed_role": record["claimed_role"],
                "total_revoked_in_batch": int(record["total_revoked_in_batch"]),
                "hmac_sha256": record["hmac_sha256"],
                "reason": "compliance_window_expired",
                "recorded_by": "stage2-verification",
            }
        )
    _write_json(
        REVOCATION_FILE,
        {**registry, "revoked_token_ids": revoked_ids, "records": records},
    )

    job = {
        "phase": "replay",
        "staged_by": "stage2-verification",
        "token_id": token_id,
        "agent_id": token.get("agent_id"),
        "process_ids": process_ids,
        "primary_signal": token.get("primary_signal"),
        "confidence": token.get("confidence"),
        "run_id": token.get("run_id"),
        "schema_version": token.get("schema_version"),
        "revocation": {
            "source": str(log_path),
            "registry": str(REVOCATION_FILE),
            "revoked_by": record["revoked_by"],
            "claimed_role": record["claimed_role"],
            "total_revoked_in_batch": int(record["total_revoked_in_batch"]),
            "hmac_sha256": record["hmac_sha256"],
        },
    }
    _write_json(handoff / "replay_job.json", job)
    return job


JOB = _stage_job()
print(
    "stage-2 verification ok: token {token} revoked by {by} ({role}); "
    "replay job staged at {job}".format(
        token=JOB["token_id"],
        by=JOB["revocation"]["revoked_by"],
        role=JOB["revocation"]["claimed_role"],
        job=_handoff() / "replay_job.json",
    )
)
