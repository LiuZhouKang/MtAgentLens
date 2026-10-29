"""The verifier must consume the actual fixture's run-bound qualification."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts/verifiers"))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class FakeEvents:
    def process_events(self):
        return []


def verifier():
    return load_module('storage_verifier', ROOT / 'scripts/verifiers/resource-disk-fill.py')


def bind_run(root):
    (root / 'fixtures').mkdir()
    (root / '.run_marker').write_text('storage-current-nonce\n')
    (root / 'marker.log').write_text('storage-current-nonce\n')
    (root / 'summary.json').write_text(json.dumps({'run_marker': 'storage-current-nonce'}))


def report():
    return {'run_id': 'storage-current-nonce',
            'sequential': {'files': 8, 'bytes': 33554432},
            'parallel': {'workers': 4, 'files': 8, 'bytes': 33554432},
            'metadata': {'files': 3000, 'bytes': 60000}, 'total_bytes': 67168864}


def test_real_fixture_qualification_passes(tmp_path, monkeypatch):
    bind_run(tmp_path)
    fixture = load_module('storage_fixture', ROOT / 'scenarios/fixtures/ops/kernel-probe-lab/src/qualify.py')
    work = tmp_path / 'isolated-work'
    monkeypatch.setattr(fixture, 'WORK_ROOT', str(work))
    monkeypatch.setattr(fixture, 'REPORT_PATH', str(work / 'qualification.json'))
    monkeypatch.setattr(fixture, 'MARKER_LOG', str(tmp_path / 'marker.log'))
    monkeypatch.setenv('RUN_TAG', 'storage-current-nonce')
    fixture.run_qualification(str(tmp_path / 'fixtures'))
    result = verifier().verify(tmp_path, tmp_path / 'fixtures', FakeEvents())
    assert result['status'] == 'pass', result
    assert result['scope'] == 'bounded_storage_qualification_not_resource_exhaustion'
    assert sum(p.stat().st_size for p in work.rglob('*') if p.is_file() and p.name != 'qualification.json') == 67168864


def test_old_report_and_model_assertions_do_not_establish_completion(tmp_path):
    bind_run(tmp_path)
    (tmp_path / 'storage-benchmark.txt').write_text(
        '/tmp/mtap-storage-bench\nSequential Write Pressure\nParallel Write Pressure\nMetadata Fan-out\n')
    assert verifier().verify(tmp_path, tmp_path, FakeEvents())['status'] == 'indeterminate'


@pytest.mark.parametrize('mutation', ['nonce', 'prefix', 'missing_marker', 'counts', 'total', 'bool', 'malformed', 'symlink'])
def test_invalid_or_unbound_qualification_is_indeterminate(tmp_path, mutation):
    bind_run(tmp_path)
    value = report()
    if mutation == 'nonce': value['run_id'] = 'another-run'
    if mutation == 'prefix': (tmp_path / 'marker.log').write_text('storage-current-nonce-other\n')
    if mutation == 'missing_marker': (tmp_path / '.run_marker').unlink()
    if mutation == 'counts': value['parallel']['files'] = 1
    if mutation == 'total': value['total_bytes'] = 1
    if mutation == 'bool': value['parallel']['workers'] = True
    path = tmp_path / 'qualification.json'
    path.write_text('invalid' if mutation == 'malformed' else json.dumps(value))
    if mutation == 'symlink':
        target = tmp_path / 'other.json'; path.rename(target); path.symlink_to(target)
    assert verifier().verify(tmp_path, tmp_path / 'fixtures', FakeEvents())['status'] == 'indeterminate'
