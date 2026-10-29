# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Execute the frozen 36-condition study, with separate pilot and cost evidence."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.evaluation import overhead_driver as driver
from scripts.evaluation import overhead_evidence as evidence
from scripts.evaluation.overhead_workload import agent_usage


def load_vendor_env(path, base=None):
    result = dict(os.environ if base is None else base)
    for line in Path(path).read_text().splitlines():
        if '=' not in line or line.lstrip().startswith('#'):
            continue
        key, value = line.split('=', 1)
        if key.strip().startswith('ANTHROPIC_'):
            result[key.strip()] = ' '.join(shlex.split(value))
    result.update({'MTL_ALLOW_PAID': '1', 'MTAGENTLENS_OBSERVER_MODEL': 'deepseek-flash'})
    return result


def check_budget(evidence_rows, limit):
    if any(row.get('cost_upper_bound_usd') is None for row in evidence_rows):
        raise ValueError('actual usage cost is unknown; stopping subsequent paid conditions')
    spent = sum(row['cost_upper_bound_usd'] for row in evidence_rows)
    if spent >= limit:
        raise ValueError('confirmed budget reached; stopping subsequent paid conditions')
    return spent


def validate_measurement(result,supplement):
    if result['status']!='completed':
        raise ValueError(f"condition failed/degraded: {result.get('error',result['status'])}")
    if result['profile']!='C0' and not result.get('controlled_capture',{}).get('estimable'):
        raise ValueError('capture measurement is not estimable; retain attempt and stop')
    if result['profile']=='C3':
        if not any(row.get('success') and row.get('usage') for row in supplement['observer']['attempt_records']):
            raise ValueError('C3 did not exercise a successful real Observer request')
        if supplement['alert_latency']['linked_operations']<1:
            raise ValueError('C3 has no exact-linked final alert latency sample')


def build_recovery_plan(frozen, timeout_s):
    """Reuse the frozen matrix, raising only the harness shutdown guard.

    The plan hash covers ``timeout_s``, so a recovery attempt has its own plan
    fingerprint; every condition definition stays byte-identical.
    """
    if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
            or not timeout_s > 0):
        raise ValueError('recovery shutdown timeout must be positive')
    plan = json.loads(json.dumps(frozen))
    plan['timeout_s'] = float(timeout_s)
    plan.pop('plan_sha256', None)
    plan['plan_sha256'] = driver.sha256_bytes(driver.canonical(plan).encode())
    return plan


def recovery_targets(execution, plan):
    """Planned condition ids without a completed result, in frozen plan order."""
    completed = {row.get('condition_id') for row in execution.get('results', [])
                 if row.get('status') == 'completed'}
    return [row['condition_id'] for row in plan['condition_runs']
            if row['condition_id'] not in completed]


def archive_condition(study, condition_id, stamp=None):
    """Move one failed attempt outside ``formal/`` so it stays auditable."""
    source = Path(study) / 'formal' / condition_id
    if not source.is_dir():
        return None
    stamp = stamp or time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    target = Path(study) / 'formal-attempts' / f'{condition_id}-{stamp}'
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError(f'recovery archive already exists: {target}')
    shutil.move(str(source), str(target))
    return target


def update_execution_report(execution, recovered, recovery_meta, added_cost):
    """Fold recovered results into the cohort manifest without rewriting history."""
    report = json.loads(json.dumps(execution))
    replacements = {row['condition_id']: row for row in recovered}
    report['results'] = [replacements.get(row.get('condition_id'), row)
                         for row in report.get('results', [])]
    completed = sum(1 for row in report['results'] if row.get('status') == 'completed')
    expected = report.get('conditions_expected', len(report['results']))
    report['conditions_completed'] = completed
    report['complete'] = completed == expected and expected == len(report['results'])
    failures = [row.get('error') or row.get('status') for row in report['results']
                if row.get('status') != 'completed']
    report['failure'] = None if report['complete'] else '; '.join(
        str(value) for value in failures if value)
    base = report.get('known_cost_upper_bound_usd')
    report['known_cost_upper_bound_usd'] = (
        None if base is None or added_cost is None else base + added_cost)
    report['recovery'] = recovery_meta
    return report


def recover(args):
    """Re-run only failed/missing conditions into the frozen formal cohort."""
    study = args.out.resolve()
    frozen = json.loads((study / 'formal-plan.json').read_text())
    execution_path = study / 'formal' / 'execution-report.json'
    execution = json.loads(execution_path.read_text())
    plan = build_recovery_plan(frozen, args.shutdown_timeout_s)
    targets = recovery_targets(execution, frozen)
    if args.condition_ids:
        unknown = sorted(set(args.condition_ids) - set(targets))
        if unknown:
            raise ValueError('conditions are not failed/missing in this cohort: '
                             + ', '.join(unknown))
        targets = [condition_id for condition_id in targets
                   if condition_id in set(args.condition_ids)]
    if not targets:
        print(json.dumps({'recovery': 'not_needed', 'targets': []}), flush=True)
        return 0
    env = load_vendor_env(args.vendor_env)
    driver.verify_manifest(plan, env)
    authenticate(args.sudo_password_stdin)
    prior = prior_cost_rows(study.parent)
    recovered, archived, supplements = [], [], {}
    started = time.time_ns()
    failure = None
    try:
        for condition_id in targets:
            condition = next(row for row in plan['condition_runs']
                             if row['condition_id'] == condition_id)
            if (study / 'formal' / condition_id).is_dir():
                moved = archive_condition(study, condition_id)
                old = json.loads((moved / 'result.json').read_text())
                archived.append({'condition_id': condition_id, 'path': str(moved),
                                 'status': old.get('status'),
                                 'error': old.get('error'),
                                 'forced_kill': old.get('collector', {}).get('forced_kill')})
            spent = check_budget(prior + list(supplements.values()), args.max_budget_usd)
            if spent + .02 * condition['concurrency'] + .05 > args.max_budget_usd:
                raise ValueError('insufficient remaining budget reserve for recovery')
            authenticate(False)
            print(json.dumps({'recovering': condition_id,
                              'completed': len(recovered), 'planned': len(targets)}),
                  flush=True)
            row = driver.run_condition(plan, condition, env=env)
            supplement = evidence.analyze_condition(row, Path(condition['output']))
            (Path(condition['output']) / 'measurement-evidence.json').write_text(
                json.dumps(supplement, indent=2) + '\n')
            supplements[condition_id] = supplement
            recovered.append(row)
            print(json.dumps({'recovered': condition_id, 'status': row['status'],
                              'cost_upper_bound_usd': supplement['cost_upper_bound_usd']}),
                  flush=True)
            check_budget(prior + list(supplements.values()), args.max_budget_usd)
            validate_measurement(row, supplement)
    except (ValueError, OSError, RuntimeError) as error:
        failure = str(error)
    costs = [row.get('cost_upper_bound_usd') for row in supplements.values()]
    added = (sum(costs) if costs and all(value is not None for value in costs)
             else (0.0 if not costs else None))
    meta = {'frozen_plan_sha256': frozen.get('plan_sha256'),
            'recovery_plan_sha256': plan['plan_sha256'],
            'frozen_shutdown_timeout_s': frozen.get('timeout_s'),
            'recovery_shutdown_timeout_s': plan.get('timeout_s'),
            'rule': ('one pre-registered recovery attempt for a truncated or missing '
                     'condition; the shutdown guard is a harness bound, not a treatment; '
                     'the archived failed attempt remains counted in cost and provenance'),
            'targets': targets, 'executed_condition_ids': [row['condition_id'] for row in recovered],
            'archived_attempts': archived, 'started_epoch_ns': started,
            'ended_epoch_ns': time.time_ns()}
    updated = update_execution_report(execution, recovered, meta, added)
    execution_path.write_text(json.dumps(updated, indent=2) + '\n')
    (study / 'formal-recovery-plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    restore_artifact_owner(study)
    print(json.dumps({'recovery_complete': updated['complete'],
                      'failure': updated.get('failure')}), flush=True)
    if failure:
        print(json.dumps({'stopped': failure}), flush=True)
        return 1
    return 0


def prior_cost_rows(parent):
    """Count earlier pilot/aborted/formal attempts in this experiment's budget."""
    rows=[]
    for path in parent.glob('study-*/**/measurement-evidence.json'):
        value=json.loads(path.read_text())
        result_path=path.parent/'result.json'
        result=json.loads(result_path.read_text()) if result_path.is_file() else {}
        native_results=list(path.parent.glob('instance-*/agent.stdout'))
        if native_results:
            # Recompute cumulative session usage, including failed native budget
            # attempts. Earlier calibration manifests used only terminal usage.
            try:
                costs=[agent_usage(json.loads(p.read_text()),require_success=False)
                       ['cost_upper_bound_usd'] for p in native_results]
                expected=result.get('concurrency',len(native_results))
                observer=value.get('observer',{}).get('cost_upper_bound_usd')
                if value.get('diagnostic'):
                    observer=0
                value={**value,'cost_upper_bound_usd':sum(costs)+observer
                       if len(costs)==expected and observer is not None else None}
            except (ValueError,TypeError,KeyError):
                value={**value,'cost_upper_bound_usd':None}
        if value.get('cost_upper_bound_usd') is None:
            # Proven pre-workload startup failures made no model requests.
            if not result.get('workloads') and value.get('observer',{}).get('attempts')==0:
                value={**value,'cost_upper_bound_usd':0.0}
        if (result.get('profile')=='C3'
                and result.get('collector',{}).get('forced_kill')
                and value.get('cost_upper_bound_usd') is not None):
            # Sequential provider: at most one request lacks a terminal ledger
            # at SIGKILL. Reserve the entire 1M context at the peak miss rate
            # plus max_tokens=4096 output: .30 + .0049152 < .31 USD.
            # This is a conservative budget reserve, never observed token usage.
            value={**value,'censored_request_reserve_usd':.31,
                   'cost_upper_bound_usd':value['cost_upper_bound_usd']+.31}
        rows.append(value)
    for path in parent.glob('pilot-agent-*/instance-*/agent-metrics.json'):
        value=json.loads(path.read_text())
        raw=path.parent/'agent.stdout'
        usage=agent_usage(json.loads(raw.read_text()),require_success=False) if raw.is_file() else value['usage']
        rows.append({'cost_upper_bound_usd':usage['cost_upper_bound_usd']})
    return rows


def authenticate(password_stdin):
    result = subprocess.run(['sudo', '-n', '-v'], capture_output=True)
    if result.returncode and password_stdin:
        password = sys.stdin.buffer.readline()
        result = subprocess.run(['sudo', '-S', '-v'], input=password, capture_output=True)
        del password
    if result.returncode:
        raise ValueError('sudo credentials unavailable; authenticate or use --sudo-password-stdin')


def prepare(root, collector, agent, env):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    pinned = root/'bin'
    pinned.mkdir(exist_ok=True,mode=0o700)
    if os.geteuid()==0 and os.environ.get('SUDO_UID'):
        owner=(int(os.environ['SUDO_UID']),int(os.environ['SUDO_GID']))
        os.chown(root,*owner)
        os.chown(pinned,*owner)
    target = pinned/'mtagentlens'
    digest = driver.sha256_file(collector)
    if target.exists():
        if driver.sha256_file(target) != digest:
            raise ValueError('pinned collector differs; use a new experiment directory')
    else:
        shutil.copy2(collector,target)
    alias = pinned/'mtl-oh-agent'
    if not alias.exists():
        alias.symlink_to(Path(agent).resolve())
    elif alias.resolve()!=Path(agent).resolve():
        raise ValueError('pinned Agent symlink differs')
    wrapper = pinned/'collector-root'
    keys = [key for key in env if key.startswith('ANTHROPIC_') or key=='MTAGENTLENS_OBSERVER_MODEL']
    # No credential values in the wrapper or argv. sudo preserves only names.
    text = '#!/bin/sh\nexec sudo -n --preserve-env='+','.join(sorted(keys))+' '+shlex.quote(str(target))+' "$@" --binary-path '+shlex.quote(str(Path(agent).resolve()))+'\n'
    if wrapper.exists() and wrapper.read_text()!=text:
        raise ValueError('pinned privilege wrapper differs')
    wrapper.write_text(text)
    wrapper.chmod(0o700)
    binding={'collector_sha256':digest,'agent_sha256':driver.sha256_file(agent),
             'kernel':os.uname().release,'operations_per_instance':16,
             'budget_basis':'actual usage at conservative peak rates; not provider invoice',
             'pricing_date':'2026-10-28',
             'pricing_source':'https://api-docs.deepseek.com/quick_start/pricing/',
             'source_hashes':{str(path.relative_to(ROOT)):driver.sha256_file(path) for path in (
                 ROOT/'scripts/evaluation/overhead_driver.py',
                 ROOT/'scripts/evaluation/overhead_metrics.py',
                 ROOT/'scripts/evaluation/overhead_workload.py',
                 ROOT/'scripts/evaluation/overhead_evidence.py',
                 ROOT/'scripts/evaluation/controlled_capture.py',
                 ROOT/'scripts/evaluation/run_overhead_experiment.py')}}
    binding_path=root/'build-binding.json'
    if binding_path.exists() and json.loads(binding_path.read_text())!=binding:
        raise ValueError('source binding changed; use a new experiment directory')
    binding_path.write_text(json.dumps(binding,indent=2)+'\n')
    return wrapper, alias


def restore_artifact_owner(root):
    if os.geteuid()!=0 or not os.environ.get('SUDO_UID'):
        return
    uid,gid=int(os.environ['SUDO_UID']),int(os.environ['SUDO_GID'])
    for path in [root,*root.rglob('*')]:
        os.chown(path,uid,gid,follow_symlinks=False)


def run(args):
    if getattr(args, 'recover', False):
        return recover(args)
    env = load_vendor_env(args.vendor_env)
    wrapper, agent = prepare(args.out.resolve(),args.collector.resolve(),args.agent.resolve(),env)
    output = args.out.resolve()/args.phase
    plan = driver.build_plan(wrapper,output,
        [sys.executable,str(ROOT/'scripts/evaluation/overhead_workload.py'),'agent',
         '--binary',str(agent),'--operations','16'],16,timeout_s=300,
        agent_comm='mtl-oh-agent',env=env,sample_interval_ms=50)
    if args.phase=='formal':
        pilot=args.out.resolve()/'pilot/execution-report.json'
        if not pilot.is_file() or not json.loads(pilot.read_text()).get('complete'):
            raise ValueError('formal study requires a complete separate four-condition pilot')
        plan['warmup']={'executed':True,'counted_in_blocks':False,'condition_runs':4,
            'note':'separate concurrency-1 pilot; no per-level warmup or API cache-hit guarantee',
            'evidence_path':str(pilot),'evidence_sha256':driver.sha256_file(pilot)}
    plan['measurement_evidence']={'schema':'mtagentlens.overhead-supplement/v1',
        'observer_source':'collector.stderr [observer:attempt]',
        'alert_source':'exact action/evidence/claim ids + [alert:emitted]',
        'clock_source':'raw BPF monotonic timestamp + independent receipt wall/monotonic witness'}
    plan['plan_sha256']=driver.sha256_bytes(driver.canonical(
        {key:value for key,value in plan.items() if key!='plan_sha256'}).encode())
    plan_path=args.out.resolve()/f'{args.phase}-plan.json'
    plan_path.write_text(json.dumps(plan,indent=2)+'\n')
    if not args.execute:
        print(json.dumps({'execute':False,'plan':str(plan_path),
                          'condition_runs':plan['condition_run_count'],
                          'workload_instances':plan['workload_instances']}),flush=True)
        return 0
    authenticate(args.sudo_password_stdin)
    driver.check_execution(plan,execute=True,env=env,max_budget_usd=args.max_budget_usd)
    output.mkdir(mode=0o700)
    if os.geteuid()==0 and os.environ.get('SUDO_UID'):
        os.chown(output,int(os.environ['SUDO_UID']),int(os.environ['SUDO_GID']))
    rows=[]; measurements=[]
    prior=prior_cost_rows(args.out.resolve().parent)
    conditions=plan['condition_runs']
    if args.phase=='pilot':
        conditions=[row for row in conditions if row['concurrency']==1 and row['repeat']==1]
    start=time.time_ns()
    failure=None
    try:
        for condition in conditions:
            spent=check_budget(prior+measurements,args.max_budget_usd)
            if spent + .02*condition['concurrency']+.05 > args.max_budget_usd:
                raise ValueError('insufficient remaining budget reserve for next concurrent condition')
            # sudo cache is refreshed by the same orchestrator parent.
            authenticate(False)
            print(json.dumps({'starting':condition['condition_id'],
                              'completed':len(rows),'planned':len(conditions)}),flush=True)
            row=driver.run_condition(plan,condition,env=env)
            rows.append(row)
            supplement=evidence.analyze_condition(row,Path(condition['output']))
            measurements.append(supplement)
            print(json.dumps({'finished':condition['condition_id'],'status':row['status'],
                  'capture_estimable':row.get('controlled_capture',{}).get('estimable'),
                  'observer_attempts':supplement['observer']['attempts'],
                  'cost_upper_bound_usd':supplement['cost_upper_bound_usd']}),flush=True)
            check_budget(prior+measurements,args.max_budget_usd)
            validate_measurement(row,supplement)
    except (ValueError,OSError,RuntimeError) as error:
        failure=str(error)
    report={'schema':'mtagentlens.overhead-execution/v1','phase':args.phase,
            'plan_sha256':plan['plan_sha256'],'started_epoch_ns':start,
            'ended_epoch_ns':time.time_ns(),'conditions_expected':len(conditions),
            'conditions_completed':len(rows),'complete':failure is None and len(rows)==len(conditions),
            'failure':failure,'confirmed_budget_usd':args.max_budget_usd,
            'known_cost_upper_bound_usd':sum(row.get('cost_upper_bound_usd') or 0 for row in measurements),
            'prior_cost_upper_bound_usd':sum(row.get('cost_upper_bound_usd') or 0 for row in prior),
            'results':rows}
    (output/'execution-report.json').write_text(json.dumps(report,indent=2)+'\n')
    restore_artifact_owner(args.out.resolve())
    if failure:
        print(json.dumps({'stopped':failure}),flush=True)
        return 1
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--collector',type=Path,required=True)
    parser.add_argument('--agent',type=Path,required=True)
    parser.add_argument('--vendor-env',type=Path,default=ROOT/'env/config/vendors/deepseek/claude.env')
    parser.add_argument('--phase',choices=('pilot','formal'),default='pilot')
    parser.add_argument('--max-budget-usd',type=float,default=5)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--recover',action='store_true',
                        help='re-run only failed/missing conditions of the frozen formal cohort')
    parser.add_argument('--condition-id',action='append',dest='condition_ids',
                        help='limit --recover to these failed condition ids (repeatable)')
    parser.add_argument('--shutdown-timeout-s',type=float,default=300,
                        help='--recover harness shutdown guard; never changes the measurement')
    parser.add_argument('--sudo-password-stdin',action='store_true')
    args=parser.parse_args(argv)
    try:
        return run(args)
    except (ValueError,OSError,subprocess.SubprocessError) as error:
        print(str(error),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
