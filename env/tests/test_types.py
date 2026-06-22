"""Tests for BenchmarkScore calculation."""
from mtagentprism_benchmark.types import (
    BenchmarkScore, DetectionMetrics, CausalChainMetrics,
    IdentityMetrics, FalsePositiveMetrics, RobustnessMetrics,
)


def test_overall_score_perfect():
    score = BenchmarkScore(
        detection=DetectionMetrics(f1=1.0),
        causal_chain=CausalChainMetrics(chain_completeness=1.0),
        identity=IdentityMetrics(process_mapping_accuracy=1.0),
        false_positive=FalsePositiveMetrics(baseline_fp_rate=0.0),
        robustness=RobustnessMetrics(
            signal_contribution={"pbc_detection": 1.0, "argument_matching": 1.0},
        ),
    )
    overall = score.overall
    assert 95 <= overall <= 100, f"Expected ~100, got {overall}"


def test_overall_score_zero():
    score = BenchmarkScore()
    overall = score.overall
    # Zero FP rate contributes (1.0 - 0.0) * 0.15 * 100 = 15 points.
    # This is correct: no false positives is a positive signal.
    assert 14.9 <= overall <= 15.1, f"Expected ~15, got {overall}"


def test_detection_metrics_defaults():
    dm = DetectionMetrics()
    assert dm.precision == 0.0
    assert dm.recall == 0.0
    assert dm.f1 == 0.0
    assert dm.tp == 0


def test_benchmark_score_roundtrip():
    """Verify BenchmarkScore can be created with all sub-metrics."""
    score = BenchmarkScore(
        detection=DetectionMetrics(precision=0.9, recall=0.8, f1=0.85, tp=10, fp=2, fn=3),
        causal_chain=CausalChainMetrics(chain_completeness=0.75, broken_chains=3),
        identity=IdentityMetrics(process_mapping_accuracy=0.95),
        false_positive=FalsePositiveMetrics(baseline_fp_rate=0.1),
        robustness=RobustnessMetrics(signal_contribution={"signal_a": 0.5}),
    )
    assert score.detection.tp == 10
    assert score.causal_chain.broken_chains == 3
    assert score.identity.process_mapping_accuracy == 0.95
    assert 0 < score.overall < 100
