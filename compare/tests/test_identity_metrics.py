"""Independent identity partitions: hand calculations and a naive oracle."""
import itertools
import random

import pytest

from compare.tools.identity_metrics import IdentityPartition, evaluate_identity_partition


def partition(labels, *, run_id="run-1", digest="a" * 64):
    return IdentityPartition(run_id, digest, {f"e{i}": value for i, value in enumerate(labels)})


def evaluate(truth, predicted):
    return evaluate_identity_partition(partition(truth), partition(predicted))


def test_complete_partition_matches_perfectly_without_requiring_same_actor_names():
    result = evaluate(["a", "a", "b", "b"], ["x", "x", "y", "y"])
    assert result["events"] == 4
    assert result["truth_clusters"] == result["predicted_clusters"] == 2
    assert result["pairwise"] == {"tp": 2, "fp": 0, "fn": 0,
                                   "precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert result["b_cubed"] == {"precision": 1.0, "recall": 1.0, "f1": 1.0}


def test_event_identity_alignment_does_not_depend_on_dictionary_insertion_order():
    truth = partition(["a", "a", "b", "b"])
    predicted = IdentityPartition("run-1", "a" * 64,
                                  {"e0": "x", "e2": "y", "e1": "x", "e3": "y"})
    result = evaluate_identity_partition(truth, predicted)
    assert result["pairwise"]["f1"] == 1.0
    assert result["b_cubed"]["f1"] == 1.0


def test_merge_and_split_hand_calculation():
    result = evaluate(["a", "a", "b", "b"], ["x", "x", "x", "y"])
    assert result["pairwise"] == {"tp": 1, "fp": 2, "fn": 1,
                                   "precision": 1 / 3, "recall": 1 / 2, "f1": 0.4}
    assert result["b_cubed"]["precision"] == pytest.approx(2 / 3)
    assert result["b_cubed"]["recall"] == 0.75
    assert result["b_cubed"]["f1"] == pytest.approx(12 / 17)
    assert result["merged_predicted_clusters"] == 1
    assert result["split_truth_clusters"] == 1


def test_all_merge_and_all_split_are_different_errors():
    merged = evaluate(["a", "a", "b", "b"], ["x"] * 4)
    assert merged["pairwise"]["fp"] == 4
    assert merged["pairwise"]["fn"] == 0
    split = evaluate(["a", "a"], ["x", "y"])
    assert split["pairwise"] == {"tp": 0, "fp": 0, "fn": 1,
                                  "precision": None, "recall": 0.0, "f1": 0.0}
    assert split["b_cubed"]["precision"] == 1.0
    assert split["b_cubed"]["recall"] == 0.5


@pytest.mark.parametrize("truth,predicted", [([], []), (["a"], ["x"]),
                                            (["a", "b"], ["x", "y"])])
def test_no_event_pairs_are_undefined_instead_of_perfect(truth, predicted):
    result = evaluate(truth, predicted)
    assert result["pairwise"] == {"tp": 0, "fp": 0, "fn": 0,
                                   "precision": None, "recall": None, "f1": None}
    if not truth:
        assert result["b_cubed"] == {"precision": None, "recall": None, "f1": None}


@pytest.mark.parametrize("other", [partition(["x"], run_id="another-run"),
                                   partition(["x"], digest="b" * 64)])
def test_different_runs_or_evidence_bundles_cannot_be_compared(other):
    with pytest.raises(ValueError):
        evaluate_identity_partition(partition(["a"]), other)


def test_missing_predictions_are_not_silently_dropped():
    with pytest.raises(ValueError, match="(?i)event"):
        evaluate_identity_partition(partition(["a", "a"]), partition(["x"]))


@pytest.mark.parametrize("label", [None, "", " ", ["x", "y"], {"x", "y"}, 1])
def test_unknown_or_candidate_assignments_are_not_partitions(label):
    with pytest.raises(ValueError):
        evaluate(["a"], [label])
    with pytest.raises(ValueError):
        evaluate([label], ["x"])


@pytest.mark.parametrize("record", [partition(["x"], run_id=""),
                                    partition(["x"], digest="not-a-sha256"),
                                    IdentityPartition("run-1", "a" * 64, {"": "x"})])
def test_invalid_bindings_or_event_identifiers_fail(record):
    with pytest.raises(ValueError):
        evaluate_identity_partition(record, record)


def test_random_small_partitions_match_independent_naive_pair_and_event_counts():
    rng = random.Random(42)
    for _ in range(60):
        n = rng.randrange(2, 10)
        truth = [str(rng.randrange(3)) for _ in range(n)]
        predicted = [str(rng.randrange(4)) for _ in range(n)]
        result = evaluate(truth, predicted)
        pairs = list(itertools.combinations(range(n), 2))
        assert result["pairwise"]["tp"] == sum(truth[i] == truth[j] and predicted[i] == predicted[j] for i, j in pairs)
        assert result["pairwise"]["fp"] == sum(truth[i] != truth[j] and predicted[i] == predicted[j] for i, j in pairs)
        assert result["pairwise"]["fn"] == sum(truth[i] == truth[j] and predicted[i] != predicted[j] for i, j in pairs)
        precision = recall = 0.0
        for i in range(n):
            t = {j for j in range(n) if truth[j] == truth[i]}
            p = {j for j in range(n) if predicted[j] == predicted[i]}
            precision += len(t & p) / len(p)
            recall += len(t & p) / len(t)
        assert result["b_cubed"]["precision"] == pytest.approx(precision / n)
        assert result["b_cubed"]["recall"] == pytest.approx(recall / n)


def test_pair_counts_use_cluster_sizes_without_materializing_quadratic_pairs():
    result = evaluate(["a"] * 10_000, ["x"] * 10_000)
    assert result["pairwise"]["tp"] == 49_995_000
