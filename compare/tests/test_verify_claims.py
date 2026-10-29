# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Paper numbers must fail loudly when the artifact they cite changes."""

import json
from pathlib import Path

from scripts.evaluation.verify_claims import SCHEMA, check_claim, main, resolve


def write_manifest(tmp_path: Path, claims: list[dict]) -> Path:
    (tmp_path / "metrics.json").write_text(json.dumps({
        "benign": {"actionable": 0, "actionable_rate": 0.0, "decided": 12},
        "attack": {"actionable": 4, "actionable_rate": 4 / 24},
        "paper_metrics_available": False,
    }))
    manifest = tmp_path / "claims.json"
    manifest.write_text(json.dumps({
        "schema": SCHEMA, "measurement": "test", "artifact_root": str(tmp_path),
        "claims": claims,
    }))
    return manifest


def test_equalities_and_approximations_hold(tmp_path):
    manifest = write_manifest(tmp_path, [
        {"id": "a", "artifact": "metrics.json", "path": "benign.actionable", "equals": 0},
        {"id": "b", "artifact": "metrics.json", "path": "attack.actionable_rate",
         "close_to": 4 / 24, "tolerance": 1e-9},
        {"id": "c", "artifact": "metrics.json", "path": "paper_metrics_available", "equals": False},
    ])
    assert main(["--manifest", str(manifest)]) == 0


def test_a_changed_artifact_breaks_the_claim(tmp_path):
    manifest = write_manifest(tmp_path, [
        {"id": "a", "artifact": "metrics.json", "path": "benign.actionable", "equals": 7},
    ])
    assert main(["--manifest", str(manifest)]) == 1


def test_missing_artifact_or_path_is_a_failure(tmp_path):
    manifest = write_manifest(tmp_path, [
        {"id": "a", "artifact": "missing.json", "path": "x", "equals": 1},
        {"id": "b", "artifact": "metrics.json", "path": "nope.deeper", "equals": 1},
    ])
    assert main(["--manifest", str(manifest)]) == 1
    ok, detail = check_claim({"artifact": "metrics.json", "path": "nope", "equals": 1}, tmp_path)
    assert not ok and "path not found" in detail


def test_dotted_resolution_handles_lists(tmp_path):
    assert resolve({"a": [{"b": 2}]}, "a.0.b") == 2


def test_a_bad_manifest_schema_is_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "nope", "claims": []}))
    assert main(["--manifest", str(bad)]) == 2
