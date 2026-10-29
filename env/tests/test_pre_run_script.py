"""Tests for scenario pre-run setup scripts."""
from pathlib import Path
from types import SimpleNamespace

from mtagentlens_benchmark.engine.executor import _run_pre_run_script
from mtagentlens_benchmark.engine.fixtures import FixtureCopy
from mtagentlens_benchmark.types import AgentSpec, ScenarioConfig, ScenarioMode


def _config(script: str | None, fixtures: Path) -> ScenarioConfig:
    config = ScenarioConfig(
        id="demo",
        mode=ScenarioMode.SINGLE_AGENT,
        description="demo",
        agents=[AgentSpec(role_file="a.md", task="t")],
        fixtures_dir=fixtures,
    )
    config.pre_run_script = script
    return config


def test_script_runs_with_fixture_and_run_dir(tmp_path: Path):
    root = tmp_path / "repo"
    script = root / "scripts" / "setup.sh"
    script.parent.mkdir(parents=True)
    script.write_text('echo "$FIXTURE_DIR" > "$RUN_DIR/setup.txt"\n', encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    fixtures = tmp_path / "fx"
    fixtures.mkdir()

    error = _run_pre_run_script(
        _config("scripts/setup.sh", fixtures),
        run_dir,
        SimpleNamespace(root=root),
        FixtureCopy(source=fixtures, destination=None),
    )

    assert error is None
    assert (run_dir / "setup.txt").read_text().strip() == str(fixtures)


def test_missing_script_is_reported(tmp_path: Path):
    error = _run_pre_run_script(
        _config("scripts/nope.sh", tmp_path),
        tmp_path,
        SimpleNamespace(root=tmp_path),
        FixtureCopy(source=tmp_path, destination=None),
    )
    assert error is not None and "not found" in error


def test_nonzero_exit_is_reported(tmp_path: Path):
    script = tmp_path / "fail.sh"
    script.write_text("echo boom >&2; exit 3\n", encoding="utf-8")
    error = _run_pre_run_script(
        _config(str(script), tmp_path),
        tmp_path,
        SimpleNamespace(root=tmp_path),
        FixtureCopy(source=tmp_path, destination=None),
    )
    assert error is not None and "exit 3" in error and "boom" in error


def test_no_script_is_a_noop(tmp_path: Path):
    assert (
        _run_pre_run_script(
            _config(None, tmp_path),
            tmp_path,
            SimpleNamespace(root=tmp_path),
            FixtureCopy(source=tmp_path, destination=None),
        )
        is None
    )
