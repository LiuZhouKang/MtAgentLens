# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Final experiments: validate real inputs without spending; --execute opts in.

multi-agent runs the current scored multi-agent cohort (type-c is a legacy CLI
alias); campaign runs every included single- and multi-agent scenario exactly
once. e8 validates and exports one recording per judge invocation. e9 is only a
synthetic parser microbenchmark, never the full-system overhead experiment.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from compare.tools.detection_scope import (DEFAULT_DATASET_DIR, is_detection_excluded,
                                          load_ground_truth, validate_current_attack_type)

WORK = ROOT / 'local/workpackages/2026-10-26-final-dataset'
DEFAULT_RELEASE = DEFAULT_DATASET_DIR
DEFAULT_OUT = WORK / 'experiments'
ENV_PYTHON = ROOT / 'env/.venv/bin/python'
FROZEN_SCENARIO_COUNT = 93


def _python() -> str:
    return str(ENV_PYTHON) if ENV_PYTHON.exists() else sys.executable


def _env() -> dict:
    return dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, (ROOT, ROOT / 'env', ROOT / 'env/src'))))


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _release_gt(release: Path) -> dict[str, dict]:
    return load_ground_truth(release / 'ground_truth')


def _validated_release(release: Path) -> dict:
    from compare.tools.dataset_release import validate_release
    return validate_release(release)


COHORTS = ('multi-agent', 'campaign')


def campaign_cases(release: Path, cohort: str = 'multi-agent') -> list[dict]:
    """Load each release scenario once, optionally restricted to multi-agent."""
    if cohort not in COHORTS:
        raise ValueError(f'unknown cohort {cohort!r}; use one of {COHORTS}')
    gt = _release_gt(release)
    cases, seen = [], set()
    for path in sorted((release / 'scenarios').glob('*.json')):
        scenario = _read_json(path)
        sid = scenario['id']
        if sid in seen:
            raise ValueError(f'duplicate scenario input: {sid}')
        seen.add(sid)
        if sid not in gt:
            raise ValueError(f'ground truth missing for scenario input: {sid}')
        agents = len(scenario.get('agents') or [])
        if cohort == 'multi-agent' and agents < 2:
            continue
        row = gt[sid]
        validate_current_attack_type(row.get('attack_type'))
        cases.append({'scenario_id': sid, 'label': row['label'], 'attack_type': row['attack_type'],
                      'agents': agents, 'detection_excluded': is_detection_excluded(row)})
    if cohort == 'campaign':
        missing = sorted(set(gt) - seen)
        if missing:
            raise ValueError(f'ground truth scenario without input: {missing}')
    return cases


def multi_agent_cases(release: Path) -> list[dict]:
    return campaign_cases(release, 'multi-agent')


def campaign_plan(release: Path, out: Path, cohort: str = 'multi-agent') -> dict:
    if cohort not in COHORTS:
        raise ValueError(f'unknown cohort {cohort!r}; use one of {COHORTS}')
    problems = []
    manifest, cases = {}, []
    try:
        manifest = _validated_release(release)
        gt = _release_gt(release)
        cases = campaign_cases(release, cohort)
        if len(gt) != FROZEN_SCENARIO_COUNT:
            problems.append('expected the frozen 93-scenario corpus')
        # Exercise the actual scenario loader used by pilot_observer, without executing it.
        from mtagentlens_benchmark.loader.scenario_loader import load_all_scenarios
        configs = load_all_scenarios(release / 'scenarios', release / 'fixtures')
        if len(configs) != len(gt):
            problems.append(f'scenario loader resolved {len(configs)} of {len(gt)} inputs')
        for config in configs:
            if config.pre_run_script and not (release / config.pre_run_script).is_file():
                problems.append(f'{config.id}: setup script missing from release')
    except (OSError, ValueError, KeyError, ImportError) as exc:
        problems.append(str(exc))
    included = [c for c in cases if not c['detection_excluded']]
    excluded_ids = [c['scenario_id'] for c in cases if c['detection_excluded']]
    plan = {'release': str(release), 'release_id': manifest.get('release_id'), 'out': str(out),
            'cohort': cohort, 'input_count': len(cases), 'target': len(included),
            'target_count': len(included), 'total': len(included),
            'attack': sum(c['label'] == 'attack' for c in included),
            'benign': sum(c['label'] == 'benign' for c in included),
            'attack_types': {t: sum(c['attack_type'] == t for c in included) for t in ('A', 'B')},
            'detection_excluded': excluded_ids, 'excluded_ids': excluded_ids,
            'scenarios': included, 'problems': problems}
    if cohort == 'multi-agent':
        plan['multi_agent_before_exclusions'] = len(cases)
    return plan


# Fixed before collection; E0 artifacts live in campaign/runs and are reused.
E0_STRATA = (
    ('exfil-env-secrets', 'attack', 'A', False),
    ('consensus-split', 'attack', 'B', True),
    ('benign-api-doc', 'benign', 'benign', False),
    ('benign-multi-handoff', 'benign', 'benign', True),
)


def e0_plan(plan: dict) -> dict:
    selected = {**plan, 'problems': list(plan['problems']),
                'campaign_target_count': plan['target_count'],
                'selection': 'E0-four-registered-strata/1',
                'planned_paid_calls': None, 'execution_started': False}
    by_id = {row['scenario_id']: row for row in plan['scenarios']}
    cases = []
    for sid, label, attack_type, multi in E0_STRATA:
        row = by_id.get(sid)
        if row is None or (row['label'], row['attack_type'], row['agents'] > 1) != (label, attack_type, multi):
            selected['problems'].append(f'E0 registered stratum missing or changed: {sid}')
        else:
            cases.append(row)
    selected.update(scenarios=cases, target=len(cases), target_count=len(cases), total=len(cases),
                    attack=sum(row['label'] == 'attack' for row in cases),
                    benign=sum(row['label'] == 'benign' for row in cases),
                    attack_types={t: sum(row['attack_type'] == t for row in cases) for t in ('A', 'B')})
    return selected


def _require_execute(args) -> None:
    if not args.execute or os.environ.get('MTL_ALLOW_PAID') != '1':
        raise SystemExit('paid execution requires --execute and MTL_ALLOW_PAID=1')


def _write_plan(out: Path, name: str, plan: dict) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / f'{name}-plan.json'
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


def cmd_status(args) -> int:
    release, out = args.release.resolve(), args.out.resolve()
    plan = campaign_plan(release, out)
    plan['multi_agent'] = plan.pop('total')
    for key in ('cohort', 'input_count', 'target', 'target_count', 'excluded_ids'):
        plan.pop(key)
    plan.pop('scenarios')
    plan['harnesses'] = {
        'multi-agent': 'input checks passed; live capture and model preflight still required' if not plan['problems'] else 'blocked by input validation',
        'e8': 'requires completed, release-bound, healthy run recordings; use e8 dry-run',
        'e9': 'synthetic microbenchmark only; full-system concurrency/loss/latency not implemented here',
        'e9-system': 'requires root and built collector; process/BPF/runtime/persistence load, excludes TLS and model calls',
        'runtime-annotations': 'requires explicit campaign ledgers; use runtime-annotations dry-run',
    }
    plan['reference_readiness'] = {
        'detection_capture': 'inputs validated; live collector health and model preflight still required' if not plan['problems'] else 'blocked by inputs',
        'runtime_execution_annotations': 'three-reviewer execution-outcome pipeline; requires validated native run evidence',
        'logical_root_and_identity': 'blocked: protocol and collector-independent native session anchoring required; current runtime reviews enforce root_actor=null and provide conditional physical association only',
    }
    campaign = campaign_plan(release, out / 'campaign', cohort='campaign')
    plan['full_campaign'] = {key: campaign[key] for key in (
        'cohort', 'input_count', 'target', 'target_count', 'attack', 'benign',
        'attack_types', 'excluded_ids', 'problems')}
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return int(bool(plan['problems'] or campaign['problems']))


def completed_case(target: Path, release: Path, sid: str, collector_hash: str) -> bool:
    """Resume only an intact completed run from the same release and collector."""
    from compare.scripts.run.app_baseline import load_rows, validate_real_run
    from scripts.evaluation.run_artifacts import validate_runtime_inputs
    from compare.tools.dataset_release import sha256
    try:
        rows = load_rows(target / 'results.jsonl')
        row = rows[-1]
        if row.get('scenario_id') != sid or row.get('collector_sha256') != collector_hash:
            return False
        if sha256(Path(row['collector_binary'])) != collector_hash:
            return False
        validate_runtime_inputs(Path(row['runtime_inputs']), row['configuration_sha256'])
        validate_real_run(row, release)
        return True
    except (OSError, ValueError, KeyError, IndexError, sqlite3.Error):
        return False


def _execute_campaign(args, cohort: str, *, pilot: bool = False) -> int:
    """Shared guarded runner: one pilot command per planned case, resumable."""
    if args.execute:
        _require_execute(args)
    release, out = args.release.resolve(), args.out.resolve() / cohort
    plan = campaign_plan(release, out, cohort=cohort)
    if pilot:
        plan = e0_plan(plan)
    plan_name = 'e0' if pilot else cohort
    runner = ROOT / 'compare/scripts/run/pilot_observer.py'
    if not plan['problems']:
        help_result = subprocess.run([_python(), str(runner), '--help'], env=_env(), capture_output=True, text=True)
        if help_result.returncode:
            plan['problems'].append(help_result.stderr)
    _write_plan(out, plan_name, plan)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if plan['problems'] or not args.execute:
        return int(bool(plan['problems']))
    from compare.scripts.run.pilot_observer import BINARY, binary_sha256
    collector_hash = binary_sha256(BINARY)
    if collector_hash is None:
        raise ValueError('collector binary missing; build before paid execution')
    failures = []
    for case in plan['scenarios']:
        sid = case['scenario_id']
        target = out / 'runs' / sid
        if not args.force and completed_case(target, release, sid, collector_hash):
            print(f'skip verified completed run: {sid}')
            continue
        command = [_python(), str(runner), '--release', str(release), '--observer', 'on',
                   '--out', str(target), '--only', sid, '--timeout', str(args.timeout)]
        result = subprocess.run(command, cwd=ROOT, env=_env())
        if result.returncode or not completed_case(target, release, sid, collector_hash):
            failures.append({'scenario_id': sid, 'returncode': result.returncode})
            # A systemic capture/model failure must not spend through the remaining cohort.
            break
    _write_plan(out, f'{plan_name}-summary', {'plan': plan, 'failures': failures})
    return int(bool(failures))


def cmd_type_c(args) -> int:
    return _execute_campaign(args, 'multi-agent')


def cmd_campaign(args) -> int:
    return _execute_campaign(args, 'campaign')


def cmd_e0(args) -> int:
    return _execute_campaign(args, 'campaign', pilot=True)


def e8_plan(ledger: Path, release: Path, out: Path, limit: int | None) -> dict:
    from compare.scripts.run import app_baseline as baseline
    manifest = _validated_release(release)
    gt = _release_gt(release)
    plan = {'release_id': manifest['release_id'], 'results_ledger': str(ledger),
            'commands': [], 'problems': [], 'detection_excluded': []}
    if limit is not None and limit < 1:
        raise ValueError('--limit must be positive')
    rows = baseline.load_rows(ledger)
    counts = {}
    for row in rows:
        sid = row.get('scenario_id')
        counts[sid] = counts.get(sid, 0) + 1
    duplicates = {sid for sid, count in counts.items() if count > 1}
    plan['problems'].extend(f'duplicate scenario recordings: {sid}'
                            for sid in sorted(duplicates, key=str))
    selected = []
    for row in rows:
        sid = row.get('scenario_id')
        if sid in duplicates:
            continue
        if sid not in gt:
            plan['problems'].append(f'unknown scenario in ledger: {sid}')
        elif is_detection_excluded(gt[sid]):
            plan['detection_excluded'].append(sid)
        else:
            selected.append(row)
    if limit is not None:
        selected = selected[:limit]
    if not selected:
        plan['problems'].append('no scored recordings selected')
    arms = (baseline.ARM_PROMPT_ONLY, baseline.ARM_FULL_TRACE, baseline.ARM_NO_TOOL_RESULT)
    failures = []
    arm_counts = {arm: 0 for arm in arms}
    plan['failures'] = failures
    for index, row in enumerate(selected):
        sid = str(row.get('scenario_id'))
        target = out / f'run-{index:04d}'
        target.mkdir(parents=True, exist_ok=True)
        # One original recording input is shared by all three arms.
        single = target / 'input.jsonl'
        single.write_text(json.dumps(row) + '\n', encoding='utf-8')
        try:
            baseline.validate_real_run(row, release)
        except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
            failures.append({'scenario_id': sid, 'run_index': index, 'arm': None,
                             'stage': 'validation', 'error': str(exc)})
            plan['problems'].append(f'{sid}: validation failed: {exc}')
            continue
        for arm in arms:
            arm_out = target / arm
            try:
                # Actual CLI parsing/exporter per arm; unavailable client cannot spend.
                dry_args = ['--results', str(single), '--out', str(arm_out / 'dry-run'),
                            '--release', str(release), '--client', 'unavailable',
                            '--arm', arm, '--limit', '1']
                with contextlib.redirect_stdout(io.StringIO()):
                    code = baseline.main(dry_args)
                summary = _read_json(arm_out / 'dry-run/summary.json')
                if code != 2 or summary.get('records') != 1 or summary.get('failures'):
                    raise ValueError(f'application trace export failed: {summary.get("failures")}')
            except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
                failures.append({'scenario_id': sid, 'run_index': index, 'arm': arm,
                                 'stage': 'dry_run', 'error': str(exc)})
                plan['problems'].append(f'{sid} [{arm}]: {exc}')
                continue
            arm_counts[arm] += 1
            plan['commands'].append([
                _python(), str(ROOT / 'compare/scripts/run/app_baseline.py'),
                '--results', str(single), '--out', str(arm_out / 'results'),
                '--release', str(release), '--client', 'anthropic',
                '--execution-mode', 'real', '--arm', arm, '--limit', '1'])
    plan['budget'] = {
        'selected_runs': len(selected), 'arms_per_run': len(arms),
        'arm_counts': arm_counts, 'record_count': len(plan['commands']),
        'commands': len(plan['commands']), 'model_calls_per_command': 2,
        'max_model_calls': 2 * len(plan['commands']),
        'basis': 'one two-step judge per command: same reason -> label; not two independent judges',
    }
    return plan


def cmd_e8(args) -> int:
    if args.execute:
        _require_execute(args)
    out = args.out.resolve() / 'e8'
    plan = e8_plan(args.results.resolve(), args.release.resolve(), out, args.limit)
    _write_plan(out, 'e8', plan)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if plan['problems'] or not args.execute:
        return int(bool(plan['problems']))
    for command in plan['commands']:
        result = subprocess.run(command, cwd=ROOT, env=_env())
        if result.returncode:
            return result.returncode
    return 0


def e9_plan(events: int) -> dict:
    if events < 1:
        raise ValueError('--events must be positive')
    binary = ROOT / 'collector/build/release/perf_throughput'
    return {'scope': 'synthetic_parser_microbenchmark', 'full_system_e9_available': False,
            'levels': [1, 4, 8, 16, 32], 'events_per_process': events,
            'perf_binary': str(binary), 'binary_exists': binary.is_file(),
            'limitation': 'FakeRunner + HTTPParser + OutputAnalyzer; excludes eBPF, responsibility runtime, persistence, Observer, capture loss and end-to-end latency',
            'system_load_tool': 'scripts/evaluation/system_load.py (process/BPF/runtime/persistence; excludes TLS/Observer)'}


def summarize_synthetic(stats: list[dict], codes: list[int], elapsed: float, peak_rss: int) -> dict:
    observed = sum(s['total_events'] + s['raw_events'] for s in stats)
    return {'wall_seconds': elapsed, 'observed_events': observed,
            'aggregate_events_per_second': observed / elapsed if elapsed > 0 else None,
            'sampled_peak_rss_kib_sum': peak_rss, 'per_process': stats, 'returncodes': codes,
            'ok': len(stats) == len(codes) and all(code == 0 for code in codes)}


def cmd_e9(args) -> int:
    out = args.out.resolve() / 'e9'
    plan = e9_plan(args.events)
    _write_plan(out, 'e9', plan)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.execute:
        return 0
    binary = Path(plan['perf_binary'])
    if not binary.is_file():
        build = subprocess.run(['cargo', 'build', '--release', '--bin', 'perf_throughput'], cwd=ROOT / 'collector')
        if build.returncode:
            return build.returncode
    records = []
    for level in plan['levels']:
        # Files avoid pipe backpressure while sampling RSS; retain real binary reports.
        with tempfile.TemporaryDirectory(prefix='mtl-e9-') as temp, contextlib.ExitStack() as stack:
            reports = [stack.enter_context(open(Path(temp) / f'{i}.json', 'w+')) for i in range(level)]
            started = time.monotonic()
            procs = [subprocess.Popen([str(binary), str(args.events)], stdout=report, stderr=subprocess.DEVNULL)
                     for report in reports]
            peak_rss = 0
            while any(proc.poll() is None for proc in procs):
                rss_sum = 0
                for proc in procs:
                    try:
                        for line in Path(f'/proc/{proc.pid}/status').read_text().splitlines():
                            if line.startswith('VmRSS:'):
                                rss_sum += int(line.split()[1])
                    except OSError:
                        pass
                peak_rss = max(peak_rss, rss_sum)
                time.sleep(0.01)
            codes = [proc.wait() for proc in procs]
            elapsed = time.monotonic() - started
            stats, errors = [], []
            for report in reports:
                report.seek(0)
                try:
                    stats.append(json.load(report))
                except ValueError as exc:
                    errors.append(str(exc))
            result = summarize_synthetic(stats, codes, elapsed, peak_rss)
            result.update(concurrency=level, errors=errors)
            records.append(result)
    _write_plan(out, 'results', {'schema': 'mtagentlens.synthetic-parser-concurrency/2',
                                'config': plan, 'records': records})
    return int(not all(row['ok'] for row in records))


def runtime_commands(runtime: Path, run_root: Path) -> list[list[str]]:
    script = str(ROOT / 'tools/annotator/ai_runtime.py')
    commands = [[_python(), script, stage, '--root', str(runtime), '--reviewers', '3']
                for stage in ('prepare', 'compact', 'sanitize', 'review', 'finalize', 'adjudicate', 'finalize', 'verify')]
    commands[0].extend(['--run-root', str(run_root)])
    commands[2].extend(['--source-root', str(runtime)])
    return commands


def cmd_e9_system(args) -> int:
    """Delegate the zero-model process-capture load to the measured system tool."""
    command = [_python(), str(ROOT / 'scripts/evaluation/system_load.py'),
               '--binary', str(args.binary.resolve()), '--out', str(args.out.resolve() / 'e9-system'),
               '--concurrency', args.concurrency, '--operations', str(args.operations),
               '--interval-ms', str(args.interval_ms)]
    if args.execute:
        command.append('--execute')
    return subprocess.run(command, cwd=ROOT, env=_env()).returncode


def cmd_runtime_annotations(args) -> int:
    if args.execute:
        _require_execute(args)
    out = args.out.resolve() / 'runtime-annotations'
    runtime = out / 'runtime'
    commands = runtime_commands(runtime, args.run_root.resolve())
    # All preparation stages are local; dry-run really checks evidence and blinding.
    for command in commands[:3]:
        result = subprocess.run(command, cwd=ROOT, env=_env())
        if result.returncode:
            return result.returncode
    inventory = _read_json(runtime / 'inventory.json')
    manifest = _validated_release(args.release.resolve())
    problems = []
    if not inventory['cases'] or any(row.get('status') != 'included' for row in inventory['records']):
        problems.append('empty, duplicate or invalid runner evidence; inspect inventory')
    if any(case['binding']['data_release_id'] != manifest['release_id'] for case in inventory['cases']):
        problems.append('runtime ledgers belong to a different release')
    plan = {'run_root': str(args.run_root.resolve()), 'candidates': len(inventory['cases']),
            'commands': commands, 'problems': problems}
    _write_plan(out, 'runtime-annotations', plan)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if problems or not args.execute:
        return int(bool(problems))
    for command in commands[3:]:
        result = subprocess.run(command, cwd=ROOT, env=_env())
        if result.returncode:
            return result.returncode
    verification = _read_json(runtime / 'verification.json')
    return int(not (verification['valid'] and verification['all_candidates_reviewed']
                    and verification['reviewer_count'] == 3))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, default=DEFAULT_RELEASE)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--force', action='store_true')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    p = sub.add_parser('multi-agent', aliases=['type-c']); p.add_argument('--timeout', type=int, default=900)
    p = sub.add_parser('campaign'); p.add_argument('--timeout', type=int, default=900)
    p = sub.add_parser('e0'); p.add_argument('--timeout', type=int, default=900)
    p = sub.add_parser('e8'); p.add_argument('--results', type=Path, required=True); p.add_argument('--limit', type=int)
    p = sub.add_parser('e9'); p.add_argument('--events', type=int, default=100_000)
    p = sub.add_parser('e9-system')
    p.add_argument('--binary', type=Path, default=ROOT / 'collector/build/release/mtagentlens')
    p.add_argument('--concurrency', default='1,4,8,16,32')
    p.add_argument('--operations', type=int, default=20)
    p.add_argument('--interval-ms', type=int, default=100)
    p = sub.add_parser('runtime-annotations'); p.add_argument('--run-root', type=Path, required=True)
    args = parser.parse_args()
    for path in (ROOT / 'env', ROOT / 'env/src'):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    handlers = {'status': cmd_status, 'multi-agent': cmd_type_c, 'type-c': cmd_type_c,
                'campaign': cmd_campaign, 'e0': cmd_e0,
                'e8': cmd_e8, 'e9': cmd_e9, 'e9-system': cmd_e9_system,
                'runtime-annotations': cmd_runtime_annotations}
    try:
        if args.command != 'status' and args.out.resolve().is_relative_to(args.release.resolve()):
            raise ValueError('output must be outside the immutable dataset release')
        return handlers[args.command](args)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        print(f'blocked: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
