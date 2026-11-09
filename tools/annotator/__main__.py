"""Local evidence bundle and revision commands, separate from the legacy UI."""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from tools.annotator.evidence import _loads_strict, build_bundle
from tools.annotator.records import RecordStore
from tools.annotator.runner_import import import_runner_bundle


def _publish_export(path: Path, payload: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".annotation-export-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Publish complete bytes atomically; link refuses an existing target.
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="freeze declared raw sources against a validated release")
    build.add_argument("--spec", required=True, type=Path)
    build.add_argument("--out", required=True, type=Path)
    native = commands.add_parser("import-runner", help="freeze native process-launch evidence")
    native.add_argument("--ledger", required=True, type=Path)
    native.add_argument("--release", required=True, type=Path)
    native.add_argument("--codebook-version", required=True)
    native.add_argument("--collector-db", type=Path, default=None,
                        help="finalized collector SQLite snapshot (default: sibling collector/finalized.db)")
    native.add_argument("--out", required=True, type=Path)
    register = commands.add_parser("register", help="add a bundle to the complete annotation queue")
    register.add_argument("--bundle", required=True, type=Path)
    save = commands.add_parser("save", help="append a revision with a checked parent")
    save.add_argument("--task", required=True)
    save.add_argument("--annotator", required=True)
    save.add_argument("--method", required=True, choices=("human", "controlled_execution", "model_proposal"))
    save.add_argument("--annotation", required=True, type=Path)
    save.add_argument("--expected-revision", help="required for updates; omit only for the first revision")
    export = commands.add_parser("export", help="export all tasks and revisions without overwriting a file")
    export.add_argument("--out", required=True, type=Path)
    serve = commands.add_parser("serve", help="open a fixed-rater local annotation workbench")
    serve.add_argument("--rater", required=True)
    serve.add_argument("--method", required=True, choices=("human", "controlled_execution", "model_proposal"))
    serve.add_argument("--mode", choices=("annotate", "adjudicate", "review", "studio"), default="annotate")
    serve.add_argument("--reviewer", action="append", default=[])
    serve.add_argument("--port", type=int, default=7411)
    serve.add_argument("--codebook-version", default=None,
                       help="reject tasks bound to another annotation codebook")
    audit = commands.add_parser("audit",
        help="report evidence sufficiency for independent annotation (no labels or kappa)")
    audit.add_argument("--codebook-version", default=None)
    audit.add_argument("--out", type=Path, default=None)
    attest = commands.add_parser("attest",
        help="record a typed human review statement for the latest model proposal")
    attest.add_argument("--task", required=True)
    attest.add_argument("--reviewer", required=True)
    attest.add_argument("--proposal-revision", required=True)
    statement = attest.add_mutually_exclusive_group(required=True)
    statement.add_argument("--statement")
    statement.add_argument("--statement-file", type=Path)
    attestations = commands.add_parser("attestations",
        help="list recorded human review statements (not labels)")
    attestations.add_argument("--out", type=Path, default=None)
    for command in (attest, attestations, audit):
        command.add_argument("--store", required=True, type=Path)
    evidence = commands.add_parser("task-evidence",
        help="export one task's blinded evidence for proposal drafting")
    evidence.add_argument("--task", required=True)
    evidence.add_argument("--limit", type=int, default=None)
    evidence.add_argument("--out", type=Path, default=None)
    proposal = commands.add_parser("propose",
        help="save a validated model-proposal revision (never a human label)")
    proposal.add_argument("--task", required=True)
    proposal.add_argument("--annotator", required=True)
    proposal.add_argument("--annotation", required=True, type=Path)
    proposal.add_argument("--codebook-version", default=None)
    proposal.add_argument("--expected-revision", default=None)
    for command in (evidence, proposal):
        command.add_argument("--store", required=True, type=Path)
    pilot = commands.add_parser("pilot", help="prepare a pilot store from verified native ledgers")
    pilot.add_argument("--release", required=True, type=Path)
    pilot.add_argument("--ledger", required=True, action="append", type=Path,
                       help="native runner ledger (repeatable)")
    pilot.add_argument("--codebook-version", required=True)
    pilot.add_argument("--bundle-root", type=Path, default=None)
    migrate = commands.add_parser(
        "migrate-codebook", help="re-version a store and its bundles under another codebook")
    migrate.add_argument("--out-store", required=True, type=Path)
    migrate.add_argument("--bundles-out", required=True, type=Path)
    migrate.add_argument("--release", required=True, type=Path)
    migrate.add_argument("--codebook-version", required=True)
    migrate.add_argument("--manifest-out", type=Path, default=None)
    migrate.add_argument("--note", default=None,
                         help="why this migration was performed; stored in the manifest")
    migrate.add_argument("--carry-human", action="store_true",
                         help="explicitly carry human/controlled-execution revisions (default: refuse)")
    release = commands.add_parser(
        "release", help="freeze one store into an immutable annotation dataset release")
    release.add_argument("--out", required=True, type=Path)
    release.add_argument("--splits", type=Path, default=None,
                         help="JSON mapping of task id (or case alias) to dev/test")
    release.add_argument("--reviewers", type=Path, default=None,
                         help="reviewer responsibility sidecar to bind into the release")
    release.add_argument("--note", default=None)
    release_verify = commands.add_parser(
        "release-verify", help="verify an annotation dataset release byte for byte")
    release_verify.add_argument("--root", required=True, type=Path)
    for command in (register, save, export, serve, pilot, migrate, release):
        command.add_argument("--store", required=True, type=Path)
    scene_serve = commands.add_parser(
        "scene-serve", help="open the isolated FuxiBench scene annotation workbench")
    scene_source = scene_serve.add_mutually_exclusive_group(required=True)
    scene_source.add_argument("--dataset", type=Path)
    scene_source.add_argument("--package", type=Path)
    scene_serve.add_argument("--store", required=True, type=Path)
    scene_serve.add_argument("--rater", required=True, action="append",
                             help="configured rater name (repeat for all three)")
    scene_serve.add_argument("--codebook-version", default=None)
    scene_serve.add_argument("--active-rater",
                             help="fixed local annotator identity; prevents switching names in the UI")
    scene_serve.add_argument("--host", default="127.0.0.1")
    scene_serve.add_argument("--port", type=int, default=7412)
    scene_pack = commands.add_parser(
        "scene-pack", help="freeze sanitized FuxiBench materials for offline raters")
    scene_pack.add_argument("--dataset", required=True, type=Path)
    scene_pack.add_argument("--codebook-version", default="fuxibench-2type/1")
    scene_pack.add_argument("--out", required=True, type=Path)
    scene_export = commands.add_parser(
        "scene-export", help="export scene annotation chains without overwriting")
    scene_export.add_argument("--store", required=True, type=Path)
    scene_export.add_argument("--out", required=True, type=Path)
    scene_merge = commands.add_parser(
        "scene-merge", help="merge three isolated scene exports after verifying their bindings")
    scene_merge.add_argument("--input", required=True, action="append", type=Path)
    scene_merge.add_argument("--rater", required=True, action="append")
    scene_merge.add_argument("--out", required=True, type=Path)
    scene_stage = commands.add_parser(
        "scene-stage-adjudication", help="prepare a three-rater scene store for final review")
    scene_stage.add_argument("--merged", required=True, type=Path)
    scene_source = scene_stage.add_mutually_exclusive_group(required=True)
    scene_source.add_argument("--dataset", type=Path)
    scene_source.add_argument("--package", type=Path)
    scene_stage.add_argument("--store", required=True, type=Path)
    scene_gt = commands.add_parser(
        "scene-gt-export", help="publish schema-validated, adjudicated FuxiBench GT files")
    scene_gt.add_argument("--merged", required=True, type=Path)
    scene_gt_source = scene_gt.add_mutually_exclusive_group(required=True)
    scene_gt_source.add_argument("--dataset", type=Path)
    scene_gt_source.add_argument("--package", type=Path)
    scene_gt.add_argument("--categories", required=True, type=Path,
                          help="JSON mapping with schema and scenario_id-to-category values")
    scene_gt.add_argument("--reviewed-at", required=True,
                          help="human review date in YYYY-MM-DD format")
    scene_gt.add_argument("--confirm-blind-review", action="store_true",
                          help="attest that reviewers did not consult collector output")
    scene_gt.add_argument("--out", required=True, type=Path)
    runtime_merge = commands.add_parser(
        "runtime-merge", help="merge isolated one-annotator runtime exports")
    runtime_merge.add_argument("--input", required=True, action="append", type=Path)
    runtime_merge.add_argument("--rater", required=True, action="append")
    runtime_merge.add_argument("--out", required=True, type=Path)
    runtime_stage = commands.add_parser(
        "runtime-stage-adjudication", help="load merged human chains into a review store")
    runtime_stage.add_argument("--merged", required=True, type=Path)
    runtime_stage.add_argument("--release", required=True, type=Path)
    runtime_stage.add_argument("--store", required=True, type=Path)
    identity_export = commands.add_parser(
        "identity-export", help="validate and export a merged identity reference")
    identity_export.add_argument("--merged", required=True, type=Path)
    identity_export.add_argument("--mapping", required=True, type=Path)
    identity_export.add_argument("--out", required=True, type=Path)
    root_export = commands.add_parser(
        "root-export", help="validate and export a merged scenario-root reference")
    root_export.add_argument("--merged", required=True, type=Path)
    root_export.add_argument("--mapping", required=True, type=Path)
    root_export.add_argument("--out", required=True, type=Path)
    unified = commands.add_parser(
        "unified-serve", help="open scene and runtime annotation in one tabbed workbench")
    unified_source = unified.add_mutually_exclusive_group(required=True)
    unified_source.add_argument("--dataset", type=Path)
    unified_source.add_argument("--package", type=Path)
    unified.add_argument("--scene-store", required=True, type=Path)
    unified.add_argument("--runtime-store", required=True, type=Path)
    unified.add_argument("--scene-rater", required=True, action="append",
                         help="scene workspace identity (one local annotator or all three for adjudication)")
    unified.add_argument("--active-scene-rater",
                         help="fixed scene identity for a one-rater offline workspace")
    unified.add_argument("--runtime-rater", required=True)
    unified.add_argument("--runtime-mode", choices=("annotate", "adjudicate"), default="annotate")
    unified.add_argument("--reviewer", action="append", default=[])
    unified.add_argument("--scene-codebook-version", default=None)
    unified.add_argument("--runtime-codebook-version", default=None)
    unified.add_argument("--port", type=int, default=7420)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            spec = _loads_strict(args.spec.read_text(encoding="utf-8"))
            fields = {"release_root", "binding", "sources", "actors", "events"}
            if not isinstance(spec, dict) or set(spec) != fields:
                raise ValueError("spec must contain release_root, binding, sources, actors and events")
            base = args.spec.resolve().parent
            if not isinstance(spec["release_root"], str):
                raise ValueError("release_root must be a path string")
            spec["release_root"] = base / spec["release_root"]
            if not isinstance(spec["sources"], dict):
                raise ValueError("sources must be a mapping")
            for source in spec["sources"].values():
                if not isinstance(source, dict) or not isinstance(source.get("path"), str):
                    raise ValueError("source path must be a path string")
                source["path"] = base / source["path"]
            result = {"evidence_bundle_hash": build_bundle(args.out, **spec)["evidence_bundle_hash"]}
        elif args.command == "import-runner":
            result = {"evidence_bundle_hash": import_runner_bundle(
                args.out, release_root=args.release, ledger=args.ledger,
                codebook_version=args.codebook_version,
                collector_db=args.collector_db)["evidence_bundle_hash"]}
        elif args.command == "audit":
            if not args.store.is_file():
                raise ValueError("record store does not exist; register a bundle first")
            from tools.annotator.audit import audit_store, write_audit
            result = audit_store(args.store, codebook_version=args.codebook_version)
            if args.out is not None:
                write_audit(args.out, result)
                result = {**result["summary"], "out": str(args.out)}
        elif args.command in ("attest", "attestations"):
            if not args.store.is_file():
                raise ValueError("record store does not exist; register a bundle first")
            store = RecordStore(args.store)
            if args.command == "attest":
                statement = (args.statement if args.statement is not None
                             else args.statement_file.read_text(encoding="utf-8"))
                result = store.save_attestation(args.task, args.reviewer,
                                                args.proposal_revision,
                                                statement=statement)
            else:
                result = {"schema": "mtagentlens.review-attestations/v1",
                          "attestations": store.list_attestations(),
                          "paper_metrics_available": False}
                if args.out is not None:
                    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
                    args.out.write_text(payload, encoding="utf-8")
                    result = {"attestations": len(result["attestations"]),
                              "out": str(args.out)}
        elif args.command in ("task-evidence", "propose"):
            if not args.store.is_file():
                raise ValueError("record store does not exist; register a bundle first")
            from tools.annotator.propose import export_task, save_proposal, write_task_export
            store = RecordStore(args.store)
            if args.command == "task-evidence":
                result = export_task(store, args.task, limit=args.limit)
                if args.out is not None:
                    write_task_export(args.out, result)
                    result = {"task_id": result["task_id"], "events": len(result["events"]),
                              "out": str(args.out)}
            else:
                value = _loads_strict(args.annotation.read_text(encoding="utf-8"))
                result = save_proposal(store, args.task, value, annotator=args.annotator,
                                       codebook_version=args.codebook_version,
                                       expected_revision=args.expected_revision)
        elif args.command == "pilot":
            from tools.annotator.pilot import prepare_pilot
            result = prepare_pilot(args.store, args.release, args.ledger,
                                   args.codebook_version, bundle_root=args.bundle_root)
        elif args.command == "migrate-codebook":
            from tools.annotator.migrate import migrate_store, write_migration_manifest
            manifest = migrate_store(source_store=args.store, out_store=args.out_store,
                                     out_bundles=args.bundles_out,
                                     codebook_version=args.codebook_version,
                                     release_root=args.release, note=args.note,
                                     carry_human=args.carry_human)
            if args.manifest_out is not None:
                write_migration_manifest(args.manifest_out, manifest)
            result = {"codebook_version_from": manifest["codebook_version_from"],
                      "codebook_version_to": manifest["codebook_version_to"],
                      "counts": manifest["counts"],
                      "revision_map": {entry["old_task_id"]: entry["new_task_id"]
                                       for entry in manifest["tasks"]},
                      "manifest_out": str(args.manifest_out) if args.manifest_out else None}
        elif args.command == "release":
            from tools.annotator.release import build_annotation_release
            splits = None
            if args.splits is not None:
                splits = _loads_strict(args.splits.read_text(encoding="utf-8"))
                if not isinstance(splits, dict) or not all(
                        isinstance(key, str) and isinstance(value, str)
                        for key, value in splits.items()):
                    raise ValueError("splits must map task ids or case aliases to dev/test")
            frozen = build_annotation_release(args.store, args.out, splits=splits,
                                              reviewers=args.reviewers, note=args.note)
            result = {"release_id": frozen["release_id"],
                      "cases": len(frozen["cases"]),
                      "splits": frozen["splits"],
                      "codebook_version": frozen["codebook_version"],
                      "paper_metrics_available": frozen["paper_metrics_available"],
                      "out": str(args.out)}
        elif args.command == "release-verify":
            from tools.annotator.release import verify_annotation_release
            verified = verify_annotation_release(args.root)
            result = {"release_id": verified["release_id"],
                      "cases": len(verified["cases"]),
                      "codebook_version": verified["codebook_version"],
                      "paper_metrics_available": verified["paper_metrics_available"]}
        elif args.command == "scene-export":
            from tools.annotator.scene_records import SceneRecordStore
            store = SceneRecordStore.open(args.store)
            payload = store.export()
            _publish_export(args.out, payload)
            result = {
                "scenarios": len(payload["scenarios"]),
                "raters": payload["raters"],
                "codebook_version": payload["codebook_version"],
                "out": str(args.out),
            }
        elif args.command == "scene-pack":
            from tools.annotator.offline import build_scene_package
            manifest = build_scene_package(args.dataset, args.out,
                                           codebook_version=args.codebook_version)
            result = {**manifest, "out": str(args.out)}
        elif args.command == "scene-merge":
            from tools.annotator.offline import merge_scene_exports
            if len(args.input) != 3 or len(args.rater) != 3:
                raise ValueError("scene-merge requires exactly three --input and three --rater values")
            exports = [_loads_strict(path.read_text(encoding="utf-8")) for path in args.input]
            payload = merge_scene_exports(exports, args.rater)
            _publish_export(args.out, payload)
            result = {"scenarios": len(payload["scenarios"]),
                      "raters": payload["raters"], "out": str(args.out)}
        elif args.command == "scene-stage-adjudication":
            from tools.annotator.offline import load_scene_package, stage_scene_adjudication
            from tools.annotator.scene_catalog import load_scenarios
            merged = _loads_strict(args.merged.read_text(encoding="utf-8"))
            if args.package is not None:
                manifest, catalog, codebook_path = load_scene_package(args.package)
                codebook_version = manifest["codebook_version"]
            else:
                catalog = load_scenarios(args.dataset)
                codebook_path = args.dataset / "docs" / "codebook.md"
                codebook_version = "fuxibench-2type/1"
            result = stage_scene_adjudication(merged, catalog, codebook_version,
                                              codebook_path, args.store)
        elif args.command == "scene-gt-export":
            from tools.annotator.offline import (
                export_scene_ground_truth,
                load_scene_package,
                publish_scene_ground_truth,
            )
            from tools.annotator.scene_catalog import load_scenarios
            merged = _loads_strict(args.merged.read_text(encoding="utf-8"))
            mapping = _loads_strict(args.categories.read_text(encoding="utf-8"))
            if (not isinstance(mapping, dict)
                    or mapping.get("schema") != "mtagentlens.fuxibench-gt-category-map/v1"
                    or not isinstance(mapping.get("categories"), dict)):
                raise ValueError("unsupported FuxiBench ground-truth category mapping")
            if args.package is not None:
                manifest, catalog, codebook_path = load_scene_package(args.package)
                if manifest["codebook_version"] != merged.get("codebook_version"):
                    raise ValueError("scene package codebook version differs from annotations")
            else:
                catalog = load_scenarios(args.dataset)
                codebook_path = args.dataset / "docs" / "codebook.md"
            release = export_scene_ground_truth(
                merged, catalog, mapping["categories"], codebook_path=codebook_path,
                reviewed_at=args.reviewed_at,
                blind_review_attested=args.confirm_blind_review,
            )
            result = publish_scene_ground_truth(release, args.out)
        elif args.command == "runtime-merge":
            from tools.annotator.offline import merge_runtime_exports
            if len(args.input) != 3 or len(args.rater) != 3:
                raise ValueError("runtime-merge requires exactly three --input and three --rater values")
            exports = [_loads_strict(path.read_text(encoding="utf-8")) for path in args.input]
            payload = merge_runtime_exports(exports, args.rater)
            _publish_export(args.out, payload)
            result = {"tasks": len(payload["tasks"]),
                      "raters": payload["annotators"], "out": str(args.out)}
        elif args.command == "runtime-stage-adjudication":
            from tools.annotator.offline import stage_runtime_adjudication
            merged = _loads_strict(args.merged.read_text(encoding="utf-8"))
            result = stage_runtime_adjudication(merged, args.release, args.store)
        elif args.command in ("identity-export", "root-export"):
            from tools.annotator.offline import export_identity_reference, export_root_reference
            merged = _loads_strict(args.merged.read_text(encoding="utf-8"))
            mapping = _loads_strict(args.mapping.read_text(encoding="utf-8"))
            payload = (export_identity_reference(merged, mapping)
                       if args.command == "identity-export"
                       else export_root_reference(merged, mapping))
            _publish_export(args.out, payload)
            result = {"records": len(payload.get("cases", payload.get("scenarios", {}))),
                      "schema": payload["schema"], "out": str(args.out)}
        elif args.command == "scene-serve":
            from tools.annotator.offline import load_scene_package
            from tools.annotator.scene_catalog import load_scenarios
            from tools.annotator.scene_records import SceneRecordStore
            from tools.annotator.scene_server import create_scene_server
            if args.package is not None:
                manifest, catalog, codebook_path = load_scene_package(args.package)
                codebook_version = args.codebook_version or manifest["codebook_version"]
                if codebook_version != manifest["codebook_version"]:
                    raise ValueError("requested codebook version differs from frozen scene package")
            else:
                catalog = load_scenarios(args.dataset)
                codebook_path = args.dataset / "docs" / "codebook.md"
                codebook_version = args.codebook_version or "fuxibench-2type/1"
            if len(args.rater) == 1 and args.active_rater != args.rater[0]:
                raise ValueError("single-rater offline stores must fix --active-rater to their only rater")
            store = SceneRecordStore(
                args.store, catalog, args.rater, codebook_version,
                codebook_path=codebook_path,
            )
            server = create_scene_server(store, catalog, host=args.host, port=args.port,
                                         active_rater=args.active_rater)
            try:
                print(f"http://{args.host}:{server.server_port}/", flush=True)
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        elif args.command == "unified-serve":
            import threading
            from tools.annotator.offline import load_scene_package
            from tools.annotator.scene_catalog import load_scenarios
            from tools.annotator.scene_records import SceneRecordStore
            from tools.annotator.scene_server import create_scene_server
            from tools.annotator.unified_workbench import create_unified_server
            from tools.annotator.workbench import create_server

            if args.package is not None:
                manifest, catalog, codebook_path = load_scene_package(args.package)
                scene_codebook = args.scene_codebook_version or manifest["codebook_version"]
                if scene_codebook != manifest["codebook_version"]:
                    raise ValueError("requested codebook version differs from frozen scene package")
            else:
                catalog = load_scenarios(args.dataset)
                codebook_path = args.dataset / "docs" / "codebook.md"
                scene_codebook = args.scene_codebook_version or "fuxibench-2type/1"

            if len(args.scene_rater) == 1 and args.active_scene_rater != args.scene_rater[0]:
                raise ValueError("single-rater scene workspaces must fix --active-scene-rater")
            if len(args.scene_rater) == 3 and args.active_scene_rater is not None:
                raise ValueError("three-rater scene workspaces do not use --active-scene-rater")
            scene_store = SceneRecordStore(args.scene_store, catalog, args.scene_rater,
                                           scene_codebook, codebook_path=codebook_path)
            unified_server = create_unified_server(port=args.port)
            frame_ancestor = f"http://127.0.0.1:{unified_server.server_port}"
            scene_server = create_scene_server(
                scene_store, catalog, host="127.0.0.1", port=0,
                active_rater=args.active_scene_rater, frame_ancestor=frame_ancestor,
            )
            runtime_server = create_server(
                args.runtime_store, rater=args.runtime_rater, method="human",
                mode=args.runtime_mode, reviewers=args.reviewer,
                port=0, codebook_version=args.runtime_codebook_version,
                frame_ancestor=frame_ancestor,
            )
            unified_server.workbench_urls = {
                "scene": f"http://127.0.0.1:{scene_server.server_port}/",
                "runtime": (f"http://127.0.0.1:{runtime_server.server_port}/"
                            f"#token={runtime_server.token}"),
            }
            servers = (scene_server, runtime_server)
            for child in servers:
                threading.Thread(target=child.serve_forever, daemon=True).start()
            try:
                print(f"http://127.0.0.1:{unified_server.server_port}/", flush=True)
                unified_server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                unified_server.server_close()
                for child in servers:
                    child.shutdown()
                    child.server_close()
            return 0
        elif args.command == "serve":
            from tools.annotator.workbench import create_server
            server = create_server(args.store, rater=args.rater, method=args.method, mode=args.mode,
                                   reviewers=args.reviewer, port=args.port,
                                   codebook_version=args.codebook_version)
            try:
                print(f"http://127.0.0.1:{server.server_port}/#token={server.token}", flush=True)
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        else:
            if args.command in ("save", "export") and not args.store.is_file():
                raise ValueError("record store does not exist; register a bundle first")
            store = RecordStore(args.store)
            if args.command == "register":
                result = {"task_id": store.register_bundle(args.bundle)}
            elif args.command == "save":
                value = _loads_strict(args.annotation.read_text(encoding="utf-8"))
                result = store.save(args.task, args.annotator, value, method=args.method,
                                    expected_revision=args.expected_revision)
            else:
                payload = store.export()
                _publish_export(args.out, payload)
                result = {"tasks": len(payload["tasks"]), "out": str(args.out)}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
