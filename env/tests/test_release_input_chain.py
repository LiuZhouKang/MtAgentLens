"""Release source checks bind deterministic transformations and observed setup."""

import hashlib
import json
from pathlib import Path

import pytest

from mtagentlens_benchmark.engine.fixtures import materialize_fixtures
from mtagentlens_benchmark.loader.scenario_loader import load_scenario


def source_release(root, *, with_setup=True, fixture_bytes=b'read {{FIXTURE_DIR}}/input.txt\n',
                   setup_script='env/scenarios/bench/prepare.sh', task='Read {{FIXTURE_DIR}}/input.txt'):
    release = root / 'release'
    (release/'scenarios').mkdir(parents=True)
    (release/'fixtures/neutral').mkdir(parents=True)
    (release/'bench').mkdir()
    (release/'fixtures/neutral/input.txt').write_bytes(fixture_bytes)
    (release/'bench/prepare.sh').write_text('printf prepared > "$FIXTURE_DIR/setup.txt"\n')
    scenario={'id':'scenario','mode':'single_agent','description':'Synthetic source check',
              'fixture_dir':'neutral','pre_run_script':setup_script,
              'agents':[{'role_file':'role','task':task}]}
    if not with_setup:
        scenario.pop('pre_run_script')
    (release/'scenarios/scenario.json').write_text(json.dumps(scenario))
    from compare.tools.dataset_release import _inventory
    files = _inventory(release)
    manifest = {'schema':'mtagentlens.dataset-release/1','files':files,
                'case_bindings':{'scenario':{
                    'scenario_sha256':files['scenarios/scenario.json'],
                    'fixture_sha256':hashlib.sha256(json.dumps(
                        _inventory(release/'fixtures/neutral'),sort_keys=True).encode()).hexdigest()}}}
    manifest['release_id'] = hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    (release/'release.json').write_text(json.dumps(manifest))
    config=load_scenario(release/'scenarios/scenario.json',release/'fixtures')
    if with_setup:
        config.pre_run_script=str(release/'bench/prepare.sh')
    return release, manifest, config


def audit_source(tmp_path):
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    release, manifest, config = source_release(tmp_path)
    run=tmp_path/'run'
    run.mkdir()
    audit=ReleaseInputAudit(release,manifest,config,run)
    return release, manifest, config, run, audit


def test_source_materialization_and_observed_setup_are_separate_bound_stages(tmp_path):
    release, manifest, config, run, audit = audit_source(tmp_path)
    materialize_fixtures(config, run)
    audit.verify_materialized(config)
    (run/'fixtures/setup.txt').write_bytes(b'prepared')
    reference=audit.finish_setup(config)
    assert reference['schema']=='mtagentlens.execution-source/v1'
    assert reference['release_id']==manifest['release_id']
    assert reference['release_root']==str(release.resolve())
    assert reference['scenario_id']=='scenario'
    assert reference['source_files']==manifest['files']
    assert reference['source_fixture_dir']==str(release/'fixtures/neutral')
    assert reference['materialized_fixture_dir']==str(run/'fixtures')
    assert reference['pre_setup_fixture_sha256']!=reference['prepared_fixture_sha256']
    assert reference['setup']=={'path':'bench/prepare.sh',
                               'sha256':manifest['files']['bench/prepare.sh'],'exit_code':0}
    assert config.agents[0].task=='Read '+str(run/'fixtures/input.txt')


def test_unversioned_directory_records_actual_input_without_release_identity(tmp_path):
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    release, _, config = source_release(tmp_path)
    (release/'release.json').unlink()
    run = tmp_path/'run'
    run.mkdir()
    audit = ReleaseInputAudit(release, None, config, run)
    materialize_fixtures(config, run)
    audit.verify_materialized(config)
    (run/'fixtures/setup.txt').write_bytes(b'prepared')
    reference = audit.finish_setup(config)
    assert reference['release_id'] is None
    assert reference['source_files']['bench/prepare.sh'] == hashlib.sha256(
        (release/'bench/prepare.sh').read_bytes()).hexdigest()
    assert reference['setup']['sha256'] == reference['source_files']['bench/prepare.sh']


def test_unversioned_directory_detects_input_changes_during_setup(tmp_path):
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    release, _, config = source_release(tmp_path)
    (release/'release.json').unlink()
    run = tmp_path/'run'
    run.mkdir()
    audit = ReleaseInputAudit(release, None, config, run)
    materialize_fixtures(config, run)
    audit.verify_materialized(config)
    (release/'bench/prepare.sh').write_text('changed')
    with pytest.raises(ValueError, match='source inputs changed'):
        audit.finish_setup(config)


@pytest.mark.parametrize('change',['prompt','model','setup','source_bytes','fixture_bytes'])
def test_source_or_loaded_config_mismatch_is_rejected_before_copy(tmp_path, change):
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    release, manifest, config = source_release(tmp_path)
    if change=='prompt': config.agents[0].task='unbound prompt'
    elif change=='model': config.model='different model'
    elif change=='setup': config.pre_run_script='/outside/script.sh'
    elif change=='source_bytes': (release/'scenarios/scenario.json').write_text('{}')
    else: (release/'fixtures/neutral/input.txt').write_text('unbound bytes')
    with pytest.raises(ValueError):
        ReleaseInputAudit(release,manifest,config,tmp_path/'run')


@pytest.mark.parametrize('change',['bytes','extra','prompt'])
def test_materialized_copy_must_match_the_declared_path_rewrite(tmp_path, change):
    _, _, config, run, audit = audit_source(tmp_path)
    materialize_fixtures(config,run)
    if change=='bytes': (run/'fixtures/input.txt').write_text('wrong copy')
    elif change=='extra': (run/'fixtures/extra').write_text('unbound extra')
    else: config.agents[0].task='wrong transformed prompt'
    with pytest.raises(ValueError): audit.verify_materialized(config)


def test_setup_cannot_silently_change_its_frozen_source_script(tmp_path):
    release, _, config, run, audit = audit_source(tmp_path)
    materialize_fixtures(config,run)
    audit.verify_materialized(config)
    (release/'bench/prepare.sh').write_text('different script')
    with pytest.raises(ValueError): audit.finish_setup(config)


def configure_client(tmp_path, monkeypatch):
    import sys
    from mtagentlens_benchmark.engine import executor
    client=tmp_path/'client'
    client.write_text(f'#!{sys.executable}\nprint("synthetic local client")\n')
    client.chmod(0o700)
    monkeypatch.setattr(executor,'_find_claude_binary_with_diagnostics',lambda:(str(client),{}))
    monkeypatch.setattr(executor,'_apply_benchmark_env',lambda *args:None)
    monkeypatch.setattr(executor,'_capture_agent_artifacts',lambda *args:None)
    monkeypatch.setattr(executor.os,'getuid',lambda:1000)
    monkeypatch.delenv('MTAGENTLENS_CLEAN_PROFILE',raising=False)
    monkeypatch.setattr(executor,'Path',lambda value:tmp_path/'pid'
                        if value=='/tmp/mtagentlens-agent-pid' else Path(value))
    return executor


def test_actual_release_execution_records_source_chain_and_import_rechecks_it(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    from tools.annotator.runner_import import import_runner_bundle
    release,manifest,config=source_release(tmp_path)
    executor=configure_client(tmp_path,monkeypatch)
    run=tmp_path/'run'
    result=executor.execute_scenario(config,run,SimpleNamespace(root=tmp_path),
        input_audit_factory=lambda conf,directory:ReleaseInputAudit(release,manifest,conf,directory))
    assert result.success,result.error
    ledger=run/'runner-events.jsonl'
    rows=[json.loads(line) for line in ledger.read_text().splitlines()]
    reference=rows[0]['details']['input_reference']
    assert reference['release_id']==manifest['release_id']
    assert reference['setup']['exit_code']==0
    imported=import_runner_bundle(tmp_path/'bundle',release_root=release,ledger=ledger,codebook_version='pilot/1')
    assert imported['binding']['run_id']==result.run_id
    # A wrong declaration with valid JSON and unchanged raw inputs must fail before output.
    rows[0]['details']['input_reference']['prepared_fixture_sha256']='f'*64
    ledger.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    with pytest.raises(ValueError):
        import_runner_bundle(tmp_path/'wrong',release_root=release,ledger=ledger,codebook_version='pilot/1')
    assert not (tmp_path/'wrong').exists()


@pytest.mark.parametrize('change',['source','materialization'])
def test_mismatched_release_inputs_stop_before_setup_or_client_launch(tmp_path,monkeypatch,change):
    from types import SimpleNamespace
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    from mtagentlens_benchmark.engine import fixtures
    release,manifest,config=source_release(tmp_path)
    executor=configure_client(tmp_path,monkeypatch)
    if change=='source':
        config.agents[0].task='unbound prompt'
    else:
        original=fixtures.materialize_fixtures
        def changed(conf,directory):
            copied=original(conf,directory)
            (directory/'fixtures/input.txt').write_text('wrong materialization')
            return copied
        monkeypatch.setattr(fixtures,'materialize_fixtures',changed)
    monkeypatch.setattr(executor.subprocess,'Popen',lambda *args,**kwargs:pytest.fail('must stop before setup/client'))
    result=executor.execute_scenario(config,tmp_path/'run',SimpleNamespace(root=tmp_path),
        input_audit_factory=lambda conf,directory:ReleaseInputAudit(release,manifest,conf,directory))
    assert not result.success
    assert result.summary['input_validation']['status']=='failed'
    assert not (tmp_path/'run/runner-events.jsonl').exists()



def test_no_setup_does_not_allow_unexplained_fixture_changes(tmp_path):
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    release,manifest,config=source_release(tmp_path,with_setup=False)
    run=tmp_path/'run'
    run.mkdir()
    audit=ReleaseInputAudit(release,manifest,config,run)
    materialize_fixtures(config,run)
    audit.verify_materialized(config)
    (run/'fixtures/input.txt').write_text('unexplained change')
    with pytest.raises(ValueError): audit.finish_setup(config)


def test_audited_output_cannot_be_inside_the_immutable_release(tmp_path):
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    release,manifest,config=source_release(tmp_path)
    with pytest.raises(ValueError):
        ReleaseInputAudit(release,manifest,config,release/'new-run')


@pytest.fixture
def executed_chain(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    release,manifest,config=source_release(tmp_path)
    executor=configure_client(tmp_path,monkeypatch)
    run=tmp_path/'run'
    result=executor.execute_scenario(config,run,SimpleNamespace(root=tmp_path),
        input_audit_factory=lambda conf,directory:ReleaseInputAudit(release,manifest,conf,directory))
    assert result.success,result.error
    ledger=run/'runner-events.jsonl'
    rows=[json.loads(line) for line in ledger.read_text().splitlines()]
    return release,ledger,rows


@pytest.mark.parametrize('change',['release','source_files','source_path','pre_setup','prepared',
                                  'prompt','model','cwd','missing_launch','permission'])
def test_import_rechecks_source_chain_instead_of_trusting_declarations(tmp_path,executed_chain,change):
    from tools.annotator.runner_import import import_runner_bundle
    release,ledger,rows=executed_chain
    reference=rows[0]['details']['input_reference']
    details=rows[1]['details']
    snapshot=details['input_snapshot']
    if change=='release': reference['release_id']='f'*64
    elif change=='source_files': reference['source_files']={}
    elif change=='source_path': reference['source_fixture_dir']='/outside'
    elif change=='pre_setup': reference['pre_setup_fixture_sha256']='f'*64
    elif change=='prepared': reference['prepared_fixture_sha256']='f'*64
    elif change=='prompt': snapshot['prompt']=snapshot['claude_args'][1]='wrong but internally consistent'
    elif change=='model': snapshot['claude_args'][snapshot['claude_args'].index('--model')+1]='wrong model'
    elif change=='cwd': snapshot['cwd']='/outside'
    elif change=='missing_launch': rows=[rows[0],rows[-1]]
    else: snapshot['claude_args']+=['--permission-mode','bypassPermissions']
    details['input_sha256']=hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False,
                                                    separators=(',',':')).encode()).hexdigest()
    ledger.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    with pytest.raises(ValueError):
        import_runner_bundle(tmp_path/'invalid',release_root=release,ledger=ledger,codebook_version='pilot/1')
    assert not (tmp_path/'invalid').exists()


def test_release_source_verification_remains_portable_after_moving_the_release(tmp_path,executed_chain):
    from tools.annotator.runner_import import import_runner_bundle
    release,ledger,_=executed_chain
    moved=tmp_path/'moved-release'
    release.rename(moved)
    imported=import_runner_bundle(tmp_path/'portable',release_root=moved,ledger=ledger,codebook_version='pilot/1')
    assert imported['binding']['scenario_id']=='scenario'


def test_input_changes_between_setup_and_first_launch_stop_the_client(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    release,manifest,config=source_release(tmp_path)
    executor=configure_client(tmp_path,monkeypatch)
    original=executor.capture_launch_inputs
    def changed(*args,**kwargs):
        (tmp_path/'run/fixtures/input.txt').write_text('changed after setup')
        monkeypatch.setattr(executor.subprocess,'Popen',lambda *args,**kwargs:pytest.fail('must not launch'))
        return original(*args,**kwargs)
    monkeypatch.setattr(executor,'capture_launch_inputs',changed)
    result=executor.execute_scenario(config,tmp_path/'run',SimpleNamespace(root=tmp_path),
        input_audit_factory=lambda conf,directory:ReleaseInputAudit(release,manifest,conf,directory))
    assert not result.success
    assert 'prepared fixture changed' in result.error
    rows=[json.loads(line) for line in (tmp_path/'run/runner-events.jsonl').read_text().splitlines()]
    assert [row['event_type'] for row in rows]==['run_started','run_finished']
    assert rows[-1]['details']['state']=='interrupted'


def test_interrupted_scope_still_requires_well_formed_prepared_input_digest(tmp_path,executed_chain):
    from tools.annotator.runner_import import import_runner_bundle
    release,ledger,rows=executed_chain
    rows=[rows[0],rows[-1]]
    rows[-1]['details']['state']='interrupted'
    rows[0]['details']['input_reference']['prepared_fixture_sha256']=42
    ledger.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    with pytest.raises(ValueError):
        import_runner_bundle(tmp_path/'invalid',release_root=release,ledger=ledger,codebook_version='pilot/1')
    assert not (tmp_path/'invalid').exists()



@pytest.mark.parametrize('payload',[b'path={{FIXTURE_DIR}}\r\nsecond line\r\n',
                                    b'no replacement\r\n',b'\x00\xff{{FIXTURE_DIR}}'])
def test_source_transformation_preserves_binary_and_existing_newline_rules(tmp_path,payload):
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    release,manifest,config=source_release(tmp_path,with_setup=False,fixture_bytes=payload)
    run=tmp_path/'run'
    run.mkdir()
    audit=ReleaseInputAudit(release,manifest,config,run)
    materialize_fixtures(config,run)
    audit.verify_materialized(config)
    reference=audit.finish_setup(config)
    assert reference['pre_setup_fixture_sha256']==reference['prepared_fixture_sha256']


@pytest.mark.parametrize('parallel',[1,2])
def test_scheduler_forwards_source_audit_in_sequential_and_parallel_paths(tmp_path,monkeypatch,parallel):
    from types import SimpleNamespace
    from mtagentlens_benchmark.engine import runner
    from mtagentlens_benchmark.types import RunResult
    factory=lambda config,directory:None
    seen=[]
    def executed(config,directory,*args,**kwargs):
        assert kwargs['input_audit_factory'] is factory
        seen.append(config.id)
        directory.mkdir(parents=True)
        return RunResult(run_id='synthetic-run',scenario_id=config.id,success=True,duration_seconds=0)
    monkeypatch.setattr(runner,'execute_scenario',executed)
    results=runner.run_scenarios([SimpleNamespace(id='scenario')],SimpleNamespace(runs_dir=tmp_path/'runs'),
                                 parallel=parallel,input_audit_factory=factory)
    assert seen==['scenario']
    assert results[0].success


def test_real_release_child_installs_the_source_audit_factory(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from compare.scripts.run import pilot_observer
    from mtagentlens_benchmark.engine import runner
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    release,_,_=source_release(tmp_path)
    collector=tmp_path/'collector'
    collector.write_bytes(b'synthetic collector; never executed')
    seen=[]
    def scheduled(configs,workspace,**kwargs):
        assert len(configs)==1
        audit=kwargs['input_audit_factory'](configs[0],tmp_path/'out/run')
        assert isinstance(audit,ReleaseInputAudit)
        seen.append(configs[0].id)
        return [SimpleNamespace(success=True)]
    monkeypatch.setattr(runner,'run_scenarios',scheduled)
    args=SimpleNamespace(collector_binary=collector,collector_sha256=hashlib.sha256(collector.read_bytes()).hexdigest(),
                         release=release,only=['scenario'],out=tmp_path/'out',mode='process',observer='off')
    assert pilot_observer.execute_release_scenario(args)==0
    assert seen==['scenario']


def test_real_unversioned_child_installs_unbound_source_audit(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from compare.scripts.run import pilot_observer
    from mtagentlens_benchmark.engine import runner
    release, _, _ = source_release(tmp_path)
    (release/'release.json').unlink()
    collector = tmp_path/'collector'
    collector.write_bytes(b'synthetic collector; never executed')
    def scheduled(configs, workspace, **kwargs):
        audit = kwargs['input_audit_factory'](configs[0], tmp_path/'out/run')
        assert audit.manifest is None
        return [SimpleNamespace(success=True)]
    monkeypatch.setattr(runner, 'run_scenarios', scheduled)
    args = SimpleNamespace(collector_binary=collector, collector_sha256=hashlib.sha256(collector.read_bytes()).hexdigest(),
                           release=release, only=['scenario'], out=tmp_path/'out', mode='process', observer='off')
    assert pilot_observer.execute_release_scenario(args) == 0


def test_unknown_release_schema_is_not_interpreted_as_current_input_format(tmp_path):
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    release,manifest,config=source_release(tmp_path)
    manifest['schema']='unknown/future'
    manifest['release_id']=hashlib.sha256(json.dumps({key:value for key,value in manifest.items()
                                                   if key!='release_id'},sort_keys=True).encode()).hexdigest()
    with pytest.raises(ValueError): ReleaseInputAudit(release,manifest,config,tmp_path/'run')


def test_setup_exit_code_must_be_an_integer_not_a_boolean(tmp_path,executed_chain):
    from tools.annotator.runner_import import import_runner_bundle
    release,ledger,rows=executed_chain
    rows[0]['details']['input_reference']['setup']['exit_code']=False
    ledger.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    with pytest.raises(ValueError):
        import_runner_bundle(tmp_path/'invalid',release_root=release,ledger=ledger,codebook_version='pilot/1')


def test_source_reference_does_not_copy_ground_truth_or_release_labels(tmp_path):
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    release,manifest,config=source_release(tmp_path)
    gold=release/'ground_truth/secret.json'
    gold.parent.mkdir()
    gold.write_text('{"label":"synthetic-gold-secret"}')
    manifest['files']['ground_truth/secret.json']=hashlib.sha256(gold.read_bytes()).hexdigest()
    manifest['release_id']=hashlib.sha256(json.dumps({key:value for key,value in manifest.items()
                                                   if key!='release_id'},sort_keys=True).encode()).hexdigest()
    (release/'release.json').write_text(json.dumps(manifest))
    manifest['case_labels']={'scenario':'synthetic-gold-secret'}
    run=tmp_path/'run'
    run.mkdir()
    audit=ReleaseInputAudit(release,manifest,config,run)
    materialize_fixtures(config,run)
    audit.verify_materialized(config)
    reference=audit.finish_setup(config)
    assert 'ground_truth/secret.json' not in reference['source_files']
    assert 'synthetic-gold-secret' not in json.dumps(reference)



@pytest.mark.parametrize('script',['./bench/prepare.sh','bench/../bench/prepare.sh'])
def test_source_setup_path_aliases_use_the_same_release_script(tmp_path,monkeypatch,script):
    from types import SimpleNamespace
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    from tools.annotator.runner_import import import_runner_bundle
    release,manifest,config=source_release(tmp_path,setup_script=script)
    executor=configure_client(tmp_path,monkeypatch)
    run=tmp_path/'run'
    result=executor.execute_scenario(config,run,SimpleNamespace(root=tmp_path),
        input_audit_factory=lambda conf,directory:ReleaseInputAudit(release,manifest,conf,directory))
    assert result.success,result.error
    imported=import_runner_bundle(tmp_path/'bundle',release_root=release,
                                  ledger=run/'runner-events.jsonl',codebook_version='pilot/1')
    assert imported['binding']['run_id']==result.run_id


def test_source_declared_setup_must_remain_release_relative(tmp_path):
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    script=str(tmp_path/'release/bench/prepare.sh')
    release,manifest,config=source_release(tmp_path,setup_script=script)
    with pytest.raises(ValueError): ReleaseInputAudit(release,manifest,config,tmp_path/'run')



@pytest.mark.parametrize('prompt',['--model','--effort','--permission-mode'])
def test_literal_prompt_is_not_reinterpreted_as_a_client_option(tmp_path,monkeypatch,prompt):
    from types import SimpleNamespace
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    from tools.annotator.runner_import import import_runner_bundle
    release,manifest,config=source_release(tmp_path,task=prompt)
    executor=configure_client(tmp_path,monkeypatch)
    run=tmp_path/'run'
    result=executor.execute_scenario(config,run,SimpleNamespace(root=tmp_path),
        input_audit_factory=lambda conf,directory:ReleaseInputAudit(release,manifest,conf,directory))
    assert result.success,result.error
    imported=import_runner_bundle(tmp_path/'bundle',release_root=release,
                                  ledger=run/'runner-events.jsonl',codebook_version='pilot/1')
    assert imported['binding']['run_id']==result.run_id
