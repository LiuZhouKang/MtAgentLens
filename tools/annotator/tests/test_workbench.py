"""Local capability-separated annotation and curator HTTP workflows."""

import http.client
import json
import threading
from contextlib import contextmanager

import pytest

from tools.annotator.tests.test_records import annotation, save, setup
from tools.annotator.workbench import create_server


@pytest.fixture
def serve(setup, tmp_path):
    @contextmanager
    def start(rater="alice", mode="annotate", reviewers=(), store_path=None,
              codebook_version=None):
        server = create_server(store_path or tmp_path / "records.sqlite", rater=rater,
                               method="controlled_execution", mode=mode,
                               reviewers=reviewers, token="test-" + rater, port=0,
                               codebook_version=codebook_version)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(path, *, method="GET", body=None, token=True, headers=None, raw=False):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            given = {"Authorization": "Bearer test-" + rater} if token else {}
            if body is not None:
                given["Content-Type"] = "application/json"
            given.update(headers or {})
            try:
                conn.request(method, path, body=json.dumps(body) if body is not None else None,
                             headers=given)
                response = conn.getresponse()
                data = response.read()
                return response.status, data if raw else json.loads(data)
            finally:
                conn.close()

        try:
            yield request
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()
    return start


def task_id(request):
    status, payload = request("/api/tasks")
    assert status == 200
    return payload["tasks"][0]["id"]


def test_independent_mode_never_returns_other_raters_or_private_bindings(setup, serve):
    store, task, _, _, _ = setup
    value = annotation()
    value["notes"] = "bob-secret-annotation"
    save(store, task, value, annotator="bob", method="human")
    with serve() as request:
        assert request("/api/tasks", token=False)[0] == 401
        name = task_id(request)
        status, payload = request("/api/tasks/" + name)
        assert status == 200
        assert payload["own"] is None
        assert payload["history"] == payload["comparisons"] == payload["differences"] == []
        serialized = json.dumps(payload)
        assert "bob-secret-annotation" not in serialized
        assert "data_release_id" not in serialized
        assert task not in serialized
        assert request("/api/tasks/" + name + "?rater=bob&reveal=1")[0] == 400
        assert request("/api/export")[0] == 403


def test_save_uses_fixed_rater_and_checked_parent_revision(setup, serve):
    store, task, *_ = setup
    with serve() as request:
        name = task_id(request)
        endpoint = "/api/tasks/" + name + "/annotations"
        payload = {"expected_revision": None, "annotation": annotation()}
        status, first = request(endpoint, method="POST", body=payload)
        assert status == 200
        assert "binding" not in first["own"]
        assert request(endpoint, method="POST", body=payload)[0] == 409
        assert request(endpoint, method="POST", body=dict(payload, rater="bob"))[0] == 400
    revisions = store.export()["tasks"][0]["revisions"]
    assert len(revisions) == 1
    assert revisions[0]["annotator"] == "alice"
    assert revisions[0]["method"] == "controlled_execution"


def test_evidence_preview_download_and_path_boundary(setup, serve):
    _, _, _, inputs, _ = setup
    with serve() as request:
        prefix = "/api/tasks/" + task_id(request) + "/events/"
        status, preview = request(prefix + "event-1")
        assert status == 200
        raw = inputs["sources"]["runner"]["path"].read_bytes()
        assert preview["text"] == raw.decode()
        assert preview["blinded"] is True
        assert preview["redactions"] == 0
        assert preview["event"]["source_kind"] == "controlled_runner"
        assert preview["truncated"] is False
        # Independent raters never receive the unblinded bytes.
        assert request(prefix + "event-1/raw")[0] == 403
        assert request(prefix + "event-1/redacted", raw=True) == (200, raw)
        assert request(prefix + "..%2Fsecret/raw")[0] == 404


def test_annotator_view_blinds_scenario_and_run_identifiers(tmp_path, serve):
    import hashlib

    from tools.annotator.evidence import build_bundle
    from tools.annotator.records import RecordStore
    from tools.annotator.tests.test_evidence import fixture_inputs

    fixture_root = tmp_path / "custom"
    fixture_root.mkdir()
    inputs = fixture_inputs(fixture_root)
    release = inputs["release_root"]
    manifest = json.loads((release / "release.json").read_text())
    manifest["case_bindings"] = {"delegation-chain": {}}
    manifest["release_id"] = hashlib.sha256(json.dumps(
        {key: value for key, value in manifest.items() if key != "release_id"},
        sort_keys=True).encode()).hexdigest()
    (release / "release.json").write_text(json.dumps(manifest))
    inputs["binding"].update(data_release_id=manifest["release_id"],
                             scenario_id="delegation-chain",
                             run_id="delegation-chain-a1b2c3d4")
    raw = inputs["sources"]["runner"]["path"]
    raw.write_text(json.dumps({
        "scenario_id": "delegation-chain",
        "run_id": "delegation-chain-a1b2c3d4",
        "cwd": "/tmp/delegation-chain/run", "pid": 42,
    }) + "\n")
    inputs["events"][0]["byte_length"] = raw.stat().st_size
    inputs["events"][0]["record_sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
    root = tmp_path / "blinded-bundle"
    build_bundle(root, **inputs)
    store_path = tmp_path / "blinded.sqlite"
    RecordStore(store_path).register_bundle(root)
    with serve(store_path=store_path) as request:
        prefix = "/api/tasks/" + task_id(request) + "/events/event-1"
        status, preview = request(prefix)
        assert status == 200
        assert preview["redactions"] == 3
        assert "delegation-chain" not in preview["text"]
        assert '"scenario_id": "blinded-' in preview["text"]
        assert preview["raw_sha256"] == inputs["events"][0]["record_sha256"]
        assert request(prefix + "/raw")[0] == 403
        status, redacted = request(prefix + "/redacted", raw=True)
        assert status == 200
        assert b"delegation-chain" not in redacted
        assert json.loads(redacted)["pid"] == 42
    with serve("curator", "adjudicate", ("alice", "bob"), store_path=store_path) as request:
        prefix = "/api/tasks/" + task_id(request) + "/events/event-1"
        status, exact = request(prefix + "/raw", raw=True)
        assert status == 200
        assert exact == raw.read_bytes()


def test_codebook_version_is_reported_and_mismatches_are_refused(setup, serve):
    with serve(codebook_version="pilot/1") as request:
        assert request("/api/session")[1]["codebook_version"] == "pilot/1"
        name = task_id(request)
        status, view = request("/api/tasks/" + name)
        assert status == 200
        assert view["codebook_version"] == "pilot/1"
    with serve(codebook_version="pilot/2") as request:
        assert request("/api/session")[1]["codebook_version"] == "pilot/2"
        name = task_id(request)
        status, payload = request("/api/tasks/" + name)
        assert status == 409 and payload["error"] == "codebook_mismatch"
        assert request("/api/tasks/" + name + "/annotations", method="POST",
                       body={"expected_revision": None,
                             "annotation": annotation()})[0] == 409


def test_wrong_host_origin_and_credentials_are_rejected(serve):
    with serve() as request:
        assert request("/api/tasks", headers={"Host": "attacker.invalid"})[0] == 403
        assert request("/api/tasks", headers={"Authorization": "Bearer test-bob"})[0] == 401
        assert request("/api/tasks", headers={"Authorization": "Bearer café"})[0] == 401
        assert request("/api/tasks", headers={"Origin": "https://attacker.invalid"})[0] == 403
        name = task_id(request)
        assert request("/api/tasks/" + name + "/annotations", method="POST",
                       body={"expected_revision": None, "annotation": annotation()},
                       headers={"Content-Type": "text/plain"})[0] == 415


def test_curator_compares_and_records_exact_source_heads(setup, serve):
    store, task, *_ = setup
    a, b = annotation(), annotation()
    a["identities"]["event-1"] = "actor-a"
    b["identities"]["event-1"] = "actor-b"
    alice = save(store, task, a, annotator="alice", method="human")
    bob = save(store, task, b, annotator="bob", method="human")
    with serve("curator", "adjudicate", ("alice", "bob")) as request:
        name = task_id(request)
        _, view = request("/api/tasks/" + name)
        assert {row["field"] for row in view["differences"]} == {"identities.event-1"}
        heads = {"alice": alice["revision_id"], "bob": bob["revision_id"]}
        assert view["reviewed_revisions"] == heads
        body = {"expected_revision": None, "annotation": a, "reviewed_revisions": heads,
                "rationale": "Checked original evidence."}
        status, saved = request("/api/tasks/" + name + "/annotations", method="POST", body=body)
        assert status == 200
        assert saved["own"]["review"]["reviewed_revisions"] == heads
        assert request("/api/export")[1]["paper_metrics_available"] is False
        save(store, task, a, annotator="alice", method="human", parent=alice["revision_id"])
        assert request("/api/tasks/" + name)[1]["review_stale"] is True
        body["expected_revision"] = saved["own"]["revision_id"]
        assert request("/api/tasks/" + name + "/annotations", method="POST", body=body)[0] == 409


def test_tampered_evidence_is_not_served_or_saved(setup, serve):
    _, _, root, _, bundle = setup
    with serve() as request:
        name = task_id(request)
        (root / bundle["sources"]["runner"]["path"]).write_text("tampered")
        assert request("/api/tasks/" + name)[0] == 422
        assert request("/api/tasks/" + name + "/events/event-1")[0] == 422
        assert request("/api/tasks/" + name + "/annotations", method="POST",
                       body={"expected_revision": None, "annotation": annotation()})[0] == 422


def test_model_proposals_cannot_be_adjudicated_as_independent_reviews(setup, serve):
    store, task, *_ = setup
    alice = save(store, task, annotator="alice", method="human")
    bob = save(store, task, annotator="bob", method="model_proposal")
    with serve("curator", "adjudicate", ("alice", "bob")) as request:
        name = task_id(request)
        body = {"expected_revision": None, "annotation": annotation(),
                "reviewed_revisions": {"alice": alice["revision_id"], "bob": bob["revision_id"]},
                "rationale": "not an independent human review"}
        assert request("/api/tasks/" + name + "/annotations", method="POST", body=body)[0] == 400


@pytest.mark.parametrize("mode,reviewers", [("annotate", ("alice", "bob")),
    ("adjudicate", ("alice",)), ("adjudicate", ("alice", "alice")),
    ("adjudicate", ("alice", "curator")), ("unsupported", ())])
def test_invalid_role_configuration_is_rejected(tmp_path, mode, reviewers):
    from tools.annotator.records import RecordStore
    store = tmp_path / "records.sqlite"
    RecordStore(store)
    with pytest.raises(ValueError):
        create_server(store, rater="curator", method="human", mode=mode, reviewers=reviewers)
    with pytest.raises(ValueError):
        create_server(store, rater="curator", method="human", codebook_version=" ")


def test_project_three_rater_adjudication_requires_every_current_revision(setup, serve):
    store, task, *_ = setup
    raters = ("刘周康", "唐浩钺", "刘锦样")
    # Synthetic records exercise the roster; no human participation is asserted.
    heads = {name: save(store, task, annotator=name, method="controlled_execution")["revision_id"]
             for name in raters[:2]}
    with serve("curator", "adjudicate", raters) as request:
        endpoint = "/api/tasks/" + task_id(request)
        payload = {"expected_revision": None, "annotation": annotation(),
                   "reviewed_revisions": heads.copy(), "rationale": "Synthetic adjudication."}
        assert request(endpoint + "/annotations", method="POST", body=payload)[0] == 400
        heads[raters[2]] = "missing-revision"
        payload["reviewed_revisions"] = heads.copy()
        assert request(endpoint + "/annotations", method="POST", body=payload)[0] == 409
        third = save(store, task, annotator=raters[2], method="controlled_execution")
        heads[raters[2]] = third["revision_id"]
        payload["reviewed_revisions"] = heads.copy()
        assert request(endpoint)[1]["reviewed_revisions"] == heads
        updated = save(store, task, annotator=raters[2], parent=third["revision_id"],
                       method="controlled_execution")
        assert request(endpoint + "/annotations", method="POST", body=payload)[0] == 409
        payload["reviewed_revisions"][raters[2]] = updated["revision_id"]
        status, saved = request(endpoint + "/annotations", method="POST", body=payload)
        assert status == 200
        assert set(saved["own"]["review"]["reviewed_revisions"]) == set(raters)
    revisions = store.export()["tasks"][0]["revisions"]
    assert len([row for row in revisions if row["annotator"] == "curator"]) == 1


def test_task_view_and_preview_expose_human_readable_event_summaries(setup, serve):
    with serve() as request:
        name = task_id(request)
        status, payload = request("/api/tasks/" + name)
        assert status == 200
        event = payload["events"][0]
        assert event["label"] and event["summary"]
        status, preview = request("/api/tasks/" + name + "/events/" + event["event_id"])
        assert status == 200
        assert preview["summary"]["label"] in ("原始记录", "采集记录")
        assert preview["summary"]["summary"]
