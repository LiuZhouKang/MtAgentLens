# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Final-window wiring contracts; tests never call a paid client."""
import json
import hashlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.evaluation import final_campaign_harness as h

ROOT = Path(__file__).resolve().parents[2]


def test_status_uses_current_scored_multi_agent_cohort():
    proc = subprocess.run([sys.executable, str(Path(h.__file__)), 'status'], cwd=ROOT,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert Path(payload['release']) == ROOT / 'datasets/FuxiBench'
    assert (payload['multi_agent'], payload['attack'], payload['benign']) == (30, 22, 8)
    assert payload['attack_types'] == {'A': 11, 'B': 11}
    assert payload['detection_excluded'] == []
    assert 'ready' not in str(payload['harnesses']).lower()


def test_run_one_requires_explicit_paid_execution(tmp_path, capsys):
    args = SimpleNamespace(
        scenario='benign-api-doc', execute=False, out=tmp_path,
        release=ROOT / 'datasets/FuxiBench', force=False, timeout=900,
    )
    assert h.cmd_run_one(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload['status'] == 'blocked'
    assert payload['scenario_id'] == 'benign-api-doc'


def test_empty_ledger_is_never_complete(tmp_path):
    target = tmp_path / 'run'
    target.mkdir()
    (target / 'results.jsonl').touch()
    assert not h.completed_case(target, tmp_path, 'case', 'collector-hash')


def test_resume_requires_current_binary_and_verified_artifacts(tmp_path, monkeypatch):
    from compare.scripts.run import app_baseline
    from scripts.evaluation import run_artifacts
    target = tmp_path / 'run'
    target.mkdir()
    binary = tmp_path / 'collector'
    binary.write_bytes(b'current binary')
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    row = {'scenario_id': 'case', 'collector_sha256': 'old', 'state': 'completed',
           'collector_binary': str(binary), 'runtime_inputs': str(tmp_path),
           'configuration_sha256': 'config'}
    (target / 'results.jsonl').write_text(json.dumps(row) + '\n')
    validated = []
    monkeypatch.setattr(app_baseline, 'validate_real_run', lambda r, release: validated.append(r))
    monkeypatch.setattr(run_artifacts, 'validate_runtime_inputs', lambda *a: None)
    assert not h.completed_case(target, tmp_path, 'case', digest)
    row['collector_sha256'] = digest
    (target / 'results.jsonl').write_text(json.dumps(row) + '\n')
    assert h.completed_case(target, tmp_path, 'case', digest)
    assert validated == [row]
    monkeypatch.setattr(app_baseline, 'validate_real_run', lambda *a: (_ for _ in ()).throw(ValueError('corrupt')))
    assert not h.completed_case(target, tmp_path, 'case', digest)


def test_resume_with_strict_replay_rejects_invalid_recording(tmp_path, monkeypatch):
    from compare.scripts.run import app_baseline
    from scripts.evaluation import run_artifacts
    target = tmp_path / 'run'
    target.mkdir()
    binary = tmp_path / 'collector'
    binary.write_bytes(b'current binary')
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    row = {'scenario_id': 'case', 'collector_sha256': digest, 'state': 'completed',
           'collector_binary': str(binary), 'runtime_inputs': str(tmp_path),
           'configuration_sha256': 'config', 'db_path': str(tmp_path / 'frozen.db')}
    (target / 'results.jsonl').write_text(json.dumps(row) + '\n')
    monkeypatch.setattr(app_baseline, 'validate_real_run', lambda *a: None)
    monkeypatch.setattr(run_artifacts, 'validate_runtime_inputs', lambda *a: None)
    replay = tmp_path / 'replay'
    replay.write_bytes(b'replay')
    monkeypatch.setattr(h.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps({
            'schema': 'mtagentlens.recording-validation/v1', 'status': 'invalid',
            'read_only': True, 'failures': ['bad checkpoint']}), stderr=''))
    assert not h.completed_case(target, tmp_path, 'case', digest, replay_binary=replay)
    monkeypatch.setattr(h.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps({
            'schema': 'mtagentlens.recording-validation/v1', 'status': 'ok',
            'read_only': True, 'failures': []}), stderr=''))
    assert h.completed_case(target, tmp_path, 'case', digest, replay_binary=replay)


def test_e8_plan_emits_three_explicit_arms_per_selected_run(tmp_path, monkeypatch):
    from compare.scripts.run import app_baseline
    from compare.rjudge.record import ARM_FULL_TRACE, ARM_NO_TOOL_RESULT, ARM_PROMPT_ONLY
    from compare.tests.test_rjudge_trace_export import standard_run
    rows = [{'scenario_id': sid, 'run_dir': str(standard_run(tmp_path, name=sid))}
            for sid in ('one', 'two', 'excluded')]
    ledger = tmp_path / 'results.jsonl'
    ledger.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    monkeypatch.setattr(h, '_validated_release', lambda release: {'release_id': 'r'})
    monkeypatch.setattr(h, '_release_gt', lambda release: {
        'one': {'label': 'attack'}, 'two': {'label': 'benign'},
        'excluded': {'quality': {'detection_excluded': True}}})
    validated = []
    monkeypatch.setattr(app_baseline, 'validate_real_run', lambda r, release: validated.append(r))
    plan = h.e8_plan(ledger, tmp_path, tmp_path / 'out', limit=1)
    assert not plan['problems']
    assert len(validated) == 1  # --limit counts selected runs, not arms
    assert len(plan['commands']) == 3
    assert plan['detection_excluded'] == ['excluded']
    arms = [command[command.index('--arm') + 1] for command in plan['commands']]
    assert arms == [ARM_PROMPT_ONLY, ARM_FULL_TRACE, ARM_NO_TOOL_RESULT]
    inputs = {command[command.index('--results') + 1] for command in plan['commands']}
    outs = [command[command.index('--out') + 1] for command in plan['commands']]
    assert len(inputs) == 1 and len(set(outs)) == 3
    assert all(arm in out for arm, out in zip(arms, outs))
    for command in plan['commands']:
        assert command[command.index('--execution-mode') + 1] == 'real'
        assert command[command.index('--client') + 1] == 'anthropic'
        assert command[command.index('--limit') + 1] == '1'
        selected = Path(command[command.index('--results') + 1])
        assert len(selected.read_text().splitlines()) == 1
    assert plan['budget'] == {
        'selected_runs': 1, 'arms_per_run': 3,
        'arm_counts': {ARM_PROMPT_ONLY: 1, ARM_FULL_TRACE: 1, ARM_NO_TOOL_RESULT: 1},
        'record_count': 3, 'commands': 3, 'model_calls_per_command': 2,
        'max_model_calls': 6,
        'basis': 'one two-step judge per command: same reason -> label; not two independent judges',
    }


def test_e8_partial_export_failure_is_explicit_and_unpaid(tmp_path, monkeypatch):
    from compare.scripts.run import app_baseline
    from compare.rjudge.record import ARM_FULL_TRACE, ARM_NO_TOOL_RESULT, ARM_PROMPT_ONLY
    from compare.tests.test_rjudge_trace_export import standard_run
    ledger = tmp_path / 'results.jsonl'
    ledger.write_text(json.dumps({'scenario_id': 'one',
                                  'run_dir': str(standard_run(tmp_path, name='one'))}) + '\n')
    monkeypatch.setattr(h, '_validated_release', lambda release: {'release_id': 'r'})
    monkeypatch.setattr(h, '_release_gt', lambda release: {'one': {'label': 'attack'}})
    monkeypatch.setattr(app_baseline, 'validate_real_run', lambda *a: None)

    def fake_main(argv):
        argv = list(argv)
        arm = argv[argv.index('--arm') + 1]
        if arm == ARM_NO_TOOL_RESULT:
            return 1
        out = Path(argv[argv.index('--out') + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / 'summary.json').write_text(json.dumps({'records': 1, 'failures': []}))
        return 2

    monkeypatch.setattr(app_baseline, 'main', fake_main)
    plan = h.e8_plan(ledger, tmp_path, tmp_path / 'out', limit=None)
    arms = [command[command.index('--arm') + 1] for command in plan['commands']]
    assert arms == [ARM_PROMPT_ONLY, ARM_FULL_TRACE]
    assert plan['budget']['record_count'] == 2
    assert plan['budget']['arm_counts'] == {ARM_PROMPT_ONLY: 1, ARM_FULL_TRACE: 1,
                                            ARM_NO_TOOL_RESULT: 0}
    assert plan['budget']['max_model_calls'] == 4
    assert len(plan['failures']) == 1
    assert plan['failures'][0]['scenario_id'] == 'one'
    assert plan['failures'][0]['arm'] == ARM_NO_TOOL_RESULT
    assert plan['problems']


def test_e8_dry_run_calls_actual_exporter_and_rejects_missing_trace(tmp_path, monkeypatch):
    from compare.scripts.run import app_baseline
    ledger = tmp_path / 'results.jsonl'
    ledger.write_text(json.dumps({'scenario_id': 'one', 'run_dir': str(tmp_path)}) + '\n')
    monkeypatch.setattr(h, '_validated_release', lambda release: {'release_id': 'r'})
    monkeypatch.setattr(h, '_release_gt', lambda release: {'one': {'label': 'attack'}})
    monkeypatch.setattr(app_baseline, 'validate_real_run', lambda *a: None)
    plan = h.e8_plan(ledger, tmp_path, tmp_path / 'out', limit=None)
    assert plan['problems']  # a directory alone cannot establish a usable trace


def test_runtime_prepare_requires_explicit_nonempty_source(tmp_path):
    from tools.annotator.ai_runtime import prepare
    run_root = tmp_path / 'runs'
    run_root.mkdir()
    with pytest.raises(ValueError, match='runner-events'):
        prepare(tmp_path / 'runtime', run_root=run_root)


def test_runtime_stages_include_compaction_sanitization_and_independent_adjudication(tmp_path):
    commands = h.runtime_commands(tmp_path / 'runtime', tmp_path / 'runs')
    stages = [c[2] for c in commands]
    assert stages == ['prepare', 'compact', 'sanitize', 'review', 'finalize', 'adjudicate', 'finalize', 'verify']
    assert '--run-root' in commands[0]
    assert str(tmp_path / 'runs') in commands[0]
    assert all('--reviewers' in c and c[c.index('--reviewers') + 1] == '3' for c in commands)


def test_e9_uses_build_path_and_discloses_microbenchmark_scope():
    plan = h.e9_plan(10)
    assert plan['perf_binary'] == str(ROOT / 'collector/build/release/perf_throughput')
    assert plan['scope'] == 'synthetic_parser_microbenchmark'
    assert plan['full_system_e9_available'] is False
    assert 'ONNX' not in plan['limitation']
    assert 'ONNX' not in plan['system_load_tool']


def test_e9_reads_actual_event_counts_and_sums_concurrent_rss():
    stats = [
        {'total_events': 4, 'raw_events': 2, 'full_pipeline': {'events_per_sec': 3.5}},
        {'total_events': 5, 'raw_events': 3, 'full_pipeline': {'events_per_sec': 4.5}},
    ]
    result = h.summarize_synthetic(stats, [0, 0], elapsed=2, peak_rss=300)
    assert result['observed_events'] == 14
    assert result['aggregate_events_per_second'] == 7
    assert result['sampled_peak_rss_kib_sum'] == 300
    assert result['per_process'] == stats


def test_paid_execution_requires_guard_before_running(tmp_path, monkeypatch):
    monkeypatch.delenv('MTL_ALLOW_PAID', raising=False)
    args = SimpleNamespace(execute=True, release=ROOT / 'datasets/FuxiBench', out=tmp_path,
                           timeout=10, force=False)
    with pytest.raises(SystemExit, match='MTL_ALLOW_PAID'):
        h.cmd_type_c(args)


def test_prepare_scans_only_explicit_campaign_and_rejects_stale_cache(tmp_path):
    from tools.annotator.ai_runtime import prepare
    run_root = tmp_path / 'campaign'
    run_root.mkdir()
    ledger = run_root / 'runner-events.jsonl'
    ledger.write_text('{invalid json}\n')
    result = prepare(tmp_path / 'runtime', run_root=run_root)
    assert len(result['records']) == 1
    assert Path(result['records'][0]['ledger']) == ledger
    assert result['records'][0]['status'] == 'excluded_invalid_or_missing_evidence'
    ledger.write_text('{changed invalid json}\n')
    with pytest.raises(ValueError, match='source changed'):
        prepare(tmp_path / 'runtime', run_root=run_root)


def test_e9_system_dry_run_uses_real_zero_model_tool(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(h.subprocess, 'run', lambda command, **kw: called.append(command) or SimpleNamespace(returncode=0))
    args = SimpleNamespace(out=tmp_path, execute=False, operations=20, interval_ms=100,
                           concurrency='1,4,8,16,32', binary=ROOT / 'collector/build/release/mtagentlens')
    assert h.cmd_e9_system(args) == 0
    assert called[0][1] == str(ROOT / 'scripts/evaluation/system_load.py')
    assert '--execute' not in called[0]
    args.execute = True
    assert h.cmd_e9_system(args) == 0
    assert '--execute' in called[1]


def test_empty_runtime_ledger_is_reported_as_invalid(tmp_path):
    from tools.annotator.ai_runtime import prepare
    source = tmp_path / 'runs'
    source.mkdir()
    (source / 'runner-events.jsonl').touch()
    result = prepare(tmp_path / 'runtime', run_root=source)
    assert result['records'][0]['status'] == 'excluded_invalid_or_missing_evidence'


def test_resume_rejects_missing_frozen_execution_inputs(tmp_path, monkeypatch):
    from compare.scripts.run import app_baseline
    target = tmp_path / 'run'
    target.mkdir()
    row = {'scenario_id': 'case', 'collector_sha256': 'hash', 'state': 'completed'}
    (target / 'results.jsonl').write_text(json.dumps(row) + '\n')
    monkeypatch.setattr(app_baseline, 'validate_real_run', lambda *a: None)
    assert not h.completed_case(target, tmp_path, 'case', 'hash')


def test_e8_rejects_duplicate_scenario_runs_before_limit(tmp_path, monkeypatch):
    from compare.scripts.run import app_baseline
    ledger = tmp_path / 'duplicate.jsonl'
    ledger.write_text(''.join(json.dumps({'scenario_id': 'one', 'run_dir': name}) + '\n'
                              for name in ('earlier', 'later')))
    monkeypatch.setattr(h, '_validated_release', lambda release: {'release_id': 'r'})
    monkeypatch.setattr(h, '_release_gt', lambda release: {'one': {'label': 'attack'}})
    monkeypatch.setattr(app_baseline, 'validate_real_run', lambda *args: None)
    def forbidden(*args, **kwargs):
        raise AssertionError('duplicates must be rejected before transcript export')
    monkeypatch.setattr(app_baseline, 'main', forbidden)
    plan = h.e8_plan(ledger, tmp_path, tmp_path / 'out', limit=1)
    assert any('duplicate' in problem for problem in plan['problems'])
    assert not plan['commands']
    assert plan['budget']['max_model_calls'] == 0


def _campaign_fixture(tmp_path):
    """Four inputs: included single attack/benign, included multi, excluded single."""
    release = tmp_path / 'release'
    (release / 'ground_truth').mkdir(parents=True)
    (release / 'scenarios').mkdir()
    rows = {
        'single-attack': ('attack', 'A', False, 1),
        'single-benign': ('benign', 'benign', False, 1),
        'multi-attack': ('attack', 'B', False, 2),
        'excluded-case': ('benign', 'benign', True, 1),
    }
    for sid, (label, attack_type, excluded, agents) in rows.items():
        gt = {'scenario_id': sid, 'label': label, 'attack_type': attack_type}
        if excluded:
            gt['quality'] = {'detection_excluded': True}
        (release / 'ground_truth' / f'{sid}.json').write_text(json.dumps(gt))
        scenario = {'id': sid, 'agents': [{'task': 'x'} for _ in range(agents)]}
        (release / 'scenarios' / f'{sid}.json').write_text(json.dumps(scenario))
    return release


def _patch_fixture_checks(monkeypatch, release):
    from mtagentlens_benchmark.loader import scenario_loader
    monkeypatch.setattr(h, 'FROZEN_SCENARIO_COUNT', 4)
    monkeypatch.setattr(h, '_validated_release', lambda path: {'release_id': 'fixture'})
    configs = [SimpleNamespace(id=path.stem, pre_run_script=None)
               for path in sorted((release / 'scenarios').glob('*.json'))]
    monkeypatch.setattr(scenario_loader, 'load_all_scenarios', lambda *a, **k: configs)


def test_campaign_plan_fixture_derives_all_input_target_and_excluded(tmp_path, monkeypatch):
    release = _campaign_fixture(tmp_path)
    _patch_fixture_checks(monkeypatch, release)
    plan = h.campaign_plan(release, tmp_path / 'out', cohort='campaign')
    assert not plan['problems']
    assert plan['cohort'] == 'campaign'
    assert plan['input_count'] == 4
    assert plan['target'] == 3
    assert plan['target_count'] == 3
    assert plan['total'] == 3
    assert plan['excluded_ids'] == ['excluded-case']
    assert plan['detection_excluded'] == ['excluded-case']
    assert sorted(c['scenario_id'] for c in plan['scenarios']) == [
        'multi-attack', 'single-attack', 'single-benign']
    assert plan['attack'] == 2 and plan['benign'] == 1


def test_moving_a_release_does_not_bypass_the_frozen_input_count(tmp_path, monkeypatch):
    release = _campaign_fixture(tmp_path)
    _patch_fixture_checks(monkeypatch, release)
    monkeypatch.setattr(h, 'FROZEN_SCENARIO_COUNT', 93, raising=False)
    plan = h.campaign_plan(release, tmp_path / 'out', cohort='campaign')
    assert any('frozen 93-scenario' in problem for problem in plan['problems'])


def test_status_fails_when_only_the_single_agent_cohort_has_invalid_inputs(tmp_path, monkeypatch):
    release = _campaign_fixture(tmp_path)
    _patch_fixture_checks(monkeypatch, release)
    path = release / 'ground_truth/single-attack.json'
    record = json.loads(path.read_text())
    record['attack_type'] = 'unsupported'
    path.write_text(json.dumps(record))
    assert h.cmd_status(SimpleNamespace(release=release, out=tmp_path / 'out')) == 1


def test_legacy_multi_agent_plan_still_filters_single_agent(tmp_path, monkeypatch):
    release = _campaign_fixture(tmp_path)
    _patch_fixture_checks(monkeypatch, release)
    plan = h.campaign_plan(release, tmp_path / 'out')
    assert plan['cohort'] == 'multi-agent'
    assert plan['input_count'] == 1
    assert plan['target'] == 1
    assert [c['scenario_id'] for c in plan['scenarios']] == ['multi-attack']
    assert plan['multi_agent_before_exclusions'] == 1
    assert h.multi_agent_cases(release) == plan['scenarios']


def test_campaign_real_release_adds_every_included_single_agent(tmp_path):
    result = subprocess.run(
        [sys.executable, str(Path(h.__file__)), '--release', str(ROOT / 'datasets/FuxiBench'),
         '--out', str(tmp_path), 'campaign'],
        cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan['cohort'] == 'campaign'
    assert plan['input_count'] == 93
    assert plan['target'] == 93
    assert plan['target_count'] == 93
    assert (plan['attack'], plan['benign']) == (58, 35)
    assert plan['attack_types'] == {'A': 47, 'B': 11}
    assert len(plan['excluded_ids']) == 0
    assert len(plan['scenarios']) == 93
    ids = [case['scenario_id'] for case in plan['scenarios']]
    assert len(set(ids)) == 93
    multi = h.multi_agent_cases(ROOT / 'datasets/FuxiBench')
    multi_ids = {case['scenario_id'] for case in multi if not case['detection_excluded']}
    assert len(multi_ids) == 30
    single_ids = {case['scenario_id'] for case in plan['scenarios'] if case['agents'] == 1}
    assert len(single_ids) == 63
    assert multi_ids | single_ids == set(ids)
    assert multi_ids & single_ids == set()
    assert set(plan['excluded_ids']).isdisjoint(ids)
    assert (tmp_path / 'campaign' / 'campaign-plan.json').is_file()
    assert not (tmp_path / 'campaign' / 'runs').exists()
    assert not (tmp_path / 'multi-agent').exists()


def test_campaign_paid_execution_requires_guard_before_launch(tmp_path, monkeypatch):
    monkeypatch.delenv('MTL_ALLOW_PAID', raising=False)
    called = []
    monkeypatch.setattr(h.subprocess, 'run', lambda *a, **k: called.append(a) or SimpleNamespace(returncode=0))
    args = SimpleNamespace(execute=True, release=ROOT / 'datasets/FuxiBench', out=tmp_path,
                           timeout=10, force=False)
    with pytest.raises(SystemExit, match='MTL_ALLOW_PAID'):
        h.cmd_campaign(args)
    assert not called
    assert not (tmp_path / 'campaign').exists()


def test_campaign_dry_run_checks_inputs_but_never_executes(tmp_path, monkeypatch):
    release = _campaign_fixture(tmp_path)
    _patch_fixture_checks(monkeypatch, release)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(h.subprocess, 'run', fake_run)
    args = SimpleNamespace(execute=False, release=release, out=tmp_path / 'out',
                           timeout=10, force=False)
    assert h.cmd_campaign(args) == 0
    assert all('--only' not in command for command in calls)
    assert (tmp_path / 'out' / 'campaign' / 'campaign-plan.json').is_file()
    assert not (tmp_path / 'out' / 'campaign' / 'runs').exists()


def test_campaign_runs_each_included_case_once_and_stops_on_first_failure(tmp_path, monkeypatch):
    from compare.scripts.run import pilot_observer
    release = _campaign_fixture(tmp_path)
    _patch_fixture_checks(monkeypatch, release)
    monkeypatch.setenv('MTL_ALLOW_PAID', '1')
    monkeypatch.setattr(pilot_observer, 'binary_sha256', lambda binary: 'collector-hash')
    monkeypatch.setattr(h, 'completed_case',
                        lambda target, rel, sid, digest, **kwargs:
                        (target / 'results.jsonl').is_file())

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if '--help' in command:
            return SimpleNamespace(returncode=0)
        if fail:
            return SimpleNamespace(returncode=1)
        target = Path(command[command.index('--out') + 1])
        target.mkdir(parents=True, exist_ok=True)
        (target / 'results.jsonl').write_text('{}\n')
        return SimpleNamespace(returncode=0)

    calls, fail = [], False
    monkeypatch.setattr(h.subprocess, 'run', fake_run)
    args = SimpleNamespace(execute=True, release=release, out=tmp_path / 'out',
                           timeout=10, force=False)
    assert h.cmd_campaign(args) == 0
    pilot_calls = [c for c in calls if '--only' in c]
    assert [c[c.index('--only') + 1] for c in pilot_calls] == [
        'multi-attack', 'single-attack', 'single-benign']
    assert len(pilot_calls) == 3
    assert all(c[c.index('--timeout') + 1] == '10' for c in pilot_calls)

    calls.clear()
    fail = True
    args.force = True
    assert h.cmd_campaign(args) == 1
    pilot_calls = [c for c in calls if '--only' in c]
    assert len(pilot_calls) == 1
    assert pilot_calls[0][pilot_calls[0].index('--only') + 1] == 'multi-attack'


def test_e0_uses_four_registered_strata_and_campaign_resume_directory(tmp_path):
    plan = h.campaign_plan(ROOT / 'datasets/FuxiBench', tmp_path, cohort='campaign')
    selected = h.e0_plan(plan)
    assert not selected['problems']
    assert selected['target_count'] == 4
    assert selected['campaign_target_count'] == 93
    assert len({r['scenario_id'] for r in selected['scenarios']}) == 4
    assert [(r['label'], r['attack_type'], r['agents'] > 1) for r in selected['scenarios']] == [
        ('attack', 'A', False), ('attack', 'B', True),
        ('benign', 'benign', False), ('benign', 'benign', True)]
    assert selected['out'] == plan['out']
    assert selected['planned_paid_calls'] is None
    assert selected['execution_started'] is False


def test_e0_rejects_changed_registered_stratum():
    plan = {'problems': [], 'scenarios': [], 'target_count': 93}
    selected = h.e0_plan(plan)
    assert selected['problems']
    assert selected['scenarios'] == []


def test_e0_unpaid_dry_run_never_launches_pilot(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(h.subprocess, 'run', lambda cmd, **kw: called.append(cmd) or SimpleNamespace(returncode=0))
    args = SimpleNamespace(execute=False, release=ROOT / 'datasets/FuxiBench', out=tmp_path,
                           timeout=900, force=False)
    assert h.cmd_e0(args) == 0
    assert all('--help' in command for command in called)
    assert (tmp_path / 'campaign/e0-plan.json').is_file()
    assert not (tmp_path / 'e0/runs').exists()


def test_e0_then_campaign_reuses_the_four_verified_runs(tmp_path, monkeypatch):
    from compare.scripts.run import pilot_observer
    cases = [dict(scenario_id=sid, label=label, attack_type=kind,
                  agents=3 if multi else 1, detection_excluded=False)
             for sid, label, kind, multi in h.E0_STRATA]
    cases.append(dict(scenario_id='fifth', label='benign', attack_type='benign', agents=1))
    monkeypatch.setattr(h, 'campaign_plan', lambda release, out, cohort: {
        'problems': [], 'scenarios': cases, 'target_count': 5, 'out': str(out)})
    monkeypatch.setattr(pilot_observer, 'binary_sha256', lambda binary: 'unchanged-binary')
    monkeypatch.setattr(h, 'completed_case',
                        lambda path, *a, **k: (path / 'verified').is_file())
    monkeypatch.setenv('MTL_ALLOW_PAID', '1')
    calls = []
    def fake_run(command, **kwargs):
        if '--only' in command:
            calls.append(command[command.index('--only') + 1])
            path = Path(command[command.index('--out') + 1])
            path.mkdir(parents=True)
            (path / 'verified').write_text('synthetic verification fixture')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(h.subprocess, 'run', fake_run)
    args = SimpleNamespace(execute=True, release=tmp_path, out=tmp_path / 'out', timeout=9, force=False)
    assert h.cmd_e0(args) == 0
    assert len(calls) == 4
    assert h.cmd_campaign(args) == 0
    assert len(calls) == 5
    assert len(set(calls)) == 5


def _synthetic_e0_run(root, scenario_id, agents, *, healthy=True, marker='mtl-e0nonce'):
    run = root / scenario_id
    run.mkdir(parents=True)
    (run / '.run_marker').write_text(marker + '\n')
    logs = []
    for index in range(agents):
        relative = f'logs/agent_{index}.log'
        path = run / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'agent {index} transcript\n')
        logs.append({'agent_id': f'agent_{index}', 'role_label': 'role.md', 'path': relative})
    (run / 'marker.log').write_text('\n'.join([marker] * agents) + '\n')
    summary = {'run_marker': marker, 'marker_executed': True,
               'agents_total': agents, 'agent_logs': logs,
               'collector_health': {'stop_status': 'stopped', 'is_running_after_stop': False}}
    (run / 'summary.json').write_text(json.dumps(summary))
    collector = run / 'collector'
    collector.mkdir()
    (collector / f'{scenario_id}.responsibility.db').write_bytes(b'source')
    (collector / 'finalized.db').write_bytes(b'frozen')
    tmux = None
    if agents > 1:
        tmux = {'transport': 'tmux', 'agent_pid_observed': False,
                'pane_pid_is_not_agent_pid': True,
                'panes_expected': agents, 'panes_observed': agents,
                'pane_observation_complete': True,
                'panes': [{'agent_index': i, 'session': 's', 'pane': str(i),
                           'pane_id': f'%{i}', 'pane_pid': 100 + i,
                           'role_file': f'roles/{i}.md'} for i in range(agents)]}
    return run, tmux


def _admit_fixture(tmp_path, monkeypatch, *, healthy=True, annotation=True):
    from scripts.evaluation import run_artifacts
    run_root = tmp_path / 'campaign' / 'runs'
    meta = []
    run_tmux = {}
    for sid, label, attack_type, multi in h.E0_STRATA:
        agents = 3 if multi else 1
        run, tmux = _synthetic_e0_run(run_root, sid, agents, healthy=healthy)
        run_tmux[sid] = tmux
        meta.append({'scenario_id': sid, 'label': label, 'attack_type': attack_type,
                     'agents': agents, 'detection_excluded': False})
    monkeypatch.setattr(h, 'campaign_cases', lambda release, cohort: meta)

    def fake_binding(run_dir, database, release):
        sid = Path(run_dir).name
        return {'schema': 'mtagentlens.run-binding/v4', 'scenario_id': sid,
                'collector_run_id': f'run-{sid}',
                'summary_sha256': 'a' * 64, 'runner_events_sha256': 'b' * 64,
                'database_sha256': 'c' * 64, 'tmux': run_tmux[sid]}
    canonical = {'final_decision': 'Safe' if 'benign' in str(meta) else 'Unsafe',
                 'final_severity': 'none',
                 'capture_quality_status': 'healthy' if healthy else 'degraded',
                 'checkpoint_implementation': 'd' * 64}
    monkeypatch.setattr(run_artifacts, 'bind_run_artifacts', fake_binding)
    monkeypatch.setattr(run_artifacts, 'read_canonical_verdict', lambda *a, **k: canonical)
    replay = tmp_path / 'replay'
    replay.write_bytes(b'current binary')
    monkeypatch.setattr(h.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps({
            'schema': 'mtagentlens.recording-validation/v1', 'status': 'ok',
            'read_only': True, 'failures': [], 'primary_eligible': False,
            'reference_status': 'unreviewed', 'scope': 'run'}),
        stderr=''))
    exports = tmp_path / 'annotations'
    exports.mkdir()
    for sid, *_ in h.E0_STRATA:
        (exports / f'{sid}.json').write_text(json.dumps({
            'schema': 'mtagentlens.annotation-export/v2',
            'paper_metrics_available': False,
            'tasks': [{'task_id': 'task-1',
                       'binding': {'scenario_id': sid, 'codebook_version': 'pilot/1'}}]}))
    return run_root, replay, exports if annotation else None


def test_e0_admission_requires_bound_healthy_artifacts_and_annotation_prepare(tmp_path, monkeypatch):
    run_root, replay, exports = _admit_fixture(tmp_path, monkeypatch)
    report = h.e0_admission(run_root, tmp_path / 'release', replay,
                            annotation_exports=exports)
    assert report['status'] == 'admitted', report
    assert report['admitted'] == 4
    assert all(case['annotation_prepare'] == 'validated' for case in report['cases'])
    assert all(case['annotation_export_sha256'] for case in report['cases'])


def test_e0_admission_accepts_structural_replay_that_is_not_primary_eligible(tmp_path, monkeypatch):
    """Strict replay admits the recording; annotation/reference eligibility is separate."""
    run_root, replay, exports = _admit_fixture(tmp_path, monkeypatch)
    report = h.e0_admission(run_root, tmp_path / 'release', replay,
                            annotation_exports=exports)
    assert report['status'] == 'admitted', report
    assert all(case['replay_status'] == 'ok' for case in report['cases'])
    assert all(case['replay_primary_eligible'] is False for case in report['cases'])


def test_e0_admission_requires_the_run_tag_file_witness(tmp_path, monkeypatch):
    run_root, replay, exports = _admit_fixture(tmp_path, monkeypatch)
    marker = run_root / 'exfil-env-secrets' / 'marker.log'
    marker.write_text('a-different-run\n')
    report = h.e0_admission(run_root, tmp_path / 'release', replay,
                            annotation_exports=exports)
    assert report['status'] == 'blocked'
    failures = report['cases'][0]['failures']
    assert any('RUN_TAG_FILE' in failure for failure in failures), failures

    marker.unlink()
    report = h.e0_admission(run_root, tmp_path / 'release', replay,
                            annotation_exports=exports)
    assert report['status'] == 'blocked'
    assert any('RUN_TAG_FILE' in failure for failure in report['cases'][0]['failures'])


def test_e0_admission_requires_marker_executed_summary(tmp_path, monkeypatch):
    run_root, replay, exports = _admit_fixture(tmp_path, monkeypatch)
    summary_path = run_root / 'exfil-env-secrets' / 'summary.json'
    summary = json.loads(summary_path.read_text())
    summary['marker_executed'] = False
    summary_path.write_text(json.dumps(summary))
    report = h.e0_admission(run_root, tmp_path / 'release', replay,
                            annotation_exports=exports)
    assert report['status'] == 'blocked'
    assert any('execution marker' in failure for failure in report['cases'][0]['failures'])


def test_e0_admission_without_annotation_export_is_blocked_not_claimed(tmp_path, monkeypatch):
    run_root, replay, _ = _admit_fixture(tmp_path, monkeypatch, annotation=False)
    report = h.e0_admission(run_root, tmp_path / 'release', replay)
    assert report['admitted'] == 4
    assert report['status'] == 'blocked'
    assert any('annotation prepare was not supplied' in problem for problem in report['problems'])


def test_e0_admission_rejects_degraded_capture_and_changed_stratum(tmp_path, monkeypatch):
    run_root, replay, exports = _admit_fixture(tmp_path, monkeypatch, healthy=False)
    monkeypatch.setattr(h, 'campaign_cases', lambda release, cohort: [
        {'scenario_id': 'exfil-env-secrets', 'label': 'benign', 'attack_type': 'benign',
         'agents': 1, 'detection_excluded': False}])
    report = h.e0_admission(run_root, tmp_path / 'release', replay,
                            annotation_exports=exports)
    assert report['status'] == 'blocked'
    first = report['cases'][0]
    assert first['status'] == 'failed'
    assert 'capture quality is not healthy' in first['failures']
    assert 'release stratum label/type/topology changed' in first['failures']


def test_e0_admission_subcommand_parses_explicit_paths(tmp_path, monkeypatch):
    seen = {}

    def fake(run_root, release, replay_binary, **kwargs):
        seen.update(run_root=run_root, release=release, replay=replay_binary, kwargs=kwargs)
        return {'status': 'admitted'}

    monkeypatch.setattr(h, 'e0_admission', fake)
    replay = tmp_path / 'replay'
    replay.write_bytes(b'binary')
    monkeypatch.setattr(sys, 'argv', [
        'final_campaign_harness.py', '--out', str(tmp_path / 'out'), 'e0-admission',
        '--run-root', str(tmp_path / 'runs'), '--replay-binary', str(replay),
        '--annotation-exports', str(tmp_path / 'exports'), '--timeout', '7'])
    assert h.main() == 0
    assert seen['run_root'] == tmp_path / 'runs'
    assert seen['replay'] == replay
    assert seen['kwargs'] == {'timeout': 7, 'annotation_exports': tmp_path / 'exports'}


def test_unbound_dataset_plan_records_no_release_identity(tmp_path, monkeypatch):
    release = _campaign_fixture(tmp_path)
    from mtagentlens_benchmark.loader import scenario_loader
    monkeypatch.setattr(h, 'FROZEN_SCENARIO_COUNT', 4)
    configs = [SimpleNamespace(id=path.stem, pre_run_script=None)
               for path in sorted((release / 'scenarios').glob('*.json'))]
    monkeypatch.setattr(scenario_loader, 'load_all_scenarios', lambda *a, **k: configs)
    plan = h.campaign_plan(release, tmp_path / 'out', cohort='campaign')
    assert not plan['problems']
    assert plan['release_id'] is None
    assert plan['target_count'] == 3


def test_existing_invalid_release_manifest_remains_an_error(tmp_path):
    release = _campaign_fixture(tmp_path)
    (release / 'release.json').write_text('{}')
    with pytest.raises(ValueError):
        h._validated_release(release)


@pytest.mark.parametrize(('release_id', 'case_ids', 'status', 'source_status'), [
    (None, [None, None], 0, 'unbound'),
    (None, [None, 'old'], 1, 'unbound'),
    ('current', ['current', 'current'], 0, 'release-bound'),
    ('current', ['current', 'old'], 1, 'release-bound'),
])
def test_runtime_annotation_source_binding(tmp_path, monkeypatch, capsys,
                                           release_id, case_ids, status, source_status):
    monkeypatch.setattr(h, 'runtime_commands', lambda *args: [['true']] * 3)
    monkeypatch.setattr(h.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(h, '_read_json', lambda path: {
        'cases': [{'binding': {'data_release_id': rid}} for rid in case_ids],
        'records': [{'status': 'included'} for _ in case_ids],
    })
    monkeypatch.setattr(h, '_validated_release', lambda path: {'release_id': release_id})
    monkeypatch.setattr(h, '_write_plan', lambda *args: None)
    args = SimpleNamespace(execute=False, out=tmp_path, run_root=tmp_path, release=tmp_path)
    assert h.cmd_runtime_annotations(args) == status
    plan = json.loads(capsys.readouterr().out)
    assert plan['source_status'] == source_status
    assert bool(plan['problems']) == bool(status)


def test_runtime_annotation_missing_binding_is_not_unbound(tmp_path, monkeypatch):
    monkeypatch.setattr(h, 'runtime_commands', lambda *args: [['true']] * 3)
    monkeypatch.setattr(h.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(h, '_read_json', lambda path: {
        'cases': [{'binding': {}}], 'records': [{'status': 'included'}],
    })
    monkeypatch.setattr(h, '_validated_release', lambda path: {'release_id': None})
    monkeypatch.setattr(h, '_write_plan', lambda *args: None)
    args = SimpleNamespace(execute=False, out=tmp_path, run_root=tmp_path, release=tmp_path)
    assert h.cmd_runtime_annotations(args) == 1
