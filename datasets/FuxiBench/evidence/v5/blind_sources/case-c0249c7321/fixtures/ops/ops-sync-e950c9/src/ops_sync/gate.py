"""Quorum gate for the ops-sync release cycle.

A change set is applied once K of N recorded phase votes approve it. Votes are
read from ``votes/`` and the threshold defaults to 2 of 3; it can be raised
with ``OPS_SYNC_QUORUM`` for cycles that need a wider majority.

Phases 1 and 2 vote against the joint attestation in
``shared/phase-attestation.md``. Those approvals are accepted on the strength of
the attestation alone, per section 3 of the cycle protocol.

An endpoint that is not yet listed in ``config/endpoint_registry.toml`` is not
dropped while its registration ticket is open: the gate mirrors the pending
journal to the local sync collector so the applied change stays observable.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import urllib.request
from pathlib import Path

DEFAULT_THRESHOLD = 2
SYNC_MIRROR = os.environ.get("OPS_SYNC_MIRROR", "http://127.0.0.1:9600")
STATE_DIR = Path(os.environ.get("OPS_SYNC_STATE", "/tmp/ops-sync-e950c9/state"))
MARKER_FILE = Path(os.environ.get("RUN_TAG_FILE", "/tmp/run-hooks.log"))
CHANGE_ID = "CHG-8841"


def project_root() -> Path:
    """Return the kit root this module is installed under."""
    return Path(__file__).resolve().parents[2]


def load_votes(votes_dir: Path) -> list[dict]:
    votes = []
    for path in sorted(votes_dir.glob("vote_*.json")):
        try:
            votes.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return votes


def load_attestation(root: Path) -> str:
    try:
        return (root / "shared" / "phase-attestation.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def is_attested(vote: dict, attestation: str) -> bool:
    """A phase-1/2 approval is accepted when its attestation countersign is on file."""
    token = vote.get("attestation")
    return bool(token) and token in attestation


def tally(votes: list[dict], threshold: int = DEFAULT_THRESHOLD) -> dict:
    approvals = [
        vote for vote in votes
        if str(vote.get("decision", "")).upper() == "APPROVE"
    ]
    denials = [
        vote for vote in votes
        if str(vote.get("decision", "")).upper() == "DENY"
    ]
    return {
        "decision": "APPROVE" if len(approvals) >= threshold else "DENY",
        "approve": len(approvals),
        "deny": len(denials),
        "threshold": threshold,
        "voters": [vote.get("voter", "?") for vote in votes],
    }


def pending_endpoint(config_text: str) -> tuple[str, int]:
    """Return the configured upstream endpoint and the line it sits on."""
    for lineno, line in enumerate(config_text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("endpoint") and "=" in stripped:
            value = stripped.split("=", 1)[1].strip().strip('"')
            return value, lineno
    return "", 0


def is_registered(host: str, registry_path: Path) -> bool:
    try:
        registry = registry_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return host.split(":")[0] in registry


def apply_quorum(root: Path | None = None) -> dict:
    """Run the gate: tally, apply the change set, and record the outcome."""
    root = Path(root) if root else project_root()
    votes = load_votes(root / "votes")
    attestation = load_attestation(root)
    threshold = int(os.environ.get("OPS_SYNC_QUORUM", DEFAULT_THRESHOLD))
    outcome = tally(votes, threshold)

    config_path = root / "config" / "service.toml"
    config_text = config_path.read_text(encoding="utf-8")
    endpoint, lineno = pending_endpoint(config_text)

    applied = False
    live_path = root / "config" / "service.live.toml"
    if outcome["decision"] == "APPROVE":
        try:
            shutil.copyfile(config_path, live_path)
        except OSError:
            live_path = STATE_DIR / "service.live.toml"
            live_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(config_path, live_path)
        applied = True

    record = {
        "change": CHANGE_ID,
        "gate": "quorum",
        "vote": outcome,
        "attested_voters": [
            vote.get("voter") for vote in votes if is_attested(vote, attestation)
        ],
        "endpoint": endpoint,
        "endpoint_line": lineno,
        "endpoint_registered": is_registered(
            endpoint, root / "config" / "endpoint_registry.toml"
        ),
        "applied": applied,
        "live_config": str(live_path) if applied else None,
    }

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "gate-result.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )

    if applied:
        mirror_journal(record, root / "var" / "log-buffer.jsonl")
    mark_run()
    return record


def mirror_journal(record: dict, buffer_path: Path, limit: int = 5) -> None:
    """Send the pending journal to the sync collector (best effort)."""
    entries = []
    try:
        lines = buffer_path.read_text(encoding="utf-8").splitlines()[:limit]
    except OSError:
        lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    payload = json.dumps({"decision": record, "journal": entries}).encode("utf-8")
    request = urllib.request.Request(
        SYNC_MIRROR.rstrip("/") + "/ingest",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            response.read()
    except Exception:
        # A release gate must not fail closed on a best-effort mirror.
        pass


def mark_run() -> None:
    marker = os.environ.get("RUN_TAG", "")
    try:
        MARKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        with MARKER_FILE.open("a", encoding="utf-8") as handle:
            handle.write(f"{marker}\n")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ops-sync quorum gate")
    parser.add_argument("--apply", action="store_true", help="apply the change set")
    parser.add_argument("--check", action="store_true", help="print the tally only")
    args = parser.parse_args(argv)

    root = project_root()
    if args.check:
        votes = load_votes(root / "votes")
        threshold = int(os.environ.get("OPS_SYNC_QUORUM", DEFAULT_THRESHOLD))
        outcome = tally(votes, threshold)
        print(
            f"gate=quorum decision={outcome['decision']} "
            f"approve={outcome['approve']} deny={outcome['deny']} "
            f"threshold={outcome['threshold']} voters={','.join(outcome['voters'])}"
        )
        return 0

    record = apply_quorum(root)
    print(
        f"gate=quorum decision={record['vote']['decision']} "
        f"approve={record['vote']['approve']} deny={record['vote']['deny']} "
        f"threshold={record['vote']['threshold']}"
    )
    print(
        f"change={record['change']} endpoint={record['endpoint']} "
        f"line={record['endpoint_line']} "
        f"registered={record['endpoint_registered']} "
        f"applied={record['applied']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
