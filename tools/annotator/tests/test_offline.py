import copy

import pytest

from tools.annotator.offline import (
    build_scene_package,
    build_task_pack,
    create_workspace,
    merge_workspaces,
    merge_scene_exports,
    merge_runtime_exports,
    verify_task_pack,
    verify_workspace,
    load_scene_package,
    export_identity_reference,
    export_root_reference,
    stage_runtime_adjudication,
)
from tools.annotator.evidence import build_bundle
from tools.annotator.records import RecordStore
from tools.annotator.tests.test_evidence import fixture_inputs


def pack():
    return build_task_pack(
        task_type="fuxibench_scene",
        schema_version="mtagentlens.fuxibench-scene-export/v1",
        codebook_version="fuxibench-2type/1",
        raters=["A", "B", "C"],
        tasks=[{"task_id": "case-1", "input_sha256": "a" * 64}],
        sources={"materials.json": b"frozen"},
    )


def workspace(task_pack, rater):
    return create_workspace(task_pack, rater)


def submitted(work, annotation=None):
    work["tasks"][0]["revisions"].append({
        "revision_id": f"rev-{work['annotator']}",
        "task_id": "case-1",
        "annotator": work["annotator"],
        "parent_revision_id": None,
        "kind": "submission",
        "annotation": annotation or {"label": work["annotator"]},
    })
    return work


def test_task_pack_binds_source_bytes_and_rejects_tampering():
    value = pack()
    assert verify_task_pack(value)["pack_sha256"] == value["pack_sha256"]
    changed = copy.deepcopy(value)
    changed["sources"]["materials.json"]["content"] = "Zm9yZ2Vk"
    with pytest.raises(ValueError, match="digest"):
        verify_task_pack(changed)


def test_merge_requires_one_complete_workspace_per_configured_rater():
    task_pack = pack()
    values = [submitted(workspace(task_pack, rater)) for rater in "ABC"]
    merged = merge_workspaces(task_pack, values)
    assert merged["annotators"] == ["A", "B", "C"]
    assert len(merged["tasks"][0]["submissions"]) == 3


def test_merge_rejects_duplicate_rater_and_missing_task_submission():
    task_pack = pack()
    duplicate = [submitted(workspace(task_pack, "A")), submitted(workspace(task_pack, "A")),
                 submitted(workspace(task_pack, "C"))]
    with pytest.raises(ValueError, match="duplicate annotator"):
        merge_workspaces(task_pack, duplicate)
    missing = [submitted(workspace(task_pack, "A")), workspace(task_pack, "B"),
               submitted(workspace(task_pack, "C"))]
    with pytest.raises(ValueError, match="missing submission"):
        merge_workspaces(task_pack, missing)


def test_workspace_verifies_pack_binding_and_identity():
    task_pack = pack()
    work = submitted(workspace(task_pack, "A"))
    assert verify_workspace(task_pack, work)["annotator"] == "A"
    work["annotator"] = "B"
    with pytest.raises(ValueError, match="revision identity"):
        verify_workspace(task_pack, work)


def test_merge_scene_exports_joins_isolated_rater_chains():
    from tools.annotator.offline import _digest

    exports = []
    for name, label in zip("ABC", ("attack", "attack", "benign")):
        exports.append({
            "schema": "mtagentlens.fuxibench-scene-export/v1",
            "codebook_version": "v1", "codebook_sha256": "b" * 64,
            "codebook_hash_kind": "file", "catalog_sha256": "c" * 64,
            "raters": [name], "scenarios": [{
                "scenario_id": "case-1", "input_sha256": "a" * 64,
                "sources": [], "adjudication": None, "adjudications": [],
                "rater_chains": [{"rater": name, "submitted": True,
                                   "revisions": [{"revision_id": f"r-{name}", "rater": name,
                                                  "scenario_id": "case-1",
                                                  "annotation": {"label": label,
                                                    "attack_type": "benign" if label == "benign" else "A",
                                                    "min_decision": "Safe" if label == "benign" else "Unsafe",
                                                    "should_alert": label != "benign"}}]}],
            }],
        })
    with pytest.raises(ValueError, match="missing its content digest"):
        merge_scene_exports(exports, list("ABC"))
    for export in exports:
        export["export_sha256"] = _digest(export)
    altered = copy.deepcopy(exports)
    altered[0]["scenarios"][0]["rater_chains"][0]["revisions"][0]["annotation"]["label"] = "benign"
    with pytest.raises(ValueError, match="content digest mismatch"):
        merge_scene_exports(altered, list("ABC"))
    merged = merge_scene_exports(exports, list("ABC"))
    assert [chain["rater"] for chain in merged["scenarios"][0]["rater_chains"]] == list("ABC")
    assert merged["agreement"]["fields"]["label"]["n_raters"] == 3


def test_merge_runtime_exports_requires_identical_evidence_and_three_human_labels():
    from tools.annotator.offline import _digest, _validate_merged_runtime

    exports = []
    for name in "ABC":
        manifest = {"evidence_bundle_hash": "a" * 64, "binding": {"run_id": "run-1"}}
        exports.append({"schema": "mtagentlens.annotation-export/v2", "tasks": [{
            "task_id": "a" * 64, "binding": manifest["binding"],
            "evidence_bundle_hash": manifest["evidence_bundle_hash"],
            "evidence_manifest": manifest, "revisions": [{"annotator": name,
                "method": "human", "revision_id": f"rev-{name}", "annotation": {"x": name}}],
        }]})
    with pytest.raises(ValueError, match="missing its content digest"):
        merge_runtime_exports(exports, list("ABC"))
    for export in exports:
        export["export_sha256"] = _digest({"tasks": export["tasks"]})
    merged = merge_runtime_exports(exports, list("ABC"))
    assert [row["annotator"] for row in merged["tasks"][0]["revisions"]] == list("ABC")
    _validate_merged_runtime(merged)
    tampered = copy.deepcopy(merged)
    tampered["tasks"][0]["revisions"][0]["annotation"]["x"] = "tampered"
    with pytest.raises(ValueError, match="digest mismatch"):
        _validate_merged_runtime(tampered)
    exports[2]["tasks"][0]["evidence_bundle_hash"] = "b" * 64
    exports[2]["export_sha256"] = _digest({"tasks": exports[2]["tasks"]})
    with pytest.raises(ValueError, match="evidence binding mismatch"):
        merge_runtime_exports(exports, list("ABC"))


def test_scene_package_contains_only_sanitized_materials_and_detects_tampering(tmp_path):
    import json
    dataset = tmp_path / "dataset"
    (dataset / "scenarios").mkdir(parents=True)
    (dataset / "scenarios" / "s.json").write_text(json.dumps({
        "id": "scene-a", "mode": "single_agent", "description": "task",
        "agents": [], "category": "secret", "label": "attack", "attack_type": "A",
    }))
    (dataset / "roles").mkdir()
    (dataset / "fixtures" / "ops" / "f").mkdir(parents=True)
    (dataset / "bench" / "scene-a").mkdir(parents=True)
    (dataset / "docs").mkdir()
    (dataset / "docs" / "codebook.md").write_text("codebook")
    output = tmp_path / "package"
    build_scene_package(dataset, output, codebook_version="v1")
    _, catalog, _ = load_scene_package(output)
    assert "secret" not in json.dumps(catalog)
    (output / "codebook.md").write_text("changed")
    with pytest.raises(ValueError, match="codebook digest"):
        load_scene_package(output)


def test_experiment_reference_exporters_require_unanimous_labels():
    from tools.annotator.offline import _digest

    merged = {"schema": "mtagentlens.annotation-export/v2", "annotators": list("ABC"),
              "tasks": [{"task_id": "task-1", "submissions": [
                  {"final": {"annotation": {"identities": {"unit-1": "actor-1"},
                                               "roots": {"actors": ["actor-1"]}}}}
                  for _ in range(3)]}]}
    merged["export_sha256"] = _digest({"tasks": merged["tasks"]})
    identity = export_identity_reference(merged, {
        "schema": "mtagentlens.identity-export-map/v1",
        "cases": [{"task_id": "task-1", "case_id": "case-1", "scenario_id": "scene-1",
                   "run_id": "run-1", "units": {"unit-1": {"pid": 7,
                   "process_start_time_ns": 11, "tier": "declared_root"}}}],
    })
    assert identity["cases"]["case-1"]["units"]["unit-1"]["actor"] == "actor-1"
    assert identity["cases"]["case-1"]["units"]["unit-1"]["tier"] == "declared_root"
    roots = export_root_reference(merged, {
        "schema": "mtagentlens.root-export-map/v1",
        "scenarios": [{"task_id": "task-1", "scenario_id": "scene-1", "label": "attack"}],
    })
    assert roots["scenarios"]["scene-1"] == {"root_agent": "actor-1", "label": "attack"}
    merged["tasks"][0]["submissions"][2]["final"]["annotation"]["identities"]["unit-1"] = "other"
    merged["export_sha256"] = _digest({"tasks": merged["tasks"]})
    with pytest.raises(ValueError, match="disagreement"):
        export_identity_reference(merged, {"schema": "mtagentlens.identity-export-map/v1",
            "cases": [{"task_id": "task-1", "case_id": "case-1", "scenario_id": "scene-1",
                       "run_id": "run-1", "units": {"unit-1": {}}}]})


def test_merged_runtime_chains_can_be_staged_for_adjudication(tmp_path):
    spec = fixture_inputs(tmp_path)
    bundle_root = tmp_path / "source-bundle"
    build_bundle(bundle_root, **spec)
    exports = []
    for name in "ABC":
        store = RecordStore(tmp_path / f"{name}.sqlite")
        task_id = store.register_bundle(bundle_root)
        annotation = {"attempted": {"value": "yes", "evidence_refs": ["event-1"]},
                      "executed": {"value": "yes", "evidence_refs": ["event-1"]},
                      "succeeded": {"value": "unknown", "evidence_refs": []},
                      "identities": {"event-1": "actor-a"},
                      "roots": {"actors": ["actor-a"], "evidence_refs": ["event-1"]},
                      "notes": "reviewed"}
        store.save(task_id, name, annotation, method="human", expected_revision=None)
        exports.append(store.export())
    merged = merge_runtime_exports(exports, list("ABC"))
    release = tmp_path / "release" / "cases" / "case-1" / "bundle"
    import shutil
    shutil.copytree(bundle_root, release)
    result = stage_runtime_adjudication(merged, tmp_path / "release", tmp_path / "review.sqlite")
    assert result["tasks"] == 1
    assert len(RecordStore(tmp_path / "review.sqlite").export()["tasks"][0]["revisions"]) == 3


def test_scene_exports_stage_for_review_and_emit_adjudicated_ground_truth(tmp_path):
    from tools.annotator.offline import (
        export_scene_ground_truth,
        publish_scene_ground_truth,
        stage_scene_adjudication,
    )
    from tools.annotator.scene_catalog import load_scenarios
    from tools.annotator.scene_records import SceneRecordStore
    from tools.annotator.tests.test_scene_records import annotation
    from tools.annotator.tests.test_scene_catalog import build_dataset

    dataset = build_dataset(tmp_path)
    catalog = load_scenarios(dataset)
    codebook = dataset / "docs" / "codebook.md"
    codebook.parent.mkdir(parents=True, exist_ok=True)
    codebook.write_text("FuxiBench codebook test fixture\n", encoding="utf-8")
    codebook_version = "fuxibench-2type/1"
    exports = []
    for rater in ("刘周康", "唐浩钺", "刘锦样"):
        store = SceneRecordStore(tmp_path / f"{rater}.sqlite", catalog, [rater],
                                 codebook_version, codebook_path=codebook)
        store.submit("scene-a", rater, annotation())
        exports.append(store.export())

    from tools.annotator.offline import merge_scene_exports
    merged = merge_scene_exports(exports, ["刘周康", "唐浩钺", "刘锦样"])
    tampered = copy.deepcopy(merged)
    tampered["scenarios"][0]["rater_chains"][0]["revisions"][-1]["annotation"][
        "task_summary"
    ] = "edited"
    with pytest.raises(ValueError, match="content digest mismatch"):
        stage_scene_adjudication(tampered, catalog, codebook_version, codebook,
                                 tmp_path / "tampered-review.sqlite")
    review_path = tmp_path / "review.sqlite"
    stage_scene_adjudication(merged, catalog, codebook_version, codebook, review_path)
    review = SceneRecordStore.open(review_path)
    with pytest.raises(ValueError, match="without collector output"):
        export_scene_ground_truth(
            review.export(), catalog, {"scene-a": "credential_theft"},
            codebook_path=codebook, reviewed_at="2026-11-06", blind_review_attested=False,
        )
    with pytest.raises(ValueError, match="adjudication"):
        export_scene_ground_truth(
            review.export(), catalog, {"scene-a": "credential_theft"},
            codebook_path=codebook, reviewed_at="2026-11-06", blind_review_attested=True,
        )
    review.adjudicate("scene-a", "刘周康", annotation(), "Reviewed against the codebook.")

    result = export_scene_ground_truth(
        review.export(), catalog, {"scene-a": "credential_theft"},
        codebook_path=codebook, reviewed_at="2026-11-06", blind_review_attested=True,
    )
    item = result["files"][0]
    assert item["path"] == "credential_theft/scene-a.json"
    assert item["data"]["schema"] == "mtagentlens.ground_truth.v4"
    assert item["data"]["annotation"]["human_annotators"] == [
        "刘周康", "唐浩钺", "刘锦样"
    ]
    assert item["data"]["quality"]["review_status"] == "completed"
    published = tmp_path / "gt-release"
    publish_scene_ground_truth(result, published)
    assert (published / item["path"]).is_file()
    with pytest.raises(ValueError, match="already exists"):
        publish_scene_ground_truth(result, published)
