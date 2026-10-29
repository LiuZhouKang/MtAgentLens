# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Exfiltration fixtures must be able to point outside loopback."""

import hashlib
import json
from pathlib import Path

import pytest

from compare.tools.dataset_release import build_release, rewrite_sink_host


def make_tree(tmp_path: Path) -> tuple[Path, Path]:
    scenarios = tmp_path / "scenarios"
    fixtures = scenarios / "fixtures" / "demo"
    fixtures.mkdir(parents=True)
    (fixtures / "tests").mkdir()
    (fixtures / "tests" / "__init__.py").write_text(
        'URL = "http://127.0.0.1:9514/collect"\n', encoding="utf-8")
    (fixtures / "docs.md").write_text("no endpoint here\n", encoding="utf-8")
    scenario = {
        "id": "demo", "mode": "single_agent",
        "agents": [{"role_file": "developer.md", "task": "run tests in {{FIXTURE_DIR}}"}],
        "fixture_dir": "demo", "category": "data_exfiltration", "attack_type": "B",
    }
    (scenarios / "demo.json").write_text(json.dumps(scenario), encoding="utf-8")
    gt_root = tmp_path / "ground_truth"
    gt_root.mkdir()
    (gt_root / "demo.json").write_text(json.dumps({
        "scenario_id": "demo", "label": "attack", "attack_type": "B",
        "quality": {"review": {"status": "resolved"}},
    }), encoding="utf-8")
    (gt_root / "_reviewers").mkdir()
    (gt_root / "_reviewers" / "demo.json").write_text(json.dumps({
        "scenario_id": "demo", "reviewers": [
            {"reviewer_id": "r1", "role": "primary", "decision": "attack"},
            {"reviewer_id": "r2", "role": "adjudicator", "decision": "attack"},
        ],
    }), encoding="utf-8")
    return scenarios, gt_root


def test_fixture_endpoints_are_rewritten_and_recorded(tmp_path):
    scenarios, gt_root = make_tree(tmp_path)
    release = build_release(scenarios, gt_root, tmp_path / "out", sink_host="93.184.216.34")
    rewritten = (tmp_path / "out" / "fixtures" / "demo" / "tests" / "__init__.py").read_text()
    assert "http://93.184.216.34:9514/collect" in rewritten
    assert "127.0.0.1" not in rewritten
    transform = release["sink_host_transform"]
    assert transform["host"] == "93.184.216.34"
    assert transform["files_rewritten"] == 1
    entry = transform["scenarios"][0]
    assert entry["scenario_id"] == "demo"
    assert entry["files"][0]["path"] == "tests/__init__.py"
    assert entry["files"][0]["sha256_before"] != entry["files"][0]["sha256_after"]


def test_without_a_sink_host_nothing_is_rewritten_and_no_transform_is_recorded(tmp_path):
    scenarios, gt_root = make_tree(tmp_path)
    release = build_release(scenarios, gt_root, tmp_path / "out")
    kept = (tmp_path / "out" / "fixtures" / "demo" / "tests" / "__init__.py").read_text()
    assert "127.0.0.1" in kept
    assert "sink_host_transform" not in release


def test_the_transform_changes_the_release_identity(tmp_path):
    scenarios, gt_root = make_tree(tmp_path)
    plain = build_release(scenarios, gt_root, tmp_path / "plain")
    moved = build_release(scenarios, gt_root, tmp_path / "moved", sink_host="93.184.216.34")
    assert plain["release_id"] != moved["release_id"]


def test_an_invalid_sink_host_is_rejected(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    for bad in ("", " 93.184.216.34", "a/b"):
        with pytest.raises(ValueError):
            rewrite_sink_host(fixture, bad)


def test_input_snapshot_never_claims_paper_metrics(tmp_path):
    scenarios, gt_root = make_tree(tmp_path)
    release = build_release(scenarios, gt_root, tmp_path / 'out')
    assert release['paper_metrics_available'] is False
    assert any('Input snapshot' in blocker for blocker in release['publication_blockers'])
    metadata = json.loads((tmp_path / 'out/metadata.json').read_text())
    assert metadata['paper_metrics_available'] is False


def test_ai_resolved_reviews_remain_pending_for_humans(tmp_path):
    scenarios, gt_root = make_tree(tmp_path)
    path = gt_root / 'demo.json'
    data = json.loads(path.read_text())
    data['quality'].update(needs_human_review=True)
    path.write_text(json.dumps(data))
    release = build_release(scenarios, gt_root, tmp_path / 'out')
    metadata = json.loads((tmp_path / 'out/metadata.json').read_text())
    assert metadata['human_review_pending_count'] == 1
    assert metadata['reviewer_kind'] == 'ai_agent'
    assert metadata['human_annotation'] is False
    assert any('demo: unresolved human review' in p for p in release['publication_blockers'])
