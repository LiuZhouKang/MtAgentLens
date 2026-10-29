# SPDX-License-Identifier: Apache-2.0 OR MIT
from scripts.evaluation import overhead_diagnostics as diagnostic


def test_diagnostic_reuses_c1_without_mutating_formal_plan(tmp_path):
    condition={'condition_id':'c1-r1-C1','profile':'C1','concurrency':1,'repeat':1,
               'collector_command':['collector','trace','--capture-only','-o','formal/trace.log'],
               'workload_argv':['python','fixed-workload'],'output':'formal'}
    plan={'condition_runs':[condition]}
    row=diagnostic.diagnostic_condition(plan,1,tmp_path)
    assert condition['collector_command'][-1]=='formal/trace.log'
    assert row['collector_command'][-1]==str(tmp_path/'r1/trace.log')
    assert row['workload_argv']==condition['workload_argv']
    assert row['profile']=='C1' and row['concurrency']==1
