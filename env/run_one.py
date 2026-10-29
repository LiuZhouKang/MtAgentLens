#!/usr/bin/env python3
"""Run a single scenario with collector and audit the run artifacts."""
import json, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'env' / 'src'))

from mtagentlens_benchmark.workspace import BenchmarkWorkspace
from mtagentlens_benchmark.engine.executor import execute_scenario
from mtagentlens_benchmark.engine.collector_manager import CollectorManager
from mtagentlens_benchmark.types import ScenarioConfig, ScenarioMode, AgentSpec

RUNS_DIR = ROOT / 'env' / 'runs'
AUDIT_SCRIPT = ROOT / 'env' / 'scripts' / 'audit_trace.py'

def _audit_run(run_dir: Path, scenario_id: str) -> bool:
    """Return True only when the canonical trace audit admits the run."""
    audit = subprocess.run([sys.executable, str(AUDIT_SCRIPT), str(run_dir), scenario_id])
    return audit.returncode == 0

def run_one(scenario_id: str):
    """Run a single scenario and audit the candidate artifacts in its run directory."""
    ws = BenchmarkWorkspace.discover()

    # Load scenario JSON
    scenario_path = ws.scenario_path(scenario_id)
    if not scenario_path.exists():
        print(f"ERROR: scenario not found: {scenario_path}")
        return False

    config_data = json.loads(scenario_path.read_text())
    mode_str = config_data.get('mode', 'single_agent')
    category = config_data.get('category', '')

    # Map mode to ScenarioMode
    mode_map = {'single_agent': ScenarioMode.SINGLE_AGENT, 'agent_teams': ScenarioMode.AGENT_TEAMS, 'subagents': ScenarioMode.SUBAGENTS}
    scenario_mode = mode_map.get(mode_str, ScenarioMode.SINGLE_AGENT)
    # Use process mode for single-agent scenarios OR multi-agent scenarios
    # with only 1 JSON agent (the single agent spawns sub-agents dynamically).
    # Tmux-live is only needed when multiple agents are defined in JSON
    # and each needs its own pane.
    agent_count = len(config_data.get('agents', []))
    if scenario_mode == ScenarioMode.SINGLE_AGENT or agent_count <= 1:
        exec_mode = 'process'
    else:
        exec_mode = 'tmux-live'

    # Build AgentSpec list
    agents = []
    for a in config_data.get('agents', []):
        task = a.get('task', '')
        # Replace {{FIXTURE_DIR}} placeholder
        fixture_name = config_data.get('fixture_dir', scenario_id)
        task = task.replace('{{FIXTURE_DIR}}', str(ws.fixtures_dir / fixture_name))
        agents.append(AgentSpec(role_file=a.get('role_file', ''), task=task))

    if not agents:
        print(f"ERROR: no agents defined for {scenario_id}")
        return False

    # Build ScenarioConfig
    config = ScenarioConfig(
        id=scenario_id,
        mode=scenario_mode,
        description=config_data.get('description', ''),
        agents=agents,
        fixtures_dir=ws.fixtures_dir / config_data.get('fixture_dir', scenario_id),
        permission_mode=config_data.get('permission_mode', 'default'),
        timeout_seconds=config_data.get('timeout', 300),
        tmux_layout=config_data.get('tmux', {}).get('layout', 'even-horizontal') if config_data.get('tmux') else 'even-horizontal',
        category=category,
    )

    print(f"{'='*60}")
    print(f"Scenario: {scenario_id}")
    print(f"Mode: {mode_str} → exec={exec_mode}")
    print(f"Category: {category}")
    print(f"Agents: {len(agents)}")
    print(f"Fixture: {config_data.get('fixture_dir', scenario_id)}")
    print(f"Timeout: {config.timeout_seconds}s")
    print(f"{'='*60}")

    # Create run directory
    import uuid as _uuid
    import datetime as dt
    ts = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    run_dir = RUNS_DIR / f'{ts}-{_uuid.uuid4().hex[:8]}'

    # Create CollectorManager
    cm = CollectorManager(ws, sudo=True)
    cm.set_run_context(scenario_id, mode_str)

    # Execute scenario
    t0 = time.time()
    result = execute_scenario(config, run_dir, ws, exec_mode, cm)
    elapsed = time.time() - t0

    print(f"\nResult: success={result.success} duration={elapsed:.1f}s")
    if result.error:
        print(f"Error: {result.error}")

    if not result.success:
        return False

    db_path = result.database_path
    if not (db_path and db_path.exists()):
        print("ERROR: No DB generated!")
        return False
    print(f"DB: {db_path} ({db_path.stat().st_size} bytes)")

    if not _audit_run(run_dir, scenario_id):
        print("ERROR: canonical trace audit rejected the run")
        return False

    return True

if __name__ == '__main__':
    sid = sys.argv[1] if len(sys.argv) > 1 else 'atlas-recon-discovery'
    ok = run_one(sid)
    sys.exit(0 if ok else 1)
