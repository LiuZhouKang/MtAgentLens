# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Three separate C1 diagnostic windows; never part of formal performance timing."""
import argparse
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from scripts.evaluation import overhead_driver as driver
from scripts.evaluation import probe_overhead as probes
from scripts.evaluation import run_overhead_experiment as experiment


def diagnostic_condition(plan,repeat,output):
    row=copy.deepcopy(next(row for row in plan['condition_runs']
                           if row['profile']=='C1' and row['repeat']==repeat))
    folder=Path(output)/f'r{repeat}'
    row['output']=str(folder)
    command=row['collector_command']
    command[command.index('-o')+1]=str(folder/'trace.log')
    return row


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study',type=Path,required=True)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args(argv)
    study=args.study.resolve()
    plan=json.loads((study/'formal-plan.json').read_text())
    if not args.execute:
        print(json.dumps({'execute':False,'repeats':3,'scope':'C1 workload outside main timing'}))
        return 0
    env=experiment.load_vendor_env(ROOT/'env/config/vendors/deepseek/claude.env')
    driver.verify_manifest(plan,env)
    output=study/'diagnostics'
    output.mkdir(mode=0o700)
    if os.environ.get('SUDO_UID'):
        os.chown(output,int(os.environ['SUDO_UID']),int(os.environ['SUDO_GID']))
    for repeat in range(1,4):
        experiment.check_budget(experiment.prior_cost_rows(study.parent),5)
        row=diagnostic_condition(plan,repeat,output)
        folder=Path(row['output']);folder.mkdir(mode=0o700)
        before=probes.snapshot_programs('/usr/sbin/bpftool',sudo=False)
        with (folder/'collector.stdout').open('w') as stdout,(folder/'collector.stderr').open('w') as stderr:
            collector=subprocess.Popen(row['collector_command'],cwd=folder,env=env,
                                       stdout=stdout,stderr=stderr,start_new_session=True)
            try:
                time.sleep(1)
                if collector.poll() is not None:
                    raise RuntimeError('C1 diagnostic collector did not start')
                loaded=probes.snapshot_programs('/usr/sbin/bpftool',sudo=False)
                selected=probes.select_run_programs({p['id'] for p in before},loaded,
                    probes.snapshot_link_program_ids('/usr/sbin/bpftool',sudo=False))
                ids=[p['id'] for p in selected]
                with probes.StatsGate() as gate:
                    start=probes.snapshot_programs('/usr/sbin/bpftool',sudo=False)
                    began=time.monotonic()
                    with (folder/'workload-0.stdout').open('w') as out,(folder/'workload-0.stderr').open('w') as err:
                        result=subprocess.run(row['workload_argv'],cwd=folder,
                            env=driver._workload_environment(plan,row,0,env),
                            stdout=out,stderr=err,timeout=180)
                    ended=time.monotonic()
                    end=probes.snapshot_programs('/usr/sbin/bpftool',sudo=False)
                if result.returncode:
                    raise RuntimeError('diagnostic workload failed')
                data={'schema':probes.SCHEMA_DIAGNOSTIC,'status':'ok',
                    'mode':'separate_c1_workload_diagnostic','repeat':repeat,
                    'formal_plan_sha256':plan['plan_sha256'],'program_ids':ids,
                    'programs':probes.counter_deltas(start,end,ids),
                    'start_snapshot':start,'end_snapshot':end,'window_seconds':ended-began,
                    'stats_gate':{'original':gate.original,'restored':gate.restored},
                    'note':'Includes filtered returns and system-wide hook invocations; not per-effective-event cost.'}
                (folder/'probe-diagnostic.json').write_text(json.dumps(data,indent=2)+'\n')
                agent=json.loads((folder/'instance-0/agent-metrics.json').read_text())
                (folder/'measurement-evidence.json').write_text(json.dumps({
                    'cost_upper_bound_usd':agent['usage']['cost_upper_bound_usd'],
                    'observer':{'attempts':0},'diagnostic':True},indent=2)+'\n')
                print(json.dumps({'diagnostic_repeat':repeat,'programs':len(ids),
                                  'stats_restored':gate.restored}),flush=True)
            finally:
                if collector.poll() is None:
                    collector.send_signal(signal.SIGINT)
                    try:
                        collector.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(collector.pid,signal.SIGKILL)
                        collector.wait()
    experiment.restore_artifact_owner(study)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
