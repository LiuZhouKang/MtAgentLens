# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Reproducible annotation dataset releases built from synthetic stores only.

Every fixture here is synthetic: raw bytes are frozen by ``fixture_inputs`` and
never derived from detector output. The real pilot artifacts under ``local/``
are deliberately not read by this test module.
"""

import copy
import hashlib
import json
import shutil
import uuid
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from tools.annotator.evidence import build_bundle, load_bundle
from tools.annotator.records import RecordStore, _validate_annotation
from tools.annotator.release import (
    SCHEMA,
    build_annotation_release,
    consume_annotation_release,
    verify_annotation_release,
)
from tools.annotator.tests.test_evidence import fixture_inputs

ANNOTATION_ROW_KEYS = {
    "case_id",
    "task_id",
    "annotator",
    "method",
    "revision_id",
    "parent_revision_id",
    "saved_at",
    "codebook_version",
    "annotation",
    "is_model_proposal",
}
CASE_KEYS = {
    "case_id",
    "task_id",
    "binding",
    "split",
    "evidence_bundle_hash",
    "evidence_counts",
    "actors_count",
    "events_count",
}


def annotation():
    # Valid under the monotone attempted >= executed >= succeeded rule in records.py.
    return {
        "attempted": {"value": "yes", "evidence_refs": ["event-1"]},
        "executed": {"value": "yes", "evidence_refs": ["event-1"]},
        "succeeded": {"value": "unknown", "evidence_refs": []},
        "identities": {"event-1": None},
        "roots": {"actors": None, "evidence_refs": []},
        "notes": "Synthetic regression; not a human annotation.",
    }


def make_store(tmp_path, *, count=2, codebook="pilot/1", methods=None, codebooks=None):
    """Register synthetic bundles and save one revision per task."""
    store_path = tmp_path / "records.sqlite"
    store = RecordStore(store_path)
    methods = methods or ("model_proposal",) * count
    codebooks = codebooks or (codebook,) * count
    tasks, bundles, revisions = [], [], []
    for index in range(count):
        base = tmp_path / f"input-{index}"
        base.mkdir()
        spec = fixture_inputs(base)
        spec["binding"]["codebook_version"] = codebooks[index]
        spec["binding"]["run_id"] = f"run-{index}"
        spec["binding"]["source_run_uuid"] = str(uuid.UUID(int=index + 1))
        root = tmp_path / f"bundle-{index}"
        build_bundle(root, **spec)
        task = store.register_bundle(root)
        tasks.append(task)
        bundles.append(root)
        revisions.append(store.save(task, f"rater-{index}", annotation(),
                                     method=methods[index], expected_revision=None))
    return SimpleNamespace(store=store, store_path=store_path, tasks=tasks,
                           bundles=bundles, revisions=revisions, codebook=codebook)


def build_release(tmp_path, env, name="release", **kwargs):
    out = tmp_path / name
    release = build_annotation_release(env.store_path, out, **kwargs)
    return release, out


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def release_of(root):
    return read_json(root / "release.json")


def read_rows(root):
    return [json.loads(line) for line in
            (root / "annotations.jsonl").read_text(encoding="utf-8").splitlines()]


def write_rows(root, rows):
    (root / "annotations.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def hash_files(root):
    """Mirror the builder's inventory rule: release.json and reviewers.json excluded."""
    excluded = {"release.json", "reviewers.json"}
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.relative_to(root).as_posix() not in excluded
    }


def reseal(root, *, sync_reviewers=True):
    """Rewrite files/release_id so a tamper bypasses the byte manifest on purpose."""
    release = release_of(root)
    release["files"] = hash_files(root)
    body = {key: value for key, value in release.items() if key != "release_id"}
    release["release_id"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    (root / "release.json").write_text(
        json.dumps(release, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if sync_reviewers:
        sidecar = read_json(root / "reviewers.json")
        sidecar["release_id"] = release["release_id"]
        (root / "reviewers.json").write_text(
            json.dumps(sidecar, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
    return release


# --------------------------------------------------------------------------- build


def test_release_id_is_reproducible_and_the_inventory_covers_every_file(tmp_path):
    env = make_store(tmp_path)
    first, root = build_release(tmp_path, env, "first")
    second = build_annotation_release(env.store_path, tmp_path / "second")
    assert first == release_of(root)
    assert first["release_id"] == second["release_id"]
    assert first["files"] == second["files"]
    assert first["cases"] == second["cases"]
    assert first["splits"] == second["splits"]
    assert (tmp_path / "second" / "release.json").read_bytes() == (
        root / "release.json").read_bytes()
    release = release_of(root)
    assert release["schema"] == SCHEMA
    assert release["files"] == hash_files(root)
    body = {key: value for key, value in release.items() if key != "release_id"}
    assert hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() == (
        release["release_id"])
    assert "release.json" not in release["files"]
    assert "reviewers.json" not in release["files"]
    for relative in ("README.md", "LICENSE", "annotations.jsonl", "attestations.json",
                     "schema/annotation.schema.json", "schema/evidence-bundle.schema.json",
                     "cases/case-0001/case.json", "cases/case-0001/bundle/bundle.json"):
        assert relative in release["files"]


def test_case_layout_and_annotation_contract(tmp_path):
    env = make_store(tmp_path)
    release, root = build_release(tmp_path, env)
    assert release["codebook_version"] == "pilot/1"
    assert release["paper_metrics_available"] is False
    assert release["publication_blockers"]
    assert all(isinstance(blocker, str) and blocker for blocker in release["publication_blockers"])
    tasks = sorted(env.tasks)
    assert [case["task_id"] for case in release["cases"]] == tasks
    assert [case["case_id"] for case in release["cases"]] == ["case-0001", "case-0002"]
    for case in release["cases"]:
        assert set(case) == CASE_KEYS
        case_dir = root / "cases" / case["case_id"]
        assert read_json(case_dir / "case.json") == case
        manifest = load_bundle(case_dir / "bundle")
        assert manifest["evidence_bundle_hash"] == case["evidence_bundle_hash"]
        assert case["task_id"] == case["evidence_bundle_hash"]
        assert case["binding"] == manifest["binding"]
        assert case["binding"]["codebook_version"] == "pilot/1"
        assert case["events_count"] == len(manifest["events"])
        assert case["actors_count"] == len(manifest["actors"])
        assert sum(case["evidence_counts"].values()) == case["events_count"]
        assert case["split"] == release["splits"][case["case_id"]]
        assert case["split"] in ("dev", "test")
    rows = read_rows(root)
    assert len(rows) == 2
    for row, record in zip(rows, env.revisions):
        assert set(row) == ANNOTATION_ROW_KEYS
        assert row["annotation"] == record["annotation"] == annotation()
        assert row["revision_id"] == record["revision_id"]
        assert row["method"] == "model_proposal"
        assert row["is_model_proposal"] is True
        assert row["parent_revision_id"] is None
        assert row["codebook_version"] == "pilot/1"
        assert row["task_id"] == record["task_id"]
    assert verify_annotation_release(root) == release


def test_readme_license_and_schemas_state_the_honest_boundaries(tmp_path):
    env = make_store(tmp_path)
    release, root = build_release(tmp_path, env)
    readme = (root / "README.md").read_text(encoding="utf-8")
    for phrase in ("model_proposal", "不是人工标签", "κ", "TPR/FPR", "非盲化",
                   "release_id", "Apache-2.0 OR MIT"):
        assert phrase in readme, phrase
    license_text = (root / "LICENSE").read_text(encoding="utf-8")
    assert "Apache-2.0 OR MIT" in license_text
    assert "LICENSE-APACHE" in license_text and "LICENSE-MIT" in license_text
    for name in ("annotation", "evidence-bundle"):
        schema = read_json(root / "schema" / f"{name}.schema.json")
        Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_store_without_any_revision_releases_evidence_only_cases(tmp_path):
    store_path = tmp_path / "records.sqlite"
    store = RecordStore(store_path)
    (tmp_path / "input").mkdir()
    spec = fixture_inputs(tmp_path / "input")
    build_bundle(tmp_path / "bundle", **spec)
    store.register_bundle(tmp_path / "bundle")
    release = build_annotation_release(store_path, tmp_path / "release")
    assert release["counts"]["annotations"] == 0
    assert (tmp_path / "release" / "annotations.jsonl").read_bytes() == b""
    assert verify_annotation_release(tmp_path / "release") == release
    assert any("no independent human annotations" in blocker
               for blocker in release["publication_blockers"])


# ------------------------------------------------------------------------ manifest


@pytest.mark.parametrize("tamper", ["bundle_source", "case_json", "delete", "extra", "readme"])
def test_manifest_detects_modified_missing_and_unregistered_files(tmp_path, tamper):
    env = make_store(tmp_path)
    _, root = build_release(tmp_path, env)
    verify_annotation_release(root)
    if tamper == "bundle_source":
        source = next((root / "cases/case-0001/bundle/sources").iterdir())
        source.write_bytes(b"x" * source.stat().st_size)
    elif tamper == "case_json":
        (root / "cases/case-0001/case.json").write_text("{}", encoding="utf-8")
    elif tamper == "delete":
        (root / "attestations.json").unlink()
    elif tamper == "extra":
        (root / "unexpected.txt").write_text("not inventoried", encoding="utf-8")
    else:
        with (root / "README.md").open("a", encoding="utf-8") as stream:
            stream.write("tampered\n")
    with pytest.raises(ValueError):
        verify_annotation_release(root)


def test_verify_rejects_a_rewritten_release_id_or_schema(tmp_path):
    env = make_store(tmp_path)
    _, root = build_release(tmp_path, env)
    original = read_json(root / "release.json")
    release = copy.deepcopy(original)
    release["release_id"] = "0" * 64
    (root / "release.json").write_text(json.dumps(release), encoding="utf-8")
    with pytest.raises(ValueError, match="release id"):
        verify_annotation_release(root)
    release = copy.deepcopy(original)
    release["schema"] = "mtagentlens.annotation-dataset/999"
    body = {key: value for key, value in release.items() if key != "release_id"}
    release["release_id"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    (root / "release.json").write_text(json.dumps(release), encoding="utf-8")
    with pytest.raises(ValueError):
        verify_annotation_release(root)


@pytest.mark.parametrize("where", ["root", "license", "cases"])
def test_symlinks_are_rejected(tmp_path, where):
    env = make_store(tmp_path)
    _, root = build_release(tmp_path, env)
    if where == "root":
        link = tmp_path / "linked-release"
        link.symlink_to(root)
        with pytest.raises(ValueError):
            verify_annotation_release(link)
        return
    if where == "license":
        target = root / "LICENSE"
        target.unlink()
        target.symlink_to(root / "release.json")
    else:
        (root / "cases" / "case-link").symlink_to(root / "cases" / "case-0001")
    with pytest.raises(ValueError):
        verify_annotation_release(root)


# ------------------------------------------------------------------------ consume


def test_consume_exposes_every_case_annotation_and_bundle(tmp_path):
    env = make_store(tmp_path)
    release, root = build_release(tmp_path, env)
    cases = consume_annotation_release(root)
    assert len(cases) == len(release["cases"]) == 2
    assert [case["case_id"] for case in cases] == ["case-0001", "case-0002"]
    records = {record["task_id"]: record for record in env.revisions}
    for case in cases:
        assert set(case) == CASE_KEYS | {"release_id", "codebook_version", "bundle_dir",
                                         "annotations"}
        assert case["release_id"] == release["release_id"]
        assert case["codebook_version"] == release["codebook_version"]
        manifest = load_bundle(root / case["bundle_dir"])
        assert manifest["evidence_bundle_hash"] == case["task_id"]
        assert len(case["annotations"]) == 1
        row = case["annotations"][0]
        assert row["task_id"] == case["task_id"]
        assert row["annotation"] == records[case["task_id"]]["annotation"]
        assert row["is_model_proposal"] is True


def test_consume_refuses_a_tampered_release(tmp_path):
    env = make_store(tmp_path)
    _, root = build_release(tmp_path, env)
    source = next((root / "cases/case-0001/bundle/sources").iterdir())
    source.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        consume_annotation_release(root)


# ------------------------------------------------------------------------- splits


def test_default_splits_are_deterministic(tmp_path):
    env = make_store(tmp_path, count=4)
    first, root = build_release(tmp_path, env, "first")
    second = build_annotation_release(env.store_path, tmp_path / "second")
    assert first["splits"] == second["splits"]
    assert set(first["splits"]) == {case["case_id"] for case in first["cases"]}
    assert set(first["splits"].values()) <= {"dev", "test"}
    assert all(case["split"] == first["splits"][case["case_id"]]
               for case in first["cases"])
    assert first["counts"]["splits"] == {
        name: sum(1 for value in first["splits"].values() if value == name)
        for name in ("dev", "test")
    }
    assert set(first["splits"]) == {case["case_id"] for case in first["cases"]}
    assert verify_annotation_release(root) == first


def test_explicit_splits_override_and_are_validated(tmp_path):
    env = make_store(tmp_path, count=3)
    base = build_annotation_release(env.store_path, tmp_path / "base")
    explicit = {case["case_id"]: "test" for case in base["cases"]}
    release = build_annotation_release(env.store_path, tmp_path / "explicit", splits=explicit)
    assert release["splits"] == explicit
    assert release["counts"]["splits"] == {"dev": 0, "test": 3}
    overridden = build_annotation_release(env.store_path, tmp_path / "override",
                                          splits={"case-0001": "test"})
    assert overridden["splits"]["case-0001"] == "test"
    assert overridden["splits"]["case-0002"] == "test"
    with pytest.raises(ValueError, match="split"):
        build_annotation_release(env.store_path, tmp_path / "unknown",
                                 splits={"case-9999": "dev"})
    with pytest.raises(ValueError, match="split"):
        build_annotation_release(env.store_path, tmp_path / "bad-value",
                                 splits={"case-0001": "train"})
    with pytest.raises(ValueError, match="splits"):
        build_annotation_release(env.store_path, tmp_path / "not-a-mapping", splits=["dev"])
    for name in ("unknown", "bad-value", "not-a-mapping"):
        assert not (tmp_path / name).exists()


# ------------------------------------------------------- immutability / failures


def test_existing_destination_is_never_overwritten(tmp_path):
    env = make_store(tmp_path)
    target = tmp_path / "release"
    target.mkdir()
    (target / "keep.txt").write_text("pre-existing", encoding="utf-8")
    with pytest.raises(ValueError, match="immutable"):
        build_annotation_release(env.store_path, target)
    assert (target / "keep.txt").read_text(encoding="utf-8") == "pre-existing"
    assert not (target / "release.json").exists()


def test_mixed_codebooks_are_rejected_without_partial_output(tmp_path):
    env = make_store(tmp_path, codebooks=["pilot/1", "pilot/1-draft"])
    with pytest.raises(ValueError, match="codebook"):
        build_annotation_release(env.store_path, tmp_path / "release")
    assert not (tmp_path / "release").exists()
    assert list(tmp_path.glob(".annotation-dataset-*")) == []


@pytest.mark.parametrize("case", ["missing_store", "empty_store", "missing_bundle"])
def test_missing_inputs_fail_without_output(tmp_path, case):
    if case == "missing_store":
        store_path = tmp_path / "absent.sqlite"
    elif case == "empty_store":
        store_path = tmp_path / "empty.sqlite"
        RecordStore(store_path)
    else:
        env = make_store(tmp_path, count=1)
        store_path = env.store_path
        shutil.rmtree(env.bundles[0])
    with pytest.raises(ValueError):
        build_annotation_release(store_path, tmp_path / "release")
    assert not (tmp_path / "release").exists()
    assert list(tmp_path.glob(".annotation-dataset-*")) == []


# ------------------------------------------------------- codebook neutrality etc.


def test_codebook_version_is_parameterized_and_never_hardcoded(tmp_path):
    codebook = "research/annotation-codebook@2026-10"
    env = make_store(tmp_path, codebook=codebook)
    release, root = build_release(tmp_path, env)
    assert release["codebook_version"] == codebook
    assert {case["binding"]["codebook_version"] for case in release["cases"]} == {codebook}
    assert all(row["codebook_version"] == codebook for row in read_rows(root))
    assert codebook in (root / "README.md").read_text(encoding="utf-8")
    assert "pilot/" not in (root / "README.md").read_text(encoding="utf-8")
    assert verify_annotation_release(root) == release


def test_declared_human_method_is_not_promoted_to_paper_gold(tmp_path):
    env = make_store(tmp_path, count=1, methods=("human",))
    release, root = build_release(tmp_path, env)
    rows = read_rows(root)
    assert rows[0]["method"] == "human"
    assert rows[0]["is_model_proposal"] is False
    assert release["counts"]["human_annotations"] == 1
    assert release["counts"]["model_proposals"] == 0
    assert release["paper_metrics_available"] is False
    assert any("identity" in blocker or "independent" in blocker
               for blocker in release["publication_blockers"])


def test_parent_revision_chain_and_adjudication_review_are_preserved(tmp_path):
    env = make_store(tmp_path, count=1, methods=("human",))
    task = env.tasks[0]
    first = env.revisions[0]
    second = env.store.save(task, "rater-0", annotation(), method="human",
                            expected_revision=first["revision_id"])
    alice = env.store.save(task, "alice", annotation(), method="human", expected_revision=None)
    bob = env.store.save(task, "bob", annotation(), method="human", expected_revision=None)
    review = {"reviewed_revisions": {"alice": alice["revision_id"],
                                     "bob": bob["revision_id"]},
              "rationale": "Compared the two original records."}
    curator = env.store.save(task, "curator", annotation(), method="human",
                             expected_revision=None, review=review)
    release, root = build_release(tmp_path, env)
    rows = read_rows(root)
    assert len(rows) == 5
    first_chain = [row for row in rows if row["annotator"] == "rater-0"]
    assert [row["parent_revision_id"] for row in first_chain] == [None, first["revision_id"]]
    assert [row["revision_id"] for row in first_chain] == [first["revision_id"],
                                                           second["revision_id"]]
    curator_row = next(row for row in rows if row["annotator"] == "curator")
    assert curator_row["review"] == review
    assert curator_row["revision_id"] == curator["revision_id"]
    assert release["counts"]["annotations"] == 5
    assert verify_annotation_release(root) == release


def test_note_is_recorded_and_type_checked(tmp_path):
    env = make_store(tmp_path, count=1)
    release = build_annotation_release(env.store_path, tmp_path / "release",
                                       note="Pilot release for external tools.")
    assert release["note"] == "Pilot release for external tools."
    with pytest.raises(ValueError, match="note"):
        build_annotation_release(env.store_path, tmp_path / "other", note=5)
    assert not (tmp_path / "other").exists()


# ------------------------------------------------------------- schema consistency


def test_annotation_schema_matches_the_authoritative_validator(tmp_path):
    env = make_store(tmp_path, count=1)
    _, root = build_release(tmp_path, env)
    schema = read_json(root / "schema" / "annotation.schema.json")
    validator = Draft202012Validator(schema)
    bundle = load_bundle(root / "cases/case-0001/bundle")
    valid = annotation()
    assert list(validator.iter_errors(valid)) == []
    _validate_annotation(valid, bundle)

    # Every legal branch of the monotone chain and the roots rules also validates.
    legal = []
    sample = copy.deepcopy(valid)  # attempted yes / executed unknown / succeeded unknown
    sample["executed"] = {"value": "unknown", "evidence_refs": []}
    legal.append(sample)
    sample = copy.deepcopy(valid)  # attempted no forces executed=no and succeeded=no
    for field in ("attempted", "executed", "succeeded"):
        sample[field] = {"value": "no", "evidence_refs": ["event-1"]}
    sample["roots"] = {"actors": [], "evidence_refs": ["event-1"]}
    legal.append(sample)
    sample = copy.deepcopy(valid)  # positive root attribution
    sample["identities"] = {"event-1": "actor-a"}
    sample["roots"] = {"actors": ["actor-a"], "evidence_refs": ["event-1"]}
    legal.append(sample)
    for sample in legal:
        assert list(validator.iter_errors(sample)) == []
        _validate_annotation(sample, bundle)

    invalid = {}

    sample = copy.deepcopy(valid)
    sample["human_signed"] = True
    invalid["extra_root_field"] = sample

    sample = copy.deepcopy(valid)
    del sample["notes"]
    invalid["missing_notes"] = sample

    sample = copy.deepcopy(valid)
    sample["attempted"]["value"] = "maybe"
    invalid["invalid_state"] = sample

    sample = copy.deepcopy(valid)
    sample["executed"]["evidence_refs"] = []
    invalid["known_without_refs"] = sample

    sample = copy.deepcopy(valid)
    sample["attempted"]["evidence_refs"] = ["event-1", "event-1"]
    invalid["duplicate_refs"] = sample

    sample = copy.deepcopy(valid)
    sample["attempted"]["why"] = "because"
    invalid["judgment_extra_field"] = sample

    sample = copy.deepcopy(valid)
    sample["attempted"] = {"value": "no", "evidence_refs": ["event-1"]}
    invalid["attempted_no_executed_yes"] = sample

    sample = copy.deepcopy(valid)
    sample["executed"] = {"value": "no", "evidence_refs": ["event-1"]}
    invalid["executed_no_succeeded_yes"] = sample

    sample = copy.deepcopy(valid)
    sample["executed"] = {"value": "unknown", "evidence_refs": []}
    sample["succeeded"] = {"value": "yes", "evidence_refs": ["event-1"]}
    invalid["success_outruns_unknown_execution"] = sample

    sample = copy.deepcopy(valid)
    sample["attempted"] = {"value": "no", "evidence_refs": ["event-1"]}
    sample["executed"] = {"value": "no", "evidence_refs": ["event-1"]}
    sample["succeeded"] = {"value": "no", "evidence_refs": ["event-1"]}
    sample["roots"] = {"actors": None, "evidence_refs": []}
    invalid["no_attempt_without_empty_roots"] = sample

    sample = copy.deepcopy(valid)
    sample["roots"] = {"actors": [], "evidence_refs": ["event-1"]}
    invalid["attempted_yes_with_empty_roots"] = sample

    sample = copy.deepcopy(valid)
    sample["roots"] = {"actors": [], "evidence_refs": []}
    invalid["empty_root_without_refs"] = sample

    sample = copy.deepcopy(valid)
    sample["notes"] = 5
    invalid["notes_not_text"] = sample

    sample = copy.deepcopy(valid)
    sample["identities"] = {"event-1": 5}
    invalid["identity_not_text_or_null"] = sample

    for name, document in invalid.items():
        errors = list(validator.iter_errors(document))
        assert errors, name
        with pytest.raises(ValueError):
            _validate_annotation(document, bundle)


def test_annotation_schema_defers_referential_checks_to_release_verification(tmp_path):
    env = make_store(tmp_path, count=1)
    _, root = build_release(tmp_path, env)
    schema = read_json(root / "schema" / "annotation.schema.json")
    bundle = load_bundle(root / "cases/case-0001/bundle")
    document = annotation()
    document["attempted"]["evidence_refs"] = ["event-outside-the-bundle"]
    document["identities"] = {}
    # JSON Schema cannot see the bundle, so structural validation still accepts this.
    assert list(Draft202012Validator(schema).iter_errors(document)) == []
    with pytest.raises(ValueError):
        _validate_annotation(document, bundle)


def test_verify_rejects_resealed_referential_or_provenance_tampering(tmp_path):
    env = make_store(tmp_path)
    _, root = build_release(tmp_path, env)

    rows = read_rows(root)
    rows[0]["annotation"]["attempted"]["evidence_refs"] = ["foreign-event"]
    write_rows(root, rows)
    reseal(root)
    with pytest.raises(ValueError):
        verify_annotation_release(root)

    # Restore a clean release for the next independent tamper.
    shutil.rmtree(root)
    _, root = build_release(tmp_path, env)
    rows = read_rows(root)
    rows[0]["is_model_proposal"] = False
    write_rows(root, rows)
    reseal(root)
    with pytest.raises(ValueError, match="is_model_proposal|method"):
        verify_annotation_release(root)

    shutil.rmtree(root)
    _, root = build_release(tmp_path, env)
    rows = read_rows(root)
    rows[0]["parent_revision_id"] = "0" * 32
    write_rows(root, rows)
    reseal(root)
    with pytest.raises(ValueError, match="chain|parent"):
        verify_annotation_release(root)


@pytest.mark.parametrize("field,value", [
    ("case_id", "../../outside"),
    ("task_id", "0" * 64),
    ("split", "train"),
])
def test_verify_rejects_forged_case_entries_even_when_resealed(tmp_path, field, value):
    env = make_store(tmp_path, count=1)
    _, root = build_release(tmp_path, env)
    release = release_of(root)
    release["cases"][0][field] = value
    (root / "release.json").write_text(json.dumps(release), encoding="utf-8")
    reseal(root)
    with pytest.raises(ValueError):
        verify_annotation_release(root)


def test_verify_rejects_resealed_counts_or_blocker_edits(tmp_path):
    env = make_store(tmp_path, count=1)
    _, root = build_release(tmp_path, env)
    release = release_of(root)
    release["counts"]["annotations"] = 99
    (root / "release.json").write_text(json.dumps(release), encoding="utf-8")
    reseal(root)
    with pytest.raises(ValueError, match="counts"):
        verify_annotation_release(root)

    release = release_of(root)
    release["counts"]["annotations"] = 1
    release["publication_blockers"] = []
    (root / "release.json").write_text(json.dumps(release), encoding="utf-8")
    reseal(root)
    with pytest.raises(ValueError):
        verify_annotation_release(root)


def test_evidence_bundle_schema_covers_the_frozen_bundle(tmp_path):
    env = make_store(tmp_path, count=1)
    _, root = build_release(tmp_path, env)
    schema = read_json(root / "schema" / "evidence-bundle.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    manifest = read_json(root / "cases/case-0001/bundle/bundle.json")
    assert list(validator.iter_errors(manifest)) == []
    invalid = {}
    sample = copy.deepcopy(manifest)
    sample["schema"] = "mtagentlens.evidence-bundle/9"
    invalid["schema"] = sample
    sample = copy.deepcopy(manifest)
    sample["binding"]["unexpected"] = "value"
    invalid["binding_extra_key"] = sample
    sample = copy.deepcopy(manifest)
    sample["binding"]["data_release_id"] = "zz"
    invalid["binding_digest"] = sample
    sample = copy.deepcopy(manifest)
    sample["events"][0]["byte_length"] = 0
    invalid["event_length"] = sample
    sample = copy.deepcopy(manifest)
    sample["actors"].append(sample["actors"][0])
    invalid["duplicate_actor"] = sample
    sample = copy.deepcopy(manifest)
    sample["evidence_bundle_hash"] = "0" * 63
    invalid["bundle_hash"] = sample
    sample = copy.deepcopy(manifest)
    sample["events"][0]["unexpected"] = "value"
    invalid["event_extra_key"] = sample
    for name, document in invalid.items():
        assert list(validator.iter_errors(document)), name


# --------------------------------------------------- reviewers / attestations


def test_reviewers_sidecar_without_input_is_empty_and_honest(tmp_path):
    env = make_store(tmp_path)
    release, root = build_release(tmp_path, env)
    sidecar = read_json(root / "reviewers.json")
    assert sidecar["reviewers"] == []
    assert sidecar["release_id"] == release["release_id"]
    assert sidecar["paper_metrics_available"] is False
    assert "not evidence of review" in sidecar["note"]
    assert "reviewers" not in release["counts"]
    assert "reviewers.json" not in release["files"]
    assert verify_annotation_release(root) == release


def test_reviewer_assignment_input_is_imported_and_bound(tmp_path):
    env = make_store(tmp_path)
    source = tmp_path / "reviewers.json"
    payload = {
        "schema": "mtagentlens.reviewer-assignment/v1",
        "tasks": ["task-0001", "task-0002"],
        "reviewers": [
            {"name": "reviewer-a", "role": "final_reviewer", "status": "assigned_pending_review"},
            {"name": "reviewer-b", "role": "reviewer", "status": "assigned_pending_review"},
        ],
        "note": "Responsibility assignment only.",
        "paper_metrics_available": True,
    }
    source.write_text(json.dumps(payload), encoding="utf-8")
    release, root = build_release(tmp_path, env, reviewers=source)
    sidecar = read_json(root / "reviewers.json")
    assert [entry["name"] for entry in sidecar["reviewers"]] == ["reviewer-a", "reviewer-b"]
    assert sidecar["tasks"] == payload["tasks"]
    assert sidecar["note"] == payload["note"]
    assert sidecar["release_id"] == release["release_id"]
    # An input claiming paper metrics cannot turn them on for this release.
    assert sidecar["paper_metrics_available"] is False
    assert release["paper_metrics_available"] is False
    assert verify_annotation_release(root) == release
    sidecar["release_id"] = "0" * 64
    (root / "reviewers.json").write_text(json.dumps(sidecar), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewers"):
        verify_annotation_release(root)


def test_missing_or_invalid_reviewer_sidecars_are_rejected(tmp_path):
    env = make_store(tmp_path, count=1)
    bad = tmp_path / "bad-reviewers.json"
    bad.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewer"):
        build_annotation_release(env.store_path, tmp_path / "release", reviewers=bad)
    assert not (tmp_path / "release").exists()
    bad.write_text(json.dumps({"reviewers": [{"role": "reviewer"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewer"):
        build_annotation_release(env.store_path, tmp_path / "release", reviewers=bad)
    build_annotation_release(env.store_path, tmp_path / "release")
    (tmp_path / "release" / "reviewers.json").unlink()
    with pytest.raises(ValueError):
        verify_annotation_release(tmp_path / "release")


def test_attestations_are_exported_truthfully(tmp_path):
    env = make_store(tmp_path, count=1)
    record = env.store.save_attestation(
        env.tasks[0], "reviewer-a", env.revisions[0]["revision_id"],
        statement="I checked the model draft against the evidence bundle.")
    release, root = build_release(tmp_path, env)
    exported = read_json(root / "attestations.json")
    assert exported["attestations"] == env.store.list_attestations() == [record]
    assert exported["schema"] == "mtagentlens.review-attestation-export/v1"
    assert exported["paper_metrics_available"] is False
    assert exported["counts"]["attestations"] == 1
    assert release["counts"]["attestations"] == 1
    assert any("attestation" in blocker.lower()
               for blocker in release["publication_blockers"])
    assert release["paper_metrics_available"] is False
    assert verify_annotation_release(root) == release


def test_verify_rejects_attestation_tampering_without_reseal(tmp_path):
    env = make_store(tmp_path, count=1)
    _, root = build_release(tmp_path, env)
    payload = read_json(root / "attestations.json")
    payload["attestations"].append({"schema": "mtagentlens.review-attestation/v1"})
    (root / "attestations.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        verify_annotation_release(root)


def test_verify_rejects_a_resealed_schema_downgrade(tmp_path):
    env = make_store(tmp_path, count=1)
    _, root = build_release(tmp_path, env)
    schema = read_json(root / "schema" / "annotation.schema.json")
    schema["additionalProperties"] = True  # weaken the shipped structural contract
    (root / "schema" / "annotation.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    reseal(root)
    with pytest.raises(ValueError, match="schema"):
        verify_annotation_release(root)


def test_verify_rejects_a_resealed_fake_attestation(tmp_path):
    env = make_store(tmp_path, count=1, methods=("human",))
    task = env.tasks[0]
    human = env.revisions[0]
    proposal = env.store.save(task, "model-proposal:test", annotation(),
                              method="model_proposal", expected_revision=None)
    env.store.save_attestation(task, "reviewer-a", proposal["revision_id"],
                               statement="I checked the model draft.")
    _, root = build_release(tmp_path, env)
    payload = read_json(root / "attestations.json")
    # Point the typed statement at a human revision: attestations only cover model drafts.
    payload["attestations"][0]["proposal_revision_id"] = human["revision_id"]
    (root / "attestations.json").write_text(json.dumps(payload), encoding="utf-8")
    reseal(root)
    with pytest.raises(ValueError, match="proposal"):
        verify_annotation_release(root)


def test_verify_rejects_resealed_paper_metric_claims(tmp_path):
    env = make_store(tmp_path, count=1)
    _, root = build_release(tmp_path, env)
    release = release_of(root)
    release["paper_metrics_available"] = True
    (root / "release.json").write_text(json.dumps(release), encoding="utf-8")
    reseal(root)
    with pytest.raises(ValueError, match="paper metrics"):
        verify_annotation_release(root)
    release = release_of(root)
    release["paper_metrics_available"] = False
    (root / "release.json").write_text(json.dumps(release), encoding="utf-8")
    reseal(root)
    sidecar = read_json(root / "reviewers.json")
    sidecar["paper_metrics_available"] = True
    (root / "reviewers.json").write_text(json.dumps(sidecar), encoding="utf-8")
    with pytest.raises(ValueError, match="paper metrics"):
        verify_annotation_release(root)


def test_default_split_groups_all_runs_of_one_scenario(tmp_path):
    env = make_store(tmp_path, count=4)
    release, _ = build_release(tmp_path, env)
    assert len({case['binding']['scenario_id'] for case in release['cases']}) == 1
    assert len(set(release['splits'].values())) == 1


def test_scenario_split_is_stable_when_case_numbers_and_release_change():
    from tools.annotator.release import _resolve_splits
    original = [{'case_id': 'case-0001', 'binding': {'scenario_id': 'scenario-a'}}]
    expanded = [
        {'case_id': 'case-0001', 'binding': {'scenario_id': 'scenario-b'}},
        {'case_id': 'case-0002', 'binding': {'scenario_id': 'scenario-a'}},
    ]
    assert _resolve_splits(original, None)['case-0001'] == _resolve_splits(expanded, None)['case-0002']


def test_conflicting_explicit_splits_for_same_scenario_are_rejected(tmp_path):
    env = make_store(tmp_path, count=2)
    with pytest.raises(ValueError, match='scenario.*split|split.*scenario'):
        build_annotation_release(env.store_path, tmp_path / 'release',
                                 splits={'case-0001': 'dev', 'case-0002': 'test'})
    assert not (tmp_path / 'release').exists()


def test_verify_and_consume_reject_resealed_scenario_split_leakage(tmp_path):
    env = make_store(tmp_path, count=2)
    release, root = build_release(tmp_path, env)
    for case, split in zip(release['cases'], ('dev', 'test')):
        case['split'] = split
        release['splits'][case['case_id']] = split
        (root / 'cases' / case['case_id'] / 'case.json').write_text(json.dumps(case))
    release['counts']['splits'] = {'dev': 1, 'test': 1}
    (root / 'release.json').write_text(json.dumps(release))
    reseal(root)
    for reader in (verify_annotation_release, consume_annotation_release):
        with pytest.raises(ValueError, match='scenario.*split|split.*scenario'):
            reader(root)


def test_unbound_bundle_exports_and_consumes_without_claiming_dataset_release(tmp_path):
    from scripts.evaluation.annotation_release_consumer import verify_release as independent_verify

    spec = fixture_inputs(tmp_path)
    dataset = spec['release_root']
    (dataset / 'release.json').unlink()
    (dataset / 'scenarios').mkdir()
    (dataset / 'scenarios/scenario.json').write_text('{"id":"scenario"}')
    spec['binding']['data_release_id'] = None
    bundle_dir = tmp_path / 'unbound-bundle'
    build_bundle(bundle_dir, **spec)
    store = RecordStore(tmp_path / 'records.sqlite')
    task = store.register_bundle(bundle_dir)
    store.save(task, 'model-a', annotation(), method='model_proposal', expected_revision=None)
    out = tmp_path / 'annotation-export'
    manifest = build_annotation_release(tmp_path / 'records.sqlite', out)
    assert manifest['cases'][0]['binding']['data_release_id'] is None
    assert verify_annotation_release(out) == manifest
    assert consume_annotation_release(out)[0]['binding']['data_release_id'] is None
    result = independent_verify(out)
    assert result['problems'] == []


def test_mixed_historical_and_unbound_bundles_keep_distinct_identities(tmp_path):
    from scripts.evaluation.annotation_release_consumer import verify_release as independent_verify
    env = make_store(tmp_path, count=1)
    base = tmp_path / 'unbound-input'
    base.mkdir()
    spec = fixture_inputs(base)
    dataset = spec['release_root']
    (dataset / 'release.json').unlink()
    (dataset / 'scenarios').mkdir()
    (dataset / 'scenarios/scenario.json').write_text('{"id":"scenario"}')
    spec['binding']['data_release_id'] = None
    bundle = tmp_path / 'unbound-bundle'
    build_bundle(bundle, **spec)
    task = env.store.register_bundle(bundle)
    env.store.save(task, 'model-b', annotation(), method='model_proposal', expected_revision=None)
    out = tmp_path / 'mixed-export'
    manifest = build_annotation_release(env.store_path, out)
    identities = [case['binding']['data_release_id'] for case in manifest['cases']]
    assert identities.count(None) == 1
    assert sum(isinstance(value, str) and len(value) == 64 for value in identities) == 1
    assert verify_annotation_release(out) == manifest
    assert independent_verify(out)['problems'] == []


@pytest.mark.parametrize('schema, identity, expected', [
    ('mtagentlens.evidence-bundle/v1', None, 'historical bundle lacks a valid release identity'),
    ('mtagentlens.evidence-bundle/v2', 'a' * 64, 'unbound bundle claims a release identity'),
])
def test_external_consumer_rejects_mislabelled_evidence_identity(tmp_path, schema, identity, expected):
    from scripts.evaluation.annotation_release_consumer import verify_cases
    env = make_store(tmp_path, count=1)
    manifest, out = build_release(tmp_path, env)
    path = out / 'cases/case-0001/bundle/bundle.json'
    bundle = json.loads(path.read_text())
    bundle['schema'] = schema
    bundle['binding']['data_release_id'] = identity
    path.write_text(json.dumps(bundle))
    assert any(expected in problem for problem in verify_cases(out, manifest))
