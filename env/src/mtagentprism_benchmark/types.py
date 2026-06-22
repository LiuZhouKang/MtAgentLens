"""Shared types for the benchmark framework."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal, Optional


class ScenarioMode(str, Enum):
    SINGLE_AGENT = "single_agent"
    AGENT_TEAMS = "agent_teams"
    SUBAGENTS = "subagents"


class RunMode(str, Enum):
    BATCH = "batch"
    TMUX_LIVE = "tmux-live"
    TMUX_TASK = "tmux-task"
    PROCESS = "process"


class Severity(str, Enum):
    CRITICAL = "Critical"
    WARNING = "Warning"
    INFO = "Info"


class AnnotationConfidence(str, Enum):
    CERTAIN = "certain"
    UNCERTAIN = "uncertain"


class VerificationStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INDETERMINATE = "indeterminate"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class AgentSpec:
    role_file: str
    task: str


@dataclass
class BypassDimensions:
    role_identity: str
    behavior_guidance: str
    mechanism: str
    metaphor_mapping: str
    expression_style: str
    knowledge_association: str
    situation_setting: str
    trigger_mode: str


@dataclass
class BypassStrategy:
    technique: Literal["classical_chinese", "none"]
    framework: str = ""
    dimensions: Optional[BypassDimensions] = None
    prompt_template: str = ""


@dataclass
class ScenarioConfig:
    id: str
    mode: ScenarioMode
    description: str
    agents: list[AgentSpec]
    fixtures_dir: Path
    tmux_layout: str = "even-horizontal"
    leader_role_file: str = ""
    pre_run_script: Optional[str] = None
    timeout_seconds: int = 300
    permission_mode: str = "default"
    bypass_strategy: Optional[BypassStrategy] = None
    category: str = ""
    is_baseline: bool = False
    stability_score: float = 0.0
    risk_level: Optional['RiskLevel'] = None
    model: str = "haiku"   # "haiku" | "sonnet"
    effort: str = "low"    # "low" | "medium" | "high" (Sonnet: use "low" to save tokens)


@dataclass
class RunResult:
    run_id: str
    scenario_id: str
    success: bool
    duration_seconds: float
    collector_output: Optional[Path] = None
    database_path: Optional[Path] = None
    task_logs: dict[str, Path] = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class VerificationResult:
    status: VerificationStatus
    scenario_id: str
    evidence: list[str] = field(default_factory=list)
    details: str = ""
    reason: Optional[str] = None


@dataclass
class DetectionMetrics:
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    time_window_tolerance_s: float = 5.0


@dataclass
class CausalChainMetrics:
    chain_completeness: float = 0.0
    link_precision: float = 0.0
    link_recall: float = 0.0
    broken_chains: int = 0
    avg_chain_length_match: float = 0.0


@dataclass
class IdentityMetrics:
    agent_precision: float = 0.0
    agent_recall: float = 0.0
    merging_errors: int = 0
    splitting_errors: int = 0
    process_mapping_accuracy: float = 0.0


@dataclass
class FalsePositiveMetrics:
    baseline_fp_rate: float = 0.0
    alert_fatigue_ratio: float = 0.0
    avg_fp_confidence: float = 0.0
    per_baseline_breakdown: dict[str, int] = field(default_factory=dict)


@dataclass
class RobustnessMetrics:
    ablation_results: dict[str, 'DetectionMetrics'] = field(default_factory=dict)
    signal_contribution: dict[str, float] = field(default_factory=dict)


@dataclass
class BenchmarkScore:
    detection: DetectionMetrics = field(default_factory=DetectionMetrics)
    causal_chain: CausalChainMetrics = field(default_factory=CausalChainMetrics)
    identity: IdentityMetrics = field(default_factory=IdentityMetrics)
    false_positive: FalsePositiveMetrics = field(default_factory=FalsePositiveMetrics)
    robustness: RobustnessMetrics = field(default_factory=RobustnessMetrics)

    @property
    def overall(self) -> float:
        import numpy as np
        sc = self.robustness.signal_contribution
        mean_contribution = float(np.mean(list(sc.values()))) if sc else 0.0
        return (
            self.detection.f1 * 0.35
            + self.causal_chain.chain_completeness * 0.20
            + (1.0 - self.false_positive.baseline_fp_rate) * 0.15
            + self.identity.process_mapping_accuracy * 0.15
            + mean_contribution * 0.15
        ) * 100


@dataclass
class AnnotationRecord:
    alert_id: str
    severity: Severity
    category: str
    causal_chain: list[dict]
    agent_id: str
    confidence: AnnotationConfidence
    evidence_refs: list[int]
    annotator: str  # "lzk1" | "lzk2" | "lzk3"


@dataclass
class GroundTruth:
    scenario_id: str
    annotator: str
    annotation_date: str
    alerts: list[AnnotationRecord]
    responsibility: dict = field(default_factory=dict)
    accountability: dict = field(default_factory=dict)  # deprecated: kept for backward compat
    baseline_comparison: dict = field(default_factory=dict)
    ablation_expectations: dict = field(default_factory=dict)
    kappa_score: float = 0.0
    annotation_quality: Literal["verified", "needs_review"] = "needs_review"


# ─── Collector Interface (tool-agnostic) ─────────────────────────────────


class CollectorInterface(ABC):
    """Abstract interface for any anomaly detection collector.

    MtAgentPrism's CollectorManager is ONE implementation. Third-party
    monitoring tools implement this interface to plug into the benchmark.
    The benchmark runner is completely collector-agnostic.
    """

    @abstractmethod
    def start(self, output_dir: Path) -> None:
        """Start collecting. Must be callable before agents run."""
        ...

    @abstractmethod
    def stop(self, wait_seconds: int = 5) -> None:
        """Stop collecting gracefully. Called after all agents complete."""
        ...

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """Whether the collector is currently active."""
        ...

    @property
    @abstractmethod
    def output_paths(self) -> list[Path]:
        """Paths to collector output files (DB, JSONL, logs)."""
        ...


class RiskLevel(str, Enum):
    """Scenario risk classification for safety transparency."""
    SAFE = "safe"                # No sensitive access, purely behavioral
    READ_SENSITIVE = "read_sensitive"  # Reads /etc/, .ssh/, /proc/ etc.
    NETWORK_TEST = "network_test"      # Makes curl/nmap connections
    MODIFY_SYSTEM = "modify_system"    # Could modify files, install packages
