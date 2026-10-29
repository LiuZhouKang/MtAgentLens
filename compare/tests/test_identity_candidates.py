"""Candidate and abstention metrics retain the complete event cohort."""

import pytest

from compare.tools.identity_metrics import (
    IdentityCandidates,
    IdentityReference,
    evaluate_identity_candidates,
)


def assess(truth, predictions, mapping=None, **bindings):
    return evaluate_identity_candidates(
        IdentityReference("run", "a" * 64, truth),
        IdentityCandidates(
            bindings.get("run", "run"), bindings.get("digest", "a" * 64), predictions
        ),
        actor_mapping=mapping if mapping is not None else {"x": "alice", "y": "bob"},
    )


def test_hand_calculation_retains_unknown_truth_and_abstention():
    result = assess(
        {"e1": "alice", "e2": "bob", "e3": "alice", "e4": None, "e5": "bob"},
        {"e1": frozenset({"x"}), "e2": frozenset({"x", "y"}),
         "e3": None, "e4": frozenset({"y"}), "e5": frozenset({"x"})},
    )
    assert result["events"] == 5
    assert result["known_truth_events"] == 4
    assert result["unknown_truth_events"] == 1
    assert result["predictions"] == {"abstained": 1, "singleton": 3, "candidate_set": 1}
    assert result["scorable"] == {
        "answered": 3, "abstained": 1, "singleton": 2, "candidate_set": 1,
        "hits": 2, "singleton_correct": 1,
    }
    assert result["known_truth_coverage"] == 3 / 4
    assert result["singleton_coverage"] == 1 / 2
    assert result["selective_singleton_accuracy"] == 1 / 2
    assert result["singleton_correct_rate"] == 1 / 4
    assert result["answered_candidate_hit_rate"] == 2 / 3
    assert result["known_truth_hit_rate"] == 1 / 2
    assert result["mean_candidate_count"] == 4 / 3
    assert "pairwise" not in result


@pytest.mark.parametrize("truth,predictions", [({}, {}), ({"e": None}, {"e": None})])
def test_no_known_truth_is_undefined(truth, predictions):
    result = assess(truth, predictions)
    for key in ("known_truth_coverage", "singleton_coverage", "selective_singleton_accuracy",
                "singleton_correct_rate", "answered_candidate_hit_rate",
                "known_truth_hit_rate", "mean_candidate_count"):
        assert result[key] is None


def test_all_abstentions_have_zero_coverage_and_no_selective_accuracy():
    result = assess({"e1": "alice", "e2": "bob"}, {"e1": None, "e2": None})
    assert result["known_truth_coverage"] == 0
    assert result["singleton_correct_rate"] == 0
    assert result["known_truth_hit_rate"] == 0
    assert result["selective_singleton_accuracy"] is None
    assert result["answered_candidate_hit_rate"] is None
    assert result["mean_candidate_count"] is None


def test_explicit_mapping_is_used_instead_of_coincident_names():
    result = assess({"e": "alice"}, {"e": frozenset({"alice"})}, {"alice": "bob"})
    assert result["singleton_correct_rate"] == 0


def test_aliases_count_distinct_reference_candidates():
    result = assess({"e": "alice"}, {"e": frozenset({"x", "alias"})},
                    {"x": "alice", "alias": "alice"})
    assert result["mean_candidate_count"] == 1
    assert result["scorable"]["singleton"] == 1


@pytest.mark.parametrize("kwargs", [{"run": "other"}, {"digest": "b" * 64}])
def test_binding_mismatch_rejected(kwargs):
    with pytest.raises(ValueError):
        assess({"e": "alice"}, {"e": None}, **kwargs)


@pytest.mark.parametrize("predictions", [{}, {"e": None, "extra": None}])
def test_missing_or_extra_event_is_not_an_abstention(predictions):
    with pytest.raises(ValueError, match="event"):
        assess({"e": "alice"}, predictions)


@pytest.mark.parametrize("value", [frozenset(), "x", ["x"], {"x"}, 1, True,
                                   frozenset({""}), frozenset({None})])
def test_malformed_candidate_values_rejected(value):
    with pytest.raises(ValueError):
        assess({"e": "alice"}, {"e": value})


@pytest.mark.parametrize("value", ["", " ", [], {"alice"}, False])
def test_truth_is_unique_or_explicitly_unknown(value):
    with pytest.raises(ValueError):
        assess({"e": value}, {"e": None})


@pytest.mark.parametrize("mapping", [{}, {"x": None}, {"x": ""}, {None: "alice"}, []])
def test_unbound_or_invalid_actor_mapping_rejected(mapping):
    with pytest.raises(ValueError):
        assess({"e": "alice"}, {"e": frozenset({"x"})}, mapping)


def test_unknown_truth_predictions_still_require_mapping():
    with pytest.raises(ValueError):
        assess({"e": None}, {"e": frozenset({"unmapped"})})


def test_answered_unknown_truth_is_counted_but_never_scored():
    result = assess({"e": None}, {"e": frozenset({"x"})})
    assert result["predictions"]["singleton"] == 1
    assert result["scorable"]["answered"] == 0
    assert result["known_truth_coverage"] is None
    assert result["selective_singleton_accuracy"] is None
    assert result["answered_candidate_hit_rate"] is None


def test_actor_absent_from_truth_is_a_valid_incorrect_prediction():
    result = assess({"e": "alice"}, {"e": frozenset({"x"})}, {"x": "outsider"})
    assert result["known_truth_coverage"] == 1
    assert result["selective_singleton_accuracy"] == 0


def test_event_iteration_order_does_not_change_candidate_scores():
    truth = {"e1": "alice", "e2": "bob", "e3": None, "e4": "alice"}
    candidates = {"e1": frozenset({"x"}), "e2": frozenset({"x", "y"}),
                  "e3": frozenset({"x"}), "e4": None}
    assert assess(truth, candidates) == assess(truth, dict(reversed(list(candidates.items()))))


@pytest.mark.parametrize("reference,prediction", [
    (IdentityReference("", "a" * 64, {}), IdentityCandidates("", "a" * 64, {})),
    (IdentityReference("run", "bad", {}), IdentityCandidates("run", "bad", {})),
    (IdentityReference("run", "a" * 64, {"": None}), IdentityCandidates("run", "a" * 64, {"": None})),
    (IdentityReference("run", "a" * 64, []), IdentityCandidates("run", "a" * 64, {})),
])
def test_malformed_bindings_and_event_maps_rejected(reference, prediction):
    with pytest.raises(ValueError):
        evaluate_identity_candidates(reference, prediction, actor_mapping={})
