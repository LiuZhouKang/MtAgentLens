"""BenchmarkWorkspace — single source of truth for all filesystem paths.

Replaces the fragile `Path(__file__).resolve().parents[N]` pattern
used across engine modules. One workspace, auto-discovered, shared
by all modules.

Discovery order:
  1. MTAGENTPRISM_BENCHMARK_HOME environment variable
  2. Walk upward from cwd looking for pyproject.toml + scenarios/
  3. Walk upward from this file's location
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BenchmarkWorkspace:
    """Immutable workspace paths — compute once, use everywhere."""

    root: Path          # env/ directory
    scenarios_dir: Path
    fixtures_dir: Path
    runs_dir: Path
    config_dir: Path
    roles_dir: Path
    project_root: Path  # mtagentlens/ (git root)

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def discover(cls, start: Path | str | None = None) -> BenchmarkWorkspace:
        """Auto-discover the benchmark workspace.

        Tries, in order:
        1. MTAGENTPRISM_BENCHMARK_HOME env var
        2. Walk upward from `start` (defaults to cwd)
        3. Walk upward from this module's location
        """
        # Env var takes priority
        env_home = os.environ.get("MTAGENTPRISM_BENCHMARK_HOME")
        if env_home:
            return cls._from_root(Path(env_home))

        # Walk upward from start
        search = Path(start) if start else Path.cwd()
        for parent in [search, *search.parents]:
            if cls._is_workspace(parent):
                return cls._from_root(parent)

        # Walk upward from this module
        module_dir = Path(__file__).resolve().parent
        for parent in [module_dir, *module_dir.parents]:
            if cls._is_workspace(parent):
                return cls._from_root(parent)

        # Last resort: assume cwd IS the workspace
        return cls._from_root(Path.cwd())

    # ── Path helpers ─────────────────────────────────────────────────

    def scenario_path(self, scenario_id: str) -> Path:
        """Path to a scenario JSON file — searches categorized subdirectories (and nested)."""
        for cat_dir in sorted(self.scenarios_dir.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name in ("fixtures", "archive", "_archive"):
                continue
            # Check flat file in category dir
            candidate = cat_dir / f"{scenario_id}.json"
            if candidate.exists():
                return candidate
            # Check nested subdirectories (e.g. multi_agent_attack/agent_teams/)
            if cat_dir.is_dir():
                for sub_dir in sorted(cat_dir.iterdir()):
                    if sub_dir.is_dir() and not sub_dir.name.startswith("_"):
                        candidate = sub_dir / f"{scenario_id}.json"
                        if candidate.exists():
                            return candidate
        # Fallback: flat structure (legacy)
        return self.scenarios_dir / f"{scenario_id}.json"

    def fixture_path(self, scenario_id: str) -> Path:
        """Path to a scenario's fixture directory.

        Note: the actual fixture directory is resolved by the loader from
        the scenario JSON's `fixture_dir` field, not from the scenario ID.
        This method returns a best-effort default path.
        """
        return self.fixtures_dir / scenario_id

    def all_scenario_paths(self) -> list[Path]:
        """Return all scenario JSON paths from categorized subdirectories (and nested)."""
        paths = []
        for cat_dir in sorted(self.scenarios_dir.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name in ("fixtures", "archive", "_archive"):
                continue
            # Flat files in category dir
            for sf in sorted(cat_dir.glob("*.json")):
                paths.append(sf)
            # Nested subdirectories
            for sub_dir in sorted(cat_dir.iterdir()):
                if sub_dir.is_dir() and not sub_dir.name.startswith("_"):
                    for sf in sorted(sub_dir.glob("*.json")):
                        paths.append(sf)
        return paths

    def run_dir(self, run_id: str) -> Path:
        """Path to a run output directory."""
        return self.runs_dir / run_id

    # ── Internal ─────────────────────────────────────────────────────

    @staticmethod
    def _is_workspace(path: Path) -> bool:
        """Check if a directory looks like the benchmark workspace root."""
        return (
            (path / "scenarios").is_dir()
            and (path / "pyproject.toml").exists()
        )

    @classmethod
    def _from_root(cls, root: Path) -> BenchmarkWorkspace:
        root = root.resolve()
        return cls(
            root=root,
            scenarios_dir=root / "scenarios",
            fixtures_dir=root / "scenarios" / "fixtures",
            runs_dir=root / "runs",
            config_dir=root / "config",
            roles_dir=root / "scenarios" / "roles",
            project_root=root.parent,  # env/ → mtagentlens/
        )
