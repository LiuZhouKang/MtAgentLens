# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Freeze an annotation store into a reproducible, externally consumable dataset.

The release is a content-addressed snapshot: every frozen evidence bundle, every
append-only annotation revision and every honesty declaration is inventoried by
SHA-256 and bound by ``release_id``. Model proposals stay ``model_proposal``;
nothing here promotes a draft to a human label, invents kappa, or claims review.

``release.json`` and ``reviewers.json`` are the only files outside the byte
inventory: ``release.json`` is the manifest itself, while ``reviewers.json`` must
embed ``release_id`` (a deliberate forward reference to this release), which
would otherwise make the digest self-referential. ``verify_annotation_release``
still requires ``reviewers.json`` to exist and to carry the matching
``release_id``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from tools.annotator.evidence import _loads_strict, load_bundle
from tools.annotator.records import RecordStore, _validate_annotation

try:  # jsonschema is available in the project environment; fail loudly if absent.
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - exercised only in stripped environments
    Draft202012Validator = None

SCHEMA = "mtagentlens.annotation-dataset/1"
_REVIEWERS_SCHEMA = "mtagentlens.reviewer-assignment/v1"
_ATTESTATIONS_SCHEMA = "mtagentlens.review-attestation-export/v1"
_ATTESTATION_RECORD_SCHEMA = "mtagentlens.review-attestation/v1"
_EVIDENCE_SCHEMA = "mtagentlens.evidence-bundle/v1"

_METHODS = ("controlled_execution", "human", "model_proposal")
_SPLIT_VALUES = ("dev", "test")

# Files that cannot be hashed into their own manifest (see the module docstring).
_UNMANIFESTED = frozenset({"release.json", "reviewers.json"})

_RELEASE_KEYS = frozenset({
    "schema", "release_id", "codebook_version", "cases", "splits", "files",
    "counts", "paper_metrics_available", "publication_blockers", "note",
})
_CASE_KEYS = frozenset({
    "case_id", "task_id", "binding", "split", "evidence_bundle_hash",
    "evidence_counts", "actors_count", "events_count",
})
_ANNOTATION_ROW_KEYS = frozenset({
    "case_id", "task_id", "annotator", "method", "revision_id",
    "parent_revision_id", "saved_at", "codebook_version", "annotation",
    "is_model_proposal",
})
_ATTESTATIONS_PAYLOAD_KEYS = frozenset({
    "schema", "codebook_version", "attestations", "counts",
    "paper_metrics_available", "note",
})

_ANNOTATION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://mtagentlens.local/schema/annotation.schema.json",
    "title": "mtagentlens.annotation/v2",
    "description": (
        "Structural contract for one annotation payload. Referential integrity "
        "(evidence_refs and identities against the frozen evidence bundle) is "
        "enforced by tools.annotator.release.verify_annotation_release."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": ["attempted", "executed", "succeeded", "identities", "roots", "notes"],
    "properties": {
        "attempted": {"$ref": "#/$defs/judgment"},
        "executed": {"$ref": "#/$defs/judgment"},
        "succeeded": {"$ref": "#/$defs/judgment"},
        "identities": {
            "type": "object",
            "additionalProperties": {
                "oneOf": [{"type": "null"}, {"type": "string", "minLength": 1}],
            },
        },
        "roots": {
            "type": "object",
            "additionalProperties": False,
            "required": ["actors", "evidence_refs"],
            "properties": {
                "actors": {
                    "oneOf": [{"type": "null"}, {"$ref": "#/$defs/actors"}],
                },
                "evidence_refs": {"$ref": "#/$defs/evidence_refs"},
            },
        },
        "notes": {"type": "string"},
    },
    "allOf": [
        # Known judgments must cite at least one evidence record.
        {
            "if": {"properties": {"attempted": {"properties": {
                "value": {"not": {"const": "unknown"}}}}}},
            "then": {"properties": {"attempted": {"properties": {
                "evidence_refs": {"minItems": 1}}}}},
        },
        {
            "if": {"properties": {"executed": {"properties": {
                "value": {"not": {"const": "unknown"}}}}}},
            "then": {"properties": {"executed": {"properties": {
                "evidence_refs": {"minItems": 1}}}}},
        },
        {
            "if": {"properties": {"succeeded": {"properties": {
                "value": {"not": {"const": "unknown"}}}}}},
            "then": {"properties": {"succeeded": {"properties": {
                "evidence_refs": {"minItems": 1}}}}},
        },
        # Monotone chain attempted >= executed >= succeeded (yes > unknown > no).
        {
            "if": {"properties": {"attempted": {"properties": {
                "value": {"const": "no"}}}}},
            "then": {"properties": {
                "executed": {"properties": {"value": {"const": "no"}}},
                "succeeded": {"properties": {"value": {"const": "no"}}},
            }},
        },
        {
            "if": {"properties": {"attempted": {"properties": {
                "value": {"const": "unknown"}}}}},
            "then": {"properties": {
                "executed": {"properties": {"value": {"enum": ["no", "unknown"]}}},
                "succeeded": {"properties": {"value": {"enum": ["no", "unknown"]}}},
            }},
        },
        {
            "if": {"properties": {"executed": {"properties": {
                "value": {"const": "no"}}}}},
            "then": {"properties": {"succeeded": {"properties": {
                "value": {"const": "no"}}}}},
        },
        {
            "if": {"properties": {"executed": {"properties": {
                "value": {"const": "unknown"}}}}},
            "then": {"properties": {"succeeded": {"properties": {
                "value": {"enum": ["no", "unknown"]}}}}},
        },
        # A non-null root claim must cite evidence.
        {
            "if": {"properties": {"roots": {"properties": {
                "actors": {"type": "array"}}}}},
            "then": {"properties": {"roots": {"properties": {
                "evidence_refs": {"minItems": 1}}}}},
        },
        # attempted=no is a positive "nothing happened" claim: it requires the empty
        # root set (with evidence), and attempted=yes forbids that empty set.
        {
            "if": {"properties": {"attempted": {"properties": {
                "value": {"const": "no"}}}}},
            "then": {"properties": {"roots": {"properties": {
                "actors": {"type": "array", "maxItems": 0},
                "evidence_refs": {"minItems": 1},
            }}}},
        },
        {
            "if": {"properties": {"attempted": {"properties": {
                "value": {"const": "yes"}}}}},
            "then": {"properties": {"roots": {"properties": {
                "actors": {"oneOf": [
                    {"type": "null"},
                    {"$ref": "#/$defs/actors_nonempty"},
                ]},
            }}}},
        },
    ],
    "$defs": {
        "actors_nonempty": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
            "minItems": 1,
        },
        "evidence_refs": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
        "actors": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
        "judgment": {
            "type": "object",
            "additionalProperties": False,
            "required": ["value", "evidence_refs"],
            "properties": {
                "value": {"enum": ["yes", "no", "unknown"]},
                "evidence_refs": {"$ref": "#/$defs/evidence_refs"},
            },
        },
    },
}

_EVIDENCE_BUNDLE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://mtagentlens.local/schema/evidence-bundle.schema.json",
    "title": _EVIDENCE_SCHEMA,
    "description": (
        "Structural contract for a frozen evidence bundle manifest. Byte checks "
        "(source sha256, record anchors, evidence_bundle_hash) are enforced by "
        "tools.annotator.evidence.load_bundle."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "binding", "sources", "actors", "events",
                 "evidence_bundle_hash"],
    "properties": {
        "schema": {"const": _EVIDENCE_SCHEMA},
        "binding": {
            "type": "object",
            "additionalProperties": False,
            "required": ["data_release_id", "scenario_id", "run_id",
                         "source_run_uuid", "codebook_version"],
            "properties": {
                "data_release_id": {"$ref": "#/$defs/sha256"},
                "scenario_id": {"$ref": "#/$defs/text"},
                "run_id": {"$ref": "#/$defs/text"},
                "source_run_uuid": {
                    "type": "string",
                    "pattern": ("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                                "[0-9a-f]{4}-[0-9a-f]{12}$"),
                },
                "codebook_version": {"$ref": "#/$defs/text"},
            },
        },
        "sources": {
            "type": "object",
            "minProperties": 1,
            "propertyNames": {"$ref": "#/$defs/text"},
            "additionalProperties": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "sha256", "kind", "producer"],
                "properties": {
                    "path": {"$ref": "#/$defs/text"},
                    "sha256": {"$ref": "#/$defs/sha256"},
                    "kind": {"enum": ["controlled_runner", "raw_capture",
                                      "human_record"]},
                    "producer": {"$ref": "#/$defs/text"},
                },
            },
        },
        "actors": {
            "type": "array",
            "items": {"$ref": "#/$defs/text"},
            "uniqueItems": True,
        },
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["event_id", "source_id", "record_id", "timestamp",
                             "clock_domain", "derivation", "byte_offset",
                             "byte_length", "record_sha256"],
                "properties": {
                    "event_id": {"$ref": "#/$defs/text"},
                    "source_id": {"$ref": "#/$defs/text"},
                    "record_id": {"$ref": "#/$defs/text"},
                    "timestamp": {"$ref": "#/$defs/text"},
                    "clock_domain": {"$ref": "#/$defs/text"},
                    "derivation": {"$ref": "#/$defs/text"},
                    "byte_offset": {"type": "integer", "minimum": 0},
                    "byte_length": {"type": "integer", "minimum": 1},
                    "record_sha256": {"$ref": "#/$defs/sha256"},
                },
            },
        },
        "evidence_bundle_hash": {"$ref": "#/$defs/sha256"},
    },
    "$defs": {
        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "text": {"type": "string", "pattern": "\\S"},
    },
}

_SCHEMAS = {
    "annotation": _ANNOTATION_SCHEMA,
    "evidence-bundle": _EVIDENCE_BUNDLE_SCHEMA,
}

_CONSUME_SNIPPET = """\
import hashlib
import json
from pathlib import Path

root = Path("annotation-dataset")  # this release directory
release = json.loads((root / "release.json").read_text(encoding="utf-8"))

# 1. Verify the content address and the full byte inventory (stdlib only).
body = {key: value for key, value in release.items() if key != "release_id"}
digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
assert digest == release["release_id"], "release_id mismatch"
for relative, expected in release["files"].items():
    assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected

# 2. Read cases and the flat revision log.
cases = {case["case_id"]: case for case in release["cases"]}
rows = [json.loads(line) for line in
        (root / "annotations.jsonl").read_text(encoding="utf-8").splitlines()]
for row in rows:
    assert row["case_id"] in cases
    assert row["is_model_proposal"] == (row["method"] == "model_proposal")

# 3. Each case bundle keeps its own frozen hash and per-source checksums.
for case in cases.values():
    bundle_dir = root / "cases" / case["case_id"] / "bundle"
    manifest = json.loads((bundle_dir / "bundle.json").read_text(encoding="utf-8"))
    assert manifest["evidence_bundle_hash"] == case["task_id"]
    frozen = {key: value for key, value in manifest.items()
              if key != "evidence_bundle_hash"}
    encoded = json.dumps(frozen, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == manifest["evidence_bundle_hash"]
    for source in manifest["sources"].values():
        content = (bundle_dir / source["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == source["sha256"]
"""


# --------------------------------------------------------------------------- helpers


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _release_id(release: dict) -> str:
    body = {key: value for key, value in release.items() if key != "release_id"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def _validator(schema: dict):
    if Draft202012Validator is None:  # pragma: no cover
        raise ValueError("jsonschema is required to validate annotation dataset schemas")
    key = schema["$id"]
    validator = _VALIDATORS.get(key)
    if validator is None:
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # jsonschema.SchemaError
            raise ValueError(f"invalid JSON schema {key}: {exc}") from None
        validator = Draft202012Validator(schema)
        _VALIDATORS[key] = validator
    return validator


_VALIDATORS: dict = {}


def _validate_instance(document: object, schema: dict, label: str) -> None:
    errors = sorted(_validator(schema).iter_errors(document),
                    key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        path = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise ValueError(f"{label} violates {schema['title']} at {path}: {error.message}")


def _inventory(root: Path) -> dict[str, str]:
    """Byte inventory of every manifest-bound file, rejecting symlinks anywhere."""
    files: dict[str, str] = {}
    for current, directories, names in os.walk(root, followlinks=False):
        directory = Path(current)
        for name in directories + names:
            path = directory / name
            if path.is_symlink():
                raise ValueError(
                    f"release contains a symlink: {path.relative_to(root).as_posix()}")
        for name in names:
            path = directory / name
            relative = path.relative_to(root).as_posix()
            if relative in _UNMANIFESTED:
                continue
            _require(path.is_file(), f"release contains an unsupported entry: {relative}")
            files[relative] = _sha256(path)
    return dict(sorted(files.items()))


def _deterministic_split(scenario_id: str) -> str:
    digest = hashlib.sha256(scenario_id.encode("utf-8")).hexdigest()
    return "dev" if int(digest, 16) % 2 == 0 else "test"


def _resolve_splits(cases: list[dict], splits: dict[str, str] | None) -> dict[str, str]:
    if splits is None:
        splits = {}
    if not isinstance(splits, dict):
        raise ValueError("splits must be a mapping of case id to dev or test")
    unknown = sorted(set(splits) - {case["case_id"] for case in cases})
    if unknown:
        raise ValueError(f"splits names cases outside the release: {unknown}")
    scenario_splits = {}
    for case in cases:
        case_id = case["case_id"]
        if case_id not in splits:
            continue
        value = splits[case_id]
        _require(isinstance(value, str) and value in _SPLIT_VALUES,
                 f"split for {case_id} must be dev or test")
        scenario_id = case["binding"]["scenario_id"]
        _require(scenario_splits.get(scenario_id, value) == value,
                 f"scenario {scenario_id} has conflicting splits")
        scenario_splits[scenario_id] = value
    return {
        case["case_id"]: scenario_splits.get(
            case["binding"]["scenario_id"],
            _deterministic_split(case["binding"]["scenario_id"]),
        )
        for case in cases
    }


# ---------------------------------------------------------------------------- data


def _case_entry(index: int, task: dict) -> dict:
    bundle = task["evidence_manifest"]
    counts = Counter(event["source_id"] for event in bundle["events"])
    _require(bundle["evidence_bundle_hash"] == task["task_id"],
             "evidence bundle hash does not match its registered task id")
    return {
        "case_id": f"case-{index:04d}",
        "task_id": task["task_id"],
        "binding": bundle["binding"],
        "split": None,
        "evidence_bundle_hash": bundle["evidence_bundle_hash"],
        "evidence_counts": dict(sorted(counts.items())),
        "actors_count": len(bundle["actors"]),
        "events_count": len(bundle["events"]),
    }


def _annotation_rows(cases: list[dict], tasks: list[dict]) -> list[dict]:
    by_task = {task["task_id"]: task for task in tasks}
    rows = []
    for case in cases:
        task = by_task[case["task_id"]]
        bundle = task["evidence_manifest"]
        for record in task["revisions"]:
            for field in ("annotator", "method", "revision_id", "parent_revision_id",
                          "saved_at", "binding", "evidence_bundle_hash", "annotation"):
                _require(field in record,
                         f"store revision for {task['task_id']} is missing {field}")
            _require(record["binding"] == bundle["binding"],
                     "annotation revision binding differs from its evidence bundle")
            _require(record["evidence_bundle_hash"] == task["task_id"],
                     "annotation revision evidence hash differs from its task")
            row = {
                "case_id": case["case_id"],
                "task_id": task["task_id"],
                "annotator": record["annotator"],
                "method": record["method"],
                "revision_id": record["revision_id"],
                "parent_revision_id": record["parent_revision_id"],
                "saved_at": record["saved_at"],
                "codebook_version": record["binding"]["codebook_version"],
                "annotation": record["annotation"],
                "is_model_proposal": record["method"] == "model_proposal",
            }
            if "review" in record:
                row["review"] = record["review"]
            rows.append(row)
    return rows


def _check_annotation_rows(rows: list[dict], cases: list[dict],
                           bundles: dict[str, dict]) -> None:
    by_case = {case["case_id"]: case for case in cases}
    seen_revisions: set[str] = set()
    heads: dict[tuple[str, str], str | None] = {}
    for number, row in enumerate(rows, start=1):
        label = f"annotation line {number}"
        _require(isinstance(row, dict), f"{label} must be a JSON object")
        keys = set(row)
        _require(_ANNOTATION_ROW_KEYS <= keys
                 and not keys - (_ANNOTATION_ROW_KEYS | {"review"}),
                 f"{label} has an unexpected field set")
        case = by_case.get(row["case_id"])
        _require(case is not None, f"{label} references a case outside the release")
        _require(row["task_id"] == case["task_id"],
                 f"{label} task does not match its case")
        for field in ("annotator", "revision_id", "saved_at"):
            _require(_text(row[field]), f"{label} {field} must be non-empty text")
        _require(row["revision_id"] not in seen_revisions,
                 f"{label} duplicates a revision id")
        seen_revisions.add(row["revision_id"])
        _require(row["method"] in _METHODS,
                 f"{label} declared an unsupported method")
        _require(isinstance(row["is_model_proposal"], bool),
                 f"{label} is_model_proposal must be a boolean")
        _require(row["is_model_proposal"] == (row["method"] == "model_proposal"),
                 f"{label} method and is_model_proposal disagree")
        _require(row["codebook_version"] == case["binding"]["codebook_version"],
                 f"{label} codebook does not match its case binding")
        parent = row["parent_revision_id"]
        _require(parent is None or _text(parent),
                 f"{label} parent_revision_id must be an id or null")
        key = (row["task_id"], row["annotator"])
        _require(parent == heads.get(key),
                 f"{label} breaks the append-only parent revision chain")
        heads[key] = row["revision_id"]
        if "review" in row:
            _require(isinstance(row["review"], dict),
                     f"{label} adjudication review must be an object")
        try:
            _validate_annotation(row["annotation"], bundles[row["task_id"]])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{label} is not a valid annotation: {exc}") from None
        _validate_instance(row["annotation"], _ANNOTATION_SCHEMA, label)


def _read_annotation_rows(root: Path) -> list[dict]:
    path = root / "annotations.jsonl"
    _require(not path.is_symlink() and path.is_file(), "annotations.jsonl is missing")
    text = path.read_text(encoding="utf-8")
    _require(not text or text.endswith("\n"),
             "annotations.jsonl must end with a newline")
    rows = []
    for number, line in enumerate(text.splitlines(), start=1):
        _require(bool(line.strip()), f"annotations.jsonl line {number} is blank")
        try:
            rows.append(_loads_strict(line))
        except ValueError as exc:
            raise ValueError(f"annotations.jsonl line {number}: {exc}") from None
    return rows


def _counts(cases: list[dict], rows: list[dict], attestations: list[dict],
            splits: dict[str, str]) -> dict:
    methods = Counter(row["method"] for row in rows)
    return {
        "cases": len(cases),
        "annotations": len(rows),
        "annotators": len({row["annotator"] for row in rows}),
        "methods": {name: methods.get(name, 0) for name in _METHODS},
        "model_proposals": methods.get("model_proposal", 0),
        "human_annotations": methods.get("human", 0),
        "attestations": len(attestations),
        "splits": {value: sum(1 for split in splits.values() if split == value)
                   for value in _SPLIT_VALUES},
    }


def _publication_blockers(codebook_version: str, counts: dict) -> list[str]:
    blockers = [f"codebook {codebook_version!r} approval is not asserted by this release"]
    if counts["human_annotations"] == 0:
        blockers.append(
            "no independent human annotations: every annotation is a declared "
            "model proposal or controlled-execution draft, not a human label")
    else:
        blockers.append(
            "human annotations are self-declared methods; annotator identity and "
            "independence are not authenticated")
    if counts["attestations"] == 0:
        blockers.append(
            "no reviewer attestations recorded: reviewers.json is a responsibility "
            "assignment, not completed review")
    else:
        blockers.append(
            "review attestations are typed statements, not independent human "
            "annotations or cryptographic signatures")
    blockers.append(
        "no human inter-annotator agreement (kappa) is computed or available in this release")
    blockers.append(
        "detector performance (TPR/FPR) is not estimable from this annotation release")
    blockers.append(
        "this release is not blinded; the workbench blinded view is separate")
    return blockers


def _attestations_payload(store: RecordStore, codebook_version: str,
                          task_ids: set[str]) -> dict:
    records = store.list_attestations()
    for record in records:
        _require(record.get("task_id") in task_ids,
                 "attestation references a task outside the release")
    return {
        "schema": _ATTESTATIONS_SCHEMA,
        "codebook_version": codebook_version,
        "attestations": records,
        "counts": {"attestations": len(records)},
        "paper_metrics_available": False,
        "note": ("Typed reviewer statements only: not independent human annotations "
                 "and not cryptographic signatures."),
    }


def _check_attestations(payload: object, codebook_version: str, rows: list[dict],
                        task_ids: set[str]) -> None:
    _require(isinstance(payload, dict) and set(payload) == _ATTESTATIONS_PAYLOAD_KEYS,
             "attestations.json keys are not exact")
    _require(payload["schema"] == _ATTESTATIONS_SCHEMA,
             "unsupported attestation export schema")
    _require(payload["codebook_version"] == codebook_version,
             "attestation codebook does not match the release")
    _require(payload["paper_metrics_available"] is False,
             "attestations cannot make paper metrics available")
    records = payload["attestations"]
    _require(isinstance(records, list), "attestations must be a list")
    _require(payload["counts"] == {"attestations": len(records)},
             "attestation counts do not match its records")
    _require(_text(payload["note"]), "attestation export note must be text")
    revisions = {row["revision_id"]: row for row in rows}
    seen: set[str] = set()
    for number, record in enumerate(records, start=1):
        label = f"attestation {number}"
        _require(isinstance(record, dict), f"{label} must be a JSON object")
        _require(record.get("schema") == _ATTESTATION_RECORD_SCHEMA,
                 f"{label} has an unsupported schema")
        _require(_text(record.get("attestation_id")), f"{label} needs an id")
        _require(record["attestation_id"] not in seen, f"{label} duplicates an id")
        seen.add(record["attestation_id"])
        _require(_text(record.get("reviewer")), f"{label} needs a reviewer")
        _require(record.get("task_id") in task_ids,
                 f"{label} references a task outside the release")
        proposal = revisions.get(record.get("proposal_revision_id"))
        _require(proposal is not None,
                 f"{label} references an unknown proposal revision")
        _require(proposal["method"] == "model_proposal",
                 f"{label} must reference a model proposal")
        _require(proposal["task_id"] == record["task_id"],
                 f"{label} task does not match its proposal")
        _require(record["reviewer"] != proposal["annotator"],
                 f"{label} lets a proposal annotator review their own draft")
        _require(record.get("evidence_bundle_hash") == record["task_id"],
                 f"{label} evidence hash does not match its task")
        _require(record.get("codebook_version") == codebook_version,
                 f"{label} codebook does not match the release")
        _require(record.get("paper_metrics_available") is False,
                 f"{label} cannot make paper metrics available")
        _require(_text(record.get("statement")), f"{label} needs a statement")


def _reviewer_sidecar(reviewers: Path | None, release_id: str) -> dict:
    if reviewers is None:
        return {
            "schema": _REVIEWERS_SCHEMA,
            "release_id": release_id,
            "reviewers": [],
            "paper_metrics_available": False,
            "note": ("No reviewer assignments were supplied for this release. An empty "
                     "reviewer list is not evidence of review and no human review is "
                     "asserted."),
        }
    path = Path(reviewers)
    _require(path.is_file() and not path.is_symlink(), "reviewer sidecar does not exist")
    payload = _loads_strict(path.read_text(encoding="utf-8"))
    _require(isinstance(payload, dict), "reviewer sidecar must be a JSON object")
    entries = payload.get("reviewers", [])
    _require(isinstance(entries, list), "reviewer sidecar reviewers must be a list")
    for entry in entries:
        _require(isinstance(entry, dict) and _text(entry.get("name")),
                 "each reviewer entry must be an object with a non-empty name")
    sidecar = dict(payload)
    if not _text(sidecar.get("schema")):
        sidecar["schema"] = _REVIEWERS_SCHEMA
    sidecar["release_id"] = release_id
    sidecar["reviewers"] = [dict(entry) for entry in entries]
    # An imported sidecar can never claim paper metrics: no review is authenticated here.
    sidecar["paper_metrics_available"] = False
    sidecar["release_note"] = ("Imported reviewer responsibility assignment. It is not "
                               "evidence of completed review; completion requires typed "
                               "attestations.")
    return sidecar


def _check_reviewers(payload: object, release_id: str) -> None:
    _require(isinstance(payload, dict), "reviewers.json must be a JSON object")
    _require(_text(payload.get("schema")), "reviewers.json needs a schema identifier")
    _require(payload.get("release_id") == release_id,
             "reviewers.json is not bound to this release id")
    entries = payload.get("reviewers")
    _require(isinstance(entries, list), "reviewers.json reviewers must be a list")
    for entry in entries:
        _require(isinstance(entry, dict) and _text(entry.get("name")),
                 "each reviewer entry must be an object with a non-empty name")
    _require(payload.get("paper_metrics_available") is False,
             "reviewers.json cannot make paper metrics available")


# --------------------------------------------------------------------------- readme


def _readme(release: dict) -> str:
    cases = release["cases"]
    counts = release["counts"]
    methods = counts["methods"]
    splits = release["splits"]
    table = "\n".join(
        f"| `{case['case_id']}` | `{case['split']}` | {case['events_count']} | "
        f"{case['actors_count']} | `{case['task_id']}` |"
        for case in cases)
    parts = [
        "# MtAgentLens agent 标注数据集 release",
        "",
        "本目录是一个**可复现的标注数据集 release**：由 "
        "`tools/annotator/release.py` 从 append-only 标注 store 构建，冻结"
        "「证据包 + 标注修订 + 复核责任 sidecar + 复核声明」，供外部工具离线消费与审计。",
        "",
        f"- schema：`{SCHEMA}`",
        f"- codebook：`{release['codebook_version']}`",
        f"- case 数：{counts['cases']}（dev {counts['splits']['dev']} / "
        f"test {counts['splits']['test']}）",
        f"- 标注修订：{counts['annotations']} 条（model_proposal "
        f"{methods['model_proposal']} / human {methods['human']} / "
        f"controlled_execution {methods['controlled_execution']}）",
        f"- 复核声明（attestations）：{counts['attestations']} 条",
        "- `paper_metrics_available`：`false`",
        "",
        "> 本文件由 builder 生成且**不含 `release_id`**：`release_id` 覆盖本文件的字节，"
        "把 `release_id` 写进来会造成自引用。权威内容地址以 `release.json` 为准。",
        "",
        "## 1. 这是什么 / 不是什么",
        "",
        "**是**：一次标注状态的字节级快照。它把每个 case 的证据包、所有标注修订"
        "（含 parent 链与 method 声明）、复核责任分配和复核声明分别冻结，并用"
        "`release_id` 与逐文件 sha256 绑定，使第三方可以离线复核「这份数据现在是什么」。",
        "",
        "**不是**：",
        "",
        "- 不是人工标签集。`method=model_proposal` 是 AI/模型草稿，**永远不等于 human**；",
        "- 不是人类一致性结论。本 release 不含真人独立标注，因此没有任何 kappa；",
        "- 不是检测器评测集。TPR/FPR 不能由标注 release 直接得出；",
        "- 不是复核完成证明。`reviewers.json` 只是责任分配，"
        "`attestations.json` 才是 typed 复核声明，二者都不构成独立人工标注。",
        "",
        "## 2. 目录结构",
        "",
        "```text",
        "annotation-dataset/",
        "├── release.json              # 清单：schema / release_id / cases / splits / files / counts",
        "├── README.md                 # 本文件（不在自引用范围内的说明见上）",
        "├── LICENSE                   # Apache-2.0 OR MIT 声明与仓库许可文件指引",
        "├── annotations.jsonl         # 每行一条标注修订（扁平化，保留 parent 链）",
        "├── reviewers.json            # 复核责任 sidecar（内嵌 release_id 绑定）",
        "├── attestations.json         # 如实导出的复核声明（typed statements）",
        "├── schema/",
        "│   ├── annotation.schema.json",
        "│   └── evidence-bundle.schema.json",
        "└── cases/",
        "    └── case-0001/",
        "        ├── case.json         # case 元数据（binding 含 scenario/run uuid）",
        "        └── bundle/           # 完整证据包，evidence_bundle_hash 可独立校验",
        "            ├── bundle.json",
        "            └── sources/",
        "```",
        "",
        "## 3. 数据契约",
        "",
        "### release.json",
        "",
        "| 字段 | 含义 |",
        "|------|------|",
        "| `schema` | 固定为 `" + SCHEMA + "` |",
        "| `release_id` | 除自身外整个 release 对象的 SHA-256（`sort_keys`） |",
        "| `codebook_version` | 本 release 绑定的唯一 codebook 版本（来自证据包 binding） |",
        "| `cases` | case 列表，按 `task_id` 排序，ID 从 `case-0001` 连续编号 |",
        "| `splits` | `case_id -> dev/test`；默认按 scenario_id 的 SHA-256 奇偶分组，同场景所有 run 共组 |",
        "| `files` | 除 `release.json`/`reviewers.json` 外所有文件的相对路径 -> sha256 |",
        "| `counts` | case / 修订 / annotator / method / split 计数 |",
        "| `paper_metrics_available` | 恒为 `false`（本 builder 不认证可发表标签） |",
        "| `publication_blockers` | 非空，逐条说明为何不能当论文指标使用 |",
        "| `note` | builder 调用方传入的说明，可为 `null` |",
        "",
        "新增 case 或调整排序不会改变已有 scenario 的默认划分。显式指定某 case 会覆盖该场景全部 case；同场景冲突划分被拒绝。",
        "当前 binding 没有 family_id，因此此约束不证明不同 scenario 的变体家族之间不存在泄漏。",
        "",
        "### case.json",
        "",
        "`case_id`、`task_id`（= `evidence_bundle_hash`）、`binding`（含 "
        "`scenario_id`/`run_id`/`source_run_uuid`/`codebook_version`）、`split`、"
        "`evidence_bundle_hash`、`evidence_counts`（按 source 计数）、"
        "`actors_count`、`events_count`。",
        "",
        "### annotations.jsonl",
        "",
        "每行一条修订，字段固定为 `case_id`、`task_id`、`annotator`、`method`、"
        "`revision_id`、`parent_revision_id`、`saved_at`、`codebook_version`、"
        "`annotation`（原始 payload）、`is_model_proposal`；若该修订是一次裁决，"
        "额外保留 `review`（被复核 revision 与 rationale），不丢 provenance。"
        "`method` 原样保留，绝不改名。",
        "",
        "### case 一览",
        "",
        "| case | split | events | actors | task_id |",
        "|------|-------|--------|--------|---------|",
        table,
        "",
        "## 4. 如何消费",
        "",
        "最小纯标准库校验 + 读取（不依赖本仓库）：",
        "",
        "```python",
        _CONSUME_SNIPPET.rstrip("\n"),
        "```",
        "",
        "仓库内可直接使用已实现同一契约的 API：",
        "",
        "```python",
        "from pathlib import Path",
        "from tools.annotator.release import (",
        "    verify_annotation_release,",
        "    consume_annotation_release,",
        ")",
        "",
        "root = Path(\"annotation-dataset\")",
        "release = verify_annotation_release(root)  # 全量校验，失败抛 ValueError",
        "for case in consume_annotation_release(root):",
        "    print(case[\"case_id\"], case[\"split\"], len(case[\"annotations\"]))",
        "```",
        "",
        "`consume_annotation_release` 先执行全量校验再返回，因此外部工具的默认路径"
        "不会消费被篡改的数据。每个 case 条目包含 `release_id`、`codebook_version`、"
        "`bundle_dir`（相对路径）和 `annotations`（该 case 的全部修订）。",
        "",
        "## 5. provenance 链",
        "",
        "1. `binding` 由上游数据集 release 产生：`data_release_id`、`scenario_id`、"
        "`run_id`、`source_run_uuid`、`codebook_version`。",
        "2. 证据包 `bundle.json` 记录每个原始 source 的 sha256 与每条 evidence "
        "record 的 `record_sha256`；`evidence_bundle_hash` 是除该字段外整个 manifest "
        "的 SHA-256，且 `task_id == evidence_bundle_hash`。",
        "3. store 是 append-only：每条修订有 `revision_id`、`parent_revision_id`、"
        "`saved_at`、`method`，历史不可 UPDATE/DELETE。",
        "4. `annotations.jsonl` 逐行保留上述修订；`release.json.files` 再对"
        "`annotations.jsonl` 及每个 case 的 `bundle/` 字节做 sha256。",
        "5. `release_id` 覆盖 `release.json` 除自身外的全部字段，因此同时绑定"
        "`files` 清单、`cases`、`splits`、`counts`、`publication_blockers`。",
        "6. `reviewers.json` 内嵌 `release_id`，把责任分配绑定到本次 release；"
        "`attestations.json` 为 store 中 typed 复核声明的原样导出。",
        "",
        "**为什么 `reviewers.json` 不在 `files` 里？** 它必须内嵌 `release_id`，"
        "而 `release_id` 覆盖 `files`；若把它的字节也算进清单就会形成自引用。"
        "因此校验器改为强制要求该文件存在且其 `release_id` 与本 release 完全一致。",
        "",
        "## 6. schema",
        "",
        "- `schema/annotation.schema.json`（draft 2020-12）与 "
        "`records.py::_validate_annotation` 的结构性规则严格一致：字段集合、"
        "`yes/no/unknown` 取值、已知判断必须引用证据、引用不得重复、"
        "attempted/executed/succeeded 矛盾拒绝、roots 非 null 必须引用证据；"
        "`identities` 的引用完整性（必须覆盖 bundle 的全部 event 且 actor 必须注册）"
        "无法用 JSON Schema 表达，由 `verify_annotation_release` 结合证据包强制。",
        "- `schema/evidence-bundle.schema.json`（draft 2020-12）镜像 `load_bundle` "
        "的结构规则；字节级校验（source sha256、record anchor、"
        "`evidence_bundle_hash`）仍由 `load_bundle` 执行。",
        "",
        "## 7. 不可变性与校验",
        "",
        "- 目标目录已存在时构建直接失败；写入在临时目录完成后原子 rename，"
        "失败不会留下半成品。",
        "- release 内不允许 symlink；所有路径必须 contained。",
        "- `release.json.files` 是逐文件 sha256 全量清单：任何 case bundle 字节改动、"
        "文件缺失、或新增未登记文件都会被 `verify_annotation_release` 拒绝。",
        "- 重新校验（仓库内）：",
        "",
        "```python",
        "from pathlib import Path",
        "from tools.annotator.release import verify_annotation_release",
        "",
        "verify_annotation_release(Path(\"annotation-dataset\"))",
        "```",
        "",
        "## 8. 诚实边界",
        "",
        "本 release 的 `publication_blockers` 如实列出：",
        "",
    ]
    parts.extend(f"- {blocker}" for blocker in release["publication_blockers"])
    parts.extend([
        "",
        "额外说明：",
        "",
        "- **AI 草稿不是人工标签。** `model_proposal` 只是模型草稿；即使 `method` "
        "声明为 `human`，也只是声明，身份与独立性未经认证。",
        "- **没有真人 κ。** 未记录任何人类独立标注，因此不存在可引用的一致性系数。",
        "- **样本不足以做 TPR/FPR。** 本 release 只有 "
        f"{counts['cases']} 个 case，且不含攻击/良性评测结论，不能报告检测性能。",
        "- **非盲化。** `binding` 含 scenario/run uuid，本 release 面向外部工具与复现；"
        "标注时请使用 workbench 的盲化视图，不要把本目录当作盲化材料。",
        "",
        "## 9. 许可",
        "",
        "本 release 采用 **Apache-2.0 OR MIT** 双许可，SPDX 标识为",
        "`Apache-2.0 OR MIT`，可任选其一。完整许可文本随 MtAgentLens 仓库分发，",
        "见仓库根目录 `LICENSE-APACHE` 与 `LICENSE-MIT`；本目录的 `LICENSE` 文件",
        "给出声明与指引，不重复版权年份或作者。",
        "",
        "## 10. 复现",
        "",
        "```python",
        "from pathlib import Path",
        "from tools.annotator.release import build_annotation_release",
        "",
        "build_annotation_release(",
        "    Path(\"records.sqlite\"),",
        "    Path(\"annotation-dataset\"),",
        "    splits={\"case-0001\": \"dev\"},          # 省略则按 sha256(binding.scenario_id) 确定性分配",
        "    reviewers=Path(\"reviewers-input.json\"),  # 可选；无输入时写空 reviewers",
        "    note=\"pilot annotation dataset\",",
        ")",
        "```",
        "",
        "同一 store、同一参数构建两次会得到相同 `release_id` 与相同 `files` 清单；",
        "构建时间、主机名等信息不写入 release。",
        "",
    ])
    return "\n".join(parts)


_LICENSE_TEXT = """\
SPDX-License-Identifier: Apache-2.0 OR MIT

This annotation dataset release is dual-licensed under the Apache License,
Version 2.0, or the MIT License, at your option. SPDX expression:
Apache-2.0 OR MIT.

The complete license texts are distributed with the MtAgentLens repository:

  LICENSE-APACHE   Apache License, Version 2.0
  LICENSE-MIT      MIT License (see the file for its copyright holder)

Unless required by applicable law or agreed to in writing, software
distributed under these licenses is provided on an "AS IS" BASIS, WITHOUT
WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
respective license file for the specific language governing permissions and
limitations.
"""


# --------------------------------------------------------------------------- build


def build_annotation_release(store_path: Path, out: Path, *,
                             splits: dict[str, str] | None = None,
                             reviewers: Path | None = None,
                             note: str | None = None) -> dict:
    """Freeze one annotation store into an immutable, externally consumable release."""
    store_path = Path(store_path)
    out = Path(out)
    if note is not None and not isinstance(note, str):
        raise ValueError("note must be text or None")
    if not store_path.is_file():
        raise ValueError("annotation store does not exist; register evidence bundles first")
    if out.exists() or out.is_symlink():
        raise ValueError("annotation dataset destination already exists; releases are immutable")
    out = out.resolve()
    if out.exists() or out.is_symlink():
        raise ValueError("annotation dataset destination already exists; releases are immutable")

    store = RecordStore(store_path)
    exported = store.export()
    tasks = sorted(exported["tasks"], key=lambda task: task["task_id"])
    if not tasks:
        raise ValueError("annotation store has no registered tasks to release")
    codebooks = sorted({task["binding"]["codebook_version"] for task in tasks})
    if len(codebooks) != 1:
        raise ValueError(
            f"store mixes annotation codebooks {codebooks}; a release must bind exactly "
            "one codebook")
    codebook_version = codebooks[0]

    cases = [_case_entry(index, task) for index, task in enumerate(tasks, start=1)]
    splits = _resolve_splits(cases, splits)
    for case in cases:
        case["split"] = splits[case["case_id"]]
    rows = _annotation_rows(cases, tasks)
    bundles = {task["task_id"]: task["evidence_manifest"] for task in tasks}
    _check_annotation_rows(rows, cases, bundles)

    task_ids = {task["task_id"] for task in tasks}
    attestations = _attestations_payload(store, codebook_version, task_ids)
    _check_attestations(attestations, codebook_version, rows, task_ids)

    counts = _counts(cases, rows, attestations["attestations"], splits)
    release = {
        "schema": SCHEMA,
        "codebook_version": codebook_version,
        "cases": cases,
        "splits": splits,
        "counts": counts,
        "paper_metrics_available": False,
        "publication_blockers": _publication_blockers(codebook_version, counts),
        "note": note,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".annotation-dataset-", dir=str(out.parent)))
    try:
        for case, task in zip(cases, tasks):
            case_dir = temporary / "cases" / case["case_id"]
            case_dir.mkdir(parents=True)
            source_root = Path(store._task(task["task_id"])["bundle_path"])
            shutil.copytree(source_root, case_dir / "bundle")
            copied = load_bundle(case_dir / "bundle")
            _require(copied["binding"] == case["binding"],
                     "copied evidence bundle binding changed")
            _require(copied["evidence_bundle_hash"] == case["evidence_bundle_hash"],
                     "copied evidence bundle does not reproduce its hash")
            _validate_instance(copied, _EVIDENCE_BUNDLE_SCHEMA,
                               f"evidence bundle {case['case_id']}")
            _write_json(case_dir / "case.json", case)

        _write_text(temporary / "annotations.jsonl",
                    "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                            for row in rows))
        _write_json(temporary / "attestations.json", attestations)
        for name, schema in _SCHEMAS.items():
            _write_json(temporary / "schema" / f"{name}.schema.json", schema)
        _write_text(temporary / "LICENSE", _LICENSE_TEXT)
        _write_text(temporary / "README.md", _readme(release))

        release["files"] = _inventory(temporary)
        release["release_id"] = _release_id(release)
        _write_json(temporary / "release.json", release)
        _write_json(temporary / "reviewers.json",
                    _reviewer_sidecar(reviewers, release["release_id"]))

        # Gate the rename on the same full verification external consumers use.
        verify_annotation_release(temporary)
        for current, directories, names in os.walk(temporary):
            os.chmod(current, 0o755)
            for name in names:
                os.chmod(Path(current) / name, 0o644)
        os.rename(temporary, out)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return release


# -------------------------------------------------------------------------- verify


def verify_annotation_release(root: Path) -> dict:
    """Fully verify a release; raise ValueError on any violation."""
    root = Path(root)
    _require(not root.is_symlink() and root.is_dir(),
             "annotation dataset root must be a regular directory")
    root = root.resolve()
    manifest_path = root / "release.json"
    _require(not manifest_path.is_symlink() and manifest_path.is_file(),
             "release.json is missing")
    release = _loads_strict(manifest_path.read_text(encoding="utf-8"))
    _require(isinstance(release, dict) and set(release) == _RELEASE_KEYS,
             "release.json keys are not exact")
    _require(release["schema"] == SCHEMA, "unsupported annotation dataset schema")
    actual = _inventory(root)
    _require(actual == release["files"],
             "annotation dataset inventory mismatch: a file was modified, removed or added")
    _require(_release_id(release) == release["release_id"],
             "annotation dataset release id mismatch")

    for name, schema in _SCHEMAS.items():
        path = root / "schema" / f"{name}.schema.json"
        _require(not path.is_symlink() and path.is_file(),
                 f"schema/{name}.schema.json is missing")
        shipped = _loads_strict(path.read_text(encoding="utf-8"))
        _require(shipped == schema,
                 f"schema/{name}.schema.json does not match the verifier schema")
        _validator(schema)

    cases = release["cases"]
    _require(isinstance(cases, list) and bool(cases),
             "release must contain at least one case")
    _require(isinstance(release["splits"], dict), "splits must be a mapping")
    case_ids: list[str] = []
    bundles: dict[str, dict] = {}
    codebooks: set[str] = set()
    scenario_splits: dict[str, str] = {}
    for index, case in enumerate(cases, start=1):
        _require(isinstance(case, dict) and set(case) == _CASE_KEYS,
                 "case entry keys are not exact")
        expected = f"case-{index:04d}"
        _require(case["case_id"] == expected,
                 "case ids must be sequential case-0001, case-0002, ...")
        _require(case["split"] in _SPLIT_VALUES, f"{expected} has an invalid split")
        case_dir = root / "cases" / case["case_id"]
        _require(not case_dir.is_symlink() and case_dir.is_dir(),
                 f"{case['case_id']} directory is missing")
        payload_path = case_dir / "case.json"
        _require(not payload_path.is_symlink() and payload_path.is_file(),
                 f"{case['case_id']}/case.json is missing")
        payload = _loads_strict(payload_path.read_text(encoding="utf-8"))
        _require(payload == case, f"{case['case_id']}/case.json disagrees with release.json")
        bundle = load_bundle(case_dir / "bundle")
        _validate_instance(bundle, _EVIDENCE_BUNDLE_SCHEMA,
                           f"evidence bundle {case['case_id']}")
        _require(bundle["binding"] == case["binding"],
                 f"{case['case_id']} binding disagrees with its evidence bundle")
        _require(bundle["evidence_bundle_hash"] == case["evidence_bundle_hash"],
                 f"{case['case_id']} evidence bundle hash mismatch")
        _require(case["task_id"] == case["evidence_bundle_hash"],
                 f"{case['case_id']} task id must equal its evidence bundle hash")
        counts = Counter(event["source_id"] for event in bundle["events"])
        _require(dict(sorted(counts.items())) == case["evidence_counts"],
                 f"{case['case_id']} evidence counts mismatch")
        _require(len(bundle["actors"]) == case["actors_count"],
                 f"{case['case_id']} actors count mismatch")
        _require(len(bundle["events"]) == case["events_count"],
                 f"{case['case_id']} events count mismatch")
        _require(release["splits"].get(case["case_id"]) == case["split"],
                 f"{case['case_id']} split disagrees with release.splits")
        scenario_id = bundle["binding"]["scenario_id"]
        _require(scenario_splits.get(scenario_id, case["split"]) == case["split"],
                 f"scenario {scenario_id} has conflicting splits")
        scenario_splits[scenario_id] = case["split"]
        codebooks.add(bundle["binding"]["codebook_version"])
        bundles[case["task_id"]] = bundle
        case_ids.append(case["case_id"])

    _require(set(release["splits"]) == set(case_ids),
             "splits must name exactly the release cases")
    _require(codebooks == {release["codebook_version"]},
             "release codebook does not match every case binding")

    rows = _read_annotation_rows(root)
    _check_annotation_rows(rows, cases, bundles)

    attestations_path = root / "attestations.json"
    _require(not attestations_path.is_symlink() and attestations_path.is_file(),
             "attestations.json is missing")
    attestations = _loads_strict(attestations_path.read_text(encoding="utf-8"))
    _check_attestations(attestations, release["codebook_version"], rows,
                        {case["task_id"] for case in cases})

    expected_counts = _counts(cases, rows, attestations["attestations"],
                              release["splits"])
    _require(release["counts"] == expected_counts,
             "release counts do not match the released files")
    _require(release["paper_metrics_available"] is False,
             "paper metrics cannot be available from this annotation release")
    _require(release["publication_blockers"]
             == _publication_blockers(release["codebook_version"], expected_counts),
             "publication blockers do not match the honest declaration")
    _require(release["note"] is None or isinstance(release["note"], str),
             "release note must be text or null")

    reviewers_path = root / "reviewers.json"
    _require(not reviewers_path.is_symlink() and reviewers_path.is_file(),
             "reviewers.json is missing")
    _check_reviewers(_loads_strict(reviewers_path.read_text(encoding="utf-8")),
                     release["release_id"])

    for name in ("README.md", "LICENSE"):
        path = root / name
        _require(not path.is_symlink() and path.is_file(), f"{name} is missing")
    return release


# ------------------------------------------------------------------------- consume


def consume_annotation_release(root: Path) -> list[dict]:
    """Verify then return every case with its binding, bundle path and revisions.

    This is the external-consumer contract: it never returns data from a release
    that fails ``verify_annotation_release``.
    """
    root = Path(root)
    release = verify_annotation_release(root)
    rows = _read_annotation_rows(root)
    result = []
    for case in release["cases"]:
        result.append({
            **case,
            "release_id": release["release_id"],
            "codebook_version": release["codebook_version"],
            "bundle_dir": f"cases/{case['case_id']}/bundle",
            "annotations": [row for row in rows if row["case_id"] == case["case_id"]],
        })
    return result
