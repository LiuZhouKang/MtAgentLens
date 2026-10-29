# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Agreement is computed from independent run annotations, never from adjudication."""

import json

import pytest

from scripts.evaluation.annotation_agreement import agreement_from_export

RATERS = ("刘周康", "唐浩钺", "刘锦样")


def annotation(
    attempted="yes", executed="no", succeeded="unknown", actor=None, roots=None
):
    return {
        "attempted": {"value": attempted, "evidence_refs": ["event-1"]},
        "executed": {"value": executed, "evidence_refs": ["event-1"]},
        "succeeded": {"value": succeeded, "evidence_refs": []},
        "identities": {"event-1": actor},
        "roots": {"actors": roots, "evidence_refs": []},
        "notes": "Synthetic agreement fixture; not a human label.",
    }


def revision(annotator, value, *, method="human", review=None):
    record = {"annotator": annotator, "method": method, "annotation": value,
              "revision_id": annotator + "-r"}
    if review is not None:
        record["review"] = review
    return record


def export(tasks):
    return {"schema": "mtagentlens.annotation-export/v2",
            "paper_metrics_available": False,
            "tasks": [{"task_id": task_id,
                       "binding": {"scenario_id": "s",
                                   "codebook_version": "pilot/1"},
                       "status": "annotated", "revisions": revisions}
                      for task_id, revisions in tasks]}


def unanimous_tasks():
    return [
        ("task-1", [revision(r, annotation(actor="actor-a")) for r in RATERS]),
        ("task-2", [revision(r, annotation(attempted="no", executed="yes",
                                            actor="actor-b")) for r in RATERS]),
    ]


def test_unanimous_independent_annotations_have_perfect_agreement():
    report = agreement_from_export(export(unanimous_tasks()), RATERS)
    assert report["schema"] == "mtagentlens.annotation-agreement/v1"
    assert report["paper_metrics_available"] is False
    for field in ("attempted", "executed"):
        assert report["fields"][field]["items"] == 2
        assert report["fields"][field]["fleiss_kappa"] == 1.0
        assert report["fields"][field]["percent_agreement"] == 1.0
    assert report["fields"]["identities"]["fleiss_kappa"] == 1.0
    assert report["fields"]["succeeded"]["fleiss_kappa"] is None  # no variation
    assert report["coverage"]["tasks_with_all_raters"] == 2
    assert report["exact_agreement"]["percent"] == 1.0
    assert report["codebook_version"] == "pilot/1"
    assert report["gate"]["status"] == "pass"


def test_mixed_codebook_versions_cannot_be_averaged():
    document = export(unanimous_tasks())
    document["tasks"][1]["binding"]["codebook_version"] = "pilot/2"
    with pytest.raises(ValueError, match="codebook"):
        agreement_from_export(document, RATERS)


def test_systematic_disagreement_below_threshold_requires_codebook_revision():
    tasks = []
    for index in range(6):
        tasks.append(("task-%d" % index, [
            revision(RATERS[0], annotation(attempted="yes")),
            revision(RATERS[1], annotation(attempted="no")),
            revision(RATERS[2], annotation(attempted="unknown")),
        ]))
    report = agreement_from_export(export(tasks), RATERS)
    attempted = report["fields"]["attempted"]
    assert attempted["items"] == 6
    assert attempted["percent_agreement"] == 0.0
    assert attempted["fleiss_kappa"] < 0.6
    assert report["gate"]["status"] == "revise_codebook"
    assert "attempted" in report["gate"]["fields_below_threshold"]


def test_missing_rater_is_coverage_not_silent_agreement():
    tasks = [("task-1", [revision(RATERS[0], annotation()),
                         revision(RATERS[1], annotation())])]
    report = agreement_from_export(export(tasks), RATERS)
    assert report["coverage"]["tasks_with_all_raters"] == 0
    assert report["coverage"]["per_rater"] == {RATERS[0]: 1, RATERS[1]: 1, RATERS[2]: 0}
    assert report["fields"]["attempted"]["items"] == 0
    assert report["fields"]["attempted"]["fleiss_kappa"] is None
    pair = report["fields"]["attempted"]["pairwise"]["%s/%s" % (RATERS[0], RATERS[1])]
    assert pair["items"] == 1 and pair["percent_agreement"] == 1.0
    assert report["gate"]["status"] == "not_evaluable"


def test_model_proposals_and_adjudication_are_not_independent_raters():
    tasks = [("task-1", [
        revision(RATERS[0], annotation(attempted="yes")),
        revision(RATERS[1], annotation(attempted="yes")),
        revision(RATERS[2], annotation(attempted="no"), method="model_proposal"),
        revision("curator", annotation(attempted="no"),
                 review={"reviewed_revisions": {}, "rationale": "synthetic"}),
    ])]
    report = agreement_from_export(export(tasks), RATERS)
    assert report["coverage"]["tasks_with_all_raters"] == 0
    assert report["coverage"]["tasks_adjudicated"] == 1
    assert "curator" not in report["coverage"]["ignored_annotators"]
    assert report["fields"]["attempted"]["items"] == 0
    assert report["gate"]["status"] == "not_evaluable"


def test_identity_events_missing_from_one_rater_are_reported_not_inferred():
    partial = annotation()
    partial["identities"] = {"event-1": "actor-a", "event-2": "actor-a"}
    tasks = [("task-1", [revision(RATERS[0], partial),
                         revision(RATERS[1], annotation()),
                         revision(RATERS[2], annotation())])]
    report = agreement_from_export(export(tasks), RATERS)
    assert report["coverage"]["identity_events_omitted"] == 1
    assert report["fields"]["identities"]["items"] == 1


def test_latest_independent_revision_supersedes_the_earlier_one():
    tasks = [("task-1", [
        revision(RATERS[0], annotation(attempted="no")),
        revision(RATERS[0], annotation(attempted="yes")),
        revision(RATERS[1], annotation(attempted="yes")),
        revision(RATERS[2], annotation(attempted="yes")),
    ]), ("task-2", [revision(r, annotation(attempted="no")) for r in RATERS])]
    report = agreement_from_export(export(tasks), RATERS)
    assert report["fields"]["attempted"]["fleiss_kappa"] == 1.0
    assert report["coverage"]["per_rater"][RATERS[0]] == 2


def test_export_must_be_unreleased_and_well_formed():
    with pytest.raises(ValueError):
        agreement_from_export({"schema": "other", "tasks": []}, RATERS)
    broken = export(unanimous_tasks())
    broken["paper_metrics_available"] = True
    with pytest.raises(ValueError):
        agreement_from_export(broken, RATERS)
    with pytest.raises(ValueError):
        agreement_from_export(export(unanimous_tasks()), ("only-one",))


def test_real_record_store_export_and_cli_round_trip(tmp_path):
    from tools.annotator.evidence import build_bundle
    from tools.annotator.records import RecordStore
    from tools.annotator.tests.test_evidence import fixture_inputs

    from scripts.evaluation.annotation_agreement import main

    import copy

    inputs = fixture_inputs(tmp_path)
    root = tmp_path / "bundle"
    build_bundle(root, **inputs)
    second = copy.deepcopy(inputs)
    second["binding"].update(run_id="run-2",
                             source_run_uuid="96714245-5940-4804-b54e-3d5b3f647ced")
    build_bundle(tmp_path / "bundle-2", **second)
    store = RecordStore(tmp_path / "records.sqlite")

    def storable(attempted, executed, succeeded, actor):
        # The store enforces the documented monotone judgement chain.
        return {
            "attempted": {"value": attempted, "evidence_refs": ["event-1"]},
            "executed": {"value": executed, "evidence_refs": ["event-1"]},
            "succeeded": {"value": succeeded, "evidence_refs": ["event-1"]},
            "identities": {"event-1": actor},
            "roots": {"actors": [] if attempted == "no" else None,
                      "evidence_refs": ["event-1"] if attempted == "no" else []},
            "notes": "Synthetic agreement fixture; not a human label.",
        }

    for task, value in (
            (store.register_bundle(root), storable("yes", "yes", "no", "actor-a")),
            (store.register_bundle(tmp_path / "bundle-2"),
             storable("no", "no", "no", "actor-b"))):
        for name in RATERS:
            store.save(task, name, value, method="controlled_execution",
                       expected_revision=None)
    exported = tmp_path / "annotations.json"
    exported.write_text(json.dumps(store.export()))
    output = tmp_path / "agreement.json"
    assert main(["--export", str(exported), "--raters", *RATERS,
                 "--out", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["coverage"]["tasks_with_all_raters"] == 2
    assert report["exact_agreement"] == {"items": 2, "all_equal": 2, "percent": 1.0}
    assert report["fields"]["attempted"]["fleiss_kappa"] == 1.0
    assert report["gate"]["status"] == "pass"
    assert report["codebook_version"] == "pilot/1"
    assert report["paper_metrics_available"] is False
