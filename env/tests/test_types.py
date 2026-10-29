"""Tests for BenchmarkScore calculation."""
from mtagentlens_benchmark.types import (
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
    # A default score has no measured dimension. The false-positive dimension
    # is only counted when a baseline was actually assessed; awarding it for
    # "no data" inflated every score by 15 points.
    assert overall == 0.0, f"Expected 0 with no measured dimension, got {overall}"


def test_unassessed_false_positive_dimension_is_excluded():
    score = BenchmarkScore(
        detection=DetectionMetrics(f1=1.0),
        false_positive=FalsePositiveMetrics(baseline_assessed=False),
    )
    # Weights renormalise over measured dimensions: 0.35 / (0.35+0.20+0.15+0.15).
    assert abs(score.overall - 41.176470588) < 0.01
    assessed = BenchmarkScore(
        detection=DetectionMetrics(f1=1.0),
        false_positive=FalsePositiveMetrics(baseline_assessed=True, baseline_fp_rate=0.0),
    )
    # A measured zero-FP baseline adds its own 0.15 weight and contribution:
    # (0.35*1.0 + 0.15*1.0) / 1.0.
    assert abs(assessed.overall - 50.0) < 0.01


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
