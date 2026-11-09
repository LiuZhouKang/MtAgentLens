"""Scene HTTP API, static UI and CLI tests."""

from __future__ import annotations

import hashlib
import http.client
import json
import threading

import pytest

from tools.annotator.__main__ import build_parser, main
from tools.annotator.scene_catalog import load_scenarios
from tools.annotator.scene_records import SceneRecordStore
from tools.annotator.scene_server import create_scene_server
from tools.annotator.tests.test_scene_catalog import build_dataset
from tools.annotator.tests.test_scene_records import (
    CODEBOOK,
    RATERS,
    annotation,
    benign_annotation,
)


@pytest.fixture
def serve(tmp_path):
    dataset = build_dataset(tmp_path)
    catalog = load_scenarios(dataset)
    codebook = tmp_path / "codebook.md"
    codebook.write_text("test codebook\n", encoding="utf-8")
    store = SceneRecordStore(
        tmp_path / "scenes.sqlite", catalog, RATERS, CODEBOOK, codebook_path=codebook
    )
    server = create_scene_server(store, catalog, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, *, method="GET", body=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        try:
            connection.request(
                method, path, body=json.dumps(body) if body is not None else None,
                headers=headers,
            )
            response = connection.getresponse()
            raw = response.read()
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = None
            return response.status, payload, raw
        finally:
            connection.close()

    try:
        yield request, store, catalog, codebook
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _draft(request, rater, value, *, expected_revision=None, change_reason=None):
    return request(
        f"/api/scenes/{_case_id(request)}/draft",
        method="POST",
        body={
            "rater": rater,
            "annotation": value,
            "expected_revision": expected_revision,
            "change_reason": change_reason,
        },
    )


def _submit(request, rater, value, *, expected_revision=None, change_reason=None):
    return request(
        f"/api/scenes/{_case_id(request)}/submit",
        method="POST",
        body={
            "rater": rater,
            "annotation": value,
            "expected_revision": expected_revision,
            "change_reason": change_reason,
        },
    )


def _case_id(request):
    status, payload, _ = request("/api/scenes?rater=alice")
    assert status == 200
    return payload["scenes"][0]["scenario_id"]


def test_static_ui_and_queue_never_expose_gt_or_peer_records(serve):
    request, _, _, _ = serve
    status, _, raw = request("/")
    assert status == 200
    html = raw.decode("utf-8")
    for marker in (
        'aria-label="标注者"',
        'aria-label="场景列表"',
        "expected-behaviors",
        "adjudication-rationale",
        "source-hash",
        "场景级标签描述设计预期",
    ):
        assert marker in html

    status, payload, _ = request("/api/scenes?rater=alice")
    assert status == 200
    assert payload["raters"] == RATERS
    assert len(payload["scenes"]) == 1
    scene = payload["scenes"][0]
    assert scene["scenario_id"].startswith("case-")
    assert scene["scenario_id"] != "scene-a"
    assert scene["own"] is None
    assert scene["peers"] == []
    assert scene["comparison_visible"] is False
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "ground_truth" not in serialized
    assert "scenarios/scene-a.json" not in serialized
    assert "fixtures/ops/fixture-a" not in serialized
    assert '"id": "scene-a"' not in serialized
    assert '"category":' not in serialized
    assert "label" not in serialized
    assert "attack_type" not in serialized
    assert request("/api/scenes?rater=mallory")[0] == 400


def test_scene_detail_hides_another_raters_draft(serve):
    request, _, _, _ = serve
    _, queue, _ = request("/api/scenes?rater=alice")
    scene = queue["scenes"][0]
    case_id = scene["scenario_id"]
    value = annotation()
    value["evidence_paths"] = [scene["sources"][0]["path"]]
    status, saved, _ = _draft(request, "alice", value)
    assert status == 200
    revision_id = saved["revision_id"]
    assert saved["own"]["revision_id"] == revision_id
    assert saved["scene"]["own"]["revision_id"] == revision_id
    assert saved["own"]["annotation"]["evidence_paths"] == value["evidence_paths"]
    status, payload, _ = request(f"/api/scenes/{case_id}?rater=alice")
    assert status == 200
    assert payload["own"]["revision_id"] == revision_id
    assert payload["peers"] == []
    assert payload["codebook"]["classes"] == ["attack", "benign"]
    status, payload, _ = request(f"/api/scenes/{case_id}?rater=bob")
    assert status == 200
    assert payload["own"] is None
    assert payload["peers"] == []
    assert "credential-read" not in json.dumps(payload)
    assert "ground_truth" not in json.dumps(payload)
    assert request("/api/scenes/missing?rater=alice")[0] == 404


def test_submit_comparison_flow_and_validation_errors(serve):
    request, _, _, _ = serve
    case_id = _case_id(request)
    status, alice_draft, _ = _draft(request, "alice", annotation())
    assert status == 200
    assert _draft(request, "alice", annotation())[0] == 409
    bob_status, bob_submission, _ = _submit(request, "bob", benign_annotation())
    assert bob_status == 200
    assert _submit(request, "carol", benign_annotation())[0] == 200
    status, alice, _ = _submit(
        request, "alice", annotation(),
        expected_revision=alice_draft["revision_id"], change_reason="submit",
    )
    assert status == 200
    status, payload, _ = request(f"/api/scenes/{case_id}?rater=alice")
    assert payload["comparison_visible"] is True
    assert payload["comparison"]["visible"] is True
    assert "label" in payload["comparison"]["different_fields"]
    assert {peer["rater"] for peer in payload["peers"]} == {"bob", "carol"}
    assert payload["own"]["revision_id"] == alice["revision_id"]

    invalid = annotation()
    invalid["label"] = "unknown"
    status, error, _ = _submit(
        request, "bob", invalid, expected_revision=bob_submission["revision_id"],
        change_reason="test invalid label rejection",
    )
    assert status == 400
    assert "label" in error["error"]
    assert "ground_truth" not in json.dumps(error)
    assert request("/api/scenes/scene-a/submit", method="POST", body={"rater": "alice"})[0] == 400


def test_adjudication_and_export_are_locked_until_all_submit(serve):
    request, store, _, codebook = serve
    case_id = _case_id(request)
    assert request("/api/export")[0] == 409
    _submit(request, "alice", annotation())
    _submit(request, "bob", benign_annotation())
    assert request(
        f"/api/scenes/{case_id}/adjudicate",
        method="POST",
        body={"adjudicator": "alice", "annotation": benign_annotation(), "rationale": "early"},
    )[0] == 409
    _submit(request, "carol", benign_annotation())
    status, error, _ = request(
        f"/api/scenes/{case_id}/adjudicate",
        method="POST",
        body={"adjudicator": "alice", "annotation": benign_annotation(), "rationale": " "},
    )
    assert status == 400
    assert "rationale" in error["error"]
    status, adjudication, _ = request(
        f"/api/scenes/{case_id}/adjudicate",
        method="POST",
        body={
            "adjudicator": "alice",
            "annotation": benign_annotation(),
            "rationale": "Two raters read the fixture as benign.",
        },
    )
    assert status == 200
    assert adjudication["adjudication_id"]
    assert adjudication["rationale"] == "Two raters read the fixture as benign."

    status, export, _ = request("/api/export")
    assert status == 200
    assert export["schema"] == "mtagentlens.fuxibench-scene-export/v1"
    assert export["catalog_sha256"] == store.catalog_sha256
    assert export["codebook_sha256"] == hashlib.sha256(codebook.read_bytes()).hexdigest()
    assert export["agreement"]["available"] is True
    scenario = export["scenarios"][0]
    assert len(scenario["rater_chains"]) == 3
    assert scenario["adjudication"]["adjudication_id"] == adjudication["adjudication_id"]
    serialized = json.dumps(export)
    assert "ground_truth" not in serialized
    assert '\"category\":' not in serialized


def test_agreement_endpoint_is_locked_then_reports_nominal_fields(serve):
    request, _, _, _ = serve
    case_id = _case_id(request)
    assert request("/api/agreement")[0] == 409
    _submit(request, "alice", annotation())
    _submit(request, "bob", benign_annotation())
    _submit(request, "carol", benign_annotation())
    status, report, _ = request("/api/agreement")
    assert status == 200
    assert report["n_scenarios"] == 1
    assert report["fields"]["label"]["n_raters"] == 3
    assert report["fields"]["label"]["kappa"] == pytest.approx(-0.5)
    assert request(f"/api/scenes/{case_id}?rater=alice")[0] == 200


def test_cli_parser_and_scene_export_do_not_overwrite(serve, tmp_path):
    _, _, _, _ = serve
    parser = build_parser()
    args = parser.parse_args([
        "scene-serve", "--dataset", str(tmp_path), "--store", str(tmp_path / "scenes.sqlite"),
        "--rater", "alice", "--rater", "bob", "--rater", "carol",
        "--active-rater", "alice", "--codebook-version", CODEBOOK, "--port", "7419",
    ])
    assert args.command == "scene-serve"
    assert args.dataset == tmp_path
    assert args.rater == RATERS
    assert args.active_rater == "alice"
    assert args.port == 7419
    args = parser.parse_args([
        "scene-export", "--store", str(tmp_path / "scenes.sqlite"),
        "--out", str(tmp_path / "export.json"),
    ])
    assert args.command == "scene-export"

    out = tmp_path / "export.json"
    assert main(["scene-export", "--store", str(tmp_path / "scenes.sqlite"), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["schema"].startswith("mtagentlens.fuxibench")
    before = out.read_bytes()
    assert main(["scene-export", "--store", str(tmp_path / "scenes.sqlite"), "--out", str(out)]) == 1
    assert out.read_bytes() == before


def test_method_and_path_errors_are_json(serve):
    request, _, _, _ = serve
    status, payload, _ = request("/api/scenes/scene-a", method="POST", body={})
    assert status in (400, 404, 405)
    assert "error" in payload
    assert request("/api/unknown")[0] == 404


def test_real_fuxibench_queue_uses_opaque_case_and_material_names(tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "datasets" / "FuxiBench"
    catalog = load_scenarios(root)
    store = SceneRecordStore(
        tmp_path / "real-scenes.sqlite", catalog, RATERS, "fuxibench-2type/1",
        codebook_path=root / "docs" / "codebook.md",
    )
    server = create_scene_server(store, catalog, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        connection.request("GET", "/api/scenes?rater=alice")
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        assert response.status == 200
        assert len(payload["scenes"]) == 93
        aliases = [scene["scenario_id"] for scene in payload["scenes"]]
        assert len(set(aliases)) == 93
        serialized = json.dumps(payload, ensure_ascii=False)
        leaked_ids = [scenario["scenario_id"] for scenario in catalog
                      if scenario["scenario_id"] in serialized]
        assert not leaked_ids, f"scenario ids leaked: {leaked_ids}"
        source_paths = {source["path"] for scene in catalog for source in scene["sources"]}
        leaked_paths = [path for path in source_paths if path in serialized]
        assert (leaked_ids, leaked_paths) == ([], []), (leaked_ids, leaked_paths)
        aliases_only = all(source["path"].startswith("material-")
                           for scene in payload["scenes"] for source in scene["sources"])
        assert aliases_only, "a raw material path leaked"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
