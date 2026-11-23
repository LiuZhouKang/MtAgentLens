# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Reference oracle for evidence-bounded cross-layer association.

This is deliberately NOT a production scorer and its ranks are NOT probabilities.
It is an executable contract that can reject unsupported unique-attribution claims.
The oracle assumes that an 'observed' bridge from the *allowlisted trusted
producer* is genuine. Verifying that instrumentation is a separate M0 task.

A raw process tree, TLS pointer, tool_call_id, task text, or temporal proximity
can only generate MAY hypotheses, never a unique MUST assignment.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional
import json


class EvidenceTier(str, Enum):
    OBSERVED = "observed"
    DERIVED = "derived"
    HYPOTHESIZED = "hypothesized"


class CaptureStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class LinkStatus(str, Enum):
    MUST = "MUST"
    MAY = "MAY"
    NO = "NO"
    INDETERMINATE = "INDETERMINATE"


TRUSTED_BRIDGE_PRODUCERS = frozenset({"instrumented_dispatch", "trusted_harness"})


@dataclass(frozen=True, order=True)
class ProcessKey:
    pid: int
    start_time_ns: int
    namespace_id: str = "unknown"

    def __post_init__(self) -> None:
        if self.pid <= 0 or self.start_time_ns < 0 or not self.namespace_id:
            raise ValueError("invalid process identity")

    @property
    def complete(self) -> bool:
        return self.start_time_ns > 0 and self.namespace_id != "unknown"


@dataclass(frozen=True)
class IntentFact:
    intent_id: str
    timestamp_ns: int
    process: Optional[ProcessKey] = None
    resource: Optional[str] = None
    tool_call_id: Optional[str] = None
    clock_domain: str = "monotonic_boot"
    tier: EvidenceTier = EvidenceTier.HYPOTHESIZED
    capture: CaptureStatus = CaptureStatus.UNKNOWN


@dataclass(frozen=True)
class ActionFact:
    action_id: str
    timestamp_ns: int
    process: Optional[ProcessKey] = None
    resource: Optional[str] = None
    tool_call_id: Optional[str] = None
    clock_domain: str = "monotonic_boot"
    tier: EvidenceTier = EvidenceTier.HYPOTHESIZED
    capture: CaptureStatus = CaptureStatus.UNKNOWN


@dataclass(frozen=True)
class BridgeFact:
    """Exact invocation→OS action bridge claimed by a particular producer.

    A text field or user-authored conversation id is NOT an observed bridge.
    The producer identity is verified independently of model-generated payloads.
    """
    evidence_id: str
    intent_id: str
    action_id: str
    producer: str
    tier: EvidenceTier
    # Set only by a separately validated capture-ingestion boundary; never by JSON.
    trusted_ingest: bool = False


@dataclass(frozen=True)
class NegativeWitness:
    """Independently attested exclusion for an exact pair (closed-world fact).

    This is not the absence of a match; absence cannot prove a negative.
    """
    evidence_id: str
    intent_id: str
    action_id: str
    producer: str
    tier: EvidenceTier
    trusted_ingest: bool = False


@dataclass(frozen=True)
class WitnessLedger:
    intents: tuple[IntentFact, ...]
    actions: tuple[ActionFact, ...]
    bridges: tuple[BridgeFact, ...] = ()
    exclusions: tuple[NegativeWitness, ...] = ()
    # Explicit observed parent links (child, parent), keyed by full generation.
    process_parent_edges: tuple[tuple[ProcessKey, ProcessKey], ...] = ()


@dataclass(frozen=True)
class PairAssessment:
    intent_id: str
    action_id: str
    status: LinkStatus
    evidence_refs: tuple[str, ...]
    support: tuple[str, ...]
    # Deterministic comparison key, NOT a probability or causal certainty.
    rank: tuple[int, int, int]
    contradictions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActionAssessment:
    action_id: str
    status: LinkStatus
    selected_intent_id: Optional[str]
    plausible_intent_ids: tuple[str, ...]
    pairs: tuple[PairAssessment, ...]
    contradictions: tuple[str, ...] = ()


def _valid_bridge(value: BridgeFact | NegativeWitness) -> bool:
    return (value.trusted_ingest
            and value.tier is EvidenceTier.OBSERVED
            and value.producer in TRUSTED_BRIDGE_PRODUCERS
            and bool(value.evidence_id.strip()))


def _lineage(intent_proc: Optional[ProcessKey], action_proc: Optional[ProcessKey],
             edges: tuple[tuple[ProcessKey, ProcessKey], ...]) -> bool:
    """A witnessed full-generation ancestor path, never raw PID equality."""
    if not intent_proc or not action_proc or not intent_proc.complete or not action_proc.complete:
        return False
    if intent_proc == action_proc:
        return False
    parents: dict[ProcessKey, ProcessKey] = {}
    ambiguous: set[ProcessKey] = set()
    for child, parent in edges:
        if not child.complete or not parent.complete:
            continue
        if child in parents and parents[child] != parent:
            ambiguous.add(child)
        parents[child] = parent
    current = action_proc
    visited: set[ProcessKey] = set()
    for _ in range(32):
        if current in visited or current in ambiguous:
            return False
        visited.add(current)
        parent = parents.get(current)
        if parent is None:
            return False
        if parent == intent_proc:
            return True
        current = parent
    return False


def _pair(intent: IntentFact, action: ActionFact,
          bridges: tuple[BridgeFact, ...], exclusions: tuple[NegativeWitness, ...],
          parent_edges: tuple[tuple[ProcessKey, ProcessKey], ...]) -> PairAssessment:
    supporting_bridges = tuple(b for b in bridges if b.intent_id == intent.intent_id
                               and b.action_id == action.action_id)
    explicit_negative = tuple(n for n in exclusions if n.intent_id == intent.intent_id
                              and n.action_id == action.action_id and _valid_bridge(n))
    strong = tuple(b for b in supporting_bridges if _valid_bridge(b))
    attrs: set[str] = set()
    refs: set[str] = set()
    issues: set[str] = set()

    if strong:
        attrs.add("trusted_exact_dispatch_witness")
        refs.update(b.evidence_id for b in strong)
    elif supporting_bridges:
        attrs.add("unverified_bridge_assertion")
        refs.update(b.evidence_id for b in supporting_bridges if b.evidence_id.strip())

    # A complete, precise generation is essential. A raw PID alone is not.
    same_process = (intent.process is not None and action.process is not None
                    and intent.process.complete and action.process.complete
                    and intent.process == action.process)
    if same_process:
        attrs.add("same_process_generation")
    elif _lineage(intent.process, action.process, parent_edges):
        attrs.add("observed_process_lineage")

    if intent.resource and action.resource and intent.resource == action.resource:
        attrs.add("exact_resource")
    if intent.tool_call_id and action.tool_call_id and intent.tool_call_id == action.tool_call_id:
        attrs.add("native_call_reference")

    if intent.clock_domain == action.clock_domain:
        if action.timestamp_ns < intent.timestamp_ns:
            issues.add("observed_order_inversion")
        time_distance = abs(intent.timestamp_ns - action.timestamp_ns)
        # A tie-break only; deliberately not a promotion to MUST.
        temporal_rank = max(0, 10_000_000_000 - min(time_distance, 10_000_000_000))
        attrs.add("comparable_clock")
    else:
        temporal_rank = 0
        issues.add("incomparable_clocks")

    if intent.capture is not CaptureStatus.HEALTHY or action.capture is not CaptureStatus.HEALTHY:
        issues.add("capture_quality_unproven")
    if intent.tier is not EvidenceTier.OBSERVED or action.tier is not EvidenceTier.OBSERVED:
        issues.add("endpoint_not_observed")

    if explicit_negative:
        refs.update(n.evidence_id for n in explicit_negative)
    if strong and explicit_negative:
        issues.add("contradictory_trusted_witnesses")
        status = LinkStatus.INDETERMINATE
    elif explicit_negative:
        status = LinkStatus.NO
    elif strong and (not issues.intersection({"capture_quality_unproven", "endpoint_not_observed"})):
        status = LinkStatus.MUST
    elif strong or supporting_bridges or same_process or "observed_process_lineage" in attrs or "native_call_reference" in attrs:
        # Exact resource alone across unrelated processes is not enough to link.
        status = LinkStatus.MAY
    else:
        status = LinkStatus.INDETERMINATE

    # Rank is interpretable and order-independent; duplicate evidence does not boost it.
    support_priority = 4 if strong else 3 if supporting_bridges else 2 if "native_call_reference" in attrs else 1 if same_process or "observed_process_lineage" in attrs else 0
    precise_features = sum(k in attrs for k in ("same_process_generation", "observed_process_lineage", "exact_resource", "native_call_reference"))
    return PairAssessment(intent.intent_id, action.action_id, status,
                          tuple(sorted(refs)), tuple(sorted(attrs)),
                          (support_priority, precise_features, temporal_rank), tuple(sorted(issues)))


def reconstruct(ledger: WitnessLedger) -> tuple[ActionAssessment, ...]:
    """Produce reproducible abstention-aware association candidates.

    This oracle is O(I*A) for clarity and intended for finite fixture traces,
    not high-volume streaming. It is intentionally separate from production.
    """
    intents = tuple(sorted(ledger.intents, key=lambda it: it.intent_id))
    actions = tuple(sorted(ledger.actions, key=lambda ac: ac.action_id))
    if len({i.intent_id for i in intents}) != len(intents):
        raise ValueError("duplicate intent identifier")
    if len({a.action_id for a in actions}) != len(actions):
        raise ValueError("duplicate action identifier")
    if any(not i.intent_id or i.timestamp_ns < 0 for i in intents):
        raise ValueError("malformed intent")
    if any(not a.action_id or a.timestamp_ns < 0 for a in actions):
        raise ValueError("malformed action")
    # A reference to an absent fact is an input-contract failure, not a
    # silent absence of evidence. Prevent passing a no-op test with no pairs.
    intent_ids = {item.intent_id for item in intents}
    action_ids = {item.action_id for item in actions}
    for witness in (*ledger.bridges, *ledger.exclusions):
        if witness.intent_id not in intent_ids or witness.action_id not in action_ids:
            raise ValueError(f"orphan witness {witness.evidence_id!r}")

    out = []
    for action in actions:
        pairs = [_pair(intent, action, ledger.bridges, ledger.exclusions,
                       ledger.process_parent_edges) for intent in intents]
        pairs.sort(key=lambda p: (-p.rank[0], -p.rank[1], -p.rank[2], p.intent_id))
        strong = [p for p in pairs if p.status is LinkStatus.MUST]
        possible = [p for p in pairs if p.status in (LinkStatus.MAY, LinkStatus.MUST)]
        issues: set[str] = set()
        if len(strong) > 1:
            # Two independently attested exclusive invocations for one action:
            # this is inconsistent instrumentation, NOT a reason to select one.
            status = LinkStatus.INDETERMINATE
            chosen = None
            issues.add("multiple_strong_owners")
        elif len(strong) == 1:
            status = LinkStatus.MUST
            chosen = strong[0].intent_id
        elif possible:
            status = LinkStatus.MAY
            chosen = None
        else:
            status = LinkStatus.INDETERMINATE
            chosen = None
            issues.add("no_positive_unique_link")
        if any("contradictory_trusted_witnesses" in p.contradictions for p in pairs):
            # A verified contradiction blocks unique attribution for this action.
            status = LinkStatus.INDETERMINATE
            chosen = None
            issues.add("contradictory_trusted_witnesses")
        out.append(ActionAssessment(action.action_id, status, chosen,
                                    tuple(sorted(p.intent_id for p in possible)),
                                    tuple(pairs), tuple(sorted(issues))))
    return tuple(out)


def canonical_result(results: Iterable[ActionAssessment]) -> str:
    """Canonical JSON for hash/replay comparisons (no randomized object ordering)."""
    import dataclasses
    payload = [dataclasses.asdict(x) for x in results]
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=lambda item: item.value if isinstance(item, Enum) else str(item))
