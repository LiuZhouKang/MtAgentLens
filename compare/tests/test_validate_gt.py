"""The GT validator fails closed on ambiguous or malformed source records."""
import json
import sys

import pytest

from scripts.evaluation import validate_gt


def dataset(tmp_path):
    (tmp_path / 'prompt_inputs').mkdir()
    (tmp_path / 'ground_truth').mkdir()
    (tmp_path / 'prompt_inputs/manifest.json').write_text(json.dumps({'entries': [{'scenario_id': 'case'}]}))
    (tmp_path / 'ground_truth/case.json').write_text(json.dumps({
        'schema': 'mtagentlens.ground_truth.v4', 'scenario_id': 'case', 'category': 'benign',
        'label': 'benign', 'attack_type': 'benign', 'task_summary': 'ordinary task',
        'payload': None, 'min_decision': 'Safe', 'should_alert': False, 'execution_check': {},
        'benign_activity': {'summary': 'ordinary task'},
        'annotation': {'annotators': validate_gt.ANNOTATORS, 'reviewer_agent_labels': validate_gt.ANNOTATORS,
                       'collector_output_consulted': False}, 'quality': {}}))
    sidecar = tmp_path / 'ground_truth/_reviewers/case.json'
    sidecar.parent.mkdir()
    sidecar.write_text(json.dumps({'scenario_id': 'case', 'reviewers': [
        {'name': name, 'review': {'label': 'benign', 'attack_type': 'benign', 'expected_behaviors': []}}
        for name in validate_gt.ANNOTATORS]}))
    return tmp_path


def run(dataset, monkeypatch, capsys):
    monkeypatch.setattr(validate_gt, 'EXTRA_BENIGN', [])
    monkeypatch.setattr(sys, 'argv', ['validate_gt', '--dataset', str(dataset)])
    status = validate_gt.main()
    return status, json.loads(capsys.readouterr().out)


def test_explicit_ai_alias_provenance_is_accepted(tmp_path, monkeypatch, capsys):
    root = dataset(tmp_path)
    path = root / 'ground_truth/case.json'
    data = json.loads(path.read_text())
    data['annotation'].pop('annotators')
    path.write_text(json.dumps(data))
    status, result = run(root, monkeypatch, capsys)
    assert status == 0, result


@pytest.mark.parametrize('problem', ['duplicate', 'malformed', 'wrong_schema', 'wrong_type', 'blocked'])
def test_invalid_records_or_unresolved_review_are_not_a_clean_dataset(tmp_path, monkeypatch, capsys, problem):
    root = dataset(tmp_path)
    path = root / 'ground_truth/case.json'
    data = json.loads(path.read_text())
    if problem == 'duplicate':
        (root / 'ground_truth/duplicate.json').write_text(path.read_text())
    elif problem == 'malformed':
        (root / 'ground_truth/broken.json').write_text('{')
    elif problem == 'wrong_schema':
        data['schema'] = 'invented.v99'
    elif problem == 'wrong_type':
        data['attack_type'] = 'B'
    elif problem == 'blocked':
        data['quality']['review'] = {'status': 'blocked'}
    path.write_text(json.dumps(data))
    status, result = run(root, monkeypatch, capsys)
    assert status == 1
    assert result['problems'] or result['review'] > 0
