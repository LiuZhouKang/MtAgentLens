# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Third-party consumer check for an annotation dataset release.

This script deliberately imports **nothing from `tools.annotator`**: it re-derives
the `release_id`, re-hashes the byte inventory, validates every annotation row
against the shipped JSON Schema, and re-checks each case bundle. Its purpose is
the acceptance criterion "an external tool can consume the release" — if this
script passes, a reviewer with only the release directory can verify it.

Exit codes: 0 = release verified; 1 = problems found (all are listed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
import sys
from collections import Counter
from pathlib import Path

SCHEMA = "mtagentlens.annotation-dataset/1"
RELEASE_KEYS = {
    "schema", "release_id", "codebook_version", "cases", "splits", "files",
    "counts", "paper_metrics_available", "publication_blockers", "note",
}
UNMANIFESTED = {"release.json", "reviewers.json"}
METHODS = {"controlled_execution", "human", "model_proposal"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_release_id(release: dict) -> list[str]:
    problems = []
    body = {key: value for key, value in release.items() if key != "release_id"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    if digest != release.get("release_id"):
        problems.append(f"release_id mismatch: manifest={release.get('release_id')} computed={digest}")
    return problems


def verify_inventory(root: Path, release: dict) -> list[str]:
    problems = []
    files = release.get("files") or {}
    if not isinstance(files, dict) or not files:
        return ["files inventory is missing"]
    for relative, expected in files.items():
        path = root / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts or not path.resolve().is_relative_to(root.resolve()):
            problems.append(f"escaping inventory path: {relative}")
            continue
        if not path.is_file():
            problems.append(f"missing file: {relative}")
            continue
        actual = _sha256(path)
        if actual != expected:
            problems.append(f"hash mismatch: {relative}")
    for path in root.rglob("*"):
        if path.is_symlink():
            problems.append(f"release contains a symlink: {path.relative_to(root)}")
    present = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    extra = sorted(present - set(files) - UNMANIFESTED)
    if extra:
        problems.append(f"unmanifested files present: {extra}")
    return problems


def verify_cases(root: Path, release: dict) -> list[str]:
    problems = []
    seen = set()
    scenario_splits = {}
    for index, case in enumerate(release.get("cases") or [], 1):
        case_id = case.get("case_id")
        if case_id != f"case-{index:04d}" or case_id in seen:
            problems.append("case ids must be sequential and unique")
            continue
        seen.add(case_id)
        scenario_id = (case.get("binding") or {}).get("scenario_id")
        if scenario_id:
            split = case.get("split")
            if scenario_id in scenario_splits and scenario_splits[scenario_id] != split:
                problems.append(f"scenario {scenario_id}: cases cross dev/test splits")
            scenario_splits[scenario_id] = split
        case_path = root / "cases" / str(case_id) / "case.json"
        if not case_path.is_file():
            problems.append(f"{case_id}: case.json missing")
        elif json.loads(case_path.read_text(encoding="utf-8")) != case:
            problems.append(f"{case_id}: case.json disagrees with release manifest")
        bundle_dir = root / "cases" / str(case_id) / "bundle"
        manifest_path = bundle_dir / "bundle.json"
        if not manifest_path.is_file():
            problems.append(f"{case_id}: bundle.json missing")
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            problems.append(f"{case_id}: bundle.json invalid: {error}")
            continue
        schema = manifest.get("schema")
        binding = manifest.get("binding")
        if not isinstance(binding, dict) or "data_release_id" not in binding:
            problems.append(f"{case_id}: bundle binding must explicitly declare data_release_id")
        identity = binding.get("data_release_id") if isinstance(binding, dict) else None
        if schema == "mtagentlens.evidence-bundle/v1":
            if not isinstance(identity, str) or re.fullmatch(r"[0-9a-f]{64}", identity) is None:
                problems.append(f"{case_id}: historical bundle lacks a valid release identity")
        elif schema == "mtagentlens.evidence-bundle/v2":
            required = {"data_release_id", "scenario_id", "run_id", "source_run_uuid", "codebook_version"}
            if (not isinstance(binding, dict) or set(binding) != required or
                    any(not isinstance(binding.get(key), str) or not binding[key].strip()
                        for key in required - {"data_release_id"})):
                problems.append(f"{case_id}: unbound bundle binding lacks its run identities")
            else:
                try:
                    if str(uuid.UUID(binding["source_run_uuid"])) != binding["source_run_uuid"]:
                        raise ValueError("noncanonical UUID")
                except ValueError:
                    problems.append(f"{case_id}: unbound bundle binding has an invalid source UUID")
            if identity is not None:
                problems.append(f"{case_id}: unbound bundle claims a release identity")
        else:
            problems.append(f"{case_id}: unsupported evidence bundle schema")
        frozen = {key: value for key, value in manifest.items()
                  if key != "evidence_bundle_hash"}
        encoded = json.dumps(frozen, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        if digest != manifest.get("evidence_bundle_hash"):
            problems.append(f"{case_id}: bundle hash does not match its content")
        if manifest.get("evidence_bundle_hash") != case.get("task_id"):
            problems.append(f"{case_id}: bundle hash is not bound to the case task id")
        if binding != case.get("binding"):
            problems.append(f"{case_id}: bundle binding differs from case")
        events = manifest.get("events", [])
        counts = Counter(event["source_id"] for event in events)
        for field, expected in (("events_count", len(events)),
                                ("actors_count", len(manifest.get("actors", []))),
                                ("evidence_counts", dict(counts))):
            if field in case and case[field] != expected:
                problems.append(f"{case_id}: {field} differs from bundle")
        for event in events:
            source = manifest.get("sources", {}).get(event.get("source_id"))
            if source is None:
                problems.append(f"{case_id}: event anchor references unknown source")
                continue
            source_path = bundle_dir / source["path"]
            if not source_path.resolve().is_relative_to(bundle_dir.resolve()) or not source_path.is_file():
                problems.append(f"{case_id}: event anchor source is not contained")
                continue
            offset, length = event.get("byte_offset"), event.get("byte_length")
            if type(offset) is not int or type(length) is not int or offset < 0 or length <= 0:
                problems.append(f"{case_id}: invalid event anchor range")
                continue
            with source_path.open("rb") as stream:
                stream.seek(offset)
                content = stream.read(length)
            if len(content) != length or hashlib.sha256(content).hexdigest() != event.get("record_sha256"):
                problems.append(f"{case_id}: event anchor hash mismatch")
        for name, source in (manifest.get("sources") or {}).items():
            source_path = bundle_dir / source["path"]
            if not source_path.resolve().is_relative_to(bundle_dir.resolve()):
                problems.append(f"{case_id}: source {name} escapes bundle")
                continue
            if not source_path.is_file():
                problems.append(f"{case_id}: source {name} missing")
            elif _sha256(source_path) != source.get("sha256"):
                problems.append(f"{case_id}: source {name} hash mismatch")
    return problems


def load_annotations(root: Path) -> tuple[list[dict], list[str]]:
    problems = []
    path = root / "annotations.jsonl"
    if not path.is_file():
        return [], ["annotations.jsonl missing"]
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            problems.append(f"annotations.jsonl:{number}: {error}")
            continue
        if not isinstance(payload, dict):
            problems.append(f"annotations.jsonl:{number}: row must be an object")
            continue
        rows.append(payload)
    return rows, problems


def verify_annotations(root: Path, release: dict, rows: list[dict]) -> list[str]:
    problems = []
    schema_path = root / "schema" / "annotation.schema.json"
    if not schema_path.is_file():
        return ["schema/annotation.schema.json missing"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - the environment ships it
        return ["jsonschema not installed; cannot validate annotation rows"]
    known_cases = {case.get("case_id"): case for case in release.get("cases") or []}
    revisions = set()
    heads = {}
    row_keys = {"case_id", "task_id", "annotator", "method", "revision_id",
                "parent_revision_id", "saved_at", "codebook_version", "annotation",
                "is_model_proposal"}
    for row in rows:
        # The shipped schema documents the *annotation payload*; the JSONL adds
        # the revision envelope, whose key set is fixed by the format spec.
        if not isinstance(row, dict):
            problems.append("annotation row must be an object")
            continue
        if not row_keys <= set(row) or set(row) - (row_keys | {"review"}):
            problems.append(
                f"row {row.get('revision_id')}: unexpected envelope keys "
                f"{sorted(set(row) ^ row_keys)}")
            continue
        try:
            jsonschema.validate(row["annotation"], schema)
        except jsonschema.ValidationError as error:
            problems.append(f"row {row.get('revision_id')}: schema violation: {error.message}")
            continue
        if row.get("case_id") not in known_cases:
            problems.append(f"row {row.get('revision_id')}: unknown case id")
        case = known_cases.get(row.get("case_id"))
        if case is not None:
            bundle_path = root / "cases" / case["case_id"] / "bundle" / "bundle.json"
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            events = {event["event_id"] for event in bundle.get("events", [])}
            actors = set(bundle.get("actors", []))
            annotation = row["annotation"]
            for field in ("attempted", "executed", "succeeded", "roots"):
                references = (annotation.get(field) or {}).get("evidence_refs", [])
                if not set(references) <= events:
                    problems.append(f"row {row.get('revision_id')}: dangling evidence reference")
            identities = annotation.get("identities")
            if identities is not None and (set(identities) != events or
                    any(actor is not None and actor not in actors for actor in identities.values())):
                problems.append(f"row {row.get('revision_id')}: invalid identity reference coverage")
            roots = (annotation.get("roots") or {}).get("actors")
            if roots is not None and not set(roots) <= actors:
                problems.append(f"row {row.get('revision_id')}: dangling root actor reference")
        if case is not None and row.get("task_id") != case.get("task_id"):
            problems.append(f"row {row.get('revision_id')}: task does not match its case")
        revision = row.get("revision_id")
        if not isinstance(revision, str) or not revision.strip() or revision in revisions:
            problems.append("duplicate or invalid revision id")
        else:
            revisions.add(revision)
        annotator = row.get("annotator")
        if not isinstance(annotator, str) or not annotator.strip():
            problems.append("annotator must be nonempty text")
            continue
        key = (row.get("case_id"), annotator)
        if row.get("parent_revision_id") != heads.get(key):
            problems.append(f"row {revision}: broken parent revision chain")
        heads[key] = revision
        if "review" in row and not isinstance(row["review"], dict):
            problems.append(f"row {revision}: review must be an object")
        if row.get("codebook_version") != release.get("codebook_version"):
            problems.append(f"row {row.get('revision_id')}: codebook version mismatch")
        method = row.get("method")
        if method not in METHODS:
            problems.append(f"row {row.get('revision_id')}: unknown method {method!r}")
        if not isinstance(row.get("is_model_proposal"), bool) or row["is_model_proposal"] != (method == "model_proposal"):
            problems.append(f"row {row.get('revision_id')}: proposal flag inconsistent with method")
    return problems


def verify_counts(release: dict, rows: list[dict]) -> list[str]:
    problems = []
    counts = release.get("counts") or {}
    methods = Counter(str(row.get("method")) for row in rows)
    annotators = {row.get("annotator") for row in rows}
    expectations = {
        "annotations": len(rows),
        "annotators": len(annotators),
        "methods": {method: methods.get(method, 0) for method in sorted(METHODS)},
        "model_proposals": methods.get("model_proposal", 0),
        "human_annotations": methods.get("human", 0),
        "cases": len(release.get("cases") or []),
    }
    for key, expected in expectations.items():
        if counts.get(key) != expected:
            problems.append(f"counts.{key}: manifest={counts.get(key)} recomputed={expected}")
    splits = Counter(case.get("split") for case in release.get("cases") or [])
    declared_splits = sorted(set(splits) | set((counts.get("splits") or {})))
    recomputed_splits = {key: splits.get(key, 0) for key in declared_splits}
    if counts.get("splits") != recomputed_splits:
        problems.append(
            f"counts.splits mismatch: {counts.get('splits')} vs {recomputed_splits}")
    # The top-level split map is the per-case source of truth; both must agree.
    split_map = release.get("splits") or {}
    if not isinstance(split_map, dict):
        problems.append("splits must map case_id -> split")
    else:
        for case in release.get("cases") or []:
            case_id = case.get("case_id")
            if split_map.get(case_id) != case.get("split"):
                problems.append(f"{case_id}: split map disagrees with case record")
    # Honesty: without a single human annotation the release cannot claim metrics.
    if release.get("paper_metrics_available") is not False:
        problems.append("paper_metrics_available must be false: this release does not authenticate human gold")
    return problems


def verify_attestations(root: Path, release: dict, rows: list[dict]) -> list[str]:
    path = root / "attestations.json"
    if not path.is_file():
        return ["attestations.json missing"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    records = payload.get("attestations") or []
    count = len(records)
    revisions = {row.get("revision_id"): row for row in rows}
    seen = set()
    for record in records:
        proposal = revisions.get(record.get("proposal_revision_id"))
        if proposal is None or proposal.get("method") != "model_proposal":
            problems.append("attestation references an unknown or non-model proposal")
            continue
        if record.get("reviewer") == proposal.get("annotator"):
            problems.append("attestation permits self-review")
        if not record.get("reviewer") or not record.get("statement"):
            problems.append("attestation needs reviewer and statement")
        if record.get("task_id") != proposal.get("task_id") or record.get("evidence_bundle_hash") != proposal.get("task_id"):
            problems.append("attestation task or evidence hash differs from proposal")
        if record.get("codebook_version") != release.get("codebook_version"):
            problems.append("attestation codebook mismatch")
        if record.get("paper_metrics_available") is not False:
            problems.append("attestation cannot make paper metrics available")
        identifier = record.get("attestation_id")
        if not identifier or identifier in seen:
            problems.append("attestation id missing or duplicated")
        seen.add(identifier)
    if (release.get("counts") or {}).get("attestations") != count:
        problems.append(f"attestations count mismatch: manifest={(release.get('counts') or {}).get('attestations')} file={count}")
    if payload.get("paper_metrics_available") is not False:
        problems.append("attestations cannot make paper metrics available")
    return problems


def verify_release(root: Path) -> dict:
    problems: list[str] = []
    if root.is_symlink() or not root.is_dir():
        return {"root": str(root), "problems": ["release root must be a regular directory"]}
    if (root / "release.json").is_symlink():
        return {"root": str(root), "problems": ["release.json must not be a symlink"]}
    release_path = root / "release.json"
    if not release_path.is_file():
        return {"root": str(root), "problems": ["release.json missing"]}
    release = json.loads(release_path.read_text(encoding="utf-8"))
    if release.get("schema") != SCHEMA:
        problems.append(f"unexpected schema {release.get('schema')!r}")
    missing = RELEASE_KEYS - set(release)
    if missing:
        problems.append(f"manifest missing keys: {sorted(missing)}")
    extra = set(release) - RELEASE_KEYS
    if extra:
        problems.append(f"manifest has unknown keys: {sorted(extra)}")
    problems.extend(verify_release_id(release))
    problems.extend(verify_inventory(root, release))
    problems.extend(verify_cases(root, release))
    rows, row_problems = load_annotations(root)
    problems.extend(row_problems)
    problems.extend(verify_annotations(root, release, rows))
    problems.extend(verify_counts(release, rows))
    problems.extend(verify_attestations(root, release, rows))
    reviewers_path = root / "reviewers.json"
    if not reviewers_path.is_file():
        problems.append("reviewers.json missing")
    else:
        reviewers = json.loads(reviewers_path.read_text(encoding="utf-8"))
        if reviewers.get("release_id") != release.get("release_id"):
            problems.append("reviewers.json is not bound to this release id")
        if reviewers.get("paper_metrics_available") is not False:
            problems.append("reviewers.json cannot make paper metrics available")
        if not isinstance(reviewers.get("reviewers"), list):
            problems.append("reviewers.json reviewers must be a list")
    return {
        "root": str(root),
        "release_id": release.get("release_id"),
        "codebook_version": release.get("codebook_version"),
        "cases": len(release.get("cases") or []),
        "annotations": len(rows),
        "paper_metrics_available": release.get("paper_metrics_available"),
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True,
                        help="release directory to verify")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    try:
        report = verify_release(args.root)
    except (OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"release: {report.get('release_id')}")
        print(f"root: {report['root']}")
        print(f"cases={report.get('cases')} annotations={report.get('annotations')} "
              f"paper_metrics_available={report.get('paper_metrics_available')}")
        for problem in report["problems"]:
            print(f"  PROBLEM: {problem}")
        print("VERIFIED for external consumption" if not report["problems"]
              else f"{len(report['problems'])} problem(s)")
    return 0 if not report["problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
