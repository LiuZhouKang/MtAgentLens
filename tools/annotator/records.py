"""Run-bound local revisions; declared rater methods are not authentication.

This store deliberately does not promote annotations to scientific gold.
Bundle sources, actor registries and human participation require external audit.
The old v1 JSON annotations remain untouched.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from tools.annotator.evidence import load_bundle


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_review(review: dict | None, annotator: str) -> None:
    if review is None:
        return
    if not isinstance(review, dict) or set(review) != {"reviewed_revisions", "rationale"}:
        raise ValueError("review must contain exactly reviewed_revisions and rationale")
    reviewed = review["reviewed_revisions"]
    if not isinstance(reviewed, dict) or len(reviewed) < 2:
        raise ValueError("review must cover at least two distinct raters")
    for rater, revision in reviewed.items():
        if not _text(rater) or not _text(revision):
            raise ValueError("review rater and revision ids must be non-empty strings")
        if rater == annotator:
            raise ValueError("an adjudicator cannot review their own revision")
    if not _text(review["rationale"]):
        raise ValueError("review rationale must be non-empty text")


def _validate_review_source(conn: sqlite3.Connection, task_id: str, rater: str,
                            revision_id: str) -> None:
    row = conn.execute(
        "SELECT record_json FROM bundle_revisions WHERE task_id=? AND annotator=? "
        "ORDER BY seq DESC LIMIT 1", (task_id, rater),
    ).fetchone()
    if row is None:
        raise ValueError("review revision is missing")
    source = json.loads(row[0])
    if source.get("revision_id") != revision_id:
        raise ValueError("stale review revision; reload the reviewed record")
    if source.get("method") == "model_proposal":
        raise ValueError("review revision must not be a model proposal")
    if "review" in source:
        raise ValueError("review revision must be independently annotated")


def _validate_annotation(annotation: dict, bundle: dict) -> None:
    fields = {"attempted", "executed", "succeeded", "identities", "roots", "notes"}
    if not isinstance(annotation, dict) or set(annotation) != fields:
        raise ValueError("annotation must contain exactly the supported fields")
    events = {event["event_id"] for event in bundle["events"]}
    actors = set(bundle["actors"])

    def references(value: object, *, required: bool) -> None:
        if not isinstance(value, list) or any(not isinstance(ref, str) for ref in value):
            raise ValueError("evidence_refs must be a list of event ids")
        if len(set(value)) != len(value) or not set(value) <= events:
            raise ValueError("duplicate or unknown evidence reference")
        if required and not value:
            raise ValueError("known judgments require evidence references")

    for field in ("attempted", "executed", "succeeded"):
        judgment = annotation[field]
        if not isinstance(judgment, dict) or set(judgment) != {"value", "evidence_refs"}:
            raise ValueError(f"invalid {field} judgment")
        if judgment["value"] not in ("yes", "no", "unknown"):
            raise ValueError(f"invalid {field} state")
        references(judgment["evidence_refs"], required=judgment["value"] != "unknown")
    attempted, executed, succeeded = (annotation[key]["value"]
                                     for key in ("attempted", "executed", "succeeded"))
    # Monotone chain attempted >= executed >= succeeded (yes > unknown > no): a
    # downstream judgement may never outrun its precondition, so success proves
    # execution, execution proves an attempt, and a definite "no attempt" forces every
    # downstream judgement to "no". `unknown` is always stated, never inferred.
    chain = {"no": 0, "unknown": 1, "yes": 2}
    order = (chain[attempted], chain[executed], chain[succeeded])
    if order[0] < order[1] or order[1] < order[2]:
        raise ValueError(
            "judgments must form a monotone attempted >= executed >= succeeded chain")
    identities = annotation["identities"]
    if not isinstance(identities, dict) or set(identities) != events:
        raise ValueError("identities must retain the complete event set")
    for actor in identities.values():
        if actor is not None and (not isinstance(actor, str) or actor not in actors):
            raise ValueError("identity must reference a registered actor or be unknown")
    roots = annotation["roots"]
    if not isinstance(roots, dict) or set(roots) != {"actors", "evidence_refs"}:
        raise ValueError("invalid roots judgment")
    chosen = roots["actors"]
    if chosen is not None:
        if not isinstance(chosen, list) or any(not isinstance(actor, str) for actor in chosen):
            raise ValueError("root actors must be a list or unknown")
        if len(set(chosen)) != len(chosen) or not set(chosen) <= actors:
            raise ValueError("duplicate or unknown root actor")
    references(roots["evidence_refs"], required=chosen is not None)
    # The empty root set is a positive "no actor is the root cause" claim, not a shrug:
    # it may only accompany a definite no-attempt, which may not leave roots unknown.
    if attempted == "no" and chosen != []:
        raise ValueError("attempted=no requires the empty root set")
    if attempted == "yes" and chosen == []:
        raise ValueError("attempted=yes requires root actors or unknown")
    if not isinstance(annotation["notes"], str):
        raise ValueError("notes must be a string")


class RecordStore:
    """Append-only tasks and revisions with cross-connection parent checks."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.path.exists() and self.path.stat().st_size > 0
        with closing(self._connect()) as conn:
            application_id = conn.execute("PRAGMA application_id").fetchone()[0]
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            if application_id not in (0, 0x4D54414E) or (existing and application_id != 0x4D54414E):
                raise ValueError("database is not an annotation record store")
            allowed_tables = {"bundle_tasks", "bundle_revisions", "review_attestations"}
            if tables and not tables <= allowed_tables:
                raise ValueError("unexpected record store tables")
            conn.executescript("""
                PRAGMA application_id=1297367374;
                CREATE TABLE IF NOT EXISTS bundle_tasks (
                    task_id TEXT PRIMARY KEY,
                    binding_json TEXT NOT NULL UNIQUE,
                    bundle_path TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bundle_revisions (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES bundle_tasks(task_id),
                    annotator TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_bundle_rater
                    ON bundle_revisions(task_id, annotator, seq);
                CREATE TRIGGER IF NOT EXISTS bundle_tasks_no_update
                    BEFORE UPDATE ON bundle_tasks BEGIN SELECT RAISE(ABORT, 'immutable task'); END;
                CREATE TRIGGER IF NOT EXISTS bundle_tasks_no_delete
                    BEFORE DELETE ON bundle_tasks BEGIN SELECT RAISE(ABORT, 'immutable task'); END;
                CREATE TRIGGER IF NOT EXISTS bundle_revisions_no_update
                    BEFORE UPDATE ON bundle_revisions BEGIN SELECT RAISE(ABORT, 'immutable revision'); END;
                CREATE TRIGGER IF NOT EXISTS bundle_revisions_no_delete
                    BEFORE DELETE ON bundle_revisions BEGIN SELECT RAISE(ABORT, 'immutable revision'); END;
                CREATE TABLE IF NOT EXISTS review_attestations (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES bundle_tasks(task_id),
                    reviewer TEXT NOT NULL,
                    proposal_revision_id TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    UNIQUE(task_id, reviewer, proposal_revision_id)
                );
                CREATE INDEX IF NOT EXISTS idx_attestation_task
                    ON review_attestations(task_id, seq);
                CREATE TRIGGER IF NOT EXISTS review_attestations_no_update
                    BEFORE UPDATE ON review_attestations BEGIN SELECT RAISE(ABORT, 'immutable attestation'); END;
                CREATE TRIGGER IF NOT EXISTS review_attestations_no_delete
                    BEFORE DELETE ON review_attestations BEGIN SELECT RAISE(ABORT, 'immutable attestation'); END;
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _task(self, task_id: str) -> sqlite3.Row:
        with closing(self._connect()) as conn:
            task = conn.execute("SELECT * FROM bundle_tasks WHERE task_id=?", (task_id,)).fetchone()
        if task is None:
            raise ValueError("unknown task")
        return task

    def bundle_path(self, task_id: str) -> Path:
        """Registered location of one frozen evidence bundle."""
        return Path(self._task(task_id)["bundle_path"])

    def list_tasks(self, annotator: str) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT t.task_id, EXISTS(SELECT 1 FROM bundle_revisions r "
                "WHERE r.task_id=t.task_id AND r.annotator=?) AS has_annotation "
                "FROM bundle_tasks t ORDER BY t.task_id", (annotator,),
            ).fetchall()
        return [{"task_id": row["task_id"], "has_annotation": bool(row["has_annotation"])}
                for row in rows]

    def read_task(self, task_id: str, *, annotators: tuple[str, ...]) -> dict:
        task = self._task(task_id)
        if annotators:
            placeholders = ",".join("?" * len(annotators))
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    f"SELECT record_json FROM bundle_revisions WHERE task_id=? "
                    f"AND annotator IN ({placeholders}) ORDER BY seq",
                    (task_id, *annotators),
                ).fetchall()
            revisions = [json.loads(row[0]) for row in rows]
        else:
            revisions = []
        return {"bundle": self._bundle(task), "revisions": revisions}

    def latest_model_proposal(self, task_id: str) -> dict | None:
        proposals = self.latest_model_proposals(task_id)
        if not proposals:
            return None
        return max(proposals.values(), key=lambda record: record["saved_at"])

    def latest_model_proposals(self, task_id: str) -> dict[str, dict]:
        """Latest model proposal per annotator, preserving the audit trail."""
        self._task(task_id)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT record_json FROM bundle_revisions WHERE task_id=? ORDER BY seq",
                (task_id,),
            ).fetchall()
        latest: dict[str, dict] = {}
        for row in rows:
            record = json.loads(row[0])
            if record.get("method") == "model_proposal":
                latest[record["annotator"]] = record
        return latest

    def read_evidence(self, task_id: str, event_id: str, *, limit: int | None = None) -> dict:
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("limit must be a non-negative integer")
        task = self._task(task_id)
        bundle = self._bundle(task)
        event = next((item for item in bundle["events"] if item["event_id"] == event_id), None)
        if event is None:
            raise ValueError("unknown evidence event")
        return self._read_evidence_event(task, bundle, event, limit)

    def read_evidence_many(self, task_id: str, *, limit: int | None = None) -> list[dict]:
        """Read every event from one frozen bundle without reloading it per row."""
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("limit must be a non-negative integer")
        task = self._task(task_id)
        bundle = self._bundle(task)
        return [self._read_evidence_event(task, bundle, event, limit)
                for event in bundle["events"]]

    @staticmethod
    def _read_evidence_event(task: sqlite3.Row, bundle: dict, event: dict,
                             limit: int | None) -> dict:
        length = event["byte_length"]
        wanted = length if limit is None else min(limit, length)
        path = Path(task["bundle_path"]) / bundle["sources"][event["source_id"]]["path"]
        digest, chunks, remaining = hashlib.sha256(), [], length
        collected = 0
        with path.open("rb") as stream:
            stream.seek(event["byte_offset"])
            while remaining:
                chunk = stream.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise ValueError("evidence source was truncated")
                digest.update(chunk)
                remaining -= len(chunk)
                prefix = chunk[:max(0, wanted - collected)]
                if prefix:
                    chunks.append(prefix)
                    collected += len(prefix)
        if digest.hexdigest() != event["record_sha256"]:
            raise ValueError("evidence record checksum mismatch")
        return {"event": event, "bytes": b"".join(chunks), "byte_length": length,
                "source_kind": bundle["sources"][event["source_id"]]["kind"],
                "binding": bundle["binding"]}

    def save_attestation(self, task_id: str, reviewer: str, proposal_revision_id: str, *,
                         statement: str) -> dict:
        """Record a typed human review statement; never a human annotation label."""
        if not _text(reviewer) or len(reviewer) > 128:
            raise ValueError("reviewer must be a non-empty identifier of at most 128 characters")
        if not _text(proposal_revision_id):
            raise ValueError("proposal revision must be a non-empty identifier")
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError("statement must be non-empty text")
        task = self._task(task_id)
        bundle = self._bundle(task)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT record_json FROM bundle_revisions WHERE task_id=? ORDER BY seq",
                (task_id,),
            ).fetchall()
            proposals = [json.loads(row[0]) for row in rows
                         if json.loads(row[0]).get("method") == "model_proposal"]
            match = next((record for record in proposals
                          if record.get("revision_id") == proposal_revision_id), None)
            if match is None:
                raise ValueError("proposal revision does not exist for this task")
            latest_for_annotator = next(
                (record for record in reversed(proposals)
                 if record.get("annotator") == match.get("annotator")), None)
            if latest_for_annotator is None or (
                    latest_for_annotator.get("revision_id") != proposal_revision_id):
                raise ValueError("stale model proposal revision; reload the latest draft")
            if reviewer == match.get("annotator"):
                raise ValueError("a proposal annotator cannot review their own proposal")
            record = {
                "schema": "mtagentlens.review-attestation/v1",
                "attestation_id": uuid.uuid4().hex,
                "task_id": task_id,
                "reviewer": reviewer,
                "reviewer_type": "human_statement",
                "proposal_revision_id": proposal_revision_id,
                "proposal_annotator": match.get("annotator"),
                "evidence_bundle_hash": task_id,
                "codebook_version": bundle["binding"].get("codebook_version"),
                "statement": statement.strip(),
                "signature": {"method": "typed_statement", "cryptographic": False},
                "created_at": datetime.now(timezone.utc).isoformat(),
                "paper_metrics_available": False,
                "note": ("Typed reviewer statement. It is not an independent annotation, "
                         "not a cryptographic signature, and does not authenticate identity."),
            }
            try:
                conn.execute(
                    "INSERT INTO review_attestations(task_id, reviewer, proposal_revision_id, record_json) "
                    "VALUES (?, ?, ?, ?)",
                    (task_id, reviewer, proposal_revision_id, _json(record)),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("attestation already exists for this reviewer and proposal") from exc
        return record

    def list_attestations(self, task_id: str | None = None) -> list[dict]:
        with closing(self._connect()) as conn:
            if task_id is None:
                rows = conn.execute(
                    "SELECT record_json FROM review_attestations ORDER BY seq").fetchall()
            else:
                rows = conn.execute(
                    "SELECT record_json FROM review_attestations WHERE task_id=? ORDER BY seq",
                    (task_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def register_bundle(self, root: Path) -> str:
        root = Path(root).resolve()
        bundle = load_bundle(root)
        task_id = bundle["evidence_bundle_hash"]
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM bundle_tasks WHERE task_id=?", (task_id,)).fetchone():
                return task_id
            try:
                conn.execute("INSERT INTO bundle_tasks VALUES (?, ?, ?)",
                             (task_id, _json(bundle["binding"]), str(root)))
            except sqlite3.IntegrityError as exc:
                raise ValueError("run binding already registered with different evidence") from exc
        return task_id

    @staticmethod
    def _bundle(task: sqlite3.Row) -> dict:
        bundle = load_bundle(Path(task["bundle_path"]))
        if (bundle["evidence_bundle_hash"] != task["task_id"]
                or _json(bundle["binding"]) != task["binding_json"]):
            raise ValueError("registered evidence bundle binding changed")
        return bundle

    def save(self, task_id: str, annotator: str, annotation: dict, *,
             method: str, expected_revision: str | None, review: dict | None = None) -> dict:
        if not _text(annotator) or len(annotator) > 128:
            raise ValueError("annotator must be a non-empty identifier of at most 128 characters")
        if method not in ("human", "controlled_execution", "model_proposal"):
            raise ValueError("method must declare human, controlled_execution or model_proposal")
        if expected_revision is not None and not _text(expected_revision):
            raise ValueError("expected revision must be an identifier or None")
        _validate_review(review, annotator)
        task = self._task(task_id)
        # Tasks are immutable. Audit files before taking the writer lock; the
        # parent comparison below still happens inside the write transaction.
        bundle = self._bundle(task)
        _validate_annotation(annotation, bundle)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT record_json FROM bundle_revisions WHERE task_id=? AND annotator=? "
                "ORDER BY seq DESC LIMIT 1", (task_id, annotator),
            ).fetchone()
            parent = json.loads(previous[0])["revision_id"] if previous else None
            if expected_revision != parent:
                raise ValueError("stale parent revision; reload the current record")
            if review is not None:
                for rater, revision_id in review["reviewed_revisions"].items():
                    _validate_review_source(conn, task_id, rater, revision_id)
            record = {
                "schema": "mtagentlens.annotation/v2", "task_id": task_id,
                "binding": bundle["binding"], "evidence_bundle_hash": task_id,
                "annotator": annotator, "method": method,
                "revision_id": uuid.uuid4().hex, "parent_revision_id": parent,
                "saved_at": datetime.now(timezone.utc).isoformat(), "annotation": annotation,
            }
            if review is not None:
                record["review"] = review
            conn.execute("INSERT INTO bundle_revisions(task_id, annotator, record_json) VALUES (?, ?, ?)",
                         (task_id, annotator, _json(record)))
        return record

    def import_human_revisions(self, task_id: str, records: list[dict]) -> int:
        """Import a validated offline human revision chain without rewriting IDs."""
        task = self._task(task_id)
        bundle = self._bundle(task)
        if not isinstance(records, list) or not records:
            raise ValueError("at least one human revision is required")
        parent = None
        annotator = None
        normalized = []
        revision_ids = set()
        for record in records:
            if (not isinstance(record, dict) or record.get("schema") != "mtagentlens.annotation/v2"
                    or record.get("task_id") != task_id
                    or record.get("evidence_bundle_hash") != task_id
                    or record.get("binding") != bundle["binding"]
                    or record.get("method") != "human" or "review" in record):
                raise ValueError("offline source is not an independent human annotation chain")
            if annotator is None:
                annotator = record.get("annotator")
            if record.get("annotator") != annotator or record.get("parent_revision_id") != parent:
                raise ValueError("offline human revision chain is inconsistent")
            revision_id = record.get("revision_id")
            if not _text(revision_id) or not _text(record.get("saved_at")):
                raise ValueError("offline revision id and timestamp are required")
            if revision_id in revision_ids:
                raise ValueError("duplicate revision id in offline human chain")
            _validate_annotation(record.get("annotation"), bundle)
            normalized.append(record)
            revision_ids.add(revision_id)
            parent = revision_id
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM bundle_revisions WHERE task_id=? AND annotator=?",
                            (task_id, annotator)).fetchone():
                raise ValueError("annotator already has revisions for this task")
            existing_ids = {json.loads(row[0])["revision_id"] for row in conn.execute(
                "SELECT record_json FROM bundle_revisions")}
            if any(row["revision_id"] in existing_ids for row in normalized):
                raise ValueError("duplicate imported revision id")
            conn.executemany(
                "INSERT INTO bundle_revisions(task_id, annotator, record_json) VALUES (?, ?, ?)",
                [(task_id, annotator, _json(row)) for row in normalized],
            )
        return len(normalized)

    def export(self) -> dict:
        tasks = []
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN")
            snapshot = []
            for task in conn.execute("SELECT * FROM bundle_tasks ORDER BY task_id").fetchall():
                revisions = [json.loads(row[0]) for row in conn.execute(
                    "SELECT record_json FROM bundle_revisions WHERE task_id=? ORDER BY seq",
                    (task["task_id"],),
                )]
                snapshot.append((task, revisions))
        # Hashing large raw sources must not keep the SQLite read snapshot open.
        for task, revisions in snapshot:
            bundle = self._bundle(task)
            tasks.append({"task_id": task["task_id"], "binding": bundle["binding"],
                          "evidence_bundle_hash": task["task_id"],
                          "evidence_manifest": bundle,
                          "status": "annotated" if revisions else "unannotated",
                          "revisions": revisions})
        export = {"schema": "mtagentlens.annotation-export/v2", "paper_metrics_available": False,
                  "tasks": tasks}
        canonical = json.dumps({"tasks": tasks}, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False).encode("utf-8")
        export["export_sha256"] = hashlib.sha256(canonical).hexdigest()
        return export
