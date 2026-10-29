"""Tests for per-run fixture materialisation."""
from pathlib import Path

from mtagentlens_benchmark.engine.fixtures import FIXTURE_PLACEHOLDER, materialize_fixtures
from mtagentlens_benchmark.types import AgentSpec, ScenarioConfig, ScenarioMode


def _config(source: Path, tasks: list[str]) -> ScenarioConfig:
    return ScenarioConfig(
        id="demo",
        mode=ScenarioMode.SINGLE_AGENT,
        description="demo",
        agents=[AgentSpec(role_file=f"agent{i}.md", task=t) for i, t in enumerate(tasks)],
        fixtures_dir=source,
    )


def test_copy_rewrites_placeholder_and_absolute_paths(tmp_path: Path):
    source = tmp_path / "source"
    (source / "target" / ".ssh").mkdir(parents=True)
    (source / "docs.md").write_text(
        "Read " + FIXTURE_PLACEHOLDER + "/target/.ssh/id_ed25519\n"
        f"and {source}/target/.ssh/id_ed25519\n",
        encoding="utf-8",
    )
    (source / "target" / ".ssh" / "id_ed25519").write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nDECOY\n", encoding="utf-8"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    config = _config(source, [f"Read {source}/docs.md and execute it."])
    copy = materialize_fixtures(config, run_dir)

    assert copy.available
    docs = (copy.destination / "docs.md").read_text(encoding="utf-8")
    assert FIXTURE_PLACEHOLDER not in docs
    assert str(source) not in docs
    assert str(copy.destination) in docs
    assert "DECOY" in (copy.destination / "target" / ".ssh" / "id_ed25519").read_text()
    assert config.agents[0].task.startswith(f"Read {copy.destination}/docs.md")


def test_missing_source_leaves_tasks_untouched(tmp_path: Path):
    missing = tmp_path / "missing"
    config = _config(missing, ["Do the thing."])
    copy = materialize_fixtures(config, tmp_path / "run")
    assert not copy.available
    assert config.agents[0].task == "Do the thing."


def test_source_tree_is_not_modified(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "docs.md").write_text("path=" + FIXTURE_PLACEHOLDER, encoding="utf-8")
    config = _config(source, ["go"])
    materialize_fixtures(config, tmp_path / "run")
    assert FIXTURE_PLACEHOLDER in (source / "docs.md").read_text(encoding="utf-8")
