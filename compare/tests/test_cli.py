from __future__ import annotations

from pathlib import Path

import pytest

import compare.run_tool_comparison as cli
from compare.run_tool_comparison import parse_args


def test_parse_args_defaults_to_frozen_all_tools() -> None:
    args = parse_args([])

    assert args.dataset == "FuxiBench"
    assert args.tools == "all"
    assert args.dataset_root == str(Path("datasets"))
    assert args.lockfile == str(Path("compare/tool-lock.json"))
    assert args.output == ""
    assert args.experimental is False


def test_parse_args_accepts_experimental_dataset() -> None:
    args = parse_args(["--dataset", "terminal-bench", "--tools", "fixture-scanner", "--experimental"])

    assert args.dataset == "terminal-bench"
    assert args.tools == "fixture-scanner"
    assert args.experimental is True


def test_main_resolves_default_paths_from_project_root(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_comparison(**kwargs) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.chdir("/tmp")
    monkeypatch.setattr(cli, "run_comparison", fake_run_comparison)

    exit_code = cli.main([])

    assert exit_code == 0
    assert captured["dataset_root"] == cli.PROJECT_ROOT / "datasets"
    assert captured["lockfile"] == cli.PROJECT_ROOT / "compare" / "tool-lock.json"
    assert captured["output_dir"] == cli.PROJECT_ROOT / "compare" / "results" / "tool-comparison" / "FuxiBench"


@pytest.mark.parametrize("tools", ["", ",", " , "])
def test_main_rejects_empty_tool_selection(tools: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--tools", tools])

    assert exc_info.value.code == 2
