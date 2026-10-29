"""Local pilot UI with fixed rater capabilities; raw sources are not blinded.

Curator capabilities include complete-store audit export. This is not a
multi-user authentication service or proof of human participation.
"""

import json
import secrets
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from tools.annotator.blinding import binding_identifiers, blind_identifiers
from tools.annotator.evidence_summary import summarize_event
from tools.annotator.evidence import _loads_strict
from tools.annotator.records import RecordStore

_PAGE = Path(__file__).parent / "static" / "workbench.html"
_PREVIEW_BYTES = 262144


class RequestError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def _record_view(record):
    if record is None:
        return None
    return {key: record[key] for key in
            ("revision_id", "parent_revision_id", "saved_at", "annotator", "method",
             "annotation", "review")
            if key in record}


def _judgments(annotation):
    result = {name: {"value": annotation[name]["value"],
                     "evidence_refs": sorted(annotation[name]["evidence_refs"])}
              for name in ("attempted", "executed", "succeeded")}
    actors = annotation["roots"]["actors"]
    result["roots"] = {"actors": None if actors is None else sorted(actors),
                       "evidence_refs": sorted(annotation["roots"]["evidence_refs"])}
    result.update({"identities." + event: actor for event, actor in annotation["identities"].items()})
    return result


class Workbench:
    def __init__(self, store, rater, method, mode, reviewers, codebook_version=None):
        self.store, self.rater, self.method = store, rater, method
        self.mode, self.reviewers = mode, tuple(reviewers)
        self.codebook_version = codebook_version
        self.aliases, self.task_ids = {}, {}
        self.lock = threading.Lock()
        self.summary_cache: dict[str, tuple[str, dict[str, dict]]] = {}

    def queue(self, *, review=False):
        """Task queue rows.

        Draft metadata is review-pane-only: an independent annotate pane must
        not learn that a model draft exists, otherwise the rater is primed by
        the very artefact the blinding contract excludes. ``studio`` servers
        expose it only for an explicit ``?review=1`` queue read.
        """
        entries = self.store.list_tasks(self.rater)
        with self.lock:
            for entry in entries:
                task = entry["task_id"]
                if task not in self.aliases:
                    alias = f"task-{len(self.aliases) + 1:04d}"
                    self.aliases[task] = alias
                    self.task_ids[alias] = task
            rows = []
            for entry in entries:
                task = entry["task_id"]
                row = {"id": self.aliases[task],
                       "has_annotation": entry["has_annotation"]}
                if self.mode == "review" or (self.mode == "studio" and review):
                    attestations = self.store.list_attestations(task)
                    row["has_model_proposal"] = (self.store.latest_model_proposal(task)
                                                 is not None)
                    row["has_attestation"] = any(
                        item["reviewer"] == self.rater for item in attestations)
                rows.append(row)
            return rows

    def task_id(self, alias):
        self.queue()
        if alias not in self.task_ids:
            raise RequestError(404, "not_found", "任务不存在。")
        return self.task_ids[alias]

    def event_summaries(self, task_id: str, bundle: dict) -> dict[str, dict]:
        """Decode each event once per task for semantic list labels."""
        cache_key = bundle["evidence_bundle_hash"]
        cached = self.summary_cache.get(task_id)
        if cached is not None and cached[0] == cache_key:
            return cached[1]
        summaries: dict[str, dict] = {}
        for evidence in self.store.read_evidence_many(task_id, limit=None):
            event = evidence["event"]
            summaries[event["event_id"]] = summarize_event(
                evidence["bytes"], source_id=event["source_id"],
                derivation=event.get("derivation", ""))
        self.summary_cache[task_id] = (cache_key, summaries)
        return summaries

    def view(self, alias, *, review=False):
        raters = (self.rater, *self.reviewers) if self.mode == "adjudicate" else (self.rater,)
        task_id = self.task_id(alias)
        data = self.store.read_task(task_id, annotators=raters)
        bundle = data["bundle"]
        summaries = self.event_summaries(task_id, bundle)
        task_codebook = bundle["binding"].get("codebook_version")
        if self.codebook_version is not None and task_codebook != self.codebook_version:
            raise RequestError(409, "codebook_mismatch",
                               "任务使用 codebook %r，与当前实例 %r 不一致；请用匹配的 codebook 重新导入或标注。"
                               % (task_codebook, self.codebook_version))
        history = [record for record in data["revisions"] if record["annotator"] == self.rater]
        own = history[-1] if history else None
        latest = {record["annotator"]: record for record in data["revisions"]}
        comparisons = [{"annotator": rater, **_record_view(latest[rater])}
                       for rater in self.reviewers if rater in latest]
        heads = {record["annotator"]: record["revision_id"] for record in comparisons}
        judgments = {record["annotator"]: _judgments(record["annotation"])
                     for record in comparisons}
        differences = []
        if len(judgments) >= 2:
            for field in sorted(next(iter(judgments.values()))):
                values = {rater: fields[field] for rater, fields in judgments.items()}
                if any(value != next(iter(values.values())) for value in values.values()):
                    differences.append({"field": field, "values": values})
        payload = {"id": alias, "actors": bundle["actors"],
                "codebook_version": task_codebook,
                "events": [{key: event[key] for key in
                            ("event_id", "record_id", "timestamp", "clock_domain",
                             "source_id", "derivation")} |
                           {"source_kind": bundle["sources"][event["source_id"]]["kind"],
                            "label": summaries.get(event["event_id"], {}).get("label", "记录"),
                            "summary": summaries.get(event["event_id"], {}).get("summary", "")}
                           for event in bundle["events"]],
                "own": _record_view(own), "history": [_record_view(record) for record in history],
                "comparisons": comparisons, "differences": differences,
                "reviewed_revisions": heads,
                "review_stale": bool(self.mode == "adjudicate" and own and own.get("review") and
                                     own["review"]["reviewed_revisions"] != heads)}
        if self.mode == "review" or (self.mode == "studio" and review):
            task = self.task_id(alias)
            proposals = self.store.latest_model_proposals(task)
            payload["proposals"] = [_record_view(record) for _, record in sorted(proposals.items())]
            proposal = self.store.latest_model_proposal(task)
            payload["proposal"] = _record_view(proposal) if proposal else None
            payload["attestations"] = self.store.list_attestations(task)
        return payload

    def attest(self, alias, payload):
        if self.mode not in ("review", "studio"):
            raise RequestError(403, "review_required", "只有复核模式可以记录复核声明。")
        if not isinstance(payload, dict) or set(payload) != {"proposal_revision_id", "statement"}:
            raise RequestError(400, "invalid_payload", "复核请求字段不完整。")
        try:
            return self.store.save_attestation(
                self.task_id(alias), self.rater, payload["proposal_revision_id"],
                statement=payload["statement"])
        except ValueError as exc:
            message = str(exc)
            if "already exists" in message:
                raise RequestError(409, "attestation_exists", "该复核声明已存在。") from exc
            if "stale model proposal" in message:
                raise RequestError(409, "stale_proposal", "模型草稿已更新，请重新载入。") from exc
            raise RequestError(400, "invalid_attestation",
                               "复核声明无效：请检查草稿修订号与声明文本。") from exc

    def save(self, alias, payload):
        if self.mode == "review":
            raise RequestError(403, "review_mode_read_only",
                               "复核模式只记录复核声明，不写入人工标注。")
        fields = {"expected_revision", "annotation"}
        if self.mode == "adjudicate":
            fields |= {"reviewed_revisions", "rationale"}
        if not isinstance(payload, dict) or set(payload) != fields:
            raise RequestError(400, "invalid_payload", "保存字段不完整或包含未允许的字段。")
        review = None
        if self.mode == "adjudicate":
            heads = payload["reviewed_revisions"]
            if not isinstance(heads, dict) or set(heads) != set(self.reviewers):
                raise RequestError(400, "invalid_reviewers", "裁决必须引用全部指定标注者的修订。")
            review = {"reviewed_revisions": heads, "rationale": payload["rationale"]}
        # Validate source integrity separately so an invalid bundle cannot be
        # reported as an ordinary annotation-field error.
        self.view(alias)
        try:
            saved = self.store.save(self.task_id(alias), self.rater, payload["annotation"],
                                    method=self.method, expected_revision=payload["expected_revision"],
                                    review=review)
        except ValueError as exc:
            if "stale parent revision" in str(exc) or "stale review revision" in str(exc) or str(exc) == "review revision is missing":
                raise RequestError(409, "revision_conflict", "修订冲突：记录已更新，当前草稿请保留并复核。") from exc
            if "model proposal" in str(exc) or "independently annotated" in str(exc):
                raise RequestError(400, "independent_reviews_required", "裁决来源必须是独立标注，不能用模型建议或已有裁决替代。") from exc
            raise RequestError(400, "invalid_annotation",
                               "标注无效：请检查已知判断的证据引用、身份、状态与裁决理由。") from exc
        return {"own": _record_view(saved)}


def create_server(store_path: Path, *, rater: str, method: str, mode: str = "annotate",
                  reviewers=(), token: str | None = None, port: int = 7411,
                  codebook_version: str | None = None):
    if (not isinstance(rater, str) or not rater.strip() or len(rater) > 128 or
            method not in ("human", "controlled_execution", "model_proposal") or
            mode not in ("annotate", "adjudicate", "review", "studio")):
        raise ValueError("invalid rater, method or workbench mode")
    if (not isinstance(reviewers, (tuple, list)) or
            any(not isinstance(name, str) or not name.strip() or len(name) > 128 for name in reviewers)):
        raise ValueError("invalid reviewer identifiers")
    if mode == "review" and method != "human":
        raise ValueError("review mode requires the human statement method")
    if ((mode in ("annotate", "review", "studio") and reviewers) or (mode == "adjudicate" and
            (len(set(reviewers)) != len(reviewers) or len(reviewers) < 2 or rater in reviewers))):
        raise ValueError("adjudication requires at least two distinct other reviewers")
    if codebook_version is not None and (
            not isinstance(codebook_version, str) or not codebook_version.strip()):
        raise ValueError("codebook_version must be a non-empty string or None")
    if not Path(store_path).is_file():
        raise ValueError("record store does not exist; register a bundle first")
    app = Workbench(RecordStore(store_path), rater, method, mode, reviewers,
                    codebook_version=codebook_version)
    capability = token if token is not None else secrets.token_urlsafe(32)
    if not isinstance(capability, str) or not capability:
        raise ValueError("invalid workbench capability")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body, content_type="application/json; charset=utf-8", *, headers=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            for name, value in {"Content-Type": content_type, "Content-Length": str(len(body)),
                                "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                "Referrer-Policy": "no-referrer", **(headers or {})}.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def guard(self, parsed):
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            host = self.headers.get("Host", "")
            if host not in allowed or self.headers.get("Origin", "http://" + host) != "http://" + host:
                raise RequestError(403, "forbidden_origin", "请求来源不被允许。")
            if (parsed.scheme or parsed.netloc or
                    (parsed.query and not (mode == "studio" and parsed.query == "review=1"))):
                raise RequestError(400, "invalid_url", "不支持此请求地址或查询参数。")
            if parsed.path.startswith("/api/") and not secrets.compare_digest(
                    self.headers.get("Authorization", "").encode("utf-8"),
                    ("Bearer " + capability).encode("utf-8")):
                raise RequestError(401, "unauthorized", "访问凭据无效，请重新打开启动时的私有链接。")

        def read_json(self):
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise RequestError(415, "invalid_content_type", "保存请求必须是 JSON。")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError as exc:
                raise RequestError(400, "invalid_length", "请求长度无效。") from exc
            if length < 1 or length > 1048576:
                self.close_connection = True
                raise RequestError(413, "invalid_length", "保存内容超过允许范围。")
            try:
                return _loads_strict(self.rfile.read(length).decode("utf-8"))
            except ValueError as exc:
                raise RequestError(400, "invalid_json", "保存内容不是有效 JSON。") from exc

        def dispatch(self):
            self.connection.settimeout(10)
            parsed = urlsplit(self.path)
            self.guard(parsed)
            path = parsed.path
            if self.command == "GET" and path == "/":
                nonce = secrets.token_urlsafe(18)
                page = _PAGE.read_text(encoding="utf-8").replace("WORKBENCH_NONCE", nonce).encode()
                csp = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                       "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
                return self.reply(200, page, "text/html; charset=utf-8", headers={"Content-Security-Policy": csp})
            if self.command == "GET" and path == "/api/session":
                return self.reply(200, {"rater": rater, "method": method, "mode": mode,
                                        "reviewers": list(reviewers),
                                        "codebook_version": codebook_version})
            if self.command == "GET" and path == "/api/tasks":
                return self.reply(200, {"tasks": app.queue(review="review=1" in parsed.query)})
            if self.command == "GET" and path == "/api/export":
                if mode != "adjudicate":
                    raise RequestError(403, "curator_required", "独立标注模式不能读取完整审计导出。")
                return self.reply(200, app.store.export())
            parts = path.strip("/").split("/")
            if len(parts) >= 3 and parts[:2] == ["api", "tasks"]:
                alias = parts[2]
                if self.command == "GET" and len(parts) == 3:
                    review = "review=1" in parsed.query
                    return self.reply(200, app.view(alias, review=review))
                if self.command == "POST" and parts[3:] == ["annotations"]:
                    return self.reply(200, app.save(alias, self.read_json()))
                if self.command == "POST" and parts[3:] == ["attestations"]:
                    return self.reply(200, app.attest(alias, self.read_json()))
                if self.command == "GET" and len(parts) in (5, 6) and parts[3] == "events":
                    suffix = parts[5] if len(parts) == 6 else None
                    if suffix not in (None, "raw", "redacted"):
                        raise RequestError(404, "not_found", "入口不存在。")
                    resolved_task = app.task_id(alias)
                    try:
                        evidence = app.store.read_evidence(
                            resolved_task, unquote(parts[4]),
                            limit=None if suffix else _PREVIEW_BYTES)
                    except ValueError as exc:
                        if str(exc) == "unknown evidence event":
                            raise RequestError(404, "not_found", "证据记录不存在。") from exc
                        raise
                    if suffix == "raw" and mode != "adjudicate":
                        raise RequestError(403, "curator_required",
                                           "独立标注模式不能读取未脱敏原始记录。")
                    if suffix == "raw":
                        return self.reply(200, evidence["bytes"], "application/octet-stream",
                                          headers={"Content-Disposition": 'attachment; filename="evidence.bin"'})
                    binding = evidence["binding"]
                    text, redactions = blind_identifiers(
                        evidence["bytes"], binding_identifiers(binding))
                    if suffix == "redacted":
                        return self.reply(200, text.encode("utf-8"), "text/plain; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="evidence.txt"'})
                    decoded = summarize_event(evidence["bytes"],
                        source_id=evidence["event"]["source_id"],
                        derivation=evidence["event"].get("derivation", ""))
                    return self.reply(200, {"event": evidence["event"] | {"source_kind": evidence["source_kind"]},
                        "text": text, "redactions": redactions, "blinded": True,
                        "summary": decoded,
                        "raw_sha256": evidence["event"]["record_sha256"],
                        "byte_length": evidence["byte_length"], "shown_bytes": len(evidence["bytes"]),
                        "truncated": len(evidence["bytes"]) < evidence["byte_length"]})
            raise RequestError(404, "not_found", "入口不存在。")

        def handle_request(self):
            try:
                self.dispatch()
            except RequestError as exc:
                self.reply(exc.status, {"error": exc.code, "message": exc.message})
            except (ValueError, OSError, sqlite3.Error):
                self.reply(422, {"error": "evidence_unavailable", "message": "证据或存储验证失败，请联系本地管理员。"})

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.token = capability
    return server
