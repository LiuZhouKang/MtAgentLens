from __future__ import annotations

from pathlib import Path

import pytest

from compare.tools.base import AdapterCase, BoundaryType, RunStatus, ToolMetadata
from compare.tools.adapters.fixture_scanner import Adapter


def _metadata() -> ToolMetadata:
    return ToolMetadata(
        id="fixture-scanner",
        name="Fixture Scanner",
        tool_class="static",
        source_url="builtin://compare/tools/adapters/fixture_scanner.py",
        version="builtin",
        license="MtAgentLens-project",
        install=[],
        adapter="fixture_scanner",
        input_modes=["static"],
        score_scope="main_offline",
        inclusion_rationale="Built-in harness smoke adapter.",
    )


def test_fixture_scanner_detects_sensitive_file_patterns_from_fixture_files(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.json"
    scenario.write_text('{"id": "case-1"}', encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "environment-config.env").write_text("API_KEY=abc123", encoding="utf-8")
    case = AdapterCase(
        dataset_name="FuxiBench",
        scenario_id="case-1",
        scenario_json=scenario,
        fixture_dir=fixtures,
        saved_logs=[],
        input_artifacts={"scenario_json": scenario, "fixture_dir": fixtures},
    )

    result = Adapter(_metadata()).run(case, tmp_path / "out")

    assert result.status == RunStatus.SUCCESS
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.boundary_type == BoundaryType.RESOURCE
    assert finding.resource == "environment-config.env"
    assert finding.raw_output_path is not None
    assert finding.raw_output_path.exists()


def test_fixture_scanner_reports_no_findings(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.json"
    scenario.write_text('{"id": "case-1"}', encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "notes.txt").write_text("hello world", encoding="utf-8")
    case = AdapterCase(
        dataset_name="FuxiBench",
        scenario_id="case-1",
        scenario_json=scenario,
        fixture_dir=fixtures,
        saved_logs=[],
        input_artifacts={"scenario_json": scenario, "fixture_dir": fixtures},
    )

    result = Adapter(_metadata()).run(case, tmp_path / "out")

    assert result.status == RunStatus.NO_FINDINGS
    assert result.findings == ()


def test_fixture_scanner_rejects_unsafe_scenario_id(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.json"
    scenario.write_text('{"id": "../outside"}', encoding="utf-8")
    case = AdapterCase(
        dataset_name="FuxiBench",
        scenario_id="../outside",
        scenario_json=scenario,
        fixture_dir=None,
        saved_logs=[],
        input_artifacts={"scenario_json": scenario},
    )

    with pytest.raises(ValueError, match="scenario_id.*safe"):
        Adapter(_metadata()).run(case, tmp_path / "out")

    assert not (tmp_path / "outside.fixture-scanner.json").exists()


def test_fixture_scanner_detects_quoted_json_secret_keys(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.json"
    scenario.write_text('{"api_key": "abc123"}', encoding="utf-8")
    case = AdapterCase(
        dataset_name="FuxiBench",
        scenario_id="case-1",
        scenario_json=scenario,
        fixture_dir=None,
        saved_logs=[],
        input_artifacts={"scenario_json": scenario},
    )

    result = Adapter(_metadata()).run(case, tmp_path / "out")

    assert result.status == RunStatus.SUCCESS
    assert result.findings[0].resource == "scenario.json"


def test_fixture_scanner_skips_symlinks_and_large_files(tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.json"
    scenario.write_text('{"id": "case-1"}', encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    external = tmp_path / "external.env"
    external.write_text("API_KEY=external", encoding="utf-8")
    (fixtures / "external.env").symlink_to(external)
    (fixtures / "large.env").write_text("API_KEY=large\n" + ("x" * 1_048_576), encoding="utf-8")
    case = AdapterCase(
        dataset_name="FuxiBench",
        scenario_id="case-1",
        scenario_json=scenario,
        fixture_dir=fixtures,
        saved_logs=[],
        input_artifacts={"scenario_json": scenario, "fixture_dir": fixtures},
    )

    result = Adapter(_metadata()).run(case, tmp_path / "out")

    assert result.status == RunStatus.NO_FINDINGS
    assert result.findings == ()
