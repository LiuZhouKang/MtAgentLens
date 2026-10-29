"""Bounded metrics for identity labels on one complete event cohort.

Both APIs share the same binding contract: one run, one evidence bundle, and
one complete event set. ``evaluate_identity_partition`` measures complete,
uniquely-owned partitions and rejects candidate sets and abstentions. The
candidate evaluator accepts explicit unknown truth (``None``) and explicit
abstention (``None``), but every prediction actor must map through the
explicit ``actor_mapping``. That mapping must come from an externally
established alignment and must not be fitted to the truth labels used for
scoring.

These metrics only measure the labels that were provided. They do not certify
independent provenance, do not establish human gold, and are not a new
scientific score. No pairwise or B-cubed score is computed on selected
subsets.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


@dataclass(frozen=True)
class IdentityPartition:
    """A complete map from event id to exactly one actor id."""

    run_id: str
    evidence_bundle_sha256: str
    event_to_actor: Mapping[str, str]


def _validate_partition(partition: IdentityPartition) -> None:
    if not isinstance(partition, IdentityPartition):
        raise ValueError("partition must be an IdentityPartition")

    # A partition is meaningful only when bound to one non-empty run and one
    # SHA256 evidence bundle; labels are never unioned across those bindings.
    if not isinstance(partition.run_id, str) or not partition.run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    digest = partition.evidence_bundle_sha256
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise ValueError("evidence_bundle_sha256 must be a 64-character hex SHA256")

    if not isinstance(partition.event_to_actor, Mapping):
        raise ValueError("event_to_actor must be a mapping")
    for event, actor in partition.event_to_actor.items():
        if not isinstance(event, str) or not event.strip():
            raise ValueError("every event id must be a non-empty string")
        # Candidate sets, None, and abstentions are not unique assignments.
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("every actor id must be a non-empty string")


def _choose_two(size: int) -> int:
    return size * (size - 1) // 2


def evaluate_identity_partition(
    truth: IdentityPartition, predicted: IdentityPartition
) -> dict[str, Any]:
    """Compare two complete partitions of the same run and event set."""
    _validate_partition(truth)
    _validate_partition(predicted)

    # Both sides must be tied to the same run and evidence bundle. A label
    # match across different bindings would be a spurious comparison.
    if truth.run_id != predicted.run_id:
        raise ValueError("run_id mismatch between truth and prediction")
    if truth.evidence_bundle_sha256 != predicted.evidence_bundle_sha256:
        raise ValueError("evidence bundle digest mismatch between truth and prediction")

    truth_map = truth.event_to_actor
    predicted_map = predicted.event_to_actor
    if set(truth_map) != set(predicted_map):
        raise ValueError("event sets differ between truth and prediction")
    n = len(truth_map)

    # All metrics are invariant to arbitrary actor/cluster renaming because
    # they depend only on the induced partitions, never on label values.
    truth_sizes = Counter(truth_map.values())
    predicted_sizes = Counter(predicted_map.values())
    intersections = Counter((truth_map[event], predicted_map[event]) for event in truth_map)

    # Pair counts come from cluster/intersection sizes, so this is O(n + k)
    # rather than enumerating all O(n^2) event pairs.
    tp = sum(_choose_two(size) for size in intersections.values())
    predicted_pairs = sum(_choose_two(size) for size in predicted_sizes.values())
    truth_pairs = sum(_choose_two(size) for size in truth_sizes.values())
    fp = predicted_pairs - tp
    fn = truth_pairs - tp
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1_denominator = 2 * tp + fp + fn
    f1 = (2 * tp / f1_denominator) if f1_denominator else None

    # B-cubed averages per-event cluster overlap, also from size counts.
    if n:
        b3_precision = (
            sum(size * size / predicted_sizes[pred] for (_, pred), size in intersections.items())
            / n
        )
        b3_recall = (
            sum(size * size / truth_sizes[actor] for (actor, _), size in intersections.items())
            / n
        )
        b3_denominator = b3_precision + b3_recall
        b3_f1 = (
            2 * b3_precision * b3_recall / b3_denominator
            if b3_denominator
            else None
        )
    else:
        b3_precision = b3_recall = b3_f1 = None

    # Count merged/split clusters directly from the intersection graph:
    # a predicted cluster is merged if it contains multiple truth actors, and
    # a truth actor is split if it appears in multiple predicted clusters.
    truths_per_predicted = Counter(pred for _, pred in intersections)
    predicted_clusters_per_truth = Counter(actor for actor, _ in intersections)
    merged_predicted_clusters = sum(
        1 for count in truths_per_predicted.values() if count > 1
    )
    split_truth_clusters = sum(
        1 for count in predicted_clusters_per_truth.values() if count > 1
    )

    # Agreement is not proof of human gold or correct roles; it only says the
    # two complete partitions induce the same grouping on these events.
    return {
        "events": n,
        "truth_clusters": len(truth_sizes),
        "predicted_clusters": len(predicted_sizes),
        "merged_predicted_clusters": merged_predicted_clusters,
        "split_truth_clusters": split_truth_clusters,
        "pairwise": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        },
        "b_cubed": {
            "precision": b3_precision,
            "recall": b3_recall,
            "f1": b3_f1,
        },
    }


@dataclass(frozen=True)
class IdentityReference:
    """A complete map from event id to one actor id or unknown truth (None)."""

    run_id: str
    evidence_bundle_sha256: str
    event_to_actor: Mapping[str, str | None]


@dataclass(frozen=True)
class IdentityCandidates:
    """A complete map from event id to a candidate set or abstention (None)."""

    run_id: str
    evidence_bundle_sha256: str
    event_to_candidates: Mapping[str, frozenset[str] | None]


def _validate_binding(run_id: str, digest: str) -> None:
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty string")
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise ValueError("evidence_bundle_sha256 must be a 64-character hex SHA256")


def _validate_reference(reference: IdentityReference) -> None:
    if not isinstance(reference, IdentityReference):
        raise ValueError("reference must be an IdentityReference")
    _validate_binding(reference.run_id, reference.evidence_bundle_sha256)
    if not isinstance(reference.event_to_actor, Mapping):
        raise ValueError("event_to_actor must be a mapping")
    for event, actor in reference.event_to_actor.items():
        if not isinstance(event, str) or not event.strip():
            raise ValueError("every event id must be a non-empty string")
        if actor is not None and (not isinstance(actor, str) or not actor.strip()):
            raise ValueError("every reference actor must be a non-empty string or None")


def _validate_candidates(prediction: IdentityCandidates) -> None:
    if not isinstance(prediction, IdentityCandidates):
        raise ValueError("prediction must be an IdentityCandidates")
    _validate_binding(prediction.run_id, prediction.evidence_bundle_sha256)
    if not isinstance(prediction.event_to_candidates, Mapping):
        raise ValueError("event_to_candidates must be a mapping")
    for event, candidates in prediction.event_to_candidates.items():
        if not isinstance(event, str) or not event.strip():
            raise ValueError("every event id must be a non-empty string")
        if candidates is None:
            continue
        if not isinstance(candidates, frozenset):
            raise ValueError("every prediction must be a frozenset or None")
        if not candidates:
            raise ValueError("candidate sets must not be empty")
        for actor in candidates:
            if not isinstance(actor, str) or not actor.strip():
                raise ValueError("every prediction actor must be a non-empty string")


def _validate_actor_mapping(actor_mapping: Mapping[str, str]) -> None:
    if not isinstance(actor_mapping, Mapping):
        raise ValueError("actor_mapping must be a mapping")
    for actor, reference_actor in actor_mapping.items():
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("every actor_mapping key must be a non-empty string")
        if not isinstance(reference_actor, str) or not reference_actor.strip():
            raise ValueError("every actor_mapping target must be a non-empty string")


def evaluate_identity_candidates(
    reference: IdentityReference,
    prediction: IdentityCandidates,
    *,
    actor_mapping: Mapping[str, str],
) -> dict[str, Any]:
    """Score candidate sets and abstentions against an explicit reference.

    ``actor_mapping`` must come from an externally established alignment; it
    must not be fitted to the reference labels used here for scoring. Events
    with unknown truth stay in the totals but are excluded from every accuracy
    denominator. All conditional rates are None when their denominator is zero.

    Counts classify distinct mapped actors, so explicit aliases do not inflate
    candidate size. Coverage, singleton_correct_rate and known_truth_hit_rate
    use all known-truth events, including abstentions. Selective accuracy uses
    known-truth singleton answers; answered_candidate_hit_rate and
    mean_candidate_count use known-truth answered events. Candidate hits are
    membership checks, not unique-actor accuracy or calibrated probabilities.
    """
    _validate_reference(reference)
    _validate_candidates(prediction)
    _validate_actor_mapping(actor_mapping)

    if reference.run_id != prediction.run_id:
        raise ValueError("run_id mismatch between reference and prediction")
    if reference.evidence_bundle_sha256 != prediction.evidence_bundle_sha256:
        raise ValueError(
            "evidence bundle digest mismatch between reference and prediction"
        )

    reference_map = reference.event_to_actor
    predicted_map = prediction.event_to_candidates
    if set(reference_map) != set(predicted_map):
        raise ValueError("event sets differ between reference and prediction")

    known_truth = unknown_truth = 0
    abstained = singleton = candidate_set = 0
    answered = hits = singleton_correct = 0
    known_singleton = known_candidate_set = 0
    candidate_count_sum = 0

    for event, truth_actor in reference_map.items():
        candidates = predicted_map[event]
        if candidates is None:
            mapped_candidates = None
            abstained += 1
        else:
            # Explicit aliases are deduplicated before any counting. Mapping
            # targets may be absent from the reference labels; incorrect
            # predictions remain evaluable instead of being rejected.
            mapped_candidates = set()
            for actor in candidates:
                if actor not in actor_mapping:
                    raise ValueError(f"prediction actor {actor!r} is not in actor_mapping")
                mapped_candidates.add(actor_mapping[actor])
            if len(mapped_candidates) == 1:
                singleton += 1
            else:
                candidate_set += 1

        if truth_actor is None:
            unknown_truth += 1
            continue

        known_truth += 1
        if mapped_candidates is None:
            continue

        answered += 1
        candidate_count_sum += len(mapped_candidates)
        if len(mapped_candidates) == 1:
            known_singleton += 1
        else:
            known_candidate_set += 1
        if truth_actor in mapped_candidates:
            hits += 1
            if len(mapped_candidates) == 1:
                singleton_correct += 1

    return {
        "events": len(reference_map),
        "known_truth_events": known_truth,
        "unknown_truth_events": unknown_truth,
        "predictions": {
            "abstained": abstained,
            "singleton": singleton,
            "candidate_set": candidate_set,
        },
        "scorable": {
            "answered": answered,
            "abstained": known_truth - answered,
            "singleton": known_singleton,
            "candidate_set": known_candidate_set,
            "hits": hits,
            "singleton_correct": singleton_correct,
        },
        "known_truth_coverage": answered / known_truth if known_truth else None,
        "singleton_coverage": known_singleton / known_truth if known_truth else None,
        "selective_singleton_accuracy": (
            singleton_correct / known_singleton if known_singleton else None
        ),
        "singleton_correct_rate": singleton_correct / known_truth if known_truth else None,
        "answered_candidate_hit_rate": hits / answered if answered else None,
        "known_truth_hit_rate": hits / known_truth if known_truth else None,
        "mean_candidate_count": candidate_count_sum / answered if answered else None,
    }
