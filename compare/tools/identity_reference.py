# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Independent identity reference -> pairwise / B-cubed / abstention metrics.

The reference is a frozen, explicitly versioned resolution produced by three
fresh reviewers (method ``ai_consensus_3``, ``human_review=false``,
``detector_blind=true``). It is supplied independently and is never inferred
from predictions. The alignment file maps *captured* prediction actor ids to
*canonical* reference actor ids and carries its own raw-capture source/hash plus
evidence anchors.

Two metrics are reported side by side and never mixed:

* pairwise precision/recall/F1 and B-cubed are computed only when both sides are
  a complete, uniquely-owned partition of every event. Unknown truth,
  candidate sets, and abstentions make the partition incomplete, so those
  fields are ``null`` rather than silently computed on a selected subset.
* coverage/abstention, candidate membership hits, and candidate-set size use
  every known-truth event, including wrong, missing, and abstained answers.

Every known-truth event stays in the denominator. Unknown truth is counted and
excluded from accuracy denominators only; it is never scored as a mistake or
used to shrink the cohort. Scores are agreement with an AI-resolved reference,
not accuracy against human gold.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from compare.tools import identity_metrics

REFERENCE_SCHEMA = "mtagentlens.identity-reference/1"
ALIGNMENT_SCHEMA = "mtagentlens.identity-alignment/1"
PREDICTION_SCHEMA = "mtagentlens.identity-prediction/1"
EXPERIMENT_SCHEMA = "mtagentlens.identity-experiment/v1"
REPORT_SCHEMA = "mtagentlens.identity-reference-report/1"
ADAPTER_VERSION = 1
IDENTITY_CONDITIONS = (
    "pid-instance-only",
    "pid-instance-plus-ppid",
    "pid-instance-plus-tls",
    "pid-instance-plus-conversation",
    "full",
)
DEFAULT_CONDITION = "full"

METHOD = "ai_consensus_3"
REVIEWER_COUNT = 3

_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_str(value: Any, label: str) -> str:
    if not _is_nonempty_str(value):
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _require_digest(value: Any, label: str) -> str:
    digest = _require_str(value, label)
    if _SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"{label} must be a 64-character hex SHA256")
    return digest


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(encoded)


def _validate_provenance(value: Any) -> dict[str, Any]:
    provenance = _require_object(value, "reference.provenance")
    if provenance.get("method") != METHOD:
        raise ValueError(f"reference.provenance.method must be {METHOD!r}")
    if provenance.get("human_review") is not False:
        raise ValueError("reference.provenance.human_review must be false")
    if provenance.get("detector_blind") is not True:
        raise ValueError("reference.provenance.detector_blind must be true")
    reviewers = provenance.get("reviewer_ids")
    if (not isinstance(reviewers, list) or len(reviewers) != REVIEWER_COUNT
            or any(not _is_nonempty_str(item) for item in reviewers)
            or len(set(reviewers)) != REVIEWER_COUNT):
        raise ValueError(
            f"reference.provenance.reviewer_ids must contain {REVIEWER_COUNT} "
            "distinct nonempty reviewer ids"
        )
    adjudicator = _require_str(
        provenance.get("adjudicator_id"), "reference.provenance.adjudicator_id")
    if adjudicator in reviewers:
        raise ValueError(
            "reference.provenance.adjudicator_id must be independent of every reviewer")
    return provenance


def validate_reference(value: Any) -> dict[str, Any]:
    """Validate one frozen identity reference; never derive labels here."""
    reference = _require_object(value, "reference")
    if reference.get("schema") != REFERENCE_SCHEMA:
        raise ValueError(f"reference.schema must be {REFERENCE_SCHEMA}")
    if reference.get("version") != 1 or isinstance(reference.get("version"), bool):
        raise ValueError("reference.version must be 1")
    if reference.get("frozen") is not True:
        raise ValueError("reference.frozen must be true")
    run_id = _require_str(reference.get("run_id"), "reference.run_id")
    digest = _require_digest(
        reference.get("evidence_bundle_sha256"),
        "reference.evidence_bundle_sha256")
    source_db = None
    if "source_database_sha256" in reference:
        source_db = _require_digest(
            reference["source_database_sha256"],
            "reference.source_database_sha256")
    provenance = _validate_provenance(reference.get("provenance"))
    events = _require_object(reference.get("events"), "reference.events")
    if not events:
        raise ValueError("reference.events must not be empty")

    normalized: dict[str, dict[str, Any]] = {}
    for event, raw in events.items():
        event_id = _require_str(event, "reference event id")
        record = _require_object(raw, f"reference event {event_id!r}")
        status = record.get("status")
        if status not in ("known", "unknown"):
            raise ValueError(
                f"reference event {event_id!r} status must be 'known' or 'unknown'")
        if status == "known":
            actor = _require_str(
                record.get("actor"), f"reference event {event_id!r} actor")
            normalized[event_id] = {"status": "known", "actor": actor}
        else:
            if record.get("actor") is not None:
                raise ValueError(
                    f"reference event {event_id!r} is unknown but carries an actor")
            normalized[event_id] = {"status": "unknown", "actor": None}

    normalized_reference = {
        "schema": REFERENCE_SCHEMA,
        "version": 1,
        "frozen": True,
        "run_id": run_id,
        "evidence_bundle_sha256": digest,
        "provenance": provenance,
        "events": normalized,
    }
    if source_db is not None:
        normalized_reference["source_database_sha256"] = source_db
    return normalized_reference


def validate_prediction(value: Any, reference: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a complete event -> singleton/candidate-set/abstention export."""
    prediction = _require_object(value, "prediction")
    if prediction.get("schema") != PREDICTION_SCHEMA:
        raise ValueError(f"prediction.schema must be {PREDICTION_SCHEMA}")
    if prediction.get("version") != 1 or isinstance(prediction.get("version"), bool):
        raise ValueError("prediction.version must be 1")
    run_id = _require_str(prediction.get("run_id"), "prediction.run_id")
    if run_id != reference["run_id"]:
        raise ValueError(
            f"run_id mismatch: prediction {run_id!r} != reference {reference['run_id']!r}")
    digest = _require_digest(
        prediction.get("evidence_bundle_sha256"),
        "prediction.evidence_bundle_sha256")
    if digest != reference["evidence_bundle_sha256"]:
        raise ValueError(
            "evidence bundle digest mismatch between reference and prediction")
    events = _require_object(prediction.get("events"), "prediction.events")
    if set(events) != set(reference["events"]):
        raise ValueError(
            "event sets differ between reference and prediction; identity metrics "
            "require one complete event cohort")

    normalized: dict[str, frozenset[str] | None] = {}
    for event, raw in events.items():
        event_id = _require_str(event, "prediction event id")
        if raw is None:
            normalized[event_id] = None
            continue
        if isinstance(raw, str):
            actors = [raw]
        elif isinstance(raw, list):
            actors = raw
        else:
            raise ValueError(
                f"prediction event {event_id!r} must be an actor id, a nonempty "
                "candidate list, or null")
        if not actors:
            raise ValueError(
                f"prediction event {event_id!r} has an empty candidate set; "
                "abstention must be null")
        mapped: set[str] = set()
        for actor in actors:
            mapped.add(_require_str(
                actor, f"prediction event {event_id!r} actor"))
        normalized[event_id] = frozenset(mapped)

    return {
        "schema": PREDICTION_SCHEMA,
        "version": 1,
        "run_id": run_id,
        "evidence_bundle_sha256": digest,
        "events": normalized,
    }


def validate_alignment(value: Any, reference: Mapping[str, Any]) -> dict[str, Any]:
    """Validate captured->canonical actor mapping plus its raw-capture anchor."""
    alignment = _require_object(value, "alignment")
    if alignment.get("schema") != ALIGNMENT_SCHEMA:
        raise ValueError(f"alignment.schema must be {ALIGNMENT_SCHEMA}")
    if alignment.get("version") != 1 or isinstance(alignment.get("version"), bool):
        raise ValueError("alignment.version must be 1")
    raw = _require_object(alignment.get("raw_capture"), "alignment.raw_capture")
    raw_source = _require_str(raw.get("source"), "alignment.raw_capture.source")
    raw_sha = _require_digest(
        raw.get("sha256"), "alignment.raw_capture.sha256")

    # A mapped canonical actor may be absent from the known reference labels:
    # that is simply a wrong prediction and must stay evaluable, not be
    # discarded from the denominator.
    mappings = alignment.get("mappings")
    if not isinstance(mappings, list):
        raise ValueError("alignment.mappings must be a list")
    captured_to_canonical: dict[str, str] = {}
    canonical_to_captured: dict[str, str] = {}
    for raw_mapping in mappings:
        mapping = _require_object(raw_mapping, "alignment mapping")
        canonical = _require_str(
            mapping.get("canonical_id"), "alignment canonical_id")
        captured = _require_str(
            mapping.get("captured_id"), "alignment captured_id")
        kind = mapping.get("kind")
        if kind not in (None, "actor"):
            raise ValueError("identity alignment mapping kind must be 'actor'")
        evidence = mapping.get("evidence_refs")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("alignment mapping requires nonempty evidence_refs")
        if any(not _is_nonempty_str(item) for item in evidence):
            raise ValueError("alignment evidence_refs must be nonempty strings")
        previous = captured_to_canonical.get(captured)
        if previous is not None and previous != canonical:
            raise ValueError(
                f"captured actor {captured!r} is aligned to both {previous!r} "
                f"and {canonical!r}")
        captured_to_canonical[captured] = canonical
        canonical_to_captured.setdefault(canonical, captured)

    return {
        "schema": ALIGNMENT_SCHEMA,
        "version": 1,
        "raw_capture": {"source": raw_source, "sha256": raw_sha},
        "mappings": mappings,
        "captured_to_canonical": captured_to_canonical,
        "canonical_to_captured": canonical_to_captured,
    }


def _project_experiment(
    value: Any,
    reference: Mapping[str, Any],
    *,
    condition: str,
) -> dict[str, Any]:
    """Project one replay identity view into the existing prediction schema.

    The experiment wrapper is the actual replay export. Projection copies only
    per-unit predicted partitions; labels and reference actor assignments are
    never consulted. The wrapper's source database digest must independently
    match the frozen reference before the reference evidence digest is copied as
    metadata, so a graph-only export cannot masquerade as a bound experiment.
    """
    experiment = _require_object(value, "identity experiment")
    if experiment.get("schema") != EXPERIMENT_SCHEMA:
        raise ValueError(f"identity experiment schema must be {EXPERIMENT_SCHEMA}")
    current = _require_str(
        experiment.get("current_fingerprint"),
        "identity experiment.current_fingerprint")
    wrapper_db = _require_digest(
        experiment.get("source_database_sha256"),
        "identity experiment.source_database_sha256")
    reference_db = reference.get("source_database_sha256")
    if reference_db is None:
        raise ValueError(
            "identity experiment requires reference.source_database_sha256; "
            "a normalized prediction cannot bind the source database")
    if wrapper_db.lower() != reference_db.lower():
        raise ValueError(
            "source_database_sha256 mismatch between identity experiment and reference")
    if condition not in IDENTITY_CONDITIONS:
        raise ValueError(
            f"condition must be one of {IDENTITY_CONDITIONS}, got {condition!r}")

    runs = experiment.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("identity experiment.runs must be a nonempty list")
    matching_runs = []
    for raw_run in runs:
        run = _require_object(raw_run, "identity experiment run")
        run_id = _require_str(run.get("run_id"), "identity experiment run_id")
        if run_id == reference["run_id"]:
            matching_runs.append(run)
    if not matching_runs:
        raise ValueError(
            f"identity experiment has no run_id {reference['run_id']!r}")
    if len(matching_runs) > 1:
        raise ValueError(
            f"identity experiment has multiple run_id {reference['run_id']!r}")
    run = matching_runs[0]
    source_fingerprint = _require_str(
        run.get("source_fingerprint"),
        "identity experiment run.source_fingerprint")
    run_fingerprint = _require_str(
        run.get("current_fingerprint"),
        "identity experiment run.current_fingerprint")
    if source_fingerprint != current:
        raise ValueError(
            "identity experiment current_fingerprint is stale against "
            "run.source_fingerprint")
    if run_fingerprint != current:
        raise ValueError(
            "identity experiment current_fingerprint mismatches "
            "run.current_fingerprint")

    views = run.get("views")
    if not isinstance(views, list):
        raise ValueError("identity experiment run.views must be a list")
    matching_views = []
    for raw_view in views:
        view = _require_object(raw_view, "identity experiment view")
        view_condition = _require_str(
            view.get("condition"), "identity experiment view.condition")
        if view_condition == condition:
            matching_views.append(view)
    if not matching_views:
        raise ValueError(
            f"identity experiment run has no condition {condition!r}")
    if len(matching_views) > 1:
        raise ValueError(
            f"identity experiment run has multiple conditions {condition!r}")

    raw_units = matching_views[0].get("units")
    if not isinstance(raw_units, list):
        raise ValueError("identity experiment view.units must be a list")
    events: dict[str, str | None] = {}
    for raw_unit in raw_units:
        unit = _require_object(raw_unit, "identity experiment view unit")
        unit_id = _require_str(
            unit.get("unit_id"), "identity experiment view unit.unit_id")
        if unit_id in events:
            raise ValueError(f"Duplicate unit_id {unit_id!r} in identity experiment")
        actor = _require_object(
            unit.get("actor"), f"identity experiment unit {unit_id!r} actor")
        state = actor.get("state")
        if state == "assigned":
            events[unit_id] = _require_str(
                actor.get("partition"),
                f"identity experiment unit {unit_id!r} actor.partition")
        elif state == "unknown":
            events[unit_id] = None
        else:
            raise ValueError(
                f"identity experiment unit {unit_id!r} actor.state must be "
                "'assigned' or 'unknown'")

    return {
        "schema": PREDICTION_SCHEMA,
        "version": 1,
        "run_id": reference["run_id"],
        "evidence_bundle_sha256": reference["evidence_bundle_sha256"],
        "events": events,
    }


def _partition_metrics(
    reference: Mapping[str, Any],
    prediction: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Score pairwise/B-cubed only for a complete, uniquely-owned partition.

    Predicted clusters are the raw captured singleton ids. Partition metrics are
    invariant to arbitrary cluster renaming, so applying an actor crosswalk here
    would silently merge distinct predicted clusters and erase false splits.
    """
    reference_events = reference["events"]
    if any(record["status"] != "known" for record in reference_events.values()):
        return None
    truth: dict[str, str] = {}
    predicted: dict[str, str] = {}
    for event, record in reference_events.items():
        candidates = prediction["events"][event]
        if candidates is None or len(candidates) != 1:
            return None
        truth[event] = record["actor"]
        predicted[event] = next(iter(candidates))
    result = identity_metrics.evaluate_identity_partition(
        identity_metrics.IdentityPartition(
            run_id=reference["run_id"],
            evidence_bundle_sha256=reference["evidence_bundle_sha256"],
            event_to_actor=truth,
        ),
        identity_metrics.IdentityPartition(
            run_id=prediction["run_id"],
            evidence_bundle_sha256=prediction["evidence_bundle_sha256"],
            event_to_actor=predicted,
        ),
    )
    return {
        "events": result["events"],
        "truth_clusters": result["truth_clusters"],
        "predicted_clusters": result["predicted_clusters"],
        "pairwise_precision": result["pairwise"]["precision"],
        "pairwise_recall": result["pairwise"]["recall"],
        "pairwise_f1": result["pairwise"]["f1"],
        "b3": result["b_cubed"]["f1"],
        "b3_precision": result["b_cubed"]["precision"],
        "b3_recall": result["b_cubed"]["recall"],
        "false_merge": result["merged_predicted_clusters"],
        "false_split": result["split_truth_clusters"],
    }


def evaluate_reference(
    reference_value: Any,
    prediction_value: Any,
    alignment_value: Any,
    *,
    source_hashes: Mapping[str, str] | None = None,
    input_paths: Mapping[str, str] | None = None,
    raw_capture_verified: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate supplied JSON values without deriving any reference content."""
    reference = validate_reference(reference_value)
    prediction = validate_prediction(prediction_value, reference)
    alignment = validate_alignment(alignment_value, reference)

    candidate_metrics = identity_metrics.evaluate_identity_candidates(
        identity_metrics.IdentityReference(
            run_id=reference["run_id"],
            evidence_bundle_sha256=reference["evidence_bundle_sha256"],
            event_to_actor={
                event: record["actor"] for event, record in reference["events"].items()
            },
        ),
        identity_metrics.IdentityCandidates(
            run_id=prediction["run_id"],
            evidence_bundle_sha256=prediction["evidence_bundle_sha256"],
            event_to_candidates=prediction["events"],
        ),
        actor_mapping=alignment["captured_to_canonical"],
    )

    known = candidate_metrics["known_truth_events"]
    unknown = candidate_metrics["unknown_truth_events"]
    scorable = candidate_metrics["scorable"]
    answered = scorable["answered"]
    coverage = answered / known if known else None
    abstention = (known - answered) / known if known else None

    partition = _partition_metrics(reference, prediction)

    metrics = {
        "events": candidate_metrics["events"],
        "known_truth_events": known,
        "unknown_truth_events": unknown,
        "coverage": coverage,
        "abstention": abstention,
        "unattributed": known - answered,
        "prediction_abstentions": candidate_metrics["predictions"]["abstained"],
        "prediction_singletons": candidate_metrics["predictions"]["singleton"],
        "prediction_candidate_sets": candidate_metrics["predictions"]["candidate_set"],
        "answered_candidate_hit_rate": candidate_metrics["answered_candidate_hit_rate"],
        "known_truth_hit_rate": candidate_metrics["known_truth_hit_rate"],
        "mean_candidate_count": candidate_metrics["mean_candidate_count"],
        "pairwise_precision": None,
        "pairwise_recall": None,
        "pairwise_f1": None,
        "b3": None,
        "b3_precision": None,
        "b3_recall": None,
        "false_merge": None,
        "false_split": None,
        "partition_estimable": partition is not None,
    }
    if partition is not None:
        for key in (
            "pairwise_precision",
            "pairwise_recall",
            "pairwise_f1",
            "b3",
            "b3_precision",
            "b3_recall",
            "false_merge",
            "false_split",
        ):
            metrics[key] = partition[key]

    paths = input_paths or {}
    hashes = source_hashes or {}
    inputs = {
        "reference": {
            "path": paths.get("reference"),
            "sha256": hashes.get("reference") or _canonical_sha(reference_value),
        },
        "alignment": {
            "path": paths.get("alignment"),
            "sha256": hashes.get("alignment") or _canonical_sha(alignment_value),
            "raw_capture": {
                **dict(alignment["raw_capture"]),
                # The pure value API never reads paths and never fabricates
                # `verified`; evaluate_files supplies the checked result.
                "verified": False,
                **(dict(raw_capture_verified) if raw_capture_verified else {}),
            },
        },
        "prediction": {
            "path": paths.get("prediction"),
            "sha256": hashes.get("prediction") or _canonical_sha(prediction_value),
        },
    }

    return {
        "schema": REPORT_SCHEMA,
        "version": ADAPTER_VERSION,
        "inputs": inputs,
        "reference": {
            "schema": reference["schema"],
            "version": reference["version"],
            "frozen": reference["frozen"],
            "provenance": reference["provenance"],
            "run_id": reference["run_id"],
            "event_count": len(reference["events"]),
        },
        "runs": 1,
        "metrics": metrics,
        "conventions": {
            "partition": (
                "pairwise/B-cubed/false-merge/false-split are reported only when "
                "every event has known truth and exactly one predicted actor; "
                "otherwise they are null, not computed on a selected subset"
            ),
            "denominator": (
                "every known-truth event stays in the denominator; unknown truth "
                "is counted separately and never scored as a miss"
            ),
            "candidates": (
                "candidate sets are deduplicated through the external alignment; "
                "answered_candidate_hit_rate is membership, not unique accuracy"
            ),
            "abstention": (
                "coverage=answered/known_truth and "
                "abstention=(known_truth-answered)/known_truth; unattributed "
                "counts known-truth events with no predicted actor"
            ),
            "alignment": (
                "captured prediction ids are mapped through the externally "
                "established alignment; the alignment is never fitted to reference "
                "labels used for scoring"
            ),
            "raw_capture": (
                "alignment.raw_capture is verified by evaluate_files against "
                "source bytes resolved relative to the alignment file; the check "
                "is structural (regular non-symlink file whose bytes match the "
                "declared SHA256), not semantic proof of capture origin"
            ),
        },
        "disclosure": {
            "reference_method": METHOD,
            "human_review": False,
            "human_gold": False,
            "accuracy_claimed": False,
            "note": (
                "Scores measure agreement with an independently supplied "
                "AI-resolved identity reference; they are not accuracy against "
                "human gold. Raw-capture hash verification is structural only "
                "and is not semantic proof of source provenance."
            ),
        },
    }


def _verify_raw_capture(alignment_path: Path, alignment_value: Any) -> dict[str, Any]:
    """Check the declared raw-capture bytes; structural provenance only."""
    alignment = _require_object(alignment_value, "alignment")
    raw = _require_object(alignment.get("raw_capture"), "alignment.raw_capture")
    source_value = _require_str(raw.get("source"), "alignment.raw_capture.source")
    declared = _require_str(raw.get("sha256"), "alignment.raw_capture.sha256")
    source = Path(source_value)
    if not source.is_absolute():
        source = alignment_path.parent / source
    if source.is_symlink() or not source.is_file():
        raise ValueError(
            f"alignment.raw_capture source must be a regular file: {source}")
    try:
        before = source.stat()
        data = source.read_bytes()
        after = source.stat()
    except OSError as exc:
        raise ValueError(
            f"alignment.raw_capture source cannot be read: {source}: {exc}"
        ) from exc
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError(
            f"alignment.raw_capture source changed while being read: {source}")
    actual = _sha256(data)
    if actual.lower() != declared.lower():
        raise ValueError(
            f"alignment.raw_capture hash drift for {source}: "
            f"declared {declared}, actual {actual}"
        )
    return {"verified": True, "actual_sha256": actual, "resolved_source": str(source)}


def evaluate_files(
    reference_path: str | Path,
    prediction_path: str | Path,
    alignment_path: str | Path,
    *,
    condition: str = DEFAULT_CONDITION,
) -> dict[str, Any]:
    """Read the three inputs, verify the raw capture, then evaluate.

    A historical normalized prediction is evaluated unchanged. The actual
    replay experiment wrapper is projected through exactly one run and one
    condition after its source database SHA256 binds to the reference.
    """
    if condition not in IDENTITY_CONDITIONS:
        raise ValueError(
            f"condition must be one of {IDENTITY_CONDITIONS}, got {condition!r}")
    paths = {
        "reference": Path(reference_path),
        "prediction": Path(prediction_path),
        "alignment": Path(alignment_path),
    }
    raw = {name: path.read_bytes() for name, path in paths.items()}
    values = {name: json.loads(data) for name, data in raw.items()}
    hashes = {name: _sha256(data) for name, data in raw.items()}
    verification = _verify_raw_capture(paths["alignment"], values["alignment"])
    reference = validate_reference(values["reference"])
    prediction_value = values["prediction"]
    schema = prediction_value.get("schema") if isinstance(prediction_value, dict) else None
    if schema == EXPERIMENT_SCHEMA:
        prediction_value = _project_experiment(
            prediction_value, reference, condition=condition)
    elif schema != PREDICTION_SCHEMA:
        raise ValueError(
            f"prediction.schema must be {PREDICTION_SCHEMA} or {EXPERIMENT_SCHEMA}")
    return evaluate_reference(
        reference,
        prediction_value,
        values["alignment"],
        source_hashes=hashes,
        input_paths={name: str(path) for name, path in paths.items()},
        raw_capture_verified=verification,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare an independently resolved identity reference against "
            "per-event identity predictions; read-only, JSON on stdout."
        )
    )
    parser.add_argument("--reference", required=True)
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--alignment", required=True)
    parser.add_argument(
        "--condition",
        default=DEFAULT_CONDITION,
        choices=IDENTITY_CONDITIONS,
        help="identity ablation view for an experiment wrapper (default: full)",
    )
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    report = evaluate_files(
        args.reference, args.prediction, args.alignment, condition=args.condition)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    json.dump(
        {
            "schema": report["schema"],
            "runs": report["runs"],
            "metrics": report["metrics"],
            "out": str(args.out) if args.out else None,
        },
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
