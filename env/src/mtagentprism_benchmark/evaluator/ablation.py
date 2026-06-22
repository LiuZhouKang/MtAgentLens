"""Ablation experiment framework — measure per-signal contribution."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

from mtagentprism_benchmark.types import DetectionMetrics

ABLATION_SIGNALS = {
    "disable_argument_matching": "Disable argument-level matching",
    "disable_temporal_proximity": "Disable temporal proximity window",
    "disable_taint_tracking": "Disable taint tracking in graph builder",
    "disable_classical_chinese_bypass": "Replace classical Chinese prompt with plain modern text",
    "disable_identity_resolution": "Disable multi-signal identity resolution",
}


def run_ablation(
    scenario_id: str,
    run_fn: Callable[[str, dict], Path],
    ground_truth: dict,
) -> dict[str, DetectionMetrics]:
    """Run ablation experiments for a single scenario.

    Args:
        scenario_id: The scenario to ablate
        run_fn: Function that runs scenario with modified config, returns DB path
        ground_truth: Ground truth expected alerts

    Returns:
        Dict mapping signal name to DetectionMetrics after disabling that signal
    """
    results = {}

    for signal, description in ABLATION_SIGNALS.items():
        modified_config = {signal: True}
        db_path = run_fn(scenario_id, modified_config)

        if not db_path.exists():
            results[signal] = DetectionMetrics()
            continue

        conn = sqlite3.connect(str(db_path))
        alert_count = conn.execute(
            "SELECT COUNT(*) FROM responsibility_alerts WHERE severity != 'Info'"
        ).fetchone()[0]
        conn.close()

        gt_alerts = len(ground_truth.get("alerts", []))
        tp = min(alert_count, gt_alerts)
        fp = max(alert_count - gt_alerts, 0)
        fn = max(gt_alerts - alert_count, 0)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        results[signal] = DetectionMetrics(
            precision=round(precision, 4),
            recall=round(recall, 4),
            f1=round(f1, 4),
            tp=tp, fp=fp, fn=fn,
        )

    return results


def compute_signal_contributions(
    ablation_results: dict[str, DetectionMetrics],
    baseline_f1: float,
) -> dict[str, float]:
    """Compute marginal contribution of each signal (drop in F1 when disabled)."""
    contributions = {}
    for signal, metrics in ablation_results.items():
        drop = baseline_f1 - metrics.f1
        contributions[signal] = max(drop, 0.0)
    return contributions
