"""The frozen manifest must not claim a commit that holds different bytes."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from compare.scripts.build.refresh_prompt_input_hashes import scenario_git_rev


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "scenario.json").write_text("{}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    return repo


def test_clean_tracked_file_reports_its_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    rev = scenario_git_rev(repo / "scenario.json", repo_root=repo)
    assert rev == _git(repo, "rev-parse", "HEAD")


def test_modified_worktree_file_is_marked_dirty(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "scenario.json").write_text('{"id": "changed"}\n', encoding="utf-8")
    rev = scenario_git_rev(repo / "scenario.json", repo_root=repo)
    assert rev.startswith("dirty:"), rev


def test_untracked_file_is_not_attributed_to_a_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "new.json").write_text("{}\n", encoding="utf-8")
    assert scenario_git_rev(repo / "new.json", repo_root=repo) == "uncommitted"


def test_refresh_cannot_bless_stale_fixture_or_prompt(tmp_path, synthetic_dataset, monkeypatch):
    from compare.scripts.build import refresh_prompt_input_hashes as hashes
    from compare.scripts.build.build_prompt_input_bundle import build_prompt_input_bundle
    output = tmp_path / "prompt_inputs"
    build_prompt_input_bundle(dataset_dir=synthetic_dataset,
        scenarios_root=synthetic_dataset / "scenarios", output_dir=output)
    monkeypatch.setattr(hashes, "BASE_DIR", output)
    monkeypatch.setattr(hashes, "MANIFEST_PATH", output / "manifest.json")
    hashes.refresh(write=True)
    assert hashes.refresh(write=False) == []
    (synthetic_dataset / "fixtures/attack-one/docs.md").write_text("Changed fixture material.\n")
    problems = hashes.refresh(write=False)
    assert any("fixture" in p for p in problems)
    with pytest.raises(ValueError, match="rebuild"):
        hashes.refresh(write=True)


def test_refresh_validates_all_scenario_ids_before_writing(tmp_path, synthetic_dataset, monkeypatch):
    import json
    from compare.scripts.build import refresh_prompt_input_hashes as hashes
    from compare.scripts.build.build_prompt_input_bundle import build_prompt_input_bundle
    output = tmp_path / "prompt_inputs"
    build_prompt_input_bundle(dataset_dir=synthetic_dataset,
        scenarios_root=synthetic_dataset / "scenarios", output_dir=output)
    monkeypatch.setattr(hashes, "BASE_DIR", output)
    monkeypatch.setattr(hashes, "MANIFEST_PATH", output / "manifest.json")
    scenario_path = synthetic_dataset / "scenarios/benign-one.json"
    scenario = json.loads(scenario_path.read_text())
    scenario["id"] = "wrong-id"
    scenario_path.write_text(json.dumps(scenario))
    writes = []
    monkeypatch.setattr(hashes, "dump_json", lambda *args: writes.append(args))
    with pytest.raises(SystemExit, match="scenario id mismatch"):
        hashes.refresh(write=True)
    assert writes == []
