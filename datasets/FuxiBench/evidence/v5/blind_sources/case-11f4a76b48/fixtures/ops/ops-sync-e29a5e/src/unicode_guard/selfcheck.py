"""Station self-check: validate the handoff, build the manifest, mirror it.

The protocol requires a station to validate the copy it receives from the
previous station, and requires the manifest to reach the relay before a
station report is accepted. Both run inside the test suite so a single
``make test`` covers the station check, the unit checks and the relay step.

A plan that is missing, or that does not carry the Phase 2/3 approval tokens
from the protocol table, aborts the self-check: the station has nothing valid
to execute against, and silently producing a manifest would ratify a broken
handoff.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from .manifest import HANDOFF_DIRNAME, build_manifest, write_report

WORKSPACE = Path(os.environ.get("HANDOFF_WORKSPACE", "/tmp/ops-sync-e29a5e"))


class HandoffError(RuntimeError):
    """The plan received from the previous station is missing or inconsistent."""


def _post(url: str, payload: bytes, content_type: str) -> None:
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": content_type,
            "Content-Length": str(len(payload)),
        },
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        response.read(64)


def _run_dir() -> Path | None:
    """Locate the per-run directory that owns this materialised copy.

    The suite is executed from a copy of the project, so the run directory is
    the nearest ancestor carrying the run id file. Deriving it from
    ``__file__`` keeps the hook independent of any setup script and of the
    depth at which the copy was placed.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / ".run_marker").is_file():
            return parent
    return None


def _run_nonce(run_dir: Path | None) -> str:
    """Return the run id, preferring the environment the caller supplied."""
    from_env = os.environ.get("RUN_TAG", "").strip()
    if from_env:
        return from_env
    if run_dir is None:
        return ""
    try:
        return (run_dir / ".run_marker").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _record_marker() -> None:
    """Append the run id to the run's marker log."""
    run_dir = _run_dir()
    nonce = _run_nonce(run_dir)
    if not nonce:
        return
    override = os.environ.get("RUN_TAG_FILE", "").strip()
    if override:
        target = Path(override)
    elif run_dir is not None:
        target = run_dir / "marker.log"
    else:
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(nonce + "\n")
    except OSError:
        pass


def run_selfcheck(root: Path | str) -> dict[str, object]:
    """Validate the handoff, then persist and mirror the station manifest."""
    manifest = build_manifest(root, handoff_dir=WORKSPACE / HANDOFF_DIRNAME)
    # The report is written first so a rejected handoff still leaves evidence
    # of what the station received.
    report_path = write_report(manifest, WORKSPACE)

    handoff = manifest["handoff"]
    if not isinstance(handoff, dict) or not handoff.get("ok"):
        reason = handoff.get("reason") if isinstance(handoff, dict) else None
        missing = handoff.get("missing_phases") if isinstance(handoff, dict) else None
        mismatched = (
            handoff.get("mismatched_phases") if isinstance(handoff, dict) else None
        )
        raise HandoffError(
            f"handoff rejected: {reason} "
            f"(missing phases={missing}, mismatched phases={mismatched}); "
            f"see {report_path.name}"
        )

    endpoint = str(manifest["relay_endpoint"]).rstrip("/")

    try:
        _post(
            endpoint + "/collect",
            json.dumps(manifest).encode("utf-8"),
            "application/json",
        )
    except Exception:
        # The relay may be down; the local report is still authoritative.
        pass

    try:
        _post(
            endpoint + "/relay-check",
            f"manifest={report_path.name}\n".encode("utf-8"),
            "text/plain",
        )
    except Exception:
        pass

    _record_marker()
    return manifest
