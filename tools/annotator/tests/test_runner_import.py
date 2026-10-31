"""Native launch evidence is indexed without inventing internal actor truth."""

import hashlib
import json
import uuid

import pytest

from tools.annotator.evidence import load_bundle
from tools.annotator.records import RecordStore
from tools.annotator.runner_import import _release_id_matches, import_runner_bundle
from tools.annotator.tests.test_evidence import fixture_inputs


def native_rows(binding):
    base = {"schema": "mtagentlens.runner-event/v1", "producer": "process-audit/1@sha256:" + "a" * 64,
            "run_id": binding["run_id"], "scenario_id": binding["scenario_id"],
            "source_run_uuid": binding["source_run_uuid"], "clock_domain": "runner-monotonic-ns"}
    key = {"pid": 42, "start_ticks": None, "boot_id": None, "ticks_per_second": 100}
    entries = [("run_started", None, None, {}),
               ("launch_attempt", "runner-agent-0", None, {"agent_index": 0}),
               ("process_started", "runner-agent-0", key, {}),
               ("process_exited", "runner-agent-0", key, {"exit_code": 0}),
               ("run_finished", None, None, {"state": "completed"})]
    return [dict(base, event_id=uuid.uuid4().hex, event_type=kind, timestamp_ns=index,
                 actor_id=actor, process_key=process, details=details)
            for index, (kind, actor, process, details) in enumerate(entries)]


def write_rows(path, rows):
    path.write_bytes(b"".join((json.dumps(row, ensure_ascii=False) + "\n").encode() for row in rows))


@pytest.fixture
def inputs(tmp_path):
    spec = fixture_inputs(tmp_path)
    rows = native_rows(spec["binding"])
    ledger = tmp_path / "runner-events.jsonl"
    write_rows(ledger, rows)
    return spec, ledger, rows


def build(tmp_path, inputs):
    spec, ledger, _ = inputs
    return import_runner_bundle(tmp_path / "bundle", release_root=spec["release_root"],
                                ledger=ledger, codebook_version=spec["binding"]["codebook_version"])


def test_historical_release_is_accepted_only_with_the_explicit_flag():
    assert _release_id_matches("a" * 64, "a" * 64, False)
    assert _release_id_matches("a" * 64, "a" * 64, True)
    assert not _release_id_matches("b" * 64, "a" * 64, False)
    assert _release_id_matches("b" * 64, "a" * 64, True)


def test_import_preserves_native_identity_scope_raw_ranges_and_registered_queue(tmp_path, inputs):
    spec, ledger, rows = inputs
    result = build(tmp_path, inputs)
    assert result == load_bundle(tmp_path / "bundle")
    assert result["binding"] == spec["binding"]
    assert result["actors"] == ["runner-agent-0"]
    raw = ledger.read_bytes()
    assert len(result["events"]) == len(rows)
    for event, row in zip(result["events"], rows):
        assert event["record_id"] == row["event_id"]
        assert event["event_id"] == row["event_id"]
        segment = raw[event["byte_offset"]:event["byte_offset"] + event["byte_length"]]
        assert hashlib.sha256(segment).hexdigest() == event["record_sha256"]
        assert json.loads(segment) == row
    store = RecordStore(tmp_path / "records.sqlite")
    store.register_bundle(tmp_path / "bundle")
    task = store.export()["tasks"][0]
    assert task["status"] == "unannotated"
    assert task["revisions"] == []
    assert "identities" not in result


@pytest.mark.parametrize("change", ["mixed_run", "mixed_uuid", "mixed_scenario", "mixed_producer",
    "duplicate_event", "missing_finish", "unknown_kind", "invalid_process", "huge_pid",
    "time_reversed", "reused_actor", "unfinished_completed", "unknown_schema"])
def test_invalid_native_ledgers_fail_before_creating_output(tmp_path, inputs, change):
    _, ledger, rows = inputs
    if change.startswith("mixed_"):
        key = {"mixed_run": "run_id", "mixed_uuid": "source_run_uuid",
               "mixed_scenario": "scenario_id", "mixed_producer": "producer"}[change]
        rows[2][key] = "other"
    elif change == "duplicate_event":
        rows[2]["event_id"] = rows[1]["event_id"]
    elif change == "missing_finish":
        rows.pop()
    elif change == "unknown_kind":
        rows[2]["event_type"] = "unknown"
    elif change == "invalid_process":
        rows[2]["process_key"] = dict(rows[2]["process_key"], pid=True)
    elif change == "huge_pid":
        rows[2]["process_key"] = dict(rows[2]["process_key"], pid=2**63)
    elif change == "time_reversed":
        rows[3]["timestamp_ns"] = 0
    elif change == "reused_actor":
        rows[2]["event_type"] = "launch_attempt"
        rows[2]["process_key"] = None
        rows[2]["details"] = {"agent_index": 0}
    elif change == "unfinished_completed":
        rows.pop(3)
    else:
        rows[0]["schema"] = "unversioned"
    write_rows(ledger, rows)
    with pytest.raises(ValueError):
        build(tmp_path, inputs)
    assert not (tmp_path / "bundle").exists()


def test_interrupted_launch_scope_keeps_unfinished_attempt_as_evidence(tmp_path, inputs):
    _, ledger, rows = inputs
    rows.pop(3)
    rows[-1]["details"]["state"] = "interrupted"
    write_rows(ledger, rows)
    assert len(build(tmp_path, inputs)["events"]) == 4


def test_exit_zero_is_not_converted_into_attack_success_or_annotations(tmp_path, inputs):
    result = build(tmp_path, inputs)
    assert not {"attempted", "executed", "succeeded", "paper_metrics_available"} & set(result)


def test_changed_source_during_freeze_is_rejected_and_new_output_removed(tmp_path, inputs, monkeypatch):
    from tools.annotator import runner_import
    _, ledger, _ = inputs
    original = runner_import.build_bundle

    def changed(*args, **kwargs):
        with ledger.open("ab") as stream:
            stream.write(b'{"late":"record"}\n')
        return original(*args, **kwargs)

    monkeypatch.setattr(runner_import, "build_bundle", changed)
    with pytest.raises(ValueError):
        build(tmp_path, inputs)
    assert not (tmp_path / "bundle").exists()


def test_native_clock_cannot_start_at_a_negative_value(tmp_path, inputs):
    _, ledger, rows = inputs
    rows[0]["timestamp_ns"] = -1
    write_rows(ledger, rows)
    with pytest.raises(ValueError):
        build(tmp_path, inputs)


def test_timeout_type_is_preserved_even_if_exit_races_with_the_deadline(tmp_path, inputs):
    _, ledger, rows = inputs
    rows[3]["event_type"] = "process_timeout"
    rows[3]["details"]["exit_code"] = 0
    write_rows(ledger, rows)
    result = build(tmp_path, inputs)
    frozen = tmp_path / "bundle" / result["sources"]["runner"]["path"]
    assert json.loads(frozen.read_bytes().splitlines()[3])["event_type"] == "process_timeout"


def test_existing_output_is_preserved_on_import_retry(tmp_path, inputs):
    original = build(tmp_path, inputs)
    before = inputs[1].read_bytes()
    with pytest.raises(ValueError):
        build(tmp_path, inputs)
    assert load_bundle(tmp_path / "bundle") == original
    assert inputs[1].read_bytes() == before


def with_launch_inputs(rows):
    import base64
    for row in rows:
        row['schema'] = 'mtagentlens.runner-event/v2'
        row['producer'] = 'process-audit/2@sha256:' + 'a' * 64
    raw = b'\x00\xff actual input'
    snapshot = {
        'schema': 'mtagentlens.launch-inputs/v1', 'cwd': '/private/run', 'prompt': 'read fixture',
        'claude_args': ['-p', 'read fixture', '--model', 'test'],
        'command': ['test-client', '-p', 'read fixture', '--model', 'test'],
        'fixtures': {'directories': {'.': 0o700}, 'files': {'input.bin': {
            'mode': 0o600, 'content_base64': base64.b64encode(raw).decode(),
            'sha256': hashlib.sha256(raw).hexdigest()}}},
    }
    rows[1]['details'].update(input_snapshot=snapshot, input_sha256=inputs_hash(snapshot))
    return snapshot


def inputs_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def test_v2_actual_launch_input_bytes_remain_bound_and_readable_after_import(tmp_path, inputs):
    _, ledger, rows = inputs
    snapshot = with_launch_inputs(rows)
    write_rows(ledger, rows)
    result = build(tmp_path, inputs)
    store = RecordStore(tmp_path / 'records.sqlite')
    task = store.register_bundle(tmp_path / 'bundle')
    event = result['events'][1]
    frozen = store.read_evidence(task, event['event_id'])['bytes']
    assert json.loads(frozen)['details']['input_snapshot'] == snapshot
    assert result['binding']['source_run_uuid'] == rows[0]['source_run_uuid']
    assert store.export()['tasks'][0]['revisions'] == []


@pytest.mark.parametrize('change', ['snapshot_digest', 'bytes_digest', 'bad_base64', 'path_escape',
    'missing_directory', 'mode_bool', 'prompt_mismatch', 'mixed_version', 'wrong_producer',
    'missing_snapshot', 'extra_snapshot_field'])
def test_invalid_v2_inputs_fail_before_creating_annotation_bundle(tmp_path, inputs, change):
    _, ledger, rows = inputs
    snapshot = with_launch_inputs(rows)
    entry = snapshot['fixtures']['files']['input.bin']
    if change == 'snapshot_digest':
        snapshot['prompt'] = snapshot['claude_args'][1] = 'changed prompt'
    elif change == 'bytes_digest':
        entry['sha256'] = 'f' * 64
    elif change == 'bad_base64':
        entry['content_base64'] = '!!!!'
    elif change == 'path_escape':
        snapshot['fixtures']['files']['../escape'] = snapshot['fixtures']['files'].pop('input.bin')
    elif change == 'missing_directory':
        snapshot['fixtures']['files']['nested/input.bin'] = snapshot['fixtures']['files'].pop('input.bin')
    elif change == 'mode_bool':
        entry['mode'] = True
    elif change == 'prompt_mismatch':
        snapshot['claude_args'][1] = 'different input'
    elif change == 'mixed_version':
        rows[2]['schema'] = 'mtagentlens.runner-event/v1'
    elif change == 'wrong_producer':
        for row in rows:
            row['producer'] = 'process-audit/1@sha256:' + 'a' * 64
    elif change == 'missing_snapshot':
        rows[1]['details'].pop('input_snapshot')
    else:
        snapshot['authentication'] = 'not authenticated'
    if change != 'snapshot_digest':
        rows[1]['details']['input_sha256'] = inputs_hash(snapshot)
    write_rows(ledger, rows)
    with pytest.raises(ValueError):
        build(tmp_path, inputs)
    assert not (tmp_path / 'bundle').exists()



def with_process_observation(rows):
    with_launch_inputs(rows)
    for row in rows:
        row["schema"] = "mtagentlens.runner-event/v3"
        row["producer"] = "process-audit/3@sha256:" + "a" * 64
    key = rows[2]["process_key"]
    key["start_ticks"] = 777
    namespaces = {name: {"device": 4, "inode": 4026531836 + index}
                  for index, name in enumerate(("pid", "pid_for_children", "time", "time_for_children", "mnt"))}
    sample = {"schema": "mtagentlens.process-observation/v1", "sample_before_ns": rows[1]["timestamp_ns"],
              "sample_after_ns": rows[2]["timestamp_ns"], "boottime_ns": 9876543210, "status": "observed",
              "observer": {"pid": 101, "procfs_pid": 101, "nspid": [101],
                           "namespaces": namespaces, "time_offsets_ns": {"monotonic": 0, "boottime": 0}},
              "target": {"procfs_pid": key["pid"], "start_ticks_before": key["start_ticks"],
                         "start_ticks_after": key["start_ticks"], "nspid": [key["pid"]],
                         "namespaces": namespaces, "time_offsets_ns": {"monotonic": 0, "boottime": 0}}}
    rows[2]["details"] = {"observation": sample}
    return sample


def test_v3_observation_survives_import_without_claiming_identity_alignment(tmp_path, inputs):
    _, ledger, rows = inputs
    sample = with_process_observation(rows)
    write_rows(ledger, rows)
    result = build(tmp_path, inputs)
    frozen = tmp_path / "bundle" / result["sources"]["runner"]["path"]
    assert json.loads(frozen.read_bytes().splitlines()[2])["details"]["observation"] == sample
    assert "identities" not in result
    assert "event_alignment_available" not in result["binding"]


@pytest.mark.parametrize("change", ["missing", "legacy_smuggling", "bad_schema", "extra_field", "bool_clock",
    "clock_before_launch", "clock_after_start", "reversed_clock", "bool_offset", "bad_namespace", "bool_inode",
    "missing_namespace", "unknown_status", "observer_space", "observer_nspid", "wrong_target", "reused_pid",
    "target_nspid", "false_unknown", "unavailable_target_in_wrong_space"])
def test_invalid_process_observation_is_rejected_before_bundle_creation(tmp_path, inputs, change):
    _, ledger, rows = inputs
    sample = with_process_observation(rows)
    if change == "missing": rows[2]["details"] = {}
    elif change == "legacy_smuggling":
        for row in rows:
            row["schema"] = "mtagentlens.runner-event/v2"
            row["producer"] = "process-audit/2@sha256:" + "a" * 64
    elif change == "bad_schema": sample["schema"] = "unknown"
    elif change == "extra_field": sample["verified"] = True
    elif change == "bool_clock": sample["sample_before_ns"] = True
    elif change == "clock_before_launch": sample["sample_before_ns"] = rows[1]["timestamp_ns"] - 1
    elif change == "clock_after_start": sample["sample_after_ns"] = rows[2]["timestamp_ns"] + 1
    elif change == "reversed_clock": sample["sample_before_ns"] = sample["sample_after_ns"] + 1
    elif change == "bool_offset": sample["observer"]["time_offsets_ns"]["monotonic"] = True
    elif change == "bad_namespace": sample["observer"]["namespaces"]["pid"] = {"inode": -1, "device": 4}
    elif change == "bool_inode": sample["observer"]["namespaces"]["pid"]["inode"] = True
    elif change == "missing_namespace": sample["target"]["namespaces"].pop("time")
    elif change == "unknown_status": sample["status"] = "aligned"
    elif change == "observer_space": sample["observer"]["procfs_pid"] = 999
    elif change == "observer_nspid": sample["observer"]["nspid"] = [999, 101]
    elif change == "wrong_target": sample["target"]["procfs_pid"] += 1
    elif change == "reused_pid": sample["target"]["start_ticks_after"] += 1
    elif change == "target_nspid": sample["target"]["nspid"] = [999]
    elif change == "false_unknown": sample["status"] = "unavailable"
    else:
        sample["observer"]["procfs_pid"] = 999
        sample["status"] = "unavailable"
        rows[2]["process_key"]["start_ticks"] = None
    write_rows(ledger, rows)
    with pytest.raises(ValueError): build(tmp_path, inputs)
    assert not (tmp_path / "bundle").exists()


@pytest.mark.parametrize("status", ["unavailable", "unstable", "pid_space_mismatch"])
def test_incomplete_v3_observation_preserves_unknown_key(tmp_path, inputs, status):
    _, ledger, rows = inputs
    sample = with_process_observation(rows)
    sample["status"] = status
    rows[2]["process_key"]["start_ticks"] = None
    if status == "pid_space_mismatch":
        sample["observer"]["procfs_pid"] = 999
        sample["target"] = None
    elif status == "unavailable": sample["target"] = None
    else: sample["target"]["start_ticks_after"] += 1
    write_rows(ledger, rows)
    result = build(tmp_path, inputs)
    assert result["actors"] == ["runner-agent-0"]


def with_runner_witness(rows):
    """A v4 ledger brackets each launch with a runner self-context sample."""
    sample = with_process_observation(rows)
    for row in rows:
        row["schema"] = "mtagentlens.runner-event/v4"
        row["producer"] = "process-audit/4@sha256:" + "a" * 64
    witness = {
        "schema": "mtagentlens.runner-context/v1",
        "sample_before_ns": rows[1]["timestamp_ns"] - 1,
        "sample_after_ns": rows[1]["timestamp_ns"],
        "boottime_ns": sample["boottime_ns"],
        "boot_id": "d32c240e-0fd1-45e0-83bf-7cb2d2a39d32",
        "status": "observed",
        "observer": json.loads(json.dumps(sample["observer"])),
    }
    rows[1]["details"]["runner_context"] = witness
    return witness


def test_v4_runner_clock_witness_survives_import_without_claiming_alignment(tmp_path, inputs):
    _, ledger, rows = inputs
    witness = with_runner_witness(rows)
    write_rows(ledger, rows)
    result = build(tmp_path, inputs)
    frozen = tmp_path / "bundle" / result["sources"]["runner"]["path"]
    assert json.loads(frozen.read_bytes().splitlines()[1])["details"]["runner_context"] == witness
    assert "identities" not in result
    assert "event_alignment_available" not in result["binding"]


def test_v4_unavailable_runner_witness_is_kept_as_an_unknown(tmp_path, inputs):
    _, ledger, rows = inputs
    witness = with_runner_witness(rows)
    witness["status"] = "unavailable"
    witness["boot_id"] = None
    witness["observer"]["procfs_pid"] = None
    witness["observer"]["nspid"] = None
    write_rows(ledger, rows)
    result = build(tmp_path, inputs)
    frozen = tmp_path / "bundle" / result["sources"]["runner"]["path"]
    assert json.loads(frozen.read_bytes().splitlines()[1])["details"]["runner_context"] == witness


@pytest.mark.parametrize("change", ["missing", "legacy_smuggling", "bad_schema", "extra_field",
    "bool_clock", "negative_clock", "clock_after_attempt", "reversed_clock", "bad_boot_id",
    "bool_boottime", "negative_boottime", "unknown_status", "claimed_space", "observer_nspid",
    "false_pid_space_mismatch", "wrong_producer"])
def test_invalid_runner_clock_witness_is_rejected_before_bundle_creation(tmp_path, inputs, change):
    _, ledger, rows = inputs
    witness = with_runner_witness(rows)
    if change == "missing":
        rows[1]["details"].pop("runner_context")
    elif change == "legacy_smuggling":
        for row in rows:
            row["schema"] = "mtagentlens.runner-event/v3"
            row["producer"] = "process-audit/3@sha256:" + "a" * 64
    elif change == "bad_schema": witness["schema"] = "mtagentlens.runner-context/v2"
    elif change == "extra_field": witness["verified"] = True
    elif change == "bool_clock": witness["sample_before_ns"] = True
    elif change == "negative_clock": witness["sample_before_ns"] = -1
    elif change == "clock_after_attempt":
        witness["sample_after_ns"] = rows[1]["timestamp_ns"] + 1
    elif change == "reversed_clock":
        witness["sample_before_ns"] = witness["sample_after_ns"] + 1
    elif change == "bad_boot_id": witness["boot_id"] = "not-a-uuid"
    elif change == "bool_boottime": witness["boottime_ns"] = True
    elif change == "negative_boottime": witness["boottime_ns"] = -1
    elif change == "unknown_status": witness["status"] = "stable"
    elif change == "claimed_space": witness["observer"]["procfs_pid"] = 999
    elif change == "observer_nspid": witness["observer"]["nspid"] = [999]
    elif change == "false_pid_space_mismatch":
        witness["status"] = "pid_space_mismatch"
    else:
        for row in rows:
            row["producer"] = "process-audit/3@sha256:" + "a" * 64
    write_rows(ledger, rows)
    with pytest.raises(ValueError): build(tmp_path, inputs)
    assert not (tmp_path / "bundle").exists()


def test_unbound_dataset_runner_import_retains_source_checks_and_store(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from env.tests.test_release_input_chain import source_release, configure_client
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    from tools.annotator.records import RecordStore

    release, _, config = source_release(tmp_path)
    (release / 'release.json').unlink()
    unrelated = release / 'bench/unrelated.sh'
    unrelated.write_text('unrelated before run')
    executor = configure_client(tmp_path, monkeypatch)
    run = tmp_path / 'run'
    result = executor.execute_scenario(config, run, SimpleNamespace(root=tmp_path),
        input_audit_factory=lambda conf, directory: ReleaseInputAudit(release, None, conf, directory))
    assert result.success, result.error
    ledger = run / 'runner-events.jsonl'
    unrelated.write_text('unrelated after run')
    imported = import_runner_bundle(tmp_path / 'bundle', release_root=release,
                                    ledger=ledger, codebook_version='pilot/1')
    assert imported['schema'] == 'mtagentlens.evidence-bundle/v2'
    assert imported['binding']['data_release_id'] is None
    store = RecordStore(tmp_path / 'records.sqlite')
    store.register_bundle(tmp_path / 'bundle')
    assert store.export()['tasks'][0]['binding']['data_release_id'] is None
    # A changed source is still rejected; unbound is not proof of an old release.
    (release / 'fixtures/neutral/input.txt').write_text('changed source')
    with pytest.raises(ValueError, match='(?i)(source|input|fixture)'):
        import_runner_bundle(tmp_path / 'changed', release_root=release,
                             ledger=ledger, codebook_version='pilot/1')


def test_unbound_run_without_recorded_source_is_not_treated_as_bound(tmp_path, inputs):
    spec, ledger, _ = inputs
    (spec['release_root'] / 'release.json').unlink()
    with pytest.raises(ValueError, match='unbound.*input reference'):
        import_runner_bundle(tmp_path / 'bundle-unbound', release_root=spec['release_root'],
                             ledger=ledger, codebook_version='pilot/1')
    assert not (tmp_path / 'bundle-unbound').exists()
