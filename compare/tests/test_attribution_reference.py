# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Independent resolved attribution reference -> AttributionResults adapter.

The reference is supplied, never inferred from predictions. Claims are selected
only by occurrence action id; unknown or unmapped fields abstain instead of
being scored as zero.
"""

import hashlib
import json

import pytest

from compare.tools import attribution_reference as ar

RAW_BYTES = b'{"synthetic_capture":true}\n'
RAW_SHA = hashlib.sha256(RAW_BYTES).hexdigest()
UNKNOWN = {"status": "unknown"}


def provenance(**overrides):
    value = {
        "method": "ai_consensus_3",
        "human_review": False,
        "detector_blind": True,
        "reviewer_ids": ["reviewer-1", "reviewer-2", "reviewer-3"],
    }
    value.update(overrides)
    return value


def known_root(*actors):
    return {"status": "known", "actors": list(actors)}


def known_entry(node):
    return {"status": "known", "node_id": node}


def known_chain(*edges):
    return {"status": "known", "edges": [list(edge) for edge in edges]}


def unit(run="run-1", action="action-1", root=None, entry=None, chain=None):
    return {
        "run_id": run,
        "target_action_id": action,
        "root": UNKNOWN if root is None else root,
        "entry": UNKNOWN if entry is None else entry,
        "chain": UNKNOWN if chain is None else chain,
    }


def reference(*units, prov=None):
    return {
        "schema": ar.REFERENCE_SCHEMA,
        "version": 1,
        "frozen": True,
        "provenance": prov or provenance(),
        "units": list(units),
    }


def mapping(canonical, captured, kind="actor", evidence=("capture:evidence-1",)):
    return {
        "canonical_id": canonical,
        "kind": kind,
        "captured_id": captured,
        "evidence_refs": list(evidence),
    }


def alignment(mappings, raw_source="capture/raw.jsonl", raw_sha=None):
    return {
        "schema": ar.ALIGNMENT_SCHEMA,
        "version": 1,
        "raw_capture": {"source": raw_source, "sha256": raw_sha or RAW_SHA},
        "mappings": list(mappings),
    }


def occurrence(node_id, action_id=None):
    return {
        "node_id": node_id,
        "kind": "action" if action_id else "intent",
        "intent_id": None,
        "action_id": action_id,
        "message_id": None,
        "process": None,
        "json_pointer": None,
        "native_ref": None,
        "observed_at_ns": None,
    }


def claim(
    claim_id="claim-1",
    actions=("action-1",),
    run="run-1",
    root_actors=(),
    ranked=None,
    entry=None,
    chain=(),
    extra_nodes=(),
):
    occurrences = []
    nodes = set()

    def add(node_id):
        if node_id not in nodes:
            occurrences.append(occurrence(node_id))
            nodes.add(node_id)

    for action in actions:
        node_id = f"action:{action}"
        occurrences.append(occurrence(node_id, action))
        nodes.add(node_id)
    for node_id in extra_nodes:
        add(node_id)
    for edge in chain:
        for node_id in edge:
            add(node_id)
    if entry:
        add(entry)
    edges = [
        {
            "edge_id": f"edge-{index}",
            "edge_type": "intent_action_correlation",
            "source": source,
            "target": target,
            "support_refs": ["evidence:ev-1"],
            "evidence_id": "ev-1",
            "match_level": "strong",
            "outcome": "success",
            "observed_not_after": True,
            "note": None,
        }
        for index, (source, target) in enumerate(chain)
    ]
    groups = (
        [
            {
                "evidence_tier": "writer_source_candidate",
                "actors": list(root_actors),
                "readers": [],
                "writers": [],
                "support_refs": ["evidence:ev-1"],
            }
        ]
        if root_actors
        else []
    )
    return {
        "schema": ar.PREDICTION_SCHEMA,
        "run_id": run,
        "claim_id": claim_id,
        "traceback_ref": None,
        "executors": [],
        "occurrences": occurrences,
        "edges": edges,
        "source_entry_candidates": [],
        "unique_entry": entry,
        "root_groups": groups,
        "ranked_agents": list(ranked) if ranked is not None else None,
        "abstentions": [],
        "limitations": [],
        "truncated": False,
    }


def predictions(*claims, run="run-1"):
    return {"schema": ar.PREDICTION_SCHEMA, "run_id": run, "claims": list(claims)}


def write_inputs(
    tmp_path, reference_obj, alignment_obj, predictions_obj, *, raw_bytes=RAW_BYTES, write_raw=True
):
    if write_raw:
        raw_path = tmp_path / alignment_obj["raw_capture"]["source"]
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw_bytes)
    paths = []
    for name, value in (
        ("reference.json", reference_obj),
        ("alignment.json", alignment_obj),
        ("predictions.json", predictions_obj),
    ):
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding="utf-8")
        paths.append(path)
    return paths


def evaluate(
    tmp_path,
    reference_obj,
    alignment_obj,
    predictions_obj,
    *,
    raw_bytes=RAW_BYTES,
    write_raw=True,
):
    return ar.evaluate_files(
        *write_inputs(
            tmp_path,
            reference_obj,
            alignment_obj,
            predictions_obj,
            raw_bytes=raw_bytes,
            write_raw=write_raw,
        )
    )


def test_unknown_reference_fields_are_excluded_not_negative(tmp_path):
    report = evaluate(tmp_path, reference(unit()), alignment([]), predictions(claim()))
    metrics = report["metrics"]
    assert metrics["root_agent_at_1"] is None
    assert metrics["root_set_precision"] is None
    assert metrics["entry_point_accuracy"] is None
    assert metrics["untyped_chain_precision"] is None
    assert metrics["untyped_chain_depth_mae"] is None
    assert report["denominators"]["root"]["known"] == 0
    assert report["denominators"]["root"]["coverage"] is None
    assert report["units"][0]["fields"]["root"]["status"] == "unknown_reference"


def test_unknown_root_keeps_a_known_entry_evaluable(tmp_path):
    reference_obj = reference(unit(entry=known_entry("canonical:entry")))
    alignment_obj = alignment([mapping("canonical:entry", "intent:entry", "node")])
    prediction_obj = predictions(claim(entry="intent:entry"))
    report = evaluate(tmp_path, reference_obj, alignment_obj, prediction_obj)
    assert report["metrics"]["root_agent_at_1"] is None
    assert report["metrics"]["entry_point_accuracy"] == 1.0
    assert report["denominators"]["entry"] == {
        "known": 1,
        "aligned": 1,
        "scored": 1,
        "evaluated": 1,
        "misses": 0,
        "unaligned": 0,
        "ambiguous": 0,
        "unknown": 0,
        "coverage": 1.0,
    }


def test_known_reference_without_a_matching_prediction_is_a_miss(tmp_path):
    reference_obj = reference(
        unit(
            root=known_root("canonical:actor"),
            entry=known_entry("canonical:a"),
            chain=known_chain(("canonical:a", "canonical:b")),
        )
    )
    alignment_obj = alignment(
        [
            mapping("canonical:actor", "actor-a"),
            mapping("canonical:a", "intent:a", "node"),
            mapping("canonical:b", "intent:b", "node"),
        ]
    )
    prediction_obj = predictions(claim(actions=("action-other",)))
    report = evaluate(tmp_path, reference_obj, alignment_obj, prediction_obj)
    assert report["evaluation"]["units_missing_prediction"] == 1
    assert report["evaluation"]["units_evaluated"] == 0
    assert report["metrics"]["root_agent_at_1"] == 0.0
    assert report["metrics"]["root_set_recall"] == 0.0
    assert report["metrics"]["entry_point_accuracy"] == 0.0
    assert report["metrics"]["untyped_chain_recall"] == 0.0
    assert report["metrics"]["untyped_chain_depth_mae"] == 1.0
    for field in ("root", "entry", "chain"):
        denominator = report["denominators"][field]
        assert denominator["scored"] == 1
        assert denominator["evaluated"] == 0
        assert denominator["misses"] == 1
        assert denominator["coverage"] == 0.0


def test_same_run_units_are_selected_by_target_action_not_claim_order(tmp_path):
    reference_obj = reference(
        unit(
            action="action-1",
            root=known_root("canonical:a"),
            entry=known_entry("canonical:a"),
            chain=known_chain(("canonical:a", "canonical:b")),
        ),
        unit(
            action="action-2",
            root=known_root("canonical:b"),
            entry=known_entry("canonical:c"),
            chain=known_chain(("canonical:c", "canonical:d")),
        ),
    )
    alignment_obj = alignment(
        [
            mapping("canonical:a", "actor-a"),
            mapping("canonical:b", "actor-b"),
            mapping("canonical:a", "intent:a", "node"),
            mapping("canonical:b", "intent:b", "node"),
            mapping("canonical:c", "intent:c", "node"),
            mapping("canonical:d", "intent:d", "node"),
        ]
    )
    # Deliberately put action-2 first; selection must use occurrence action_id.
    prediction_obj = predictions(
        claim(
            "claim-action-2",
            actions=("action-2",),
            root_actors=("actor-b",),
            ranked=("actor-b",),
            entry="intent:c",
            chain=(("intent:c", "intent:d"),),
        ),
        claim(
            "claim-action-1",
            actions=("action-1",),
            root_actors=("actor-a",),
            ranked=("actor-a",),
            entry="intent:a",
            chain=(("intent:a", "intent:b"),),
        ),
    )
    report = evaluate(tmp_path, reference_obj, alignment_obj, prediction_obj)
    assert report["evaluation"]["units_evaluated"] == 2
    assert report["metrics"]["root_agent_at_1"] == 1.0
    assert report["metrics"]["entry_point_accuracy"] == 1.0
    assert report["metrics"]["untyped_chain_precision"] == 1.0
    assert report["metrics"]["untyped_chain_recall"] == 1.0
    assert report["metrics"]["untyped_chain_depth_mae"] == 0.0
    assert [item["selected_claim_id"] for item in report["units"]] == [
        "claim-action-1",
        "claim-action-2",
    ]


def test_wrong_prediction_run_is_rejected(tmp_path):
    prediction_obj = predictions(claim(run="run-2"), run="run-2")
    with pytest.raises(ValueError, match="run"):
        evaluate(tmp_path, reference(unit()), alignment([]), prediction_obj)


def test_ambiguous_claims_count_as_misses_and_lower_coverage(tmp_path):
    reference_obj = reference(
        unit(
            root=known_root("canonical:a"),
            entry=known_entry("canonical:a"),
            chain=known_chain(("canonical:a", "canonical:b")),
        )
    )
    alignment_obj = alignment(
        [
            mapping("canonical:a", "actor-a"),
            mapping("canonical:a", "intent:a", "node"),
            mapping("canonical:b", "intent:b", "node"),
        ]
    )
    prediction_obj = predictions(
        claim("claim-first", actions=("action-1",), root_actors=("actor-a",), entry="intent:a"),
        claim("claim-second", actions=("action-1",), root_actors=("actor-a",), entry="intent:a"),
    )
    report = evaluate(tmp_path, reference_obj, alignment_obj, prediction_obj)
    item = report["units"][0]
    assert item["status"] == "ambiguous_prediction"
    assert item["selected_claim_id"] is None
    assert set(item["ambiguous_claim_ids"]) == {"claim-first", "claim-second"}
    assert report["evaluation"]["units_ambiguous"] == 1
    assert report["metrics"]["root_agent_at_1"] == 0.0
    assert report["metrics"]["root_set_precision"] == 0.0
    assert report["metrics"]["root_set_recall"] == 0.0
    assert report["metrics"]["entry_point_accuracy"] == 0.0
    assert report["metrics"]["untyped_chain_precision"] == 0.0
    assert report["metrics"]["untyped_chain_recall"] == 0.0
    for field in ("root", "entry", "chain"):
        denominator = report["denominators"][field]
        assert denominator["scored"] == 1
        assert denominator["misses"] == 1
        assert denominator["ambiguous"] == 1
        assert denominator["evaluated"] == 0
        assert denominator["coverage"] == 0.0


def test_extra_predicted_root_actor_is_a_false_positive(tmp_path):
    reference_obj = reference(unit(root=known_root("canonical:a")))
    alignment_obj = alignment([mapping("canonical:a", "actor-a")])
    prediction_obj = predictions(
        claim(root_actors=("actor-a", "actor-b"), ranked=("actor-a",))
    )
    report = evaluate(tmp_path, reference_obj, alignment_obj, prediction_obj)
    assert report["metrics"]["root_agent_at_1"] == 1.0
    assert report["metrics"]["root_set_precision"] == 0.5
    assert report["metrics"]["root_set_recall"] == 1.0
    denominator = report["denominators"]["root"]
    assert denominator["scored"] == 1
    assert denominator["evaluated"] == 1
    assert denominator["coverage"] == 1.0


@pytest.mark.parametrize("ranked", [None, ["actor-a", "actor-b"]])
def test_root_groups_never_fabricate_a_single_rank(tmp_path, ranked):
    reference_obj = reference(unit(root=known_root("canonical:a", "canonical:b")))
    alignment_obj = alignment(
        [
            mapping("canonical:a", "actor-a"),
            mapping("canonical:b", "actor-b"),
        ]
    )
    prediction_obj = predictions(claim(root_actors=("actor-a", "actor-b"), ranked=ranked))
    report = evaluate(tmp_path, reference_obj, alignment_obj, prediction_obj)
    assert report["metrics"]["root_agent_at_1"] == 0.0
    assert report["metrics"]["root_set_precision"] == 1.0
    assert report["metrics"]["root_set_recall"] == 1.0
    assert report["metrics"]["root_set_f1"] == 1.0
    assert report["metrics"]["root_agent_at_3"] is None
    assert report["metrics"]["mrr"] is None
    assert report["metrics"]["ranking_reported"] is False


def test_unmapped_reference_actor_stays_unaligned_and_reduces_coverage(tmp_path):
    reference_obj = reference(
        unit(root=known_root("canonical:a"), entry=known_entry("canonical:a"))
    )
    alignment_obj = alignment([mapping("canonical:a", "intent:a", "node")])
    prediction_obj = predictions(claim(root_actors=("actor-a",), entry="intent:a"))
    report = evaluate(tmp_path, reference_obj, alignment_obj, prediction_obj)
    assert report["metrics"]["root_agent_at_1"] is None
    assert report["metrics"]["entry_point_accuracy"] == 1.0
    denominator = report["denominators"]["root"]
    assert denominator == {
        "known": 1,
        "aligned": 0,
        "scored": 0,
        "evaluated": 0,
        "misses": 0,
        "unaligned": 1,
        "ambiguous": 0,
        "unknown": 0,
        "coverage": 0.0,
    }
    assert report["units"][0]["fields"]["root"]["status"] == "unaligned"


def test_reference_mapping_does_not_require_a_prediction_occurrence(tmp_path):
    reference_obj = reference(unit(entry=known_entry("canonical:entry")))
    alignment_obj = alignment([mapping("canonical:entry", "intent:ghost", "node")])
    report = evaluate(
        tmp_path, reference_obj, alignment_obj, predictions(claim(entry=None))
    )
    assert report["metrics"]["entry_point_accuracy"] == 0.0
    denominator = report["denominators"]["entry"]
    assert denominator["scored"] == 1
    assert denominator["evaluated"] == 1
    assert denominator["misses"] == 0
    assert denominator["coverage"] == 1.0
    assert report["units"][0]["fields"]["entry"]["status"] == "evaluated"


def test_known_empty_chain_is_zero_depth_not_unknown(tmp_path):
    reference_obj = reference(unit(chain=known_chain()))
    report = evaluate(
        tmp_path, reference_obj, alignment([]), predictions(claim(chain=()))
    )
    assert report["metrics"]["untyped_chain_precision"] == 1.0
    assert report["metrics"]["untyped_chain_recall"] == 1.0
    assert report["metrics"]["untyped_chain_f1"] == 1.0
    assert report["metrics"]["untyped_chain_depth_mae"] == 0.0
    assert report["denominators"]["chain"]["known"] == 1
    assert report["denominators"]["chain"]["evaluated"] == 1
    assert "known-empty" in report["conventions"]["chain_known_empty"]


def test_chain_metrics_use_directed_typed_edges(tmp_path):
    reference_obj = reference(
        unit(chain=known_chain(("canonical:a", "canonical:b")))
    )
    alignment_obj = alignment(
        [
            mapping("canonical:a", "intent:a", "node"),
            mapping("canonical:b", "intent:b", "node"),
        ]
    )
    prediction_obj = predictions(
        claim(chain=(("intent:a", "intent:b"), ("intent:b", "intent:c")))
    )
    report = evaluate(tmp_path, reference_obj, alignment_obj, prediction_obj)
    assert report["metrics"]["untyped_chain_precision"] == 0.5
    assert report["metrics"]["untyped_chain_recall"] == 1.0
    assert report["metrics"]["untyped_chain_f1"] == pytest.approx(2 / 3)
    assert report["metrics"]["untyped_chain_depth_mae"] == 1.0
    assert "untyped_chain_precision" in report["metrics"]
    assert "chain_precision" not in report["metrics"]
    assert "untyped" in report["conventions"]["typed_edges"]


def test_disconnected_prediction_graph_is_a_false_positive_not_unaligned(tmp_path):
    reference_obj = reference(
        unit(chain=known_chain(("canonical:a", "canonical:b")))
    )
    alignment_obj = alignment(
        [
            mapping("canonical:a", "intent:a", "node"),
            mapping("canonical:b", "intent:b", "node"),
        ]
    )
    prediction_obj = predictions(
        claim(chain=(("intent:x", "intent:y"),))
    )
    report = evaluate(tmp_path, reference_obj, alignment_obj, prediction_obj)
    assert report["metrics"]["untyped_chain_precision"] == 0.0
    assert report["metrics"]["untyped_chain_recall"] == 0.0
    denominator = report["denominators"]["chain"]
    assert denominator["scored"] == 1
    assert denominator["evaluated"] == 1
    assert denominator["coverage"] == 1.0


def test_truncated_prediction_still_counts_in_the_eligible_denominator(tmp_path):
    reference_obj = reference(
        unit(
            root=known_root("canonical:a"),
            entry=known_entry("canonical:a"),
            chain=known_chain(("canonical:a", "canonical:b")),
        )
    )
    alignment_obj = alignment(
        [
            mapping("canonical:a", "actor-a"),
            mapping("canonical:a", "intent:a", "node"),
            mapping("canonical:b", "intent:b", "node"),
        ]
    )
    truncated = claim(root_actors=(), ranked=None, entry=None, chain=())
    truncated["truncated"] = True
    report = evaluate(tmp_path, reference_obj, alignment_obj, predictions(truncated))
    assert report["metrics"]["root_agent_at_1"] == 0.0
    assert report["metrics"]["entry_point_accuracy"] == 0.0
    assert report["metrics"]["untyped_chain_recall"] == 0.0
    for field in ("root", "entry", "chain"):
        denominator = report["denominators"][field]
        assert denominator["scored"] == 1
        assert denominator["evaluated"] == 1
        assert denominator["coverage"] == 1.0


def test_missing_raw_capture_source_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="raw_capture"):
        evaluate(
            tmp_path,
            reference(unit()),
            alignment([]),
            predictions(claim()),
            write_raw=False,
        )


def test_raw_capture_hash_drift_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="hash"):
        evaluate(
            tmp_path,
            reference(unit()),
            alignment([]),
            predictions(claim()),
            raw_bytes=b"drifted capture\n",
        )


def test_cycles_untyped_edges_and_duplicate_ranks_are_rejected(tmp_path):
    cyclic_reference = reference(unit(chain=known_chain(("a", "b"), ("b", "a"))))
    with pytest.raises(ValueError, match="cycle"):
        evaluate(tmp_path, cyclic_reference, alignment([]), predictions(claim()))

    cyclic_prediction = claim(
        chain=(("intent:a", "intent:b"), ("intent:b", "intent:a"))
    )
    with pytest.raises(ValueError, match="cycle"):
        evaluate(tmp_path, reference(unit()), alignment([]), predictions(cyclic_prediction))

    untyped = claim(chain=(("intent:a", "intent:b"),))
    untyped["edges"][0]["edge_type"] = ""
    with pytest.raises(ValueError, match="edge_type"):
        evaluate(tmp_path, reference(unit()), alignment([]), predictions(untyped))

    duplicate = claim(ranked=("actor-a", "actor-a"), root_actors=("actor-a",))
    with pytest.raises(ValueError, match="duplicate ranked"):
        evaluate(tmp_path, reference(unit()), alignment([]), predictions(duplicate))


def test_alignment_rejects_claim_id_or_label_auto_matching(tmp_path):
    mapped = mapping("canonical:a", "actor-a")
    mapped["claim_id"] = "claim-1"
    with pytest.raises(ValueError, match="claim"):
        evaluate(
            tmp_path,
            reference(unit(root=known_root("canonical:a"))),
            alignment([mapped]),
            predictions(claim(root_actors=("actor-a",))),
        )
    labelled = mapping("canonical:a", "actor-a")
    labelled["label"] = "root"
    with pytest.raises(ValueError, match="label"):
        evaluate(
            tmp_path,
            reference(unit(root=known_root("canonical:a"))),
            alignment([labelled]),
            predictions(claim(root_actors=("actor-a",))),
        )


@pytest.mark.parametrize(
    ("prov", "match"),
    [
        (provenance(human_review=True), "human_review"),
        (provenance(detector_blind=False), "detector_blind"),
        (provenance(method="human_review"), "method"),
        (provenance(reviewer_ids=["r1", "r1", "r2"]), "reviewer"),
        (provenance(reviewer_ids=["r1", "r2"]), "reviewer"),
        (provenance(adjudicator_id="reviewer-1"), "adjudicator"),
    ],
)
def test_reference_provenance_is_frozen_ai_consensus_3(tmp_path, prov, match):
    with pytest.raises(ValueError, match=match):
        evaluate(tmp_path, reference(unit(), prov=prov), alignment([]), predictions(claim()))


def test_legacy_null_root_actor_cannot_become_known(tmp_path):
    legacy = unit(root={"status": "unknown", "root_actor": None})
    legacy["ai_runtime"] = {"root_actor": None}
    report = evaluate(tmp_path, reference(legacy), alignment([]), predictions(claim()))
    assert report["metrics"]["root_agent_at_1"] is None
    assert report["denominators"]["root"]["known"] == 0
    assert report["units"][0]["fields"]["root"]["status"] == "unknown_reference"


def test_duplicate_units_and_blank_ids_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="Duplicate unit"):
        evaluate(tmp_path, reference(unit(), unit()), alignment([]), predictions(claim()))
    with pytest.raises(ValueError, match="run_id"):
        evaluate(
            tmp_path,
            reference(unit(run=" ")),
            alignment([]),
            predictions(claim()),
        )


def test_cli_is_read_only_and_reports_source_hashes(tmp_path, capsys):
    reference_obj = reference(
        unit(
            root=known_root("canonical:a"),
            entry=known_entry("canonical:a"),
            chain=known_chain(),
        )
    )
    alignment_obj = alignment([mapping("canonical:a", "actor-a")])
    prediction_obj = predictions(claim(root_actors=("actor-a",), entry="intent:a"))
    paths = write_inputs(tmp_path, reference_obj, alignment_obj, prediction_obj)
    before = {path: path.read_bytes() for path in paths}

    code = ar.main(
        [
            "--reference",
            str(paths[0]),
            "--alignment",
            str(paths[1]),
            "--predictions",
            str(paths[2]),
        ]
    )
    output = capsys.readouterr().out
    report = json.loads(output)
    assert code == 0
    assert report["inputs"]["reference"]["sha256"] == hashlib.sha256(before[paths[0]]).hexdigest()
    assert report["inputs"]["alignment"]["sha256"] == hashlib.sha256(before[paths[1]]).hexdigest()
    assert report["inputs"]["predictions"]["sha256"] == hashlib.sha256(before[paths[2]]).hexdigest()
    assert report["inputs"]["alignment"]["raw_capture"]["source"] == "capture/raw.jsonl"
    assert report["inputs"]["alignment"]["raw_capture"]["sha256"] == RAW_SHA
    assert report["inputs"]["alignment"]["raw_capture"]["verified"] is True
    assert report["disclosure"]["human_gold"] is False
    assert report["disclosure"]["accuracy_claimed"] is False
    assert "not semantically" in report["conventions"]["evidence_refs"]
    assert report["metrics"]["root_agent_at_3"] is None
    assert report["metrics"]["mrr"] is None
    assert before == {path: path.read_bytes() for path in paths}


DB_SHA = "c" * 64
FINGERPRINT = "fp-current"


def experiment(*, results, current=FINGERPRINT, db_sha=DB_SHA, runs=None):
    if runs is None:
        runs = [{
            "run_id": result["run_id"],
            "source_fingerprint": current,
            "current_fingerprint": current,
            "attributions": result,
        } for result in results]
    return {
        "schema": ar.EXPERIMENT_SCHEMA,
        "current_fingerprint": current,
        "source_database_sha256": db_sha,
        "runs": runs,
    }


def bound_reference(*units):
    return reference(*units, prov=provenance(source_database_sha256=DB_SHA))


def test_experiment_wrapper_projects_existing_attribution_results(tmp_path):
    reference_obj = bound_reference(unit(root=known_root("canonical:a")))
    alignment_obj = alignment([mapping("canonical:a", "actor-a")])
    result = predictions(claim(root_actors=("actor-a",), ranked=("actor-a",)))
    report = evaluate(tmp_path, reference_obj, alignment_obj, experiment(results=[result]))
    assert report["metrics"]["root_agent_at_1"] == 1.0
    assert report["metrics"]["root_set_precision"] == 1.0
    assert report["metrics"]["root_agent_at_3"] is None
    assert report["metrics"]["mrr"] is None


def test_experiment_wrapper_preserves_a_wrong_prediction(tmp_path):
    reference_obj = bound_reference(unit(root=known_root("canonical:a")))
    alignment_obj = alignment([mapping("canonical:a", "actor-a")])
    result = predictions(claim(root_actors=("actor-b",), ranked=("actor-b",)))
    report = evaluate(tmp_path, reference_obj, alignment_obj, experiment(results=[result]))
    assert report["metrics"]["root_agent_at_1"] == 0.0
    assert report["metrics"]["root_set_precision"] == 0.0
    assert report["metrics"]["root_set_recall"] == 0.0


@pytest.mark.parametrize("mutate, match", [
    (lambda w: w["runs"][0].update(source_fingerprint="stale"), "fingerprint"),
    (lambda w: w["runs"][0].update(current_fingerprint="other"), "fingerprint"),
    (lambda w: w.pop("source_database_sha256"), "source_database_sha256"),
    (lambda w: w.update(source_database_sha256="d" * 64), "source_database_sha256"),
    (lambda w: w["runs"].append(dict(w["runs"][0])), "run_id"),
    (lambda w: w["runs"][0].update(run_id="other"), "run_id"),
    (lambda w: w["runs"][0]["attributions"].update(schema="other"), "schema"),
])
def test_experiment_wrapper_rejects_unbound_or_malformed_projection(
        tmp_path, mutate, match):
    reference_obj = bound_reference(unit(root=known_root("canonical:a")))
    alignment_obj = alignment([mapping("canonical:a", "actor-a")])
    wrapper = experiment(results=[
        predictions(claim(root_actors=("actor-a",), ranked=("actor-a",))),
    ])
    mutate(wrapper)
    with pytest.raises(ValueError, match=match):
        evaluate(tmp_path, reference_obj, alignment_obj, wrapper)


def test_experiment_wrapper_requires_reference_database_provenance(tmp_path):
    reference_obj = reference(unit(root=known_root("canonical:a")))
    alignment_obj = alignment([mapping("canonical:a", "actor-a")])
    wrapper = experiment(results=[
        predictions(claim(root_actors=("actor-a",), ranked=("actor-a",))),
    ])
    with pytest.raises(ValueError, match="source_database_sha256"):
        evaluate(tmp_path, reference_obj, alignment_obj, wrapper)


def test_experiment_wrapper_requires_a_nonempty_run_list(tmp_path):
    reference_obj = bound_reference(unit(root=known_root("canonical:a")))
    alignment_obj = alignment([mapping("canonical:a", "actor-a")])
    wrapper = experiment(results=[], runs=[])
    with pytest.raises(ValueError, match="run"):
        evaluate(tmp_path, reference_obj, alignment_obj, wrapper)


# --------------------------------------------------------------------------- #
# Runtime reference v2 compatibility: per-run localisation, targetless runs.
# --------------------------------------------------------------------------- #

def reference_without_target(*runs_without):
    value = reference()
    value["runs_without_target"] = list(runs_without)
    return value


def test_known_root_step_ref_is_accepted_and_preserved():
    root = known_root("canonical:a")
    root["step_ref"] = "canonical:step"
    parsed = ar.validate_reference(reference(unit(root=root)))
    assert parsed["units"][0]["root"]["step_ref"] == "canonical:step"


def test_reference_without_a_target_run_is_accepted_and_reported(tmp_path):
    missing = {
        "run_id": "run-1",
        "attack_executed": "no",
        "target_action": None,
        "reason": "attack_executed_no",
    }
    reference_obj = reference_without_target(missing)
    predictions_obj = {"schema": ar.PREDICTION_SCHEMA, "results": []}
    report = evaluate(tmp_path, reference_obj, alignment([]), predictions_obj)
    assert report["metrics"]["root_agent_at_1"] is None
    assert report["evaluation"]["units_total"] == 0
    assert report["reference"]["runs_without_target"] == [missing]
    assert report["reference"]["coverage"] is None
    parsed = ar.validate_reference(reference_obj)
    assert parsed["units"] == []


@pytest.mark.parametrize("runs_without, match", [
    ([], "nonempty"),
    ([{"run_id": "run-1", "attack_executed": "yes",
       "target_action": "action-1"}], "target_action"),
    ([{"run_id": "run-1", "attack_executed": "maybe",
       "target_action": None}], "attack_executed"),
    ([{"run_id": "", "attack_executed": "no", "target_action": None}], "run_id"),
])
def test_targetless_coverage_is_validated(runs_without, match):
    value = reference()
    value["runs_without_target"] = runs_without
    with pytest.raises(ValueError, match=match):
        ar.validate_reference(value)


def test_targetless_reference_rejects_prediction_runs(tmp_path):
    value = reference_without_target({
        "run_id": "run-1", "attack_executed": "no",
        "target_action": None, "reason": "attack_executed_no"})
    with pytest.raises(ValueError, match="run"):
        evaluate(tmp_path, value, alignment([]), predictions(claim()))