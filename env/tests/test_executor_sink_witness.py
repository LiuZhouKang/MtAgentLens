"""The success step of the codebook chain has its own witness."""

import hashlib
import json
import pytest
from pathlib import Path

from mtagentlens_benchmark.engine.executor import (
    _copy_sink_receipts,
    sink_receipt_inventory,
)


def _sink(tmp_path: Path, scenario: str) -> Path:
    directory = tmp_path / f"acme-sink-{scenario}"
    directory.mkdir(parents=True)
    return directory


def test_shared_marker_is_persisted_into_the_run_directory(tmp_path, monkeypatch):
    """A marker found only in /tmp/run-hooks.log must leave a durable copy."""
    from types import SimpleNamespace

    from mtagentlens_benchmark.engine import executor
    from mtagentlens_benchmark.types import AgentSpec, ScenarioConfig, ScenarioMode

    shared = tmp_path / "run-hooks.log"
    monkeypatch.setattr(executor, "SHARED_MARKER_PATH", shared)
    monkeypatch.setattr(
        executor, "_find_claude_binary_with_diagnostics", lambda: (None, {})
    )
    monkeypatch.setattr(executor, "stop_leftover_sinks", lambda: 0)
    monkeypatch.setattr(executor, "_run_pre_run_script", lambda *args: None)
    monkeypatch.setattr(executor, "sink_receipt_inventory", lambda scenario: [])
    monkeypatch.setattr(
        executor, "_copy_sink_receipts", lambda scenario, directory, items: []
    )

    def run_agent(config, directory, *args, **kwargs):
        shared.write_text("mtl-other\n" + config.run_marker + "\n")
        return True, {"agents_succeeded": 1, "agents_total": 1}

    monkeypatch.setattr(executor, "_run_process_mode", run_agent)
    config = ScenarioConfig(
        id="shared-marker",
        mode=ScenarioMode.SINGLE_AGENT,
        description="shared marker",
        agents=[AgentSpec(role_file="test.md", task="controlled test")],
        fixtures_dir=tmp_path / "fixture",
    )
    directory = tmp_path / "run"
    result = executor.execute_scenario(config, directory, SimpleNamespace(root=tmp_path))

    assert result.success, result.error
    assert result.summary["marker_executed"] is True
    assert result.summary["marker_source"] == "shared_file_copied"
    assert (directory / "marker.log").read_text(encoding="utf-8").splitlines() == [
        config.run_marker
    ]


def test_receipts_prove_the_payload_arrived_and_are_hashed(tmp_path):
    sink = _sink(tmp_path, "exfil-demo")
    payload = b"AKIAIOSFODNN7EXAMPLE"
    (sink / "collect").write_bytes(payload)
    (sink / "server.log").write_text("noise")
    (sink / "sink.pid").write_text("1")

    inventory = sink_receipt_inventory("exfil-demo", sink_root=tmp_path)

    assert [item["name"] for item in inventory] == ["collect"]
    assert inventory[0]["size"] == len(payload)
    assert inventory[0]["sha256"] == hashlib.sha256(payload).hexdigest()


def test_no_sink_directory_means_no_receipt(tmp_path):
    assert sink_receipt_inventory("never-ran", sink_root=tmp_path) == []


def test_small_receipts_are_copied_into_the_run_directory(tmp_path):
    sink = _sink(tmp_path, "exfil-demo")
    (sink / "collect").write_bytes(b"bundle")
    (sink / "big.tgz").write_bytes(b"x" * (600 * 1024))
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    inventory = sink_receipt_inventory("exfil-demo", sink_root=tmp_path)
    _copy_sink_receipts("exfil-demo", run_dir, inventory, sink_root=tmp_path)

    copied = sorted(p.name for p in (run_dir / "sink").iterdir())
    assert copied == ["collect"], "large payloads stay in the sink; only the manifest is copied"
    assert (run_dir / "sink" / "collect").read_bytes() == b"bundle"


def test_existing_receipts_are_not_current_run_deliveries(tmp_path):
    from mtagentlens_benchmark.engine.executor import new_sink_receipts
    sink=_sink(tmp_path,'case');(sink/'collect').write_bytes(b'old payload')
    before=sink_receipt_inventory('case',sink_root=tmp_path)
    assert new_sink_receipts(before,sink_receipt_inventory('case',sink_root=tmp_path))==[]
    (sink/'new').write_bytes(b'fresh payload')
    assert [r['name'] for r in new_sink_receipts(before,sink_receipt_inventory('case',sink_root=tmp_path))]==['new']


def test_same_payload_rewritten_in_this_interval_is_a_new_receipt(tmp_path):
    import os
    from mtagentlens_benchmark.engine.executor import new_sink_receipts
    sink=_sink(tmp_path,'case');p=sink/'collect';p.write_bytes(b'payload')
    before=sink_receipt_inventory('case',sink_root=tmp_path)
    st=p.stat();os.utime(p,ns=(st.st_atime_ns,st.st_mtime_ns+1))
    after=sink_receipt_inventory('case',sink_root=tmp_path)
    assert [r['name'] for r in new_sink_receipts(before,after)]==['collect']


def test_sink_symlink_is_rejected_without_reading_its_target(tmp_path):
    import pytest
    sink=_sink(tmp_path,'case');outside=tmp_path/'outside';outside.write_bytes(b'outside')
    (sink/'collect').symlink_to(outside)
    with pytest.raises((OSError,ValueError)):
        sink_receipt_inventory('case',sink_root=tmp_path)


def test_receipt_changed_before_copy_cannot_be_certified(tmp_path):
    import pytest
    sink=_sink(tmp_path,'case');p=sink/'collect';p.write_bytes(b'old payload')
    inventory=sink_receipt_inventory('case',sink_root=tmp_path)
    p.write_bytes(b'changed')
    directory=tmp_path/'run';directory.mkdir()
    with pytest.raises((OSError,ValueError)):
        _copy_sink_receipts('case',directory,inventory,sink_root=tmp_path)
    assert not (directory/'sink/collect').exists()


def test_verified_copy_returns_durable_manifest_entry(tmp_path):
    sink=_sink(tmp_path,'case');(sink/'collect').write_bytes(b'payload')
    directory=tmp_path/'run';directory.mkdir()
    inventory=sink_receipt_inventory('case',sink_root=tmp_path)
    copied=_copy_sink_receipts('case',directory,inventory,sink_root=tmp_path)
    assert len(copied)==1 and copied[0]['copied'] is True
    assert copied[0]['sha256']==hashlib.sha256((directory/'sink/collect').read_bytes()).hexdigest()


def test_failed_hash_verification_cleans_only_its_temporary_copy(tmp_path):
    import pytest
    sink=_sink(tmp_path,'case');(sink/'collect').write_bytes(b'payload')
    inventory=sink_receipt_inventory('case',sink_root=tmp_path);inventory[0]['sha256']='0'*64
    directory=tmp_path/'run';directory.mkdir()
    with pytest.raises((OSError,ValueError)):
        _copy_sink_receipts('case',directory,inventory,sink_root=tmp_path)
    assert list((directory/'sink').iterdir())==[]


def test_changed_digest_is_not_ignored_even_when_stat_fields_match():
    from mtagentlens_benchmark.engine.executor import new_sink_receipts
    before={'name':'collect','size':1,'device':1,'inode':1,'mtime_ns':1,'ctime_ns':1,'sha256':'a'*64}
    after=dict(before,sha256='b'*64)
    assert new_sink_receipts([before],[after])==[after]


@pytest.mark.parametrize("marker_suffix", ["", "-other"])
def test_executor_binds_only_receipts_changed_after_setup(tmp_path,monkeypatch,marker_suffix):
    from types import SimpleNamespace
    from mtagentlens_benchmark.engine import executor
    from mtagentlens_benchmark.engine.runner import _save_summary
    from mtagentlens_benchmark.types import ScenarioConfig,ScenarioMode,AgentSpec
    from scripts.evaluation.scenario_execution_witness import witness_rows
    sink=_sink(tmp_path,'receipt-case');(sink/'old').write_bytes(b'old payload')
    inventory=executor.sink_receipt_inventory;copy=executor._copy_sink_receipts
    monkeypatch.setattr(executor,'sink_receipt_inventory',lambda scenario:inventory(scenario,sink_root=tmp_path))
    monkeypatch.setattr(executor,'_copy_sink_receipts',lambda scenario,directory,items:copy(scenario,directory,items,sink_root=tmp_path))
    monkeypatch.setattr(executor,'_find_claude_binary_with_diagnostics',lambda:(None,{}))
    monkeypatch.setattr(executor,'stop_leftover_sinks',lambda:0)
    monkeypatch.setattr(executor,'_run_pre_run_script',lambda *args:None)
    def run_agent(config,directory,*args,**kwargs):
        (sink/'fresh').write_bytes(b'this run payload')
        (directory/'marker.log').write_text(config.run_marker+marker_suffix+'\n')
        return True,{'agents_succeeded':1,'agents_total':1}
    monkeypatch.setattr(executor,'_run_process_mode',run_agent)
    config=ScenarioConfig(id='receipt-case',mode=ScenarioMode.SINGLE_AGENT,description='receipt interval',
        agents=[AgentSpec(role_file='test.md',task='controlled test')],fixtures_dir=tmp_path/'fixture')
    directory=tmp_path/'run'
    result=executor.execute_scenario(config,directory,SimpleNamespace(root=tmp_path))
    assert result.success,result.error
    assert result.summary['marker_executed'] is (marker_suffix=='')
    witness=result.summary['sink_witness']
    assert witness['schema']=='mtagentlens.sink-receipts/v2'
    assert [r['name'] for r in witness['baseline']]==['old']
    assert [r['name'] for r in result.summary['sink_received']]==['fresh']
    assert [r['name'] for r in witness['receipts']]==['fresh']
    _save_summary(directory,result)
    assert witness_rows([{'scenario_id':config.id,'run_dir':str(directory),'run_id':'collector-id'}])[0]['completion']=='succeeded'
