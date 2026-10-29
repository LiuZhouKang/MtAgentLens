# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Attribution metrics: the reviewer's E6 needs fixed definitions before runs."""

import pytest

from compare.tools.root_cause_metrics import (
    SCHEMA,
    RootCausePrediction,
    RootCauseReference,
    evaluate_root_cause,
)


def ref(run="r1", root="agent-a", entry="fixture:docs.md", chain=(("agent-a", "agent-b"),),
        boundary=("policy_boundary",)):
    return RootCauseReference(run_id=run, root_agent=root, entry_point=entry,
                              chain=tuple(chain), boundary_types=tuple(boundary))


def pred(run="r1", ranking=("agent-a",), entry="fixture:docs.md",
         chain=(("agent-a", "agent-b"),), boundary=("policy_boundary",), frozen=False):
    return RootCausePrediction(run_id=run, ranked_agents=tuple(ranking),
                               entry_point=entry, chain=tuple(chain),
                               boundary_types=tuple(boundary),
                               ranked_from_frozen_ordering=frozen)


def test_a_perfect_prediction_scores_one_everywhere():
    metrics = evaluate_root_cause([ref()], [pred(frozen=True)])
    assert metrics.schema == SCHEMA and metrics.runs == 1
    assert metrics.root_agent_at_1 == 1.0
    assert metrics.root_agent_at_3 == 1.0
    assert metrics.mrr == 1.0
    assert metrics.entry_point_accuracy == 1.0
    assert (metrics.chain_precision, metrics.chain_recall, metrics.chain_f1) == (1.0, 1.0, 1.0)
    assert metrics.chain_depth_mae == 0.0
    assert metrics.boundary_type_accuracy == 1.0
    assert metrics.missing_root_predictions == 0


def test_rank_three_hits_at_three_but_not_at_one():
    metrics = evaluate_root_cause([ref()], [pred(ranking=("x", "y", "agent-a"), frozen=True)])
    assert metrics.root_agent_at_1 == 0.0
    assert metrics.root_agent_at_3 == 1.0
    assert metrics.mrr == pytest.approx(1 / 3)
    assert metrics.missing_root_predictions == 0


def test_absent_root_counts_as_a_miss_not_as_an_error():
    metrics = evaluate_root_cause([ref()], [pred(ranking=("x", "y"), frozen=True)])
    assert metrics.root_agent_at_1 == 0.0
    assert metrics.root_agent_at_3 == 0.0
    assert metrics.mrr == 0.0
    assert metrics.missing_root_predictions == 1


def test_chain_precision_and_recall_are_separate():
    metrics = evaluate_root_cause(
        [ref(chain=(("a", "b"), ("b", "c")))],
        [pred(chain=(("a", "b"), ("c", "d")))],
    )
    assert metrics.chain_precision == pytest.approx(0.5)
    assert metrics.chain_recall == pytest.approx(0.5)
    assert metrics.chain_f1 == pytest.approx(0.5)
    assert metrics.chain_depth_mae == 1.0


def test_depth_mae_counts_edges_not_agents():
    metrics = evaluate_root_cause([ref(chain=(("a", "b"),))], [pred(chain=())])
    assert metrics.chain_depth_mae == 1.0


def test_branching_graph_depth_is_longest_path_not_total_edges():
    metrics = evaluate_root_cause(
        [ref(chain=(("a", "b"), ("a", "c"), ("a", "d")))],
        [pred(chain=(("a", "b"), ("b", "c"), ("c", "d")))],
    )
    assert metrics.chain_depth_mae == 2.0


@pytest.mark.parametrize("reference_cycle", [True, False])
def test_cyclic_chains_are_rejected_instead_of_reporting_fake_depth(reference_cycle):
    cycle = (("a", "b"), ("b", "a"))
    with pytest.raises(ValueError, match="cycle"):
        evaluate_root_cause(
            [ref(chain=cycle) if reference_cycle else ref()],
            [pred() if reference_cycle else pred(chain=cycle)],
        )


def test_metric_groups_without_labels_are_none_not_zero():
    metrics = evaluate_root_cause([ref(entry="", chain=(), boundary=())], [pred(entry=None, chain=(), boundary=())])
    assert metrics.entry_point_accuracy is None
    assert metrics.chain_precision is None
    assert metrics.chain_recall is None
    assert metrics.chain_depth_mae is None
    assert metrics.boundary_type_accuracy is None


def test_boundary_accuracy_is_a_jaccard():
    metrics = evaluate_root_cause(
        [ref(boundary=("policy_boundary", "taint"))],
        [pred(boundary=("policy_boundary",))],
    )
    assert metrics.boundary_type_accuracy == pytest.approx(0.5)


def test_run_ids_must_match_exactly():
    with pytest.raises(ValueError, match="Run ids must match"):
        evaluate_root_cause([ref(run="r1")], [pred(run="r2")])
    with pytest.raises(ValueError, match="Duplicate run id"):
        evaluate_root_cause([ref(), ref()], [pred()])
    with pytest.raises(ValueError, match="At least one"):
        evaluate_root_cause([], [])


def test_duplicate_ranked_agents_are_rejected():
    with pytest.raises(ValueError, match="duplicate ranked agent"):
        evaluate_root_cause([ref()], [pred(ranking=("a", "a"))])


@pytest.mark.parametrize('chain', [(("a", "b"), ("a", "b"))])
def test_reference_causal_edges_must_be_unique(chain):
    with pytest.raises(ValueError, match='duplicate'):
        evaluate_root_cause([ref(chain=chain)], [pred()])


def test_unknown_chain_is_excluded_from_depth_error():
    result = evaluate_root_cause([ref(chain=())], [pred(chain=(("x", "y"),))])
    assert result.chain_depth_mae is None


@pytest.mark.parametrize('run', ['', '  '])
def test_reference_requires_nonempty_run_identity(run):
    with pytest.raises(ValueError, match='run'):
        evaluate_root_cause([ref(run=run)], [pred(run=run)])


def test_a_single_candidate_ranking_is_not_reported_as_top_k():
    # The attribution kernel emits at most one ranked agent, so @3/MRR would be
    # a relabelled @1. Until a frozen multi-candidate ordering exists they must
    # stay unreported rather than be inferred from a degenerate ranking.
    metrics = evaluate_root_cause([ref()], [pred()])
    assert metrics.root_agent_at_1 == 1.0
    assert metrics.root_agent_at_3 is None
    assert metrics.mrr is None
    assert metrics.ranking_reported is False


def test_a_multi_candidate_ranking_must_declare_a_frozen_ordering():
    with pytest.raises(ValueError, match="(?i)frozen"):
        evaluate_root_cause([ref()], [pred(ranking=("x", "y", "agent-a"))])
