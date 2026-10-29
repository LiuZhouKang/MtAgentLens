"""Assemble the immutable AI-silver reference release; never claim human gold.

The release binds scenario-design annotations, observed-run annotations, family
splits and any auxiliary design artifact into one content-addressed directory.
Its quality tier is explicitly ``ai_silver`` (model proposals, no human review);
``human_gold_available`` is always false and unknown reference states are never
scored as negatives.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

SCHEMA = "mtagentlens.ai-reference-release/1"
PROTOCOL_VERSION = "ai-silver/1"
SCENARIO_SCHEMA = "mtagentlens.ai-scenario-silver/1"
RUNTIME_SCHEMA = "mtagentlens.ai-runtime-silver/v1"
FAMILIES_SCHEMA = "mtagentlens.dataset-families/1"
QUALITY_TIER = "ai_silver"
BINDING_KEYS = ("data_release_id", "scenario_id", "run_id", "source_run_uuid")
CORE_COMPONENTS = ("scenario", "runtime", "families")
BENCHMARK_COHORT = "fuxibench_historical_run"
DIAGNOSTIC_COHORT = "engineering_diagnostic_run"
RUNTIME_COHORTS = {BENCHMARK_COHORT, DIAGNOSTIC_COHORT}
STATES = {"yes", "no", "unknown"}
SPLITS = {"dev", "test"}
COMPONENT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load(path: Path, name: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable JSON component {name}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"component {name} must be a JSON object: {path}")
    return value


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _tree_inventory(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): sha256_file(path)
            for path in sorted(root.rglob("*")) if path.is_file()}


def _inventory(root: Path, *, exclude_manifest: bool = False) -> dict[str, str]:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"release contains a symlink: {path}")
        if path.is_file() and not (exclude_manifest and path == root / "release.json"):
            files[path.relative_to(root).as_posix()] = sha256_file(path)
    return files


def _binding(record: object, *, where: str) -> tuple[str, ...]:
    binding = record.get("binding") if isinstance(record, dict) else None
    if not isinstance(binding, dict):
        raise ValueError(f"{where} has no run binding")
    missing = [key for key in BINDING_KEYS
               if not isinstance(binding.get(key), str) or not binding[key]]
    if missing:
        raise ValueError(f"{where} run binding is incomplete: {missing}")
    return tuple(binding[key] for key in BINDING_KEYS)


def validate_components(scenario: dict, runtime: dict, families: dict, expected_ids) -> dict:
    """Reject human rebranding, duplicate run bindings and family split leakage.

    Runtime records are split by cohort: benchmark runs must bind a scenario in
    scope, while engineering-diagnostic runs may reference older scenarios and
    never contribute to benchmark coverage. A missing cohort means benchmark.
    """
    expected = set(expected_ids)
    if (scenario.get("schema") != SCENARIO_SCHEMA
            or scenario.get("scope") != "scenario_design"
            or scenario.get("eligible_for_runtime_metrics") is not False):
        raise ValueError("invalid scenario scientific scope; AI silver design data is never runtime truth")
    if scenario.get("human_review") is not False:
        raise ValueError("scenario component claims human review")
    records = scenario.get("records")
    if not isinstance(records, list):
        raise ValueError("scenario component has no records")
    if any(isinstance(record, dict) and {"run_id", "runtime_agent_id"} & set(record)
           for record in records):
        raise ValueError("cross-sample reference: design annotation carries runtime identity")
    ids = [record.get("scenario_id") for record in records if isinstance(record, dict)]
    if len(ids) != len(records) or any(not isinstance(x, str) or not x for x in ids):
        raise ValueError("invalid scenario record identity")
    if len(ids) != len(set(ids)) or set(ids) != expected:
        missing, extra = sorted(expected - set(ids)), sorted(set(ids) - expected)
        raise ValueError(f"scenario coverage mismatch: missing {missing}; unexpected {extra}")

    if runtime.get("schema") != RUNTIME_SCHEMA or runtime.get("scope") != "observed_run":
        raise ValueError("invalid runtime scientific scope")
    if runtime.get("human_review") is not False:
        raise ValueError("runtime component claims human review; AI silver evidence is never human gold")
    runtime_records = runtime.get("records")
    if not isinstance(runtime_records, list):
        raise ValueError("runtime component has no records")
    bindings, diagnostic = [], 0
    for index, record in enumerate(runtime_records):
        binding = _binding(record, where=f"runtime record {index}")
        cohort = record.get("cohort", BENCHMARK_COHORT)
        if cohort not in RUNTIME_COHORTS:
            raise ValueError(f"unknown runtime cohort: {cohort!r}")
        if cohort == BENCHMARK_COHORT and binding[1] not in expected:
            raise ValueError("runtime binding references a scenario outside the benchmark scope: "
                             f"{binding[1]}")
        diagnostic += cohort == DIAGNOSTIC_COHORT
        bindings.append(binding)
    if len(bindings) != len(set(bindings)):
        raise ValueError("duplicate run binding in runtime component")

    if families.get("schema") != FAMILIES_SCHEMA:
        raise ValueError("invalid families schema")
    if families.get("human_review") is not False:
        raise ValueError("families component claims human review")
    assignments = families.get("assignments")
    if not isinstance(assignments, dict) or set(assignments) != expected:
        present = set(assignments) if isinstance(assignments, dict) else set()
        raise ValueError(f"family coverage mismatch: missing {sorted(expected - present)}; "
                         f"unexpected {sorted(present - expected)}")
    family_splits: dict[str, set[str]] = {}
    for scenario_id in sorted(expected):
        assignment = assignments[scenario_id]
        family, split = (assignment.get("family_id"), assignment.get("split")) if isinstance(assignment, dict) else (None, None)
        if not isinstance(family, str) or not family or split not in SPLITS:
            raise ValueError(f"invalid split assignment for {scenario_id}")
        family_splits.setdefault(family, set()).add(split)
    leaking = sorted(family for family, splits in family_splits.items() if len(splits) > 1)
    if leaking:
        raise ValueError(f"family split leakage: {leaking}")
    return {
        "scenario_records": len(ids),
        "runtime_records": len(bindings),
        "runtime_benchmark_records": len(bindings) - diagnostic,
        "runtime_diagnostic_records": diagnostic,
        "runtime_diagnostic_scope": ("engineering_diagnostic_run records are released for "
                                     "provenance but never count toward benchmark coverage"),
        "family_assignments": len(assignments),
        "families": len(family_splits),
        "split_counts": {split: sum(1 for a in assignments.values() if a["split"] == split)
                         for split in sorted(SPLITS)},
        "expected_scenario_ids": sorted(expected),
        "reference_quality": QUALITY_TIER,
        "human_review": False,
    }


def evaluate_states(references: list, predictions: list, field: str) -> dict:
    """Score predictions against AI silver states; unknown references never become negatives."""
    reference_by_binding: dict[tuple[str, ...], dict] = {}
    for index, reference in enumerate(references):
        binding = _binding(reference, where=f"reference {index}")
        if binding in reference_by_binding:
            raise ValueError(f"duplicate reference binding: {binding}")
        reference_by_binding[binding] = reference
    predicted: dict[tuple[str, ...], str] = {}
    for index, prediction in enumerate(predictions):
        binding = _binding(prediction, where=f"prediction {index}")
        if binding not in reference_by_binding:
            raise ValueError(f"prediction binding absent from references: {binding}")
        if binding in predicted:
            raise ValueError(f"duplicate prediction binding: {binding}")
        value = prediction.get("value")
        if value not in STATES:
            raise ValueError(f"invalid predicted state: {value!r}")
        predicted[binding] = value

    known = unknown_refs = abstentions = 0
    confusion = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    for binding, reference in reference_by_binding.items():
        annotation = reference.get("annotation") if isinstance(reference, dict) else None
        value = (annotation.get(field) or {}).get("value") if isinstance(annotation, dict) else None
        if value not in STATES:
            raise ValueError(f"invalid reference state for {field!r}: {value!r}")
        if value == "unknown":
            unknown_refs += 1
            continue
        known += 1
        guess = predicted.get(binding, "unknown")
        if guess == "unknown":
            abstentions += 1
        elif value == "yes":
            confusion["tp" if guess == "yes" else "fn"] += 1
        else:
            confusion["tn" if guess == "no" else "fp"] += 1
    answered = sum(confusion.values())
    return {
        "reference_quality": QUALITY_TIER,
        "field": field,
        "total_runs": len(reference_by_binding),
        "reference_known": known,
        "reference_unknown": unknown_refs,
        "prediction_abstentions_on_known": abstentions,
        "confusion": confusion,
        "accuracy_on_binary_known": (confusion["tp"] + confusion["tn"]) / answered if answered else None,
        "binary_coverage_on_known": answered / known if known else None,
    }


def _component_entry(source: Path, target: Path, out_dir: Path) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if any(path.is_symlink() for path in source.rglob("*")):
            raise ValueError(f"component directory contains a symlink: {source}")
        shutil.copytree(source, target,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
        files = _tree_inventory(target)
        if not files:
            raise ValueError(f"component directory is empty: {source}")
        return {"kind": "directory", "path": target.relative_to(out_dir).as_posix(),
                "source": source.as_posix(), "tree_sha256": _digest(files),
                "file_count": len(files)}
    shutil.copy2(source, target)
    return {"kind": "file", "path": target.relative_to(out_dir).as_posix(),
            "source": source.as_posix(), "sha256": sha256_file(target),
            "size": target.stat().st_size}


def build_release(components: dict[str, Path], out_dir: Path, *, expected_ids) -> dict:
    """Freeze validated component bytes; refuse to overwrite an existing release."""
    sources = {name: Path(path) for name, path in components.items()}
    missing = [name for name in CORE_COMPONENTS if name not in sources]
    if missing:
        raise ValueError(f"missing AI reference release components: {missing}")
    for name, path in sources.items():
        if not COMPONENT_NAME.fullmatch(name):
            raise ValueError(f"invalid component name: {name!r}")
        if not path.exists() or (name in CORE_COMPONENTS and not path.is_file()):
            raise ValueError(f"missing component {name}: {path}")
    loaded = {name: _load(sources[name], name) for name in CORE_COMPONENTS}
    validation = validate_components(loaded["scenario"], loaded["runtime"],
                                     loaded["families"], expected_ids=set(expected_ids))

    out_dir = Path(out_dir)
    if out_dir.exists():
        raise ValueError("AI reference release destination already exists; releases are immutable")
    out_dir.mkdir(parents=True)
    entries = {}
    for name in sorted(sources):
        suffix = ".json" if sources[name].is_file() else ""
        target = out_dir / "components" / f"{name}{suffix}"
        entries[name] = _component_entry(sources[name], target, out_dir)

    release = {
        "schema": SCHEMA,
        "protocol_version": PROTOCOL_VERSION,
        "quality_tier": QUALITY_TIER,
        "method": "model_proposal",
        "human_review": False,
        "ai_reference_available": True,
        "human_gold_available": False,
        "scope": {
            "expected_scenario_ids": validation["expected_scenario_ids"],
            "reference_quality": QUALITY_TIER,
            "human_review": False,
        },
        "components": entries,
        "validation": validation,
        "files": _inventory(out_dir),
        "limitations": [
            "AI silver reference: model proposals without human review or a human gold anchor.",
            "Unknown reference states are never scored as negatives or true negatives.",
            "Release identity is content-addressed; any changed, missing or extra file breaks verification.",
        ],
    }
    release["release_id"] = _digest({key: value for key, value in release.items() if key != "release_id"})
    _write(out_dir / "release.json", release)
    return release


def verify_release(root: Path, *, expected_ids=None) -> dict:
    """Reject tampered files, coverage gaps, cross-sample references and human gold claims."""
    root = Path(root)
    manifest_path = root / "release.json"
    if not manifest_path.is_file():
        raise ValueError("missing AI reference release manifest")
    release = _load(manifest_path, "release")
    if release.get("schema") != SCHEMA:
        raise ValueError(f"unsupported AI reference release schema: {release.get('schema')!r}")
    if (release.get("quality_tier") != QUALITY_TIER
            or release.get("human_review") is not False
            or release.get("human_gold_available") is not False
            or release.get("ai_reference_available") is not True):
        raise ValueError("release claims human gold or a non-AI-silver quality tier")
    stored = release.get("files") or {}
    actual = _inventory(root, exclude_manifest=True)
    if actual != stored:
        raise ValueError("AI reference release checksum mismatch: missing={} extra={} changed={}".format(
            sorted(set(stored) - set(actual)), sorted(set(actual) - set(stored)),
            sorted(key for key in set(actual) & set(stored) if actual[key] != stored[key])))
    if _digest({key: value for key, value in release.items() if key != "release_id"}) != release.get("release_id"):
        raise ValueError("AI reference release id mismatch")

    components = release.get("components")
    if not isinstance(components, dict) or not set(CORE_COMPONENTS) <= set(components):
        raise ValueError("AI reference release is missing core components")
    loaded = {}
    for name in sorted(components):
        entry = components[name]
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError(f"invalid release component entry: {name}")
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(root.resolve()) or not path.exists():
            raise ValueError(f"release component escapes or is missing: {name}")
        if entry.get("kind") == "directory":
            files = _tree_inventory(path)
            if _digest(files) != entry.get("tree_sha256") or len(files) != entry.get("file_count"):
                raise ValueError(f"release component tree mismatch: {name}")
        elif entry.get("kind") == "file":
            if (sha256_file(path) != entry.get("sha256") or path.stat().st_size != entry.get("size")
                    or entry["path"] not in stored):
                raise ValueError(f"release component checksum mismatch: {name}")
        else:
            raise ValueError(f"unknown release component kind: {name}")
        if name in CORE_COMPONENTS:
            if entry["kind"] != "file":
                raise ValueError(f"release core component must be a file: {name}")
            loaded[name] = _load(path, name)
    scope_ids = set((release.get("scope") or {}).get("expected_scenario_ids") or [])
    if expected_ids is not None and set(expected_ids) != scope_ids:
        raise ValueError("release scenario coverage differs from external expected ids: "
                         f"{sorted(set(expected_ids) ^ scope_ids)}")
    validate_components(loaded["scenario"], loaded["runtime"], loaded["families"],
                        expected_ids=scope_ids)
    return release


def _manifest_ids(manifest: Path) -> set[str]:
    payload = _load(manifest, "manifest")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"prompt-input manifest has no entries: {manifest}")
    ids = [entry.get("scenario_id") for entry in entries if isinstance(entry, dict)]
    if len(ids) != len(entries) or len(set(ids)) != len(ids):
        raise ValueError(f"prompt-input manifest has invalid scenario ids: {manifest}")
    return set(ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="assemble an immutable AI-silver release")
    build.add_argument("--scenario", type=Path, required=True, help="scenario annotations.json")
    build.add_argument("--runtime", type=Path, required=True, help="runtime annotations.json")
    build.add_argument("--families", type=Path, required=True, help="dataset families.json")
    build.add_argument("--component", action="append", default=[], metavar="NAME=PATH",
                       help="extra component file or directory (e.g. causal_design)")
    build.add_argument("--manifest", type=Path,
                       help="prompt-input manifest anchoring the expected scenario ids")
    build.add_argument("--out", type=Path, required=True, help="new release directory")
    verify = commands.add_parser("verify", help="verify an assembled release")
    verify.add_argument("--release", type=Path, required=True, help="release directory")
    verify.add_argument("--manifest", type=Path,
                        help="prompt-input manifest to re-check scenario coverage")
    args = parser.parse_args(argv)

    try:
        if args.command == "build":
            components = {"scenario": args.scenario, "runtime": args.runtime,
                          "families": args.families}
            for spec in args.component:
                name, separator, raw = spec.partition("=")
                if not separator or not name or not raw:
                    parser.error(f"--component expects NAME=PATH, got {spec!r}")
                components[name] = Path(raw)
            expected = (_manifest_ids(args.manifest) if args.manifest
                        else {record["scenario_id"]
                              for record in _load(args.scenario, "scenario").get("records", [])})
            release = build_release(components, args.out, expected_ids=expected)
            print(json.dumps({"release_id": release["release_id"], "out": str(args.out),
                              "components": sorted(release["components"]),
                              "validation": release["validation"]}, ensure_ascii=False))
            return 0
        expected = _manifest_ids(args.manifest) if args.manifest else None
        release = verify_release(args.release, expected_ids=expected)
        print(json.dumps({"valid": True, "release_id": release["release_id"],
                          "validation": release["validation"]}, ensure_ascii=False))
        return 0
    except ValueError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
