# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Studio UX contract: a11y semantics, anti-contamination gates and scale.

The single-page studio keeps independent annotation and draft review on one
route. These tests pin the properties that make that safe and usable:

* the annotate pane must never learn that a model draft exists (no proposal
  metadata, no proposal body) until the rater explicitly asks for review;
* the page is offline-only, keeps evidence as text nodes and exposes real
  ARIA tab semantics;
* the palette meets WCAG AA contrast and 5000-event tasks stay responsive.
"""

import hashlib
import http.client
import json
import re
import threading
import time
from pathlib import Path

import pytest

from tools.annotator.evidence import build_bundle
from tools.annotator.evidence_summary import summarize_event
from tools.annotator.records import RecordStore
from tools.annotator.tests.test_evidence import fixture_inputs
from tools.annotator.tests.test_records import annotation, setup  # noqa: F401
from tools.annotator.workbench import create_server

PAGE = Path(__file__).resolve().parents[1] / "static" / "workbench.html"


@pytest.fixture(scope="module")
def page_text():
    return PAGE.read_text(encoding="utf-8")


# ─── static page contract ──────────────────────────────────────────────────


def test_page_declares_tablist_tabs_and_labelled_panels(page_text):
    assert 'role="tablist"' in page_text
    assert page_text.count('role="tab"') == 2
    assert page_text.count('role="tabpanel"') == 2
    # Each tab points at its panel and each panel is labelled by its tab.
    assert 'aria-controls="annotate-panel"' in page_text
    assert 'aria-controls="review-area"' in page_text
    assert 'aria-labelledby="studio-tab-annotate"' in page_text
    assert 'aria-labelledby="studio-tab-review"' in page_text
    # Roving tabindex: only the selected tab is in the tab order.
    annotate_tab = re.search(r'<button[^>]*id="studio-tab-annotate"[^>]*>', page_text).group(0)
    review_tab = re.search(r'<button[^>]*id="studio-tab-review"[^>]*>', page_text).group(0)
    assert 'role="tab"' in annotate_tab and 'tabindex="0"' in annotate_tab
    assert 'role="tab"' in review_tab and 'tabindex="-1"' in review_tab
    # The review pane starts hidden so no draft markup is rendered on load.
    review_section = re.search(r'<section id="review-area"[^>]*>', page_text).group(0)
    assert "hidden" in review_section


def test_page_announces_status_and_errors_to_assistive_tech(page_text):
    status = re.search(r'<p id="status"[^>]*>', page_text).group(0)
    assert 'role="status"' in status and 'aria-live="polite"' in status
    assert re.search(r'<p id="a11y-live"[^>]*role="alert"[^>]*aria-live="assertive"', page_text)
    # The tablist must not own non-tab children (axe aria-required-children).
    tablist = re.search(r'<div id="studio-tabs"[^>]*>(.*?)</div>', page_text, re.S).group(1)
    assert re.findall(r'role="([a-z]+)"', tablist) == ["tab", "tab"]
    # Root-cause semantics: unknown (null) and evidenced-empty ([]) differ.
    assert 'id="roots-meaning"' in page_text


def test_page_is_offline_only_and_never_sinks_text_into_markup(page_text):
    assert not re.search(r'(?:src|href)="https?://', page_text)
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in page_text, sink
    # Server substitutes a fresh CSP nonce into exactly these placeholders.
    assert page_text.count("WORKBENCH_NONCE") >= 2
    assert "http://" not in page_text.replace("http://www.w3.org/2000/svg", "").replace("http://localhost", "")


def test_trace_navigation_uses_structured_metadata_and_bigint_time(page_text):
    assert 'time.className = "event-time"' in page_text
    assert "formatEventTime(event.timestamp, event.clock_domain)" in page_text
    assert 'id="agent-relationship-panel"' in page_text
    assert 'id="relation-canvas"' in page_text
    assert 'id="timeline-canvas"' in page_text
    assert "task.visualization" in page_text
    assert "dagre.graphlib.Graph" in page_text
    assert "window.cytoscape(" in page_text
    assert "window.echarts.init(" in page_text
    assert 'renderMode: "richText"' in page_text
    assert "BigInt(" in page_text
    assert "from_agent_id" not in page_text
    # Graph edges must never be recovered by matching summary prose.
    assert not re.search(r"summary[^\n]{0,80}\.match\(", page_text)
    # Canvas views need an explicit, bounded keyboard fallback instead of
    # hundreds of focusable offscreen controls.
    assert 'id="relation-evidence-picker"' in page_text
    assert 'id="relation-open-evidence"' in page_text
    assert page_text.count('data-trace-view="graph"') >= 2
    assert page_text.count('data-trace-view="timeline"') >= 2
    assert page_text.count('data-trace-view="events"') >= 2
    # The replaced custom SVG renderers are gone.
    assert "relation-svg" not in page_text and "timeline-svg" not in page_text


def test_palette_meets_wcag_aa_contrast(page_text):
    def value(name):
        match = re.search(r"--%s:\s*(#[0-9a-fA-F]{6})" % re.escape(name), page_text)
        assert match, name
        return match.group(1)

    def luminance(color):
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                  for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    def ratio(one, other):
        first, second = sorted((luminance(one), luminance(other)), reverse=True)
        return (first + 0.05) / (second + 0.05)

    surfaces = [value("bg"), value("surface"), value("surface-2"), value("surface-3")]
    for ink in ("text", "text-2", "muted", "faint"):
        for surface in surfaces:
            assert ratio(value(ink), surface) >= 4.5, (ink, surface)
    primary = re.search(r"\.primary\s*\{([^}]*)\}", page_text).group(1)
    ink = re.search(r"color:\s*(#[0-9a-fA-F]{6})", primary).group(1)
    assert ratio(value("accent"), ink) >= 4.5
    hover = re.search(r"\.primary:hover:not\(:disabled\)\s*\{\s*background:\s*(#[0-9a-fA-F]{6})",
                      page_text)
    assert hover and ratio(hover.group(1), ink) >= 4.5


def test_review_requests_are_guarded_by_the_review_pane(page_text):
    # Every review-scoped request must sit behind an explicit review-pane
    # condition, so the annotate pane can never fetch a model draft.
    positions = [match.start() for match in re.finditer(r"\?review=1", page_text)]
    assert len(positions) == 3  # pane switch, task open, review queue
    for position in positions:
        assert "reviewPane" in page_text[max(0, position - 320):position + 120]


# ─── server gate: no draft metadata in the annotate pane ───────────────────


@pytest.fixture
def studio_server(tmp_path, setup):
    store, task, *_ = setup
    proposal = store.save(task, "model-proposal:test",
                          dict(annotation(), notes="DRAFT-SECRET-MARKER"),
                          method="model_proposal", expected_revision=None)
    server = create_server(tmp_path / "records.sqlite", rater="reviewer",
                           method="human", mode="studio", token="studio-token", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, *, method="GET", body=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        headers = {"Authorization": "Bearer studio-token"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=json.dumps(body) if body is not None else None,
                         headers=headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    try:
        yield request, proposal
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_annotate_pane_never_sees_draft_metadata_or_body(studio_server):
    request, proposal = studio_server
    status, queue = request("/api/tasks")
    assert status == 200
    row = queue["tasks"][0]
    assert "has_model_proposal" not in row and "has_attestation" not in row
    name = row["id"]
    status, view = request("/api/tasks/" + name)
    assert status == 200
    assert set(view) & {"proposal", "proposals", "attestations"} == set()
    serialized = json.dumps(view)
    assert "DRAFT-SECRET-MARKER" not in serialized
    assert proposal["revision_id"] not in serialized

    status, review_queue = request("/api/tasks?review=1")
    assert status == 200
    assert review_queue["tasks"][0]["has_model_proposal"] is True
    assert review_queue["tasks"][0]["has_attestation"] is False
    status, review = request("/api/tasks/" + name + "?review=1")
    assert status == 200
    assert review["proposal"]["revision_id"] == proposal["revision_id"]
    assert "DRAFT-SECRET-MARKER" in json.dumps(review)


def test_review_pane_cannot_write_annotations_in_studio_mode(studio_server):
    request, _ = studio_server
    name = request("/api/tasks")[1]["tasks"][0]["id"]
    # Annotation writes stay available for the annotate pane ...
    status, _ = request("/api/tasks/" + name + "/annotations", method="POST",
                        body={"expected_revision": None, "annotation": annotation()})
    assert status == 200
    # ... while a review-only statement is the only review-pane write path.
    status, record = request("/api/tasks/" + name + "/attestations", method="POST",
                             body={"proposal_revision_id": request(
                                 "/api/tasks/" + name + "?review=1")[1]["proposal"]["revision_id"],
                                 "statement": "已核对证据与草稿。"})
    assert status == 200 and record["signature"]["cryptographic"] is False
    status, view = request("/api/tasks/" + name)
    assert "attestations" not in view


def test_annotate_mode_rejects_review_query(setup, tmp_path):
    server = create_server(tmp_path / "records.sqlite", rater="alice",
                           method="human", mode="annotate", token="annotate-token", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        conn.request("GET", "/api/tasks?review=1",
                     headers={"Authorization": "Bearer annotate-token"})
        response = conn.getresponse()
        assert response.status == 400
        conn.close()
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


# ─── scale ─────────────────────────────────────────────────────────────────


def test_five_thousand_event_task_serves_bounded_and_caches(tmp_path):
    inputs = fixture_inputs(tmp_path)
    raw = inputs["sources"]["runner"]["path"]
    events, lines, offset = [], [], 0
    for index in range(5000):
        event_id = "event-%d" % index
        line = json.dumps({"native_id": "r%d" % index, "pid": 42,
                           "kind": "assistant_message", "text": "step %d" % index}) + "\n"
        payload = line.encode()
        events.append({"event_id": event_id, "source_id": "runner",
                       "record_id": "r%d" % index, "timestamp": str(index),
                       "clock_domain": "runner-monotonic-ns",
                       "derivation": "native record id", "byte_offset": offset,
                       "byte_length": len(payload),
                       "record_sha256": hashlib.sha256(payload).hexdigest()})
        offset += len(payload)
        lines.append(line)
    raw.write_text("".join(lines))
    inputs["events"] = events
    build_bundle(tmp_path / "bundle", **inputs)
    store = RecordStore(tmp_path / "records.sqlite")
    store.register_bundle(tmp_path / "bundle")
    server = create_server(tmp_path / "records.sqlite", rater="alice",
                           method="human", mode="studio", token="scale-token", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def get(path):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
        conn.request("GET", path, headers={"Authorization": "Bearer scale-token"})
        response = conn.getresponse()
        body = response.read()
        conn.close()
        return response.status, json.loads(body)

    try:
        started = time.perf_counter()
        status, view = get("/api/tasks/task-0001")
        first = time.perf_counter() - started
        assert status == 200
        assert len(view["events"]) == 5000
        assert all(event["label"] for event in view["events"])
        started = time.perf_counter()
        status, cached = get("/api/tasks/task-0001")
        replay = time.perf_counter() - started
        assert status == 200 and len(cached["events"]) == 5000
        assert first < 5.0 and replay < 1.5, (first, replay)
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


# ─── event localization ────────────────────────────────────────────────────


def line(value):
    return json.dumps(value, ensure_ascii=False).encode()


def test_collector_rows_are_localized_while_raw_audit_values_remain():
    summary = summarize_event(line({
        "kind": "row", "table": "actions", "primary_id": "a1",
        "row": {"action_type": "FileOpen", "outcome": "succeeded", "pid": 42,
                "normalized_args_json": '[{"Path":"/etc/passwd"}]',
                "syscall_name": "openat"}}), source_id="collector")
    assert summary["label"] == "文件读取"
    assert "FileOpen" in summary["summary"] and "/etc/passwd" in summary["summary"]
    assert "打开" in summary["summary"] and "成功" in summary["summary"]
    fields = {field["key"]: field["value"] for field in summary["fields"]}
    assert fields["结果"] == "succeeded"  # raw outcome preserved for audit

    alert = summarize_event(line({
        "kind": "row", "table": "responsibility_alerts",
        "row": {"decision": "Unsafe", "severity": "Critical", "title": "shadow read"}}),
        source_id="collector")
    assert "Unsafe" in alert["summary"] and "不安全" in alert["summary"]
    assert "Critical" in alert["summary"] and "严重" in alert["summary"]

    claim = summarize_event(line({
        "kind": "row", "table": "responsibility_claims",
        "row": {"claim_type": "PolicyViolation", "claim_state": "Confirmed",
                "severity": "Warning"}}), source_id="collector")
    assert claim["label"] == "系统声明"
    assert "策略违规" in claim["summary"] and "已确认" in claim["summary"]


def test_identity_and_policy_rows_use_chinese_labels():
    identity = summarize_event(line({
        "kind": "row", "table": "process_events",
        "row": {"comm": "node", "pid": 7}}), source_id="collector")
    assert "进程事件" in identity["summary"]
    policy = summarize_event(line({
        "kind": "row", "table": "policy_evaluations",
        "row": {"boundary": "Taint", "confidence": "high"}}), source_id="collector")
    assert "污点" in policy["summary"]
    assert policy["fields"][0]["key"] == "边界"
