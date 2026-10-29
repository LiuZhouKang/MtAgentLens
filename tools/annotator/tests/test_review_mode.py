# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Review mode shows a model draft and records statements, never annotations."""

import http.client
import json
import threading

import pytest

from tools.annotator.tests.test_records import annotation, setup  # noqa: F401
from tools.annotator.workbench import create_server


@pytest.fixture
def review_server(tmp_path, setup):
    store, task, *_ = setup
    proposal = store.save(task, "model-proposal:test", annotation(),
                          method="model_proposal", expected_revision=None)
    server = create_server(tmp_path / "records.sqlite", rater="reviewer",
                           method="human", mode="review", token="review-token", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, *, method="GET", body=None):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        headers = {"Authorization": "Bearer review-token"}
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
        yield request, task, proposal
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_review_session_exposes_draft_and_blocks_annotation_writes(review_server):
    request, task, proposal = review_server
    status, session = request("/api/session")
    assert status == 200 and session["mode"] == "review"
    status, queue = request("/api/tasks")
    assert status == 200
    entry = queue["tasks"][0]
    assert entry["has_model_proposal"] is True
    assert entry["has_attestation"] is False
    status, view = request("/api/tasks/" + entry["id"])
    assert status == 200
    assert view["proposal"]["revision_id"] == proposal["revision_id"]
    assert view["proposal"]["annotation"]["attempted"]["value"] == "yes"
    assert view["attestations"] == []
    status, _ = request("/api/tasks/" + entry["id"] + "/annotations", method="POST",
                        body={"expected_revision": None, "annotation": annotation()})
    assert status == 403


def test_attestation_endpoint_records_and_rejects_empty_or_duplicate(review_server):
    request, task, proposal = review_server
    name = request("/api/tasks")[1]["tasks"][0]["id"]
    path = "/api/tasks/" + name + "/attestations"
    status, payload = request(path, method="POST",
                              body={"proposal_revision_id": proposal["revision_id"],
                                    "statement": "   "})
    assert status == 400
    status, record = request(path, method="POST",
                             body={"proposal_revision_id": proposal["revision_id"],
                                   "statement": "我已查看证据与草稿。"})
    assert status == 200
    assert record["reviewer"] == "reviewer"
    assert record["signature"]["cryptographic"] is False
    status, view = request("/api/tasks/" + name)
    assert len(view["attestations"]) == 1
    assert request("/api/tasks")[1]["tasks"][0]["has_attestation"] is True
    status, _ = request(path, method="POST",
                        body={"proposal_revision_id": proposal["revision_id"],
                              "statement": "再次确认。"})
    assert status == 409


def test_review_mode_requires_a_human_statement_method(tmp_path, setup):
    with pytest.raises(ValueError):
        create_server(tmp_path / "records.sqlite", rater="reviewer",
                      method="controlled_execution", mode="review")



@pytest.fixture
def studio_server(tmp_path, setup):
    store, task, *_ = setup
    store.save(task, "model-proposal:test", annotation(),
               method="model_proposal", expected_revision=None)
    server = create_server(tmp_path / "records.sqlite", rater="studio-rater",
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
        yield request, task
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_studio_mode_keeps_annotation_writes_and_review_view_separate(studio_server):
    request, task = studio_server
    name = request("/api/tasks")[1]["tasks"][0]["id"]
    status, session = request("/api/session")
    assert status == 200 and session["mode"] == "studio"
    status, _ = request("/api/tasks/" + name + "/annotations", method="POST",
                        body={"expected_revision": None, "annotation": annotation()})
    assert status == 200
    # The annotate pane never receives draft metadata or draft bodies.
    assert "has_model_proposal" not in request("/api/tasks")[1]["tasks"][0]
    status, plain = request("/api/tasks/" + name)
    assert status == 200
    assert set(plain) & {"proposal", "proposals", "attestations"} == set()
    # Only an explicit review read exposes the draft and its metadata.
    review_queue = request("/api/tasks?review=1")[1]["tasks"][0]
    assert review_queue["has_model_proposal"] is True
    status, review = request("/api/tasks/" + name + "?review=1")
    assert status == 200, review
    assert len(review["proposals"]) == 1
    # The review view itself never accepts annotation writes: that route is
    # reserved for the annotate pane and still enforces parent revisions.
    assert request("/api/tasks/" + name + "/annotations", method="POST",
                   body={"expected_revision": None,
                         "annotation": annotation()})[0] == 409
