#!/usr/bin/env python3
"""Build frozen prompt inputs for fair prompt-level baseline evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_DATASET = PROJECT_ROOT / "compare" / "Dataset" / "FuxiBench"
DEFAULT_SCENARIOS_ROOT = PROJECT_ROOT / "env" / "scenarios"
TEXT_SUFFIXES = {
    ".cfg",
    ".conf",
    ".env",
    ".json",
    ".log",
    ".md",
    ".py",
    ".sample",
    ".sh",
    ".txt",
    ".ts",
    ".yaml",
    ".yml",
}
MAX_FIXTURE_FILE_BYTES = 128_000
MAX_TOTAL_FIXTURE_BYTES = 512_000
EVALUATOR_ONLY_TOKENS = (
    "ground_truth",
    "expected_claim",
    "responsibility.db",
    "responsibility_claim",
    "responsibility_alert",
    "mtagentprism_baseline",
    "trace_db",
)
PROMPT_TEXT_EXCLUDED_METADATA = (
    "scenario_id",
    "mode",
    "category",
    "attack_type",
    "label",
    "risk_level",
    "is_baseline",
)
EVALUATOR_ONLY_REDACTIONS = {
    "ground_truth": "[REDACTED_EVALUATOR_TOKEN]",
    "expected_claim": "[REDACTED_EVALUATOR_TOKEN]",
    "responsibility.db": "[REDACTED_EVALUATOR_TOKEN]",
    "responsibility_claim": "[REDACTED_EVALUATOR_TOKEN]",
    "responsibility_alert": "[REDACTED_EVALUATOR_TOKEN]",
    "mtagentprism_baseline": "[REDACTED_EVALUATOR_TOKEN]",
    "trace_db": "[REDACTED_EVALUATOR_TOKEN]",
}


def build_prompt_input_bundle(
    *,
    dataset_dir: Path,
    scenarios_root: Path,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    dataset_dir = dataset_dir.resolve()
    scenarios_root = scenarios_root.resolve()
    output_dir = (output_dir or dataset_dir / "prompt_inputs").resolve()
    _ensure_output_location(dataset_dir, output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    quality_manifest = _read_json(dataset_dir / "quality" / "manifest.json")
    samples = quality_manifest.get("samples", [])
    if not isinstance(samples, list):
        raise ValueError("quality manifest must contain samples list")

    scenario_index = _index_scenarios(scenarios_root)
    entries: list[dict[str, Any]] = []
    for sample in sorted(samples, key=lambda item: str(item.get("scenario_id", ""))):
        if not isinstance(sample, dict):
            raise ValueError("quality manifest sample must be an object")
        scenario_id = _safe_scenario_id(sample.get("scenario_id"))
        source_scenario_path = scenario_index.get(scenario_id)
        if source_scenario_path is None:
            raise FileNotFoundError(f"Missing source scenario for prompt input: {scenario_id}")
        scenario = _read_json(source_scenario_path)
        ground_truth_path = dataset_dir / sample.get("paths", {}).get("ground_truth", "")
        ground_truth = _read_json(ground_truth_path) if ground_truth_path.is_file() else {}
        entry = _build_entry(
            scenario_id=scenario_id,
            scenario=scenario,
            ground_truth=ground_truth,
            source_scenario_path=source_scenario_path,
            scenarios_root=scenarios_root,
            output_dir=output_dir,
        )
        entries.append(entry)

    manifest = {
        "schema": "mtagentprism.prompt_inputs.v1",
        "dataset": dataset_dir.name,
        "generated_from": {
            "dataset_dir": _display_path(dataset_dir),
            "quality_manifest": _display_path(dataset_dir / "quality" / "manifest.json"),
            "scenarios_root": _display_path(scenarios_root),
        },
        "total_cases": len(entries),
        "entries": entries,
        "no_ground_truth_leak_guarantee": True,
        "sanitization": {
            "excluded_artifacts": [
                "ground truth labels",
                "scenario identifiers",
                "scenario category labels",
                "attack-type labels",
                "risk-level labels",
                "expected claims",
                "responsibility databases",
                "responsibility alerts",
                "trace databases",
                "MtAgentPrism baseline outputs",
            ],
            "fixture_file_policy": {
                "text_suffixes": sorted(TEXT_SUFFIXES),
                "max_file_bytes": MAX_FIXTURE_FILE_BYTES,
                "max_total_fixture_bytes": MAX_TOTAL_FIXTURE_BYTES,
            },
            "redacted_text_tokens": sorted(EVALUATOR_ONLY_REDACTIONS),
            "prompt_text_excluded_metadata": sorted(PROMPT_TEXT_EXCLUDED_METADATA),
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _build_entry(
    *,
    scenario_id: str,
    scenario: dict[str, Any],
    ground_truth: dict[str, Any],
    source_scenario_path: Path,
    scenarios_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    scenario_dir = output_dir / "cases" / scenario_id
    scenario_dir.mkdir(parents=True)

    fixture_dir = _resolve_fixture_dir(scenario, scenarios_root)
    fixture_files = _collect_fixture_files(fixture_dir) if fixture_dir is not None else []
    if _requires_fixture_text(scenario, ground_truth) and not fixture_files:
        raise ValueError(
            f"prompt input for {scenario_id} requires fixture text but none was included"
        )
    agents = _agent_tasks(scenario)
    attack_type = str(ground_truth.get("attack_type") or scenario.get("attack_type") or "")
    prompt_text = _render_prompt_text(
        scenario=scenario,
        agents=agents,
        fixture_dir=fixture_dir,
        fixture_files=fixture_files,
    )
    _assert_no_evaluator_leak(prompt_text, scenario_id)

    prompt_path = scenario_dir / "prompt.txt"
    prompt_path.write_text(prompt_text, encoding="utf-8")
    bundle_json = {
        "schema": "mtagentprism.prompt_input_case.v1",
        "scenario_id": scenario_id,
        "source_scenario_path": _display_path(source_scenario_path),
        "mode": scenario.get("mode"),
        "category": scenario.get("category"),
        "attack_type": attack_type,
        "input_mode": "agent_task_plus_fixture_text",
        "agents": agents,
        "fixture": {
            "source_fixture_dir": _display_path(fixture_dir) if fixture_dir else None,
            "files": [
                {
                    "relative_path": rel_path,
                    "sha256": sha256,
                    "bytes": byte_count,
                }
                for rel_path, _text, sha256, byte_count in fixture_files
            ],
        },
        "sanitized_no_ground_truth_leak": True,
        "prompt_text_path": "prompt.txt",
        "prompt_text_sha256": _sha256_text(prompt_text),
    }
    bundle_path = scenario_dir / "bundle.json"
    bundle_path.write_text(json.dumps(bundle_json, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "scenario_id": scenario_id,
        "source_scenario_path": _display_path(source_scenario_path),
        "mode": scenario.get("mode"),
        "category": scenario.get("category"),
        "attack_type": attack_type,
        "input_mode": "agent_task_plus_fixture_text",
        "agent_count": len(agents),
        "fixture_file_count": len(fixture_files),
        "fixture_text_bytes": sum(item[3] for item in fixture_files),
        "sanitized_no_ground_truth_leak": True,
        "bundle_json_path": f"cases/{scenario_id}/bundle.json",
        "prompt_text_path": f"cases/{scenario_id}/prompt.txt",
        "prompt_text_sha256": _sha256_text(prompt_text),
    }


def _render_prompt_text(
    *,
    scenario: dict[str, Any],
    agents: list[dict[str, str]],
    fixture_dir: Path | None,
    fixture_files: list[tuple[str, str, str, int]],
) -> str:
    lines = [
        "# MtAgentPrism Prompt Baseline Input",
        "",
        "## Agent Tasks",
    ]
    for index, agent in enumerate(agents, start=1):
        lines.extend(
            [
                "",
                f"### Agent {index}",
                f"Role file: {agent.get('role_file', '')}",
                "",
                _redact_evaluator_tokens(agent.get("task", "").replace("{{FIXTURE_DIR}}", str(fixture_dir) if fixture_dir else "/tmp/mtap-fixtures").replace("<FIXTURE_DIR>", str(fixture_dir) if fixture_dir else "/tmp/mtap-fixtures").strip()),
            ]
        )

    lines.extend(["", "## Fixture Text"])
    if fixture_dir is None:
        lines.append("")
        lines.append("[No fixture directory was available for this scenario.]")
    elif not fixture_files:
        lines.append("")
        lines.append(f"[Fixture directory {_display_path(fixture_dir)} has no included text files.]")
    else:
        for rel_path, text, _sha256, _byte_count in fixture_files:
            lines.extend(
                [
                    "",
                    f"### {rel_path}",
                    "```text",
                    _redact_evaluator_tokens(text),
                    "```",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def _agent_tasks(scenario: dict[str, Any]) -> list[dict[str, str]]:
    agents = scenario.get("agents", [])
    if not isinstance(agents, list):
        raise ValueError("scenario agents must be a list")
    normalized: list[dict[str, str]] = []
    for index, agent in enumerate(agents):
        if not isinstance(agent, dict):
            raise ValueError(f"scenario agent at index {index} must be an object")
        normalized.append(
            {
                "role_file": str(agent.get("role_file", "")),
                "task": str(agent.get("task", "")),
            }
        )
    if not normalized and isinstance(scenario.get("description"), str):
        normalized.append({"role_file": "", "task": str(scenario["description"])})
    return normalized


def _collect_fixture_files(fixture_dir: Path) -> list[tuple[str, str, str, int]]:
    files: list[tuple[str, str, str, int]] = []
    total_bytes = 0
    for path in sorted(fixture_dir.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            byte_count = path.stat().st_size
        except OSError:
            continue
        if byte_count > MAX_FIXTURE_FILE_BYTES or total_bytes + byte_count > MAX_TOTAL_FIXTURE_BYTES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        relative_path = path.relative_to(fixture_dir).as_posix()
        files.append((relative_path, text, _sha256_text(text), byte_count))
        total_bytes += byte_count
    return files


def _resolve_fixture_dir(scenario: dict[str, Any], scenarios_root: Path) -> Path | None:
    fixture_name = scenario.get("fixture_dir")
    fixtures_dir = scenario.get("fixtures_dir")
    if not isinstance(fixture_name, str) or not fixture_name:
        return None
    root = Path(fixtures_dir) if isinstance(fixtures_dir, str) and fixtures_dir else scenarios_root / "fixtures"
    if not root.is_absolute():
        root = scenarios_root / root
    root = root.resolve()
    candidate = (root / fixture_name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"fixture_dir escapes fixtures root: {fixture_name}") from exc
    if not candidate.is_dir():
        fallback_root = (scenarios_root / "fixtures").resolve()
        fallback = (fallback_root / fixture_name).resolve()
        try:
            fallback.relative_to(fallback_root)
        except ValueError as exc:
            raise ValueError(f"fixture_dir escapes fallback fixtures root: {fixture_name}") from exc
        if fallback.is_dir():
            return fallback
        return None
    return candidate


def _requires_fixture_text(scenario: dict[str, Any], ground_truth: dict[str, Any]) -> bool:
    if scenario.get("fixture_optional") is True:
        return False
    attack_type = str(ground_truth.get("attack_type") or scenario.get("attack_type") or "")
    label = str(ground_truth.get("label") or "")
    return label != "benign" and attack_type in {"B", "C"}


def _index_scenarios(scenarios_root: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for path in sorted(scenarios_root.rglob("*.json")):
        if "/fixtures/" in path.as_posix() or "/archive/" in path.as_posix():
            continue
        try:
            scenario = _read_json(path)
        except json.JSONDecodeError:
            continue
        scenario_id = scenario.get("id")
        if isinstance(scenario_id, str):
            index.setdefault(scenario_id, path)
    return index


def _assert_no_evaluator_leak(text: str, scenario_id: str) -> None:
    lower = text.lower()
    leaked = [token for token in EVALUATOR_ONLY_TOKENS if token in lower]
    if leaked:
        raise ValueError(f"prompt input for {scenario_id} contains evaluator-only token(s): {', '.join(leaked)}")


def _ensure_output_location(dataset_dir: Path, output_dir: Path) -> None:
    del dataset_dir
    if output_dir.name in {"", ".", ".."}:
        raise ValueError("prompt input output_dir must be a concrete directory")


def _redact_evaluator_tokens(text: str) -> str:
    redacted = text
    for token, replacement in EVALUATOR_ONLY_REDACTIONS.items():
        redacted = _replace_case_insensitive(redacted, token, replacement)
    return _redact_prompt_metadata_lines(redacted)


def _redact_prompt_metadata_lines(text: str) -> str:
    import re

    metadata_line = re.compile(
        r"^(\s*)(scenario\s+id|mode|category|attack\s+type|label|risk[_\s-]*level|is[_\s-]*baseline)(\s*:).*$",
        flags=re.IGNORECASE,
    )
    redacted_lines = []
    for line in text.splitlines():
        redacted_lines.append(
            metadata_line.sub(r"\1[REDACTED_EVALUATOR_METADATA]", line)
        )
    trailing_newline = "\n" if text.endswith("\n") else ""
    return "\n".join(redacted_lines) + trailing_newline


def _replace_case_insensitive(text: str, needle: str, replacement: str) -> str:
    import re

    return re.sub(re.escape(needle), replacement, text, flags=re.IGNORECASE)


def _safe_scenario_id(value: object) -> str:
    if not isinstance(value, str) or "/" in value or "\\" in value or ".." in value or not value:
        raise ValueError(f"invalid scenario_id: {value!r}")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build frozen prompt inputs for prompt-level baselines.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--scenarios-root", default=str(DEFAULT_SCENARIOS_ROOT))
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args(argv)

    manifest = build_prompt_input_bundle(
        dataset_dir=Path(args.dataset),
        scenarios_root=Path(args.scenarios_root),
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )
    print(
        json.dumps(
            {
                "prompt_inputs": str((Path(args.output_dir) if args.output_dir else Path(args.dataset) / "prompt_inputs")),
                "total_cases": manifest["total_cases"],
                "schema": manifest["schema"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
