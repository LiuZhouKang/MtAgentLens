"""Readiness scoring and probe pass for the relay probe matrix."""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.error
import urllib.request

PROBE_STATES = ("ok", "degraded", "unreachable")
MATRIX_RELPATH = os.path.join("config", "internal-endpoints.txt")
DEFAULT_TIMEOUT = 2


def summarise(probes):
    """Fold probe results into a per-state count."""
    counts = {state: 0 for state in PROBE_STATES}
    for probe in probes:
        state = probe.get("state", "unreachable")
        if state not in counts:
            state = "unreachable"
        counts[state] += 1
    return counts


def load_matrix(root):
    """Return the probe targets listed in the matrix file, comments skipped."""
    targets = []
    try:
        with open(os.path.join(root, MATRIX_RELPATH), encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#"):
                    targets.append(line)
    except OSError:
        pass
    return targets


def probe(target, timeout=DEFAULT_TIMEOUT):
    """Probe one matrix target and return only the status code."""
    if shutil.which("curl"):
        try:
            completed = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "--max-time",
                    str(timeout),
                    "-o",
                    "/dev/null",
                    "-w",
                    "%{http_code}",
                    target,
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 3,
                check=False,
            )
            return completed.stdout.strip() or "error"
        except Exception:
            return "error"
    try:
        with urllib.request.urlopen(target, timeout=timeout) as response:
            return str(response.status)
    except urllib.error.HTTPError as exc:
        return str(exc.code)
    except Exception:
        return "error"


def run_matrix(root, timeout=DEFAULT_TIMEOUT):
    """Probe every target in the matrix once, in file order."""
    return [(target, probe(target, timeout)) for target in load_matrix(root)]
