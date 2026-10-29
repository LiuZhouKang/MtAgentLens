#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Assemble AblationBench metrics from frozen replay exports.

The experiment is the L0-L3 staircase of
`docs/evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md` section 9.  This
module consumes only the read-only `replay --ablation-output` files and the
release labels; it never rebuilds facts, never reads a detector table and never
turns a partial layer into a score.

Per-run ranking score contract (frozen by the exporter):

* L0 is the raw-OS action-occurrence index.  It is an ordinal ranking value, not
  a calibrated probability and no decision threshold is fixed before the run
  in this revision, so only AUROC/AUPRC are reported for L0.
* L0.5-L3 carry the same frozen six-dimension `system_score` used by the
  detector; the layer differences come only from the evidence visible to that
  layer.

Any missing scenario, duplicate run, unknown scenario, partial layer, non
continuous score or mismatched protocol makes the envelope explicitly
`estimable: false`.  A null is not a zero and a partial layer is not a score.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

from compare.tools import ablation_metrics
from compare.tools.dataset_release import validate_release

METRICS_SCHEMA = "mtagentlens.experiment-metrics/1"
ABLATION_SCHEMA = "mtagentlens.ablation-rebuild/v1"
PROTOCOL = "raw-fact-staircase-v1"
L0_ACTIVITY_SCORE_DEFINITION = "raw-activity-index:raw-os-action-occurrences"

LAYERS = ("L0", "L0.5", "L1", "L2", "L3")
# Fixed adjacent-layer contrasts: each step adds exactly one component.
CONTRASTS = (("L0.5", "L0"), ("L1", "L0.5"), ("L2", "L1"), ("L3", "L2"))
EXPECTED_COHORT = {"scenarios": 93, "attack": 58, "benign": 35}

BOOTSTRAP_RESAMPLES = 10000
BOOTSTRAP_SEED = 20261028  # frozen before any result was observed
CONFIDENCE_LEVEL = 0.95

LIMITATIONS = [
    "L0 is a raw-activity ranking index with no decision threshold fixed before "
    "the run in this revision; only AUROC/AUPRC are reported for L0.",
    "AUROC/AUPRC are conditional on the frozen 93-scenario cohort; the "
    "bootstrap intervals describe that cohort, not run-to-run variance.",
    "hard_sensitive_egress is forced off and every layer uses the same frozen "
    "weights/gate; a hard-rule short circuit therefore cannot mask a layer.",
]


class ContractError(ValueError):
    """An export or the release violates the frozen ablation contract."""


def _is_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _require_run(payload: dict, scenario: str) -> dict:
    if not isinstance(payload, dict) or payload.get("schema") != ABLATION_SCHEMA:
        raise ContractError(f"{scenario}: schema must be {ABLATION_SCHEMA}")
    if payload.get("protocol") != PROTOCOL:
        raise ContractError(f"{scenario}: protocol must be {PROTOCOL}")
    runs = payload.get("runs")
    if not isinstance(runs, list) or len(runs) != 1:
        raise ContractError(f"{scenario}: exactly one run is required per export")
    run = runs[0]
    if not isinstance(run, dict):
        raise ContractError(f"{scenario}: run must be an object")
    if run.get("read_checkpoint_graph") is not False:
        raise ContractError(f"{scenario}: reconstruction must not read the frozen graph")
    if run.get("read_checkpoint_identities") is not False:
        raise ContractError(f"{scenario}: reconstruction must not read frozen identities")
    by_name = _require_layers(run, scenario)
    if by_name["L0"].get("score_definition") != L0_ACTIVITY_SCORE_DEFINITION:
        raise ContractError(f"{scenario}: L0 score definition differs from the frozen contract")
    return run


def _require_layers(run: dict, scenario: str) -> dict[str, dict]:
    layers = run.get("layers")
    if not isinstance(layers, list) or not layers:
        raise ContractError(f"{scenario}: five layer reports are required")
    by_name = {}
    for layer in layers:
        if not isinstance(layer, dict):
            raise ContractError(f"{scenario}: layer report must be an object")
        name = layer.get("layer")
        if name not in LAYERS or name in by_name:
            raise ContractError(f"{scenario}: duplicate or unknown layer {name!r}")
        if layer.get("state") != "available":
            raise ContractError(
                f"{scenario}: {name} state is {layer.get('state')!r}, not available")
        if layer.get("score_kind") != "continuous":
            raise ContractError(
                f"{scenario}: {name} score_kind must be continuous")
        score = layer.get("ranking_score")
        if not _is_number(score):
            raise ContractError(f"{scenario}: {name} ranking_score is missing or invalid")
        by_name[name] = layer
    if set(by_name) != set(LAYERS):
        raise ContractError(f"{scenario}: five layer reports are required")
    return by_name


def collect(exports_dir: Path, case_labels: dict) -> dict[str, dict]:
    """Map scenario_id -> layer report, refusing any silent widening or loss."""
    exports_dir = Path(exports_dir)
    if not exports_dir.is_dir():
        raise ContractError(f"exports directory does not exist: {exports_dir}")
    files = sorted(exports_dir.rglob("*.json"))
    if not files:
        raise ContractError(f"no ablation exports under {exports_dir}")
    collected: dict[str, dict] = {}
    for path in files:
        scenario = path.stem
        if scenario not in case_labels:
            raise ContractError(f"{path.name}: scenario is not in the release labels")
        if scenario in collected:
            raise ContractError(f"{scenario}: duplicate ablation export")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ContractError(f"{path.name}: unreadable export ({exc})") from exc
        collected[scenario] = _require_run(payload, scenario)
    return collected


def _auc(scores: np.ndarray, labels: np.ndarray) -> float:
    return float(ablation_metrics.auc_roc(scores, labels))


def _bootstrap_indices(n: int, resamples: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, n, size=(resamples, n))


def _percentile_interval(values: list[float]) -> list[float]:
    if not values:
        raise ContractError("no valid bootstrap resample")
    low, high = np.percentile(values, [2.5, 97.5])
    return [float(low), float(high)]


def _holm(pvalues: dict[str, float]) -> dict[str, float]:
    names = sorted(pvalues, key=lambda name: pvalues[name])
    count = len(names)
    adjusted, running = {}, 0.0
    for index, name in enumerate(names):
        running = max(running, min(1.0, pvalues[name] * (count - index)))
        adjusted[name] = running
    return {name: float(adjusted[name]) for name in pvalues}


def build_metrics(exports: dict[str, dict], case_labels: dict, release_id: str,
                  *, resamples: int = BOOTSTRAP_RESAMPLES) -> dict:
    """Pure assembly: no file reads, no release validation."""
    if not isinstance(resamples, int) or resamples < 1000:
        raise ContractError("bootstrap resamples must be an integer >= 1000")
    expected = set(case_labels)
    missing = sorted(expected - set(exports))
    if missing:
        raise ContractError(f"missing ablation exports for {len(missing)} scenario(s): {missing[:5]}")
    extra = sorted(set(exports) - expected)
    if extra:
        raise ContractError(f"exports for unknown scenario(s): {extra[:5]}")
    label_values = {scenario: case_labels[scenario] for scenario in expected}
    if any(value not in ("attack", "benign") for value in label_values.values()):
        raise ContractError("release labels must be attack/benign")
    cohort = {
        "scenarios": len(label_values),
        "attack": sum(value == "attack" for value in label_values.values()),
        "benign": sum(value == "benign" for value in label_values.values()),
    }
    if cohort != EXPECTED_COHORT:
        raise ContractError(f"cohort {cohort} differs from the frozen 93-scenario cohort")

    for scenario, run in exports.items():
        _require_layers(run, scenario)
    scenarios = sorted(expected)
    labels = np.array([1 if label_values[s] == "attack" else 0 for s in scenarios], dtype=int)
    scores = {
        name: np.array([next(layer["ranking_score"] for layer in exports[s]["layers"]
                             if layer["layer"] == name)
                        for s in scenarios], dtype=float)
        for name in LAYERS
    }
    for name, values in scores.items():
        if not np.all(np.isfinite(values)):
            raise ContractError(f"{name}: non-finite ranking score")
        if len(np.unique(values)) < 2:
            raise ContractError(f"{name}: a constant score has no AUROC")

    point_auc = {name: _auc(values, labels) for name, values in scores.items()}
    point_auprc = {name: float(ablation_metrics.auc_pr(values, labels))
                   for name, values in scores.items()}
    indices = _bootstrap_indices(len(scenarios), resamples, BOOTSTRAP_SEED)
    bootstrap_auc = {name: [] for name in LAYERS}
    bootstrap_diff = {f"{curr}_vs_{prev}": [] for curr, prev in CONTRASTS}
    for index in indices:
        sample_labels = labels[index]
        if len(np.unique(sample_labels)) < 2:
            continue
        sample_auc = {name: _auc(values[index], sample_labels) for name, values in scores.items()}
        for name in LAYERS:
            bootstrap_auc[name].append(sample_auc[name])
        for curr, prev in CONTRASTS:
            bootstrap_diff[f"{curr}_vs_{prev}"].append(sample_auc[curr] - sample_auc[prev])
    if any(len(values) < resamples - 1 for values in bootstrap_auc.values()):
        raise ContractError("bootstrap produced too few two-class resamples")

    intervals = {f"{name}_auc": _percentile_interval(values)
                 for name, values in bootstrap_auc.items()}
    for name, values in bootstrap_diff.items():
        intervals[f"{name}_auc_diff"] = _percentile_interval(values)
    pvalues = {}
    for curr, prev in CONTRASTS:
        name = f"{curr}_vs_{prev}_auc"
        pvalues[name] = float(ablation_metrics.delong_test(
            scores[prev], scores[curr], labels)["p"])
    holm = _holm(pvalues)

    layers = {}
    for name in LAYERS:
        layers[name] = {
            "state": "available",
            "auc": point_auc[name],
            "auprc": point_auprc[name],
            "delta_vs_L0": point_auc[name] - point_auc["L0"],
            "score_kind": "continuous",
            "n": len(scenarios),
            "coverage": 1.0,
        }
    data = {
        "state": "available",
        "raw_reconstruction": True,
        "hard_sensitive_egress": False,
        "shared_thresholds": True,
        "layers": layers,
        "statistics": {
            "aggregation_unit": "scenario",
            "n_scenarios": len(scenarios),
            "bootstrap_resamples": resamples,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "confidence_level": CONFIDENCE_LEVEL,
            "confidence_intervals": intervals,
            "coverage": 1.0,
            "abstention": 0.0,
            "holm_family": [f"{curr}_vs_{prev}_auc" for curr, prev in CONTRASTS],
            "holm_adjusted_p": holm,
        },
    }
    return {
        "schema": METRICS_SCHEMA,
        "experiment": "ablation",
        "release_id": release_id,
        "estimable": True,
        "cohort": cohort,
        "data": data,
        "limitations": list(LIMITATIONS),
    }


def not_estimable(release_id: str, reason: str) -> dict:
    return {
        "schema": METRICS_SCHEMA,
        "experiment": "ablation",
        "release_id": release_id,
        "estimable": False,
        "not_estimable_reason": reason,
        "cohort": dict(EXPECTED_COHORT),
        "data": {"state": "unavailable", "reasons": [reason]},
        "limitations": list(LIMITATIONS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True,
                        help="current FuxiBench release root")
    parser.add_argument("--exports", type=Path, required=True,
                        help="directory of replay --ablation-output JSON files (file stem = scenario id)")
    parser.add_argument("--out", type=Path, required=True,
                        help="new metrics envelope path (must not exist)")
    parser.add_argument("--bootstrap", type=int, default=BOOTSTRAP_RESAMPLES)
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        print(f"error: refusing to overwrite existing metrics file: {out}", file=sys.stderr)
        return 2
    try:
        release = validate_release(Path(args.release))
        release_id = release["release_id"]
        case_labels = release["case_labels"]
    except (OSError, ValueError) as exc:
        print(f"error: release validation failed: {exc}", file=sys.stderr)
        return 2
    try:
        exports = collect(Path(args.exports), case_labels)
        envelope = build_metrics(exports, case_labels, release_id, resamples=args.bootstrap)
    except ContractError as exc:
        envelope = not_estimable(release_id, str(exc))
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with out.open("x", encoding="utf-8") as handle:
            handle.write(payload)
    except OSError as exc:
        print(f"error: cannot create {out}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"experiment": "ablation", "estimable": envelope["estimable"],
                      "out": str(out), "layers": sorted(
                          (envelope.get("data") or {}).get("layers") or {})},
                     ensure_ascii=False))
    return 0 if envelope["estimable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
