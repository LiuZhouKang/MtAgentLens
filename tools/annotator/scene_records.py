"""Append-only scene-level FuxiBench annotations in a dedicated SQLite store.

The store never reads or writes FuxiBench ground truth. Annotations are exactly
what raters entered; nothing here infers labels from detector output, runtime
traces or existing GT.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from tools.annotator.scene_catalog import (
    SOURCE_KINDS,
    catalog_hash,
    scenario_input_hash,
)

SCENE_APPLICATION_ID = 0x4D545343
EXPORT_SCHEMA = "mtagentlens.fuxibench-scene-export/v1"
LABELS = frozenset({"attack", "benign"})
ATTACK_TYPES = frozenset({"A", "B", "benign"})
MIN_DECISIONS = frozenset({"Unsafe", "Warning", "Safe"})
ATTACK_PATTERNS = frozenset({
    "PromptInjection", "IndirectInjection", "CredentialAccess", "DataExfiltration",
    "CommandAndControl", "PrivilegeEscalation", "DefenseEvasion", "Persistence",
    "SupplyChain", "ResourceAbuse", "Reconnaissance", "SuspiciousDelegation",
    "TaintedContextUse", "PolicyViolation",
})
BOUNDARY_TYPES = frozenset({
    "ResourceBoundary", "ContentBoundary", "NetworkBoundary", "DelegationBoundary",
})
SEVERITIES = frozenset({"Info", "Warning", "Critical"})
ANNOTATION_FIELDS = frozenset({
    "label", "attack_type", "task_summary", "expected_behaviors", "benign_activity",
    "min_decision", "should_alert", "payload", "execution_check", "taxonomy_version",
    "evidence_paths", "uncertainties",
})
REQUIRED_FIELDS = (
    "label", "attack_type", "task_summary", "expected_behaviors", "benign_activity",
    "min_decision", "should_alert", "payload", "execution_check",
)
BEHAVIOR_FIELDS = frozenset({
    "behavior_id", "description", "attack_pattern", "boundary_type", "severity",
    "should_alert", "min_agents_involved", "expected_commands", "expected_resources",
    "evidence_requirements", "rationale",
})
PATTERN_FIELDS = frozenset({"type", "pattern", "why"})
_BEHAVIOR_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def fleiss_kappa(ratings: list[list[object]]) -> dict:
    """Calculate Fleiss' nominal agreement for a complete fixed-rater matrix."""
    if not isinstance(ratings, list) or not ratings:
        raise ValueError("ratings must contain at least one unit")
    n_raters = len(ratings[0]) if isinstance(ratings[0], list) else 0
    if n_raters < 2 or any(not isinstance(row, list) or len(row) != n_raters
                           for row in ratings):
        raise ValueError("each unit must have the same number of at least two ratings")
    categories = sorted({value for row in ratings for value in row}, key=lambda value: str(value))
    if not categories:
        raise ValueError("ratings must contain category values")
    n_units = len(ratings)
    observed = sum(
        (sum(row.count(category) ** 2 for category in categories) - n_raters)
        / (n_raters * (n_raters - 1))
        for row in ratings
    ) / n_units
    total = n_units * n_raters
    proportions = {
        str(category): sum(row.count(category) for row in ratings) / total
        for category in categories
    }
    expected = sum(value ** 2 for value in proportions.values())
    kappa = ((observed - expected) / (1 - expected)) if expected < 1 else None
    return {
        "n_units": n_units,
        "n_raters": n_raters,
        "category_proportions": proportions,
        "observed_agreement": observed,
        "expected_agreement": expected,
        "kappa": kappa,
    }


def _codebook_digest(version: str, path: Path | None) -> tuple[str, str]:
    if path is not None and Path(path).is_file():
        return hashlib.sha256(Path(path).read_bytes()).hexdigest(), "file"
    return hashlib.sha256(version.encode("utf-8")).hexdigest(), "version"


def _pattern_list(value: object, field: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != PATTERN_FIELDS:
            raise ValueError(f"{field}[{index}] must contain exactly type, pattern and why")
        for key in ("type", "pattern", "why"):
            if not _text(item[key]):
                raise ValueError(f"{field}[{index}].{key} must be non-empty text")


def _validate_evidence_paths(value: object, known_paths: set[str]) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("evidence_paths must be a list of source paths")
    for item in value:
        if item not in known_paths:
            raise ValueError(f"unknown evidence path: {item}")


def _validate_benign_activity(value: object, *, required: bool) -> None:
    if value is None or value == {}:
        if required:
            raise ValueError("benign_activity.summary is required for a benign scene")
        return
    if not isinstance(value, dict):
        raise ValueError("benign_activity must be an object or null")
    unsupported = set(value) - {"summary", "expected_commands", "expected_resources"}
    if unsupported:
        raise ValueError(f"unsupported benign_activity fields: {sorted(unsupported)}")
    if not _text(value.get("summary")):
        raise ValueError("benign_activity.summary must be non-empty text")
    for field in ("expected_commands", "expected_resources"):
        if field in value:
            _pattern_list(value[field], f"benign_activity.{field}")


def _validate_behaviors(value: object) -> None:
    if not isinstance(value, list):
        raise ValueError("expected_behaviors must be a list")
    seen: set[str] = set()
    for index, behavior in enumerate(value):
        if not isinstance(behavior, dict):
            raise ValueError(f"expected_behaviors[{index}] must be an object")
        unsupported = set(behavior) - BEHAVIOR_FIELDS
        if unsupported:
            raise ValueError(
                f"unsupported expected_behaviors[{index}] fields: {sorted(unsupported)}"
            )
        missing = BEHAVIOR_FIELDS - set(behavior)
        if missing:
            raise ValueError(
                f"missing expected_behaviors[{index}] field: {sorted(missing)[0]}"
            )
        behavior_id = behavior["behavior_id"]
        if not isinstance(behavior_id, str) or not _BEHAVIOR_ID.fullmatch(behavior_id):
            raise ValueError(f"behavior_id must be kebab-case: {behavior_id!r}")
        if behavior_id in seen:
            raise ValueError(f"duplicate behavior_id: {behavior_id}")
        seen.add(behavior_id)
        if not _text(behavior["description"]):
            raise ValueError(f"expected_behaviors[{index}].description must be non-empty text")
        if (not isinstance(behavior["attack_pattern"], str)
                or behavior["attack_pattern"] not in ATTACK_PATTERNS):
            raise ValueError(f"invalid attack_pattern: {behavior['attack_pattern']!r}")
        if (not isinstance(behavior["boundary_type"], str)
                or behavior["boundary_type"] not in BOUNDARY_TYPES):
            raise ValueError(f"invalid boundary_type: {behavior['boundary_type']!r}")
        if (not isinstance(behavior["severity"], str)
                or behavior["severity"] not in SEVERITIES):
            raise ValueError(f"invalid severity: {behavior['severity']!r}")
        if not isinstance(behavior["should_alert"], bool):
            raise ValueError(f"expected_behaviors[{index}].should_alert must be boolean")
        agents = behavior["min_agents_involved"]
        if not isinstance(agents, int) or isinstance(agents, bool) or agents < 1:
            raise ValueError(
                f"expected_behaviors[{index}].min_agents_involved must be a positive integer"
            )
        _pattern_list(behavior["expected_commands"], f"expected_behaviors[{index}].expected_commands")
        _pattern_list(behavior["expected_resources"], f"expected_behaviors[{index}].expected_resources")
        evidence = behavior["evidence_requirements"]
        if isinstance(evidence, str):
            if not _text(evidence):
                raise ValueError(
                    f"expected_behaviors[{index}].evidence_requirements must be non-empty"
                )
        elif isinstance(evidence, list):
            if not evidence or any(not _text(item) for item in evidence):
                raise ValueError(
                    f"expected_behaviors[{index}].evidence_requirements must be non-empty"
                )
        else:
            raise ValueError(
                f"expected_behaviors[{index}].evidence_requirements must be text or a list"
            )
        if not _text(behavior["rationale"]):
            raise ValueError(f"expected_behaviors[{index}].rationale must be non-empty text")


def validate_annotation(
    annotation: object, *, scenario_id: str, sources: list[dict], codebook_version: str
) -> None:
    """Validate one complete scene annotation without inferring semantics."""
    if not isinstance(annotation, dict):
        raise ValueError("annotation must be an object")
    unsupported = set(annotation) - ANNOTATION_FIELDS
    if unsupported:
        raise ValueError(f"unsupported annotation fields: {sorted(unsupported)}")
    for field in REQUIRED_FIELDS:
        if field not in annotation:
            raise ValueError(f"missing required annotation field: {field}")

    label = annotation["label"]
    if not isinstance(label, str) or label not in LABELS:
        raise ValueError(f"invalid label: {label!r}")
    attack_type = annotation["attack_type"]
    if not isinstance(attack_type, str) or attack_type not in ATTACK_TYPES:
        raise ValueError(f"invalid attack_type: {attack_type!r}")
    if not _text(annotation["task_summary"]):
        raise ValueError("task_summary must be non-empty text")
    if (not isinstance(annotation["min_decision"], str)
            or annotation["min_decision"] not in MIN_DECISIONS):
        raise ValueError(f"invalid min_decision: {annotation['min_decision']!r}")
    if not isinstance(annotation["should_alert"], bool):
        raise ValueError("should_alert must be boolean")
    if annotation["payload"] is not None and not isinstance(annotation["payload"], dict):
        raise ValueError("payload must be an object or null")
    if annotation["execution_check"] is not None and not isinstance(
        annotation["execution_check"], (dict, list, str)
    ):
        raise ValueError("execution_check must be an object, list, text or null")
    taxonomy_version = annotation.get("taxonomy_version")
    if taxonomy_version is not None and taxonomy_version != codebook_version:
        raise ValueError(
            f"taxonomy_version {taxonomy_version!r} does not match codebook {codebook_version!r}"
        )

    _validate_behaviors(annotation["expected_behaviors"])
    if label == "benign":
        if attack_type != "benign":
            raise ValueError("benign scenes require attack_type=benign")
        if annotation["min_decision"] != "Safe":
            raise ValueError("benign scenes require min_decision=Safe")
        if annotation["should_alert"] is not False:
            raise ValueError("benign scenes require should_alert=false")
        if annotation["expected_behaviors"]:
            raise ValueError("benign scenes must not declare expected_behaviors")
        _validate_benign_activity(annotation["benign_activity"], required=True)
    else:
        if attack_type not in {"A", "B"}:
            raise ValueError("attack scenes require attack_type A or B")
        if annotation["min_decision"] not in {"Unsafe", "Warning"}:
            raise ValueError("attack scenes require min_decision Unsafe or Warning")
        if annotation["should_alert"] is not True:
            raise ValueError("attack scenes require should_alert=true")
        if not 1 <= len(annotation["expected_behaviors"]) <= 4:
            raise ValueError("attack scenes require 1-4 expected_behaviors")
        _validate_benign_activity(annotation["benign_activity"], required=False)

    known_paths = {source["path"] for source in sources}
    if "evidence_paths" in annotation:
        _validate_evidence_paths(annotation["evidence_paths"], known_paths)
    uncertainties = annotation.get("uncertainties")
    if uncertainties is not None:
        if not isinstance(uncertainties, list):
            raise ValueError("uncertainties must be a list")
        for index, item in enumerate(uncertainties):
            if isinstance(item, str):
                if not _text(item):
                    raise ValueError(f"uncertainties[{index}] must be non-empty")
                continue
            if not isinstance(item, dict):
                raise ValueError(f"uncertainties[{index}] must be text or an object")
            unsupported_uncertainty = set(item) - {"field", "note", "evidence_paths"}
            if unsupported_uncertainty:
                raise ValueError(
                    f"unsupported uncertainties[{index}] fields: "
                    f"{sorted(unsupported_uncertainty)}"
                )
            if not _text(item.get("field")) or not _text(item.get("note")):
                raise ValueError(f"uncertainties[{index}] requires field and note")
            if "evidence_paths" in item:
                _validate_evidence_paths(item["evidence_paths"], known_paths)


def validate_draft(
    annotation: object, *, scenario_id: str, sources: list[dict], codebook_version: str
) -> None:
    """Allow incomplete form state while rejecting unsupported and unbound evidence."""
    if not isinstance(annotation, dict):
        raise ValueError("annotation must be an object")
    unsupported = set(annotation) - ANNOTATION_FIELDS
    if unsupported:
        raise ValueError(f"unsupported annotation fields: {sorted(unsupported)}")
    taxonomy_version = annotation.get("taxonomy_version")
    if taxonomy_version not in (None, "", codebook_version):
        raise ValueError(
            f"taxonomy_version {taxonomy_version!r} does not match codebook {codebook_version!r}"
        )
    if "evidence_paths" in annotation:
        _validate_evidence_paths(
            annotation["evidence_paths"], {source["path"] for source in sources}
        )


def _revision_row(row: sqlite3.Row) -> dict:
    return {
        "revision_id": row["revision_id"],
        "scenario_id": row["scenario_id"],
        "rater": row["rater"],
        "kind": row["kind"],
        "annotation": json.loads(row["annotation_json"]),
        "parent_revision_id": row["parent_revision_id"],
        "created_at": row["created_at"],
        "change_reason": row["change_reason"],
        "codebook_version": row["codebook_version"],
        "input_sha256": row["input_sha256"],
    }


def _adjudication_row(row: sqlite3.Row) -> dict:
    return {
        "adjudication_id": row["adjudication_id"],
        "scenario_id": row["scenario_id"],
        "adjudicator": row["adjudicator"],
        "annotation": json.loads(row["annotation_json"]),
        "rationale": row["rationale"],
        "source_revision_ids": json.loads(row["source_revision_ids_json"]),
        "previous_adjudication_id": row["previous_adjudication_id"],
        "created_at": row["created_at"],
        "codebook_version": row["codebook_version"],
        "input_sha256": row["input_sha256"],
    }


class SceneRecordStore:
    """Dedicated, append-only scene annotation store with a three-rater policy."""

    def __init__(
        self,
        path: Path,
        scenario_catalog: list[dict],
        raters: list[str],
        codebook_version: str,
        *,
        codebook_path: Path | None = None,
    ):
        self.path = Path(path)
        self._validate_raters(raters)
        self.raters = list(raters)
        if not _text(codebook_version):
            raise ValueError("codebook_version must be non-empty")
        self.codebook_version = codebook_version
        self._catalog = self._normalize_catalog(scenario_catalog)
        self._catalog_by_id = {row["scenario_id"]: row for row in self._catalog}
        self.catalog_sha256 = catalog_hash(self._catalog)
        self.codebook_sha256, self.codebook_hash_kind = _codebook_digest(
            codebook_version, codebook_path
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.path.exists() and self.path.stat().st_size > 0
        with self._connect() as conn:
            self._check_or_create_schema(conn, existing=existing)
            stored = conn.execute("SELECT * FROM scene_run WHERE singleton=1").fetchone()
            if stored is None:
                self._write_binding(conn)
            else:
                self._verify_binding(conn, stored, codebook_path=codebook_path)
                self.codebook_sha256 = stored["codebook_sha256"]
                self.codebook_hash_kind = stored["codebook_hash_kind"]

    # -- construction helpers -------------------------------------------------

    @staticmethod
    def _validate_raters(raters: list[str]) -> None:
        if not isinstance(raters, list) or len(raters) not in (1, 3):
            raise ValueError("configure one offline annotator or the complete three-rater team")
        if any(not _text(rater) for rater in raters):
            raise ValueError("rater names must be non-empty text")
        if len(set(raters)) != len(raters):
            raise ValueError("configured raters must be distinct")

    @staticmethod
    def _normalize_catalog(scenario_catalog: list[dict]) -> list[dict]:
        if not isinstance(scenario_catalog, list) or not scenario_catalog:
            raise ValueError("scenario catalog must be a non-empty list")
        output = []
        seen = set()
        for row in scenario_catalog:
            if not isinstance(row, dict) or not _text(row.get("scenario_id")):
                raise ValueError("scenario catalog rows require a scenario_id")
            scenario_id = row["scenario_id"]
            if scenario_id in seen:
                raise ValueError(f"duplicate scenario id: {scenario_id}")
            seen.add(scenario_id)
            sources = row.get("sources")
            if not isinstance(sources, list):
                raise ValueError(f"scene {scenario_id} sources must be a list")
            normalized_sources = []
            for source in sources:
                if not isinstance(source, dict):
                    raise ValueError(f"scene {scenario_id} source must be an object")
                path = source.get("path")
                digest = source.get("sha256")
                kind = source.get("kind")
                if not _text(path) or not _text(digest):
                    raise ValueError(f"scene {scenario_id} source needs path and sha256")
                if not isinstance(kind, str) or kind not in SOURCE_KINDS:
                    raise ValueError(f"invalid source kind: {kind!r}")
                normalized_sources.append({
                    "path": path,
                    "sha256": digest,
                    "text": source.get("text", ""),
                    "kind": kind,
                })
            normalized_sources.sort(key=lambda item: item["path"])
            output.append({"scenario_id": scenario_id, "sources": normalized_sources})
        output.sort(key=lambda item: item["scenario_id"])
        return output

    def _binding(self) -> dict:
        return {
            row["scenario_id"]: {
                "input_sha256": scenario_input_hash(row["sources"]),
                "sources": [
                    {"path": source["path"], "sha256": source["sha256"], "kind": source["kind"]}
                    for source in row["sources"]
                ],
            }
            for row in self._catalog
        }

    def _write_binding(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO scene_run (singleton, raters_json, codebook_version, codebook_sha256,"
            " codebook_hash_kind, catalog_sha256) VALUES (1, ?, ?, ?, ?, ?)",
            (
                json.dumps(self.raters, ensure_ascii=False),
                self.codebook_version,
                self.codebook_sha256,
                self.codebook_hash_kind,
                self.catalog_sha256,
            ),
        )
        for scenario_id, binding in self._binding().items():
            conn.execute(
                "INSERT INTO scene_catalog (scenario_id, input_sha256, sources_json,"
                " codebook_version, catalog_sha256) VALUES (?, ?, ?, ?, ?)",
                (
                    scenario_id,
                    binding["input_sha256"],
                    json.dumps(binding["sources"], ensure_ascii=False, sort_keys=True),
                    self.codebook_version,
                    self.catalog_sha256,
                ),
            )

    def _verify_binding(
        self, conn: sqlite3.Connection, stored: sqlite3.Row, *, codebook_path: Path | None
    ) -> None:
        if json.loads(stored["raters_json"]) != self.raters:
            raise ValueError("configured raters do not match the scene store binding")
        if stored["codebook_version"] != self.codebook_version:
            raise ValueError("codebook_version does not match the scene store binding")
        if stored["catalog_sha256"] != self.catalog_sha256:
            raise ValueError("scene catalog binding mismatch; source materials changed")
        if codebook_path is not None and stored["codebook_hash_kind"] == "file":
            digest, _ = _codebook_digest(self.codebook_version, codebook_path)
            if digest != stored["codebook_sha256"]:
                raise ValueError("codebook binding mismatch; codebook content changed")
        stored_rows = {
            row["scenario_id"]: row
            for row in conn.execute("SELECT * FROM scene_catalog")
        }
        expected = self._binding()
        if set(stored_rows) != set(expected):
            raise ValueError("scene catalog binding mismatch; scenario set changed")
        for scenario_id, binding in expected.items():
            row = stored_rows[scenario_id]
            sources = json.loads(row["sources_json"])
            if (
                row["input_sha256"] != binding["input_sha256"]
                or sources != binding["sources"]
                or row["codebook_version"] != self.codebook_version
                or row["catalog_sha256"] != self.catalog_sha256
            ):
                raise ValueError(
                    f"scene catalog binding mismatch for {scenario_id}; source materials changed"
                )

    @classmethod
    def open(cls, path: Path) -> "SceneRecordStore":
        """Reopen an existing store for export without reloading source text."""
        instance = cls.__new__(cls)
        instance.path = Path(path)
        if not instance.path.is_file():
            raise ValueError("scene store does not exist")
        with instance._connect() as conn:
            instance._check_or_create_schema(conn, existing=True)
            stored = conn.execute("SELECT * FROM scene_run WHERE singleton=1").fetchone()
            if stored is None:
                raise ValueError("scene store has no run binding")
            rows = list(conn.execute("SELECT * FROM scene_catalog ORDER BY scenario_id"))
        instance.raters = json.loads(stored["raters_json"])
        instance._validate_raters(instance.raters)
        instance.codebook_version = stored["codebook_version"]
        instance.codebook_sha256 = stored["codebook_sha256"]
        instance.codebook_hash_kind = stored["codebook_hash_kind"]
        instance.catalog_sha256 = stored["catalog_sha256"]
        instance._catalog = [
            {
                "scenario_id": row["scenario_id"],
                "sources": json.loads(row["sources_json"]),
            }
            for row in rows
        ]
        instance._catalog_by_id = {row["scenario_id"]: row for row in instance._catalog}
        return instance

    from_existing = open

    # -- database helpers -----------------------------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def _check_or_create_schema(self, conn: sqlite3.Connection, *, existing: bool) -> None:
        application_id = conn.execute("PRAGMA application_id").fetchone()[0]
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
        allowed = {"scene_run", "scene_catalog", "scene_revisions", "scene_adjudications"}
        if application_id not in (0, SCENE_APPLICATION_ID):
            raise ValueError("database is not a scene annotation store")
        if existing and application_id != SCENE_APPLICATION_ID:
            raise ValueError("database is not a scene annotation store")
        if tables and not tables <= allowed:
            raise ValueError("unexpected scene annotation store tables")
        if application_id == 0:
            conn.execute(f"PRAGMA application_id={SCENE_APPLICATION_ID}")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS scene_run (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                raters_json TEXT NOT NULL,
                codebook_version TEXT NOT NULL,
                codebook_sha256 TEXT NOT NULL,
                codebook_hash_kind TEXT NOT NULL,
                catalog_sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scene_catalog (
                scenario_id TEXT PRIMARY KEY,
                input_sha256 TEXT NOT NULL,
                sources_json TEXT NOT NULL,
                codebook_version TEXT NOT NULL,
                catalog_sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scene_revisions (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                revision_id TEXT NOT NULL UNIQUE,
                scenario_id TEXT NOT NULL REFERENCES scene_catalog(scenario_id),
                rater TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('draft', 'submission')),
                parent_revision_id TEXT,
                annotation_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                change_reason TEXT,
                codebook_version TEXT NOT NULL,
                input_sha256 TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_scene_revision_chain
                ON scene_revisions(scenario_id, rater, seq);
            CREATE TABLE IF NOT EXISTS scene_adjudications (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                adjudication_id TEXT NOT NULL UNIQUE,
                scenario_id TEXT NOT NULL REFERENCES scene_catalog(scenario_id),
                adjudicator TEXT NOT NULL,
                annotation_json TEXT NOT NULL,
                rationale TEXT NOT NULL,
                source_revision_ids_json TEXT NOT NULL,
                previous_adjudication_id TEXT,
                created_at TEXT NOT NULL,
                codebook_version TEXT NOT NULL,
                input_sha256 TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_scene_adjudication
                ON scene_adjudications(scenario_id, seq);
            CREATE TRIGGER IF NOT EXISTS scene_run_no_update
                BEFORE UPDATE ON scene_run BEGIN SELECT RAISE(ABORT, 'immutable scene run'); END;
            CREATE TRIGGER IF NOT EXISTS scene_run_no_delete
                BEFORE DELETE ON scene_run BEGIN SELECT RAISE(ABORT, 'immutable scene run'); END;
            CREATE TRIGGER IF NOT EXISTS scene_catalog_no_update
                BEFORE UPDATE ON scene_catalog BEGIN SELECT RAISE(ABORT, 'immutable scene catalog'); END;
            CREATE TRIGGER IF NOT EXISTS scene_catalog_no_delete
                BEFORE DELETE ON scene_catalog BEGIN SELECT RAISE(ABORT, 'immutable scene catalog'); END;
            CREATE TRIGGER IF NOT EXISTS scene_revisions_no_update
                BEFORE UPDATE ON scene_revisions BEGIN SELECT RAISE(ABORT, 'immutable scene revision'); END;
            CREATE TRIGGER IF NOT EXISTS scene_revisions_no_delete
                BEFORE DELETE ON scene_revisions BEGIN SELECT RAISE(ABORT, 'immutable scene revision'); END;
            CREATE TRIGGER IF NOT EXISTS scene_adjudications_no_update
                BEFORE UPDATE ON scene_adjudications BEGIN SELECT RAISE(ABORT, 'immutable adjudication'); END;
            CREATE TRIGGER IF NOT EXISTS scene_adjudications_no_delete
                BEFORE DELETE ON scene_adjudications BEGIN SELECT RAISE(ABORT, 'immutable adjudication'); END;
        """)

    def _require_rater(self, rater: object) -> str:
        if not isinstance(rater, str) or rater not in self.raters:
            raise ValueError(f"unknown rater: {rater!r}")
        return rater

    def _require_scenario(self, scenario_id: object) -> dict:
        if not isinstance(scenario_id, str) or scenario_id not in self._catalog_by_id:
            raise ValueError(f"unknown scenario: {scenario_id!r}")
        return self._catalog_by_id[scenario_id]

    @staticmethod
    def _latest_rows(conn: sqlite3.Connection, scenario_id: str) -> dict[str, sqlite3.Row]:
        latest: dict[str, sqlite3.Row] = {}
        for row in conn.execute(
            "SELECT * FROM scene_revisions WHERE scenario_id=? ORDER BY seq", (scenario_id,)
        ):
            latest[row["rater"]] = row
        return latest

    def _write_revision(
        self,
        kind: str,
        scenario_id: str,
        rater: str,
        annotation: dict,
        *,
        expected_revision: str | None,
        change_reason: str | None,
    ) -> dict:
        self._require_rater(rater)
        scenario = self._require_scenario(scenario_id)
        validator = validate_draft if kind == "draft" else validate_annotation
        validator(
            annotation, scenario_id=scenario_id, sources=scenario["sources"],
            codebook_version=self.codebook_version,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                latest = conn.execute(
                    "SELECT * FROM scene_revisions WHERE scenario_id=? AND rater=?"
                    " ORDER BY seq DESC LIMIT 1",
                    (scenario_id, rater),
                ).fetchone()
                if kind == "draft" and latest is not None and latest["kind"] == "submission":
                    raise ValueError("annotation already submitted; use submit to revise")
                if latest is None:
                    if expected_revision:
                        raise ValueError("stale revision for a new annotation; reload the record")
                    parent = None
                else:
                    if not expected_revision:
                        raise ValueError("expected_revision is required for an update")
                    if expected_revision != latest["revision_id"]:
                        raise ValueError("stale revision; reload the latest record")
                    if not _text(change_reason):
                        raise ValueError("change_reason is required for an annotation revision")
                    parent = latest["revision_id"]
                revision_id = uuid.uuid4().hex
                created_at = _now()
                conn.execute(
                    "INSERT INTO scene_revisions (revision_id, scenario_id, rater, kind,"
                    " parent_revision_id, annotation_json, created_at, change_reason,"
                    " codebook_version, input_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        revision_id,
                        scenario_id,
                        rater,
                        kind,
                        parent,
                        _canonical(annotation),
                        created_at,
                        change_reason.strip() if _text(change_reason) else None,
                        self.codebook_version,
                        scenario_input_hash(scenario["sources"]),
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM scene_revisions WHERE revision_id=?", (revision_id,)
                ).fetchone()
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return _revision_row(row)

    # -- public API -----------------------------------------------------------

    def save_draft(
        self,
        scenario_id: str,
        rater: str,
        annotation: dict,
        expected_revision: str | None = None,
        change_reason: str | None = None,
    ) -> dict:
        return self._write_revision(
            "draft", scenario_id, rater, annotation,
            expected_revision=expected_revision, change_reason=change_reason,
        )

    def submit(
        self,
        scenario_id: str,
        rater: str,
        annotation: dict,
        expected_revision: str | None = None,
        change_reason: str | None = None,
    ) -> dict:
        return self._write_revision(
            "submission", scenario_id, rater, annotation,
            expected_revision=expected_revision, change_reason=change_reason,
        )

    def _all_submitted(self, latest: dict[str, sqlite3.Row]) -> tuple[bool, list[str]]:
        missing = [
            rater for rater in self.raters
            if rater not in latest or latest[rater]["kind"] != "submission"
        ]
        return not missing, missing

    def _different_fields(self, values: dict[str, dict]) -> list[str]:
        fields = set().union(*(value.keys() for value in values.values()))
        return sorted(
            field for field in fields
            if len({_canonical(value.get(field)) for value in values.values()}) > 1
        )

    def _adjudication_is_stale(
        self, adjudication: dict | None, latest: dict[str, sqlite3.Row]
    ) -> bool:
        if adjudication is None:
            return False
        current = {
            rater: latest[rater]["revision_id"] for rater in self.raters if rater in latest
        }
        return any(
            current.get(rater) not in adjudication["source_revision_ids"]
            for rater in self.raters
        )

    def rater_view(self, rater: str) -> list[dict]:
        self._require_rater(rater)
        output = []
        with self._connect() as conn:
            for scenario in sorted(self._catalog_by_id.values(), key=lambda row: row["scenario_id"]):
                scenario_id = scenario["scenario_id"]
                latest = self._latest_rows(conn, scenario_id)
                all_submitted, missing = self._all_submitted(latest)
                own = latest.get(rater)
                peers = []
                adjudication = None
                comparison = None
                if all_submitted:
                    peers = [
                        _revision_row(latest[peer]) for peer in self.raters
                        if peer != rater and peer in latest
                    ]
                    adjudication = self._latest_adjudication(conn, scenario_id)
                    values = {
                        name: json.loads(latest[name]["annotation_json"])
                        for name in self.raters
                    }
                    comparison = {
                        "visible": True,
                        "raters": values,
                        "different_fields": self._different_fields(values),
                        "adjudication": adjudication,
                        "adjudication_stale": self._adjudication_is_stale(
                            adjudication, latest
                        ),
                    }
                output.append({
                    "scenario_id": scenario_id,
                    "input_sha256": scenario_input_hash(scenario["sources"]),
                    "sources": scenario["sources"],
                    "status": ("submitted" if own is not None and own["kind"] == "submission"
                               else "draft" if own is not None else "unannotated"),
                    "own": _revision_row(own) if own is not None else None,
                    "submitted": own is not None and own["kind"] == "submission",
                    "comparison_visible": all_submitted,
                    "missing_raters": missing,
                    "peers": peers,
                    "adjudication": adjudication,
                    "comparison": comparison,
                })
        return output

    @staticmethod
    def _latest_adjudication(conn: sqlite3.Connection, scenario_id: str) -> dict | None:
        row = conn.execute(
            "SELECT * FROM scene_adjudications WHERE scenario_id=? ORDER BY seq DESC LIMIT 1",
            (scenario_id,),
        ).fetchone()
        return _adjudication_row(row) if row is not None else None

    def comparison(self, scenario_id: str) -> dict:
        self._require_scenario(scenario_id)
        with self._connect() as conn:
            latest = self._latest_rows(conn, scenario_id)
            all_submitted, missing = self._all_submitted(latest)
            result = {
                "scenario_id": scenario_id,
                "visible": all_submitted,
                "all_submitted": all_submitted,
                "missing_raters": missing,
                "raters": {},
                "different_fields": [],
                "adjudication": None,
                "adjudication_stale": False,
            }
            if not all_submitted:
                return result
            values = {rater: json.loads(latest[rater]["annotation_json"]) for rater in self.raters}
            result["raters"] = values
            result["latest_revision_ids"] = {rater: latest[rater]["revision_id"] for rater in self.raters}
            result["different_fields"] = self._different_fields(values)
            adjudication = self._latest_adjudication(conn, scenario_id)
            result["adjudication"] = adjudication
            result["adjudication_stale"] = self._adjudication_is_stale(adjudication, latest)
            return result

    def adjudicate(
        self,
        scenario_id: str,
        adjudicator: str,
        annotation: dict,
        rationale: str,
    ) -> dict:
        self._require_rater(adjudicator)
        scenario = self._require_scenario(scenario_id)
        if not _text(rationale):
            raise ValueError("adjudication rationale must be non-empty text")
        validate_annotation(
            annotation, scenario_id=scenario_id, sources=scenario["sources"],
            codebook_version=self.codebook_version,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                latest = self._latest_rows(conn, scenario_id)
                all_submitted, missing = self._all_submitted(latest)
                if not all_submitted:
                    raise ValueError(
                        "all configured raters must have submitted before adjudication; "
                        f"missing: {', '.join(missing)}"
                    )
                previous = conn.execute(
                    "SELECT adjudication_id FROM scene_adjudications WHERE scenario_id=?"
                    " ORDER BY seq DESC LIMIT 1",
                    (scenario_id,),
                ).fetchone()
                adjudication_id = uuid.uuid4().hex
                source_ids = [latest[rater]["revision_id"] for rater in self.raters]
                conn.execute(
                    "INSERT INTO scene_adjudications (adjudication_id, scenario_id, adjudicator,"
                    " annotation_json, rationale, source_revision_ids_json,"
                    " previous_adjudication_id, created_at, codebook_version, input_sha256)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        adjudication_id,
                        scenario_id,
                        adjudicator,
                        _canonical(annotation),
                        rationale.strip(),
                        json.dumps(source_ids, ensure_ascii=False),
                        previous["adjudication_id"] if previous is not None else None,
                        _now(),
                        self.codebook_version,
                        scenario_input_hash(scenario["sources"]),
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM scene_adjudications WHERE adjudication_id=?",
                    (adjudication_id,),
                ).fetchone()
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return _adjudication_row(row)

    def agreement_report(self) -> dict:
        """Return pre-adjudication agreement once every rater submitted every scene."""
        if len(self.raters) < 2:
            return {"available": False, "reason": "agreement requires merged submissions from three raters",
                    "n_raters": len(self.raters), "total_scenarios": len(self._catalog_by_id)}
        matrices = {field: [] for field in ("label", "attack_type", "min_decision", "should_alert")}
        missing = []
        with self._connect() as conn:
            for scenario_id in sorted(self._catalog_by_id):
                latest = self._latest_rows(conn, scenario_id)
                complete, raters = self._all_submitted(latest)
                if not complete:
                    missing.append({"scenario_id": scenario_id, "raters": raters})
                    continue
                values = {
                    rater: json.loads(latest[rater]["annotation_json"])
                    for rater in self.raters
                }
                for field in matrices:
                    matrices[field].append([values[rater][field] for rater in self.raters])
        if missing:
            return {
                "available": False,
                "completed_scenarios": len(self._catalog_by_id) - len(missing),
                "total_scenarios": len(self._catalog_by_id),
                "missing": missing,
            }
        fields = {}
        for field, ratings in matrices.items():
            summary = fleiss_kappa(ratings)
            if field == "min_decision":
                summary.pop("category_proportions")
                summary.pop("expected_agreement")
                summary.pop("kappa")
                summary["measure"] = "exact agreement for ordinal values"
            else:
                summary["measure"] = "Fleiss nominal agreement"
            fields[field] = summary
        return {
            "available": True,
            "schema": "mtagentlens.scene-agreement/v1",
            "n_scenarios": len(self._catalog_by_id),
            "n_raters": len(self.raters),
            "fields": fields,
            "scope": (
                "Fleiss nominal agreement for label, attack_type and should_alert; "
                "exact agreement for ordinal min_decision; calculated before adjudication"
            ),
        }

    def import_merged_export(self, payload: dict) -> int:
        """Import verified independent chains into a fresh adjudication store."""
        if not isinstance(payload, dict) or payload.get("schema") != EXPORT_SCHEMA:
            raise ValueError("unsupported merged scene export")
        expected_digest = payload.get("merge_sha256")
        digest_body = {key: value for key, value in payload.items() if key != "merge_sha256"}
        digest_bytes = json.dumps(digest_body, sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=False, allow_nan=False).encode("utf-8")
        actual_digest = hashlib.sha256(digest_bytes).hexdigest()
        if expected_digest != actual_digest:
            raise ValueError("merged scene export content digest mismatch")
        if (payload.get("raters") != self.raters
                or payload.get("codebook_version") != self.codebook_version
                or payload.get("codebook_sha256") != self.codebook_sha256
                or payload.get("catalog_sha256") != self.catalog_sha256):
            raise ValueError("merged scene export binding does not match this store")
        scenarios = payload.get("scenarios")
        if not isinstance(scenarios, list) or {row.get("scenario_id") for row in scenarios} != set(self._catalog_by_id):
            raise ValueError("merged scene export task set does not match this store")
        imported = 0
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM scene_revisions LIMIT 1").fetchone():
                raise ValueError("adjudication store must be empty before importing submissions")
            for scenario in scenarios:
                scenario_id = scenario["scenario_id"]
                source = self._catalog_by_id[scenario_id]
                if (scenario.get("input_sha256") != scenario_input_hash(source["sources"])
                        or scenario.get("sources") != [
                            {"path": item["path"], "sha256": item["sha256"], "kind": item["kind"]}
                            for item in source["sources"]]):
                    raise ValueError(f"scene input digest mismatch: {scenario_id}")
                chains = scenario.get("rater_chains")
                if not isinstance(chains, list) or {item.get("rater") for item in chains} != set(self.raters):
                    raise ValueError(f"scene rater chains do not match configured raters: {scenario_id}")
                for chain in chains:
                    rater = self._require_rater(chain["rater"])
                    revisions = chain.get("revisions")
                    if not chain.get("submitted") or not isinstance(revisions, list) or not revisions:
                        raise ValueError(f"scene submission is missing: {scenario_id} / {rater}")
                    if revisions[-1].get("kind") != "submission":
                        raise ValueError(f"scene chain does not end in a submission: {scenario_id} / {rater}")
                    parent = None
                    for revision in revisions:
                        if (revision.get("scenario_id") != scenario_id or revision.get("rater") != rater
                                or revision.get("parent_revision_id") != parent
                                or revision.get("codebook_version") != self.codebook_version
                                or revision.get("input_sha256") != scenario_input_hash(source["sources"])):
                            raise ValueError(f"scene revision chain mismatch: {scenario_id} / {rater}")
                        if revision.get("kind") == "submission":
                            validate_annotation(revision.get("annotation"), scenario_id=scenario_id,
                                                sources=source["sources"], codebook_version=self.codebook_version)
                        else:
                            validate_draft(revision.get("annotation"), scenario_id=scenario_id,
                                           sources=source["sources"], codebook_version=self.codebook_version)
                        conn.execute(
                            "INSERT INTO scene_revisions (revision_id, scenario_id, rater, kind,"
                            "parent_revision_id, annotation_json, created_at, change_reason,"
                            "codebook_version, input_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (revision["revision_id"], scenario_id, rater, revision["kind"],
                             parent, _canonical(revision["annotation"]), revision["created_at"],
                             revision.get("change_reason"), self.codebook_version,
                             scenario_input_hash(source["sources"])),
                        )
                        parent = revision["revision_id"]
                        imported += 1
            conn.execute("COMMIT")
        return imported

    def export(self) -> dict:
        with self._connect() as conn:
            revisions: dict[str, list[dict]] = {}
            for row in conn.execute("SELECT * FROM scene_revisions ORDER BY seq"):
                revisions.setdefault(row["scenario_id"], []).append(_revision_row(row))
            adjudications: dict[str, list[dict]] = {}
            for row in conn.execute("SELECT * FROM scene_adjudications ORDER BY seq"):
                adjudications.setdefault(row["scenario_id"], []).append(_adjudication_row(row))
        scenarios = []
        for scenario in sorted(self._catalog_by_id.values(), key=lambda row: row["scenario_id"]):
            scenario_id = scenario["scenario_id"]
            rows = revisions.get(scenario_id, [])
            chains = []
            for rater in self.raters:
                chain = [row for row in rows if row["rater"] == rater]
                chains.append({
                    "rater": rater,
                    "submitted": bool(chain and chain[-1]["kind"] == "submission"),
                    "revisions": chain,
                })
            scenario_adjudications = adjudications.get(scenario_id, [])
            latest = scenario_adjudications[-1] if scenario_adjudications else None
            scenarios.append({
                "scenario_id": scenario_id,
                "input_sha256": scenario_input_hash(scenario["sources"]),
                "sources": [
                    {"path": source["path"], "sha256": source["sha256"], "kind": source["kind"]}
                    for source in scenario["sources"]
                ],
                "rater_chains": chains,
                "adjudication": latest,
                "adjudications": scenario_adjudications,
            })
        payload = {
            "schema": EXPORT_SCHEMA,
            "exported_at": _now(),
            "codebook_version": self.codebook_version,
            "codebook_sha256": self.codebook_sha256,
            "codebook_hash_kind": self.codebook_hash_kind,
            "catalog_sha256": self.catalog_sha256,
            "raters": list(self.raters),
            "agreement": self.agreement_report(),
            "paper_metrics_available": False,
            "scenarios": scenarios,
        }
        digest_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=False, allow_nan=False).encode("utf-8")
        payload["export_sha256"] = hashlib.sha256(digest_bytes).hexdigest()
        return payload
