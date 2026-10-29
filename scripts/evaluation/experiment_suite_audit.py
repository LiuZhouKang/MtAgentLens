#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Audit and assemble the seven-experiment suite without inventing results.

The suite is fixed by ``docs/evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md``.
This module does not calculate experiment metrics.  It checks that every
experiment has the producer -> export -> reference -> metrics chain that the
protocol requires, validates the metric envelope and renders the five paper
tables.  Anything missing stays ``None`` / ``not_estimable``; a null is not a
zero and a binary arm is never turned into a continuous probability.

Manifest schema (``mtagentlens.experiment-suite-manifest/1``)::

    {
      "schema": "mtagentlens.experiment-suite-manifest/1",
      "release_id": "9c70...",
      "cohort": {"scenarios": 93, "attack": 58, "benign": 35},
      "artifacts": [
        {"id": "app-metrics", "experiment": "app", "stage": "metrics",
         "path": "metrics/app.json", "sha256": "...",
         "schema": "mtagentlens.experiment-metrics/1"}
      ]
    }

Artifact paths are relative to the manifest directory.  The metrics artifact
carries experiment-specific data and must use
``mtagentlens.experiment-metrics/1``; its ``experiment`` and ``release_id``
must agree with the manifest.  Every other stage is an opaque, hash-bound
JSON/evidence file, so producers that already exist can be registered without
rewriting their output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

MANIFEST_SCHEMA = "mtagentlens.experiment-suite-manifest/1"
PLAN_SCHEMA = "mtagentlens.experiment-suite-plan/1"
METRICS_SCHEMA = "mtagentlens.experiment-metrics/1"
AUDIT_SCHEMA = "mtagentlens.experiment-suite-audit/1"
TABLES_SCHEMA = "mtagentlens.experiment-suite-tables/1"

STAGES = ("producer", "export", "reference", "metrics")

# The protocol has one frozen 93-scenario denominator.  Refusing another cohort
# here avoids silently mixing the historical 84/87 releases into a main table.
EXPECTED_COHORT = {"scenarios": 93, "attack": 58, "benign": 35}

EXPERIMENTS = {
    "identity": {
        "table": "T5",
        "stages": ("producer", "export", "reference", "metrics"),
        "description": "five identity conditions against the AI-adjudicated reference",
    },
    "attribution": {
        "table": "T6",
        "stages": ("producer", "export", "reference", "metrics"),
        "description": "root/entry/chain against the detector-blind reference",
    },
    "app": {
        "table": "T4",
        "stages": ("producer", "export", "metrics"),
        "description": "M and R-Judge A2 (full application trace) baseline, binary verdict",
    },
    "overhead": {
        "table": "T8",
        "stages": ("producer", "export", "metrics"),
        "description": "C0-C3 resource, capture and alert-latency accounting",
    },
    "sensitivity": {
        "table": "T7",
        "stages": ("producer", "metrics"),
        "description": "threshold x gate-center grid on frozen Observer observations",
    },
    "pbc": {
        "table": "T7",
        "stages": ("producer", "metrics"),
        "description": "PBC strata with a raw-activity baseline",
    },
    "ablation": {
        "table": "T7",
        "stages": ("producer", "export", "metrics"),
        "description": "L0/L0.5/L1/L2/L3 raw-fact reconstruction",
    },
}

# Table cell names are fixed so a missing producer cannot silently change the
# schema of a paper table.  Values are pulled from the metrics envelope below.
TABLE_COLUMNS = {
    "T4": ("arm", "score_kind", "n", "auc", "auprc", "tpr", "fpr", "coverage",
           "all", "decided", "executed", "status", "disclosure"),
    "T5": ("condition", "units", "pairwise_precision", "pairwise_recall",
           "pairwise_f1", "b3", "false_merge", "false_split", "unattributed",
           "coverage", "status"),
    "T6": ("method", "root_at_1", "rootset_precision", "rootset_recall",
           "rootset_f1", "root_step_at_1", "entry_accuracy", "chain_precision",
           "chain_recall", "chain_f1", "abstain", "coverage", "root_at_3",
           "mrr", "status"),
    "T7": ("part", "row", "value", "status"),
    "T8": ("profile", "concurrency", "cpu_percent_p95", "rss_kib_max",
           "db_bytes_peak", "operation_denominator",
           "raw_transport_missing_fraction", "persisted_operation_missing_fraction",
           "capture_status", "event_to_claim_p95", "claim_to_alert_p95",
           "observer_tokens", "cost_usd", "status"),
}


class ManifestError(ValueError):
    """The manifest or an artifact violates the suite contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _number(value):
    return value if _is_number(value) else None


def _pick(mapping, *keys):
    value = mapping
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def plan() -> dict:
    """Return the frozen producer/export/reference/metrics/table contract."""
    return {
        "schema": PLAN_SCHEMA,
        "release_id": None,
        "cohort": dict(EXPECTED_COHORT),
        "experiments": {
            name: {
                "table": spec["table"],
                "required_stages": list(spec["stages"]),
                "description": spec["description"],
                "metrics_schema": METRICS_SCHEMA,
            }
            for name, spec in EXPERIMENTS.items()
        },
        "table_columns": {name: list(columns) for name, columns in TABLE_COLUMNS.items()},
        "rules": [
            "missing required stage => not_estimable; no value is inferred",
            "null remains null; null is not zero and not a negative label",
            "binary AppBench arms cannot carry AUROC/AUPRC",
            "controlled capture contributes only scoped operation fractions with a denominator and source evidence",
            "no result is mixed across release_id or cohort",
        ],
    }


def load_manifest(path: Path) -> dict:
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ManifestError(f"{path}: cannot read manifest: {error}") from error
    if not isinstance(value, dict):
        raise ManifestError(f"{path}: manifest must be an object")
    if value.get("schema") != MANIFEST_SCHEMA:
        raise ManifestError(f"{path}: unexpected manifest schema {value.get('schema')!r}")
    release_id = value.get("release_id")
    if not isinstance(release_id, str) or not release_id:
        raise ManifestError(f"{path}: release_id is required")
    cohort = value.get("cohort")
    if cohort is None:
        # The cohort is optional in the manifest, but it is never a free-form
        # claim: absent means the frozen 93-scenario protocol.
        value["cohort"] = dict(EXPECTED_COHORT)
    elif not isinstance(cohort, dict):
        raise ManifestError(f"{path}: cohort must be an object when present")
    else:
        for key, expected in EXPECTED_COHORT.items():
            if cohort.get(key) != expected:
                raise ManifestError(
                    f"{path}: cohort.{key}={cohort.get(key)!r}, this suite requires {expected}")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list):
        raise ManifestError(f"{path}: artifacts must be a list")
    return value


def _artifact_payload(path: Path, declared_schema: str | None) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict):
        raise ManifestError(f"{path}: JSON artifact must be an object")
    if declared_schema is not None and value.get("schema") != declared_schema:
        raise ManifestError(
            f"{path}: artifact schema {value.get('schema')!r} != declared {declared_schema!r}")
    return value


def _validate_artifact(entry, base: Path, release_id: str, seen: set) -> tuple[dict, dict | None]:
    if not isinstance(entry, dict):
        raise ManifestError("artifact entry must be an object")
    artifact_id = entry.get("id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ManifestError("artifact.id is required")
    if artifact_id in seen:
        raise ManifestError(f"duplicate artifact id: {artifact_id}")
    seen.add(artifact_id)
    experiment = entry.get("experiment")
    if experiment not in EXPERIMENTS:
        raise ManifestError(f"{artifact_id}: unknown experiment {experiment!r}")
    stage = entry.get("stage")
    if stage not in STAGES:
        raise ManifestError(f"{artifact_id}: unknown stage {stage!r}")
    raw_path = entry.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ManifestError(f"{artifact_id}: path is required")
    path = (base / raw_path).resolve() if not Path(raw_path).is_absolute() else Path(raw_path)
    if not path.is_file():
        raise ManifestError(f"{artifact_id}: artifact does not exist: {path}")
    expected = entry.get("sha256")
    if not isinstance(expected, str) or len(expected) != 64 or any(
            char not in "0123456789abcdef" for char in expected):
        raise ManifestError(f"{artifact_id}: a lowercase sha256 is required")
    actual = sha256_file(path)
    if actual != expected:
        raise ManifestError(f"{artifact_id}: sha256 mismatch ({actual} != {expected})")
    declared_schema = entry.get("schema")
    if declared_schema is not None and not isinstance(declared_schema, str):
        raise ManifestError(f"{artifact_id}: schema must be a string")
    payload = _artifact_payload(path, declared_schema) if path.suffix == ".json" else None
    if isinstance(payload, dict):
        artifact_release = payload.get("release_id")
        if artifact_release is not None and artifact_release != release_id:
            raise ManifestError(
                f"{artifact_id}: artifact release {artifact_release!r} != manifest {release_id!r}")
    record = dict(entry)
    record["path"] = str(path)
    record["sha256_actual"] = actual
    return record, payload


def _metrics_reasons(experiment: str, payload: dict, cohort: dict) -> tuple[str, list[str], str | None]:
    """Return (status, reasons, disclosure) for one metrics envelope."""
    if payload.get("schema") != METRICS_SCHEMA:
        return "invalid", [f"metrics schema must be {METRICS_SCHEMA}"], None
    if payload.get("experiment") != experiment:
        return "invalid", [f"metrics experiment {payload.get('experiment')!r} != {experiment!r}"], None
    if payload.get("estimable") is False:
        reason = payload.get("not_estimable_reason") or "producer marked the experiment not estimable"
        return "not_estimable", [str(reason)], None
    data = payload.get("data")
    if not isinstance(data, dict):
        return "not_estimable", ["metrics envelope has no data object"], None
    if "cohort" in payload:
        payload_cohort = payload["cohort"]
        if not isinstance(payload_cohort, dict):
            return "invalid", ["metrics cohort must be an object when present"], None
        for key, expected in EXPECTED_COHORT.items():
            if payload_cohort.get(key) != expected:
                return "invalid", [
                    f"metrics cohort.{key}={payload_cohort.get(key)!r}, expected {expected}"], None

    validators = {
        "app": _audit_app,
        "identity": _audit_identity,
        "attribution": _audit_attribution,
        "overhead": _audit_overhead,
        "sensitivity": _audit_sensitivity,
        "pbc": _audit_pbc,
        "ablation": _audit_ablation,
    }
    status, reasons, disclosure = validators[experiment](data, cohort)
    if status == "estimable" and payload.get("limitations"):
        reasons.extend(str(item) for item in payload["limitations"])
    return status, reasons, disclosure


def _audit_statistics(data: dict, *, unit: str, require_holm: bool,
                      require_reference_error: bool,
                      require_coverage: bool) -> list[str]:
    """Check the fixed inference contract, never the point estimate.

    The unit must be the independent assignment (scenario or block), never a
    message/event/action inside one scenario. Holm families and intervals are
    declared before looking at results, so a missing or partial envelope makes
    the inferential claim not estimable rather than silently unit-weighted.
    """
    stats = data.get("statistics")
    if not isinstance(stats, dict):
        return ["statistics envelope is missing (aggregation/CI/Holm/coverage)"]
    reasons: list[str] = []
    if stats.get("aggregation_unit") != unit:
        reasons.append(
            f"statistics.aggregation_unit must be {unit!r}, got "
            f"{stats.get('aggregation_unit')!r}")
    n_key = "n_scenarios" if unit == "scenario" else "n_blocks"
    n = stats.get(n_key)
    if not _is_count(n) or n < 1:
        reasons.append(f"statistics.{n_key} must be a positive integer")
    resamples = stats.get("bootstrap_resamples")
    if not _is_count(resamples) or resamples < 1000:
        reasons.append("statistics.bootstrap_resamples must be >= 1000")
    level = stats.get("confidence_level")
    if not _is_number(level) or not 0.0 < float(level) < 1.0:
        reasons.append("statistics.confidence_level must be within (0, 1)")
    intervals = stats.get("confidence_intervals")
    if not isinstance(intervals, dict) or not intervals:
        reasons.append("statistics.confidence_intervals is missing")
    else:
        for name, interval in intervals.items():
            if (not isinstance(name, str) or not name
                    or not isinstance(interval, (list, tuple)) or len(interval) != 2
                    or not all(_is_number(item) for item in interval)
                    or float(interval[0]) > float(interval[1])):
                reasons.append(f"statistics.confidence_intervals[{name!r}] is invalid")
    if require_coverage:
        coverage = stats.get("coverage")
        abstention = stats.get("abstention")
        if not _is_number(coverage) or not 0.0 <= float(coverage) <= 1.0:
            reasons.append("statistics.coverage must be within [0, 1]")
        if not _is_number(abstention) or not 0.0 <= float(abstention) <= 1.0:
            reasons.append("statistics.abstention must be within [0, 1]")
    if require_holm:
        family = stats.get("holm_family")
        adjusted = stats.get("holm_adjusted_p")
        if (not isinstance(family, list) or not family
                or any(not isinstance(name, str) or not name for name in family)
                or len(set(family)) != len(family)):
            reasons.append("statistics.holm_family must be a non-empty unique string list")
        elif not isinstance(adjusted, dict) or set(adjusted) != set(family):
            reasons.append(
                "statistics.holm_adjusted_p must cover exactly the fixed Holm family")
        elif any(not _is_number(value) or not 0.0 <= float(value) <= 1.0
                 for value in adjusted.values()):
            reasons.append("statistics.holm_adjusted_p values must be within [0, 1]")
    if require_reference_error:
        sensitivity = stats.get("reference_error_sensitivity")
        if not isinstance(sensitivity, dict):
            reasons.append(
                "statistics.reference_error_sensitivity is missing; reference error bounds "
                "are required for identity/attribution claims")
        else:
            for level_key in ("0.10", "0.20"):
                bounds = sensitivity.get(level_key)
                if (not isinstance(bounds, dict)
                        or not _is_number(bounds.get("lower"))
                        or not _is_number(bounds.get("upper"))
                        or float(bounds["lower"]) > float(bounds["upper"])):
                    reasons.append(
                        f"statistics.reference_error_sensitivity[{level_key!r}] is missing or invalid")
    return reasons


def _audit_app(data: dict, cohort: dict):
    arms = data.get("arms")
    if not isinstance(arms, dict):
        return "not_estimable", ["app.metrics.data.arms is missing"], None
    required = ("M", "A2")
    missing = [arm for arm in required if not isinstance(arms.get(arm), dict)]
    if missing:
        return "not_estimable", [f"app arm missing: {', '.join(missing)}"], None
    cohort_scenarios = cohort.get("scenarios") if isinstance(cohort, dict) else None
    if cohort_scenarios != EXPECTED_COHORT["scenarios"]:
        return "invalid", [
            f"app cohort.scenarios={cohort_scenarios!r}, expected "
            f"{EXPECTED_COHORT['scenarios']}"], None
    reasons, disclosure = [], None
    all_continuous = True
    for arm in required:
        spec = arms[arm]
        kind = spec.get("score_kind")
        if kind not in ("continuous", "binary"):
            reasons.append(f"{arm}: score_kind must be continuous or binary, got {kind!r}")
            all_continuous = False
            continue
        if kind == "binary":
            all_continuous = False
            if spec.get("auc") is not None or spec.get("auprc") is not None:
                return "invalid", [
                    f"{arm}: a binary arm must not report AUROC/AUPRC"], None
            disclosure = ("binary-only arm(s): AUROC/AUPRC are not estimable; "
                          "TPR/FPR are threshold-dependent binary rates, not probabilities")
        # Counts are counts: bool is not an int here, and every denominator must
        # bind to the frozen 93-scenario cohort.
        total = spec.get("all")
        if not _is_count(total) or total != cohort_scenarios:
            return "invalid", [
                f"{arm}: all={total!r} must be the integer {cohort_scenarios}"], disclosure
        n = spec.get("n")
        if not _is_count(n) or n < 1:
            reasons.append(f"{arm}: n must be a positive integer")
        elif n > total:
            return "invalid", [f"{arm}: n={n} exceeds all={total}"], disclosure
        for key in ("decided", "executed"):
            if spec.get(key) is None:
                continue
            value = spec.get(key)
            if not _is_count(value) or value > total:
                return "invalid", [
                    f"{arm}: {key}={value!r} must be a non-negative integer <= all={total}"], disclosure
        coverage = spec.get("coverage")
        if coverage is not None:
            if not _is_number(coverage) or not 0.0 <= float(coverage) <= 1.0:
                return "invalid", [
                    f"{arm}: coverage must be a finite number within [0, 1]"], disclosure
            if _is_count(n) and n <= total and abs(float(coverage) - n / total) > 1e-9:
                return "invalid", [
                    f"{arm}: coverage={coverage!r} contradicts n/all={n}/{total}"], disclosure
        for key in ("auc", "auprc", "tpr", "fpr"):
            if spec.get(key) is None:
                continue
            value = spec.get(key)
            if not _is_number(value) or not 0.0 <= float(value) <= 1.0:
                return "invalid", [
                    f"{arm}: {key}={value!r} must be a finite number within [0, 1]"], disclosure
        if kind == "binary":
            if not _is_number(spec.get("tpr")) or not _is_number(spec.get("fpr")):
                reasons.append(f"{arm}: binary arm needs TPR/FPR, not fabricated scores")
        elif not _is_number(spec.get("auc")) or not _is_number(spec.get("auprc")):
            all_continuous = False
            reasons.append(f"{arm}: continuous arm is missing numeric AUROC/AUPRC")
    transcript = data.get("transcript_coverage")
    if _is_number(transcript):
        if not 0.0 <= float(transcript) <= 1.0:
            return "invalid", ["transcript_coverage must be within [0, 1]"], disclosure
        if float(transcript) < 1.0:
            reasons.append(f"transcript coverage is {transcript:.4f}; uncovered runs are not evidence")
    else:
        reasons.append("transcript_coverage is missing; coverage is not reported")
    stats = data.get("statistics")
    if isinstance(stats, dict) and "n_scenarios" in stats:
        if stats["n_scenarios"] != cohort_scenarios:
            return "invalid", reasons + [
                f"app statistics.n_scenarios={stats['n_scenarios']!r} must bind to "
                f"the {cohort_scenarios}-scenario cohort"], disclosure
    stats_reasons = _audit_statistics(
        data, unit="scenario", require_holm=True,
        require_reference_error=False, require_coverage=True)
    if stats_reasons:
        return "not_estimable", reasons + stats_reasons, disclosure
    status = "estimable" if not reasons else "partial"
    if not all_continuous:
        status = "partial"
    return status, reasons, disclosure


def _audit_identity(data: dict, cohort: dict):
    conditions = data.get("conditions")
    if not isinstance(conditions, dict):
        return "not_estimable", ["identity.metrics.data.conditions is missing"], None
    required = ("C0", "C1", "C2", "C3", "C4")
    missing = [name for name in required if not isinstance(conditions.get(name), dict)]
    if missing:
        return "not_estimable", [f"identity condition missing: {', '.join(missing)}"], None
    reasons = []
    for name in required:
        row = conditions[name]
        units = row.get("units")
        if not _is_count(units) or units < 1:
            reasons.append(f"{name}: units must be a positive integer")
        coverage = row.get("coverage")
        if not _is_number(coverage) or not 0.0 <= coverage <= 1.0:
            reasons.append(f"{name}: coverage is missing or outside [0, 1]")
        if not _is_number(row.get("b3")) or not _is_number(row.get("pairwise_f1")):
            reasons.append(f"{name}: B3/pairwise metrics are missing")
    ref = data.get("reference") if isinstance(data.get("reference"), dict) else {}
    reference_units = ref.get("units")
    reference_coverage = ref.get("coverage")
    if not _is_count(reference_units) or reference_units < 1:
        reasons.append("reference.units must be a positive integer")
    if not _is_number(reference_coverage) or not 0.0 <= reference_coverage <= 1.0:
        reasons.append("reference.coverage is missing or outside [0, 1]")
    reasons.extend(_audit_statistics(
        data, unit="scenario", require_holm=True,
        require_reference_error=True, require_coverage=True))
    return ("estimable" if not reasons else "not_estimable"), reasons, None


def _audit_attribution(data: dict, cohort: dict):
    methods = data.get("methods")
    if not isinstance(methods, dict):
        return "not_estimable", ["attribution.metrics.data.methods is missing"], None
    required = ("B0", "B1", "B2", "M")
    missing = [name for name in required if not isinstance(methods.get(name), dict)]
    if missing:
        return "not_estimable", [f"attribution method missing: {', '.join(missing)}"], None
    executed = data.get("n_attack_executed")
    if not _is_count(executed) or executed < 1:
        return "not_estimable", ["n_attack_executed must be a positive integer"], None
    coverage = data.get("reference_coverage")
    if not _is_number(coverage) or not 0.0 < coverage <= 1.0:
        return "not_estimable", ["reference_coverage must be within (0, 1]"], None
    ranked = data.get("ranked_agents") is True
    for name in required:
        row = methods[name]
        # The reference adapter keeps the historical `root_agent_at_1` /
        # `root_set_*` names; the paper table uses `root_at_1` / `rootset_*`.
        # Accept either spelling, but never invent a value.
        root_at_1 = row.get("root_at_1", row.get("root_agent_at_1"))
        rootset_f1 = row.get("rootset_f1", row.get("root_set_f1"))
        root_at_3 = row.get("root_at_3", row.get("root_agent_at_3"))
        if not _is_number(root_at_1) or not _is_number(rootset_f1):
            return "not_estimable", [f"{name}: Root@1/RootSet metrics are missing"], None
        if not ranked:
            if root_at_3 is not None or row.get("mrr") is not None:
                return "invalid", [
                    f"{name}: @3/MRR require an explicit ranked_agents=true contract"], None
        elif not (_is_number(root_at_3) or _is_number(row.get("mrr"))):
            return "not_estimable", [f"{name}: ranked_agents is true but @3/MRR is missing"], None
    reasons = []
    if coverage < 1.0:
        reasons.append(f"reference coverage is {coverage:.4f}; uncovered units are not scored")
    stats_reasons = _audit_statistics(
        data, unit="scenario", require_holm=True,
        require_reference_error=True, require_coverage=True)
    if stats_reasons:
        return "not_estimable", reasons + stats_reasons, None
    return ("estimable" if not reasons else "partial"), reasons, None


def _audit_overhead(data: dict, cohort: dict):
    conditions = data.get("conditions")
    if not isinstance(conditions, dict):
        return "not_estimable", ["overhead.metrics.data.conditions is missing"], None
    required = ("C0", "C1", "C2", "C3")
    missing = [name for name in required if not isinstance(conditions.get(name), dict)]
    if missing:
        return "not_estimable", [f"overhead profile missing: {', '.join(missing)}"], None
    capture = data.get("controlled_capture")
    if not isinstance(capture, dict):
        return "not_estimable", ["controlled_capture adapter evidence is missing"], None
    reasons = []
    for name in required:
        profiles = conditions[name].get("concurrency")
        if not isinstance(profiles, dict) or not profiles:
            reasons.append(f"{name}: per-concurrency metrics are missing")
            continue
        if not any(
            _is_number(spec.get(key))
            for spec in profiles.values() if isinstance(spec, dict)
            for key in ("cpu_percent_p95", "rss_kib_max", "db_bytes_peak")
        ):
            reasons.append(f"{name}: no numeric resource series")
    for name in required:
        spec = capture.get(name)
        if not isinstance(spec, dict):
            return "not_estimable", [f"{name}: controlled capture evidence is missing"], None
        status = spec.get("status")
        if name == "C0":
            if status != "not_applicable":
                return "invalid", ["C0 controlled capture must be not_applicable"], None
            if (spec.get("raw_transport_missing_fraction") is not None
                    or spec.get("persisted_operation_missing_fraction") is not None):
                return "invalid", ["C0 must not report a capture fraction"], None
            continue
        if status == "not_estimable":
            if (spec.get("raw_transport_missing_fraction") is not None
                    or spec.get("persisted_operation_missing_fraction") is not None):
                return "invalid", [
                    f"{name}: not_estimable capture must not carry a fraction"], None
            reasons.append(
                f"{name}: controlled capture is not estimable: "
                + "; ".join(spec.get("reasons") or ["no reason recorded"]))
            continue
        if status != "measured":
            return "invalid", [f"{name}: unsupported capture status {status!r}"], None
        denominator = spec.get("operation_denominator")
        if not _is_count(denominator) or denominator < 1:
            return "invalid", [
                f"{name}: measured capture fraction has no operation denominator"], None
        raw = spec.get("raw_transport_missing_fraction")
        if not _is_number(raw) or not 0 <= raw <= 1:
            return "invalid", [
                f"{name}: raw_transport_missing_fraction is not a measured rate"], None
        persisted = spec.get("persisted_operation_missing_fraction")
        if name == "C1":
            if persisted is not None:
                return "invalid", [
                    "C1: persisted_operation_missing_fraction must be null"], None
        elif not _is_number(persisted) or not 0 <= persisted <= 1:
            return "invalid", [
                f"{name}: persisted_operation_missing_fraction is not a measured rate"], None
        evidence = spec.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            return "invalid", [
                f"{name}: measured capture has no artifact/source evidence"], None
        for item in evidence:
            if not isinstance(item, dict):
                return "invalid", [f"{name}: malformed capture evidence"], None
            if item.get("adapter_version") != "controlled_file_open/v1":
                return "invalid", [f"{name}: capture adapter version is unsupported"], None
            if not all(isinstance(item.get(key), str) and len(item[key]) == 64
                       for key in ("receipt_combined_sha256", "trace_sha256")):
                return "invalid", [f"{name}: capture artifact hashes are incomplete"], None
            if name in ("C2", "C3") and not (
                    isinstance(item.get("database_sha256"), str)
                    and len(item["database_sha256"]) == 64):
                return "invalid", [f"{name}: capture database hash is missing"], None
    provider = _pick(conditions, "C3", "observer_provider")
    if provider == "mock":
        reasons.append("C3 Observer provider is mock, not a real provider")
    stats_reasons = _audit_statistics(
        data, unit="block", require_holm=False,
        require_reference_error=False, require_coverage=True)
    if stats_reasons:
        return "not_estimable", reasons + stats_reasons, None
    status = "partial" if reasons else "estimable"
    return status, reasons, None


def _audit_sensitivity(data: dict, cohort: dict):
    grid = data.get("grid")
    if not isinstance(grid, list) or len(grid) < 15:
        return "not_estimable", ["sensitivity grid must contain at least 15 points"], None
    if data.get("points") != len(grid):
        return "not_estimable", [f"sensitivity points must equal grid size ({len(grid)})"], None
    resamples = data.get("bootstrap_resamples")
    if not _is_count(resamples) or resamples < 1000:
        return "not_estimable", ["bootstrap_resamples must be >= 1000"], None
    if data.get("frozen_observer") is not True:
        return "not_estimable", ["frozen_observer must be true for the main sensitivity table"], None
    reasons = []
    if data.get("live_observer_runs"):
        reasons.append("live Observer sub-sample is reported separately; it is not the frozen main table")
    stats_reasons = _audit_statistics(
        data, unit="scenario", require_holm=False,
        require_reference_error=False, require_coverage=True)
    if stats_reasons:
        return "not_estimable", reasons + stats_reasons, None
    return ("estimable" if not reasons else "partial"), reasons, None


def _audit_pbc(data: dict, cohort: dict):
    if data.get("raw_activity_baseline") is not True:
        return "not_estimable", ["raw_activity_baseline must be true; PBC is detector-derived"], None
    if data.get("pbc_detector_derived") is not True:
        return "not_estimable", ["pbc_detector_derived must be true"], None
    if data.get("tie_aware") is not True:
        return "not_estimable", ["tie_aware metrics are required for the zero-inflated PBC strata"], None
    strata = data.get("strata")
    if not isinstance(strata, dict) or not strata:
        return "not_estimable", ["strata are missing"], None
    if not isinstance(data.get("distribution"), dict):
        return "not_estimable", ["PBC distribution is missing"], None
    stats_reasons = _audit_statistics(
        data, unit="scenario", require_holm=False,
        require_reference_error=False, require_coverage=True)
    if stats_reasons:
        return "not_estimable", stats_reasons, None
    return "estimable", [], None


def _audit_ablation(data: dict, cohort: dict):
    layers = data.get("layers")
    if not isinstance(layers, dict):
        return "not_estimable", ["ablation.metrics.data.layers is missing"], None
    top_state = data.get("state") if data.get("state") is not None else data.get("status")
    if top_state is not None and top_state != "available":
        return "not_estimable", [
            f"ablation payload state={top_state!r} is not available"], None
    if "raw_reconstruction" in data and data.get("raw_reconstruction") is not True:
        return "not_estimable", [
            "ablation payload is missing available raw reconstruction"], None
    required = ("L0", "L0.5", "L1", "L2", "L3")
    missing = [name for name in required if not isinstance(layers.get(name), dict)]
    if missing:
        return "not_estimable", [f"ablation layer missing: {', '.join(missing)}"], None
    if data.get("hard_sensitive_egress") is not False:
        return "not_estimable", ["hard_sensitive_egress must be false for layer contribution"], None
    if data.get("shared_thresholds") is not True:
        return "not_estimable", ["all layers must use the same frozen thresholds/gate"], None
    reasons = []
    for name in required:
        row = layers[name]
        state = row.get("state") if row.get("state") is not None else row.get("status")
        if state is not None and state != "available":
            reasons.append(f"{name}: state={state!r} is not available")
            continue
        if "raw_reconstruction" in row and row.get("raw_reconstruction") is not True:
            reasons.append(f"{name}: raw reconstruction is unavailable")
            continue
        if row.get("available") is False:
            reasons.append(f"{name}: layer is marked unavailable")
            continue
        score = row.get("auc")
        if not _is_number(score):
            reasons.append(f"{name}: numeric AUROC is missing; a per-run score is not AUROC")
            continue
        if not 0.0 <= float(score) <= 1.0:
            reasons.append(f"{name}: score must be within [0, 1]")
        if row.get("score_kind") not in (None, "continuous"):
            reasons.append(f"{name}: score_kind must be continuous for AUROC")
    reasons.extend(_audit_statistics(
        data, unit="scenario", require_holm=True,
        require_reference_error=False, require_coverage=True))
    return ("estimable" if not reasons else "not_estimable"), reasons, None


def audit_manifest(manifest: dict, *, base_dir: Path | None = None) -> dict:
    if base_dir is None:
        base_dir = Path.cwd()
    release_id = manifest["release_id"]
    cohort = manifest["cohort"]
    artifacts = manifest["artifacts"]
    seen: set[str] = set()
    by_experiment = {name: {stage: [] for stage in STAGES} for name in EXPERIMENTS}
    errors = []
    for entry in artifacts:
        # A missing file, changed hash or foreign release poisons the whole
        # manifest; do not render tables from untrustworthy evidence.
        record, payload = _validate_artifact(entry, Path(base_dir), release_id, seen)
        by_experiment[record["experiment"]][record["stage"]].append(
            {"artifact": record, "payload": payload})

    results = {}
    for name, spec in EXPERIMENTS.items():
        stages = by_experiment[name]
        missing = [stage for stage in spec["stages"] if not stages[stage]]
        metrics_rows = stages["metrics"]
        metrics_payload = metrics_rows[0]["payload"] if metrics_rows else None
        reasons: list[str] = []
        disclosure = None
        status = "estimable"
        if missing:
            status = "not_estimable"
            reasons = [f"missing required stage: {stage}" for stage in missing]
        elif len(metrics_rows) > 1:
            status = "invalid"
            reasons = ["exactly one metrics artifact is required per experiment"]
        elif metrics_payload is None:
            status = "invalid"
            reasons = ["metrics artifact is not a JSON object"]
        else:
            status, reasons, disclosure = _metrics_reasons(name, metrics_payload, cohort)
        if status == "invalid":
            errors.extend(f"{name}: {reason}" for reason in reasons)
        results[name] = {
            "table": spec["table"],
            "status": status,
            "estimable": status in ("estimable", "partial"),
            "primary_endpoint_estimable": status == "estimable",
            "missing_stages": missing,
            "reasons": reasons,
            "disclosure": disclosure,
            "stages": {
                stage: {
                    "status": "present" if rows else ("missing" if stage in spec["stages"] else "not_required"),
                    "artifacts": [
                        {"id": row["artifact"]["id"], "path": row["artifact"]["path"],
                         "sha256": row["artifact"]["sha256_actual"]}
                        for row in rows
                    ],
                }
                for stage, rows in stages.items()
            },
            "metrics": metrics_payload,
        }

    all_primary = all(result["primary_endpoint_estimable"] for result in results.values())
    audit = {
        "schema": AUDIT_SCHEMA,
        "release_id": release_id,
        "cohort": dict(cohort),
        # `valid` is structural integrity only: an empty manifest is a valid
        # structural report even though no scientific endpoint is estimable.
        "valid": not errors,
        "errors": errors,
        "all_primary_endpoints_estimable": all_primary,
        "primary_endpoints_estimable": all_primary,
        "scientific_results_verified": False,
        "structural_only": True,
        "experiments": results,
        "scientific_boundary": (
            "This is a structural audit: it proves artifact presence, integrity "
            "and protocol shape only. "
            "It does not recompute producer results, so hashes, confidence intervals "
            "and Holm values do not prove the reported calculations. "
            "scientific_results_verified is always false. "
            "Null means not estimable; it is never zero or a negative label."
        ),
    }
    audit["tables"] = assemble_tables(audit)
    return audit


ROW_LABELS = {"arm", "condition", "method", "profile", "concurrency", "part", "row",
              "score_kind", "status", "disclosure"}


def _row(experiment_result: dict, fields: dict, *, status: str | None = None,
         disclosure: str | None = None) -> dict:
    estimable = experiment_result["estimable"]
    row = {
        key: (value if estimable or key in ROW_LABELS else None)
        for key, value in fields.items()
    }
    row["status"] = status or ("estimable" if experiment_result["status"] == "estimable"
                               else experiment_result["status"])
    if disclosure:
        row["disclosure"] = disclosure
    return row


def assemble_tables(audit: dict) -> dict:
    """Render T4/T5/T6/T7/T8 from audit envelopes; never promote null to zero."""
    experiments = audit["experiments"]

    def app_rows():
        result = experiments["app"]
        arms = _pick(result, "metrics", "data", "arms") or {}
        rows = []
        for arm in ("M", "A2"):
            spec = arms.get(arm) if isinstance(arms, dict) else None
            if not isinstance(spec, dict):
                rows.append({column: (arm if column == "arm" else None)
                             for column in TABLE_COLUMNS["T4"]} | {"status": "not_estimable"})
                continue
            binary = spec.get("score_kind") == "binary"
            fields = {
                "arm": arm, "score_kind": spec.get("score_kind"), "n": spec.get("n"),
                "auc": None if binary else _number(spec.get("auc")),
                "auprc": None if binary else _number(spec.get("auprc")),
                "tpr": _number(spec.get("tpr")), "fpr": _number(spec.get("fpr")),
                "coverage": _number(spec.get("coverage")),
                "all": spec.get("all"), "decided": spec.get("decided"),
                "executed": spec.get("executed"),
            }
            rows.append(_row(result, fields,
                             status="binary_only_auc_not_estimable" if binary else None,
                             disclosure=("binary output: AUROC/AUPRC not estimable; "
                                         "TPR/FPR are threshold-dependent") if binary else None))
        return rows

    def identity_rows():
        result = experiments["identity"]
        conditions = _pick(result, "metrics", "data", "conditions") or {}
        rows = []
        for name in ("C0", "C1", "C2", "C3", "C4"):
            spec = conditions.get(name) if isinstance(conditions, dict) else None
            if not isinstance(spec, dict):
                rows.append({column: (name if column == "condition" else None)
                             for column in TABLE_COLUMNS["T5"]} | {"status": "not_estimable"})
                continue
            fields = {
                "condition": name, "units": spec.get("units"),
                "pairwise_precision": _number(spec.get("pairwise_precision")),
                "pairwise_recall": _number(spec.get("pairwise_recall")),
                "pairwise_f1": _number(spec.get("pairwise_f1")),
                "b3": _number(spec.get("b3")),
                "false_merge": _number(spec.get("false_merge")),
                "false_split": _number(spec.get("false_split")),
                "unattributed": _number(spec.get("unattributed")),
                "coverage": _number(spec.get("coverage")),
            }
            rows.append(_row(result, fields))
        return rows

    def attribution_rows():
        result = experiments["attribution"]
        methods = _pick(result, "metrics", "data", "methods") or {}
        ranked = bool(_pick(result, "metrics", "data", "ranked_agents"))
        rows = []
        for name in ("B0", "B1", "B2", "M", "M-entry-off", "M-taint-off"):
            spec = methods.get(name) if isinstance(methods, dict) else None
            if not isinstance(spec, dict):
                if name.startswith("M-"):
                    continue
                rows.append({column: (name if column == "method" else None)
                             for column in TABLE_COLUMNS["T6"]} | {"status": "not_estimable"})
                continue
            fields = {
                "method": name,
                "root_at_1": _number(spec.get("root_at_1",
                                              spec.get("root_agent_at_1"))),
                "rootset_precision": _number(spec.get("rootset_precision",
                                                      spec.get("root_set_precision"))),
                "rootset_recall": _number(spec.get("rootset_recall",
                                                   spec.get("root_set_recall"))),
                "rootset_f1": _number(spec.get("rootset_f1",
                                               spec.get("root_set_f1"))),
                "root_step_at_1": _number(spec.get("root_step_at_1")),
                "entry_accuracy": _number(spec.get("entry_accuracy",
                                                   spec.get("entry_point_accuracy"))),
                "chain_precision": _number(spec.get("chain_precision",
                                                    spec.get("untyped_chain_precision"))),
                "chain_recall": _number(spec.get("chain_recall",
                                                 spec.get("untyped_chain_recall"))),
                "chain_f1": _number(spec.get("chain_f1",
                                             spec.get("untyped_chain_f1"))),
                "abstain": _number(spec.get("abstain")),
                "coverage": _number(spec.get("coverage")),
                "root_at_3": (_number(spec.get("root_at_3",
                                               spec.get("root_agent_at_3")))
                              if ranked else None),
                "mrr": _number(spec.get("mrr")) if ranked else None,
            }
            rows.append(_row(result, fields,
                             status=None if ranked else "unranked_rank3_mrr_not_reported"))
        return rows

    def overhead_rows():
        result = experiments["overhead"]
        overhead_data = _pick(result, "metrics", "data") or {}
        conditions = overhead_data.get("conditions") or {}
        capture = overhead_data.get("controlled_capture")
        capture = capture if isinstance(capture, dict) else {}
        rows = []
        for profile in ("C0", "C1", "C2", "C3"):
            profile_data = conditions.get(profile) if isinstance(conditions, dict) else None
            if not isinstance(profile_data, dict):
                rows.append({column: (profile if column == "profile" else None)
                             for column in TABLE_COLUMNS["T8"]} | {"status": "not_estimable"})
                continue
            levels = profile_data.get("concurrency")
            if not isinstance(levels, dict) or not levels:
                rows.append({column: (profile if column == "profile" else None)
                             for column in TABLE_COLUMNS["T8"]} | {"status": "not_estimable"})
                continue
            capture_profile = capture.get(profile)
            capture_profile = (
                capture_profile if isinstance(capture_profile, dict) else {})
            capture_status = capture_profile.get("status", "not_estimable")
            for concurrency, spec in sorted(levels.items(), key=lambda item: str(item[0])):
                spec = spec if isinstance(spec, dict) else {}
                fields = {
                    "profile": profile, "concurrency": concurrency,
                    "cpu_percent_p95": _number(spec.get("cpu_percent_p95")),
                    "rss_kib_max": _number(spec.get("rss_kib_max")),
                    "db_bytes_peak": _number(spec.get("db_bytes_peak")),
                    "operation_denominator": capture_profile.get(
                        "operation_denominator"),
                    "raw_transport_missing_fraction": _number(
                        capture_profile.get("raw_transport_missing_fraction")),
                    "persisted_operation_missing_fraction": _number(
                        capture_profile.get("persisted_operation_missing_fraction")),
                    "capture_status": capture_status,
                    "event_to_claim_p95": _number(spec.get("event_to_claim_p95")),
                    "claim_to_alert_p95": _number(spec.get("claim_to_alert_p95")),
                    "observer_tokens": _number(spec.get("observer_tokens")),
                    "cost_usd": _number(spec.get("cost_usd")),
                }
                rows.append(_row(
                    result, fields,
                    status=("not_applicable" if capture_status == "not_applicable"
                            else ("estimable" if capture_status == "measured"
                                  else "capture_not_estimable"))))
        return rows

    def t7_rows():
        rows = []
        pbc = experiments["pbc"]
        pbc_data = _pick(pbc, "metrics", "data") or {}
        if pbc["estimable"]:
            rows.append(_row(pbc, {
                "part": "PBC", "row": "distribution",
                "value": json.dumps(pbc_data.get("distribution"), sort_keys=True),
            }))
            for name, spec in (pbc_data.get("strata") or {}).items():
                rows.append(_row(pbc, {"part": "PBC", "row": str(name),
                                       "value": json.dumps(spec, sort_keys=True)}))
        else:
            rows.append(_row(pbc, {"part": "PBC", "row": "distribution", "value": None}))
        ablation = experiments["ablation"]
        layers = _pick(ablation, "metrics", "data", "layers") or {}
        for name in ("L0", "L0.5", "L1", "L2", "L3"):
            spec = layers.get(name) if isinstance(layers, dict) else None
            if not isinstance(spec, dict):
                rows.append({"part": "Ablation", "row": name, "value": None,
                             "status": "not_estimable"})
                continue
            rows.append(_row(ablation, {
                "part": "Ablation", "row": name,
                "value": json.dumps({key: spec.get(key) for key in
                                     ("auc", "tpr", "fpr", "coverage", "delta_vs_L0")},
                                    sort_keys=True),
            }))
        sensitivity = experiments["sensitivity"]
        sensitivity_data = _pick(sensitivity, "metrics", "data") or {}
        # The full 245-point grid lives in the metrics JSON; the audit table
        # shows only the three interpretable anchors.
        anchors = [name for name in ("baseline", "best", "worst")
                   if isinstance(sensitivity_data, dict) and isinstance(sensitivity_data.get(name), dict)]
        if anchors:
            for name in anchors:
                rows.append(_row(sensitivity, {
                    "part": "Sensitivity", "row": name,
                    "value": json.dumps(sensitivity_data[name], sort_keys=True),
                }))
        else:
            rows.append(_row(sensitivity, {
                "part": "Sensitivity", "row": "grid", "value": None}))
        return rows

    return {
        "schema": TABLES_SCHEMA,
        "release_id": audit["release_id"],
        "tables": {
            "T4": app_rows(),
            "T5": identity_rows(),
            "T6": attribution_rows(),
            "T7": t7_rows(),
            "T8": overhead_rows(),
        },
        "structural_only": True,
        "scientific_results_verified": False,
        "all_primary_endpoints_estimable": audit["all_primary_endpoints_estimable"],
        "scientific_boundary": audit["scientific_boundary"],
    }


def _cell(value) -> str:
    if value is None:
        return "NOT ESTIMABLE"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(tables: dict) -> str:
    lines = [f"# Experiment suite tables ({tables['release_id']})", "",
             "Missing values are `NOT ESTIMABLE`, not zero.", ""]
    titles = {
        "T4": "T4 Detection + AppBench",
        "T5": "T5 IdentityBench",
        "T6": "T6 AttributionBench",
        "T7": "T7 PBC / Ablation / Sensitivity",
        "T8": "T8 Overhead",
    }
    for name in ("T4", "T5", "T6", "T7", "T8"):
        rows = tables["tables"][name]
        lines.append(f"## {titles[name]}")
        lines.append("")
        if not rows:
            lines.append("_no rows_")
            lines.append("")
            continue
        columns = [column for column in TABLE_COLUMNS[name] if any(column in row for row in rows)]
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("|" + "|".join("---" for _ in columns) + "|")
        for row in rows:
            lines.append("| " + " | ".join(_cell(row.get(column)) for column in columns) + " |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan_parser = sub.add_parser("plan", help="write the frozen stage/table contract")
    plan_parser.add_argument("--out", type=Path, required=True)

    audit_parser = sub.add_parser("audit", help="audit a manifest and assemble tables")
    audit_parser.add_argument("--manifest", type=Path, required=True)
    audit_parser.add_argument("--out", type=Path, required=True)
    audit_parser.add_argument("--markdown-out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.command == "plan":
        _write(args.out, plan())
        print(json.dumps({"schema": PLAN_SCHEMA, "out": str(args.out)}))
        return 0

    try:
        manifest = load_manifest(args.manifest)
        audit = audit_manifest(manifest, base_dir=args.manifest.parent)
    except ManifestError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    _write(args.out, audit)
    if args.markdown_out is not None:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(render_markdown(audit["tables"]), encoding="utf-8")
    summary = {
        "valid": audit["valid"],
        "all_primary_endpoints_estimable": audit["all_primary_endpoints_estimable"],
        "scientific_results_verified": False,
        "experiments": {
            name: result["status"] for name, result in audit["experiments"].items()
        },
        "errors": audit["errors"],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if audit["valid"] and audit["all_primary_endpoints_estimable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
