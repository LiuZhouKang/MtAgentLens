"""5-layer evaluation metrics computation."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from mtagentlens_benchmark.types import (
    DetectionMetrics,
    CausalChainMetrics,
    IdentityMetrics,
    FalsePositiveMetrics,
    RobustnessMetrics,
    BenchmarkScore,
    GroundTruth,
)


def compute_detection_metrics(
    db_path: Path,
    ground_truth: GroundTruth,
    time_window_tolerance_s: float = 5.0,
) -> DetectionMetrics:
    """Layer 1: Compute P/R/F1 for alert detection."""
    conn = sqlite3.connect(str(db_path))

    expected_ids = {a.alert_id for a in ground_truth.alerts}
    actual_rows = conn.execute(
        "SELECT alert_id FROM responsibility_alerts"
    ).fetchall()
    actual_ids = {r[0] for r in actual_rows}

    tp = len(expected_ids & actual_ids)
    fp = len(actual_ids - expected_ids)
    fn = len(expected_ids - actual_ids)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    conn.close()
    return DetectionMetrics(
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        tp=tp,
        fp=fp,
        fn=fn,
        time_window_tolerance_s=time_window_tolerance_s,
    )


def compute_causal_chain_metrics(
    db_path: Path,
    ground_truth: GroundTruth,
) -> CausalChainMetrics:
    """Layer 2: Compute causal chain completeness."""
    conn = sqlite3.connect(str(db_path))

    if not ground_truth.alerts:
        conn.close()
        return CausalChainMetrics(
            chain_completeness=1.0,
            link_precision=1.0,
            link_recall=1.0,
            broken_chains=0,
            avg_chain_length_match=1.0,
        )

    traceback_count = conn.execute(
        "SELECT COUNT(*) FROM responsibility_tracebacks"
    ).fetchone()[0]

    matched_links = 0
    for alert in ground_truth.alerts:
        claim_rows = conn.execute(
            "SELECT claim_id FROM responsibility_claims WHERE alert_id=?",
            (alert.alert_id,),
        ).fetchall()
        if claim_rows:
            matched_links += 1

    chain_completeness = matched_links / len(ground_truth.alerts)
    link_precision = min(matched_links / max(traceback_count, 1), 1.0)
    link_recall = matched_links / max(len(ground_truth.alerts), 1)
    broken_chains = len(ground_truth.alerts) - matched_links

    conn.close()
    return CausalChainMetrics(
        chain_completeness=round(chain_completeness, 4),
        link_precision=round(link_precision, 4),
        link_recall=round(link_recall, 4),
        broken_chains=broken_chains,
        avg_chain_length_match=round(traceback_count / max(len(ground_truth.alerts), 1), 2),
    )


def compute_identity_metrics(
    db_path: Path,
) -> IdentityMetrics:
    """Layer 3: Compute identity resolution accuracy."""
    conn = sqlite3.connect(str(db_path))

    ipm_agents = set(r[0] for r in conn.execute(
        "SELECT DISTINCT agent_id FROM identity_process_map"
    ))
    id_agents = set(r[0] for r in conn.execute(
        "SELECT DISTINCT agent_id FROM agent_identities"
    ))
    overlap = len(ipm_agents & id_agents)
    process_mapping_accuracy = overlap / max(len(id_agents), 1)

    conn.close()
    return IdentityMetrics(
        agent_precision=round(process_mapping_accuracy, 4),
        agent_recall=round(process_mapping_accuracy, 4),
        merging_errors=0,
        splitting_errors=0,
        process_mapping_accuracy=round(process_mapping_accuracy, 4),
    )


def compute_false_positive_metrics(
    baseline_results: list[tuple[Path, str]],
) -> FalsePositiveMetrics:
    """Layer 4: Compute false positive rates from baseline scenarios."""
    per_baseline: dict[str, int] = {}
    total_alerts = 0

    for db_path, scenario_id in baseline_results:
        if not db_path.exists():
            continue
        conn = sqlite3.connect(str(db_path))
        non_info = conn.execute(
            "SELECT COUNT(*) FROM responsibility_alerts WHERE severity != 'Info'"
        ).fetchone()[0]
        conn.close()
        per_baseline[scenario_id] = non_info
        total_alerts += non_info

    num_baselines = len(baseline_results) or 1
    baseline_fp_rate = total_alerts / num_baselines

    return FalsePositiveMetrics(
        baseline_fp_rate=round(baseline_fp_rate, 4),
        alert_fatigue_ratio=round(total_alerts / max(num_baselines * 100, 1), 4),
        avg_fp_confidence=0.0,
        per_baseline_breakdown=per_baseline,
    )


def compute_robustness_metrics(
    db_path: Path,
    ground_truth: GroundTruth,
) -> RobustnessMetrics:
    """Layer 5: Compute robustness/signal contribution metrics."""
    conn = sqlite3.connect(str(db_path))

    ev_breakdown = conn.execute(
        "SELECT match_level, COUNT(*) FROM correlation_evidence GROUP BY match_level"
    ).fetchall()

    signal_counts: dict[str, int] = {}
    for level, count in ev_breakdown:
        if level in ("L1Exact", "L2Normalized", "L3Containment"):
            signal_counts["argument_matching"] = signal_counts.get("argument_matching", 0) + count
        elif level == "L4Basename":
            signal_counts["basename_matching"] = signal_counts.get("basename_matching", 0) + count
        elif level == "L5Substring":
            signal_counts["substring_matching"] = signal_counts.get("substring_matching", 0) + count

    total = sum(signal_counts.values()) or 1
    signal_contribution = {
        k: round(v / total, 4) for k, v in signal_counts.items()
    }

    pbc_count = conn.execute(
        "SELECT COUNT(*) FROM graph_edges WHERE edge_type='PolicyBoundaryCrossing'"
    ).fetchone()[0]
    signal_contribution["pbc_detection"] = min(pbc_count / 10, 1.0) if pbc_count > 0 else 0.0

    conn.close()
    return RobustnessMetrics(signal_contribution=signal_contribution)


def compute_benchmark_score(
    results: list[tuple[Path, GroundTruth]],
    baseline_results: list[tuple[Path, str]],
) -> BenchmarkScore:
    """Compute the full 5-layer benchmark score from all result pairs."""
    all_detection = []
    all_causal = []
    all_identity = []
    all_robustness = []

    for db_path, gt in results:
        if not db_path.exists():
            continue
        all_detection.append(compute_detection_metrics(db_path, gt))
        all_causal.append(compute_causal_chain_metrics(db_path, gt))
        all_identity.append(compute_identity_metrics(db_path))
        all_robustness.append(compute_robustness_metrics(db_path, gt))

    n = len(all_detection) or 1

    avg_detection = DetectionMetrics(
        precision=round(sum(d.precision for d in all_detection) / n, 4),
        recall=round(sum(d.recall for d in all_detection) / n, 4),
        f1=round(sum(d.f1 for d in all_detection) / n, 4),
        tp=sum(d.tp for d in all_detection),
        fp=sum(d.fp for d in all_detection),
        fn=sum(d.fn for d in all_detection),
    )

    avg_causal = CausalChainMetrics(
        chain_completeness=round(sum(c.chain_completeness for c in all_causal) / n, 4),
        link_precision=round(sum(c.link_precision for c in all_causal) / n, 4),
        link_recall=round(sum(c.link_recall for c in all_causal) / n, 4),
        broken_chains=sum(c.broken_chains for c in all_causal),
        avg_chain_length_match=round(sum(c.avg_chain_length_match for c in all_causal) / n, 2),
    )

    avg_identity = IdentityMetrics(
        agent_precision=round(sum(i.agent_precision for i in all_identity) / n, 4),
        agent_recall=round(sum(i.agent_recall for i in all_identity) / n, 4),
        merging_errors=sum(i.merging_errors for i in all_identity),
        splitting_errors=sum(i.splitting_errors for i in all_identity),
        process_mapping_accuracy=round(sum(i.process_mapping_accuracy for i in all_identity) / n, 4),
    )

    fp_metrics = compute_false_positive_metrics(baseline_results)

    avg_signal_contribution: dict[str, float] = {}
    for r in all_robustness:
        for k, v in r.signal_contribution.items():
            avg_signal_contribution[k] = avg_signal_contribution.get(k, 0.0) + v
    for k in avg_signal_contribution:
        avg_signal_contribution[k] = round(avg_signal_contribution[k] / n, 4)

    avg_robustness = RobustnessMetrics(signal_contribution=avg_signal_contribution)

    return BenchmarkScore(
        detection=avg_detection,
        causal_chain=avg_causal,
        identity=avg_identity,
        false_positive=fp_metrics,
        robustness=avg_robustness,
    )
