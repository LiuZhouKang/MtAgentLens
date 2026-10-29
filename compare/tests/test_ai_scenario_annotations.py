"""Evidence and scientific-scope contracts for fresh AI scenario silver labels."""
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('ai_scenario_annotations', Path(__file__).resolve().parents[2] / 'scripts/evaluation/ai_scenario_annotations.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def record(tmp_path):
    path = tmp_path / 'scenario.json'
    path.write_text('{"agents":[{"task":"Run tests"}]}\n')
    ref = {'id': 'task', 'path': 'scenario.json', 'sha256': m.digest(path), 'json_pointer': '/agents/0/task', 'quote': 'Run tests'}
    field = {'status': 'not_applicable', 'value': 'No malicious entry in a benign design.', 'evidence_refs': ['task']}
    return {'scenario_id': 'one', 'label': 'benign', 'attack_type': 'benign', 'authorization_boundary': {'status': 'known', 'value': 'Run tests', 'evidence_refs': ['task']}, 'entry_point': field.copy(), 'root_source': field.copy(), 'instruction_introducer': field.copy(), 'execution_roles': [{'id': 'design_role_0', 'value': 'Runs tests', 'evidence_refs': ['task']}], 'causal_edges': [], 'references': [ref], 'limitations': ['Design only.']}


def test_valid_record_and_exact_pointer(tmp_path):
    value = record(tmp_path)
    m.validate_record(value, source_root=tmp_path)
    value['references'][0]['quote'] = 'Read secrets'
    with pytest.raises(ValueError, match='quote'):
        m.validate_record(value, source_root=tmp_path)


def test_unknown_is_not_negative_and_dangling_refs_rejected(tmp_path):
    value = record(tmp_path)
    value['entry_point']['status'] = 'unknown'
    m.validate_record(value, source_root=tmp_path)
    value['root_source']['evidence_refs'] = ['missing']
    with pytest.raises(ValueError, match='reference'):
        m.validate_record(value, source_root=tmp_path)


def test_fixture_ranges_are_exact_and_paths_cannot_escape(tmp_path):
    value = record(tmp_path)
    (tmp_path / 'hook.py').write_text('first\nsecond\n')
    value['references'].append({'id': 'hook', 'path': 'hook.py', 'sha256': m.digest(tmp_path/'hook.py'), 'line_start': 2, 'line_end': 2, 'quote': 'second'})
    m.validate_record(value, source_root=tmp_path)
    value['references'][-1]['line_end'] = 3
    with pytest.raises(ValueError):
        m.validate_record(value, source_root=tmp_path)
    value['references'][-1]['path'] = '../secret'
    with pytest.raises(ValueError):
        m.validate_record(value, source_root=tmp_path)


def test_attack_requires_concrete_design_source_and_no_runtime_ids(tmp_path):
    value = record(tmp_path)
    value.update(label='attack', attack_type='B')
    with pytest.raises(ValueError, match='attack'):
        m.validate_record(value, source_root=tmp_path)
    value['root_source']['status'] = 'known'
    value['entry_point']['status'] = 'known'
    value['instruction_introducer']['status'] = 'known'
    value['execution_roles'][0]['id'] = 'observed-agent-23'
    with pytest.raises(ValueError, match='design_role'):
        m.validate_record(value, source_root=tmp_path)


def test_release_rejects_runtime_scope_and_incomplete_coverage(tmp_path):
    value = record(tmp_path)
    artifact = {'schema': m.SCHEMA, 'scope': 'scenario_design', 'eligible_for_runtime_metrics': False, 'human_review': False, 'records': [value]}
    m.validate_artifact(artifact, expected_ids={'one'}, source_root=tmp_path)
    with pytest.raises(ValueError, match='coverage'):
        m.validate_artifact(artifact, expected_ids={'one','two'}, source_root=tmp_path)
    artifact['eligible_for_runtime_metrics'] = True
    with pytest.raises(ValueError, match='scope'):
        m.validate_artifact(artifact, expected_ids={'one'}, source_root=tmp_path)


def test_reviewer_completion_rejects_truncated_json_and_wrong_coverage():
    with pytest.raises(ValueError, match='coverage'):
        m.parse_review('[{"scenario_id":"one"}]', expected_ids={'one','two'})
    with pytest.raises(ValueError, match='JSON'):
        m.parse_review('[{"scenario_id":"one"}', expected_ids={'one'})
    records = m.parse_review('```json\n[{"scenario_id":"one"}]\n```', expected_ids={'one'})
    assert records[0]['scenario_id'] == 'one'


def test_source_manifest_detects_changes_outside_cited_spans(tmp_path):
    value = record(tmp_path)
    other = tmp_path / 'other.py'
    other.write_text('unchanged\n')
    artifact = {'schema': m.SCHEMA, 'scope': 'scenario_design', 'eligible_for_runtime_metrics': False, 'human_review': False, 'records': [value], 'source_manifest': [{'path':'other.py','sha256':m.digest(other)}]}
    other.write_text('changed\n')
    with pytest.raises(ValueError, match='source'):
        m.validate_artifact(artifact, expected_ids={'one'}, source_root=tmp_path)


def test_edge_descriptive_role_endpoint_resolves_exact_role_id(tmp_path):
    value = record(tmp_path)
    value['causal_edges'] = [{'source':'design_role_0 analyst plan','target':'artifact','relation':'writes tasked plan','evidence_refs':['task']}]
    m.validate_record(value, source_root=tmp_path)
    value['causal_edges'][0]['source'] = 'design_role_1 operator'
    with pytest.raises(ValueError, match='absent design role'):
        m.validate_record(value, source_root=tmp_path)


def test_cross_scenario_reference_is_rejected_even_with_valid_hash(tmp_path):
    value = record(tmp_path)
    (tmp_path/'source.json').write_text(json.dumps({'id':'one','fixture_dir':'own','agents':[{}]}))
    value['source_scenario_path'] = 'source.json'
    other = tmp_path/'other.txt'
    other.write_text('unrelated evidence\n')
    value['references'].append({'id':'other','path':'other.txt','sha256':m.digest(other),'line_start':1,'line_end':1,'quote':'unrelated evidence'})
    with pytest.raises(ValueError, match='cross-scenario'):
        m.validate_record(value, source_root=tmp_path)
