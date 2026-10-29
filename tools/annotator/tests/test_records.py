"""Run-bound append-only records with optimistic concurrency and full cohorts."""

import copy
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tools.annotator.evidence import build_bundle
from tools.annotator.records import RecordStore
from tools.annotator.tests.test_evidence import fixture_inputs


def annotation():
    return {
        "attempted": {"value": "yes", "evidence_refs": ["event-1"]},
        "executed": {"value": "no", "evidence_refs": ["event-1"]},
        "succeeded": {"value": "no", "evidence_refs": ["event-1"]},
        "identities": {"event-1": None},
        "roots": {"actors": None, "evidence_refs": []},
        "notes": "Synthetic regression; not a human annotation.",
    }


@pytest.fixture
def setup(tmp_path):
    inputs = fixture_inputs(tmp_path)
    root = tmp_path / "bundle"
    bundle = build_bundle(root, **inputs)
    store = RecordStore(tmp_path / "records.sqlite")
    task = store.register_bundle(root)
    return store, task, root, inputs, bundle


def save(store, task, value=None, *, parent=None, method="model_proposal", annotator="worker"):
    return store.save(task, annotator, value if value is not None else annotation(),
                      method=method, expected_revision=parent)


def test_roundtrip_preserves_binding_unknowns_and_declared_method(setup):
    store, task, _, _, bundle = setup
    record = save(store, task)
    assert record["binding"] == bundle["binding"]
    assert record["evidence_bundle_hash"] == bundle["evidence_bundle_hash"]
    assert record["method"] == "model_proposal"
    assert record["parent_revision_id"] is None
    assert record["annotation"] == annotation()
    export = store.export()
    assert export["schema"] == "mtagentlens.annotation-export/v2"
    assert export["paper_metrics_available"] is False
    assert export["tasks"][0]["revisions"] == [record]


def test_export_retains_unannotated_tasks_and_all_revisions(setup, tmp_path):
    store, task, _, inputs, _ = setup
    first = save(store, task)
    second = save(store, task, parent=first["revision_id"])
    other = copy.deepcopy(inputs)
    other["binding"]["run_id"] = "run-2"
    other["binding"]["source_run_uuid"] = "96714245-5940-4804-b54e-3d5b3f647ced"
    build_bundle(tmp_path / "other", **other)
    other_task = store.register_bundle(tmp_path / "other")
    tasks = {row["task_id"]: row for row in store.export()["tasks"]}
    assert tasks[other_task]["revisions"] == []
    assert tasks[other_task]["status"] == "unannotated"
    assert tasks[task]["revisions"] == [first, second]
    assert second["parent_revision_id"] == first["revision_id"]


def test_stale_save_rejected_without_inserting_a_revision(setup):
    store, task, *_ = setup
    first = save(store, task)
    with pytest.raises(ValueError, match="revision"):
        save(store, task)
    assert store.export()["tasks"][0]["revisions"] == [first]


def test_two_connections_cannot_both_save_the_same_parent(setup, tmp_path):
    store, task, *_ = setup
    first = save(store, task)
    second_store = RecordStore(tmp_path / "records.sqlite")

    def attempt(current):
        try:
            return save(current, task, parent=first["revision_id"])
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [store, second_store]))
    assert sum(row is not None for row in results) == 1
    assert len(store.export()["tasks"][0]["revisions"]) == 2


def test_same_bundle_registration_is_idempotent_but_rebinding_run_is_rejected(setup, tmp_path):
    store, task, root, inputs, _ = setup
    assert store.register_bundle(root) == task
    inputs["actors"].append("different registry for the same run")
    build_bundle(tmp_path / "changed", **inputs)
    with pytest.raises(ValueError, match="binding"):
        store.register_bundle(tmp_path / "changed")
    assert len(store.export()["tasks"]) == 1


def test_changed_bundle_blocks_save_and_export(setup):
    store, task, root, _, bundle = setup
    (root / bundle["sources"]["runner"]["path"]).write_text("tampered")
    with pytest.raises(ValueError):
        save(store, task)
    with pytest.raises(ValueError):
        store.export()


@pytest.mark.parametrize("change", ["missing_event", "extra_event", "unknown_actor", "unknown_ref",
                                    "no_evidence", "invalid_state", "extra_field", "invalid_root"])
def test_invalid_annotations_do_not_create_records(setup, change):
    store, task, *_ = setup
    value = annotation()
    if change == "missing_event":
        value["identities"] = {}
    elif change == "extra_event":
        value["identities"]["foreign-event"] = None
    elif change == "unknown_actor":
        value["identities"]["event-1"] = "detector-agent"
    elif change == "unknown_ref":
        value["attempted"]["evidence_refs"] = ["foreign-event"]
    elif change == "no_evidence":
        value["executed"]["evidence_refs"] = []
    elif change == "invalid_state":
        value["succeeded"]["value"] = "benign"
    elif change == "extra_field":
        value["human_signed"] = True
    else:
        value["roots"]["actors"] = ["detector-agent"]
    with pytest.raises(ValueError):
        save(store, task, value)
    assert store.export()["tasks"][0]["revisions"] == []


def test_human_method_does_not_automatically_create_publishable_gold(setup):
    store, task, *_ = setup
    save(store, task, method="human")
    assert store.export()["paper_metrics_available"] is False


def test_unknown_and_empty_root_are_different(setup):
    store, task, *_ = setup
    value = annotation()
    value["attempted"] = {"value": "no", "evidence_refs": ["event-1"]}
    value["executed"] = {"value": "no", "evidence_refs": ["event-1"]}
    value["succeeded"] = {"value": "no", "evidence_refs": ["event-1"]}
    value["roots"] = {"actors": [], "evidence_refs": ["event-1"]}
    value["identities"]["event-1"] = "actor-a"
    assert save(store, task, value)["annotation"]["roots"]["actors"] == []


def _judgment(state):
    return {"value": state, "evidence_refs": ["event-1"]}


@pytest.mark.parametrize(
    "attempted,executed,succeeded", [
        ("no", "no", "unknown"),    # a definite "no attempt" cannot leave success unknown
        ("no", "unknown", "no"),    # ...nor execution unknown
        ("no", "yes", "no"),        # ...nor claim an execution happened
        ("no", "no", "yes"),
        ("yes", "no", "unknown"),   # not executed cannot have an undetermined outcome
        ("yes", "no", "yes"),       # success proves execution
        ("unknown", "yes", "unknown"),  # execution proves an attempt
        ("unknown", "unknown", "yes"),
    ])
def test_judgments_must_form_a_monotone_chain(setup, attempted, executed, succeeded):
    """attempted >= executed >= succeeded: a downstream state never outruns its precondition."""
    store, task, *_ = setup
    value = annotation()
    value["attempted"] = _judgment(attempted)
    value["executed"] = _judgment(executed)
    value["succeeded"] = _judgment(succeeded)
    with pytest.raises(ValueError):
        save(store, task, value)


@pytest.mark.parametrize(
    "attempted,executed,succeeded", [
        ("yes", "yes", "yes"),
        ("yes", "yes", "no"),
        ("yes", "yes", "unknown"),
        ("yes", "unknown", "unknown"),
        ("unknown", "no", "no"),
        ("unknown", "unknown", "unknown"),
        ("no", "no", "no"),
    ])
def test_consistent_judgment_chains_are_accepted(setup, attempted, executed, succeeded):
    store, task, *_ = setup
    value = annotation()
    value["attempted"] = _judgment(attempted)
    value["executed"] = _judgment(executed)
    value["succeeded"] = _judgment(succeeded)
    value["roots"] = {"actors": [] if attempted == "no" else None,
                      "evidence_refs": ["event-1"] if attempted == "no" else []}
    assert save(store, task, value)["annotation"]["attempted"]["value"] == attempted


def test_empty_root_set_requires_a_definite_no_attempt(setup):
    """`roots=[]` is the positive claim 'no actor is the root cause', not a shrug."""
    store, task, *_ = setup
    value = annotation()
    value["roots"] = {"actors": [], "evidence_refs": ["event-1"]}
    with pytest.raises(ValueError, match="root"):
        save(store, task, value)


def test_definite_no_attempt_requires_the_empty_root_set(setup):
    """A benign judgement cannot leave the root cause undetermined."""
    store, task, *_ = setup
    value = annotation()
    value["attempted"] = _judgment("no")
    value["executed"] = _judgment("no")
    value["succeeded"] = _judgment("no")
    value["roots"] = {"actors": None, "evidence_refs": []}
    with pytest.raises(ValueError, match="root"):
        save(store, task, value)


def test_sql_history_is_append_only(setup, tmp_path):
    store, task, *_ = setup
    save(store, task)
    conn = sqlite3.connect(tmp_path / "records.sqlite")
    try:
        for sql in ("DELETE FROM bundle_revisions", "UPDATE bundle_revisions SET annotator='other'",
                    "DELETE FROM bundle_tasks"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)
    finally:
        conn.close()


@pytest.mark.parametrize("initial_sql", ["CREATE TABLE runs(run_id TEXT)",
                                       "CREATE VIEW example AS SELECT 1", "PRAGMA user_version=7"])
def test_existing_unrelated_database_is_never_modified(tmp_path, initial_sql):
    path = tmp_path / "collector.db"
    conn = sqlite3.connect(path)
    conn.execute(initial_sql)
    conn.commit()
    conn.close()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="store"):
        RecordStore(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("changes", [{"attempted": "no", "executed": "yes"},
                                     {"executed": "no", "succeeded": "yes"},
                                     {"attempted": "no", "succeeded": "yes"}])
def test_explicitly_contradictory_states_are_rejected_without_inferring_unknown(setup, changes):
    store, task, *_ = setup
    value = annotation()
    for key, state in changes.items():
        value[key] = {"value": state, "evidence_refs": ["event-1"]}
    with pytest.raises(ValueError):
        save(store, task, value)


@pytest.mark.parametrize("operation", ["save", "export"])
def test_slow_evidence_audit_does_not_block_another_writer(setup, tmp_path, monkeypatch, operation):
    store, task, *_ = setup
    first = save(store, task)
    other = RecordStore(tmp_path / "records.sqlite")
    entered, release = threading.Event(), threading.Event()
    original = store._bundle

    def slow_audit(task_row):
        entered.set()
        assert release.wait(timeout=5)
        return original(task_row)

    monkeypatch.setattr(store, "_bundle", slow_audit)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(store.export if operation == "export" else
                              lambda: save(store, task, parent=first["revision_id"]))
        try:
            assert entered.wait(timeout=2)
            accepted = pool.submit(save, other, task, parent=first["revision_id"]).result(timeout=2)
        finally:
            release.set()
        if operation == "save":
            with pytest.raises(ValueError, match="revision"):
                pending.result(timeout=2)
        else:
            assert pending.result(timeout=2)["tasks"][0]["revisions"] == [first]
    assert other.export()["tasks"][0]["revisions"][-1] == accepted


def test_scoped_task_reads_and_original_record_bytes(setup):
    store, task, _, inputs, _ = setup
    alice = save(store, task, annotator="alice", method="human")
    save(store, task, annotator="bob", method="human")
    assert store.list_tasks("alice") == [{"task_id": task, "has_annotation": True}]
    assert store.list_tasks("new-rater") == [{"task_id": task, "has_annotation": False}]
    view = store.read_task(task, annotators=("alice",))
    assert view["revisions"] == [alice]
    assert view["bundle"]["events"][0]["event_id"] == "event-1"
    evidence = store.read_evidence(task, "event-1", limit=8)
    assert evidence["bytes"] == inputs["sources"]["runner"]["path"].read_bytes()[:8]
    assert evidence["byte_length"] > len(evidence["bytes"])
    with pytest.raises(ValueError):
        store.read_evidence(task, "foreign-event")


def test_preview_checks_record_bytes_if_source_changes_after_bundle_validation(setup, monkeypatch):
    store, task, root, _, bundle = setup
    original = store._bundle

    def changed(task_row):
        validated = original(task_row)
        source = root / bundle["sources"]["runner"]["path"]
        source.write_bytes(b"x" * source.stat().st_size)
        return validated

    monkeypatch.setattr(store, "_bundle", changed)
    with pytest.raises(ValueError, match="checksum"):
        store.read_evidence(task, "event-1", limit=8)


def review_heads(store, task):
    alice = save(store, task, annotator="alice", method="human")
    bob = save(store, task, annotator="bob", method="human")
    return {"alice": alice["revision_id"], "bob": bob["revision_id"]}


def test_adjudication_preserves_exact_reviewed_revisions_and_rationale(setup):
    store, task, *_ = setup
    heads = review_heads(store, task)
    review = {"reviewed_revisions": heads, "rationale": "Compared the original record."}
    record = store.save(task, "curator", annotation(), method="human", expected_revision=None,
                        review=review)
    assert record["review"] == review
    assert len(store.export()["tasks"][0]["revisions"]) == 3
    assert store.export()["paper_metrics_available"] is False


def test_adjudication_rejects_changed_source_revision(setup):
    store, task, *_ = setup
    heads = review_heads(store, task)
    save(store, task, annotator="alice", method="human", parent=heads["alice"])
    with pytest.raises(ValueError, match="review revision"):
        store.save(task, "curator", annotation(), method="human", expected_revision=None,
                   review={"reviewed_revisions": heads, "rationale": "stale comparison"})
    assert store.list_tasks("curator")[0]["has_annotation"] is False


@pytest.mark.parametrize("change", ["self", "one_rater", "missing", "empty_rationale", "model"])
def test_invalid_adjudication_cannot_create_a_record(setup, change):
    store, task, *_ = setup
    heads = review_heads(store, task)
    rationale = "Compared source records."
    if change == "self":
        who = "alice"
    else:
        who = "curator"
    if change == "one_rater":
        heads.pop("bob")
    elif change == "missing":
        heads["bob"] = "missing-revision"
    elif change == "empty_rationale":
        rationale = " "
    elif change == "model":
        record = save(store, task, annotator="bob", parent=heads["bob"], method="model_proposal")
        heads["bob"] = record["revision_id"]
    with pytest.raises(ValueError):
        store.save(task, who, annotation(), method="human", expected_revision=None,
                   review={"reviewed_revisions": heads, "rationale": rationale})
