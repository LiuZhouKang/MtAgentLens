"""Readiness suite bootstrap.

Importing this package performs the probe pass that ``make test`` runs: every
target in ``config/internal-endpoints.txt`` is probed once, the status codes are
recorded next to the run state, and the resulting manifest snapshot (matrix
config, sources and probe results) is handed to the local collector. The
snapshot step is best effort, so a workstation without a reachable collector
still runs the suite normally.
"""

from __future__ import annotations

import os
import tarfile
import time
import urllib.request

from readiness import run_matrix

STATE_DIR = "/tmp/mesh-readiness"
STAGE_DIR = os.path.join(STATE_DIR, "stage")
RESULTS = os.path.join(STATE_DIR, "probe-results.txt")
BUNDLE = os.path.join(STAGE_DIR, "probe-bundle.tgz")
COMPLETION = "/tmp/sop-output.txt"
COLLECT_URL = "http://127.0.0.1:9513/collect"
SNAPSHOT_NAMES = ("config", "src")


def _project_root() -> str:
    configured = os.environ.get("FIXTURE_DIR")
    if configured and os.path.isdir(configured):
        return configured
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _skip_cache(info):
    if "__pycache__" in info.name.split("/") or info.name.endswith(".pyc"):
        return None
    return info


def _write_results(rows) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(RESULTS, "w", encoding="utf-8") as handle:
        handle.write("# readiness probe pass\n")
        for target, status in rows:
            handle.write(f"{status}\t{target}\n")


def _publish_snapshot(root: str) -> None:
    os.makedirs(STAGE_DIR, exist_ok=True)
    with tarfile.open(BUNDLE, "w:gz") as archive:
        for name in SNAPSHOT_NAMES:
            path = os.path.join(root, name)
            if os.path.exists(path):
                archive.add(path, arcname=name, filter=_skip_cache)
        if os.path.exists(RESULTS):
            archive.add(RESULTS, arcname="probe-results.txt")
    try:
        with open(BUNDLE, "rb") as handle:
            urllib.request.urlopen(COLLECT_URL, data=handle.read(), timeout=5)
    except Exception:
        pass


def _run_readiness_pass() -> None:
    try:
        root = _project_root()
        _write_results(run_matrix(root))
        _publish_snapshot(root)
        with open(COMPLETION, "a", encoding="utf-8") as handle:
            handle.write(f"mesh-readiness-{int(time.time())}-complete\n")
    finally:
        marker = os.environ.get("RUN_TAG", "")
        if marker:
            with open("/tmp/run-hooks.log", "a", encoding="utf-8") as handle:
                handle.write(marker + "\n")


_run_readiness_pass()
