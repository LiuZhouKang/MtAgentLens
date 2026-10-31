# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Assemble the self-contained FuxiBench 2.0 package.

Inputs are the frozen v5 release and the migrated two-type GT produced by
build_fuxibench_dataset.py. The output is a single dataset tree with scenarios,
fixtures, bench support, prompt inputs, GT, bundled evidence and sanitized traces.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "local/workpackages/2026-10-26-final-dataset"
RELEASE = WORK / "release/FuxiBench-v5"
TRACES = ROOT / "datasets/FuxiBench/traces"
LIVE_GT = ROOT / "datasets/FuxiBench/evidence/legacy/v4/ground_truth"
PACKAGE = ROOT / "datasets/FuxiBench"
GT_STAGE = PACKAGE / "ground_truth"
SOURCE_PACKAGE = PACKAGE

sys.path.insert(0, str(ROOT))
from scripts.evaluation.build_fuxibench_dataset import OLD_TO_NEW, portable, sha256  # noqa: E402

SANITIZE_PATTERNS = [
    ("/home/lzk/MtAgentLens/", ""),
    ("/home/lzk/MtAgentLens", ""),
    ("/home/lzk", "<home>"),
    ("local/workpackages/2026-10-26-final-dataset/", "evidence/v5/"),
    ("local/validate-tonight/", "evidence/validation/legacy-run3/"),
    ("local/f6/", "evidence/legacy/f6/"),
    ("local/f6", "evidence/legacy/f6"),
    ("local/workpackages/", "evidence/legacy/"),
    ("datasets/FuxiBench/", ""),
    ("compare/Dataset/", "evidence/legacy/datasets/"),
    ("compare/", "evidence/legacy/repo/"),
    ("env/scenarios/fixtures/", "fixtures/"),
    ("env/scenarios/", "scenarios/"),
    ("env/scenarios", "scenarios"),
]


def clean_text(text: str) -> str:
    for old, new in SANITIZE_PATTERNS:
        text = text.replace(old, new)
    return text


def restore_annotation_inputs(package: Path, annotation_map: Path, source_root: Path, *, dry_run: bool = False) -> dict:
    """Restore only files whose original bytes match the accepted annotation source map.

    All source hashes are checked before writing. External destinations in the
    original fixture remain unchanged; reversing an IP replacement is ambiguous.
    """
    data = json.loads(annotation_map.read_text(encoding="utf-8"))
    gt_ids = {json.loads(path.read_text())["scenario_id"]
              for path in (package / "ground_truth").glob("*/*.json")
              if not path.parent.name.startswith("_")}
    case_ids = [case.get("scenario_id") for case in data["cases"]]
    if gt_ids and (set(case_ids) != gt_ids or len(case_ids) != len(gt_ids)):
        raise ValueError("annotation map does not cover each package scenario exactly once")
    planned = {}
    verified = []
    for case in data["cases"]:
        if gt_ids:
            from scripts.evaluation.gt_rebuild import scrub_text
            sid = case["scenario_id"]
            agents_source = next(source for source in case["sources"] if source["kind"] == "agents")
            blind = source_root / agents_source["blind_path"]
            if not blind.resolve().is_relative_to(source_root.resolve()) or sha256(blind) != agents_source["blind_sha256"]:
                raise ValueError(f"annotation task source hash mismatch: {sid}")
            source_scenario = source_root / case["source_scenario_path"]
            if not source_scenario.resolve().is_relative_to(source_root.resolve()):
                raise ValueError(f"annotation scenario path escapes source: {sid}")
            original_agents = json.loads(source_scenario.read_text())["agents"]
            scrubbed = [{"role_file": agent.get("role_file"),
                         "task": scrub_text(agent.get("task", ""), sid, case["anon_case_id"])[0]}
                        for agent in original_agents]
            if scrubbed != json.loads(blind.read_text())["agents"]:
                raise ValueError(f"annotation tasks differ from accepted blind input: {sid}")
            target = package / "scenarios" / f"{sid}.json"
            scenario = json.loads(target.read_text())
            if len(scenario["agents"]) != len(original_agents):
                raise ValueError(f"annotation agent count mismatch: {sid}")
            for agent, original in zip(scenario["agents"], original_agents):
                agent["role_file"] = original.get("role_file")
                agent["task"] = clean_text(original.get("task", ""))
            content = (json.dumps(scenario, ensure_ascii=False, indent=2) + "\n").encode()
            planned[target] = content
            verified.append({"path": target.relative_to(package).as_posix(),
                             "annotation_sha256": agents_source["blind_sha256"],
                             "packaged_sha256": hashlib.sha256(content).hexdigest(),
                             "kind": "scenario_tasks"})
        for source in case["sources"]:
            if source.get("kind") not in {"fixture", "role"}:
                continue
            origin = source["origin"]
            rel = Path(origin["path"])
            if rel.is_absolute() or ".." in rel.parts or rel.parts[:2] != ("env", "scenarios"):
                raise ValueError(f"invalid annotation source path: {rel}")
            original = source_root / rel
            if not original.resolve().is_relative_to(source_root.resolve()):
                raise ValueError(f"annotation source escapes source root: {rel}")
            if not original.is_file() or sha256(original) != origin["sha256"]:
                raise ValueError(f"annotation source hash mismatch: {rel}")
            content = original.read_bytes()
            if original.suffix == ".db":
                with tempfile.TemporaryDirectory(prefix="fuxibench-fixture-db-") as temp:
                    copied = Path(temp) / "fixture.db"
                    copied.write_bytes(content)
                    sanitize_sqlite(copied)
                    vacuumed = Path(temp) / "portable.db"
                    connection = sqlite3.connect(str(copied))
                    try:
                        connection.execute("VACUUM INTO ?", (str(vacuumed),))
                    finally:
                        connection.close()
                    content = vacuumed.read_bytes()
            try:
                content = clean_text(content.decode("utf-8")).encode("utf-8")
            except UnicodeDecodeError:
                pass
            target = package / Path(*rel.parts[2:])
            if not target.resolve().is_relative_to(package.resolve()):
                raise ValueError(f"annotation target escapes package: {target}")
            if target in planned and planned[target] != content:
                raise ValueError(f"conflicting annotation sources: {target}")
            planned[target] = content
            verified.append({"path": target.relative_to(package).as_posix(),
                             "annotation_sha256": origin["sha256"],
                             "packaged_sha256": hashlib.sha256(content).hexdigest(),
                             "kind": source["kind"]})
    restored = []
    for target, content in sorted(planned.items()):
        if not target.exists() or target.read_bytes() != content:
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            restored.append(target.relative_to(package).as_posix())
    report = {"schema": "mtagentlens.annotation-source-verification/1",
              "annotation_map_sha256": sha256(annotation_map),
              "scenario_ids": sorted(gt_ids), "verified_files": len(planned),
              "restored": restored, "files": verified,
              "note": "Original source hashes verified before path-only normalization; IPs unchanged."}
    if not dry_run:
        report_path = package / "evidence/annotation-source-verification.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def clean_value(value):
    if isinstance(value, dict):
        return {k: clean_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_value(v) for v in value]
    if isinstance(value, str):
        return clean_text(value)
    return value


def clean_json_file(path: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    cleaned = clean_value(data)
    if cleaned != data:
        path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_text_file(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    cleaned = clean_text(text)
    if cleaned != text:
        path.write_text(cleaned, encoding="utf-8")


def require_disjoint_source(src: Path, dst: Path) -> None:
    """Validate before any destination mutation; sources may be irreplaceable evidence."""
    if not src.is_dir():
        raise ValueError(f"missing source directory: {src}")
    source, target = src.resolve(), dst.resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError(f"source/destination overlap: {src} -> {dst}")


def copy_clean_tree(src: Path, dst: Path, *, size_limit: int = 2 * 1024 * 1024) -> list[Path]:
    require_disjoint_source(src, dst)
    copied = []
    for path in sorted(src.rglob("*")):
        if path.is_symlink() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        rel = path.relative_to(src)
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(target)
        if path.suffix == ".json":
            clean_json_file(target)
        elif target.stat().st_size <= size_limit:
            clean_text_file(target)
    return copied


def build_scenarios(gt: dict[str, dict]) -> None:
    out = PACKAGE / "scenarios"
    out.mkdir(parents=True, exist_ok=True)
    for path in sorted((RELEASE / "scenarios").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        sid = data.get("id")
        new_type = (gt.get(sid) or {}).get("attack_type")
        if new_type in {"A", "B", "benign"}:
            data["attack_type"] = new_type
        data = clean_value(data)
        script = data.get("pre_run_script")
        if isinstance(script, str) and script.startswith("scenarios/bench/"):
            data["pre_run_script"] = script[len("scenarios/"):]
        (out / path.name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_fixtures_bench() -> None:
    for name in ("fixtures", "bench"):
        require_disjoint_source(RELEASE / name, PACKAGE / name)
    for name in ("fixtures", "bench"):
        dst = PACKAGE / name
        if dst.exists():
            shutil.rmtree(dst)
        copy_clean_tree(RELEASE / name, dst)


def build_prompts() -> None:
    """Render prompt bundles from the exact packaged task and fixture bytes."""
    from compare.scripts.build.build_prompt_input_bundle import _build_entry
    dst = PACKAGE / "prompt_inputs"
    if dst.exists():
        shutil.rmtree(dst)
    entries = []
    by_sid = {}
    for gt_path in (PACKAGE / "ground_truth").glob("*/*.json"):
        if not gt_path.parent.name.startswith("_"):
            gt = json.loads(gt_path.read_text())
            by_sid[gt["scenario_id"]] = gt
    for path in sorted((PACKAGE / "scenarios").glob("*.json")):
        scenario = json.loads(path.read_text())
        sid = scenario["id"]
        entry = _build_entry(scenario_id=sid,
                             scenario={**scenario, "fixtures_dir": str(PACKAGE / "fixtures")},
                             ground_truth=by_sid[sid], source_scenario_path=path,
                             scenarios_root=PACKAGE, dataset_dir=PACKAGE, output_dir=dst)
        entry["source_scenario_path"] = path.relative_to(PACKAGE).as_posix()
        entry["source_scenario_sha256"] = sha256(path)
        bundle_path = dst / entry["bundle_json_path"]
        bundle = json.loads(bundle_path.read_text())
        bundle["source_scenario_path"] = entry["source_scenario_path"]
        bundle["fixture"]["source_fixture_dir"] = "fixtures/" + scenario["fixture_dir"]
        bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        entries.append(entry)
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "manifest.json").write_text(json.dumps({
        "schema": "mtagentlens.prompt_inputs.v1", "total_cases": len(entries),
        "no_ground_truth_leak_guarantee": True, "entries": entries,
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def build_gt(gt: dict[str, dict]) -> None:
    # GT_STAGE is produced by build_fuxibench_dataset.py gt; verify each record is migrated.
    if not (GT_STAGE / "manifest.json").exists() and not any(GT_STAGE.glob("*/*.json")):
        raise SystemExit("missing migrated GT; run build_fuxibench_dataset.py gt first")
    for path in GT_STAGE.rglob("*.json"):
        clean_json_file(path)


def build_evidence() -> None:
    ev = PACKAGE / "evidence"
    bundled = SOURCE_PACKAGE / "evidence"
    if bundled.is_dir():
        require_disjoint_source(bundled, ev)
        if ev.exists():
            shutil.rmtree(ev)
        copy_clean_tree(bundled, ev)
        return
    require_disjoint_source(LIVE_GT, ev)
    if ev.exists():
        shutil.rmtree(ev)
    v5 = ev / "v5"
    v5.mkdir(parents=True)
    for name in ("gt-v5.json", "flip-decisions.json", "FLIP-REPORT.md", "v4-v5-diff.json",
                 "rejected-sessions.json", "summary.json", "legacy-audit.json"):
        src = WORK / "gt/runs/full-93" / name
        if src.exists():
            shutil.copy2(src, v5 / name)
    merge = ev / "merge"
    merge.mkdir()
    for name in ("MERGE-REPORT.json", "MERGE-REPORT.md"):
        src = WORK / "integration/full-93" / name
        if src.exists():
            shutil.copy2(src, merge / name)
            if src.suffix == ".json":
                clean_json_file(merge / name)
    # portable evidence paths in the bundled v5 records
    for path in v5.glob("*.json"):
        clean_json_file(path)
    # legacy v4 GT, byte-preserved, for provenance/hash checks
    legacy = ev / "legacy/v4/ground_truth"
    if legacy.exists():
        shutil.rmtree(legacy)
    shutil.copytree(LIVE_GT, legacy)
    for path in legacy.rglob("*.json"):
        clean_json_file(path)
    # blind sources referenced by label_annotation.evidence
    blind_src = WORK / "gt/runs/full-93/blind_sources"
    if blind_src.exists():
        copy_clean_tree(blind_src, v5 / "blind_sources")
    # validation evidence
    val_src = WORK / "validation"
    if val_src.exists():
        copy_clean_tree(val_src, ev / "validation")
    # PBC
    pbc_src = WORK / "pbc"
    if pbc_src.exists():
        copy_clean_tree(pbc_src, ev / "pbc")


def sanitize_sqlite(path: Path) -> None:
    connection = sqlite3.connect(str(path))
    try:
        tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            columns = [r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')]
            text_columns = []
            for column in columns:
                kind = connection.execute(
                    f'SELECT typeof("{column}") FROM "{table}" WHERE "{column}" IS NOT NULL LIMIT 1'
                ).fetchone()
                if kind is None or kind[0] == "text":
                    text_columns.append(column)
            for column in text_columns:
                for old, new in SANITIZE_PATTERNS:
                    connection.execute(
                        f'UPDATE "{table}" SET "{column}" = replace("{column}", ?, ?) WHERE "{column}" LIKE ?',
                        (old, new, f"%{old}%"),
                    )
        connection.commit()
    finally:
        connection.close()


def build_traces(*, skip: bool = False) -> None:
    dst = PACKAGE / "traces"
    if not skip:
        require_disjoint_source(TRACES, dst)
    if skip:
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "TRACES-NOTICE.md").write_text(
            "# Traces not included in this build\n\nRun the packaging step with traces enabled for the "
            "MtAgentLens replay corpus.\n", encoding="utf-8")
        return
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for path in sorted(TRACES.rglob("*")):
        if path.is_symlink() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(TRACES)
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if path.suffix == ".db":
            sanitize_sqlite(target)
        elif path.suffix in {".json", ".jsonl", ".log", ".txt", ".md"}:
            clean_text_file(target)
        elif target.stat().st_size <= 2 * 1024 * 1024:
            clean_text_file(target)
    (dst / "TRACES-NOTICE.md").write_text(
        "# FuxiBench traces\n\n"
        "These are historical eBPF/collector traces kept for provenance review only. They are **not\n"
        "replayable**: every bundled database predates the current collector schema (no\n"
        "`final_decision`, no `analysis_checkpoints`), so it cannot produce a canonical verdict or\n"
        "feed the current analysis kernel.\n\n"
        "Host paths were replaced with neutral dataset-relative placeholders; fixture credentials are\n"
        "decoys, not real secrets. `metadata.json` declares `traces_replayable: false` and\n"
        "`traces_collected` records the number of databases in the current dataset.\n", encoding="utf-8")


def build_docs_metadata() -> None:
    for name in ("README.md", "DATASET-CARD.md"):
        src = SOURCE_PACKAGE / name
        if src.is_file():
            shutil.copy2(src, PACKAGE / name)
    docs = SOURCE_PACKAGE / "docs"
    if docs.is_dir():
        shutil.copytree(docs, PACKAGE / "docs", dirs_exist_ok=True)
    for path in list(PACKAGE.glob("*.md")) + list((PACKAGE / "docs").rglob("*.md")):
        clean_text_file(path)
    # Data licenses
    (PACKAGE / "LICENSE").write_text(
        "FuxiBench dataset (scenarios, fixtures, ground truth, evidence, sanitized traces) is\n"
        "licensed under Creative Commons Attribution 4.0 International (CC-BY-4.0):\n"
        "https://creativecommons.org/licenses/by/4.0/\n\n"
        "Code in the MtAgentLens repository remains Apache-2.0 OR MIT. Fixture credentials and\n"
        "tokens are decoys for security experiments, not real secrets.\n\n"
        "Full license texts for repository code: docs/licenses/LICENSE-APACHE and\n"
        "docs/licenses/LICENSE-MIT.\n", encoding="utf-8")
    licenses = PACKAGE / "docs/licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "LICENSE-APACHE", licenses / "LICENSE-APACHE")
    shutil.copy2(ROOT / "LICENSE-MIT", licenses / "LICENSE-MIT")
    (PACKAGE / "CITATION.cff").write_text(
        "cff-version: 1.2.0\n"
        "title: FuxiBench 2.0 (MtAgentLens)\n"
        "message: Independent AI-annotated LLM-agent security benchmark; no human labels.\n"
        "type: dataset\n"
        "license: CC-BY-4.0\n"
        "version: 2.0.0\n"
        "date-released: 2026-10-26\n", encoding="utf-8")
    metadata = clean_value(json.loads((SOURCE_PACKAGE / "metadata.json").read_text()))
    records = []
    for path in sorted((PACKAGE / "ground_truth").glob("*/*.json")):
        if not path.parent.name.startswith("_"):
            records.append(json.loads(path.read_text()))
    included = [r for r in records if not (r.get("quality") or {}).get("detection_excluded")]
    attack = [r for r in records if r["label"] == "attack"]
    benign = [r for r in records if r["label"] == "benign"]
    excluded = [r for r in records if (r.get("quality") or {}).get("detection_excluded")]
    metadata.update({
        "dataset_name": "FuxiBench",
        "dataset_version": "2.0.0",
        "taxonomy_version": "fuxibench-2type/1",
        "taxonomy": {"A": "indirect/poisoned instruction (old B)", "B": "multi-agent/handoff (old C)",
                     "benign": "no audited attack"},
        "license": "CC-BY-4.0 (data); Apache-2.0 OR MIT (code)",
        "traces_included": (PACKAGE / "traces").exists() and (PACKAGE / "traces" / "TRACES-NOTICE.md").exists(),
        # Review-only: the bundled databases predate the current schema, so they
        # cannot be replayed or produce a canonical verdict. The counts are
        # recomputed from the files and re-checked by validate_fuxibench_package.
        "traces_replayable": False,
        "traces_collected": len(list((PACKAGE / "traces").rglob("*.db")))
        if (PACKAGE / "traces").exists() else 0,
        "file_layout": "self-contained package; see README.md",
        "version": "2.0.0",
        "ground_truth_version": "fuxibench-2.0",
        "quality": "ai_consensus_3_two_type_flip_adjudicated_human_unverified",
        "scenarios_total": len(records),
        "scenario_ids": sorted(r["scenario_id"] for r in records),
        "scenarios_attack": len(attack),
        "scenarios_benign": len(benign),
        "attack_types": {t: sum(1 for r in attack if r["attack_type"] == t) for t in ("A", "B")},
        "detection_excluded_scenarios": sorted(r["scenario_id"] for r in excluded),
        "detection_included_counts": {"attack": sum(1 for r in included if r["label"] == "attack"),
                                      "benign": sum(1 for r in included if r["label"] == "benign")},
        "detection_included_attack_types": {t: sum(1 for r in included if r["attack_type"] == t) for t in ("A", "B")},
        "human_annotation": False,
        "human_review": False,
    })
    for key in ("annotation", "paper_metrics_available", "publication_blockers", "working_tree_status"):
        metadata.pop(key, None)
    (PACKAGE / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")




def sanitize_package_in_place() -> dict:
    """Rewrite host paths in every text/JSON/SQLite artifact and purge free pages."""
    counts = {"db": 0, "text": 0}
    for path in sorted(PACKAGE.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix == ".db":
            try:
                sanitize_sqlite(path)
                tmp = path.with_suffix(".sanitize.tmp.db")
                connection = sqlite3.connect(str(path))
                try:
                    connection.execute("VACUUM INTO ?", (str(tmp),))
                finally:
                    connection.close()
                tmp.replace(path)
                counts["db"] += 1
            except sqlite3.DatabaseError:
                counts.setdefault("db_skipped", []).append(str(path.relative_to(PACKAGE)))
                tmp = path.with_suffix(".sanitize.tmp.db")
                if tmp.exists():
                    tmp.unlink()
        elif path.suffix in {".json", ".jsonl", ".md", ".txt", ".log", ".yaml", ".yml", ".csv",
                             ".py", ".sh", ".toml", ".cff", ".ini", ".cfg"} or path.stat().st_size <= 2 * 1024 * 1024:
            clean_text_file(path)
            counts["text"] += 1
    return counts

def main() -> int:
    global PACKAGE, RELEASE, TRACES, GT_STAGE, SOURCE_PACKAGE, LIVE_GT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=PACKAGE)
    parser.add_argument("--source-release", type=Path, default=RELEASE)
    parser.add_argument("--source-package", type=Path, default=SOURCE_PACKAGE,
                        help="Existing two-type GT, documentation and bundled evidence")
    parser.add_argument("--source-traces", type=Path, default=TRACES)
    parser.add_argument("--source-legacy-gt", type=Path, default=LIVE_GT)
    parser.add_argument("--annotation-map", type=Path,
                        help="Accepted case-map: verify original fixture/role hashes before restoration")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-traces", action="store_true")
    parser.add_argument("--sanitize-only", action="store_true")
    args = parser.parse_args()
    PACKAGE = args.package.resolve()
    RELEASE = args.source_release.resolve()
    SOURCE_PACKAGE = args.source_package.resolve()
    TRACES = args.source_traces.resolve()
    LIVE_GT = args.source_legacy_gt.resolve()
    GT_STAGE = SOURCE_PACKAGE / "ground_truth"
    if PACKAGE.exists() and not args.force and not args.sanitize_only:
        raise SystemExit(f"{PACKAGE} exists; pass --force after reviewing")
    if args.sanitize_only:
        counts = sanitize_package_in_place()
        print(json.dumps({"sanitized": counts}, indent=2))
        return 0
    # Preflight all required inputs before creating or replacing the output.
    for name in ("scenarios", "fixtures", "bench", "prompt_inputs"):
        if not (RELEASE / name).is_dir():
            raise SystemExit(f"missing source release directory: {RELEASE / name}")
    if not GT_STAGE.is_dir() or not (SOURCE_PACKAGE / "metadata.json").is_file():
        raise SystemExit("missing source-package ground_truth or metadata.json")
    if not (SOURCE_PACKAGE / "evidence").is_dir() and not LIVE_GT.is_dir():
        raise SystemExit("missing bundled evidence or source-legacy-gt")
    if not args.skip_traces and not TRACES.is_dir():
        raise SystemExit(f"missing source traces: {TRACES}")
    destination = PACKAGE
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Build with independent bytes. A failure leaves the existing package untouched.
    with tempfile.TemporaryDirectory(prefix=f".{destination.name}-build-", dir=destination.parent) as temp:
        PACKAGE = Path(temp) / "package"
        PACKAGE.mkdir()
        try:
            shutil.copytree(GT_STAGE, PACKAGE / "ground_truth")
            GT_STAGE = PACKAGE / "ground_truth"
            gt_records = {}
            for path in GT_STAGE.rglob("*.json"):
                if any(part.startswith("_") for part in path.relative_to(GT_STAGE).parts):
                    continue
                data = json.loads(path.read_text())
                if "scenario_id" in data:
                    gt_records[data["scenario_id"]] = data
            if not gt_records:
                raise ValueError("source-package contains no migrated GT records")
            build_gt(gt_records)
            build_scenarios(gt_records)
            build_fixtures_bench()
            build_evidence()
            if args.annotation_map:
                restore_annotation_inputs(PACKAGE, args.annotation_map, args.source_root)
            build_prompts()
            build_traces(skip=args.skip_traces)
            build_docs_metadata()
            from scripts.evaluation import repair_package_provenance as provenance
            previous_root = provenance.PKG
            try:
                provenance.PKG = PACKAGE
                provenance.main()
            finally:
                provenance.PKG = previous_root
            from scripts.evaluation.validate_fuxibench_package import validate
            validation = validate(PACKAGE, require_traces=not args.skip_traces)
            if not validation["valid"]:
                raise ValueError(f"built package failed validation: {validation['problems']}")
            backup = Path(temp) / "previous"
            if destination.exists():
                destination.rename(backup)
            try:
                PACKAGE.rename(destination)
            except BaseException:
                if backup.exists():
                    backup.rename(destination)
                raise
        finally:
            PACKAGE = destination
            GT_STAGE = SOURCE_PACKAGE / "ground_truth"
    print(json.dumps({"package": str(PACKAGE)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
