"""Startup inventory is metadata, never a substitute for an EXEC witness."""
import json,sqlite3
import pytest
from compare.tests.test_run_binding import artifacts,add_collector_context
from scripts.evaluation import run_artifacts


def write_snapshot(path,snapshot):
    with sqlite3.connect(path) as conn:
        payload=json.loads(conn.execute('SELECT payload_json FROM analysis_checkpoints').fetchone()[0])
        payload['capture_quality']['initial_process_snapshot']=snapshot
        payload['capture_quality']['process_namespace_mappings']={'schema':'mtagentlens.kernel-process-mappings/v2','dropped':0,'records':[]}
        conn.execute('UPDATE analysis_checkpoints SET payload_json=?',(json.dumps(payload),))


@pytest.fixture
def inventory(artifacts):
    add_collector_context(artifacts)
    snapshot={'schema':'mtagentlens.kernel-process-snapshot/v1','dropped':0,'records':[
        {'pid':900000,'start_time_ns':123,'parent_pid':0,'parent_start_time_ns':0,'comm':'resident',
         'pgid':4321,'cgroup_id':9876543210,'kernel_timestamp_ns':456,
         'monitor_pid_namespace':None,'monitor_pid_namespace_error':-61}]}
    return artifacts,snapshot


def bind(inventory):
    (run,frozen,release,_,_),snapshot=inventory
    for path in (run/'collector/native.responsibility.db',frozen):write_snapshot(path,snapshot)
    return run_artifacts.bind_run_artifacts(run,frozen,release)


def test_initial_snapshot_has_its_own_binding_and_never_claims_exec(inventory):
    binding=bind(inventory)
    assert binding['schema']=='mtagentlens.run-binding/v4'
    assert len(binding['initial_snapshot_sha256'])==64
    assert binding['kernel_process_alignment']['status']=='unproven'
    assert binding['event_alignment_available'] is False


@pytest.mark.parametrize('change',['schema','bool_pid','start','comm','namespace','error','bool_dropped','unknown_field'])
def test_bad_snapshot_metadata_is_rejected(inventory,change):
    _,snapshot=inventory;record=snapshot['records'][0]
    if change=='schema':snapshot['schema']='unsupported'
    elif change=='bool_pid':record['pid']=True
    elif change=='start':record['start_time_ns']=0
    elif change=='comm':record['comm']=False
    elif change=='namespace':record['monitor_pid_namespace']={'device':5,'inode':123,'tgid':42}
    elif change=='error':record['monitor_pid_namespace_error']=0
    elif change=='bool_dropped':snapshot['dropped']=True
    else:record['executed']=True
    with pytest.raises(ValueError):bind(inventory)


def test_snapshot_source_and_frozen_database_must_agree(inventory):
    (run,frozen,release,_,_),snapshot=inventory
    bind(inventory)
    snapshot['records'][0]['comm']='different'
    write_snapshot(frozen,snapshot)
    with pytest.raises(ValueError):run_artifacts.bind_run_artifacts(run,frozen,release)
