"""Abstract base class for scenario verifiers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from mtagentprism_benchmark.types import VerificationResult


class BaseVerifier(ABC):
    """All scenario verifiers must inherit from this class."""

    @abstractmethod
    def verify(self, run_dir: Path, scenario_dir: Path) -> VerificationResult:
        """Verify whether the scenario anomaly was correctly detected.

        Args:
            run_dir: Path to the run output directory (contains logs/, summary.json, DB)
            scenario_dir: Path to the scenario fixtures directory

        Returns:
            VerificationResult with status, evidence, and details
        """
        ...

    @property
    @abstractmethod
    def scenario_id(self) -> str:
        """Return the scenario ID this verifier handles."""
        ...
