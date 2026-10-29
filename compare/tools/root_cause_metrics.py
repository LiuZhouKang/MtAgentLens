# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Root-cause localisation metrics for the multi-agent review comments.

The reviewer asks for metrics on the *attribution* output, not just chain depth:
which agent the system names as the root, where the chain enters, and whether the
causal links are right. These definitions are fixed here so E6 (23 Type C runs)
can be evaluated as soon as the runs exist; nothing in this module depends on a
model call.

Definitions (also in docs/paper/根因指标定义.md):

* RootAgent@k — the ground-truth root agent appears in the top-k ranked agents;
* MRR — reciprocal rank of the ground-truth root agent (0 when absent);
* EntryPoint Accuracy — the predicted entry point equals the recorded one;
* Chain Link P/R/F1 — predicted causal edges against ground-truth edges;
* Chain Depth MAE — mean absolute difference of longest directed path lengths;
* Boundary-type Accuracy — Jaccard of predicted and ground-truth boundary kinds.

An empty reference cohort is invalid. Unannotated optional fields yield None;
an empty chain never establishes a zero-depth reference.

@3/MRR require a frozen prediction ordering. The attribution kernel ranks at
most one candidate, so a multi-candidate ranking must declare
``ranked_from_frozen_ordering``; otherwise those two metrics are None and
``ranking_reported`` is False, which a report must state as "top-k not
reported" rather than as zero.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Sequence

SCHEMA = "mtagentlens.root-cause-metrics/2"


@dataclass(frozen=True)
class RootCauseReference:
    run_id: str
    root_agent: str
    entry_point: str
    chain: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    boundary_types: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RootCausePrediction:
    run_id: str
    ranked_agents: tuple[str, ...]
    entry_point: str | None
    chain: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    boundary_types: tuple[str, ...] = field(default_factory=tuple)
    # True only for a fixed, deterministic ordering. The attribution
    # kernel emits zero or one candidate, so any longer ranking must be declared
    # by whoever produced it; otherwise @3/MRR would be a fabricated ordering.
    ranked_from_frozen_ordering: bool = False


@dataclass(frozen=True)
class RootCauseMetrics:
    schema: str
    runs: int
    root_agent_at_1: float | None
    root_agent_at_3: float | None
    mrr: float | None
    entry_point_accuracy: float | None
    chain_precision: float | None
    chain_recall: float | None
    chain_f1: float | None
    chain_depth_mae: float | None
    boundary_type_accuracy: float | None
    missing_root_predictions: int
    # False unless every non-empty ranking is a declared frozen ordering; when
    # False, root_agent_at_3 and mrr are None and must be reported as "not
    # reported", never as zero.
    ranking_reported: bool = False


def _chain_depth(edges: Sequence[tuple[str, str]]) -> int:
    """Longest directed path in a DAG, measured in edges (not edge count).

    Nodes must identify event occurrences; repeated actors alone cannot encode
    an acyclic causal chain. Cycles have no DAG depth and are rejected.
    """
    children: dict[str, set[str]] = {}
    indegree: dict[str, int] = {}
    for source, target in edges:
        indegree.setdefault(source, 0)
        indegree.setdefault(target, 0)
        if target not in children.setdefault(source, set()):
            children[source].add(target)
            indegree[target] += 1
    ready = deque(node for node, degree in indegree.items() if degree == 0)
    depths = dict.fromkeys(indegree, 0)
    visited = 0
    while ready:
        node = ready.popleft()
        visited += 1
        for target in children.get(node, ()):
            depths[target] = max(depths[target], depths[node] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(indegree):
        raise ValueError("Causal chain contains a cycle; event-occurrence DAG required")
    return max(depths.values(), default=0)


def _validate(references: Sequence[RootCauseReference],
              predictions: Sequence[RootCausePrediction]) -> None:
    if not references:
        raise ValueError("At least one root-cause reference is required")
    reference_ids = [item.run_id for item in references]
    prediction_ids = [item.run_id for item in predictions]
    if len(set(reference_ids)) != len(reference_ids):
        raise ValueError("Duplicate run id in references")
    if len(set(prediction_ids)) != len(prediction_ids):
        raise ValueError("Duplicate run id in predictions")
    if set(reference_ids) != set(prediction_ids):
        missing = sorted(set(reference_ids) - set(prediction_ids))
        extra = sorted(set(prediction_ids) - set(reference_ids))
        raise ValueError(f"Run ids must match; missing={missing} extra={extra}")
    for reference in references:
        if not isinstance(reference.run_id, str) or not reference.run_id.strip():
            raise ValueError("Reference requires nonempty run id")
        if not isinstance(reference.root_agent, str) or not reference.root_agent.strip():
            raise ValueError(f"{reference.run_id}: empty root agent in reference")
        for edge in reference.chain:
            if len(edge) != 2 or not all(isinstance(component, str) and component.strip() for component in edge):
                raise ValueError(f"{reference.run_id}: malformed reference chain edge")
        if len(set(reference.chain)) != len(reference.chain):
            raise ValueError(f"{reference.run_id}: duplicate reference chain edge")
        _chain_depth(reference.chain)
    for prediction in predictions:
        if len(set(prediction.ranked_agents)) != len(prediction.ranked_agents):
            raise ValueError(f"{prediction.run_id}: duplicate ranked agent")
        if len(prediction.ranked_agents) > 1 and not prediction.ranked_from_frozen_ordering:
            raise ValueError(
                f"{prediction.run_id}: a multi-candidate ranking must declare "
                "ranked_from_frozen_ordering; the attribution kernel only ranks "
                "a single candidate")
        for edge in prediction.chain:
            if len(edge) != 2 or not all(isinstance(component, str) and component.strip() for component in edge):
                raise ValueError(f"{prediction.run_id}: malformed predicted chain edge")
        _chain_depth(prediction.chain)


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def evaluate_root_cause(
    references: Sequence[RootCauseReference],
    predictions: Sequence[RootCausePrediction],
) -> RootCauseMetrics:
    """Compute the attribution metrics over exactly matched run ids."""
    _validate(references, predictions)
    by_run = {item.run_id: item for item in predictions}
    at_1: list[float] = []
    at_3: list[float] = []
    reciprocal: list[float] = []
    entry_hits: list[float] = []
    precisions: list[float] = []
    recalls: list[float] = []
    depth_errors: list[float] = []
    boundary_scores: list[float] = []
    missing_root = 0

    for reference in references:
        prediction = by_run[reference.run_id]
        ranking = prediction.ranked_agents
        try:
            rank = ranking.index(reference.root_agent)
        except ValueError:
            rank = None
            missing_root += 1
        at_1.append(1.0 if rank == 0 else 0.0)
        at_3.append(1.0 if rank is not None and rank < 3 else 0.0)
        reciprocal.append(0.0 if rank is None else 1.0 / (rank + 1))

        if reference.entry_point:
            entry_hits.append(1.0 if prediction.entry_point == reference.entry_point else 0.0)

        predicted_edges = set(prediction.chain)
        reference_edges = set(reference.chain)
        if reference_edges:
            hit = len(predicted_edges & reference_edges)
            precisions.append(hit / len(predicted_edges) if predicted_edges else 0.0)
            recalls.append(hit / len(reference_edges))

        if reference_edges:
            depth_errors.append(abs(_chain_depth(prediction.chain) - _chain_depth(reference.chain)))

        if reference.boundary_types:
            predicted = set(prediction.boundary_types)
            reference_set = set(reference.boundary_types)
            union = predicted | reference_set
            boundary_scores.append(len(predicted & reference_set) / len(union) if union else 0.0)

    precision = _mean(precisions)
    recall = _mean(recalls)
    # @3/MRR need a frozen prediction ordering. With the kernel's zero-or-one
    # candidate output they would only restate @1, so they stay None and the
    # caller must report "top-k not reported" instead of a number.
    ranked = [item for item in predictions if item.ranked_agents]
    ranking_reported = bool(ranked) and all(
        item.ranked_from_frozen_ordering for item in ranked)
    return RootCauseMetrics(
        schema=SCHEMA,
        runs=len(references),
        root_agent_at_1=_mean(at_1),
        root_agent_at_3=_mean(at_3) if ranking_reported else None,
        mrr=_mean(reciprocal) if ranking_reported else None,
        entry_point_accuracy=_mean(entry_hits),
        chain_precision=precision,
        chain_recall=recall,
        chain_f1=_f1(precision, recall),
        chain_depth_mae=_mean(depth_errors),
        boundary_type_accuracy=_mean(boundary_scores),
        missing_root_predictions=missing_root,
        ranking_reported=ranking_reported,
    )
