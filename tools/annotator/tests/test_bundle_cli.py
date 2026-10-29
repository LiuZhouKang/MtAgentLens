"""Executable raw bundle -> revision -> cohort export regression."""

import json
import subprocess
import sys
import selectors
from unittest.mock import patch
from pathlib import Path

from tools.annotator.tests.test_evidence import fixture_inputs
from tools.annotator.tests.test_records import annotation

ROOT = Path(__file__).resolve().parents[3]


def cli(*args, ok=True):
    result = subprocess.run([sys.executable, "-m", "tools.annotator", *map(str, args)],
                            cwd=ROOT, capture_output=True, text=True, timeout=10)
    if ok:
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    return result


def test_executable_roundtrip_and_stale_parent_and_output_guards(tmp_path):
    inputs = fixture_inputs(tmp_path)
    spec = dict(inputs)
    spec["release_root"] = "release"
    spec["sources"]["runner"]["path"] = "runner.jsonl"
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    root, db = tmp_path / "bundle", tmp_path / "records.sqlite"
    built = cli("build", "--spec", spec_path, "--out", root)
    registered = cli("register", "--store", db, "--bundle", root)
    assert registered["task_id"] == built["evidence_bundle_hash"]
    annotation_path = tmp_path / "annotation.json"
    annotation_path.write_text(json.dumps(annotation()))
    args = ("save", "--store", db, "--task", registered["task_id"], "--annotator", "worker",
            "--method", "model_proposal", "--annotation", annotation_path)
    first = cli(*args)
    cli(*args, ok=False)
    second = cli(*args, "--expected-revision", first["revision_id"])
    assert second["parent_revision_id"] == first["revision_id"]
    exported = tmp_path / "export.json"
    cli("export", "--store", db, "--out", exported)
    data = json.loads(exported.read_text())
    assert data["tasks"][0]["revisions"] == [first, second]
    assert data["paper_metrics_available"] is False
    before = exported.read_bytes()
    cli("export", "--store", db, "--out", exported, ok=False)
    assert exported.read_bytes() == before


def test_invalid_json_does_not_create_bundle(tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text('{"binding":{},"binding":{}}')
    cli("build", "--spec", spec, "--out", tmp_path / "bundle", ok=False)
    assert not (tmp_path / "bundle").exists()


def test_missing_store_is_not_created_by_export(tmp_path):
    store = tmp_path / "typo.sqlite"
    cli("export", "--store", store, "--out", tmp_path / "export.json", ok=False)
    assert not store.exists()
    assert not (tmp_path / "export.json").exists()


def test_publish_failure_leaves_no_partial_export_or_temporary_file(tmp_path):
    from tools.annotator import __main__ as entrypoint
    from tools.annotator.evidence import build_bundle
    from tools.annotator.records import RecordStore

    inputs = fixture_inputs(tmp_path)
    bundle, db = tmp_path / "bundle", tmp_path / "records.sqlite"
    build_bundle(bundle, **inputs)
    RecordStore(db).register_bundle(bundle)
    output = tmp_path / "export.json"
    before = set(tmp_path.iterdir())
    with patch.object(sys, "argv", ["annotator", "export", "--store", str(db), "--out", str(output)]), \
            patch("os.link", side_effect=OSError("simulated publish failure")):
        assert entrypoint.main() == 1
    assert not output.exists()
    assert set(tmp_path.iterdir()) == before


def test_serve_starts_a_local_fixed_rater_workbench(tmp_path):
    import http.client
    from urllib.parse import urlsplit
    from tools.annotator.evidence import build_bundle
    from tools.annotator.records import RecordStore

    inputs = fixture_inputs(tmp_path)
    build_bundle(tmp_path / "bundle", **inputs)
    store = tmp_path / "records.sqlite"
    RecordStore(store).register_bundle(tmp_path / "bundle")
    process = subprocess.Popen([sys.executable, "-m", "tools.annotator", "serve", "--store", str(store),
        "--rater", "alice", "--method", "controlled_execution", "--port", "0"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=5), "server did not start"
        line = process.stdout.readline().strip()
        assert line.startswith("http://127.0.0.1:")
        url = urlsplit(line)
        token = url.fragment.removeprefix("token=")
        conn = http.client.HTTPConnection(url.hostname, url.port, timeout=3)
        try:
            conn.request("GET", "/api/session", headers={"Authorization": "Bearer " + token})
            response = conn.getresponse()
            assert response.status == 200
            assert json.loads(response.read())["rater"] == "alice"
        finally:
            conn.close()
    finally:
        process.terminate()
        process.communicate(timeout=5)
