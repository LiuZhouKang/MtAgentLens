"""Python replica of Rust collector/src/responsibility/classification/scoring.rs + fusion.rs.

These functions are the EXACT Python equivalent of the Rust scoring pipeline.
Used by evaluate_mtagentlens.py and ablation/run_staircase.py.

IMPORTANT: When Rust scoring.rs changes, these functions MUST be updated to match.
See docs/design/experiment-design.md
"""

from __future__ import annotations
import math


def sub_score_rule(rule_hits: list[dict]) -> float:
    """rule_score = unsafe_hits / max(total_hits, 1)"""
    total = max(len(rule_hits), 1)
    unsafe = sum(1 for h in rule_hits if h.get("decision") == "Unsafe")
    return unsafe / total


def sub_score_pattern(patterns: list[dict]) -> float:
    """pattern_score = mean confidence of matched templates"""
    if not patterns:
        return 0.0
    return sum(p.get("confidence", 0.0) for p in patterns) / len(patterns)


def sub_score_boundary(pbc_count: int, total_edges: int, anomaly_count: int | None = None) -> float:
    """boundary_score = anomaly_edges / max(total_edges, 1)

    If anomaly_count is provided (sum of 5 edge types matching Rust:
    PolicyBoundaryCrossing + TaintPropagatesTo + AgentDelegatesTo +
    ContextFlowsFrom + CompetingExplanation), use it; otherwise
    fallback to pbc_count for backward compatibility."""
    numerator = anomaly_count if anomaly_count is not None else pbc_count
    return numerator / max(total_edges, 1)


def sub_score_evidence(evidence_records: list[dict]) -> float:
    """evidence_score = avg confidence of records with conf >= 0.45"""
    MIN_CONF = 0.45
    quality = [e for e in evidence_records if e.get("confidence", 0.0) >= MIN_CONF]
    if not quality:
        return 0.0
    return sum(e["confidence"] for e in quality) / len(quality)


def sub_score_role(pbc_count: int, agent_count: int) -> float:
    """role_score = role_mismatch_edges / max(agent_count, 1)"""
    return min(pbc_count / max(agent_count, 1), 1.0)


def sub_score_repetition(repetition_count: int) -> float:
    """repetition_score = min(rep_count/50, 1.0)"""
    return min(repetition_count / 50.0, 1.0)


def sub_score_behavioral(confirmed_pbc: int, high_conf_evidence: int) -> float:
    """behavioral_score = min((confirmed_pbc + high_conf_evidence) / 50, 1.0)

    confirmed_pbc: count of evidence-backed PBC edges (not binary flag).
    Matches Rust sub_score_behavioral which counts PBC edges where
    evidence_refs is non-empty. Combined saturates at 50 -> 1.0."""
    return min((confirmed_pbc + high_conf_evidence) / 50.0, 1.0)


def compute_owa(scores: list[float], orness: float = 0.70) -> float:
    """Ordered Weighted Averaging (exact replica of fusion.rs)."""
    n = len(scores)
    if n == 0:
        return 0.0
    sorted_scores = sorted(scores)
    weights = []
    sum_w = 0.0
    for i in range(n):
        pos = float(i)
        w = (pos / max(n - 1, 1)) ** ((1.0 - orness) / max(orness, 0.01))
        weights.append(w)
        sum_w += w
    if sum_w > 1e-9:
        weights = [w / sum_w for w in weights]
    return sum(s * w for s, w in zip(sorted_scores, weights))


def compute_weighted_geometric_mean(scores: list[float], weights: list[float]) -> float:
    """Weighted geometric mean (exact replica of fusion.rs)."""
    EPS = 0.01
    weight_sum = sum(weights)
    if weight_sum < 1e-9:
        return 0.0
    log_sum = sum(w * math.log(max(s, EPS)) for s, w in zip(scores, weights))
    return math.exp(log_sum / weight_sum)


def fuse_dimensions(dims: list[float], dim_weights: list[float]) -> float:
    """OWA(0.70) + GeoMean(0.30) fusion (exact replica of fusion.rs)."""
    owa = compute_owa(dims, 0.70)
    geo = compute_weighted_geometric_mean(dims, dim_weights)
    # GeoMean over active dimensions only (v3: dims > 0.01)
    active = [(d, w) for d, w in zip(dims, dim_weights) if d > 0.01]
    if not active:
        return owa

    active_dims = [a[0] for a in active]
    active_weights = [a[1] for a in active]
    geo = compute_weighted_geometric_mean(active_dims, active_weights)
    return owa * 0.80 + geo * 0.20


def platt_sigmoid(score: float, a: float = 7.0, b: float = -3.0) -> float:
    """Platt sigmoid calibration (exact replica of fusion.rs -- active-dim GeoMean)."""
    z = a * score + b
    return 1.0 / (1.0 + math.exp(-max(min(z, 50.0), -50.0)))


def compute_score(
    rule_hits: list[dict],
    patterns: list[dict],
    pbc_count: int,
    total_edges: int,
    evidence_records: list[dict],
    agent_count: int,
    repetition_count: int,
    anomaly_edge_count: int | None = None,
    confirmed_pbc: int | None = None,
    gate_pbc: int | None = None,
) -> float:
    """Replica of scoring.rs::compute_score().

    Args:
        anomaly_edge_count: Sum of 5 anomaly edge types (PBC + TaintPropagatesTo +
            AgentDelegatesTo + ContextFlowsFrom + CompetingExplanation).
            Defaults to pbc_count if not provided.
        confirmed_pbc: Count of PBC edges WITH evidence_refs (evidence-backed).
            Defaults to pbc_count if not provided.
        gate_pbc: PBC count used for the soft gate. If None, uses pbc_count.
            In lesion studies, all layers share the same gate_pbc (real value)
            while pbc_count varies per layer.
    """
    # D1: 5 anomaly edge types (matches Rust, not just PBC)
    anomaly = anomaly_edge_count if anomaly_edge_count is not None else pbc_count
    rule = sub_score_rule(rule_hits)
    pattern = sub_score_pattern(patterns)
    boundary = sub_score_boundary(anomaly, total_edges)
    evidence = sub_score_evidence(evidence_records)
    role = sub_score_role(pbc_count, agent_count)
    repetition = sub_score_repetition(repetition_count)

    # D6: evidence-backed PBC only (matches Rust evidence_refs check)
    ev_backed = confirmed_pbc if confirmed_pbc is not None else pbc_count
    high_conf_evidence = sum(1 for e in evidence_records if e.get("confidence", 0.0) >= 0.80)
    behavioral = sub_score_behavioral(ev_backed, high_conf_evidence)

    dims = [
        max(boundary, rule),  # D1: Boundary severity (5 anomaly types)
        pattern,               # D2: Content patterns
        role,                  # D3: Agentic amplification
        repetition,            # D4: Temporal dynamics
        evidence,              # D5: Cross-context corroboration
        behavioral,            # D6: Behavioral deviation (evidence-backed)
    ]

    dim_weights = [0.28, 0.25, 0.20, 0.10, 0.10, 0.07]
    raw = fuse_dimensions(dims, dim_weights)
    calibrated = platt_sigmoid(raw)
    score = min(calibrated, 1.0)

    # Soft PBC gate: gate_pbc controls the gate (separate from fusion PBC).
    # In lesion studies, all layers share the real gate_pbc so the gate
    # is NOT a confound when comparing layers.
    gate_pbc_val = gate_pbc if gate_pbc is not None else pbc_count
    has_unsafe_rules = any(h.get("decision") == "Unsafe" for h in rule_hits)
    pbc_gate_k = 10.0
    pbc_gate_steepness = 0.5
    actual_pbc = float(gate_pbc_val)
    gate_factor = 1.0 / (1.0 + math.exp(-(actual_pbc - pbc_gate_k) / pbc_gate_steepness))
    effective_gate = max(gate_factor, 0.3) if has_unsafe_rules else gate_factor
    return min(score * effective_gate, 1.0)


def route_score(score: float, threshold_unsafe: float = 0.70, threshold_safe: float = 0.15) -> str:
    """Replica of scoring.rs::route_score()."""
    if score > threshold_unsafe:
        return "direct_unsafe"
    elif score < threshold_safe:
        return "direct_safe"
    else:
        return "to_observer"


def heuristic_decide(score: float) -> dict:
    """Replica of scoring.rs::heuristic_decide()."""
    if score > 0.70:
        decision, severity = "Unsafe", "Critical"
    elif score > 0.15:
        decision, severity = "Uncertain", "Warning"
    else:
        decision, severity = "Safe", "Info"
    return {
        "decision": decision,
        "severity": severity,
        "confidence": score,
        "reasoning": f"Heuristic: score={score:.3f} -> {decision}/{severity}",
    }
