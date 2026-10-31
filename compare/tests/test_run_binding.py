"""A real harmless launch is bound to a synthetic collector artifact, never gold."""

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.evaluation import run_artifacts


@pytest.fixture
def artifacts(tmp_path, monkeypatch, request):
    from env.tests.test_release_input_chain import source_release, configure_client
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    from mtagentlens_benchmark.engine.runner import _save_summary

    release, manifest, config = source_release(tmp_path)
    ground_truth = release/'ground_truth/scenario.json'
    ground_truth.parent.mkdir()
    ground_truth.write_text('{"scenario_id":"scenario","label":"benign","notes":"Synthetic test only"}')
    manifest['files']['ground_truth/scenario.json'] = hashlib.sha256(ground_truth.read_bytes()).hexdigest()
    manifest['release_id'] = hashlib.sha256(json.dumps({key:value for key,value in manifest.items()
                                                      if key!='release_id'},sort_keys=True).encode()).hexdigest()
    (release/'release.json').write_text(json.dumps(manifest))
    if getattr(request, "param", None) == "unbound":
        (release / "release.json").unlink()
        manifest = None
    executor = configure_client(tmp_path, monkeypatch)
    monkeypatch.setattr(executor, 'time', SimpleNamespace(time=time.time, sleep=lambda _: None))
    collector_id = str(uuid.uuid4())

    class Collector:
        is_running = False
        database_path = None
        output_paths = []

        def set_run_context(self, scenario_id, mode):
            self.scenario_id, self.mode = scenario_id, mode

        def start(self, directory):
            directory.mkdir(parents=True)
            self.database_path = directory / 'native.responsibility.db'
            with sqlite3.connect(self.database_path) as connection:
                connection.execute('CREATE TABLE runs(run_id TEXT, scenario_id TEXT, mode TEXT, '
                                   'started_at_ns INTEGER, ended_at_ns INTEGER, final_decision TEXT, final_severity TEXT)')
                connection.execute('INSERT INTO runs VALUES(?,?,?,1,NULL,NULL,NULL)',
                                   (collector_id, self.scenario_id, self.mode))
            self.output_paths = [self.database_path]
            self.is_running = True

        def stop(self):
            with sqlite3.connect(self.database_path) as connection:
                connection.execute("UPDATE runs SET ended_at_ns=2,final_decision='Uncertain',final_severity='Warning'")
            self.is_running = False

    collector = Collector()
    run = tmp_path / 'run'
    result = executor.execute_scenario(config, run, SimpleNamespace(root=tmp_path), collector=collector,
        input_audit_factory=lambda conf, directory: ReleaseInputAudit(release, manifest, conf, directory))
    assert result.success, result.error
    _save_summary(run, result)
    frozen = run / 'collector/finalized.db'
    run_artifacts.snapshot_database(collector.database_path, frozen)
    return run, frozen, release, result, collector_id


def test_binding_keeps_both_run_namespaces_and_source_uuid_explicit(artifacts):
    run, frozen, release, result, collector_id = artifacts
    before = {path: path.read_bytes() for path in (frozen, run/'summary.json', run/'runner-events.jsonl')}
    binding = run_artifacts.bind_run_artifacts(run, frozen, release)
    assert binding['schema'] == 'mtagentlens.run-binding/v1'
    assert binding['harness_run_id'] == result.run_id
    assert binding['collector_run_id'] == collector_id
    assert binding['harness_run_id'] != binding['collector_run_id']
    assert binding['source_run_uuid'] == result.summary['source_run_uuid']
    assert binding['database'] == 'collector/finalized.db'
    assert binding['source_database'] == 'collector/native.responsibility.db'
    assert binding['database_sha256'] == hashlib.sha256(frozen.read_bytes()).hexdigest()
    assert binding['event_alignment_available'] is False
    for path, content in before.items():
        assert path.read_bytes() == content
    row = {'run_dir': str(run), 'db_path': str(frozen), 'run_id': collector_id,
           'scenario_id': 'scenario', 'run_binding': binding}
    run_artifacts.validate_run_binding(row, release)


@pytest.mark.parametrize('change', ['summary_run', 'summary_uuid', 'summary_path', 'running',
    'multiple_dbs', 'foreign_collector', 'wrong_scenario', 'source_mismatch', 'no_input_reference'])
def test_inconsistent_artifacts_cannot_form_a_run_binding(artifacts, change):
    run, frozen, release, _, _ = artifacts
    summary_path = run/'summary.json'
    summary = json.loads(summary_path.read_text())
    if change == 'summary_run': summary['run_id'] = 'foreign-run'
    elif change == 'summary_uuid': summary['source_run_uuid'] = str(uuid.uuid4())
    elif change == 'summary_path': summary['runner_events_path'] = str(run.parent/'foreign-ledger')
    elif change == 'running': summary['collector_health']['is_running_after_stop'] = True
    elif change == 'multiple_dbs': (run/'collector/other.responsibility.db').write_bytes(frozen.read_bytes())
    elif change in ('foreign_collector', 'wrong_scenario'):
        with sqlite3.connect(frozen) as connection:
            connection.execute('UPDATE runs SET '+('run_id' if change=='foreign_collector' else 'scenario_id')+'=?',
                               (str(uuid.uuid4()) if change=='foreign_collector' else 'wrong-scenario',))
    elif change == 'source_mismatch':
        with sqlite3.connect(run/'collector/native.responsibility.db') as connection:
            connection.execute("UPDATE runs SET run_id='another-collector-run'")
    else:
        ledger = run/'runner-events.jsonl'
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        rows[0]['details'] = {}
        ledger.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    summary_path.write_text(json.dumps(summary))
    with pytest.raises(ValueError):
        run_artifacts.bind_run_artifacts(run, frozen, release)


def test_verifier_recomputes_instead_of_trusting_a_previously_valid_binding(artifacts):
    run, frozen, release, _, collector_id = artifacts
    row = {'run_dir': str(run), 'db_path': str(frozen), 'run_id': collector_id, 'scenario_id': 'scenario',
           'run_binding': run_artifacts.bind_run_artifacts(run, frozen, release)}
    summary = run/'summary.json'
    summary.write_text(summary.read_text()+'\n')
    with pytest.raises(ValueError): run_artifacts.validate_run_binding(row, release)


def test_release_metric_validation_requires_binding_when_release_root_is_supplied(artifacts):
    from compare.tools.dataset_release import validate_release
    from scripts.evaluation.final_verdict_metrics import validate_run_artifacts
    run, frozen, release, _, _ = artifacts
    manifest = validate_release(release)
    row = dict(run_artifacts.read_canonical_verdict(frozen), scenario_id='scenario',
               release_id=manifest['release_id'], **manifest['case_bindings']['scenario'])
    collector = run.parent/'collector-binary'
    collector.write_bytes(b'synthetic; not executed')
    runtime, digest = run_artifacts.freeze_runtime_inputs(run.parent/'empty-project', run.parent/'runtime', {})
    row.update(db_path=str(frozen), collector_binary=str(collector),
               collector_sha256=hashlib.sha256(collector.read_bytes()).hexdigest(),
               runtime_inputs=str(runtime), configuration_sha256=digest)
    with pytest.raises(ValueError, match='(?i)binding'):
        validate_run_artifacts([row], manifest, release_root=release)
    row.update(run_dir=str(run), run_binding=run_artifacts.bind_run_artifacts(run, frozen, release))
    validate_run_artifacts([row], manifest, release_root=release)


def test_multiple_new_run_directories_are_ambiguous_instead_of_selecting_latest(tmp_path):
    from compare.scripts.run.pilot_observer import release_run_dir
    for name in ('first','second'):
        directory = tmp_path/'runs'/name
        directory.mkdir(parents=True)
        (directory/'.scenario_id').write_text('scenario')
    with pytest.raises(ValueError): release_run_dir(tmp_path,'scenario',time.time())


def test_multiple_collector_databases_are_rejected_before_snapshot(tmp_path):
    from compare.scripts.run.pilot_observer import run_database
    collector = tmp_path/'collector'
    collector.mkdir()
    for name in ('first','second'): (collector/(name+'.responsibility.db')).write_bytes(b'not executed')
    with pytest.raises(ValueError): run_database(tmp_path)



def configure_pilot(tmp_path,monkeypatch,artifacts):
    from contextlib import contextmanager
    from compare.scripts.run import pilot_observer as pilot
    from mtagentlens_benchmark.engine import clean_profile
    run,frozen,release,_,_=artifacts
    frozen.unlink()
    binary=tmp_path/'synthetic-collector'
    binary.write_bytes(b'synthetic collector; never executed')
    monkeypatch.setattr(pilot,'BINARY',binary)
    monkeypatch.setattr(pilot,'release_run_dir',lambda *args:run)
    monkeypatch.setattr(pilot.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stderr=''))
    original=run_artifacts.freeze_runtime_inputs
    monkeypatch.setattr(run_artifacts,'freeze_runtime_inputs',lambda project,out,environ:
                        original(tmp_path/'empty-project',out,{}))
    @contextmanager
    def isolated():
        yield SimpleNamespace(binary='synthetic',comm='synthetic',apply=lambda env:env)
    monkeypatch.setattr(clean_profile,'isolated_agent',isolated)
    import sys
    monkeypatch.setattr(sys,'argv',['pilot','--release',str(release),'--out',str(tmp_path/'out'),
                                    '--only','scenario','--mode','process','--preflight','off'])
    return pilot,run,frozen,release


def test_real_pilot_entry_records_the_binding_for_the_attempt(tmp_path,monkeypatch,artifacts):
    pilot,run,frozen,release=configure_pilot(tmp_path,monkeypatch,artifacts)
    assert pilot.main()==0
    row=json.loads((tmp_path/'out/results.jsonl').read_text())
    assert row['run_binding']['harness_run_id']==artifacts[3].run_id
    assert row['run_id']==artifacts[4]
    assert row['run_binding']['collector_run_id']==row['run_id']
    from compare.tools.dataset_release import validate_release
    from scripts.evaluation.final_verdict_metrics import validate_run_artifacts
    validate_run_artifacts([row],validate_release(release),release_root=release)


@pytest.mark.parametrize("artifacts", ["unbound"], indirect=True)
def test_pilot_marks_unversioned_directory_as_unbound(tmp_path, monkeypatch, artifacts):
    pilot, _, _, release = configure_pilot(tmp_path, monkeypatch, artifacts)
    assert pilot.main() == 0
    row = json.loads((tmp_path/'out/results.jsonl').read_text())
    assert row['release_id'] is None
    assert row['evidence_scope'] == 'unbound-directory'
    assert row['run_binding']['release_id'] is None
    run_artifacts.validate_run_binding(row, release)

    # The synthetic collector has no health checkpoint. Supply only that
    # independent health result; retain the real run/DB/source verification.
    canonical_reader = run_artifacts.read_canonical_verdict
    monkeypatch.setattr(run_artifacts, "read_canonical_verdict", lambda path: {
        **canonical_reader(path), "capture_quality_status": "healthy"})
    from compare.scripts.run.app_baseline import validate_real_run
    from scripts.evaluation.final_campaign_harness import completed_case
    validate_real_run(row, release)
    with pytest.raises(ValueError, match="release identity"):
        validate_real_run({**row, "release_id": "fabricated-history"}, release)
    assert completed_case(tmp_path / "out", release, "scenario", row["collector_sha256"])
    row["run_binding"]["collector_run_id"] = "different-run"
    (tmp_path / "out/results.jsonl").write_text(json.dumps(row) + "\n")
    assert not completed_case(tmp_path / "out", release, "scenario", row["collector_sha256"])


def test_pilot_freezes_the_effective_isolated_identity_configuration(tmp_path, monkeypatch, artifacts):
    from contextlib import contextmanager
    from mtagentlens_benchmark.engine import clean_profile

    original_freeze = run_artifacts.freeze_runtime_inputs
    pilot, _, _, _ = configure_pilot(tmp_path, monkeypatch, artifacts)
    monkeypatch.setenv('MTAGENTLENS_AGENT_COMMS', 'before-isolation')
    vocabulary = tmp_path / 'custom.toml'
    vocabulary.write_text('authorized_roles = ["synthetic-role"]\n')
    monkeypatch.setenv('MTAGENTLENS_SCENARIO_PATTERNS', str(vocabulary))
    frozen_environments, launched_environments = [], []

    @contextmanager
    def isolated():
        yield SimpleNamespace(binary='synthetic', comm='isolated-agent',
                              apply=lambda env: dict(env, MTAGENTLENS_AGENT_COMMS='isolated-agent'))

    def freeze(project, output, environ):
        frozen_environments.append(dict(environ))
        return original_freeze(tmp_path / 'empty-project', output, environ)

    def launch(*args, **kwargs):
        if 'env' in kwargs:
            launched_environments.append(kwargs['env'])
        return SimpleNamespace(returncode=0, stderr='')

    monkeypatch.setattr(run_artifacts, 'freeze_runtime_inputs', freeze)
    monkeypatch.setattr(clean_profile, 'isolated_agent', isolated)
    monkeypatch.setattr(pilot.subprocess, 'run', launch)
    assert pilot.main() == 0
    assert frozen_environments[0]['MTAGENTLENS_AGENT_COMMS'] == 'isolated-agent'
    assert launched_environments[0]['MTAGENTLENS_AGENT_COMMS'] == frozen_environments[0]['MTAGENTLENS_AGENT_COMMS']
    row = json.loads((tmp_path / 'out/results.jsonl').read_text())
    frozen_vocabulary = Path(row['runtime_inputs']) / run_artifacts.RUNTIME_VOCABULARY_FILE
    assert Path(launched_environments[0]['MTAGENTLENS_SCENARIO_PATTERNS']) == frozen_vocabulary
    assert frozen_vocabulary.read_text() == vocabulary.read_text()


def test_real_pilot_entry_invalidates_agent_binary_mutated_during_the_run(tmp_path, monkeypatch, artifacts):
    from contextlib import contextmanager
    from mtagentlens_benchmark.engine import clean_profile

    original_freeze = run_artifacts.freeze_runtime_inputs
    pilot, _, _, _ = configure_pilot(tmp_path, monkeypatch, artifacts)
    agent_binary = tmp_path / 'synthetic-agent'
    agent_binary.write_bytes(b'synthetic agent v1')
    frozen_environments = []

    @contextmanager
    def isolated():
        yield SimpleNamespace(binary=str(agent_binary), comm='isolated-agent',
                              apply=lambda env: dict(env, MTAGENTLENS_AGENT_BINARY=str(agent_binary),
                                                     MTAGENTLENS_AGENT_COMM='isolated-agent'))

    def freeze(project, output, environ):
        frozen_environments.append(dict(environ))
        return original_freeze(tmp_path / 'empty-project', output, environ)

    def launch(*args, **kwargs):
        agent_binary.write_bytes(b'synthetic agent v2')
        return SimpleNamespace(returncode=0, stderr='')

    monkeypatch.setattr(run_artifacts, 'freeze_runtime_inputs', freeze)
    monkeypatch.setattr(clean_profile, 'isolated_agent', isolated)
    monkeypatch.setattr(pilot.subprocess, 'run', launch)
    assert pilot.main() == 1
    assert frozen_environments[0]['MTAGENTLENS_AGENT_BINARY'] == str(agent_binary)
    assert frozen_environments[0]['MTAGENTLENS_AGENT_COMM'] == 'isolated-agent'
    row = json.loads((tmp_path / 'out/results.jsonl').read_text())
    assert row['artifact_error']
    assert row['final_decision'] is None
    assert row['observed'] is False
    manifest = json.loads((Path(row['runtime_inputs']) / 'runtime.json').read_text())
    assert manifest['agent_binary_sha256'] == hashlib.sha256(b'synthetic agent v1').hexdigest()
    assert 'MTAGENTLENS_AGENT_BINARY' in manifest['settings_sha256']


def test_corrupt_database_attempt_is_retained_without_partial_snapshot(tmp_path,monkeypatch,artifacts):
    pilot,run,frozen,_=configure_pilot(tmp_path,monkeypatch,artifacts)
    (run/'collector/native.responsibility.db').write_bytes(b'not sqlite')
    assert pilot.main()==1
    row=json.loads((tmp_path/'out/results.jsonl').read_text())
    assert row['artifact_error']
    assert row['final_decision'] is None
    assert row.get('run_binding') is None
    assert not frozen.exists()


def test_binding_survives_moving_the_run_and_release_without_rewriting_source_records(tmp_path,artifacts):
    run,frozen,release,_,collector_id=artifacts
    binding=run_artifacts.bind_run_artifacts(run,frozen,release)
    moved=tmp_path/'moved-run'
    run.rename(moved)
    moved_release=tmp_path/'moved-release'
    release.rename(moved_release)
    row={'run_dir':str(moved),'db_path':str(moved/'collector/finalized.db'),
         'run_id':collector_id,'scenario_id':'scenario','run_binding':binding}
    run_artifacts.validate_run_binding(row,moved_release)


def test_source_database_symlink_is_rejected_before_copying(tmp_path):
    from compare.scripts.run.pilot_observer import run_database
    collector=tmp_path/'collector'
    collector.mkdir()
    outside=tmp_path/'outside.db'
    outside.write_bytes(b'outside source must not be followed')
    (collector/'native.responsibility.db').symlink_to(outside)
    with pytest.raises(ValueError): run_database(tmp_path)


def test_reader_checks_the_scenario_bytes_it_parses_after_release_validation(artifacts,monkeypatch):
    from tools.annotator import runner_import
    run,_,release,_,_=artifacts
    original=runner_import.validate_release
    def changed(root):
        validated=original(root)
        path=release/'scenarios/scenario.json'
        scenario=json.loads(path.read_text())
        scenario['mode']='subagents'
        path.write_text(json.dumps(scenario))
        return validated
    monkeypatch.setattr(runner_import,'validate_release',changed)
    with pytest.raises(ValueError):
        runner_import.read_runner_evidence(run/'runner-events.jsonl',release_root=release)


def test_formal_metric_cli_requires_binding_but_diagnostic_api_remains_available(tmp_path,artifacts):
    import subprocess
    import sys
    from compare.tools.dataset_release import validate_release
    run,frozen,release,_,_=artifacts
    manifest=validate_release(release)
    row=dict(run_artifacts.read_canonical_verdict(frozen),scenario_id='scenario',
             release_id=manifest['release_id'],**manifest['case_bindings']['scenario'])
    binary=tmp_path/'metric-collector'
    binary.write_bytes(b'synthetic collector; never executed')
    runtime,digest=run_artifacts.freeze_runtime_inputs(tmp_path/'empty-source',tmp_path/'metric-runtime',{})
    row.update(db_path=str(frozen),collector_binary=str(binary),
               collector_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
               runtime_inputs=str(runtime),configuration_sha256=digest,run_dir=str(run))
    records=tmp_path/'metrics.jsonl'
    records.write_text(json.dumps(row)+'\n')
    output=tmp_path/'metrics-report.json'
    project=Path(__file__).resolve().parents[2]
    command=[sys.executable,str(project/'scripts/evaluation/final_verdict_metrics.py'),
             '--benign',str(records),'--release',str(release),'--out',str(output)]
    failed=subprocess.run(command,cwd=project,text=True,capture_output=True,timeout=10)
    assert failed.returncode!=0 and 'binding' in failed.stderr.lower(),failed.stderr
    assert 'Traceback' not in failed.stderr
    assert not output.exists()
    row['run_binding']=run_artifacts.bind_run_artifacts(run,frozen,release)
    records.write_text(json.dumps(row)+'\n')
    completed=subprocess.run(command,cwd=project,text=True,capture_output=True,timeout=10)
    assert completed.returncode==0,completed.stderr
    assert json.loads(output.read_text())['paper_metrics_available'] is False


def test_failed_snapshot_cleans_only_its_temporary_output(tmp_path):
    source=tmp_path/'broken.db'
    source.write_bytes(b'not sqlite')
    before=set(tmp_path.iterdir())
    with pytest.raises(sqlite3.Error): run_artifacts.snapshot_database(source,tmp_path/'final.db')
    assert set(tmp_path.iterdir())==before
    assert source.read_bytes()==b'not sqlite'


def test_snapshot_publish_race_preserves_the_existing_destination(tmp_path,monkeypatch):
    import os
    source=tmp_path/'source.db'
    with sqlite3.connect(source) as connection:
        connection.execute('CREATE TABLE example(value TEXT)')
    destination=tmp_path/'final.db'
    original=os.link
    def raced(source,target):
        destination.write_bytes(b'concurrent existing artifact')
        return original(source,target)
    monkeypatch.setattr(os,'link',raced)
    with pytest.raises(FileExistsError): run_artifacts.snapshot_database(source,destination)
    assert destination.read_bytes()==b'concurrent existing artifact'
    assert not list(tmp_path.glob('.snapshot-*'))


def test_collector_directory_cannot_redirect_binding_outside_the_run(tmp_path,artifacts):
    run,frozen,release,_,_=artifacts
    outside=tmp_path/'foreign-collector'
    (run/'collector').rename(outside)
    (run/'collector').symlink_to(outside,target_is_directory=True)
    from compare.scripts.run.pilot_observer import run_database
    with pytest.raises(ValueError): run_database(run)
    with pytest.raises(ValueError): run_artifacts.bind_run_artifacts(run,frozen,release)


def test_pilot_statistics_escape_sqlite_uri_without_creating_a_second_database(tmp_path):
    from compare.scripts.run.pilot_observer import alert_stats
    path=tmp_path/'capture#?%.db'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE runs(run_id TEXT,final_decision TEXT,final_severity TEXT)')
        connection.execute("INSERT INTO runs VALUES('synthetic','Uncertain','Warning')")
    before=path.read_bytes()
    files=set(tmp_path.iterdir())
    stats=alert_stats(path)
    assert stats['final_decision']=='Uncertain'
    assert path.read_bytes()==before
    assert set(tmp_path.iterdir())==files


@pytest.mark.parametrize('collector_id',[None,''])
def test_closed_rows_require_a_nonempty_collector_identity(artifacts,collector_id):
    run,frozen,release,_,_=artifacts
    for path in (frozen,run/'collector/native.responsibility.db'):
        with sqlite3.connect(path) as connection:
            connection.execute('UPDATE runs SET run_id=?',(collector_id,))
    with pytest.raises(ValueError): run_artifacts.bind_run_artifacts(run,frozen,release)


@pytest.mark.parametrize('ended_at',[0,-1])
def test_closed_rows_require_a_valid_end_timestamp(artifacts,ended_at):
    run,frozen,release,_,_=artifacts
    for path in (frozen,run/'collector/native.responsibility.db'):
        with sqlite3.connect(path) as connection:
            connection.execute('UPDATE runs SET ended_at_ns=?',(ended_at,))
    with pytest.raises(ValueError): run_artifacts.bind_run_artifacts(run,frozen,release)


def test_launch_failures_have_distinct_attempt_ids_and_remain_in_diagnostic_denominator(tmp_path,monkeypatch,artifacts):
    import sys
    from scripts.evaluation.final_verdict_metrics import run_key,summarize
    pilot,_,_,_=configure_pilot(tmp_path,monkeypatch,artifacts)
    monkeypatch.setattr(pilot,'release_run_dir',lambda *args:None)
    called=[]
    def failed(command,**kwargs):
        called.append(command)
        raise OSError('synthetic launch failure')
    monkeypatch.setattr(pilot.subprocess,'run',failed)
    sys.argv+=['--only','scenario']
    assert pilot.main()==1
    rows=[json.loads(line) for line in (tmp_path/'out/results.jsonl').read_text().splitlines()]
    assert len(rows)==2
    assert len({row['attempt_id'] for row in rows})==2
    assert len({run_key(row) for row in rows})==2
    for row,command in zip(rows,called):
        expected=tmp_path/'out/attempts'/row['attempt_id']
        assert command[command.index('--out')+1]==str(expected)
        assert row['launch_error']=='OSError'
        assert row.get('run_id') is None
        assert row['observed'] is False
    summary=summarize(rows,'benign')
    assert summary['all_attempts']['runs']==summary['all_attempts']['unobserved']==2
    assert {row['attempt_id'] for row in summary['scenarios']}=={row['attempt_id'] for row in rows}


def add_collector_context(artifacts):
    run, frozen, _, _, collector_id = artifacts
    rows = [json.loads(line) for line in (run/'runner-events.jsonl').read_text().splitlines()]
    start = next(row for row in rows if row['event_type']=='process_started')
    observer = start['details']['observation']['observer']
    sample = {'schema':'mtagentlens.collector-process/v1','pid':9999,'procfs_pid':9999,
              'procfs_start_ticks':123,'nspid':[9999],'boot_id':start['process_key']['boot_id'],
              'namespaces':observer['namespaces'],'time_offsets_ns':observer['time_offsets_ns'],
              'pid_space_status':'observed'}
    context = {'schema':'mtagentlens.collector-context/v1','start':sample.copy(),'finish':sample.copy()}
    payload = {'run_id':collector_id,'contract':'frozen-evidence-reanalysis-v1',
               'capture_quality':{'recording_context':context}}
    for path in (run/'collector/native.responsibility.db', frozen):
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE analysis_checkpoints(run_id TEXT,checkpoint_kind TEXT,version INTEGER,payload_json TEXT)')
            conn.execute('INSERT INTO analysis_checkpoints VALUES(?,?,1,?)',
                         (collector_id,'frozen-analysis',json.dumps(payload)))
    return context


def replace_context(path, context):
    with sqlite3.connect(path) as conn:
        payload = json.loads(conn.execute('SELECT payload_json FROM analysis_checkpoints').fetchone()[0])
        payload['capture_quality']['recording_context'] = context
        conn.execute('UPDATE analysis_checkpoints SET payload_json=?',(json.dumps(payload),))


def test_collector_context_is_bound_and_checked_without_claiming_kernel_alignment(artifacts):
    run, frozen, release, _, _ = artifacts
    context = add_collector_context(artifacts)
    binding = run_artifacts.bind_run_artifacts(run,frozen,release)
    assert binding['schema']=='mtagentlens.run-binding/v2'
    assert binding['collector_context_sha256']==hashlib.sha256(
        json.dumps(context,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    comparison=binding['process_context_check']
    assert comparison['status']=='consistent_samples'
    assert comparison['event_alignment_available'] is False
    assert binding['event_alignment_available'] is False
    assert 'kernel_pid_mapping_unverified' in comparison['limitations']


@pytest.mark.parametrize('change',['collector_changed','boot_mismatch','time_mismatch','native_unknown','namespace_missing'])
def test_incomplete_or_mismatched_context_cannot_become_consistent_samples(artifacts,change):
    run, frozen, release, _, _ = artifacts
    context=add_collector_context(artifacts)
    if change=='collector_changed': context['finish']['procfs_start_ticks'] += 1
    elif change=='boot_mismatch':
        foreign_boot=str(uuid.uuid4())
        for sample in (context['start'],context['finish']): sample['boot_id']=foreign_boot
    elif change=='time_mismatch':
        for sample in (context['start'],context['finish']): sample['time_offsets_ns']['monotonic']=7000000000
    elif change=='namespace_missing':
        for sample in (context['start'],context['finish']): sample['namespaces']['pid']=None
    else:
        ledger=run/'runner-events.jsonl'
        rows=[json.loads(line) for line in ledger.read_text().splitlines()]
        for row in rows:
            if row['process_key'] is not None: row['process_key']['start_ticks']=None
            if row['event_type']=='process_started':
                row['details']['observation']['status']='unavailable'
                row['details']['observation']['target']=None
        ledger.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    replace_context(run/'collector/native.responsibility.db',context)
    replace_context(frozen,context)
    binding=run_artifacts.bind_run_artifacts(run,frozen,release)
    assert binding['process_context_check']['status']=='unproven'
    reason={'boot_mismatch':'boot_id_mismatch','time_mismatch':'time_offset_mismatch',
            'native_unknown':'native_process_context_unavailable'}.get(change,
                'collector_context_incomplete_or_changed')
    assert reason in binding['process_context_check']['actors'][0]['reasons']
    assert binding['event_alignment_available'] is False


@pytest.mark.parametrize('change',['bool_pid','negative_inode','bad_schema','missing_field','unknown_status','false_observed'])
def test_malformed_collector_context_is_rejected(artifacts,change):
    run,frozen,release,_,_=artifacts
    context=add_collector_context(artifacts)
    if change=='bool_pid': context['start']['pid']=True
    elif change=='negative_inode': context['start']['namespaces']['pid']['inode']=-1
    elif change=='bad_schema': context['schema']='unsupported'
    elif change=='missing_field': context['finish'].pop('time_offsets_ns')
    elif change=='unknown_status': context['start']['pid_space_status']='aligned'
    else: context['start']['nspid']=[8888,9999]
    replace_context(run/'collector/native.responsibility.db',context)
    replace_context(frozen,context)
    with pytest.raises(ValueError):run_artifacts.bind_run_artifacts(run,frozen,release)


def test_source_and_frozen_contexts_must_agree(artifacts):
    _,frozen,release,_,_=artifacts
    context=add_collector_context(artifacts)
    context['finish']['procfs_start_ticks'] += 1
    replace_context(frozen,context)
    with pytest.raises(ValueError):run_artifacts.bind_run_artifacts(artifacts[0],frozen,release)


def test_tmux_runner_evidence_binds_v4_without_promoting_a_pane_to_agent(tmp_path, monkeypatch):
    from env.tests.test_tmux_run_binding import run_mocked_tmux
    artifacts=run_mocked_tmux(tmp_path,monkeypatch,
        statuses={0:{'state':'done','exit_code':0},1:{'state':'done','exit_code':0}})
    assert artifacts['result'].success,artifacts['result'].error
    binding=run_artifacts.bind_run_artifacts(artifacts['run'],artifacts['frozen'],artifacts['release'])
    assert binding['schema']=='mtagentlens.run-binding/v4'
    assert binding['tmux']['transport']=='tmux'
    assert binding['tmux']['agent_pid_observed'] is False
    assert binding['tmux']['pane_pid_is_not_agent_pid'] is True
    assert [pane['role_file'] for pane in binding['tmux']['panes']]==['planner','reviewer']
    assert binding['event_alignment_available'] is False
    assert binding['kernel_process_alignment']['status']=='unproven'
    rows=[json.loads(line) for line in (artifacts['run']/'runner-events.jsonl').read_text().splitlines()]
    assert not any(row['event_type'] in ('process_started','process_exited') for row in rows)


@pytest.mark.parametrize('change',['pane','pane_pid','missing_context','process_shortcut','unknown_status','role'])
def test_malformed_tmux_details_cannot_shortcut_the_process_lifecycle(tmp_path,monkeypatch,change):
    from tools.annotator.runner_import import read_runner_evidence
    from env.tests.test_tmux_run_binding import run_mocked_tmux
    artifacts=run_mocked_tmux(tmp_path,monkeypatch,
        statuses={0:{'state':'done','exit_code':0},1:{'state':'done','exit_code':0}})
    ledger=artifacts['run']/'runner-events.jsonl'
    rows=[json.loads(line) for line in ledger.read_text().splitlines()]
    attempt=next(row for row in rows if row['event_type']=='launch_attempt')
    finished=next(row for row in rows if row['event_type']=='agent_finished')
    tmux=attempt['details']['tmux']
    if change=='pane': tmux['pane']='different-session:0.0'
    elif change=='pane_pid': tmux['pane_pid']=tmux['pane_pid']+1
    elif change=='missing_context': tmux['pane_process_key']=None
    elif change=='role': tmux['role_file']=42
    elif change=='unknown_status': finished['details']['status']='maybe'
    else:
        finished['event_type']='process_started'
        finished['process_key']=tmux['pane_process_key']
        finished['details']={'observation':tmux['pane_observation']}
    ledger.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    with pytest.raises(ValueError):
        read_runner_evidence(ledger,release_root=artifacts['release'])


def test_missing_pane_observation_is_bound_with_explicit_coverage_gap(tmp_path, monkeypatch):
    from env.tests.test_tmux_run_binding import run_mocked_tmux
    artifacts = run_mocked_tmux(tmp_path, monkeypatch,
        statuses={0: {'state': 'done', 'exit_code': 0}, 1: {'state': 'done', 'exit_code': 0}})
    ledger = artifacts['run'] / 'runner-events.jsonl'
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    attempt = next(row for row in rows if row['event_type'] == 'launch_attempt')
    for key in ('pane_id', 'pane_pid', 'pane_process_key', 'pane_observation'):
        attempt['details']['tmux'][key] = None
    ledger.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    binding = run_artifacts.bind_run_artifacts(artifacts['run'], artifacts['frozen'], artifacts['release'])
    assert binding['tmux']['pane_observation_complete'] is False
    assert binding['tmux']['panes_observed'] == 1
    assert binding['tmux']['panes_expected'] == 2
    assert binding['event_alignment_available'] is False
