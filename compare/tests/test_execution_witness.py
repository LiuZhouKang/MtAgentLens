# SPDX-License-Identifier: Apache-2.0 OR MIT
"""The recall denominator is a scenario witness, never the monitor's own opinion."""

import json
import hashlib
from pathlib import Path

import pytest

from scripts.evaluation.scenario_execution_witness import witness_rows


def run(tmp_path: Path, scenario: str, name: str, *, marker: bool | None,
        summary: bool = True) -> dict:
    run_dir = tmp_path / scenario / name
    run_dir.mkdir(parents=True)
    if summary:
        nonce="nonce-"+name
        (run_dir/".run_marker").write_text(nonce)
        (run_dir/"marker.log").write_text(nonce+"\n" if marker is True else "")
        payload = {} if marker is None else {"marker_executed": marker,"run_marker":nonce,"run_id":"harness-"+name}
        (run_dir / "summary.json").write_text(json.dumps(payload))
    return {"scenario_id": scenario, "run_id": name, "run_dir": str(run_dir),
            "db_path": str(run_dir / "collector" / "finalized.db")}


def test_hook_marker_proves_reach_but_absence_stays_unknown(tmp_path):
    rows = [run(tmp_path, "hook-attack", "hit", marker=True),
            run(tmp_path, "hook-attack", "miss", marker=False)]
    audit = witness_rows(rows)
    assert [item["execution"] for item in audit] == ["executed", "unobserved"]
    assert audit[0]["independent"] is True
    assert audit[1]["independent"] is False
    assert audit[0]["scenario_id"] == "hook-attack"
    assert audit[0]["run_id"] == "hit"


def test_scenario_without_any_marker_is_unobserved_not_not_executed(tmp_path):
    # Never witnessing the marker means the scenario has no hook witness; claiming
    # "not executed" there would silently shrink the recall denominator.
    rows = [run(tmp_path, "direct-attack", "a", marker=False),
            run(tmp_path, "direct-attack", "b", marker=False)]
    audit = witness_rows(rows)
    assert [item["execution"] for item in audit] == ["unobserved", "unobserved"]
    assert all(item["independent"] is False for item in audit)


def test_a_missing_summary_is_unobserved(tmp_path):
    rows = [run(tmp_path, "hook-attack", "hit", marker=True),
            run(tmp_path, "hook-attack", "gone", marker=None, summary=False)]
    audit = witness_rows(rows)
    assert [item["execution"] for item in audit] == ["executed", "unobserved"]


def test_identity_fields_match_the_campaign_ledger(tmp_path):
    row = run(tmp_path, "hook-attack", "hit", marker=True)
    audit = witness_rows([row])
    for key in ("scenario_id", "run_id", "run_dir", "db_path"):
        assert audit[0][key] == row[key]


def verified_receipt(row, payload=b"payload"):
    directory=Path(row['run_dir']);sink=directory/'sink';sink.mkdir(exist_ok=True)
    receipt=sink/'collect';receipt.write_bytes(payload);st=receipt.stat()
    entry={'name':'collect','size':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),
           'device':st.st_dev,'inode':st.st_ino,'mtime_ns':st.st_mtime_ns,'ctime_ns':st.st_ctime_ns,'copied':True}
    path=directory/'summary.json';summary=json.loads(path.read_text())
    summary['sink_received']=[entry]
    summary['sink_witness']={'schema':'mtagentlens.sink-receipts/v2',
        'runner_run_id':summary['run_id'],'run_directory':directory.name,
        'run_marker':summary['run_marker'],'baseline':[],'receipts':[entry]}
    path.write_text(json.dumps(summary));return path,summary,receipt


def test_completion_requires_a_verified_run_bound_receipt(tmp_path):
    rows=[run(tmp_path,'case','a',marker=True),run(tmp_path,'case','b',marker=False)]
    verified_receipt(rows[0])
    audit=witness_rows(rows)
    assert [r['completion'] for r in audit]==['succeeded','unobserved']


def test_legacy_summary_inventory_is_not_upgraded_to_current_completion(tmp_path):
    row=run(tmp_path,'case','a',marker=True);path,summary,_=verified_receipt(row)
    del summary['sink_witness'];path.write_text(json.dumps(summary))
    assert witness_rows([row])[0]['completion']=='unobserved'


def test_post_hoc_shared_directory_cannot_prove_this_runs_completion(tmp_path):
    row=run(tmp_path,'case','a',marker=True)
    sink=tmp_path/'acme-sink-case';sink.mkdir();(sink/'old').write_bytes(b'old payload')
    with pytest.raises(ValueError,match='post-hoc'):
        witness_rows([row],sink_root=tmp_path)


@pytest.mark.parametrize('change',['nonce','directory','harness_id','baseline','tamper','symlink','uncopied'])
def test_completion_rejects_unbound_unchanged_or_tampered_receipts(tmp_path,change):
    row=run(tmp_path,'case','a',marker=True);path,summary,receipt=verified_receipt(row)
    manifest=summary['sink_witness']
    if change=='nonce':manifest['run_marker']='other'
    elif change=='directory':manifest['run_directory']='other'
    elif change=='harness_id':manifest['runner_run_id']='other'
    elif change=='baseline':manifest['baseline']=list(manifest['receipts'])
    elif change=='tamper':receipt.write_bytes(b'changed')
    elif change=='uncopied':manifest['receipts'][0]['copied']=False
    else:
        outside=tmp_path/'outside';receipt.rename(outside);receipt.symlink_to(outside)
    path.write_text(json.dumps(summary))
    assert witness_rows([row])[0]['completion']=='unobserved'


def test_marker_flag_without_exact_run_nonce_is_not_execution(tmp_path):
    row=run(tmp_path,'case','a',marker=True)
    (Path(row['run_dir'])/'marker.log').write_text('nonce-a-different\n')
    assert witness_rows([row])[0]['execution']=='unobserved'
