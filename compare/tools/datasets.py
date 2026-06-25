from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from .base import AdapterCase, BoundaryType, EvaluationCase, ExpectedClaim
from .normalize import normalize_severity

_DATASET_NAME = "FuxiBench"
_SAFE_SCENARIO_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

_BOUNDARY_BY_SIGNAL = {
    "ARGUMENT_MATCH": BoundaryType.RESOURCE,
    "PROCESS_LINEAGE": BoundaryType.RESOURCE,
    "TEMPORAL_PROXIMITY": BoundaryType.RESOURCE,
    "CONTENT_PATTERN": BoundaryType.CONTENT,
    "DELEGATION_PATTERN": BoundaryType.DELEGATION,
    "EXEC_ARG_PATTERN": BoundaryType.EXEC_ARG,
}

_EVALUATOR_ONLY_KEYS = {
    "groundtruth",
    "expectedclaims",
    "alerts",
    "responsibilitydb",
    "responsibilityclaims",
    "responsibilityalerts",
    "agentsightbaseline",
    "mtagentlensbaseline",
    "tracedb",
    "traces",
    "metrics",
    "evaluation",
    "label",
    "labels",
}
_UNSAFE_SCENARIO_STRING_TOKENS = (
    "groundtruth",
    "tracedb",
    "expectedclaim",
    "expectedclaims",
    "responsibilitydb",
    "responsibilityclaims",
    "responsibilityalerts",
    "agentsightbaseline",
    "mtagentlensbaseline",
)


def load_dataset(
    name: str,
    dataset_root: Path,
    generated_root: Path | None = None,
) -> "FrozenDatasetAdapter":
    if name != _DATASET_NAME:
        raise ValueError(f"Unsupported dataset: {name}")
    return FrozenDatasetAdapter(dataset_root / name, generated_root=generated_root)


class FrozenDatasetAdapter:
    def __init__(self, dataset_dir: Path, generated_root: Path | None = None):
        self.dataset_dir = dataset_dir
        self.scenarios_dir = dataset_dir / "scenarios"
        self.fixtures_dir = dataset_dir / "fixtures"
        self.ground_truth_dir = dataset_dir / "ground_truth"
        self.traces_dir = dataset_dir / "traces"
        self.generated_root = self._generated_root_for(dataset_dir, generated_root)
        self.generated_scenarios_dir = self.generated_root / "tool_inputs" / "scenarios"
        self.metadata_path = dataset_dir / "metadata.json"
        self.quality_manifest_path = dataset_dir / "quality" / "manifest.json"
        self.prompt_inputs_dir = dataset_dir / "prompt_inputs"
        self.prompt_manifest_path = self.prompt_inputs_dir / "manifest.json"
        self._quality_manifest_cache: dict[str, dict[str, Any]] | None = None
        self._prompt_manifest_cache: dict[str, dict[str, Any]] | None = None

    def _generated_root_for(self, dataset_dir: Path, generated_root: Path | None) -> Path:
        if generated_root is None:
            return Path(tempfile.mkdtemp(prefix="mtagentlens-frozen-dataset-")).resolve()

        resolved_dataset = dataset_dir.resolve()
        resolved_generated = generated_root.resolve()
        if resolved_generated == resolved_dataset or resolved_generated.is_relative_to(resolved_dataset):
            raise ValueError("generated_root must not be equal to or inside dataset_dir")
        if resolved_generated.exists() and not resolved_generated.is_dir():
            raise ValueError("generated_root must be a directory")
        resolved_generated.mkdir(parents=True, exist_ok=True)
        return resolved_generated

    def load_cases(self) -> list[EvaluationCase]:
        manifest = self._load_quality_manifest()
        scenario_ids = self._scenario_ids_for(manifest)

        cases: list[EvaluationCase] = []
        for scenario_id in scenario_ids:
            self._validate_scenario_id(scenario_id)
            manifest_sample = manifest.get(scenario_id, {})
            scenario_path = self._scenario_path_for(scenario_id)
            ground_truth_path = self._ground_truth_path_for(scenario_id, manifest_sample)
            scenario = _read_json(scenario_path) if scenario_path.exists() else {"id": scenario_id}
            comparator_scenario_path = self._comparator_scenario_path_for(scenario_id, scenario_path, scenario)
            ground_truth = _read_json(ground_truth_path)
            fixture_dir = self._fixture_dir_for(scenario_id, scenario)

            adapter_case = AdapterCase(
                dataset_name=_DATASET_NAME,
                scenario_id=scenario_id,
                scenario_json=comparator_scenario_path,
                fixture_dir=fixture_dir,
                saved_logs=(),
                input_artifacts=self._input_artifacts_for(scenario_id, comparator_scenario_path),
            )
            # Extract expected_behavior from quality manifest (where it actually lives)
            gt_summary = manifest_sample.get("ground_truth_summary", {})
            expected_behavior_data = gt_summary.get("expected_behavior") if isinstance(gt_summary, dict) else None
            evaluation_case = EvaluationCase(
                adapter_case=adapter_case,
                ground_truth_json=ground_truth_path,
                expected_claims=_expected_claims(ground_truth, expected_behavior_data),
                mtagentlens_baseline=self._mtagentlens_baseline(scenario_id),
                is_attack=_is_attack_ground_truth(ground_truth, ground_truth_path, manifest_sample),
            )
            cases.append(evaluation_case)

        return cases

    def _load_quality_manifest(self) -> dict[str, dict[str, Any]]:
        if self._quality_manifest_cache is not None:
            return self._quality_manifest_cache

        if not self.quality_manifest_path.exists():
            self._quality_manifest_cache = {}
            return {}

        manifest = _read_json(self.quality_manifest_path)
        samples = manifest.get("samples", [])
        if not isinstance(samples, list):
            raise ValueError("Frozen dataset quality manifest must contain samples list")

        by_id: dict[str, dict[str, Any]] = {}
        for sample in samples:
            if not isinstance(sample, dict):
                raise ValueError("Frozen dataset quality manifest sample must be an object")
            scenario_id = sample.get("scenario_id")
            self._validate_scenario_id(scenario_id)
            if scenario_id in by_id:
                raise ValueError(f"Duplicate scenario_id in quality manifest: {scenario_id}")
            by_id[str(scenario_id)] = sample
        self._quality_manifest_cache = by_id
        return by_id

    def _load_prompt_manifest(self) -> dict[str, dict[str, Any]]:
        if self._prompt_manifest_cache is not None:
            return self._prompt_manifest_cache
        if not self.prompt_manifest_path.exists():
            self._prompt_manifest_cache = {}
            return {}

        manifest = _read_json(self.prompt_manifest_path)
        entries = manifest.get("entries", [])
        if not isinstance(entries, list):
            raise ValueError("Frozen prompt input manifest must contain entries list")

        by_id: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Frozen prompt input manifest entry must be an object")
            scenario_id = entry.get("scenario_id")
            self._validate_scenario_id(scenario_id)
            if scenario_id in by_id:
                raise ValueError(f"Duplicate scenario_id in prompt input manifest: {scenario_id}")
            by_id[str(scenario_id)] = entry
        self._prompt_manifest_cache = by_id
        return by_id

    def _scenario_ids_for(self, manifest: dict[str, dict[str, Any]]) -> list[str]:
        if manifest:
            return sorted(manifest)

        metadata = _read_json(self.metadata_path)
        scenario_ids = metadata.get("scenario_ids", [])
        if not isinstance(scenario_ids, list):
            raise ValueError("Frozen dataset metadata must contain scenario_ids list")
        return [str(scenario_id) for scenario_id in scenario_ids]

    def _scenario_path_for(self, scenario_id: str) -> Path:
        direct = self.scenarios_dir / f"{scenario_id}.json"
        if direct.exists():
            return direct
        matches = sorted(self.scenarios_dir.rglob(f"{scenario_id}.json")) if self.scenarios_dir.exists() else []
        return matches[0] if matches else direct

    def _ground_truth_path_for(self, scenario_id: str, manifest_sample: dict[str, Any]) -> Path:
        manifest_path = _manifest_path(self.dataset_dir, manifest_sample, "ground_truth")
        if manifest_path is not None:
            return manifest_path

        direct = self.ground_truth_dir / f"{scenario_id}.json"
        if direct.exists():
            return direct

        matches = sorted(self.ground_truth_dir.rglob(f"{scenario_id}.json"))
        if not matches:
            raise FileNotFoundError(f"Missing ground truth for scenario: {scenario_id}")
        return matches[0]

    def _validate_scenario_id(self, scenario_id: object) -> None:
        if not isinstance(scenario_id, str):
            raise ValueError(f"Invalid scenario_id: {scenario_id!r}")
        if (
            not _SAFE_SCENARIO_ID.fullmatch(scenario_id)
            or "/" in scenario_id
            or "\\" in scenario_id
            or ".." in scenario_id
            or Path(scenario_id).is_absolute()
        ):
            raise ValueError(f"Invalid scenario_id: {scenario_id}")

    def _comparator_scenario_path_for(
        self,
        scenario_id: str,
        scenario_path: Path,
        scenario: dict[str, Any],
    ) -> Path:
        generated_path = self.generated_scenarios_dir / f"{scenario_id}.json"
        generated_path.parent.mkdir(parents=True, exist_ok=True)
        generated_path.write_text(
            json.dumps(self._sanitize_scenario_payload(scenario_id, scenario), indent=2) + "\n",
            encoding="utf-8",
        )
        return generated_path

    def _sanitize_scenario_payload(self, scenario_id: str, scenario: dict[str, Any]) -> dict[str, Any]:
        sanitized = _sanitize_comparator_value(scenario)
        if not isinstance(sanitized, dict):
            sanitized = {}
        sanitized.setdefault("id", scenario_id)

        fixture_name = sanitized.get("fixture_dir")
        if (
            isinstance(fixture_name, str)
            and fixture_name
            and self._is_safe_fixture_candidate(self.fixtures_dir / fixture_name)
            and (self.fixtures_dir / fixture_name).is_dir()
        ):
            return sanitized

        fallback_fixture = self.fixtures_dir / scenario_id
        if self._is_safe_fixture_candidate(fallback_fixture) and fallback_fixture.is_dir():
            sanitized["fixture_dir"] = scenario_id
        else:
            sanitized.pop("fixture_dir", None)
        return sanitized

    def _fixture_dir_for(self, scenario_id: str, scenario: dict[str, Any]) -> Path | None:
        fixture_name = scenario.get("fixture_dir")
        if isinstance(fixture_name, str) and fixture_name:
            candidate = self.fixtures_dir / fixture_name
            if self._is_safe_fixture_candidate(candidate) and candidate.is_dir():
                return candidate
        candidate = self.fixtures_dir / scenario_id
        if self._is_safe_fixture_candidate(candidate) and candidate.is_dir():
            return candidate
        return None

    def _is_safe_fixture_candidate(self, candidate: Path) -> bool:
        resolved_fixtures_dir = self.fixtures_dir.resolve()
        resolved_candidate = candidate.resolve()
        try:
            resolved_candidate.relative_to(resolved_fixtures_dir)
        except ValueError:
            return False
        return True

    def _mtagentlens_baseline(self, scenario_id: str) -> dict[str, Path]:
        manifest = self._load_quality_manifest()
        manifest_sample = manifest.get(scenario_id, {})
        trace_dir = self.traces_dir / scenario_id
        baseline: dict[str, Path] = {}

        responsibility_db = _manifest_path(self.dataset_dir, manifest_sample, "responsibility_db")
        if responsibility_db is None:
            responsibility_db = trace_dir / "responsibility.db"
        if responsibility_db.exists():
            baseline["responsibility_db"] = responsibility_db

        summary_json = _manifest_path(self.dataset_dir, manifest_sample, "summary_json")
        if summary_json is None:
            summary_json = trace_dir / "summary.json"
        if summary_json.exists():
            baseline["summary_json"] = summary_json

        return baseline

    def _input_artifacts_for(self, scenario_id: str, comparator_scenario_path: Path) -> dict[str, Path]:
        artifacts = {"scenario_json": comparator_scenario_path}
        prompt_entry = self._load_prompt_manifest().get(scenario_id)
        if prompt_entry is None:
            return artifacts
        prompt_path_value = prompt_entry.get("prompt_text_path")
        if not isinstance(prompt_path_value, str) or not prompt_path_value:
            return artifacts
        prompt_path = (self.prompt_inputs_dir / prompt_path_value).resolve()
        try:
            prompt_path.relative_to(self.prompt_inputs_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"Prompt input path escapes prompt_inputs root: {prompt_path_value}") from exc
        if prompt_path.exists():
            artifacts["prompt_text"] = prompt_path
        return artifacts


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_path(dataset_dir: Path, manifest_sample: dict[str, Any], key: str) -> Path | None:
    paths = manifest_sample.get("paths", {})
    if not isinstance(paths, dict):
        return None
    value = paths.get(key)
    if not isinstance(value, str) or not value:
        return None
    candidate = (dataset_dir / value).resolve()
    dataset_root = dataset_dir.resolve()
    try:
        candidate.relative_to(dataset_root)
    except ValueError as exc:
        raise ValueError(f"Manifest path escapes dataset root: {value}") from exc
    return candidate


def _sanitize_comparator_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, child in value.items():
            if _normalize_sanitizer_token(str(key)) in _EVALUATOR_ONLY_KEYS:
                continue
            sanitized_child = _sanitize_comparator_value(child)
            if sanitized_child is not None:
                sanitized[str(key)] = sanitized_child
        return sanitized
    if isinstance(value, list):
        return [child for child in (_sanitize_comparator_value(child) for child in value) if child is not None]
    if isinstance(value, str):
        normalized_value = _normalize_sanitizer_token(value)
        if any(token in normalized_value for token in _UNSAFE_SCENARIO_STRING_TOKENS):
            return None
        return value
    return value


def _normalize_sanitizer_token(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "", value).lower()


def _expected_claims(ground_truth: dict[str, Any], expected_behavior_data: dict[str, Any] | None = None) -> tuple[ExpectedClaim, ...]:
    claims: list[ExpectedClaim] = []
    # Priority: 1) explicit expected_behavior_data (from quality manifest)
    #           2) ground_truth JSON "expected_behaviors" (plural)
    #           3) ground_truth JSON "expected_behavior" (singular)
    behavior_data = expected_behavior_data
    if not isinstance(behavior_data, dict):
        behavior_data = ground_truth.get("expected_behaviors") or ground_truth.get("expected_behavior")
    if isinstance(behavior_data, dict):
        claims.extend(_expected_claims_v2(ground_truth, behavior_data))

    for alert in ground_truth.get("alerts", []):
        resources: list[str] = []
        commands: list[str] = []
        contents: list[str] = []

        for step in alert.get("causal_chain", []):
            action_args = step.get("action_args", {})
            if isinstance(action_args, dict):
                resources.extend(_collect_resource_candidates(action_args))
                commands.extend(_collect_command_candidates(action_args))
            tool_name = step.get("tool_name")
            if isinstance(tool_name, str) and tool_name:
                commands.append(tool_name)
            excerpt = step.get("intent_excerpt")
            if isinstance(excerpt, str) and excerpt:
                contents.append(excerpt)
            summary = step.get("summary")
            if isinstance(summary, str) and summary:
                contents.append(summary)

        for expected_claim in alert.get("expected_claims", []):
            claim_resources, claim_commands, claim_contents = _collect_expected_claim_candidates(expected_claim)
            resources.extend(claim_resources)
            commands.extend(claim_commands)
            contents.extend(claim_contents)

        claims.append(
            ExpectedClaim(
                alert_id=str(alert.get("alert_id", "")),
                severity=str(alert.get("severity", "Info")),
                category=str(alert.get("category", "unknown")),
                boundary_type=_infer_boundary(alert),
                resource_substrings=tuple(dict.fromkeys(resources)),
                command_substrings=tuple(dict.fromkeys(commands)),
                content_substrings=tuple(dict.fromkeys(contents)),
            )
        )

    return tuple(claims)


def _expected_claims_v2(ground_truth: dict[str, Any], behavior_data: dict[str, Any] | None = None) -> list[ExpectedClaim]:
    behavior = behavior_data if behavior_data is not None else ground_truth.get("expected_behavior", {})
    if not isinstance(behavior, dict):
        return []

    category = str(ground_truth.get("category") or "unknown")
    fallback_severity = _severity_from_min_decision(behavior.get("min_decision"))
    claims: list[ExpectedClaim] = []
    for index, expected in enumerate(behavior.get("expected_claims", [])):
        resources, commands, contents = _collect_expected_claim_candidates(expected)
        claims.append(
            ExpectedClaim(
                alert_id=f"{ground_truth.get('scenario_id', '')}:expected:{index}",
                severity=_severity_from_expected_claim(expected, fallback_severity),
                category=category,
                boundary_type=_boundary_for_candidates(resources, commands, contents),
                resource_substrings=tuple(dict.fromkeys(resources)),
                command_substrings=tuple(dict.fromkeys(commands)),
                content_substrings=tuple(dict.fromkeys(contents)),
            )
        )

    return claims


def _boundary_for_candidates(
    resources: list[str] | tuple[str, ...],
    commands: list[str] | tuple[str, ...],
    contents: list[str] | tuple[str, ...],
) -> BoundaryType | None:
    if commands:
        return BoundaryType.EXEC_ARG
    if resources:
        return BoundaryType.RESOURCE
    for content in contents:
        boundary = _boundary_from_expected_claim_text(content)
        if boundary is not None:
            return boundary
    if contents:
        return BoundaryType.CONTENT
    return None


def _is_attack_ground_truth(
    ground_truth: dict[str, Any],
    ground_truth_path: Path | None = None,
    manifest_sample: dict[str, Any] | None = None,
) -> bool:
    label = ground_truth.get("label")
    if label in {"attack", "benign"}:
        return label == "attack"
    attack_type = ground_truth.get("attack_type")
    if attack_type == "benign":
        return False
    if attack_type in {"A", "B", "C"}:
        return True
    if manifest_sample:
        category = manifest_sample.get("category")
        if category == "benign":
            return False
        if isinstance(category, str) and category:
            return True
    if ground_truth_path is not None:
        parts = set(ground_truth_path.parts)
        if "benign" in parts:
            return False
        if "ground_truth" in parts:
            return True
    return bool(ground_truth.get("alerts", []))


def _collect_resource_candidates(action_args: dict[str, Any]) -> list[str]:
    resources: list[str] = []
    for key in ("path", "host", "url"):
        value = action_args.get(key)
        if isinstance(value, str) and value:
            resources.append(value)
    return resources


def _collect_command_candidates(action_args: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    command = action_args.get("command")
    if isinstance(command, str) and command:
        commands.append(command)

    argv = action_args.get("argv")
    if isinstance(argv, list):
        commands.extend(str(part) for part in argv if part)

    return commands


def _collect_expected_claim_candidates(expected_claim: Any) -> tuple[list[str], list[str], list[str]]:
    resources: list[str] = []
    commands: list[str] = []
    contents: list[str] = []
    parts = expected_claim if isinstance(expected_claim, list) else [expected_claim]
    for part in parts:
        if not isinstance(part, str) or not part:
            continue
        structured = _collect_structured_expected_claim_candidates(part)
        if structured is not None:
            claim_resources, claim_commands, claim_contents = structured
            resources.extend(claim_resources)
            commands.extend(claim_commands)
            contents.extend(claim_contents)
            continue
        boundary = _boundary_from_expected_claim_text(part)
        if boundary == BoundaryType.RESOURCE:
            resources.append(_normalize_expected_claim_resource(part))
        elif boundary == BoundaryType.EXEC_ARG:
            commands.append(_normalize_expected_claim_command(part))
        elif boundary in (BoundaryType.CONTENT, BoundaryType.DELEGATION):
            contents.append(part)
        elif _looks_like_resource_hint(part):
            resources.append(part)
        elif _looks_like_command_hint(part):
            commands.append(part)
        elif _is_meaningful_expected_claim_content(part):
            contents.append(part)
    return resources, commands, contents


def _collect_structured_expected_claim_candidates(value: str) -> tuple[list[str], list[str], list[str]] | None:
    if not value.startswith("claim."):
        return None
    parts = [part for part in value.split(".") if part]
    resources: list[str] = []
    commands: list[str] = []
    contents: list[str] = []
    for index, part in enumerate(parts):
        if part == "claim" or part in {"Info", "Warning", "Critical", "High", "Low"}:
            continue
        if part == "boundary" and index + 2 < len(parts):
            boundary_text = _structured_segment(parts, index)
            boundary = _boundary_from_expected_claim_text(boundary_text)
            if boundary == BoundaryType.RESOURCE:
                resources.append(boundary_text)
            elif boundary == BoundaryType.EXEC_ARG:
                commands.append(boundary_text)
            else:
                contents.append(boundary_text)
            continue
        if part == "policy" and index + 2 < len(parts):
            contents.append(_structured_segment(parts, index))
            continue
        if part in {"content", "resource", "delegation", "role", "tool", "command", "exec"}:
            continue
        if part.endswith("Use") or part.endswith("Violation") or part.endswith("Anomaly"):
            contents.append(part)
    if not (resources or commands or contents):
        contents.append(value)
    return resources, commands, contents


def _structured_segment(parts: list[str], index: int) -> str:
    values = [parts[index]]
    for part in parts[index + 1 :]:
        if part in {"Info", "Warning", "Critical", "High", "Low"}:
            break
        if part in {"policy", "boundary"}:
            break
        values.append(part)
    return ".".join(values)


def _severity_from_min_decision(value: Any) -> str:
    if value == "Unsafe":
        return "Critical"
    if value == "Uncertain":
        return "Warning"
    return normalize_severity(str(value) if value is not None else None)


def _severity_from_expected_claim(expected: Any, fallback: str) -> str:
    parts = expected if isinstance(expected, list) else [expected]
    for part in parts:
        if not isinstance(part, str):
            continue
        tokens = [token for token in part.split(".") if token]
        for token in reversed(tokens):
            severity = normalize_severity(token)
            if severity != "Info" or token.lower() == "info":
                return severity
    return fallback


def _normalize_expected_claim_resource(value: str) -> str:
    if value.startswith("boundary.file."):
        return value.removeprefix("boundary.file.")
    return value


def _normalize_expected_claim_command(value: str) -> str:
    if value.startswith("boundary.tool."):
        return value.removeprefix("boundary.tool.")
    return value


def _is_meaningful_expected_claim_content(value: str) -> bool:
    return value.lower() not in {"policyviolation", "critical", "high", "warning", "info", "low"}


def _looks_like_resource_hint(value: str) -> bool:
    lower_value = value.lower()
    return (
        value.startswith(("/", "~"))
        or "." in Path(value).name
        or any(token in lower_value for token in ("/etc/", "proc/", "endpoints", "filename", "file"))
    )


def _looks_like_command_hint(value: str) -> bool:
    lower_value = value.lower()
    return any(token in lower_value for token in ("bash", "write", "edit", "command", "tool"))


def _infer_boundary(alert: dict[str, Any]) -> BoundaryType | None:
    for expected_claim in alert.get("expected_claims", []):
        parts = expected_claim if isinstance(expected_claim, list) else [expected_claim]
        for part in parts:
            if not isinstance(part, str) or not part:
                continue
            boundary = _boundary_from_expected_claim_text(part)
            if boundary is not None:
                return boundary

    for signal in alert.get("detection_signals", []):
        if signal in _BOUNDARY_BY_SIGNAL:
            return _BOUNDARY_BY_SIGNAL[signal]

    return None


def _boundary_from_expected_claim_text(value: str) -> BoundaryType | None:
    boundary_hint = value.lower()
    if any(token in boundary_hint for token in ("delegation", "role", "forgery", "blind_delegation")):
        return BoundaryType.DELEGATION
    if "policy.taint" in boundary_hint or "boundary.taint" in boundary_hint:
        return BoundaryType.RESOURCE
    if any(token in boundary_hint for token in ("tool", "bash", "write", "edit", "command")):
        return BoundaryType.EXEC_ARG
    if any(token in boundary_hint for token in ("file", "filesystem", "network", "audit-endpoint", "endpoint")):
        return BoundaryType.RESOURCE
    if any(token in boundary_hint for token in ("content", "prompt")):
        return BoundaryType.CONTENT
    return None
