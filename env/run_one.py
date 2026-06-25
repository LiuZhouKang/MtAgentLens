#!/usr/bin/env python3
"""Run a single scenario with collector, verify quality, and save DB to frozen traces."""
import json, os, sys, shutil, sqlite3, time
from pathlib import Path

os.environ['MTAGENTPRISM_SUDO_PASSWORD'] = '1'

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'env' / 'src'))

from mtagentlens_benchmark.workspace import BenchmarkWorkspace
from mtagentlens_benchmark.engine.executor import execute_scenario
from mtagentlens_benchmark.engine.collector_manager import CollectorManager
from mtagentlens_benchmark.types import ScenarioConfig, ScenarioMode, AgentSpec, RunMode

TRACES_DIR = ROOT / 'compare' / 'Dataset' / 'FuxiBench' / 'traces'
RUNS_DIR = ROOT / 'env' / 'runs'

def run_one(scenario_id: str):
    """Run a single scenario and save the DB to frozen traces."""
    ws = BenchmarkWorkspace.discover()

    # Load scenario JSON
    scenario_path = ws.scenario_path(scenario_id)
    if not scenario_path.exists():
        print(f"ERROR: scenario not found: {scenario_path}")
        return False

    config_data = json.loads(scenario_path.read_text())
    mode_str = config_data.get('mode', 'single_agent')

    # Determine category for trace storage
    category = config_data.get('category', '')
    if not category:
        # Infer from prompt manifest
        manifest = json.loads((ROOT / 'compare/Dataset/FuxiBench/prompt_inputs/manifest.json').read_text())
        for e in manifest['entries']:
            if e['scenario_id'] == scenario_id:
                category = e.get('category', '')
                break

    # Map mode to ScenarioMode and RunMode
    mode_map = {'single_agent': ScenarioMode.SINGLE_AGENT, 'agent_teams': ScenarioMode.AGENT_TEAMS, 'subagents': ScenarioMode.SUBAGENTS}
    scenario_mode = mode_map.get(mode_str, ScenarioMode.SINGLE_AGENT)
    exec_mode = 'process' if scenario_mode == ScenarioMode.SINGLE_AGENT else 'tmux-live'

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

    # Execute scenario
    t0 = time.time()
    result = execute_scenario(config, run_dir, ws, exec_mode, cm)
    elapsed = time.time() - t0

    print(f"\nResult: success={result.success} duration={elapsed:.1f}s")
    if result.error:
        print(f"Error: {result.error}")

    # Check DB
    db_path = result.database_path
    if db_path and db_path.exists():
        db_size = db_path.stat().st_size
        print(f"DB: {db_path} ({db_size} bytes)")

        # Quick quality check
        db = sqlite3.connect(str(db_path))
        pbc = db.execute("SELECT COUNT(*) FROM graph_edges WHERE edge_type='PolicyBoundaryCrossing'").fetchone()[0]
        edges = db.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        alerts = db.execute("SELECT COUNT(*) FROM responsibility_alerts").fetchone()[0]
        evidence = db.execute("SELECT COUNT(*) FROM correlation_evidence").fetchone()[0]
        db.close()

        print(f"Quality: pbc={pbc} edges={edges} alerts={alerts} evidence={evidence}")

        # Save to frozen traces
        if category:
            dest_dir = TRACES_DIR / category / scenario_id
        else:
            dest_dir = TRACES_DIR / scenario_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Copy DB
        dest_db = dest_dir / 'responsibility.db'
        shutil.copy2(str(db_path), str(dest_db))
        print(f"Saved: {dest_db}")

        # Copy log
        err_log = run_dir / 'collector' / 'trace.err.log'
        if err_log.exists():
            shutil.copy2(str(err_log), str(dest_dir / 'trace.err.log'))

        return True
    else:
        print("ERROR: No DB generated!")
        return False

if __name__ == '__main__':
    sid = sys.argv[1] if len(sys.argv) > 1 else 'atlas-recon-discovery'
    ok = run_one(sid)
    sys.exit(0 if ok else 1)
