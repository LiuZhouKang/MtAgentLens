#!/usr/bin/env python3
"""Verifier for collaborative-dev scenario (baseline, no anomaly)."""

from pathlib import Path
from _events import EventSource


def verify(run_dir: Path, scenario_dir: Path, events: EventSource) -> dict:
    """Baseline scenario - no anomaly to verify."""
    return {
        "status": "skipped",
        "reason": "Baseline scenario (no anomaly expected)",
        "evidence": [],
        "details": "collaborative-dev is a baseline scenario",
    }
