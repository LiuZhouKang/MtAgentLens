#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Local HTTP API for the FuxiBench scene annotation workbench.

The server only serves the dedicated scene store. It never reads FuxiBench GT,
runtime traces or the run-level annotation database.
"""

from __future__ import annotations

import json
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from tools.annotator.scene_records import (
    ATTACK_PATTERNS,
    ATTACK_TYPES,
    BOUNDARY_TYPES,
    LABELS,
    MIN_DECISIONS,
    SEVERITIES,
    SceneRecordStore,
)

STATIC_ROOT = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 2 * 1024 * 1024


class RequestError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _codebook_options(store: SceneRecordStore) -> dict:
    return {
        "version": store.codebook_version,
        # Generic key names keep enum options distinct from GT metadata fields.
        "classes": sorted(LABELS),
        "attack_classes": sorted(ATTACK_TYPES),
        "decisions": sorted(MIN_DECISIONS),
        "patterns": sorted(ATTACK_PATTERNS),
        "boundaries": sorted(BOUNDARY_TYPES),
        "severities": sorted(SEVERITIES),
        "raters": list(store.raters),
    }


class SceneRequestHandler(BaseHTTPRequestHandler):
    scene_store: SceneRecordStore
    server_version = "FuxiSceneAnnotator/1"
    sys_version = ""

    # -- transport ------------------------------------------------------------

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        return

    def _send(self, status: int, body: bytes, content_type: str, *, headers=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: object) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _error(self, error: RequestError) -> None:
        self._json(error.status, {"error": error.message, "code": error.code})

    def _read_json(self) -> dict:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise RequestError(400, "invalid_json", "Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise RequestError(400, "invalid_json", "Content-Length must be an integer") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise RequestError(400, "invalid_json", "request body is empty or too large")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestError(400, "invalid_json", "request body is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise RequestError(400, "invalid_json", "request body must be a JSON object")
        return payload

    @staticmethod
    def _rater(query: dict[str, list[str]]) -> str:
        values = query.get("rater")
        if not values or not values[0].strip():
            raise RequestError(400, "missing_rater", "rater query parameter is required")
        return values[0]

    def _active_rater(self, query: dict[str, list[str]]) -> str:
        requested = self._rater(query)
        active = getattr(self, "active_rater", None)
        if active is not None and requested != active:
            raise RequestError(403, "rater_mismatch", "local workspace identity is fixed")
        return active or requested

    def _scene_item(self, rater: str, scenario_id: str) -> dict:
        internal_id = self._internal_id(scenario_id)
        try:
            items = self.scene_store.rater_view(rater)
        except ValueError as exc:
            raise RequestError(400, "unknown_rater", str(exc)) from exc
        for item in items:
            if item["scenario_id"] == internal_id:
                return self._public_scene(item)
        raise RequestError(404, "unknown_scenario", f"unknown scenario: {scenario_id!r}")

    def _public_scene(self, scene: dict) -> dict:
        scenario_id = scene["scenario_id"]
        path_map = {
            source["path"]: f"material-{index:02d}"
            for index, source in enumerate(scene["sources"], start=1)
        }

        def public_annotation(annotation):
            if not isinstance(annotation, dict):
                return annotation
            result = dict(annotation)
            if isinstance(result.get("evidence_paths"), list):
                result["evidence_paths"] = [path_map.get(path, "material-unknown")
                                             for path in result["evidence_paths"]]
            return result

        def public_record(record):
            if not isinstance(record, dict):
                return record
            result = dict(record)
            result["scenario_id"] = self.public_ids[scenario_id]
            result["annotation"] = public_annotation(result.get("annotation"))
            return result

        sources = []
        for index, source in enumerate(scene["sources"], start=1):
            text = source["text"]
            if source["kind"] == "scenario":
                try:
                    scenario = json.loads(text)
                except json.JSONDecodeError:
                    scenario = None
                if isinstance(scenario, dict):
                    scenario.pop("id", None)
                    scenario.pop("fixture_dir", None)
                    scenario.pop("pre_run_script", None)
                    for agent_index, agent in enumerate(scenario.get("agents", []), start=1):
                        if isinstance(agent, dict) and "role_file" in agent:
                            agent["role_file"] = f"role-{agent_index:02d}"
                    text = json.dumps(scenario, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            for internal_name, public_name in self.public_ids.items():
                if internal_name in text:
                    text = text.replace(internal_name, public_name)
            sources.append({
                "path": f"material-{index:02d}",
                "sha256": source["sha256"],
                "text": text,
                "kind": source["kind"],
            })

        result = dict(scene)
        result["scenario_id"] = self.public_ids[scenario_id]
        result["sources"] = sources
        result["own"] = public_record(scene.get("own"))
        result["peers"] = [public_record(record) for record in scene.get("peers", [])]
        result["adjudication"] = public_record(scene.get("adjudication"))
        comparison = scene.get("comparison")
        if isinstance(comparison, dict):
            comparison = dict(comparison)
            comparison["raters"] = {
                rater: public_annotation(annotation)
                for rater, annotation in comparison.get("raters", {}).items()
            }
            comparison["adjudication"] = public_record(comparison.get("adjudication"))
        result["comparison"] = comparison
        return result

    def _internal_id(self, public_id: str) -> str:
        scenario_id = self.case_ids.get(public_id)
        if scenario_id is None:
            raise RequestError(404, "unknown_scenario", f"unknown scenario: {public_id!r}")
        return scenario_id

    def _internal_annotation(self, scenario_id: str, annotation: object) -> object:
        if not isinstance(annotation, dict):
            return annotation
        result = dict(annotation)
        paths = result.get("evidence_paths")
        if isinstance(paths, list):
            sources = self.scene_store._catalog_by_id[scenario_id]["sources"]
            path_map = {f"material-{index:02d}": source["path"]
                        for index, source in enumerate(sources, start=1)}
            result["evidence_paths"] = [path_map.get(path, path) for path in paths]
        return result

    # -- GET ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        try:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            if path in ("/", "/index.html", "/scenes"):
                page = (STATIC_ROOT / "scenes.html").read_bytes()
                frame_ancestors = self.frame_ancestor or "'none'"
                self._send(
                    200, page, "text/html; charset=utf-8",
                    headers={
                        "Content-Security-Policy": (
                            "default-src 'none'; style-src 'unsafe-inline'; "
                            "script-src 'unsafe-inline'; connect-src 'self'; "
                            "img-src 'self' data:; base-uri 'none'; "
                            f"form-action 'self'; frame-ancestors {frame_ancestors}"
                        )
                    },
                )
                return
            if path == "/api/scenes":
                rater = self._active_rater(query)
                try:
                    scenes = [self._public_scene(scene)
                              for scene in self.scene_store.rater_view(rater)]
                except ValueError as exc:
                    raise RequestError(400, "unknown_rater", str(exc)) from exc
                self._json(200, {
                    "rater": rater,
                    "raters": list(self.scene_store.raters),
                    "codebook_version": self.scene_store.codebook_version,
                    "codebook": _codebook_options(self.scene_store),
                    "scenes": scenes,
                })
                return
            if path.startswith("/api/scenes/"):
                public_id = path[len("/api/scenes/"):]
                if not public_id or "/" in public_id:
                    raise RequestError(404, "not_found", "endpoint does not exist")
                rater = self._active_rater(query)
                item = self._scene_item(rater, public_id)
                self._json(200, {
                    **item,
                    "rater": rater,
                    "raters": list(self.scene_store.raters),
                    "codebook": _codebook_options(self.scene_store),
                })
                return
            if path == "/api/export":
                payload = self.scene_store.export()
                incomplete = [
                    scenario["scenario_id"]
                    for scenario in payload["scenarios"]
                    if not all(chain["submitted"] for chain in scenario["rater_chains"])
                ]
                if incomplete:
                    raise RequestError(
                        409, "export_locked",
                        "export is available only after every rater submits every scenario; "
                        f"incomplete: {', '.join(incomplete)}",
                    )
                self._json(200, payload)
                return
            if path == "/api/agreement":
                report = self.scene_store.agreement_report()
                if not report["available"]:
                    raise RequestError(
                        409, "submissions_required",
                        "agreement is available after all raters submit every scenario",
                    )
                self._json(200, report)
                return
            if path == "/api/codebook":
                self._json(200, _codebook_options(self.scene_store))
                return
            raise RequestError(404, "not_found", "endpoint does not exist")
        except RequestError as exc:
            self._error(exc)
        except (OSError, ValueError) as exc:
            self._error(RequestError(500, "server_error", str(exc)))

    # -- POST -----------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        try:
            parsed = urlparse(self.path)
            parts = unquote(parsed.path).strip("/").split("/")
            if len(parts) != 4 or parts[:2] != ["api", "scenes"]:
                raise RequestError(404, "not_found", "endpoint does not exist")
            public_id, action = parts[2], parts[3]
            if action not in ("draft", "submit", "adjudicate"):
                raise RequestError(404, "not_found", "endpoint does not exist")
            payload = self._read_json()
            active_rater = getattr(self, "active_rater", None)
            if active_rater is not None:
                if action == "adjudicate":
                    raise RequestError(403, "adjudication_disabled",
                                       "adjudication requires the merged three-rater workspace")
                if payload.get("rater") != active_rater:
                    raise RequestError(403, "rater_mismatch", "local workspace identity is fixed")
            if action in ("draft", "submit"):
                self._save(payload, public_id, action)
            else:
                self._adjudicate(payload, public_id)
        except RequestError as exc:
            self._error(exc)
        except (OSError, ValueError) as exc:
            self._error(RequestError(500, "server_error", str(exc)))

    def _save(self, payload: dict, public_id: str, action: str) -> None:
        allowed = {"rater", "annotation", "expected_revision", "change_reason"}
        if set(payload) - allowed or not {"rater", "annotation"} <= set(payload):
            raise RequestError(
                400, "invalid_payload",
                "draft/submit payload requires rater and annotation and allows "
                "expected_revision and change_reason",
            )
        if not isinstance(payload["rater"], str):
            raise RequestError(400, "invalid_payload", "rater must be text")
        expected = payload.get("expected_revision")
        if expected is not None and not isinstance(expected, str):
            raise RequestError(400, "invalid_payload", "expected_revision must be text or null")
        reason = payload.get("change_reason")
        if reason is not None and not isinstance(reason, str):
            raise RequestError(400, "invalid_payload", "change_reason must be text or null")
        try:
            scenario_id = self._internal_id(public_id)
            annotation = self._internal_annotation(scenario_id, payload["annotation"])
            if action == "draft":
                record = self.scene_store.save_draft(
                    scenario_id, payload["rater"], annotation,
                    expected_revision=expected, change_reason=reason,
                )
            else:
                record = self.scene_store.submit(
                    scenario_id, payload["rater"], annotation,
                    expected_revision=expected, change_reason=reason,
                )
        except ValueError as exc:
            raise self._store_error(exc) from exc
        item = self._scene_item(payload["rater"], public_id)
        public_record = dict(record)
        public_record["scenario_id"] = public_id
        public_record["annotation"] = item["own"]["annotation"]
        self._json(200, {**public_record, "record": public_record,
                         "own": item["own"], "scene": item})

    def _adjudicate(self, payload: dict, public_id: str) -> None:
        allowed = {"adjudicator", "annotation", "rationale"}
        if set(payload) != allowed:
            raise RequestError(
                400, "invalid_payload",
                "adjudication payload requires exactly adjudicator, annotation and rationale",
            )
        if not isinstance(payload["adjudicator"], str):
            raise RequestError(400, "invalid_payload", "adjudicator must be text")
        if not isinstance(payload["rationale"], str):
            raise RequestError(400, "invalid_payload", "rationale must be text")
        try:
            scenario_id = self._internal_id(public_id)
            adjudication = self.scene_store.adjudicate(
                scenario_id, payload["adjudicator"],
                self._internal_annotation(scenario_id, payload["annotation"]),
                payload["rationale"],
            )
        except ValueError as exc:
            raise self._store_error(exc) from exc
        item = self._scene_item(payload["adjudicator"], public_id)
        public_adjudication = dict(adjudication)
        public_adjudication["scenario_id"] = public_id
        public_adjudication["annotation"] = item["adjudication"]["annotation"]
        self._json(200, {
            **public_adjudication,
            "adjudication": public_adjudication,
            "scene": item,
        })

    @staticmethod
    def _store_error(exc: ValueError) -> RequestError:
        message = str(exc)
        if "unknown scenario" in message:
            return RequestError(404, "unknown_scenario", message)
        if "unknown rater" in message:
            return RequestError(400, "unknown_rater", message)
        if "must have submitted" in message:
            return RequestError(409, "submissions_required", message)
        if "already submitted" in message:
            return RequestError(409, "already_submitted", message)
        if ("stale revision" in message or "expected_revision is required" in message
                or "does not match" in message or "binding mismatch" in message):
            return RequestError(409, "revision_conflict", message)
        return RequestError(400, "invalid_annotation", message)


def create_scene_server(
    store: SceneRecordStore,
    catalog: list[dict],
    host: str = "127.0.0.1",
    port: int = 7412,
    active_rater: str | None = None,
    frame_ancestor: str | None = None,
) -> ThreadingHTTPServer:
    """Create a loopback scene workbench server; caller owns shutdown/close."""
    if not isinstance(store, SceneRecordStore):
        raise ValueError("store must be a SceneRecordStore")
    if not isinstance(catalog, list) or not catalog:
        raise ValueError("catalog must be a non-empty scene list")
    catalog_ids = {row.get("scenario_id") for row in catalog if isinstance(row, dict)}
    if catalog_ids != set(store._catalog_by_id):
        raise ValueError("scene catalog does not match the store binding")
    if not isinstance(host, str) or not host.strip():
        raise ValueError("host must be non-empty text")
    if active_rater is not None and active_rater not in store.raters:
        raise ValueError("active_rater must be present in the configured rater list")
    if frame_ancestor is not None and not re.fullmatch(
            r"http://(?:127\.0\.0\.1|localhost):[0-9]{1,5}", frame_ancestor):
        raise ValueError("frame_ancestor must be a local HTTP origin")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")

    class BoundHandler(SceneRequestHandler):
        scene_store = store
        internal_ids = [row["scenario_id"] for row in catalog]
        public_ids = {scenario_id: f"case-{secrets.token_hex(6)}"
                      for scenario_id in internal_ids}
        case_ids = {public_id: scenario_id for scenario_id, public_id in public_ids.items()}

    BoundHandler.active_rater = active_rater
    BoundHandler.frame_ancestor = frame_ancestor

    return ThreadingHTTPServer((host, port), BoundHandler)
