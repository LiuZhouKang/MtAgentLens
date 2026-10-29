"""Legacy review must never treat an old frozen DB as a newly collected run."""
import importlib.util
from pathlib import Path
import sqlite3
import subprocess

SOURCE = Path(__file__).parents[1] / 'scripts/run/recollect_single.py'
spec = importlib.util.spec_from_file_location('legacy_recollection', SOURCE)
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


def test_legacy_recollection_does_not_launch_or_relabel_frozen_data(tmp_path, monkeypatch):
    frozen = tmp_path / 'benign' / 'sample' / 'responsibility.db'
    frozen.parent.mkdir(parents=True)
    with sqlite3.connect(frozen) as db:
        db.execute('CREATE TABLE sentinel (value TEXT)')
    before = frozen.read_bytes()
    calls = []
    monkeypatch.setattr(legacy, 'TRACES_DIR', tmp_path)
    monkeypatch.setattr(legacy, 'audit_database', lambda *args: {
        'pass': True, 'tables': {}, 'db_size_bytes': 0, 'integrity': 'ok', 'warnings': [],
    })
    monkeypatch.setattr(legacy, 'compare_to_baseline', lambda *args: {'significant_changes': False})
    monkeypatch.setattr(legacy.subprocess, 'run', lambda *args, **kwargs: (
        calls.append(args) or subprocess.CompletedProcess(args, 0, '', '')
    ))
    result = legacy.run_one_scenario({'scenario_id': 'sample', 'category': 'benign'}, skip_if_exists=False)
    assert result['pass'] is False
    assert calls == []
    assert frozen.read_bytes() == before


def test_existing_frozen_data_can_still_be_reviewed(tmp_path, monkeypatch):
    frozen = tmp_path / 'sample' / 'responsibility.db'
    frozen.parent.mkdir()
    with sqlite3.connect(frozen) as db:
        db.execute('CREATE TABLE sentinel (value TEXT)')
    monkeypatch.setattr(legacy, 'TRACES_DIR', tmp_path)
    monkeypatch.setattr(legacy, 'audit_database', lambda *args: {'pass': True, 'tables': {}})
    monkeypatch.setattr(legacy, 'compare_to_baseline', lambda *args: {})
    result = legacy.run_one_scenario({'scenario_id': 'sample'})
    assert result['skipped'] is True


def test_retired_recollection_cli_returns_nonzero(tmp_path):
    import json
    import shutil
    import sys

    entry = tmp_path / 'compare/scripts/run/recollect_single.py'
    entry.parent.mkdir(parents=True)
    shutil.copy2(SOURCE, entry)
    manifest = tmp_path / 'datasets/FuxiBench/prompt_inputs/manifest.json'
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({'entries': [{'scenario_id': 'sample'}]}))
    result = subprocess.run([sys.executable, str(entry), '--scenario', 'sample', '--no-skip'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0, result.stdout
    assert 'retired' in result.stdout
