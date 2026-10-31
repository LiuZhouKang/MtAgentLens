import json

import pytest

from tools.annotator.ai_runtime import (adjudicator_id, finalize, reviewer_ids,
                                        sanitize_event, sanitize_review_packet,
                                        sanitize_review_view,
                                        scan_review_packet_residue, validate_review,
                                        verify_artifact)


def test_detector_rows_and_fields_cannot_enter_prompt():
    assert sanitize_event('collector', {'table': 'responsibility_claims', 'row': {'score': 1}}) is None
    row = sanitize_event('collector', {'table': 'actions', 'row': {'pid': 3, 'action_type': 'FileOpen', 'agent_id': 'derived', 'signal_class': 'danger', 'outcome': 'succeeded'}})
    assert row == {'table': 'actions', 'row': {'pid': 3, 'action_type': 'FileOpen', 'outcome': 'succeeded'}}


def test_runner_fixtures_and_release_ids_removed():
    row = sanitize_event('runner', {'event_type': 'launch_attempt', 'run_id': 'attack', 'details': {'input_snapshot': {'prompt': 'authorized task', 'fixtures': {'payload': 'secret'}}}})
    assert 'fixtures' not in str(row) and 'attack' not in str(row)
    assert row['details']['prompt'] == 'authorized task'


def test_review_rejects_unknown_reference_and_nonmonotonic_labels():
    review = {'case_id':'case-1', 'attempted':'no', 'executed':'yes', 'succeeded':'no', 'evidence_refs':['e1'], 'notes':'x', 'entry':None, 'chain':[], 'root_actor':None}
    with pytest.raises(ValueError): validate_review(review, {'e1'})
    review.update(attempted='yes', evidence_refs=['fake'])
    with pytest.raises(ValueError): validate_review(review, {'e1'})


def test_compact_selection_is_explicit_and_full_input_preserved(tmp_path):
    import json
    from tools.annotator.ai_runtime import compact_all, dump
    events = [{'ref': 'brief', 'source': 'task_brief', 'data': {'task':'audit logs'}}]
    for index in range(300):
        events.append({'ref': f'a{index}', 'source':'collector', 'data': {'table':'actions', 'row': {
            'action_id':f'a{index}', 'pid':7, 'process_start_time_ns':123,
            'action_type':'FileOpen', 'normalized_args_json':json.dumps([{'Path':f'/tmp/path-{index}'}]),
            'timestamp_ns': index, 'outcome':'unknown', 'syscall_ret':None, 'syscall_name':'openat'}}})
    packet = {'case_id':'case-0000', 'events':events}
    dump(tmp_path / 'packets/case-0000.json', packet)
    dump(tmp_path / 'inventory.json', {'cases':[{'case_id':'case-0000'}]})
    compact_all(tmp_path)
    compacted = json.loads((tmp_path / 'review-packets/case-0000.json').read_text())
    assert compacted['selection']['truncated'] is True
    assert compacted['selection']['full_event_count'] == 301
    assert compacted['events'][0]['ref'] == 'brief'
    assert json.loads((tmp_path / 'packets/case-0000.json').read_text()) == packet


def test_parse_response_uses_final_and_retains_session(tmp_path):
    import json
    from tools.annotator.ai_runtime import parse_response
    path = tmp_path / 'answer.jsonl'
    path.write_text('\n'.join(json.dumps(x) for x in [
        {'type':'session', 'sessionId':'fresh-session'}, {'type':'text', 'text':'ignore'},
        {'type':'final', 'text':'[{"case_id":"c"}]'}]))
    assert parse_response(path) == ([{'case_id':'c'}], 'fresh-session')


def test_finalize_saves_only_model_proposals_and_exports_sparse_identity(tmp_path):
    import json
    from tools.annotator.ai_runtime import dump, finalize, sha
    from tools.annotator.evidence import build_bundle
    from tools.annotator.records import RecordStore
    from tools.annotator.tests.test_evidence import fixture_inputs
    inputs = fixture_inputs(tmp_path)
    bundle_root = tmp_path / 'bundles/case-0000'
    bundle = build_bundle(bundle_root, **inputs)
    store = RecordStore(tmp_path / 'records.sqlite')
    task_id = store.register_bundle(bundle_root)
    case = {'case_id':'case-0000', 'task_id':task_id, 'binding':bundle['binding'], 'review_packet_sha256':'x'}
    dump(tmp_path / 'inventory.json', {'cases':[case]})
    dump(tmp_path / 'packets/case-0000.json', {'events':[{'ref':'event-1'}]})
    dump(tmp_path / 'review-packets/case-0000.json', {'events':[{'ref':'event-1'}], 'selection':{'truncated':False}})
    dump(tmp_path / 'physical-attribution/case-0000.json', {'mapped':0, 'total_actions':1})
    review = {'case_id':'case-0000', 'attempted':'no', 'executed':'no', 'succeeded':'no',
              'evidence_refs':['event-1'], 'notes':'Synthetic authorized action; regression only.',
              'entry':None, 'chain':[], 'root_actor':None}
    for name in ('dsh-a', 'dsh-b'):
        path = tmp_path / f'reviews/{name}-case-0000.jsonl'
        path.parent.mkdir(exist_ok=True)
        path.write_text('\n'.join(json.dumps(x) for x in [
            {'type':'session', 'sessionId':name}, {'type':'final','text':json.dumps([review])}]))
        dump(path.with_suffix('.metadata.json'), {'returncode':0, 'cases':['case-0000']})
    result = finalize(tmp_path)
    assert result['completed_cases'] == 1
    assert 'identities' not in result['records'][0]['annotation']
    assert result['records'][0]['event_identity_policy']['event_count'] == 1
    revisions = store.export()['tasks'][0]['revisions']
    assert len(revisions) == 3
    assert {r['method'] for r in revisions} == {'model_proposal'}
    assert all(r['annotation']['identities'] == {'event-1':None} for r in revisions)


def _sanitizer_packet():
    """One packet carrying every class of historical label residue."""
    events = [
        {'ref': 'runner:1', 'source': 'runner',
         'data': {'event_type': 'launch_attempt', 'details': {'prompt': 'authorized audit task'}}},
        {'ref': 'trajectory:label', 'source': 'trajectory',
         'data': {'kind': 'assistant_message', 'tool_calls': [],
                  'text': 'The ground truth says label: "attack", attack_type "A".'}},
        {'ref': 'trajectory:meta', 'source': 'trajectory',
         'data': {'kind': 'assistant_message', 'tool_calls': [],
                  'text': 'This is a benchmark eval scenario; the expected behavior is refusal.'}},
        {'ref': 'trajectory:gt-reference', 'source': 'trajectory',
         'data': {'kind': 'assistant_message', 'text': 'Let me check the runtime logs.',
                  'tool_calls': [{'name': 'Bash',
                                  'input': {'command': 'cat ground_truth/benign/case.json'}}]}},
        {'ref': 'trajectory:clean', 'source': 'trajectory',
         'data': {'kind': 'assistant_message', 'text': 'I will read docs.md first.',
                  'tool_calls': [{'name': 'Read', 'input': {'file_path': '/tmp/docs.md'}}]}},
        {'ref': 'actions:gt', 'source': 'collector',
         'data': {'table': 'actions', 'row': {'pid': 7, 'action': 'FileOpen',
                  'args': [{'Path': 'compare/Dataset/FuxiBench/ground_truth/benign/x.json'}]}}},
        {'ref': 'actions:normal', 'source': 'collector',
         'data': {'table': 'actions', 'row': {'pid': 7, 'action': 'FileOpen',
                  'args': [{'Path': '/tmp/docs.md'}]}}},
    ]
    return {'case_id': 'case-0000', 'task_id': 'task-0', 'events': events,
            'selection': {'full_event_count': 12, 'shown_event_count': 7, 'omitted_event_count': 5,
                          'truncated': True, 'shown_source_counts': {'runner': 1, 'trajectory': 4,
                                                                     'collector': 2}}}


def test_sanitizer_drops_label_residue_by_reason_and_keeps_evidence():
    clean, report = sanitize_review_packet(_sanitizer_packet())
    assert [e['ref'] for e in clean['events']] == ['runner:1', 'trajectory:clean', 'actions:normal']
    assert report['removed_event_count'] == 4
    assert report['removed_by_reason'] == {'label_quote': 1, 'gt_path_reference': 1,
                                           'benchmark_meta': 1, 'gt_path_open': 1}
    assert {x['ref'] for x in report['removed_events']} == {
        'trajectory:label', 'trajectory:meta', 'trajectory:gt-reference', 'actions:gt'}
    assert scan_review_packet_residue(clean['events']) == []
    assert clean['selection']['sanitized_event_count'] == 4
    assert clean['selection']['shown_event_count'] == 3
    assert clean['selection']['shown_source_counts'] == {'runner': 1, 'trajectory': 1, 'collector': 1}
    assert set(clean['sanitization']['rules']) >= {
        'label_quote', 'gt_path_reference', 'benchmark_meta', 'gt_path_open'}


def test_sanitizer_report_counts_clean_packet_as_unchanged():
    packet = {'case_id': 'case-0001', 'task_id': 'task-1',
              'events': [{'ref': 'runner:1', 'source': 'runner', 'data': {'event_type': 'run_started'}}],
              'selection': {'full_event_count': 1, 'shown_event_count': 1, 'omitted_event_count': 0,
                            'truncated': False, 'shown_source_counts': {'runner': 1}}}
    clean, report = sanitize_review_packet(packet)
    assert report['removed_event_count'] == 0
    assert report['removed_events'] == []
    assert clean['events'] == packet['events']
    assert clean['selection'] == packet['selection']


def test_sanitize_review_view_writes_clean_copy_and_never_touches_originals(tmp_path):
    from tools.annotator.ai_runtime import dump, sha
    source, target = tmp_path / 'source', tmp_path / 'target'
    inventory = {'schema': 'mtagentlens.ai-runtime-inventory/v1',
                 'cases': [{'case_id': 'case-0000', 'task_id': 'task-0'},
                           {'case_id': 'case-0001', 'task_id': 'task-1'}]}
    dump(source / 'inventory.json', inventory)
    dump(source / 'review-packets/case-0000.json', _sanitizer_packet())
    clean_packet = {'case_id': 'case-0001', 'task_id': 'task-1',
                    'events': [{'ref': 'runner:1', 'source': 'runner', 'data': {'event_type': 'run_started'}}],
                    'selection': {'full_event_count': 1, 'shown_event_count': 1,
                                  'omitted_event_count': 0, 'truncated': False,
                                  'shown_source_counts': {'runner': 1}}}
    dump(source / 'review-packets/case-0001.json', clean_packet)
    before = {path.name: sha(path.read_bytes())
              for path in sorted((source / 'review-packets').glob('*.json'))}
    manifest = sanitize_review_view(source, target)
    after = {path.name: sha(path.read_bytes())
             for path in sorted((source / 'review-packets').glob('*.json'))}
    assert before == after, 'source review packets must stay byte-identical'
    assert manifest['schema'] == 'mtagentlens.review-view-sanitizer/1'
    assert manifest['packets_scanned'] == 2
    assert manifest['packets_affected'] == 1
    assert manifest['events_removed'] == 4
    assert manifest['class_counts'] == {'label_quote': 1, 'gt_path_reference': 1,
                                        'gt_text_reference': 0, 'benchmark_meta': 1,
                                        'gt_path_open': 1}
    assert manifest['residual_scan']['residual_events'] == 0
    assert 'generic words like benchmark/lab' in manifest['accepted_disclosure']
    assert manifest['accepted_disclosure'].endswith('not GT labels/paths')
    cleaned = json.loads((target / 'review-packets-clean/case-0000.json').read_text())
    assert scan_review_packet_residue(cleaned['events']) == []
    assert (target / 'review-packets-clean/case-0001.json').read_bytes() == \
        (source / 'review-packets/case-0001.json').read_bytes()
    clean_inventory = json.loads((target / 'inventory-clean.json').read_text())
    assert clean_inventory['source_root'] == str(source.resolve())
    assert clean_inventory['source_inventory_sha256'] == sha((source / 'inventory.json').read_bytes())
    cases = {case['case_id']: case for case in clean_inventory['cases']}
    assert cases['case-0000']['review_packet_sha256'] == sha(
        (target / 'review-packets-clean/case-0000.json').read_bytes())
    assert cases['case-0000']['original_review_packet_sha256'] == before['case-0000.json']
    assert cases['case-0000']['review_packet_bytes'] == len(
        json.dumps(cleaned, ensure_ascii=False).encode())
    assert cases['case-0001']['review_packet_sha256'] == before['case-0001.json']
    per_case = {record['case_id']: record for record in manifest['packets']}
    assert per_case['case-0000']['removed_by_reason'] == {
        'label_quote': 1, 'gt_path_reference': 1, 'benchmark_meta': 1, 'gt_path_open': 1}


def test_reviewer_ids_scale_to_three_with_a_separate_adjudicator():
    assert reviewer_ids(2) == ['dsh-a', 'dsh-b']
    assert reviewer_ids(3) == ['dsh-a', 'dsh-b', 'dsh-c']
    assert adjudicator_id(2) == 'dsh-c'
    assert adjudicator_id(3) == 'dsh-adjudicator'
    with pytest.raises(ValueError):
        reviewer_ids(1)
    with pytest.raises(ValueError):
        reviewer_ids(27)


def _write_review(root, reviewer, case_id, review, session_id, fresh_session=True, reviewers=3):
    from tools.annotator.ai_runtime import adjudicator_id, dump, review_input, sha
    path = root / f'reviews/{reviewer}-{case_id}.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(json.dumps(value) for value in [
        {'type': 'session', 'sessionId': session_id},
        {'type': 'final', 'text': json.dumps([review])}]) + '\n')
    prompt = root / f'prompts/{reviewer}-{case_id}.txt'
    prompt.parent.mkdir(parents=True, exist_ok=True)
    if reviewers >= 3:
        text, packet_shas = review_input(root, [case_id], adjudicate=reviewer == adjudicator_id(reviewers))
    else:
        text, packet_shas = 'synthetic legacy reviewer prompt', None
    prompt.write_text(text)
    metadata = {'reviewer': reviewer, 'returncode': 0, 'cases': [case_id],
                'prompt_sha256': sha(prompt.read_bytes()), 'stdout_sha256': sha(path.read_bytes()),
                'model': 'deepseek-flash', 'provider': 'deepseek-official'}
    if reviewers >= 3:
        metadata.update(review_packet_sha256=packet_shas, reviewers=reviewers,
                        adjudication=reviewer == adjudicator_id(reviewers))
    if fresh_session is not None:
        metadata['fresh_session'] = fresh_session
    dump(path.with_suffix('.metadata.json'), metadata)


def _build_runtime_case(root, index):
    from tools.annotator.ai_runtime import dump, sha
    from tools.annotator.evidence import build_bundle
    from tools.annotator.records import RecordStore
    from tools.annotator.tests.test_evidence import fixture_inputs
    fixture_root = root / f'fixture{index}'
    fixture_root.mkdir(parents=True)
    inputs = fixture_inputs(fixture_root)
    binding = dict(inputs['binding'], run_id=f'run-{index}',
                   source_run_uuid=f'efc35a21-b940-4916-a5c8-9996638b60d{index}')
    inputs['binding'] = binding
    case_id = f'case-000{index}'
    bundle_root = root / f'bundles/{case_id}'
    build_bundle(bundle_root, **inputs)
    task_id = RecordStore(root / 'records.sqlite').register_bundle(bundle_root)
    packet_path = root / f'packets/{case_id}.json'
    dump(packet_path, {'events': [{'ref': 'event-1'}]})
    review_packet_path = root / f'review-packets/{case_id}.json'
    dump(review_packet_path, {'events': [{'ref': 'event-1', 'source': 'runner',
                                          'data': {'details': {}}}],
                              'selection': {'truncated': False, 'source_capture_truncation': False}})
    dump(root / f'physical-attribution/{case_id}.json', {'mapped': 0, 'total_actions': 1})
    return {'case_id': case_id, 'task_id': task_id, 'binding': binding,
            'packet_sha256': sha(packet_path.read_bytes()),
            'review_packet_sha256': sha(review_packet_path.read_bytes())}


def test_finalize_three_reviewers_needs_independent_adjudicator_then_verifies(tmp_path):
    from tools.annotator.ai_runtime import dump
    from tools.annotator.records import RecordStore
    cases = [_build_runtime_case(tmp_path, index) for index in (0, 1)]
    dump(tmp_path / 'inventory.json', {'cases': cases})
    agreed = {'case_id': cases[0]['case_id'], 'attempted': 'no', 'executed': 'no',
              'succeeded': 'no', 'evidence_refs': ['event-1'], 'notes': 'Synthetic no evidence.',
              'entry': None, 'chain': [], 'root_actor': None}
    first = {'case_id': cases[1]['case_id'], 'attempted': 'no', 'executed': 'no', 'succeeded': 'no',
             'evidence_refs': ['event-1'],
             'notes': 'Synthetic authorized action; one fresh vote for regression only.',
             'entry': None, 'chain': [], 'root_actor': None}
    second = {'case_id': cases[1]['case_id'], 'attempted': 'yes', 'executed': 'unknown',
              'succeeded': 'no', 'evidence_refs': ['event-1'],
              'notes': 'Synthetic conflicting vote; regression only.',
              'entry': None, 'chain': [], 'root_actor': None}
    third = {'case_id': cases[1]['case_id'], 'attempted': 'yes', 'executed': 'yes',
             'succeeded': 'unknown', 'evidence_refs': ['event-1'],
             'notes': 'Synthetic third conflicting vote; regression only.',
             'entry': None, 'chain': [], 'root_actor': None}
    for reviewer, vote in zip(reviewer_ids(3), (first, second, third)):
        _write_review(tmp_path, reviewer, cases[1]['case_id'], vote, f'session-{reviewer}-1')
    for reviewer in reviewer_ids(3):
        _write_review(tmp_path, reviewer, cases[0]['case_id'], agreed, f'session-{reviewer}-0')
    result = finalize(tmp_path, reviewers=3)
    assert result['reviewer_count'] == 3
    assert result['protocol_version'] == 'ai-consensus-3/1'
    assert result['method'] == 'ai_consensus_3'
    assert result['completed_cases'] == 1
    assert result['coverage']['awaiting_adjudication'] == 1
    assert result['disputes'][0]['case_id'] == cases[1]['case_id']
    assert result['disputes'][0]['reviewers'] == reviewer_ids(3)
    assert result['records'][0]['method'] == 'ai_consensus_3'
    assert result['records'][0]['human_review'] is False
    assert result['records'][0]['reviewer_count'] == 3
    adjudicated = {'case_id': cases[1]['case_id'], 'attempted': 'yes', 'executed': 'unknown',
                   'succeeded': 'unknown', 'evidence_refs': ['event-1'],
                   'notes': 'Synthetic adjudication override; regression only.',
                   'entry': None, 'chain': [], 'root_actor': None}
    _write_review(tmp_path, adjudicator_id(3), cases[1]['case_id'], adjudicated,
                  'session-adjudicator-1')
    result = finalize(tmp_path, reviewers=3)
    assert result['completed_cases'] == 2
    disputed = [record for record in result['records'] if record['case_id'] == cases[1]['case_id']][0]
    assert disputed['annotation']['attempted']['value'] == 'yes'
    assert 'adjudicat' in disputed['adjudication']
    assert set(disputed['reviewers']) == set(reviewer_ids(3)) | {adjudicator_id(3)}
    revisions = RecordStore(tmp_path / 'records.sqlite').export()['tasks'][0]['revisions']
    assert len(revisions) == 4
    assert {revision['method'] for revision in revisions} == {'model_proposal'}
    verification = verify_artifact(tmp_path, reviewers=3)
    assert verification['valid'] is True, verification['errors']
    assert verification['reviewer_count'] == 3
    assert verification['sanitizer_checked'] is False
    metadata_path = tmp_path / 'reviews/dsh-a-case-0000.metadata.json'
    metadata = json.loads(metadata_path.read_text())
    metadata['review_packet_sha256'] = {cases[0]['case_id']: '0' * 64}
    dump(metadata_path, metadata)
    invalid = verify_artifact(tmp_path, reviewers=3)
    assert invalid['valid'] is False
    assert any('packet binding' in error for error in invalid['errors'])


def test_legacy_two_reviewer_protocol_finalizes_and_verifies(tmp_path):
    from tools.annotator.ai_runtime import dump
    case = _build_runtime_case(tmp_path, 0)
    dump(tmp_path / 'inventory.json', {'cases': [case]})
    review = {'case_id': case['case_id'], 'attempted': 'no', 'executed': 'no', 'succeeded': 'no',
              'evidence_refs': ['event-1'],
              'notes': 'Synthetic legacy-protocol review; regression only.',
              'entry': None, 'chain': [], 'root_actor': None}
    for reviewer in reviewer_ids(2):
        # Legacy ai-silver/1 metadata predates the fresh_session field.
        _write_review(tmp_path, reviewer, case['case_id'], review,
                      f'session-legacy-{reviewer}', fresh_session=None, reviewers=2)
    result = finalize(tmp_path)
    assert result['protocol_version'] == 'ai-silver/1'
    assert result['method'] == 'model_proposal'
    assert result['reviewer_count'] == 2
    assert result['completed_cases'] == 1
    verification = verify_artifact(tmp_path)
    assert verification['valid'] is True, verification['errors']
    assert verification['reviewer_count'] == 2


def test_finalize_with_no_valid_reviews_reports_missing_instead_of_crashing(tmp_path):
    from tools.annotator.ai_runtime import dump
    case = _build_runtime_case(tmp_path, 0)
    dump(tmp_path / 'inventory.json', {'cases': [case]})
    result = finalize(tmp_path, reviewers=3)
    assert result['completed_cases'] == 0
    assert result['coverage']['missing_reviews'] == 1
    assert result['field_agreement']['attempted']['total'] == 0


def test_repair_quarantines_invalid_session_and_reschedules_missing(tmp_path, monkeypatch):
    import tools.annotator.ai_runtime as runtime
    from tools.annotator.ai_runtime import dump, quarantine_session, scan_review_sessions
    case = _build_runtime_case(tmp_path, 0)
    dump(tmp_path / 'inventory.json', {'cases': [case]})
    bad = tmp_path / 'reviews/dsh-a-case-0000.jsonl'
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text('\n'.join(json.dumps(x) for x in [
        {'type': 'session', 'sessionId': 'session-bad'},
        {'type': 'final', 'text': 'not json'}]) + '\n')
    dump(bad.with_suffix('.metadata.json'), {'reviewer': 'dsh-a', 'returncode': 0,
                                             'cases': [case['case_id']]})
    state = scan_review_sessions(tmp_path, reviewers=3)
    assert state['invalid'] and state['invalid'][0][0] == 'dsh-a-case-0000'
    assert state['valid'] == {'dsh-a': set(), 'dsh-b': set(), 'dsh-c': set()}
    calls = []
    monkeypatch.setattr(runtime, 'run_batch',
                        lambda *args, **kwargs: calls.append((args, kwargs)))
    result = runtime.repair_reviews(tmp_path, reviewers=3)
    assert result['quarantined'] == ['dsh-a-case-0000']
    assert (tmp_path / 'reviews-failed/dsh-a-case-0000/reason.txt').is_file()
    assert not (tmp_path / 'reviews/dsh-a-case-0000.jsonl').exists()
    # one fresh batch per reviewer, each asked for the missing case
    assert len(calls) == 3
    assert {call[1]['reviewers'] for call in calls} == {3}
    assert all(call[0][1][0]['case_id'] == case['case_id'] for call in calls)


def test_prepare_real_native_ledger_freezes_and_reuses_verified_sources(tmp_path, monkeypatch):
    """A local synthetic client exercises real runner/audit/bundle code, no model."""
    from pathlib import Path
    from types import SimpleNamespace
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / 'env/src'))
    from env.tests.test_release_input_chain import source_release, configure_client
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    from tools.annotator.ai_runtime import prepare
    release, manifest, config = source_release(tmp_path, with_setup=False)
    executor = configure_client(tmp_path, monkeypatch)
    run = tmp_path / 'run'
    result = executor.execute_scenario(config, run, SimpleNamespace(root=tmp_path),
        input_audit_factory=lambda conf, directory: ReleaseInputAudit(release, manifest, conf, directory))
    assert result.success, result.error
    runtime = tmp_path / 'runtime'
    inventory = prepare(runtime, run_root=run)
    assert inventory['counts'] == {'included': 1}
    assert inventory['cases'][0]['binding']['data_release_id'] == manifest['release_id']
    assert prepare(runtime, run_root=run) == inventory
    collector = run / 'collector'
    collector.mkdir(exist_ok=True)
    (collector / 'collector.log').write_text('changed captured trajectory')
    with pytest.raises(ValueError, match='source changed'):
        prepare(runtime, run_root=run)


def test_canonical_cached_review_requires_current_packet_binding(tmp_path):
    from tools.annotator.ai_runtime import dump, run_batch
    case = _build_runtime_case(tmp_path, 0)
    path = tmp_path / 'reviews/dsh-a-case-0000.jsonl'
    dump(path, {})
    dump(path.with_suffix('.metadata.json'), {'cases': [case['case_id']], 'returncode': 0})
    with pytest.raises(ValueError, match='packet binding'):
        run_batch(tmp_path, [case], 'dsh-a', reviewers=3)


def test_canonical_finalize_rejects_unbound_or_stale_review_prompts(tmp_path):
    from tools.annotator.ai_runtime import dump
    case = _build_runtime_case(tmp_path, 0)
    dump(tmp_path / 'inventory.json', {'cases': [case]})
    review = {'case_id': case['case_id'], 'attempted': 'no', 'executed': 'no', 'succeeded': 'no',
              'evidence_refs': ['event-1'], 'notes': 'Synthetic review.',
              'entry': None, 'chain': [], 'root_actor': None}
    for reviewer in reviewer_ids(3):
        _write_review(tmp_path, reviewer, case['case_id'], review, f'session-{reviewer}')
        metadata_path = tmp_path / f'reviews/{reviewer}-{case["case_id"]}.metadata.json'
        metadata = json.loads(metadata_path.read_text())
        metadata.pop('review_packet_sha256', None)
        dump(metadata_path, metadata)
    result = finalize(tmp_path, reviewers=3)
    assert result['completed_cases'] == 0
    assert any('packet binding' in str(error) for error in result['errors'])


def test_bound_cached_review_is_reused_only_until_packet_changes(tmp_path, monkeypatch):
    from tools.annotator import ai_runtime as runtime
    case = _build_runtime_case(tmp_path, 0)
    review = {'case_id': case['case_id'], 'attempted': 'no', 'executed': 'no', 'succeeded': 'no',
              'evidence_refs': ['event-1'], 'notes': 'Synthetic review.',
              'entry': None, 'chain': [], 'root_actor': None}
    _write_review(tmp_path, 'dsh-a', case['case_id'], review, 'session-a')
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *a, **kw: pytest.fail('cached review must not call a model'))
    runtime.run_batch(tmp_path, [case], 'dsh-a', reviewers=3)
    packet = tmp_path / f'review-packets/{case["case_id"]}.json'
    data = json.loads(packet.read_text())
    data['events'][0]['data']['changed'] = True
    runtime.dump(packet, data)
    with pytest.raises(ValueError, match='packet binding'):
        runtime.run_batch(tmp_path, [case], 'dsh-a', reviewers=3)


def test_prepare_unbound_native_ledger_without_release_file(tmp_path, monkeypatch):
    """A synthetic client reaches the ordinary-directory annotation path."""
    from pathlib import Path
    from types import SimpleNamespace
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / 'env/src'))
    from env.tests.test_release_input_chain import source_release, configure_client
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    from tools.annotator.ai_runtime import prepare
    from tools.annotator.evidence import load_bundle
    release, _, config = source_release(tmp_path, with_setup=False)
    (release / 'release.json').unlink()
    executor = configure_client(tmp_path, monkeypatch)
    run = tmp_path / 'run'
    result = executor.execute_scenario(config, run, SimpleNamespace(root=tmp_path),
        input_audit_factory=lambda conf, directory: ReleaseInputAudit(release, None, conf, directory))
    assert result.success, result.error
    runtime = tmp_path / 'runtime'
    inventory = prepare(runtime, run_root=run)
    assert inventory['counts'] == {'included': 1}
    case = inventory['cases'][0]
    assert case['binding']['data_release_id'] is None
    assert load_bundle(runtime / 'bundles' / case['case_id'])['schema'] == 'mtagentlens.evidence-bundle/v2'
