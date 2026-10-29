# SPDX-License-Identifier: Apache-2.0 OR MIT
import json
import sqlite3

import pytest

from scripts.evaluation import overhead_evidence as evidence


def test_observer_cost_is_unknown_if_any_attempt_lacks_usage(tmp_path):
    path = tmp_path / 'collector.stderr'
    records = [{"attempt_id": "a", "model": "deepseek-flash", "outcome": "success",
                "elapsed_ms": 10, "usage": {"input_tokens": 100, "output_tokens": 10}},
               {"attempt_id": "b", "model": "deepseek-flash", "outcome": "timeout",
                "elapsed_ms": 100, "usage": None}]
    path.write_text('\n'.join('[observer:attempt] '+json.dumps(row) for row in records))
    result = evidence.observer_summary(path)
    assert result['attempts'] == 2
    assert result['failed_attempts'] == 1
    assert result['cost_upper_bound_usd'] is None
    assert result['known_cost_upper_bound_usd'] > 0
    assert evidence.observer_summary(tmp_path / 'absent')['attempts'] == 0


def test_alert_alignment_uses_referenced_action_not_nearest_timestamp(tmp_path):
    db = tmp_path / 'db.sqlite'
    with sqlite3.connect(db) as conn:
        conn.executescript('''PRAGMA user_version=5;
        CREATE TABLE actions(action_id,pid,process_start_time_ns,action_type,
          normalized_args_json,outcome,timestamp_ns,run_id);
        CREATE TABLE correlation_evidence(evidence_id,action_id,run_id);
        CREATE TABLE responsibility_claims(claim_id,evidence_refs_json,run_id);
        ''')
        conn.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?)',
            ('action',42,100,'FileOpen','[{"Path":"/fixed/op"}]','succeeded',1000,'run'))
        conn.execute('INSERT INTO correlation_evidence VALUES(?,?,?)',('ev','action','run'))
        conn.execute('INSERT INTO responsibility_claims VALUES(?,?,?)',('claim','["ev"]','run'))
    receipts = [{"receipt_id": "op", "path": "/fixed/op", "pid": 42,
                 "process_start_time_ns": 100, "process_start_resolution_ns": 10,
                 "started_ns": 999, "completed_ns": 1001}]
    emissions = [{"alert_id": "alert", "claim_ids": ["claim"], "phase": "authoritative",
                  "emitted_epoch_ns": 2000, "broadcast_delivered": False}]
    result = evidence.linked_alert_latency(db, receipts, emissions)
    assert result['linked_operations'] == 1
    assert result['samples'][0]['action_id'] == 'action'
    assert result['samples'][0]['latency_ms'] == pytest.approx(.001)
    emissions[0]['claim_ids'] = ['unrelated-nearest-claim']
    assert evidence.linked_alert_latency(db, receipts, emissions)['linked_operations'] == 0


def test_duplicate_attempt_ids_are_rejected(tmp_path):
    path = tmp_path / 'log'
    row = '[observer:attempt] '+json.dumps({'attempt_id': 'same', 'usage': None})+'\n'
    path.write_text(row*2)
    with pytest.raises(ValueError, match='duplicate'):
        evidence.observer_summary(path)


def test_clock_conversion_uses_observed_monotonic_receipt_witness():
    receipt={'started_ns':10_000_000,'completed_ns':10_000_100,
             'monotonic_started_ns':1_000,'monotonic_completed_ns':1_100}
    assert evidence.event_wall_from_witness(receipt,1_050)==10_000_050
    with pytest.raises(ValueError,match='outside'):
        evidence.event_wall_from_witness(receipt,1_200)
