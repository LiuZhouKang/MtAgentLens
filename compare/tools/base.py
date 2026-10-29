from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol


class BoundaryType(str, Enum):
    RESOURCE = "ResourceBoundary"
    NETWORK = "NetworkBoundary"
    CONTENT = "ContentBoundary"
    DELEGATION = "DelegationBoundary"
    EXEC_ARG = "ExecArgBoundary"


class RunStatus(str, Enum):
    SUCCESS = "success"
    NO_FINDINGS = "no_findings"
    FAILED = "failed"
    UNSUPPORTED_DATASET = "unsupported_dataset"
    SKIPPED_BY_SCOPE = "skipped_by_scope"


@dataclass(frozen=True)
class ExpectedClaim:
    alert_id: str
    severity: str
    category: str
    boundary_type: BoundaryType | None
    resource_substrings: tuple[str, ...] = field(default_factory=tuple)
    command_substrings: tuple[str, ...] = field(default_factory=tuple)
    content_substrings: tuple[str, ...] = field(default_factory=tuple)
    # Optional agent attribution (for identity ablation). When None,
    # agent matching is skipped (backward compatible).
    expected_agent_id: str | None = None
    resource_patterns: tuple[str, ...] = ()
    command_patterns: tuple[str, ...] = ()
    content_patterns: tuple[str, ...] = ()
    should_alert: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "resource_substrings", tuple(self.resource_substrings))
        object.__setattr__(self, "command_substrings", tuple(self.command_substrings))
        object.__setattr__(self, "content_substrings", tuple(self.content_substrings))


@dataclass(frozen=True)
class AdapterCase:
    dataset_name: str
    scenario_id: str
    scenario_json: Path
    fixture_dir: Path | None
    saved_logs: tuple[Path, ...]
    input_artifacts: Mapping[str, Path]

    def __post_init__(self) -> None:
        object.__setattr__(self, "saved_logs", tuple(self.saved_logs))
        object.__setattr__(self, "input_artifacts", MappingProxyType(dict(self.input_artifacts)))

    def to_adapter_payload(self) -> dict[str, object]:
        return {
            "dataset_name": self.dataset_name,
            "scenario_id": self.scenario_id,
            "scenario_json": str(self.scenario_json),
            "fixture_dir": str(self.fixture_dir) if self.fixture_dir else None,
            "saved_logs": [str(path) for path in self.saved_logs],
            "input_artifacts": {key: str(path) for key, path in self.input_artifacts.items()},
        }


@dataclass(frozen=True)
class EvaluationCase:
    adapter_case: AdapterCase
    ground_truth_json: Path | None
    expected_claims: tuple[ExpectedClaim, ...]
    mtagentlens_baseline: Mapping[str, Path]
    is_attack: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected_claims", tuple(self.expected_claims))
        object.__setattr__(self, "mtagentlens_baseline", MappingProxyType(dict(self.mtagentlens_baseline)))


@dataclass(frozen=True)
class FindingRecord:
    tool_id: str
    dataset_name: str
    scenario_id: str
    finding_id: str
    severity: str
    category: str
    boundary_type: BoundaryType | None
    resource: str | None
    command: str | None
    text_span: str | None
    evidence: str
    confidence: float
    raw_output_path: Path | None
    # Agent attribution (for identity ablation)
    agent_id: str | None = None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "tool_id": self.tool_id,
            "dataset_name": self.dataset_name,
            "scenario_id": self.scenario_id,
            "finding_id": self.finding_id,
            "severity": self.severity,
            "category": self.category,
            "boundary_type": self.boundary_type.value if self.boundary_type else None,
            "resource": self.resource,
            "command": self.command,
            "text_span": self.text_span,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "raw_output_path": str(self.raw_output_path) if self.raw_output_path else None,
        }


@dataclass(frozen=True)
class ToolMetadata:
    id: str
    name: str
    tool_class: str
    source_url: str
    version: str
    license: str
    install: tuple[str, ...]
    adapter: str
    input_modes: tuple[str, ...]
    score_scope: str
    inclusion_rationale: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "install", tuple(self.install))
        object.__setattr__(self, "input_modes", tuple(self.input_modes))


@dataclass(frozen=True)
class AdapterResult:
    tool: ToolMetadata
    scenario_id: str
    status: RunStatus
    findings: tuple[FindingRecord, ...]
    raw_output_path: Path | None
    stdout_path: Path | None
    stderr_path: Path | None
    exit_code: int | None
    duration_seconds: float
    failure_reason: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "findings", tuple(self.findings))

    def is_metric_eligible(self) -> bool:
        return self.status in {RunStatus.SUCCESS, RunStatus.NO_FINDINGS}


class ToolAdapter(Protocol):
    metadata: ToolMetadata

    def supports(self, case: AdapterCase) -> bool:
        ...

    def run(self, case: AdapterCase, output_dir: Path) -> AdapterResult:
        ...
