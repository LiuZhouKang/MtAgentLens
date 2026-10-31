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
import hashlib
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
    # Phase-2 finalization of a large multi-agent trace can exceed the 30 s
    # historical grace; a SIGKILL there loses the canonical verdict entirely.
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(map(str, (ROOT, ROOT / 'env', ROOT / 'env/src'))))
    env.setdefault("MTAGENTLENS_COLLECTOR_STOP_TIMEOUT", "180")
    return env


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _release_gt(release: Path) -> dict[str, dict]:
    return load_ground_truth(release / 'ground_truth')


def _validated_release(release: Path) -> dict:
    """Read ordinary inputs or validate an explicitly supplied historical release."""
    release = Path(release)
    manifest = release / 'release.json'
    if manifest.exists() or manifest.is_symlink():
        from compare.tools.dataset_release import validate_release
        return validate_release(release)
    if not (release / 'scenarios').is_dir() or not (release / 'ground_truth').is_dir():
        raise ValueError('dataset must contain scenarios and ground_truth directories')
    records = _release_gt(release)
    if not records:
        raise ValueError('dataset ground_truth is empty')
    return {'release_id': None, 'source_status': 'unbound',
            'case_labels': {sid: row.get('label') for sid, row in records.items()}}


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
        'logical_root_and_identity': 'conditional native protocol implemented (detector-blind packet, fresh 3-reviewer + adjudicator, source-DB-bound exports); production blocked by an unfrozen per-run unit/target cohort (attribution currently uses every action) and paid review budget',
    }
    campaign = campaign_plan(release, out / 'campaign', cohort='campaign')
    plan['full_campaign'] = {key: campaign[key] for key in (
        'cohort', 'input_count', 'target', 'target_count', 'attack', 'benign',
        'attack_types', 'excluded_ids', 'problems')}
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return int(bool(plan['problems'] or campaign['problems']))


def completed_case(target: Path, release: Path, sid: str, collector_hash: str,
                   *, replay_binary: Path | None = None,
                   timeout: int = 900) -> bool:
    """Resume only an intact completed run from the same release and collector.

    When a replay binary is supplied, the frozen recording must also pass the
    current schema/implementation strict validator. A binding check alone would
    silently reuse a checkpoint written by an older analysis kernel.
    """
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
        if replay_binary is not None:
            completed = subprocess.run(
                [str(replay_binary), '--sqlite', str(row['db_path']),
                 '--validate-recording'],
                capture_output=True, text=True, timeout=timeout, cwd=ROOT,
            )
            if completed.returncode != 0 or not completed.stdout.strip():
                return False
            report = json.loads(completed.stdout)
            if (not isinstance(report, dict)
                    or report.get('schema') != 'mtagentlens.recording-validation/v1'
                    or report.get('status') != 'ok'
                    or report.get('read_only') is not True
                    or bool(report.get('failures'))):
                return False
        return True
    except (OSError, ValueError, KeyError, IndexError, sqlite3.Error,
            subprocess.SubprocessError):
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
    replay_binary = ROOT / 'collector/build/release/replay'
    for case in plan['scenarios']:
        sid = case['scenario_id']
        target = out / 'runs' / sid
        if not args.force and completed_case(
                target, release, sid, collector_hash,
                replay_binary=replay_binary if replay_binary.is_file() else None,
                timeout=args.timeout):
            print(f'skip verified completed run: {sid}')
            continue
        command = [_python(), str(runner), '--release', str(release), '--observer', 'on',
                   '--out', str(target), '--only', sid, '--timeout', str(args.timeout)]
        result = subprocess.run(command, cwd=ROOT, env=_env())
        if result.returncode or not completed_case(
                target, release, sid, collector_hash,
                replay_binary=replay_binary if replay_binary.is_file() else None,
                timeout=args.timeout):
            failures.append({'scenario_id': sid, 'returncode': result.returncode})
            # A systemic capture/model failure must not spend through the remaining cohort.
            break
    _write_plan(out, f'{plan_name}-summary', {'plan': plan, 'failures': failures})
    return int(bool(failures))


def cmd_run_one(args) -> int:
    """Run exactly one scenario, with admission, resumable and budget-visible."""
    if not args.execute:
        print(json.dumps({
            'scenario_id': args.scenario,
            'status': 'blocked',
            'reason': 'run-one requires --execute and MTL_ALLOW_PAID=1',
        }, ensure_ascii=False, indent=2))
        return 1
    _require_execute(args)
    release = args.release.resolve()
    out = args.out.resolve() / 'campaign'
    if out.is_relative_to(release):
        raise ValueError('output must be outside the immutable dataset release')
    from compare.scripts.run.pilot_observer import BINARY, binary_sha256
    collector_hash = binary_sha256(BINARY)
    if collector_hash is None:
        raise ValueError('collector binary missing; build before paid execution')
    target = out / 'runs' / args.scenario
    replay_binary = ROOT / 'collector/build/release/replay'
    if not args.force and completed_case(
        target, release, args.scenario, collector_hash,
        replay_binary=replay_binary if replay_binary.is_file() else None,
        timeout=args.timeout,
    ):
        print(json.dumps({
            'scenario_id': args.scenario, 'status': 'completed', 'skipped': True,
        }, ensure_ascii=False, indent=2))
        return 0
    command = [
        _python(), str(ROOT / 'compare/scripts/run/pilot_observer.py'),
        '--release', str(release), '--observer', 'on', '--out', str(target),
        '--only', args.scenario, '--mode', args.mode,
        '--timeout', str(args.timeout),
    ]
    result = subprocess.run(command, cwd=ROOT, env=_env())
    ok = result.returncode == 0 and completed_case(
        target, release, args.scenario, collector_hash,
        replay_binary=replay_binary if replay_binary.is_file() else None,
        timeout=args.timeout,
    )
    print(json.dumps({
        'scenario_id': args.scenario,
        'status': 'completed' if ok else 'failed',
        'returncode': result.returncode,
        'run_dir': str(target),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


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
    release_id = manifest.get('release_id')
    source_status = 'unbound' if release_id is None else 'release-bound'
    if release_id is not None and (not isinstance(release_id, str) or not release_id):
        problems.append('invalid release identity')
    if any(not isinstance(case.get('binding'), dict)
           or 'data_release_id' not in case['binding']
           or case['binding']['data_release_id'] != release_id
           for case in inventory['cases']):
        problems.append('runtime ledgers do not match the selected input source')
    plan = {'run_root': str(args.run_root.resolve()), 'candidates': len(inventory['cases']),
            'source_status': source_status, 'commands': commands, 'problems': problems}
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


E0_ADMISSION_SCHEMA = 'mtagentlens.e0-admission/v1'


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _admission_database(run_dir: Path) -> tuple[Path, Path]:
    """Locate the one source database and the frozen snapshot.

    The source database is the collector's live write target; the frozen
    snapshot is the closed copy that carries the final verdict. They must be
    distinct files, exactly as ``bind_run_artifacts`` requires.
    """
    collector = run_dir / 'collector'
    if collector.is_symlink() or not collector.is_dir():
        raise ValueError('collector directory is missing')
    sources = sorted(collector.glob('*.responsibility.db'))
    if len(sources) != 1:
        raise ValueError('collector must contain exactly one responsibility database')
    frozen = collector / 'finalized.db'
    if frozen.is_symlink() or not frozen.is_file():
        raise ValueError('collector/finalized.db is missing')
    if sources[0].resolve() == frozen.resolve():
        raise ValueError('source and frozen databases must be distinct files')
    return sources[0], frozen


def admit_e0_run(run_dir: Path, release: Path, replay_binary: Path,
                 *, timeout: int = 900) -> dict:
    """Read-only admission for one E0 run.

    This does not start the collector, the agent, or any paid model. It only
    proves that the recorded artifacts bind to the release and that the frozen
    database passes the current strict replay validator.
    """
    from scripts.evaluation.run_artifacts import bind_run_artifacts, read_canonical_verdict

    run_dir = Path(run_dir)
    result: dict = {'run_dir': str(run_dir), 'status': 'failed', 'failures': []}
    if run_dir.is_symlink() or not run_dir.is_dir():
        result['failures'].append('run directory is missing')
        return result
    try:
        _source, frozen = _admission_database(run_dir)
        binding = bind_run_artifacts(run_dir, frozen, Path(release))
        canonical = read_canonical_verdict(frozen)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        result['failures'].append(str(exc))
        return result

    result.update({
        'collector_run_id': binding['collector_run_id'],
        'scenario_id': binding['scenario_id'],
        'database_sha256': binding['database_sha256'],
        'summary_sha256': binding['summary_sha256'],
        'runner_events_sha256': binding['runner_events_sha256'],
        'binding_sha256': hashlib.sha256(json.dumps(
            binding, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'binding_schema': binding.get('schema'),
        'tmux': binding.get('tmux'),
        'final_decision': canonical['final_decision'],
        'final_severity': canonical['final_severity'],
        'capture_quality_status': canonical.get('capture_quality_status'),
        'checkpoint_implementation': canonical.get('checkpoint_implementation'),
        'database_contains_captured_content': True,
    })
    if result['capture_quality_status'] != 'healthy':
        result['failures'].append('capture quality is not healthy')
    if not result['checkpoint_implementation']:
        result['failures'].append('analysis checkpoint has no implementation fingerprint')

    binary = Path(replay_binary)
    if binary.is_symlink() or not binary.is_file():
        result['failures'].append('replay binary is missing or not a regular file')
        return result
    try:
        completed = subprocess.run(
            [str(binary), '--sqlite', str(frozen), '--validate-recording'],
            capture_output=True, text=True, timeout=timeout, cwd=ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result['failures'].append(f'strict replay failed to run: {exc}')
        return result
    replay_report = None
    if completed.stdout.strip():
        try:
            replay_report = json.loads(completed.stdout)
        except ValueError:
            result['failures'].append('strict replay did not emit a JSON report')
    if completed.returncode != 0:
        result['failures'].append(f'strict replay exited {completed.returncode}')
    if not isinstance(replay_report, dict):
        result['failures'].append('strict replay report is missing')
    else:
        result['replay_status'] = replay_report.get('status')
        result['capture_quality'] = replay_report.get('capture_quality')
        result['replay_scope'] = replay_report.get('scope')
        result['replay_primary_eligible'] = replay_report.get('primary_eligible')
        result['replay_reference_status'] = replay_report.get('reference_status')
        if replay_report.get('schema') != 'mtagentlens.recording-validation/v1':
            result['failures'].append('strict replay report schema mismatch')
        if replay_report.get('status') != 'ok' or replay_report.get('failures'):
            result['failures'].append('strict replay report is invalid')
        # `primary_eligible=false` is the frozen validator's explicit scope: it
        # proves structural/replay integrity only, not reference eligibility.
        # Requiring true here made every real E0 run unadmittable.
        if replay_report.get('read_only') is not True:
            result['failures'].append('strict replay did not declare a read-only validation')
        if replay_report.get('reference_status') != 'unreviewed':
            result['failures'].append('strict replay unexpectedly claims a reviewed reference')
    result['status'] = 'admitted' if not result['failures'] else 'failed'
    return result


def _validate_e0_run_local(run_dir: Path, expected_agents: int,
                           transport: dict | None) -> list[str]:
    """Run-local evidence that the binding layer does not interpret.

    The run marker, agent logs and tmux pane identities are physical evidence
    about the executed process, not detector output. Missing or escaped paths
    fail admission rather than being repaired or ignored.
    """
    failures: list[str] = []
    summary_path = run_dir / 'summary.json'
    try:
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return [f'summary.json is unreadable: {exc}']
    if not isinstance(summary, dict):
        return ['summary.json is not an object']

    marker = summary.get('run_marker')
    marker_file = run_dir / '.run_marker'
    if not isinstance(marker, str) or not marker.strip():
        failures.append('RUN_TAG marker is missing from summary.json')
    elif marker_file.is_symlink() or not marker_file.is_file():
        failures.append('RUN_TAG marker file is missing')
    else:
        try:
            stored = marker_file.read_text(encoding='utf-8').strip()
        except OSError as exc:
            failures.append(f'RUN_TAG marker file is unreadable: {exc}')
        else:
            if stored != marker:
                failures.append('RUN_TAG marker does not match summary.json')

    if summary.get('marker_executed') is not True:
        failures.append('RUN_TAG execution marker was not observed in the scenario log')

    marker_log = run_dir / 'marker.log'
    if marker_log.is_symlink() or not marker_log.is_file():
        failures.append('RUN_TAG_FILE marker log is missing')
    else:
        try:
            lines = [line for line in marker_log.read_text(
                encoding='utf-8', errors='replace').splitlines() if line.strip()]
        except OSError as exc:
            failures.append(f'RUN_TAG_FILE marker log is unreadable: {exc}')
        else:
            if not isinstance(marker, str) or not marker.strip():
                pass
            elif not lines or any(line != marker for line in lines):
                failures.append('RUN_TAG_FILE contains a foreign or missing run nonce')
            elif len(lines) < expected_agents:
                failures.append(
                    f'RUN_TAG_FILE covers {len(lines)} of {expected_agents} agents')

    logs = summary.get('agent_logs')
    if not isinstance(logs, list) or len(logs) != expected_agents:
        failures.append(
            f'agent logs cover {len(logs) if isinstance(logs, list) else 0} of {expected_agents} agents')
    else:
        for index, entry in enumerate(logs):
            relative = entry.get('path') if isinstance(entry, dict) else None
            if not isinstance(relative, str) or not relative:
                failures.append(f'agent log {index} has no path')
                continue
            path = run_dir / relative
            try:
                path.resolve().relative_to(run_dir.resolve())
            except ValueError:
                failures.append(f'agent log {index} escapes the run directory')
                continue
            if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
                failures.append(f'agent log {index} is missing, empty or a symlink')
        if summary.get('agents_total') not in (None, expected_agents):
            failures.append('summary agent count does not match the registered stratum')

    if expected_agents > 1:
        if not isinstance(transport, dict) or transport.get('transport') != 'tmux':
            failures.append('multi-agent run has no tmux transport evidence')
        else:
            if transport.get('panes_expected') != expected_agents:
                failures.append('tmux pane count does not match the registered stratum')
            if transport.get('pane_observation_complete') is not True:
                failures.append('tmux pane pid/role observation is incomplete')
            if transport.get('pane_pid_is_not_agent_pid') is not True:
                failures.append('tmux transport does not distinguish pane pid from agent pid')
            for pane in transport.get('panes') or []:
                if not isinstance(pane, dict) or not pane.get('role_file'):
                    failures.append('tmux pane has no declared role file')
                    break
    return failures


def e0_admission(run_root: Path, release: Path, replay_binary: Path,
                 *, timeout: int = 900,
                 annotation_exports: Path | None = None) -> dict:
    """Admit exactly the four fixed E0 strata."""
    run_root = Path(run_root)
    try:
        registered = {row['scenario_id']: row
                      for row in campaign_cases(Path(release), 'campaign')}
    except (OSError, ValueError, KeyError) as exc:
        return {
            'schema': E0_ADMISSION_SCHEMA,
            'scope': 'read-only E0 artifact admission; no collector, agent, model, or paid call is started',
            'execution_started': False,
            'paid_calls': 0,
            'release': str(Path(release)),
            'run_root': str(run_root),
            'strata': [sid for sid, *_ in E0_STRATA],
            'cases': [],
            'admitted': 0,
            'required': len(E0_STRATA),
            'status': 'blocked',
            'problems': [f'release cannot define the E0 strata: {exc}'],
        }
    cases = []
    for scenario_id, label, attack_type, multi in E0_STRATA:
        expected_agents = 3 if multi else 1
        result = admit_e0_run(
            run_root / scenario_id, release, replay_binary, timeout=timeout,
        )
        expected = registered.get(scenario_id)
        if expected is None:
            result['failures'].append('registered E0 stratum is missing from the release')
            result['status'] = 'failed'
        else:
            registered_agents = expected.get('agents')
            if (expected.get('label'), expected.get('attack_type'),
                    registered_agents is not None and registered_agents > 1) != (
                    label, attack_type, multi):
                result['failures'].append('release stratum label/type/topology changed')
                result['status'] = 'failed'
            elif registered_agents != expected_agents:
                result['failures'].append('release stratum agent count changed')
                result['status'] = 'failed'
            elif expected.get('detection_excluded'):
                result['failures'].append('release stratum is detection-excluded')
                result['status'] = 'failed'
            if result.get('scenario_id') != scenario_id:
                result['failures'].append('bound run belongs to a different scenario')
                result['status'] = 'failed'
        result['failures'].extend(_validate_e0_run_local(
            run_root / scenario_id, expected_agents, result.get('tmux'),
        ))
        if result['failures']:
            result['status'] = 'failed'
        result.update({
            'expected_label': label,
            'expected_attack_type': attack_type,
            'expected_multi_agent': multi,
        })
        cases.append(result)

    annotation_root = Path(annotation_exports) if annotation_exports else None
    for case in cases:
        scenario_id = Path(case['run_dir']).name
        export = (annotation_root / f'{scenario_id}.json') if annotation_root else None
        if export is None:
            case['annotation_prepare'] = 'not_checked'
        elif export.is_symlink() or not export.is_file():
            case['annotation_prepare'] = 'missing'
            case['failures'].append('annotation prepare export is missing')
            case['status'] = 'failed'
        else:
            try:
                payload = json.loads(export.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                payload = None
                case['annotation_prepare'] = 'invalid'
                case['failures'].append(f'annotation prepare export is invalid: {exc}')
                case['status'] = 'failed'
            if isinstance(payload, dict) and payload.get('schema') == 'mtagentlens.annotation-export/v2':
                tasks = payload.get('tasks')
                if payload.get('paper_metrics_available') is not False:
                    case['annotation_prepare'] = 'invalid'
                    case['failures'].append('annotation export claims paper metrics')
                    case['status'] = 'failed'
                elif not isinstance(tasks, list) or not tasks:
                    case['annotation_prepare'] = 'invalid'
                    case['failures'].append('annotation export has no task list')
                    case['status'] = 'failed'
                elif any(not isinstance(task, dict)
                         or (task.get('binding') or {}).get('scenario_id') != scenario_id
                         for task in tasks):
                    case['annotation_prepare'] = 'invalid'
                    case['failures'].append('annotation export binds a different scenario')
                    case['status'] = 'failed'
                else:
                    case['annotation_prepare'] = 'validated'
                    case['annotation_export_sha256'] = _sha256_file(export)
            elif isinstance(payload, dict):
                case['annotation_prepare'] = 'invalid'
                case['failures'].append('annotation prepare export schema mismatch')
                case['status'] = 'failed'

    admitted = [case for case in cases if case.get('status') == 'admitted']
    report = {
        'schema': E0_ADMISSION_SCHEMA,
        'scope': 'read-only E0 artifact admission; no collector, agent, model, or paid call is started',
        'execution_started': False,
        'paid_calls': 0,
        'release': str(Path(release)),
        'run_root': str(run_root),
        'strata': [sid for sid, *_ in E0_STRATA],
        'cases': cases,
        'admitted': len(admitted),
        'required': len(E0_STRATA),
        'status': 'admitted' if len(admitted) == len(E0_STRATA) else 'blocked',
        'problems': [f'{Path(case["run_dir"]).name}: {failure}'
                     for case in cases for failure in case.get('failures', [])],
    }
    if report['status'] != 'admitted':
        return report
    for case in cases:
        expected = next(row for row in E0_STRATA if row[0] == Path(case['run_dir']).name)
        if case['final_decision'] is None:
            report['problems'].append(f'{expected[0]}: canonical verdict is missing')
        if case['annotation_prepare'] == 'not_checked':
            report['problems'].append(f'{expected[0]}: annotation prepare was not supplied')
    if report['problems']:
        report['status'] = 'blocked'
    return report


def cmd_e0_admission(args) -> int:
    run_root = args.run_root or (args.out.resolve() / 'campaign' / 'runs')
    report = e0_admission(
        run_root, args.release.resolve(), args.replay_binary.resolve(),
        timeout=args.timeout, annotation_exports=args.annotation_exports,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'admitted' else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, default=DEFAULT_RELEASE)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--hard-sensitive-egress', action='store_true',
                        help='freeze the primary campaign config with the deterministic sensitive-egress rule on')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('run-one')
    p.add_argument('--scenario', required=True)
    p.add_argument('--timeout', type=int, default=900)
    p.add_argument('--mode', choices=('auto', 'process', 'tmux-live'), default='process')
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
    p = sub.add_parser('e0-admission')
    p.add_argument('--run-root', type=Path,
                   help='campaign/runs directory; defaults to --out/campaign/runs')
    p.add_argument('--replay-binary', type=Path,
                   default=ROOT / 'collector/build/release/replay')
    p.add_argument('--annotation-exports', type=Path,
                   help='directory with one <scenario_id>.json annotation export per E0 run')
    p.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args()
    if args.hard_sensitive_egress:
        os.environ['MTAGENTLENS_HARD_SENSITIVE_EGRESS'] = '1'
    for path in (ROOT / 'env', ROOT / 'env/src'):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    handlers = {'status': cmd_status, 'run-one': cmd_run_one,
                'multi-agent': cmd_type_c, 'type-c': cmd_type_c,
                'campaign': cmd_campaign, 'e0': cmd_e0,
                'e8': cmd_e8, 'e9': cmd_e9, 'e9-system': cmd_e9_system,
                'runtime-annotations': cmd_runtime_annotations,
                'e0-admission': cmd_e0_admission}
    try:
        if args.command != 'status' and args.out.resolve().is_relative_to(args.release.resolve()):
            raise ValueError('output must be outside the immutable dataset release')
        return handlers[args.command](args)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        print(f'blocked: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
