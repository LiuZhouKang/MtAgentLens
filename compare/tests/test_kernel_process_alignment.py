"""Synthetic kernel witnesses exercise independent native launch matching."""
import json
import sqlite3

import pytest
from compare.tests.test_run_binding import artifacts, add_collector_context
from scripts.evaluation import run_artifacts


def write_mappings(path, mappings):
    with sqlite3.connect(path) as conn:
        payload=json.loads(conn.execute('SELECT payload_json FROM analysis_checkpoints').fetchone()[0])
        payload['capture_quality']['process_namespace_mappings']=mappings
        conn.execute('UPDATE analysis_checkpoints SET payload_json=?',(json.dumps(payload),))


@pytest.fixture
def witnesses(artifacts):
    run,frozen,release,_,_=artifacts
    add_collector_context(artifacts)
    rows=[json.loads(line) for line in (run/'runner-events.jsonl').read_text().splitlines()]
    attempt=next(row for row in rows if row['event_type']=='launch_attempt')
    start=next(row for row in rows if row['event_type']=='process_started')
    sample=start['details']['observation']
    ns=sample['target']['namespaces']['pid']
    record={'pid':900000,'start_time_ns':(attempt['timestamp_ns']+start['timestamp_ns'])//2,
            'pid_namespace':dict(ns,tgid=sample['target']['nspid'][-1]),'pid_namespace_error':0}
    mappings={'schema':'mtagentlens.kernel-process-mappings/v1','dropped':0,'records':[record]}
    return artifacts,rows,mappings


def bind(witnesses):
    artifacts,rows,mappings=witnesses
    run,frozen,release,_,_=artifacts
    (run/'runner-events.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    for path in (run/'collector/native.responsibility.db',frozen):write_mappings(path,mappings)
    return run_artifacts.bind_run_artifacts(run,frozen,release)


def test_namespace_and_launch_window_match_a_different_kernel_pid(witnesses):
    binding=bind(witnesses)
    assert binding['schema']=='mtagentlens.run-binding/v3'
    alignment=binding['kernel_process_alignment']
    assert alignment['status']=='matched'
    assert alignment['actors'][0]['kernel_process']['pid']==900000
    assert alignment['scope']=='top_level_invocation_process'
    assert alignment['event_alignment_available'] is False
    assert binding['event_alignment_available'] is False


@pytest.mark.parametrize('change',['wrong_namespace','wrong_tgid','outside_window','helper_failed','records_dropped','native_unknown','namespace_missing','boot_mismatch'])
def test_missing_or_conflicting_evidence_never_becomes_a_matched_process(witnesses,change):
    artifacts,rows,mappings=witnesses
    record=mappings['records'][0]
    start=next(row for row in rows if row['event_type']=='process_started')
    if change=='wrong_namespace':record['pid_namespace']['inode']+=1
    elif change=='wrong_tgid':record['pid_namespace']['tgid']+=1
    elif change=='outside_window':record['start_time_ns']=1
    elif change=='helper_failed':record['pid_namespace']=None;record['pid_namespace_error']=-22
    elif change=='records_dropped':mappings['dropped']=1
    elif change=='namespace_missing':start['details']['observation']['target']['namespaces']['pid']=None
    elif change=='boot_mismatch':
        for row in rows:
            if row['process_key'] is not None:row['process_key']['boot_id']='d32c240e-0fd1-45e0-83bf-7cb2d2a39d32'
    else:
        for row in rows:
            if row['process_key'] is not None:row['process_key']['start_ticks']=None
        start['details']['observation']['status']='unavailable'
        start['details']['observation']['target']=None
    alignment=bind(witnesses)['kernel_process_alignment']
    assert alignment['status']=='unproven'
    assert alignment['actors'][0]['status']=='unproven'
    assert alignment['actors'][0]['kernel_process'] is None


def test_ambiguous_kernel_candidates_are_preserved_without_picking_first(witnesses):
    _,_,mappings=witnesses
    mappings['records'].append(dict(mappings['records'][0],pid=900001))
    alignment=bind(witnesses)['kernel_process_alignment']
    assert alignment['status']=='unproven'
    assert alignment['actors'][0]['status']=='ambiguous'
    assert alignment['actors'][0]['kernel_process'] is None


def test_nonzero_monotonic_offset_is_subtracted_instead_of_converting_proc_ticks(witnesses):
    _,rows,_=witnesses
    start=next(row for row in rows if row['event_type']=='process_started')
    sample=start['details']['observation']
    for row in rows:
        row['timestamp_ns']+=7000000000
        witness=row['details'].get('runner_context')
        if witness is not None:
            witness['sample_before_ns']+=7000000000;witness['sample_after_ns']+=7000000000
            witness['boottime_ns']+=11000000000
            witness['observer']['time_offsets_ns']={'monotonic':7000000000,'boottime':11000000000}
    sample['sample_before_ns']+=7000000000;sample['sample_after_ns']+=7000000000
    sample['boottime_ns']+=11000000000
    for entity in (sample['observer'],sample['target']):
        entity['time_offsets_ns']={'monotonic':7000000000,'boottime':11000000000}
    binding=bind(witnesses)
    assert binding['process_context_check']['status']=='unproven'
    assert binding['kernel_process_alignment']['status']=='matched'
    assert 'runner_clock_endpoints_sampled_continuity_unproven' in binding['kernel_process_alignment']['limitations']


def test_sampled_runner_clock_endpoints_replace_the_unsampled_assumption(witnesses):
    alignment=bind(witnesses)['kernel_process_alignment']
    assert alignment['status']=='matched'
    assert 'runner_clock_endpoints_sampled_continuity_unproven' in alignment['limitations']
    assert 'runner_clock_namespace_assumed_stable_across_launch' not in alignment['limitations']


INCOMPLETE_REASONS = {'runner_clock_context_unavailable', 'runner_clock_context_incomplete',
                      'runner_boot_id_unavailable'}


@pytest.mark.parametrize('change,reason',[
    ('pre_offset','runner_clock_offset_changed_across_launch'),
    ('post_offset','runner_clock_offset_changed_across_launch'),
    ('post_boottime','runner_clock_offset_changed_across_launch'),
    ('pre_namespace','runner_namespace_changed_across_launch'),
    ('post_namespace','runner_namespace_changed_across_launch'),
    ('pre_boot','runner_boot_changed_across_launch'),
    ('pre_unavailable','runner_clock_context_unavailable'),
    ('pre_incomplete','runner_clock_context_incomplete'),
    ('post_incomplete','runner_clock_context_incomplete'),
    ('boot_unknown','runner_boot_id_unavailable'),
    ('post_boot_unknown','runner_boot_id_unavailable'),
])
def test_runner_clock_or_namespace_change_across_launch_never_matches(witnesses,change,reason):
    _,rows,_=witnesses
    attempt=next(row for row in rows if row['event_type']=='launch_attempt')
    start=next(row for row in rows if row['event_type']=='process_started')
    witness=attempt['details']['runner_context']
    observation=start['details']['observation']
    if change=='pre_offset':witness['observer']['time_offsets_ns']['monotonic']+=1
    elif change=='post_offset':observation['observer']['time_offsets_ns']['monotonic']+=1
    elif change=='post_boottime':observation['observer']['time_offsets_ns']['boottime']+=1
    elif change=='pre_namespace':witness['observer']['namespaces']['time']['inode']+=1
    elif change=='post_namespace':observation['observer']['namespaces']['mnt']['inode']+=1
    elif change=='pre_boot':witness['boot_id']='00000000-0000-4000-8000-000000000000'
    elif change=='pre_incomplete':witness['observer']['namespaces']['time']=None
    elif change=='post_incomplete':observation['observer']['time_offsets_ns']['boottime']=None
    elif change=='boot_unknown':witness['boot_id']=None
    elif change=='post_boot_unknown':
        for row in rows:
            if row['process_key'] is not None:row['process_key']['boot_id']=None
    else:
        witness['status']='unavailable';witness['observer']['nspid']=None
    alignment=bind(witnesses)['kernel_process_alignment']
    assert alignment['status']=='unproven'
    actor=alignment['actors'][0]
    assert actor['status']=='unproven' and actor['kernel_process'] is None
    assert reason in actor['reasons']
    limitation=('runner_clock_witness_incomplete' if reason in INCOMPLETE_REASONS
                else 'runner_clock_endpoints_sampled_continuity_unproven')
    assert limitation in alignment['limitations']
    assert 'runner_clock_namespace_assumed_stable_across_launch' not in alignment['limitations']


def test_unavailable_witness_limitation_does_not_depend_on_the_observation(witnesses):
    _,rows,_=witnesses
    attempt=next(row for row in rows if row['event_type']=='launch_attempt')
    start=next(row for row in rows if row['event_type']=='process_started')
    witness=attempt['details']['runner_context']
    witness['status']='unavailable';witness['observer']['nspid']=None
    start['details']['observation']['status']='unavailable'
    start['details']['observation']['target']=None
    for row in rows:
        if row['process_key'] is not None:row['process_key']['start_ticks']=None
    alignment=bind(witnesses)['kernel_process_alignment']
    assert alignment['status']=='unproven'
    assert 'runner_clock_witness_incomplete' in alignment['limitations']
    assert 'runner_clock_endpoints_sampled_continuity_unproven' not in alignment['limitations']


def test_legacy_v3_ledger_keeps_the_previous_clock_assumption(witnesses):
    _,rows,_=witnesses
    for row in rows:
        row['schema']='mtagentlens.runner-event/v3'
        row['producer']='process-audit/3@sha256:'+'a'*64
        row['details'].pop('runner_context',None)
    alignment=bind(witnesses)['kernel_process_alignment']
    assert alignment['status']=='matched'
    assert 'runner_clock_namespace_assumed_stable_across_launch' in alignment['limitations']


def test_v4_ledger_without_a_runner_witness_is_rejected(witnesses):
    _,rows,_=witnesses
    attempt=next(row for row in rows if row['event_type']=='launch_attempt')
    attempt['details'].pop('runner_context')
    with pytest.raises(ValueError):bind(witnesses)


@pytest.mark.parametrize('change',['bool_pid','bad_start','bad_inode','positive_error','unknown_field','schema','bool_dropped','duplicate'])
def test_malformed_kernel_mapping_records_are_rejected(witnesses,change):
    _,_,mappings=witnesses
    record=mappings['records'][0]
    if change=='bool_pid':record['pid']=True
    elif change=='bad_start':record['start_time_ns']=0
    elif change=='bad_inode':record['pid_namespace']['inode']=-1
    elif change=='positive_error':record['pid_namespace_error']=1
    elif change=='unknown_field':record['agent_id']='do-not-trust'
    elif change=='schema':mappings['schema']='unsupported'
    elif change=='bool_dropped':mappings['dropped']=True
    else:mappings['records'].append(record.copy())
    with pytest.raises(ValueError):bind(witnesses)


def test_source_and_frozen_mapping_evidence_must_match(witnesses):
    artifacts,_,mappings=witnesses
    bind(witnesses)
    mappings['records'][0]['pid']+=1
    write_mappings(artifacts[1],mappings)
    with pytest.raises(ValueError):run_artifacts.bind_run_artifacts(artifacts[0],artifacts[1],artifacts[2])


def test_v2_witness_preserves_kernel_exec_metadata_without_changing_pid_matching(witnesses):
    _,_,mappings=witnesses
    mappings['schema']='mtagentlens.kernel-process-mappings/v2'
    mappings['records'][0].update(pgid=4321,cgroup_id=9876543210)
    assert bind(witnesses)['kernel_process_alignment']['status']=='matched'


@pytest.mark.parametrize('field,value',[('pgid',True),('pgid',0),('pgid',4294967296),
                                       ('cgroup_id',-1),('cgroup_id',True),('cgroup_id',18446744073709551616)])
def test_v2_metadata_types_and_ranges_cannot_be_fabricated(witnesses,field,value):
    _,_,mappings=witnesses
    mappings['schema']='mtagentlens.kernel-process-mappings/v2'
    mappings['records'][0].update(pgid=None,cgroup_id=None)
    mappings['records'][0][field]=value
    with pytest.raises(ValueError):bind(witnesses)
