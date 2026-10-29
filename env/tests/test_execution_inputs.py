"""Launch-bound snapshots preserve delivered bytes, not inferred labels."""

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from mtagentlens_benchmark.engine import process_audit


def capture(root):
    return process_audit.capture_launch_inputs(
        prompt="read the prepared input", claude_args=["-p", "read the prepared input", "--model", "test"],
        command=["test-client", "-p", "read the prepared input", "--model", "test"],
        cwd=root, fixtures_dir=root / "fixtures")


def test_launch_snapshot_binds_prompt_argv_and_exact_binary_fixture_bytes(tmp_path):
    fixtures = tmp_path / "fixtures"
    (fixtures / "nested").mkdir(parents=True)
    path = fixtures / "nested" / "input.bin"
    path.write_bytes(b"\x00\xff\noriginal")
    path.chmod(0o750)
    captured = capture(tmp_path)
    snapshot = captured["input_snapshot"]
    assert snapshot["schema"] == "mtagentlens.launch-inputs/v1"
    assert snapshot["cwd"] == str(tmp_path.resolve())
    assert snapshot["prompt"] == snapshot["claude_args"][1]
    assert snapshot["command"][0] == "test-client"
    entry = snapshot["fixtures"]["files"]["nested/input.bin"]
    assert base64.b64decode(entry["content_base64"], validate=True) == path.read_bytes()
    assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert entry["mode"] == 0o750
    assert set(snapshot["fixtures"]["directories"]) == {".", "nested"}
    expected = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":")).encode()).hexdigest()
    assert captured["input_sha256"] == expected
    path.write_bytes(b"changed after launch")
    assert base64.b64decode(entry["content_base64"], validate=True) == b"\x00\xff\noriginal"
    assert capture(tmp_path)["input_sha256"] != captured["input_sha256"]


def test_missing_and_empty_fixture_trees_are_distinct(tmp_path):
    assert capture(tmp_path)["input_snapshot"]["fixtures"] is None
    (tmp_path / "fixtures").mkdir()
    tree = capture(tmp_path)["input_snapshot"]["fixtures"]
    assert tree["files"] == {}
    assert set(tree["directories"]) == {"."}


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_non_regular_inputs_fail_without_following_or_blocking(tmp_path, kind):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    target = fixtures / "input"
    if kind == "symlink":
        target.symlink_to(tmp_path / "outside")
    else:
        os.mkfifo(target)
    with pytest.raises(ValueError):
        capture(tmp_path)


def test_fixture_changes_during_capture_are_rejected(tmp_path, monkeypatch):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    path = fixtures / "input"
    path.write_bytes(b"initial")
    original = process_audit._read_file

    def changing(source):
        value = original(source)
        if source == path:
            path.write_bytes(b"mutated")
        return value

    monkeypatch.setattr(process_audit, "_read_file", changing)
    with pytest.raises(ValueError, match="(?i)(changed|unstable)"):
        capture(tmp_path)


def test_native_launch_contains_effective_inputs_and_freezes_before_workload_changes(tmp_path, monkeypatch):
    from env.tests.test_process_audit import run_native
    _, _, rows, _ = run_native(tmp_path, monkeypatch, count=2)
    assert {row["schema"] for row in rows} == {"mtagentlens.runner-event/v4"}
    attempts = [row for row in rows if row["event_type"] == "launch_attempt"]
    assert len(attempts) == 2
    for attempt in attempts:
        details = attempt["details"]
        snapshot = details["input_snapshot"]
        assert snapshot["prompt"] == "harmless"
        assert snapshot["claude_args"][1] == "harmless"
        assert snapshot["command"][0] == str(tmp_path / "test-client")
        assert snapshot["fixtures"] is None
        assert details["input_sha256"] == hashlib.sha256(json.dumps(
            snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()).hexdigest()


@pytest.mark.parametrize('args,command', [([], ['client']), (['-p', 'other'], ['client']),
    ('-p prompt', ['client']), (['-p', 'read the prepared input'], [])])
def test_invalid_launch_arguments_are_rejected_before_capture(tmp_path, args, command):
    with pytest.raises(ValueError):
        process_audit.capture_launch_inputs('read the prepared input', args, command,
                                           tmp_path, tmp_path / 'fixtures')


@pytest.mark.parametrize('kind', ['symlink', 'fifo'])
def test_file_swapped_after_inventory_cannot_be_followed_or_block_read(tmp_path, monkeypatch, kind):
    fixtures = tmp_path / 'fixtures'
    fixtures.mkdir()
    path = fixtures / 'input'
    path.write_bytes(b'initial')
    original = process_audit._read_file

    def swapped(source):
        path.unlink()
        if kind == 'fifo':
            os.mkfifo(path)
        else:
            outside = tmp_path / 'outside'
            outside.write_bytes(b'private outside bytes')
            path.symlink_to(outside)
        return original(source)

    monkeypatch.setattr(process_audit, '_read_file', swapped)
    with pytest.raises((ValueError, OSError)):
        capture(tmp_path)


def test_each_real_launch_freezes_the_fixture_state_left_by_the_previous_client(tmp_path, monkeypatch):
    from env.tests.test_process_audit import run_native
    fixtures = tmp_path / 'run/fixtures'
    fixtures.mkdir(parents=True)
    (fixtures / 'input.bin').write_bytes(b'first input')
    success, _, rows, run = run_native(tmp_path, monkeypatch, count=2, client_body=(
        "print(json.dumps({'prompt':sys.argv[sys.argv.index('-p')+1],"
        "'input':Path('fixtures/input.bin').read_bytes().hex()}))\n"
        "Path('fixtures/input.bin').write_bytes(b'second input')\n"
    ))
    assert success
    attempts = [row for row in rows if row['event_type'] == 'launch_attempt']
    for index, expected in enumerate((b'first input', b'second input')):
        snapshot = attempts[index]['details']['input_snapshot']
        entry = snapshot['fixtures']['files']['input.bin']
        assert base64.b64decode(entry['content_base64']) == expected
        assert snapshot['prompt'] == 'harmless'
        observed = json.loads((run / 'logs' / f'agent_{index}.log').read_text().splitlines()[1])
        assert observed == {'prompt':'harmless','input':expected.hex()}
    assert (run / 'runner-events.jsonl').stat().st_mode & 0o077 == 0


def test_scenario_setup_and_path_rewrites_are_captured_from_the_real_launch_boundary(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from mtagentlens_benchmark.engine import executor
    from mtagentlens_benchmark.types import AgentSpec, ScenarioConfig, ScenarioMode
    from tools.annotator.runner_import import import_runner_bundle
    from tools.annotator.tests.test_evidence import fixture_inputs

    source = tmp_path / 'source'
    source.mkdir()
    original = b'path={{FIXTURE_DIR}}/document.txt\n'
    (source / 'document.txt').write_bytes(original)
    setup = tmp_path / 'prepare.sh'
    setup.write_text('printf prepared > "$FIXTURE_DIR/setup.txt"\n')
    client = tmp_path / 'client'
    client.write_text(f'#!{sys.executable}\nimport sys,json\nprint(json.dumps(sys.argv))\n')
    client.chmod(0o700)
    monkeypatch.setattr(executor, '_find_claude_binary_with_diagnostics', lambda: (str(client), {}))
    monkeypatch.setattr(executor, '_apply_benchmark_env', lambda *args: None)
    monkeypatch.setattr(executor, '_capture_agent_artifacts', lambda *args: None)
    monkeypatch.setattr(executor.os, 'getuid', lambda: 1000)
    monkeypatch.delenv('MTAGENTLENS_CLEAN_PROFILE', raising=False)
    monkeypatch.setattr(executor, 'Path', lambda value: tmp_path / 'pid'
                        if value == '/tmp/mtagentlens-agent-pid' else Path(value))
    config = ScenarioConfig(id='scenario', mode=ScenarioMode.SINGLE_AGENT, description='Synthetic launch',
        agents=[AgentSpec(role_file='role', task='Read {{FIXTURE_DIR}}/document.txt')],
        fixtures_dir=source, pre_run_script=str(setup))
    run = tmp_path / 'run'
    result = executor.execute_scenario(config, run, SimpleNamespace(root=tmp_path))
    assert result.success, result.error
    rows = [json.loads(line) for line in (run / 'runner-events.jsonl').read_text().splitlines()]
    snapshot = next(row['details']['input_snapshot'] for row in rows if row['event_type']=='launch_attempt')
    assert snapshot['prompt'] == 'Read ' + str(run / 'fixtures/document.txt')
    observed = json.loads((run / 'logs/agent_0.log').read_text().splitlines()[0])
    assert observed[observed.index('-p')+1] == snapshot['prompt']
    files = snapshot['fixtures']['files']
    assert base64.b64decode(files['document.txt']['content_base64']) == (run/'fixtures/document.txt').read_bytes()
    assert base64.b64decode(files['setup.txt']['content_base64']) == b'prepared'
    assert (source / 'document.txt').read_bytes() == original
    release = fixture_inputs(tmp_path)['release_root']
    manifest = import_runner_bundle(tmp_path/'bundle', release_root=release,
                                    ledger=run/'runner-events.jsonl', codebook_version='pilot/1')
    assert manifest['binding']['run_id'] == result.run_id
    assert manifest['binding']['source_run_uuid'] == result.summary['source_run_uuid']
