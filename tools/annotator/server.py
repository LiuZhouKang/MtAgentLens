#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Legacy local annotation viewer (Python stdlib + SQLite).

Hidden verdicts do not make the detector-derived graph independent truth.
See README.md for the limitations of this historical annotation format.

Run:
    python3 tools/annotator/server.py --dbs <dir-or-db> [--port 7410]

The server binds 127.0.0.1 only and never serves files outside its static dir.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import threading
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

TOOL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_ROOT.parents[1]
STATIC_ROOT = TOOL_ROOT / "static"
CODEBOOK_VERSION = "mtagentlens-codebook/v1"

ANNOTATION_FIELDS = (
    "db_sha256",
    "label",
    "actionable",
    "root_cause_agent",
    "entry_point",
    "severity",
    "confidence",
    "notes",
    "malicious_actions",
    "chain_edges",
)

_CATEGORICAL_FIELDS = ("label", "actionable", "root_cause_agent", "entry_point", "severity")
_SET_FIELDS = ("malicious_actions", "chain_edges")
_VIEW_TABLES = (
    "graph_nodes", "graph_edges", "intents", "actions", "correlation_evidence",
    "responsibility_claims", "responsibility_alerts", "graph_snapshots",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_run_id(run_id: str) -> str:
    if (
        not run_id
        or "/" in run_id
        or "\\" in run_id
        or ".." in run_id
        or run_id.startswith(".")
    ):
        raise ValueError(f"invalid run id: {run_id!r}")
    return run_id


class TaskStore:
    """Discover run databases and load a blind/revealed view of each."""

    def __init__(self, db_root: Path):
        self.db_root = db_root.resolve()
        self._lock = threading.Lock()

    def _discover(self) -> dict[str, Path]:
        if self.db_root.is_file():
            return {self.db_root.stem: self.db_root}
        found: dict[str, Path] = {}
        for path in sorted(self.db_root.rglob("*.db")):
            if path.suffix != ".db" or not path.is_file():
                continue
            relative = path.relative_to(self.db_root).as_posix()
            key = f"{path.stem}-{hashlib.sha256(relative.encode()).hexdigest()[:20]}"
            found[key] = path
        return found

    def tasks(self) -> list[dict]:
        tasks = []
        for run_id, db_path in self._discover().items():
            meta = self._scenario_meta(db_path)
            counts = self._counts(db_path)
            counts = {key: value for key, value in counts.items() if key in ("intents", "actions")}
            tasks.append(
                {
                    "run_id": run_id,
                    "scenario_id": meta.get("scenario_id") or run_id,
                    "db_path": str(db_path.relative_to(self.db_root))
                    if db_path.is_relative_to(self.db_root)
                    else str(db_path),
                    "counts": counts,
                }
            )
        return tasks

    def _connect(self, db_path: Path) -> sqlite3.Connection:
        db_path = db_path.resolve()
        # The recorded hash covers the main file, not uncheckpointed WAL pages.
        wal = Path(str(db_path) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise ValueError("uncheckpointed WAL: use a frozen single-run database")
        conn = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            tables = self._tables(conn)
            if "runs" not in tables:
                raise ValueError("annotation requires a single run in the runs table")
            runs = conn.execute("SELECT run_id FROM runs LIMIT 2").fetchall()
            if len(runs) != 1 or not isinstance(runs[0][0], str) or not runs[0][0].strip():
                raise ValueError("annotation requires exactly one non-empty single run")
            for table in _VIEW_TABLES:
                if table not in tables:
                    continue
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                # Legacy tables without run_id retain their historical format.
                if "run_id" in columns and conn.execute(
                    f"SELECT 1 FROM {table} WHERE run_id IS NULL OR run_id != ? LIMIT 1",
                    (runs[0][0],),
                ).fetchone():
                    raise ValueError(f"invalid run binding in {table}")
        except Exception:
            conn.close()
            raise
        return conn

    def _tables(self, conn: sqlite3.Connection) -> set[str]:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def _counts(self, db_path: Path) -> dict:
        conn = self._connect(db_path)
        counts = {}
        try:
            tables = self._tables(conn)
            for table in _VIEW_TABLES:
                if table in tables:
                    counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()
        return counts

    def _scenario_meta(self, db_path: Path) -> dict:
        for parent in (db_path.parent, db_path.parent.parent):
            marker = parent / ".scenario_id"
            if marker.exists():
                try:
                    return {"scenario_id": marker.read_text().strip()}
                except OSError:
                    pass
            summary = parent / "summary.json"
            if summary.exists():
                try:
                    payload = json.loads(summary.read_text(encoding="utf-8"))
                    return {
                        "scenario_id": payload.get("scenario_id")
                        or payload.get("run_id")
                        or "",
                        "state": payload.get("state") or payload.get("run_status"),
                    }
                except (OSError, ValueError):
                    pass
        return {}

    def load(self, run_id: str, reveal: bool) -> dict:
        validate_run_id(run_id)
        db_path = self._discover().get(run_id)
        if db_path is None:
            raise KeyError(run_id)
        conn = self._connect(db_path)
        try:
            tables = self._tables(conn)
            runs = [dict(row) for row in conn.execute("SELECT * FROM runs")] if "runs" in tables else []
            agents = self._agents(conn, tables)
            intents = self._intents(conn, tables)
            actions = self._actions(conn, tables)
            evidence = self._evidence(conn, tables)
            claims = self._claims(conn, tables)
            alerts = self._alerts(conn, tables, reveal)
            nodes, edges = self._graph(conn, tables)
            metrics = self._metrics(conn, tables)
        finally:
            conn.close()

        if not reveal:
            # Build a server-side whitelist. Hidden model values must never be
            # sent in the response, even when the UI does not render them.
            runs = [{k: row[k] for k in ("run_id", "started_at_ns", "ended_at_ns") if k in row} for row in runs]
            claims, alerts, evidence, edges, metrics = [], [], [], [], None
            nodes = [n for n in nodes if n.get("node_type") in ("Agent", "Resource", "Intent", "Action")]

        return {
            "run_id": run_id,
            "scenario_id": self._scenario_meta(db_path).get("scenario_id") or run_id,
            "db_sha256": sha256_file(db_path),
            "runs": runs,
            "agents": agents,
            "intents": intents,
            "actions": actions,
            "evidence": evidence,
            "claims": claims,
            "alerts": alerts,
            "graph": {"nodes": nodes, "edges": edges, "metrics": metrics},
            "model_revealed": reveal,
            "codebook_version": CODEBOOK_VERSION,
        }

    def _agents(self, conn, tables) -> list[dict]:
        if "graph_nodes" not in tables:
            return []
        rows = conn.execute(
            "SELECT node_id, agent_id, timestamp_ns FROM graph_nodes "
            "WHERE node_type = 'Agent' AND agent_id IS NOT NULL"
        )
        return [dict(row) for row in rows]

    def _intents(self, conn, tables) -> list[dict]:
        if "intents" not in tables:
            return []
        rows = conn.execute(
            "SELECT intent_id, pid, tool_name, tool_args_json, intent_class, "
            "parse_state, timestamp_ns FROM intents ORDER BY timestamp_ns"
        )
        return [dict(row) for row in rows]

    def _actions(self, conn, tables) -> list[dict]:
        if "actions" not in tables:
            return []
        rows = conn.execute(
            "SELECT action_id, pid, action_type, normalized_args_json, "
            "derivation_mode, timestamp_ns FROM actions ORDER BY timestamp_ns"
        )
        return [dict(row) for row in rows]

    def _evidence(self, conn, tables) -> list[dict]:
        if "correlation_evidence" not in tables:
            return []
        rows = conn.execute(
            "SELECT evidence_id, intent_id, action_id, match_level, confidence, timestamp_ns "
            "FROM correlation_evidence ORDER BY timestamp_ns"
        )
        return [dict(row) for row in rows]

    def _claims(self, conn, tables) -> list[dict]:
        if "responsibility_claims" not in tables:
            return []
        rows = conn.execute(
            "SELECT claim_id, claim_type, subject_agent_id, boundary_id, claim_state, "
            "severity, confidence, evidence_refs_json, phase FROM responsibility_claims"
        )
        return [dict(row) for row in rows]

    def _alerts(self, conn, tables, reveal: bool) -> list[dict]:
        if "responsibility_alerts" not in tables:
            return []
        rows = conn.execute(
            "SELECT alert_id, timestamp_ns, decision, severity, confidence, title, summary, "
            "primary_agent_id, engine_section_json FROM responsibility_alerts "
            "ORDER BY timestamp_ns"
        )
        alerts = [dict(row) for row in rows]
        if reveal:
            return alerts
        # Blind view: the annotator must not anchor on the model's verdict.
        for alert in alerts:
            alert.pop("decision", None)
            alert.pop("severity", None)
            alert.pop("confidence", None)
            alert.pop("engine_section_json", None)
        return alerts

    def _graph(self, conn, tables) -> tuple[list[dict], list[dict]]:
        if "graph_nodes" not in tables or "graph_edges" not in tables:
            return [], []
        nodes = [
            dict(row)
            for row in conn.execute(
                "SELECT node_id, node_type, agent_id, resource_json, timestamp_ns FROM graph_nodes"
            )
        ]
        edges = [
            dict(row)
            for row in conn.execute(
                "SELECT edge_id, edge_type, source_node_id, target_node_id, confidence, phase "
                "FROM graph_edges"
            )
        ]
        return nodes, edges

    def _metrics(self, conn, tables) -> dict | None:
        if "graph_snapshots" not in tables:
            return None
        row = conn.execute(
            "SELECT metrics_json FROM graph_snapshots ORDER BY timestamp_ns DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except ValueError:
            return None


class AnnotationStore:
    """One JSON file per (run, annotator); immutable history via timestamps."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, run_id: str, annotator: str) -> Path:
        validate_run_id(run_id)
        validate_annotator(annotator)
        return self.root / run_id / f"{annotator}.json"

    def save(self, run_id: str, annotator: str, annotation: dict) -> dict:
        payload = {
            "schema": "mtagentlens.annotation/v1",
            "codebook_version": CODEBOOK_VERSION,
            "run_id": run_id,
            "annotator": annotator,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "revision_id": uuid.uuid4().hex,
        }
        for field in ANNOTATION_FIELDS:
            if field in annotation:
                payload[field] = annotation[field]
        path = self._path(run_id, annotator)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            history = path.parent / "_history" / annotator
            history.mkdir(parents=True, exist_ok=True)
            if path.exists():
                previous = json.loads(path.read_text(encoding="utf-8"))
                old = history / (previous.get("revision_id", "legacy") + ".json")
                if not old.exists():
                    old.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
            # Exclusive creation keeps every revision immutable. The per-rater
            # file is only a replaceable pointer to the current revision.
            revision = history / (payload["revision_id"] + ".json")
            with revision.open("x", encoding="utf-8") as handle:
                handle.write(serialized)
            temporary = path.with_name(path.name + "." + payload["revision_id"] + ".tmp")
            temporary.write_text(serialized, encoding="utf-8")
            temporary.replace(path)
        return payload

    def history(self, run_id: str, annotator: str) -> list[dict]:
        path = self._path(run_id, annotator)
        return sorted((json.loads(p.read_text(encoding="utf-8")) for p in (path.parent / "_history" / annotator).glob("*.json")),
            key=lambda row: (row["saved_at"], row.get("revision_id", "")))

    def all(self) -> list[dict]:
        records = []
        for path in sorted(self.root.glob("*/*.json")):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return records

    def for_run(self, run_id: str) -> list[dict]:
        return [record for record in self.all() if record.get("run_id") == run_id]


def validate_annotator(annotator: str) -> str:
    if not annotator or len(annotator) > 64 or any(ch in annotator for ch in "/\\."):
        raise ValueError(f"invalid annotator: {annotator!r}")
    return annotator


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    """Cohen's kappa for two raters on categorical labels."""
    if not pairs:
        return None
    n = len(pairs)
    agree = sum(1 for a, b in pairs if a == b) / n
    counts_a = Counter(a for a, _ in pairs)
    counts_b = Counter(b for _, b in pairs)
    expected = sum((counts_a[label] / n) * (counts_b[label] / n) for label in set(counts_a) | set(counts_b))
    if expected >= 1.0:
        return 1.0 if agree == 1.0 else None
    return (agree - expected) / (1.0 - expected)


def fleiss_kappa(ratings: list[list[str]]) -> float | None:
    """Fleiss' kappa for N items rated by a variable number of raters."""
    if not ratings:
        return None
    categories = sorted({value for row in ratings for value in row})
    if len(categories) < 2:
        return 1.0
    total = sum(len(row) for row in ratings)
    if total == 0:
        return None
    p = {category: 0.0 for category in categories}
    p_agree = 0.0
    for row in ratings:
        if not row:
            continue
        counts = Counter(row)
        n = len(row)
        p_agree += (sum(count * count for count in counts.values()) - n) / (n * (n - 1)) if n > 1 else 0.0
        for category, count in counts.items():
            p[category] += count
    p_agree /= len(ratings)
    p_expected = sum((value / total) ** 2 for value in p.values())
    if p_expected >= 1.0:
        return None
    return (p_agree - p_expected) / (1.0 - p_expected)


def jaccard(a: list[str], b: list[str]) -> float | None:
    set_a, set_b = set(a), set(b)
    if not set_a and not set_b:
        return None
    return len(set_a & set_b) / len(set_a | set_b)


def agreement(records: list[dict], run_ids: list[str]) -> dict:
    by_run: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_run[record.get("run_id")].append(record)

    report: dict = {"runs": {}, "summary": {}}
    field_pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    set_scores: dict[str, list[float]] = defaultdict(list)

    for run_id in run_ids:
        annotations = by_run.get(run_id, [])
        run_report: dict = {"annotators": [a.get("annotator") for a in annotations], "fields": {}}
        if len(annotations) >= 2:
            for field in _CATEGORICAL_FIELDS:
                values = [str(a.get(field)) for a in annotations if a.get(field) not in (None, "")]
                pairs = []
                for i in range(len(values)):
                    for j in range(i + 1, len(values)):
                        pairs.append((values[i], values[j]))
                field_pairs[field].extend(pairs)
                run_report["fields"][field] = cohen_kappa(pairs)
            for field in _SET_FIELDS:
                values = [a.get(field) or [] for a in annotations if a.get(field) is not None]
                scores = [
                    score
                    for i in range(len(values))
                    for j in range(i + 1, len(values))
                    if (score := jaccard(values[i], values[j])) is not None
                ]
                set_scores[field].extend(scores)
                run_report["fields"][field] = sum(scores) / len(scores) if scores else None
        report["runs"][run_id] = run_report

    for field, pairs in field_pairs.items():
        report["summary"][field] = cohen_kappa(pairs)
    for field, scores in set_scores.items():
        report["summary"][field] = sum(scores) / len(scores) if scores else None

    # Fleiss across all annotators for the binary label.
    ratings = [
        [str(a.get("label")) for a in by_run.get(run_id, []) if a.get("label") not in (None, "")]
        for run_id in run_ids
    ]
    report["summary"]["label_fleiss"] = fleiss_kappa([row for row in ratings if row])
    return report


class Handler(BaseHTTPRequestHandler):
    store: TaskStore
    annotations: AnnotationStore
    static_root: Path

    def log_message(self, fmt, *args):  # keep the console clean
        return

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, relative: str) -> None:
        candidate = (self.static_root / relative).resolve()
        if not candidate.is_relative_to(self.static_root.resolve()) or not candidate.is_file():
            self._json(404, {"error": "not_found"})
            return
        body = candidate.read_bytes()
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(candidate.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        try:
            self._get()
        except (ValueError, sqlite3.Error) as exc:
            self._json(400, {"error": str(exc)})

    def _get(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._static("index.html")
            return
        if parsed.path.startswith("/static/"):
            self._static(parsed.path[len("/static/") :])
            return
        if parsed.path == "/api/tasks":
            self._json(200, {"tasks": self.store.tasks()})
            return
        if parsed.path.startswith("/api/task/"):
            run_id = parsed.path[len("/api/task/") :]
            reveal = parse_qs(parsed.query).get("reveal", ["0"])[0] == "1"
            try:
                payload = self.store.load(run_id, reveal)
            except KeyError as exc:
                self._json(404, {"error": str(exc)})
                return
            own = parse_qs(parsed.query).get("annotator", [""])[0]
            payload["annotations"] = [r for r in self.annotations.for_run(run_id) if reveal or (own and r.get("annotator") == own)]
            self._json(200, payload)
            return
        if parsed.path == "/api/agreement":
            tasks = [task["run_id"] for task in self.store.tasks()]
            self._json(200, agreement(self.annotations.all(), tasks))
            return
        if parsed.path == "/api/export":
            records = self.annotations.all()
            manifest = {
                "schema": "mtagentlens.annotation-export/v1",
                "codebook_version": CODEBOOK_VERSION,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "tool": "tools/annotator/server.py",
                "runs": len({record.get("run_id") for record in records}),
                "annotations": len(records),
            }
            self._json(200, {"manifest": manifest, "annotations": records})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/annotation":
            self._json(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            self._json(400, {"error": "invalid_body"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            run_id = str(payload["run_id"])
            task = self.store.load(run_id, reveal=False)
            annotation = dict(payload.get("annotation") or {})
            supplied = annotation.get("db_sha256")
            if supplied and supplied != task["db_sha256"]:
                raise ValueError("Database changed since annotation began; reload evidence")
            annotation["db_sha256"] = task["db_sha256"]
            saved = self.annotations.save(
                run_id, str(payload["annotator"]), annotation
            )
        except (KeyError, ValueError, sqlite3.Error) as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, {"saved": saved})


def build_server(db_root: Path, annotation_root: Path, port: int, host: str) -> ThreadingHTTPServer:
    store = TaskStore(db_root)
    annotations = AnnotationStore(annotation_root)
    handler = type(
        "BoundHandler",
        (Handler,),
        {"store": store, "annotations": annotations, "static_root": STATIC_ROOT},
    )
    return ThreadingHTTPServer((host, port), handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dbs", type=Path, required=True, help="directory or single .db")
    parser.add_argument(
        "--annotations",
        type=Path,
        default=TOOL_ROOT / "annotations",
        help="annotation output directory",
    )
    parser.add_argument("--port", type=int, default=7410)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    server = build_server(args.dbs, args.annotations, args.port, args.host)
    print(f"MtAgentLens annotator: http://{args.host}:{args.port}")
    print(f"  dbs:         {args.dbs}")
    print(f"  annotations: {args.annotations}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
