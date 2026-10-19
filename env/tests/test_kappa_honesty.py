"""Kappa must report "undefined" as None, never as perfect agreement.

Regression: every degenerate input path used to return 1.0, so missing
annotation data scored as maximal inter-annotator agreement.
"""
from __future__ import annotations

from mtagentlens_benchmark.annotator.kappa import (
    compute_cohens_kappa,
    compute_pairwise_kappa,
)


class _Alert:
    def __init__(self, alert_id: str, severity):
        self.alert_id = alert_id
        self.severity = severity


class _GT:
    def __init__(self, annotator: str, alerts):
        self.annotator = annotator
        self.alerts = alerts


def test_fewer_than_two_annotators_is_none_not_perfect():
    assert compute_cohens_kappa([]) is None
    assert compute_cohens_kappa([[("a", "High")]]) is None


def test_no_common_items_is_none_not_perfect():
    # Two annotators, zero shared alerts -> nothing to compare.
    assert compute_cohens_kappa([[("a", "High")], [("b", "Low")]]) is None


def test_single_category_is_none_not_perfect():
    # po == pe == 1.0 -> 0/0; must be None, not 1.0.
    labels = [[("a", "High"), ("b", "High")], [("a", "High"), ("b", "High")]]
    assert compute_cohens_kappa(labels) is None


def test_partial_overlap_uses_only_shared_items():
    # 'c' is only rated by one annotator: it must not inflate n.
    labels = [
        [("a", "High"), ("b", "Low"), ("c", "High")],
        [("a", "High"), ("b", "High")],
    ]
    k = compute_cohens_kappa(labels)
    assert k is not None
    # po = 1/2 over the two shared items; pe = (1/2*1/2)+(1/2*1/2) = 0.5
    assert k == 0.0


def test_perfect_disagreement_across_two_categories():
    labels = [
        [("a", "High"), ("b", "Low")],
        [("a", "Low"), ("b", "High")],
    ]
    assert compute_cohens_kappa(labels) == -1.0


def test_pairwise_returns_empty_without_two_annotators():
    assert compute_pairwise_kappa([]) == {}
    assert compute_pairwise_kappa([_GT("lzk1", [])]) == {}


def test_pairwise_excludes_undefined_pairs_from_average():
    class Sev:
        def __init__(self, v):
            self.value = v

    gt1 = _GT("lzk1", [_Alert("a", Sev("High")), _Alert("b", Sev("Low"))])
    gt2 = _GT("lzk2", [_Alert("a", Sev("Low")), _Alert("b", Sev("High"))])
    # third annotator shares no alerts with the others -> None
    gt3 = _GT("lzk3", [_Alert("z", Sev("High"))])

    out = compute_pairwise_kappa([gt1, gt2, gt3])
    assert out["lzk1-lzk2"] == -1.0
    assert out["lzk1-lzk3"] is None
    assert out["lzk2-lzk3"] is None
    # average must consider only the defined pair
    assert out["average"] == -1.0
