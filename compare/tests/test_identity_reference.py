# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Independent identity reference -> pairwise/B3/candidate metrics."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from compare.tools import identity_reference as ref

RUN = "run-identity-1"
DIGEST = "a" * 64


def reference(*, events=None, run=RUN, digest=DIGEST, source_db=None):
    value = {
        "schema": ref.REFERENCE_SCHEMA,
        "version": 1,
        "frozen": True,
        "run_id": run,
        "evidence_bundle_sha256": digest,
        "provenance": {
            "method": "ai_consensus_3",
            "human_review": False,
            "detector_blind": True,
            "reviewer_ids": ["r1", "r2", "r3"],
            "adjudicator_id": "adj",
        },
        "events": events or {
            "e1": {"status": "known", "actor": "a1"},
            "e2": {"status": "known", "actor": "a1"},
            "e3": {"status": "known", "actor": "a2"},
        },
    }
    if source_db is not None:
        value["source_database_sha256"] = source_db
    return value


def prediction(*, events=None, run=RUN, digest=DIGEST):
    return {
        "schema": ref.PREDICTION_SCHEMA,
        "version": 1,
        "run_id": run,
        "evidence_bundle_sha256": digest,
        "events": events or {"e1": "p1", "e2": "p1", "e3": "p2"},
    }


def alignment(*, mappings=None, raw_capture=None):
    return {
        "schema": ref.ALIGNMENT_SCHEMA,
        "version": 1,
        "raw_capture": raw_capture or {"source": "capture.json", "sha256": "b" * 64},
        "mappings": mappings or [
            {"canonical_id": "a1", "captured_id": "p1",
             "evidence_refs": ["session:1"]},
            {"canonical_id": "a2", "captured_id": "p2",
             "evidence_refs": ["session:2"]},
        ],
    }


def test_complete_partition_is_scored_with_pairwise_and_b3():
    result = ref.evaluate_reference(reference(), prediction(), alignment())
    assert result["runs"] == 1
    metrics = result["metrics"]
    assert metrics["events"] == 3
    assert metrics["coverage"] == 1.0
    assert metrics["abstention"] == 0.0
    assert metrics["pairwise_precision"] == 1.0
    assert metrics["pairwise_recall"] == 1.0
    assert metrics["pairwise_f1"] == 1.0
    assert metrics["b3"] == 1.0
    assert metrics["false_merge"] == 0
    assert metrics["false_split"] == 0
    assert metrics["unattributed"] == 0


def test_unknown_truth_and_abstention_keep_the_denominator():
    truth = reference(events={
        "e1": {"status": "known", "actor": "a1"},
        "e2": {"status": "unknown"},
        "e3": {"status": "known", "actor": "a2"},
    })
    predicted = prediction(events={"e1": "p1", "e2": "p1", "e3": None})
    result = ref.evaluate_reference(truth, predicted, alignment())
    metrics = result["metrics"]
    assert metrics["events"] == 3
    assert metrics["known_truth_events"] == 2
    assert metrics["unknown_truth_events"] == 1
    assert metrics["coverage"] == 0.5
    assert metrics["abstention"] == 0.5
    # A complete partition metric is not estimable when truth/answers abstain.
    assert metrics["pairwise_f1"] is None
    assert metrics["b3"] is None
    assert metrics["unattributed"] == 1


def test_candidate_sets_are_scored_with_an_external_actor_mapping():
    truth = reference(events={
        "e1": {"status": "known", "actor": "a1"},
        "e2": {"status": "known", "actor": "a1"},
    })
    predicted = prediction(events={"e1": ["p1", "p2"], "e2": ["p1"]})
    result = ref.evaluate_reference(truth, predicted, alignment())
    metrics = result["metrics"]
    assert metrics["coverage"] == 1.0
    assert metrics["answered_candidate_hit_rate"] == 1.0
    assert metrics["mean_candidate_count"] == 1.5
    assert metrics["pairwise_f1"] is None


def test_mapped_actor_absent_from_reference_is_scored_wrong_not_rejected():
    truth = reference(events={
        "e1": {"status": "known", "actor": "a1"},
        "e2": {"status": "known", "actor": "a1"},
    })
    predicted = prediction(events={"e1": "p1", "e2": "p3"})
    align = alignment(mappings=[
        {"canonical_id": "a1", "captured_id": "p1", "evidence_refs": ["session:1"]},
        {"canonical_id": "a9", "captured_id": "p3", "evidence_refs": ["session:9"]},
    ])
    metrics = ref.evaluate_reference(truth, predicted, align)["metrics"]
    assert metrics["partition_estimable"] is True
    assert metrics["pairwise_f1"] is not None
    assert metrics["pairwise_f1"] < 1.0


def test_many_to_one_alignment_cannot_erase_a_predicted_split():
    truth = reference(events={
        "e1": {"status": "known", "actor": "a1"},
        "e2": {"status": "known", "actor": "a1"},
    })
    predicted = prediction(events={"e1": "p1", "e2": "p2"})
    align = alignment(mappings=[
        {"canonical_id": "a1", "captured_id": "p1", "evidence_refs": ["session:1"]},
        {"canonical_id": "a1", "captured_id": "p2", "evidence_refs": ["session:2"]},
    ])
    metrics = ref.evaluate_reference(truth, predicted, align)["metrics"]
    assert metrics["partition_estimable"] is True
    assert metrics["false_split"] == 1
    assert metrics["pairwise_f1"] < 1.0
    assert metrics["b3"] < 1.0


def test_alignment_cannot_change_partition_metrics():
    one_to_one = ref.evaluate_reference(reference(), prediction(), alignment())
    many_to_one = ref.evaluate_reference(
        reference(),
        prediction(),
        alignment(mappings=[
            {"canonical_id": "a1", "captured_id": "p1",
             "evidence_refs": ["session:1"]},
            {"canonical_id": "a1", "captured_id": "p2",
             "evidence_refs": ["session:2"]},
        ]),
    )
    keys = (
        "pairwise_precision",
        "pairwise_recall",
        "pairwise_f1",
        "b3",
        "b3_precision",
        "b3_recall",
        "false_merge",
        "false_split",
    )
    assert {key: one_to_one["metrics"][key] for key in keys} == {
        key: many_to_one["metrics"][key] for key in keys
    }


def test_unmapped_prediction_actor_is_rejected_not_guessed():
    predicted = prediction(events={"e1": "unknown-actor", "e2": "p1", "e3": "p2"})
    with pytest.raises(ValueError, match="actor_mapping"):
        ref.evaluate_reference(reference(), predicted, alignment())


def test_run_or_event_set_mismatch_is_rejected():
    with pytest.raises(ValueError, match="run_id"):
        ref.evaluate_reference(reference(), prediction(run="other"), alignment())
    with pytest.raises(ValueError, match="event sets"):
        ref.evaluate_reference(reference(), prediction(events={"e1": "p1"}), alignment())


def test_pure_evaluate_reference_never_claims_a_verified_raw_capture():
    result = ref.evaluate_reference(reference(), prediction(), alignment())
    assert result["inputs"]["alignment"]["raw_capture"]["verified"] is False


def _write_inputs(tmp_path, *, source="capture.json", digest):
    payloads = {
        "reference": reference(),
        "prediction": prediction(),
        "alignment": alignment(
            raw_capture={"source": source, "sha256": digest}),
    }
    paths = {}
    for name, payload in payloads.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload))
        paths[name] = path
    return paths


def test_evaluate_files_verifies_the_raw_capture_bytes(tmp_path):
    capture_bytes = b'{"synthetic": "capture"}\n'
    (tmp_path / "capture.json").write_bytes(capture_bytes)
    digest = hashlib.sha256(capture_bytes).hexdigest()
    paths = _write_inputs(tmp_path, digest=digest)

    report = ref.evaluate_files(
        paths["reference"], paths["prediction"], paths["alignment"])
    captured = report["inputs"]["alignment"]["raw_capture"]
    assert captured["verified"] is True
    assert captured["actual_sha256"] == digest


def test_evaluate_files_rejects_missing_raw_capture(tmp_path):
    paths = _write_inputs(tmp_path, digest=hashlib.sha256(b"").hexdigest())
    with pytest.raises(ValueError, match="raw_capture"):
        ref.evaluate_files(
            paths["reference"], paths["prediction"], paths["alignment"])


def test_evaluate_files_rejects_raw_capture_hash_drift(tmp_path):
    (tmp_path / "capture.json").write_bytes(b"actual bytes")
    declared = hashlib.sha256(b"declared bytes").hexdigest()
    paths = _write_inputs(tmp_path, digest=declared)
    with pytest.raises(ValueError, match="hash drift"):
        ref.evaluate_files(
            paths["reference"], paths["prediction"], paths["alignment"])


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_evaluate_files_rejects_nonregular_raw_capture(tmp_path, kind):
    capture = tmp_path / "capture.json"
    if kind == "symlink":
        target = tmp_path / "target.json"
        target.write_bytes(b"raw bytes")
        capture.symlink_to(target)
    else:
        capture.mkdir()
    paths = _write_inputs(
        tmp_path, digest=hashlib.sha256(b"raw bytes").hexdigest())
    with pytest.raises(ValueError, match="regular file"):
        ref.evaluate_files(
            paths["reference"], paths["prediction"], paths["alignment"])


def test_cli_verifies_a_relative_raw_capture_and_writes_a_report(tmp_path, capsys):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    capture_bytes = b'{"synthetic": "cli capture"}\n'
    capture = tmp_path / "capture.json"
    capture.write_bytes(capture_bytes)
    digest = hashlib.sha256(capture_bytes).hexdigest()
    payloads = {
        "reference": reference(),
        "prediction": prediction(),
        "alignment": alignment(
            raw_capture={"source": "../capture.json", "sha256": digest}),
    }
    paths = {}
    for name, payload in payloads.items():
        path = inputs / f"{name}.json"
        path.write_text(json.dumps(payload))
        paths[name] = path
    out = tmp_path / "report.json"
    assert ref.main(["--reference", str(paths["reference"]),
                     "--prediction", str(paths["prediction"]),
                     "--alignment", str(paths["alignment"]),
                     "--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["schema"] == ref.REPORT_SCHEMA
    assert payload["metrics"]["pairwise_f1"] == 1.0
    captured = payload["inputs"]["alignment"]["raw_capture"]
    assert captured["verified"] is True
    assert captured["actual_sha256"] == digest
    assert Path(captured["resolved_source"]).resolve() == capture.resolve()
    assert json.loads(capsys.readouterr().out)["runs"] == 1


DB_SHA = "c" * 64
CONDITION = "full"


def assigned(partition):
    return {"state": "assigned", "partition": partition}


def unknown_unit():
    return {"state": "unknown", "reason": "no_positive_signal"}


def experiment(
    *,
    units,
    run=RUN,
    condition=CONDITION,
    current="fp-current",
    source="fp-current",
    db_sha=DB_SHA,
    views=None,
):
    return {
        "schema": ref.EXPERIMENT_SCHEMA,
        "current_fingerprint": current,
        "source_database_sha256": db_sha,
        "runs": [{
            "run_id": run,
            "source_fingerprint": source,
            "current_fingerprint": current,
            "views": views if views is not None else [{
                "condition": condition,
                "units": units,
            }],
        }],
    }


def write_experiment_inputs(tmp_path, *, reference_obj, experiment_obj, alignment_obj):
    capture_bytes = b'{"synthetic": "capture"}\n'
    (tmp_path / "capture.json").write_bytes(capture_bytes)
    digest = hashlib.sha256(capture_bytes).hexdigest()
    payloads = {
        "reference": reference_obj,
        "prediction": experiment_obj,
        "alignment": alignment_obj(
            raw_capture={"source": "capture.json", "sha256": digest}),
    }
    paths = {}
    for name, payload in payloads.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload))
        paths[name] = path
    return paths["reference"], paths["prediction"], paths["alignment"]


def test_experiment_wrapper_projects_assigned_and_unknown_units(tmp_path):
    truth = reference(source_db=DB_SHA, events={
        "action:e1": {"status": "known", "actor": "a1"},
        "action:e2": {"status": "unknown"},
    })
    wrapper = experiment(units=[
        {"unit_id": "action:e1", "actor": assigned("p1")},
        {"unit_id": "action:e2", "actor": unknown_unit()},
    ])
    report = ref.evaluate_files(*write_experiment_inputs(
        tmp_path, reference_obj=truth, experiment_obj=wrapper,
        alignment_obj=alignment))
    metrics = report["metrics"]
    assert metrics["known_truth_events"] == 1
    assert metrics["unknown_truth_events"] == 1
    assert metrics["coverage"] == 1.0
    assert metrics["known_truth_hit_rate"] == 1.0


def test_experiment_wrapper_preserves_a_wrong_assignment(tmp_path):
    truth = reference(source_db=DB_SHA, events={
        "action:e1": {"status": "known", "actor": "a1"},
        "action:e2": {"status": "known", "actor": "a2"},
    })
    wrapper = experiment(units=[
        {"unit_id": "action:e1", "actor": assigned("p9")},
        {"unit_id": "action:e2", "actor": assigned("p9")},
    ])
    report = ref.evaluate_files(*write_experiment_inputs(
        tmp_path, reference_obj=truth, experiment_obj=wrapper,
        alignment_obj=lambda **kwargs: alignment(mappings=[
            {"canonical_id": "a9", "captured_id": "p9",
             "evidence_refs": ["session:9"]},
        ], **kwargs)))
    metrics = report["metrics"]
    assert metrics["known_truth_hit_rate"] == 0.0
    assert metrics["pairwise_f1"] == 0.0


@pytest.mark.parametrize("mutate, match", [
    (lambda w: w["runs"][0]["views"].append(w["runs"][0]["views"][0]), "condition"),
    (lambda w: w["runs"][0]["views"][0].update(condition="other"), "condition"),
    (lambda w: w["runs"][0]["views"][0]["units"].append(
        dict(w["runs"][0]["views"][0]["units"][0])), "Duplicate unit_id"),
    (lambda w: w["runs"][0]["views"][0]["units"][0].update(
        actor={"state": "maybe"}), "actor.state"),
    (lambda w: w["runs"].append(dict(w["runs"][0])), "run_id"),
    (lambda w: w["runs"][0].update(run_id="other"), "run_id"),
    (lambda w: w["runs"][0].update(source_fingerprint="stale"), "fingerprint"),
    (lambda w: w["runs"][0].update(current_fingerprint="other"), "fingerprint"),
    (lambda w: w.pop("source_database_sha256"), "source_database_sha256"),
    (lambda w: w.update(source_database_sha256="d" * 64), "source_database_sha256"),
])
def test_experiment_wrapper_rejects_unbound_or_malformed_projection(
        tmp_path, mutate, match):
    truth = reference(source_db=DB_SHA, events={
        "action:e1": {"status": "known", "actor": "a1"},
    })
    wrapper = experiment(units=[
        {"unit_id": "action:e1", "actor": assigned("p1")},
    ])
    mutate(wrapper)
    with pytest.raises(ValueError, match=match):
        ref.evaluate_files(*write_experiment_inputs(
            tmp_path, reference_obj=truth, experiment_obj=wrapper,
            alignment_obj=alignment))


def test_experiment_wrapper_requires_an_independent_reference_database_bind(tmp_path):
    truth = reference(events={
        "action:e1": {"status": "known", "actor": "a1"},
    })
    wrapper = experiment(units=[
        {"unit_id": "action:e1", "actor": assigned("p1")},
    ])
    with pytest.raises(ValueError, match="source_database_sha256"):
        ref.evaluate_files(*write_experiment_inputs(
            tmp_path, reference_obj=truth, experiment_obj=wrapper,
            alignment_obj=alignment))


def test_experiment_condition_is_explicit_and_selects_one_view(tmp_path):
    truth = reference(source_db=DB_SHA, events={
        "action:e1": {"status": "known", "actor": "a1"},
    })
    wrapper = experiment(units=[], views=[
        {"condition": "full", "units": [
            {"unit_id": "action:e1", "actor": assigned("p1")},
        ]},
        {"condition": "pid-instance-only", "units": [
            {"unit_id": "action:e1", "actor": unknown_unit()},
        ]},
    ])
    paths = write_experiment_inputs(
        tmp_path, reference_obj=truth, experiment_obj=wrapper,
        alignment_obj=alignment)
    full = ref.evaluate_files(*paths)
    control = ref.evaluate_files(*paths, condition="pid-instance-only")
    assert full["metrics"]["coverage"] == 1.0
    assert control["metrics"]["coverage"] == 0.0
    with pytest.raises(ValueError, match="condition"):
        ref.evaluate_files(*paths, condition="unsupported")
