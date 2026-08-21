"""Frozen dataset packager — builds the offline release artifact."""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

from mtagentlens_benchmark.types import ScenarioConfig


def build_frozen_dataset(
    run_results: list[tuple[ScenarioConfig, Path]],
    ground_truth_dir: Path,
    output_dir: Path,
    version: str = "1.0.0",
) -> Path:
    """Build a frozen dataset from pre-run scenario results.

    Args:
        run_results: List of (scenario_config, run_dir) tuples
        ground_truth_dir: Directory containing ground truth JSON files
        output_dir: Output directory for the frozen dataset
        version: Dataset version string

    Returns:
        Path to the frozen dataset directory
    """
    release_dir = output_dir / f"v{version}" / "frozen"
    _reset_directory(release_dir)

    metadata = {
        "name": "MtAgentLens Benchmark",
        "version": version,
        "release_date": str(date.today()),
        "num_scenarios": len(run_results),
        "schema_version": "1.0",
        "modes": {"single_agent": 0, "agent_teams": 0, "subagents": 0},
        "attack_categories": [],
    }

    scenarios_dir = release_dir / "scenarios"
    traces_dir = release_dir / "traces"
    gt_dir = release_dir / "ground_truth"

    for d in [scenarios_dir, traces_dir, gt_dir]:
        d.mkdir(parents=True)

    for scenario, run_dir in run_results:
        mode = scenario.mode.value
        metadata["modes"][mode] = metadata["modes"].get(mode, 0) + 1

        if scenario.category and scenario.category not in metadata["attack_categories"]:
            metadata["attack_categories"].append(scenario.category)

        scenario_trace_dir = traces_dir / scenario.id
        scenario_trace_dir.mkdir(exist_ok=True)

        db_src = _resolve_db(run_dir)
        if db_src and db_src.exists():
            shutil.copy2(db_src, scenario_trace_dir / db_src.name)

        jsonl_src = _resolve_jsonl(run_dir)
        if jsonl_src and jsonl_src.exists():
            shutil.copy2(jsonl_src, scenario_trace_dir / jsonl_src.name)

    # Copy ground truth
    gt_alerts_dir = gt_dir / "alerts"
    gt_alerts_dir.mkdir(exist_ok=True)
    for gt_file in ground_truth_dir.glob("*.json"):
        shutil.copy2(gt_file, gt_alerts_dir / gt_file.name)

    # Create train/val/test splits (60/20/20)
    all_ids = sorted(set(s.id for s, _ in run_results))
    n = len(all_ids)
    n_train = int(n * 0.6)
    n_val = int(n * 0.2)

    splits_dir = release_dir / "splits"
    splits_dir.mkdir(exist_ok=True)

    _write_json(splits_dir / "train_scenarios.json", all_ids[:n_train])
    _write_json(splits_dir / "val_scenarios.json", all_ids[n_train:n_train + n_val])
    _write_json(splits_dir / "test_scenarios.json", all_ids[n_train + n_val:])

    _write_json(release_dir / "metadata.json", metadata)
    _write_readme(release_dir, version)

    return release_dir


def _reset_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _resolve_db(run_dir: Path) -> Path | None:
    """Resolve the responsibility database."""
    collector_dir = run_dir / "collector"
    for candidate in (
        collector_dir / "collector.responsibility.db",
    ):
        if candidate.exists():
            return candidate
    return None


def _resolve_jsonl(run_dir: Path) -> Path | None:
    """Resolve the JSONL collector log file."""
    collector_dir = run_dir / "collector"
    for candidate in (
        collector_dir / "collector.log",
        run_dir / "collector.log",
    ):
        if candidate.exists():
            return candidate
    return None


def _write_readme(dataset_dir: Path, version: str) -> None:
    readme = f"""# MtAgentLens Benchmark v{version} — Frozen Dataset

## Quick Start

```python
import sqlite3
from pathlib import Path

dataset = Path(".")
for scenario_dir in sorted((dataset / "traces").iterdir()):
    db_path = scenario_dir / "responsibility.db"
    if not db_path.exists():
        db_path = scenario_dir / "collector.responsibility.db"
    db = sqlite3.connect(str(db_path))
    alerts = db.execute("SELECT COUNT(*) FROM responsibility_alerts").fetchone()[0]
    print(f"{{scenario_dir.name}}: {{alerts}} alerts")
```

## Contents

- `scenarios/` — Scenario definitions (JSON)
- `traces/` — Per-scenario eBPF traces (SQLite + JSONL)
- `ground_truth/` — Expected alerts, identity, causal chains
- `splits/` — train/val/test scenario ID lists
- `metadata.json` — Dataset metadata

## License

MIT
"""
    (dataset_dir / "README.md").write_text(readme, encoding="utf-8")
