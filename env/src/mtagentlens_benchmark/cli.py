"""Unified CLI for MtAgentLens Benchmark."""
from __future__ import annotations

import click

from mtagentlens_benchmark import __version__


@click.group()
@click.version_option(__version__, prog_name="mtagentlens-benchmark")
def main() -> None:
    """MtAgentLens Benchmark — World-class OS-level Agent anomaly detection dataset."""


@main.command()
@click.option("--scenario", "-s", default=None, help="Scenario ID to run")
@click.option("--all", "run_all", is_flag=True, help="Run all scenarios")
@click.option("--mode", "-m", default="process",
              type=click.Choice(["batch", "tmux-live", "tmux-task", "process"]))
@click.option("--parallel", "-p", default=1, type=int, help="Parallel workers")
@click.option("--output", "-o", default="runs", type=click.Path(), help="Output directory")
@click.option("--collector/--no-collector", "with_collector", default=False,
              help="Start MtAgentLens eBPF collector (requires sudo)")
@click.option("--disable-responsibility-observer", is_flag=True,
              help="Disable responsibility-layer Observer LLM during collector runs")
def run(
    scenario: str | None,
    run_all: bool,
    mode: str,
    parallel: int,
    output: str,
    with_collector: bool,
    disable_responsibility_observer: bool,
) -> None:
    """Run scenarios — live or batch mode."""
    if not scenario and not run_all:
        raise click.UsageError("Must specify --scenario or --all")
    if scenario and run_all:
        raise click.UsageError("Cannot specify both --scenario and --all")

    from mtagentlens_benchmark.loader.scenario_loader import load_scenario, load_all_scenarios
    from mtagentlens_benchmark.engine.runner import run_scenarios
    from mtagentlens_benchmark.workspace import BenchmarkWorkspace

    ws = BenchmarkWorkspace.discover()

    if run_all:
        scenarios = load_all_scenarios(ws.scenarios_dir, ws.fixtures_dir)
    else:
        scenarios = [load_scenario(ws.scenario_path(scenario), ws.fixtures_dir)]

    # Safety check: warn about risky scenarios
    for s in scenarios:
        if s.risk_level and s.risk_level.value != "safe":
            click.secho(
                f"  [{s.risk_level.value.upper()}] {s.id}: {s.description[:80]}",
                fg="yellow" if s.risk_level.value == "modify_system" else None,
            )

    # Optional collector (MtAgentLens eBPF reference implementation)
    collector_factory = None
    if with_collector:
        from mtagentlens_benchmark.engine.collector_manager import CollectorManager
        def _make_collector():
            return CollectorManager(
                ws,
                disable_responsibility_observer=disable_responsibility_observer,
            )
        collector_factory = _make_collector

    results = run_scenarios(scenarios, ws, parallel, collector_factory, execution_mode=mode)

    for r in results:
        icon = "OK" if r.success else "FAIL"
        db_info = f" db={r.database_path.name}" if r.database_path else ""
        click.echo(f"  {r.scenario_id}: {icon} ({r.duration_seconds:.0f}s){db_info}")


@main.command()
@click.option("--dataset", "-d", required=True, type=click.Path(exists=True),
              help="Path to frozen dataset directory")
@click.option("--output", "-o", default="report", type=click.Path(), help="Output directory")
@click.option("--format", "-f", "fmt", default="markdown",
              type=click.Choice(["markdown", "html", "latex", "json"]))
def evaluate(dataset: str, output: str, fmt: str) -> None:
    """Evaluate detection results against ground truth."""
    from pathlib import Path
    from glob import glob
    from mtagentlens_benchmark.evaluator.metrics import compute_benchmark_score
    from mtagentlens_benchmark.evaluator.reporter import generate_report
    from mtagentlens_benchmark.annotator.ground_truth import load_ground_truth

    dataset_path = Path(dataset)
    traces_dir = dataset_path / "traces"
    gt_root = dataset_path / "ground_truth"

    if not traces_dir.exists():
        click.echo(f"Error: traces directory not found: {traces_dir}")
        return
    if not gt_root.exists():
        click.echo(f"Error: ground truth directory not found: {gt_root}")
        return

    # Build GT index: scenario_id → GT path
    gt_index: dict[str, Path] = {}
    for cat_dir in sorted(gt_root.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name.startswith("_"):
            continue
        for gt_file in sorted(cat_dir.glob("*.json")):
            sid = gt_file.stem
            gt_index[sid] = gt_file

    # Match traces to GT by scenario ID (from .scenario_id file)
    results = []
    for cat_dir in sorted(traces_dir.iterdir()):
        if not cat_dir.is_dir():
            continue
        for run_dir in sorted(cat_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            sid_file = run_dir / ".scenario_id"
            if not sid_file.exists():
                continue
            scenario_id = sid_file.read_text().strip()
            db_path = next(run_dir.glob("*.db"), None)
            if db_path and scenario_id in gt_index:
                gt = load_ground_truth(gt_index[scenario_id])
                results.append((db_path, gt))
                click.echo(f"  Loaded: {scenario_id} (alerts: {len(gt.alerts)})")

    if not results:
        click.echo("No valid result pairs found. Run scenarios with --collector first.")
        return

    score = compute_benchmark_score(results, [])
    report_path = generate_report(score, Path(output), fmt)
    click.echo(f"  Overall Score: {score.overall:.2f}/100")
    click.echo(f"  Report saved: {report_path}")


@main.command()
@click.option("--scenario", "-s", default=None, help="Scenario ID to annotate")
@click.option("--all", "annotate_all", is_flag=True, help="Annotate all scenarios")
@click.option("--annotators", "-a", default="lzk1,lzk2,lzk3",
              help="Comma-separated annotator IDs")
@click.option("--output", "-o", default="ground_truth", type=click.Path())
def annotate(scenario: str | None, annotate_all: bool, annotators: str, output: str) -> None:
    """Run multi-annotator labeling for scenarios."""
    annotator_list = annotators.split(",")
    click.echo(f"Annotating scenario={scenario} all={annotate_all} annotators={annotator_list}")
    # Delegates to annotator — implemented in a later phase


@main.command()
@click.option("--version", "-v", required=True, help="Release version (e.g. 1.0.0)")
@click.option("--output", "-o", default="releases", type=click.Path(), help="Output directory")
@click.option("--scenarios-dir", "-s", default="scenarios", type=click.Path(exists=True))
def package(version: str, output: str, scenarios_dir: str) -> None:
    """Package frozen dataset for release."""
    click.echo(f"Packaging version={version} output={output}")
    # Delegates to packager — implemented in a later phase


@main.command()
@click.option("--category", "-c", required=True, help="Attack category")
@click.option("--name", "-n", required=True, help="Scenario ID")
@click.option("--mode", "-m", default="single_agent",
              type=click.Choice(["single_agent", "agent_teams", "subagents"]))
@click.option("--agents", "-a", default=1, type=int, help="Number of agents")
@click.option("--classical-chinese/--no-classical-chinese", default=False,
              help="Use classical Chinese bypass")
def scaffold(category: str, name: str, mode: str, agents: int, classical_chinese: bool) -> None:
    """Generate a scenario template."""
    from pathlib import Path
    from mtagentlens_benchmark.scaffold import scaffold_scenario

    scenarios_dir = Path("scenarios")
    path = scaffold_scenario(scenarios_dir, name, category, mode, agents, classical_chinese)
    click.echo(f"  Created: {path}")
    click.echo(f"  Fixtures: {scenarios_dir / 'fixtures' / name}/")


@main.command()
@click.option("--scenarios-dir", "-s", default="scenarios", type=click.Path(exists=True))
def list_scenarios(scenarios_dir: str) -> None:
    """List all scenarios grouped by mode."""
    from pathlib import Path
    from collections import defaultdict
    import json
    sdir = Path(scenarios_dir)
    grouped: dict[str, list[str]] = defaultdict(list)
    for cat_dir in sorted(sdir.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name in ("fixtures", "archive", "_archive"):
            continue
        # Flat files
        for p in sorted(cat_dir.glob("*.json")):
            data = json.loads(p.read_text(encoding="utf-8"))
            grouped[data.get("mode", "unknown")].append(data["id"])
        # Nested subdirectories (e.g. multi_agent_attack/agent_teams/)
        for sub_dir in sorted(cat_dir.iterdir()):
            if sub_dir.is_dir() and not sub_dir.name.startswith("_"):
                for p in sorted(sub_dir.glob("*.json")):
                    data = json.loads(p.read_text(encoding="utf-8"))
                    key = f"{data.get('mode', 'unknown')}/{data.get('multi_agent_pattern', '')}"
                    grouped[key].append(data["id"])
    # Fallback: flat structure
    if not grouped:
        for p in sorted(sdir.glob("*.json")):
            data = json.loads(p.read_text(encoding="utf-8"))
            grouped[data.get("mode", "unknown")].append(data["id"])
    for m, ids in grouped.items():
        click.secho(f"[{m}]", bold=True)
        for sid in ids:
            click.echo(f"  {sid}")


if __name__ == "__main__":
    main()
