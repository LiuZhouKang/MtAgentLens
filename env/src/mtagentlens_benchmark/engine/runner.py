"""Batch scenario runner with parallel scheduling."""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
from pathlib import Path
from typing import Callable

from mtagentlens_benchmark.engine.executor import execute_scenario
from mtagentlens_benchmark.types import CollectorInterface, RunResult, ScenarioConfig
from mtagentlens_benchmark.workspace import BenchmarkWorkspace


def run_scenarios(
    scenarios: list[ScenarioConfig],
    workspace: BenchmarkWorkspace,
    parallel: int = 1,
    collector_factory: Callable[[], CollectorInterface] | None = None,
    execution_mode: str = "process",
) -> list[RunResult]:
    """Run multiple scenarios, optionally in parallel."""
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    results: list[RunResult] = []

    # Use UUID-based directory names to prevent Claude from inferring
    # the scenario type from the filesystem path (e.g. "prompt-injection"
    # in the path triggers safety refusal).
    import uuid as _uuid

    if parallel <= 1:
        for scenario in scenarios:
            run_dir = workspace.runs_dir / f"{timestamp}-{_uuid.uuid4().hex[:12]}"
            collector = collector_factory() if collector_factory else None
            result = execute_scenario(scenario, run_dir, workspace, execution_mode, collector)
            results.append(result)
            (run_dir / ".scenario_id").write_text(scenario.id)
            _save_summary(run_dir, result)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {}
            for scenario in scenarios:
                run_dir = workspace.runs_dir / f"{timestamp}-{_uuid.uuid4().hex[:12]}"
                collector = collector_factory() if collector_factory else None
                future = pool.submit(execute_scenario, scenario, run_dir, workspace, execution_mode, collector)
                futures[future] = scenario
            for future in concurrent.futures.as_completed(futures):
                scenario = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    results.append(RunResult(
                        run_id=f"err-{scenario.id}", scenario_id=scenario.id,
                        success=False, duration_seconds=0, error=str(e),
                    ))
    return results


def _save_summary(run_dir: Path, result: RunResult) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    summary = dict(result.summary)
    summary.update({
        "run_id": result.run_id,
        "scenario_id": result.scenario_id,
        "success": result.success,
        "duration_seconds": result.duration_seconds,
        "database_path": str(result.database_path) if result.database_path else None,
        "error": result.error,
    })
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8",
    )
