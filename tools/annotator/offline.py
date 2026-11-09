"""Content-addressed offline annotation packages and deterministic merging."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from datetime import date

PACK_SCHEMA = "mtagentlens.offline-annotation-pack/v1"
WORKSPACE_SCHEMA = "mtagentlens.offline-annotation-workspace/v1"
MERGED_SCHEMA = "mtagentlens.offline-annotation-merge/v1"
SCENE_EXPORT_SCHEMA = "mtagentlens.fuxibench-scene-export/v1"
RUNTIME_EXPORT_SCHEMA = "mtagentlens.annotation-export/v2"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_scene_package(dataset_root, out, *, codebook_version: str) -> dict:
    """Freeze only sanitized scenario materials and the codebook for local raters."""
    from pathlib import Path
    from tools.annotator.scene_catalog import load_scenarios

    dataset_root, out = Path(dataset_root), Path(out)
    if out.exists():
        raise ValueError(f"package destination already exists: {out}")
    catalog = load_scenarios(dataset_root)
    codebook_path = dataset_root / "docs" / "codebook.md"
    codebook = codebook_path.read_bytes()
    body = {"schema": "mtagentlens.fuxibench-scene-package/v1",
            "codebook_version": codebook_version,
            "catalog_sha256": _digest(catalog),
            "codebook_sha256": hashlib.sha256(codebook).hexdigest(),
            "scenario_count": len(catalog)}
    manifest = {**body, "package_sha256": _digest(body)}
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".scene-package-", dir=out.parent))
    try:
        (temp / "catalog.json").write_bytes(_canonical(catalog) + b"\n")
        (temp / "codebook.md").write_bytes(codebook)
        (temp / "manifest.json").write_bytes(_canonical(manifest) + b"\n")
        os.replace(temp, out)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return manifest


def load_scene_package(path):
    from pathlib import Path
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    body = {key: value for key, value in manifest.items() if key != "package_sha256"}
    if manifest.get("schema") != "mtagentlens.fuxibench-scene-package/v1" or _digest(body) != manifest.get("package_sha256"):
        raise ValueError("scene package manifest digest mismatch")
    catalog = json.loads((path / "catalog.json").read_text(encoding="utf-8"))
    codebook = (path / "codebook.md").read_bytes()
    if _digest(catalog) != manifest.get("catalog_sha256"):
        raise ValueError("scene package catalog digest mismatch")
    if hashlib.sha256(codebook).hexdigest() != manifest.get("codebook_sha256"):
        raise ValueError("scene package codebook digest mismatch")
    if len(catalog) != manifest.get("scenario_count"):
        raise ValueError("scene package scenario count mismatch")
    return manifest, catalog, path / "codebook.md"


def build_task_pack(*, task_type: str, schema_version: str, codebook_version: str,
                    raters: list[str], tasks: list[dict], sources: dict[str, bytes]) -> dict:
    if not all(isinstance(value, str) and value.strip() for value in
               (task_type, schema_version, codebook_version)):
        raise ValueError("task type, schema version and codebook version are required")
    if len(raters) != 3 or len(set(raters)) != 3 or any(not name.strip() for name in raters):
        raise ValueError("exactly three distinct annotators are required")
    ids = [row.get("task_id") for row in tasks if isinstance(row, dict)]
    if len(ids) != len(tasks) or not ids or any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("tasks must be a non-empty list with task_id values")
    if len(set(ids)) != len(ids):
        raise ValueError("task ids must be unique")
    encoded_sources = {}
    for name, raw in sorted(sources.items()):
        if not isinstance(name, str) or not name or name.startswith("/") or ".." in name.split("/"):
            raise ValueError(f"unsafe source path: {name!r}")
        if not isinstance(raw, bytes):
            raise ValueError(f"source content must be bytes: {name}")
        encoded_sources[name] = {"sha256": hashlib.sha256(raw).hexdigest(),
                                 "content": base64.b64encode(raw).decode("ascii")}
    pack = {"schema": PACK_SCHEMA, "task_type": task_type,
            "schema_version": schema_version, "codebook_version": codebook_version,
            "raters": list(raters), "tasks": sorted(tasks, key=lambda row: row["task_id"]),
            "sources": encoded_sources}
    pack["pack_sha256"] = _digest(pack)
    verify_task_pack(pack)
    return pack


def verify_task_pack(pack: object) -> dict:
    if not isinstance(pack, dict) or pack.get("schema") != PACK_SCHEMA:
        raise ValueError("unsupported task pack schema")
    expected = pack.get("pack_sha256")
    body = {key: value for key, value in pack.items() if key != "pack_sha256"}
    if not isinstance(expected, str) or not _SHA256.fullmatch(expected) or _digest(body) != expected:
        raise ValueError("task pack digest mismatch")
    for name, source in pack.get("sources", {}).items():
        try:
            raw = base64.b64decode(source["content"], validate=True)
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"invalid source encoding: {name}") from exc
        if hashlib.sha256(raw).hexdigest() != source.get("sha256"):
            raise ValueError(f"source digest mismatch: {name}")
    return pack


def create_workspace(pack: dict, annotator: str) -> dict:
    verify_task_pack(pack)
    if annotator not in pack["raters"]:
        raise ValueError("workspace identity must match a configured annotator")
    return {"schema": WORKSPACE_SCHEMA, "pack_sha256": pack["pack_sha256"],
            "task_type": pack["task_type"], "schema_version": pack["schema_version"],
            "codebook_version": pack["codebook_version"], "annotator": annotator,
            "tasks": [{"task_id": row["task_id"], "revisions": []} for row in pack["tasks"]]}


def verify_workspace(pack: dict, workspace: object) -> dict:
    verify_task_pack(pack)
    if not isinstance(workspace, dict) or workspace.get("schema") != WORKSPACE_SCHEMA:
        raise ValueError("unsupported annotation workspace schema")
    if workspace.get("pack_sha256") != pack["pack_sha256"]:
        raise ValueError("workspace task pack digest mismatch")
    if workspace.get("annotator") not in pack["raters"]:
        raise ValueError("workspace identity is not configured for this task pack")
    if any(workspace.get(key) != pack[key] for key in
           ("task_type", "schema_version", "codebook_version")):
        raise ValueError("workspace task or codebook binding mismatch")
    expected_ids = [row["task_id"] for row in pack["tasks"]]
    task_rows = workspace.get("tasks")
    if not isinstance(task_rows, list) or [row.get("task_id") for row in task_rows] != expected_ids:
        raise ValueError("workspace task set mismatch")
    revision_ids = set()
    for task in task_rows:
        revisions = task.get("revisions")
        if not isinstance(revisions, list):
            raise ValueError("workspace revisions must be a list")
        parent = None
        for revision in revisions:
            if (not isinstance(revision, dict) or revision.get("task_id") != task["task_id"]
                    or revision.get("annotator") != workspace["annotator"]
                    or not isinstance(revision.get("revision_id"), str)
                    or not revision["revision_id"] or revision["revision_id"] in revision_ids):
                raise ValueError("invalid or duplicate workspace revision identity")
            if revision.get("parent_revision_id") != parent:
                raise ValueError("workspace revision chain is broken")
            if revision.get("kind") not in ("draft", "submission") or "annotation" not in revision:
                raise ValueError("invalid workspace revision payload")
            revision_ids.add(revision["revision_id"])
            parent = revision["revision_id"]
    return workspace


def merge_workspaces(pack: dict, workspaces: list[dict]) -> dict:
    verify_task_pack(pack)
    if len(workspaces) != 3:
        raise ValueError("exactly three workspaces are required")
    verified = [verify_workspace(pack, item) for item in workspaces]
    names = [item["annotator"] for item in verified]
    if len(set(names)) != 3:
        raise ValueError("duplicate annotator workspace")
    if set(names) != set(pack["raters"]):
        raise ValueError("workspace annotators do not match task pack")
    by_name = {item["annotator"]: item for item in verified}
    tasks = []
    for index, task in enumerate(pack["tasks"]):
        submissions = []
        for name in pack["raters"]:
            revisions = by_name[name]["tasks"][index]["revisions"]
            if not revisions or revisions[-1]["kind"] != "submission":
                raise ValueError(f"missing submission: {task['task_id']} / {name}")
            submissions.append({"annotator": name, "revisions": revisions,
                                "final": revisions[-1]})
        tasks.append({"task_id": task["task_id"], "input": task,
                      "submissions": submissions})
    result = {"schema": MERGED_SCHEMA, "created_at": _now(),
              "pack_sha256": pack["pack_sha256"], "task_type": pack["task_type"],
              "schema_version": pack["schema_version"],
              "codebook_version": pack["codebook_version"],
              "annotators": list(pack["raters"]), "tasks": tasks,
              "paper_metrics_available": False}
    result["merge_sha256"] = _digest(result)
    return result


def merge_scene_exports(exports: list[dict], raters: list[str]) -> dict:
    """Merge isolated one-rater scene exports without altering their source records."""
    if len(exports) != 3 or len(raters) != 3 or len(set(raters)) != 3:
        raise ValueError("exactly three exports and three distinct raters are required")
    for export in exports:
        if not isinstance(export, dict) or export.get("schema") != SCENE_EXPORT_SCHEMA:
            raise ValueError("unsupported scene export schema")
        _verify_scene_export_digest(export, required=True)
    binding_keys = ("codebook_version", "codebook_sha256", "codebook_hash_kind", "catalog_sha256")
    base = exports[0]
    for export in exports[1:]:
        if any(export.get(key) != base.get(key) for key in binding_keys):
            raise ValueError("scene exports have mismatched catalog or codebook bindings")
    scenario_maps = [{row["scenario_id"]: row for row in export.get("scenarios", [])}
                     for export in exports]
    expected_ids = set(scenario_maps[0])
    if not expected_ids or any(set(rows) != expected_ids for rows in scenario_maps[1:]):
        raise ValueError("scene exports have different task sets")
    export_raters = []
    for export in exports:
        names = export.get("raters")
        if not isinstance(names, list) or len(names) != 1:
            raise ValueError("each scene export must come from a one-rater workspace")
        export_raters.append(names[0])
    if len(set(export_raters)) != 3 or set(export_raters) != set(raters):
        raise ValueError("scene export annotators do not match configured raters")
    by_rater = {export["raters"][0]: scenarios for export, scenarios in zip(exports, scenario_maps)}
    merged_scenarios = []
    matrices = {field: [] for field in ("label", "attack_type", "min_decision", "should_alert")}
    for scenario_id in sorted(expected_ids):
        rows = [by_rater[name][scenario_id] for name in raters]
        reference = rows[0]
        if any(row.get("input_sha256") != reference.get("input_sha256") or
               row.get("sources") != reference.get("sources") for row in rows[1:]):
            raise ValueError(f"scene input binding mismatch: {scenario_id}")
        chains = []
        annotations = []
        for name, row in zip(raters, rows):
            if row.get("adjudication") or row.get("adjudications"):
                raise ValueError("local workspaces must not contain adjudication records")
            chain = next((item for item in row.get("rater_chains", [])
                          if item.get("rater") == name), None)
            if chain is None or not chain.get("submitted") or not chain.get("revisions"):
                raise ValueError(f"missing submission: {scenario_id} / {name}")
            if any(revision.get("rater") != name or revision.get("scenario_id") != scenario_id
                   for revision in chain["revisions"]):
                raise ValueError(f"scene revision identity mismatch: {scenario_id} / {name}")
            annotations.append(chain["revisions"][-1]["annotation"])
            chains.append(chain)
        for field in matrices:
            matrices[field].append([annotation[field] for annotation in annotations])
        merged_scenarios.append({**reference, "rater_chains": chains,
                                 "adjudication": None, "adjudications": []})
    from tools.annotator.scene_records import fleiss_kappa
    agreement_fields = {}
    for field, ratings in matrices.items():
        metric = fleiss_kappa(ratings)
        if field == "min_decision":
            metric.pop("category_proportions")
            metric.pop("expected_agreement")
            metric.pop("kappa")
            metric["measure"] = "exact agreement for ordinal values"
        else:
            metric["measure"] = "Fleiss nominal agreement"
        agreement_fields[field] = metric
    result = {"schema": SCENE_EXPORT_SCHEMA, "exported_at": _now(),
              "codebook_version": base["codebook_version"],
              "codebook_sha256": base["codebook_sha256"],
              "codebook_hash_kind": base["codebook_hash_kind"],
              "catalog_sha256": base["catalog_sha256"], "raters": list(raters),
              "agreement": {"available": True, "schema": "mtagentlens.scene-agreement/v1",
                            "n_scenarios": len(merged_scenarios), "n_raters": 3,
                            "fields": agreement_fields,
                            "scope": "Calculated from independent pre-adjudication submissions."},
              "paper_metrics_available": False, "scenarios": merged_scenarios}
    result["merge_sha256"] = _digest(result)
    return result


def _verify_scene_export_digest(export: dict, *, required: bool = False) -> None:
    if isinstance(export.get("merge_sha256"), str):
        field = "merge_sha256"
    elif isinstance(export.get("export_sha256"), str):
        field = "export_sha256"
    elif required:
        raise ValueError("scene export is missing its content digest")
    else:
        return
    expected = export[field]
    body = {key: value for key, value in export.items() if key != field}
    if not isinstance(expected, str) or not _SHA256.fullmatch(expected) or _digest(body) != expected:
        raise ValueError("scene export content digest mismatch")


def merge_runtime_exports(exports: list[dict], raters: list[str]) -> dict:
    """Merge one-person runtime exports while preserving evidence bundles and revisions."""
    if len(exports) != 3 or len(raters) != 3 or len(set(raters)) != 3:
        raise ValueError("exactly three exports and three distinct raters are required")
    if any(not isinstance(item, dict) or item.get("schema") != RUNTIME_EXPORT_SCHEMA
           for item in exports):
        raise ValueError("unsupported runtime annotation export schema")
    for export in exports:
        _verify_runtime_export_digest(export, required=True)
    task_maps = [{row["task_id"]: row for row in export.get("tasks", [])}
                 for export in exports]
    task_ids = set(task_maps[0])
    if not task_ids or any(set(mapping) != task_ids for mapping in task_maps[1:]):
        raise ValueError("runtime exports have different task sets")
    export_raters = []
    for mapping in task_maps:
        names = {revision.get("annotator") for task in mapping.values()
                 for revision in task.get("revisions", [])
                 if revision.get("method") == "human"}
        if len(names) != 1 or None in names:
            raise ValueError("each runtime export must contain exactly one human annotator")
        export_raters.append(names.pop())
    if len(set(export_raters)) != 3 or set(export_raters) != set(raters):
        raise ValueError("runtime export annotators do not match configured raters")
    for task_id in task_ids:
        base = task_maps[0][task_id]
        for mapping in task_maps[1:]:
            row = mapping[task_id]
            if (row.get("evidence_bundle_hash") != base.get("evidence_bundle_hash")
                    or row.get("evidence_manifest") != base.get("evidence_manifest")
                    or row.get("binding") != base.get("binding")):
                raise ValueError(f"runtime evidence binding mismatch: {task_id}")
    by_rater = {name: mapping for name, mapping in zip(export_raters, task_maps)}
    tasks = []
    for task_id in sorted(task_ids):
        base = by_rater[raters[0]][task_id]
        revisions = []
        for name in raters:
            task = by_rater[name][task_id]
            human = [row for row in task.get("revisions", []) if row.get("method") == "human"]
            if not human or human[-1].get("annotator") != name:
                raise ValueError(f"missing human submission: {task_id} / {name}")
            if len(human) != len(task.get("revisions", [])):
                raise ValueError("runtime exports containing non-human revisions cannot be merged")
            revisions.extend(human)
        tasks.append({**base, "status": "annotated", "revisions": revisions})
    codebook_versions = {task["binding"].get("codebook_version") for task in tasks}
    if len(codebook_versions) != 1:
        raise ValueError("runtime exports have mismatched codebook versions")
    return {"schema": RUNTIME_EXPORT_SCHEMA, "paper_metrics_available": False,
            "codebook_version": next(iter(codebook_versions)),
            "tasks": tasks, "annotators": list(raters),
            "merge_sha256": _digest({"tasks": tasks, "annotators": raters})}


def export_identity_reference(merged: dict, mapping: dict) -> dict:
    """Project unanimous runtime identity labels onto the IdentityBench schema."""
    _validate_merged_runtime(merged)
    if mapping.get("schema") != "mtagentlens.identity-export-map/v1":
        raise ValueError("unsupported identity export mapping schema")
    tasks = {row["task_id"]: row for row in merged["tasks"]}
    cases = {}
    total = known = 0
    tiers = {"declared_root": 0, "declared_ancestor": 0, "unknown": 0}
    mapping_cases = mapping.get("cases")
    if not isinstance(mapping_cases, list):
        raise ValueError("identity mapping cases must be a list")
    for case in mapping_cases:
        if not isinstance(case, dict):
            raise ValueError("identity mapping entries must be objects")
        task_id = case.get("task_id")
        task = tasks.pop(task_id, None)
        if task is None:
            raise ValueError(f"identity mapping references an unknown or duplicate task: {task_id}")
        units = case.get("units")
        if not isinstance(units, dict) or not units:
            raise ValueError(f"identity mapping requires unit metadata: {task_id}")
        if not isinstance(case.get("case_id"), str) or not case["case_id"].strip():
            raise ValueError(f"identity mapping requires a case_id: {task_id}")
        if any(not isinstance(case.get(key), str) or not case[key].strip()
               for key in ("scenario_id", "run_id")):
            raise ValueError(f"identity mapping requires scenario_id and run_id: {task_id}")
        if case.get("case_id") in cases:
            raise ValueError(f"duplicate identity case id: {case.get('case_id')}")
        annotations = _final_annotations(task)
        identity_maps = [annotation.get("identities") for annotation in annotations]
        if any(not isinstance(item, dict) for item in identity_maps):
            raise ValueError(f"identity labels missing from task: {task_id}")
        if any(item != identity_maps[0] for item in identity_maps[1:]):
            raise ValueError(f"identity disagreement requires explicit adjudication: {task_id}")
        if set(identity_maps[0]) != set(units):
            raise ValueError(f"identity unit set does not match mapping: {task_id}")
        out_units = {}
        for unit_id, metadata in units.items():
            actor = identity_maps[0][unit_id]
            if actor is not None and (not isinstance(actor, str) or not actor.strip()):
                raise ValueError(f"invalid actor label: {task_id}/{unit_id}")
            if not isinstance(metadata, dict):
                raise ValueError(f"invalid unit metadata: {task_id}/{unit_id}")
            tier = metadata.get("tier")
            if tier not in ("declared_root", "declared_ancestor", "unknown"):
                raise ValueError(f"invalid identity tier: {task_id}/{unit_id}")
            if (actor is None) != (tier == "unknown"):
                raise ValueError(f"identity actor and tier conflict: {task_id}/{unit_id}")
            for field in ("pid", "process_start_time_ns"):
                if field not in metadata:
                    raise ValueError(f"missing {field}: {task_id}/{unit_id}")
                value = metadata.get(field)
                if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                    raise ValueError(f"invalid {field}: {task_id}/{unit_id}")
            out_units[unit_id] = {"actor": actor, "tier": tier,
                                  "pid": metadata.get("pid"),
                                  "process_start_time_ns": metadata.get("process_start_time_ns")}
            total += 1
            tiers[tier] += 1
            known += actor is not None
        cases[case["case_id"]] = {"scenario_id": case["scenario_id"],
                                  "run_id": case["run_id"], "units": out_units}
    if tasks:
        raise ValueError(f"identity mapping omits tasks: {len(tasks)}")
    return {"schema": "mtagentlens.identity-reference/1",
            "method": "human_reviewed_identity_partition", "cases": cases,
            "stats": {"units": total, "tiers": tiers,
                      "human_reviewed_units": total},
            "human_annotators": _source_annotators(merged), "human_review_status": "complete"}


def export_root_reference(merged: dict, mapping: dict) -> dict:
    """Project unanimous scenario-root labels onto the AttributionBench schema."""
    _validate_merged_runtime(merged)
    if mapping.get("schema") != "mtagentlens.root-export-map/v1":
        raise ValueError("unsupported root export mapping schema")
    tasks = {row["task_id"]: row for row in merged["tasks"]}
    scenarios = {}
    mapping_scenarios = mapping.get("scenarios")
    if not isinstance(mapping_scenarios, list):
        raise ValueError("root mapping scenarios must be a list")
    for entry in mapping_scenarios:
        if not isinstance(entry, dict):
            raise ValueError("root mapping entries must be objects")
        scenario_id, task_id = entry.get("scenario_id"), entry.get("task_id")
        if not isinstance(scenario_id, str) or scenario_id in scenarios:
            raise ValueError("root mapping has a missing or duplicate scenario_id")
        task = tasks.pop(task_id, None)
        if task is None:
            raise ValueError(f"root mapping references an unknown or duplicate task: {task_id}")
        if entry.get("label") not in ("attack", "benign"):
            raise ValueError(f"invalid scenario label: {scenario_id}")
        annotations = _final_annotations(task)
        roots = [annotation.get("roots", {}).get("actors") for annotation in annotations]
        if any(value != roots[0] for value in roots[1:]):
            raise ValueError(f"root disagreement requires explicit adjudication: {scenario_id}")
        value = roots[0]
        if value is None:
            root_agent = None
        elif isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
            root_agent = value[0]
        else:
            raise ValueError(f"root reference requires one root agent or unknown: {scenario_id}")
        scenarios[scenario_id] = {"root_agent": root_agent, "label": entry["label"]}
    if tasks:
        raise ValueError(f"root mapping omits tasks: {len(tasks)}")
    return {"schema": "mtagentlens.root-reference/1",
            "method": "human_reviewed_root_reference", "scenarios": scenarios,
            "human_annotators": _source_annotators(merged), "human_review_status": "complete"}


def _validate_merged_runtime(merged: dict) -> None:
    if not isinstance(merged, dict) or merged.get("schema") != RUNTIME_EXPORT_SCHEMA:
        raise ValueError("unsupported merged runtime export schema")
    names = _source_annotators(merged)
    if (len(names) != 3 or len(set(names)) != 3
            or any(not isinstance(name, str) or not name.strip() for name in names)):
        raise ValueError("reference export requires three independent human annotators")
    _verify_runtime_export_digest(merged, required=True)


def _verify_runtime_export_digest(export: dict, *, required: bool = False) -> None:
    if isinstance(export.get("merge_sha256"), str):
        digest = _digest({"tasks": export.get("tasks"),
                          "annotators": export.get("annotators")})
        expected = export["merge_sha256"]
    elif isinstance(export.get("export_sha256"), str):
        digest = _digest({"tasks": export.get("tasks")})
        expected = export["export_sha256"]
    elif required:
        raise ValueError("runtime export is missing its content digest")
    else:
        return
    if not isinstance(expected, str) or not _SHA256.fullmatch(expected) or digest != expected:
        raise ValueError("runtime export content digest mismatch")


def _source_annotators(merged: dict) -> list[str]:
    if isinstance(merged.get("annotators"), list):
        return merged["annotators"]
    names = {revision.get("annotator") for task in merged.get("tasks", [])
             for revision in task.get("revisions", [])
             if revision.get("method") == "human" and "review" not in revision}
    return sorted(name for name in names if isinstance(name, str))


def _final_annotations(task: dict) -> list[dict]:
    if isinstance(task.get("submissions"), list):
        return [submission["final"]["annotation"] for submission in task["submissions"]]
    source_revisions = [revision for revision in task.get("revisions", [])
                        if revision.get("method") == "human" and "review" not in revision]
    if len({row.get("annotator") for row in source_revisions}) != 3:
        raise ValueError(f"task requires three independent human annotation chains: {task.get('task_id')}")
    latest = {}
    for row in source_revisions:
        latest[row["annotator"]] = row
    adjudications = [revision for revision in task.get("revisions", [])
                     if revision.get("method") == "human" and "review" in revision]
    if adjudications:
        final = adjudications[-1]
        review = final["review"]
        reviewed = review.get("reviewed_revisions") if isinstance(review, dict) else None
        if (not isinstance(reviewed, dict)
                or not isinstance(review.get("rationale"), str) or not review["rationale"].strip()
                or set(reviewed) != set(latest)
                or any(reviewed[name] != latest[name].get("revision_id") for name in latest)):
            raise ValueError("adjudication does not reference all three source raters")
        return [final["annotation"]]
    annotations = [latest[name]["annotation"] for name in sorted(latest)]
    if any(annotation != annotations[0] for annotation in annotations[1:]):
        raise ValueError(f"disagreement requires adjudication: {task.get('task_id')}")
    return annotations[:1]


def stage_runtime_adjudication(merged: dict, release_root, store_path) -> dict:
    """Rehydrate merged human chains into a fresh local store for adjudication."""
    from pathlib import Path
    from tools.annotator.evidence import load_bundle
    from tools.annotator.records import RecordStore

    _validate_merged_runtime(merged)
    release_root, store_path = Path(release_root), Path(store_path)
    if store_path.exists():
        raise ValueError(f"adjudication store already exists: {store_path}")
    bundles = {}
    for manifest in release_root.rglob("bundle.json"):
        bundle = load_bundle(manifest.parent)
        task_id = bundle["evidence_bundle_hash"]
        if task_id in bundles:
            raise ValueError(f"duplicate evidence bundle in release: {task_id}")
        bundles[task_id] = manifest.parent
    expected_ids = {task["task_id"] for task in merged["tasks"]}
    if set(bundles) != expected_ids:
        raise ValueError("release evidence task set does not match merged annotations")
    store_path.parent.mkdir(parents=True, exist_ok=True)
    temp_store = store_path.with_name(f".{store_path.name}.{os.getpid()}.tmp")
    if temp_store.exists():
        raise ValueError(f"temporary adjudication store already exists: {temp_store}")
    try:
        store = RecordStore(temp_store)
        for task in merged["tasks"]:
            task_id = task["task_id"]
            store.register_bundle(bundles[task_id])
            revisions_by_rater = {}
            for revision in task["revisions"]:
                revisions_by_rater.setdefault(revision["annotator"], []).append(revision)
            if set(revisions_by_rater) != set(merged["annotators"]):
                raise ValueError(f"merged task lacks an annotator chain: {task_id}")
            for annotator in merged["annotators"]:
                store.import_human_revisions(task_id, revisions_by_rater[annotator])
        os.replace(temp_store, store_path)
    except BaseException:
        temp_store.unlink(missing_ok=True)
        temp_store.with_name(temp_store.name + "-wal").unlink(missing_ok=True)
        temp_store.with_name(temp_store.name + "-shm").unlink(missing_ok=True)
        raise
    return {"store": str(store_path), "tasks": len(merged["tasks"]),
            "annotators": list(merged["annotators"]),
            "schema": "mtagentlens.runtime-adjudication-store/v1"}


def stage_scene_adjudication(merged: dict, catalog: list[dict], codebook_version: str,
                             codebook_path, store_path) -> dict:
    """Create an adjudication store from three complete isolated scene exports."""
    from pathlib import Path
    from tools.annotator.scene_records import SceneRecordStore

    if not isinstance(merged, dict) or merged.get("schema") != SCENE_EXPORT_SCHEMA:
        raise ValueError("unsupported merged scene export")
    if len(merged.get("raters", [])) != 3:
        raise ValueError("scene adjudication requires three merged raters")
    store_path = Path(store_path)
    if store_path.exists():
        raise ValueError(f"adjudication store already exists: {store_path}")
    store_path.parent.mkdir(parents=True, exist_ok=True)
    temp_store = store_path.with_name(f".{store_path.name}.{os.getpid()}.tmp")
    if temp_store.exists():
        raise ValueError(f"temporary adjudication store already exists: {temp_store}")
    try:
        store = SceneRecordStore(temp_store, catalog, merged["raters"], codebook_version,
                                 codebook_path=Path(codebook_path))
        imported = store.import_merged_export(merged)
        os.replace(temp_store, store_path)
    except BaseException:
        temp_store.unlink(missing_ok=True)
        temp_store.with_name(temp_store.name + "-wal").unlink(missing_ok=True)
        temp_store.with_name(temp_store.name + "-shm").unlink(missing_ok=True)
        raise
    return {"store": str(store_path), "raters": list(merged["raters"]),
            "revisions": imported, "scenarios": len(merged["scenarios"]),
            "schema": "mtagentlens.scene-adjudication-store/v1"}


def export_scene_ground_truth(merged: dict, catalog: list[dict], categories: dict,
                              *, codebook_path, reviewed_at: str,
                              blind_review_attested: bool) -> dict:
    """Build a schema-compatible FuxiBench GT release from explicitly adjudicated scenes."""
    from pathlib import Path
    from tools.annotator.scene_catalog import catalog_hash, scenario_input_hash
    from tools.annotator.scene_records import EXPORT_SCHEMA, validate_annotation

    if not isinstance(merged, dict) or merged.get("schema") != EXPORT_SCHEMA:
        raise ValueError("unsupported merged scene export")
    _verify_scene_export_digest(merged, required=True)
    raters = merged.get("raters")
    if not isinstance(raters, list) or len(raters) != 3 or len(set(raters)) != 3:
        raise ValueError("ground-truth export requires three distinct human annotators")
    if raters != ["刘周康", "唐浩钺", "刘锦样"]:
        raise ValueError("FuxiBench annotators do not match the configured human team")
    if not blind_review_attested:
        raise ValueError("confirm that scene review was performed without collector output")
    try:
        if not isinstance(reviewed_at, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", reviewed_at):
            raise ValueError("wrong date format")
        date.fromisoformat(reviewed_at)
    except (TypeError, ValueError) as exc:
        raise ValueError("reviewed_at must be an ISO date (YYYY-MM-DD)") from exc
    if not isinstance(catalog, list) or merged.get("catalog_sha256") != catalog_hash(catalog):
        raise ValueError("scene catalog digest does not match merged annotations")
    if hashlib.sha256(Path(codebook_path).read_bytes()).hexdigest() != merged.get("codebook_sha256"):
        raise ValueError("scene codebook digest does not match merged annotations")
    if not isinstance(categories, dict) or set(categories) != {
            item.get("scenario_id") for item in catalog}:
        raise ValueError("category map must cover every catalog scenario exactly once")
    scenario_exports = {item.get("scenario_id"): item for item in merged.get("scenarios", [])}
    catalog_by_id = {item["scenario_id"]: item for item in catalog}
    if set(scenario_exports) != set(catalog_by_id):
        raise ValueError("merged annotation task set does not match scene catalog")

    files = []
    for scenario_id in sorted(catalog_by_id):
        scenario = catalog_by_id[scenario_id]
        source_rows = sorted(
            ({"path": row["path"], "sha256": row["sha256"], "kind": row["kind"]}
             for row in scenario["sources"]), key=lambda row: row["path"]
        )
        result = scenario_exports[scenario_id]
        if (result.get("input_sha256") != scenario_input_hash(scenario["sources"])
                or result.get("sources") != source_rows):
            raise ValueError(f"scene input digest mismatch: {scenario_id}")
        codebook_version = merged.get("codebook_version")
        input_hash = scenario_input_hash(scenario["sources"])
        annotation, adjudication = _adjudicated_scene_annotation(
            result, raters, scenario_id, codebook_version, input_hash
        )
        validate_annotation(annotation, scenario_id=scenario_id, sources=scenario["sources"],
                            codebook_version=codebook_version)
        category = categories[scenario_id]
        if (not isinstance(category, str)
                or not re.fullmatch(r"[a-z][a-z0-9_]*", category)):
            raise ValueError(f"invalid category for {scenario_id}: {category!r}")
        payload = {"schema": "mtagentlens.ground_truth.v4", "scenario_id": scenario_id,
                   "category": category, **annotation,
                   "taxonomy_version": codebook_version,
                   "annotation": {
                       "method": "human_independent_annotation_and_adjudication",
                       "annotation_version": "fuxibench-human-review/1",
                       "human_annotators": raters, "human_annotation": True,
                       "human_review_status": "completed", "reviewed_at": reviewed_at,
                       "codebook_version": codebook_version,
                       "collector_output_consulted": False,
                   },
                   "label_annotation": {
                       "method": "human_independent_annotation_and_adjudication",
                       "annotation_version": "fuxibench-human-review/1",
                       "scope": "scenario_design", "reviewers": raters,
                       "human_review": True, "human_review_status": "completed",
                       "reviewed_at": reviewed_at, "codebook_version": codebook_version,
                   },
                   "quality": {
                       "paper_ready": False, "taxonomy_version": codebook_version,
                       "review_status": "completed", "reviewers": raters,
                       "codebook_version": codebook_version,
                   }}
        files.append({"path": f"{category}/{scenario_id}.json", "data": payload,
                      "input_sha256": input_hash,
                      "source_revision_ids": adjudication["source_revision_ids"],
                      "adjudication_id": adjudication["adjudication_id"],
                      "adjudication_rationale": adjudication["rationale"]})
    return {"schema": "mtagentlens.fuxibench-gt-release/1",
            "codebook_version": merged["codebook_version"], "reviewed_at": reviewed_at,
            "codebook_sha256": merged["codebook_sha256"],
            "catalog_sha256": merged["catalog_sha256"],
            "raters": raters, "scenarios": len(files), "files": files,
            "paper_metrics_available": False}


def publish_scene_ground_truth(release: dict, out) -> dict:
    """Atomically publish a new GT directory; refuse any existing destination."""
    from pathlib import Path

    out = Path(out)
    if out.exists() or out.is_symlink():
        raise ValueError(f"ground-truth destination already exists: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    lock = out.with_name(f".{out.name}.publish-lock")
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError(f"ground-truth destination is being published: {out}") from exc
    temp = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=out.parent))
    try:
        if out.exists() or out.is_symlink():
            raise ValueError(f"ground-truth destination already exists: {out}")
        manifest = {key: value for key, value in release.items() if key != "files"}
        manifest["files"] = []
        for item in release["files"]:
            relative = Path(item["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe ground-truth output path: {relative}")
            content = _canonical(item["data"]) + b"\n"
            target = temp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            provenance = {key: value for key, value in item.items()
                          if key not in ("path", "data")}
            manifest["files"].append({"path": relative.as_posix(),
                                      "sha256": hashlib.sha256(content).hexdigest(),
                                      **provenance})
        (temp / "_annotation-release.json").write_bytes(_canonical(manifest) + b"\n")
        if out.exists() or out.is_symlink():
            raise ValueError(f"ground-truth destination already exists: {out}")
        os.rename(temp, out)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    finally:
        lock.rmdir()
    return {"out": str(out), "scenarios": len(release["files"]),
            "schema": release["schema"], "raters": release["raters"]}


def _adjudicated_scene_annotation(scenario: dict, raters: list[str], scenario_id: str,
                                  codebook_version: str, input_hash: str) -> tuple[dict, dict]:
    chains = scenario.get("rater_chains")
    if (not isinstance(chains, list) or len(chains) != 3
            or {chain.get("rater") for chain in chains} != set(raters)):
        raise ValueError(f"scene rater chains do not match configured raters: {scenario_id}")
    latest_ids = {}
    revision_ids = set()
    for chain in chains:
        revisions = chain.get("revisions")
        if (not chain.get("submitted") or not isinstance(revisions, list) or not revisions
                or revisions[-1].get("kind") != "submission"):
            raise ValueError(f"scene submission is missing: {scenario_id} / {chain.get('rater')}")
        parent = None
        for revision in revisions:
            revision_id = revision.get("revision_id")
            if (revision.get("rater") != chain["rater"]
                    or revision.get("scenario_id") != scenario_id
                    or revision.get("parent_revision_id") != parent
                    or revision.get("codebook_version") != codebook_version
                    or revision.get("input_sha256") != input_hash
                    or revision.get("kind") not in ("draft", "submission")
                    or not isinstance(revision_id, str) or not revision_id
                    or revision_id in revision_ids):
                raise ValueError(f"scene revision chain is invalid: {scenario_id} / {chain['rater']}")
            revision_ids.add(revision_id)
            parent = revision_id
        latest_ids[chain["rater"]] = revisions[-1].get("revision_id")
    adjudications = scenario.get("adjudications")
    if not isinstance(adjudications, list) or not adjudications:
        raise ValueError(f"scene requires an explicit adjudication: {scenario_id}")
    adjudication = adjudications[-1]
    source_revision_ids = adjudication.get("source_revision_ids")
    if (scenario.get("adjudication") != adjudication
            or adjudication.get("scenario_id") != scenario_id
            or adjudication.get("adjudicator") not in raters
            or adjudication.get("codebook_version") != codebook_version
            or adjudication.get("input_sha256") != input_hash
            or not isinstance(source_revision_ids, list)
            or len(source_revision_ids) != 3
            or len(set(source_revision_ids)) != 3
            or set(source_revision_ids) != set(latest_ids.values())
            or not isinstance(adjudication.get("rationale"), str)
            or not adjudication["rationale"].strip()):
        raise ValueError(f"scene adjudication is missing, stale or incomplete: {scenario_id}")
    return adjudication.get("annotation"), adjudication
