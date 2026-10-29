# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Read real Observer attempts and reference-linked alert emission evidence."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from scripts.evaluation import controlled_capture


def log_records(path, prefix):
    if not Path(path).is_file():
        return []
    records = []
    for line in Path(path).read_text(errors='replace').splitlines():
        if prefix in line:
            value = json.loads(line.split(prefix, 1)[1].strip())
            if not isinstance(value, dict):
                raise ValueError(f'invalid structured record {prefix}')
            records.append(value)
    return records


def token_cost_bound(usage, model):
    if model not in ('deepseek-flash', 'deepseek-v4-flash') or not isinstance(usage, dict):
        return None
    incoming, outgoing = usage.get('input_tokens'), usage.get('output_tokens')
    if any(type(value) is not int or value < 0 for value in (incoming, outgoing)):
        return None
    # Unknown cache partition is priced entirely at the peak cache-miss rate.
    # Add explicitly reported cache input as well; this is an upper bound,
    # never an invoice or an invented zero for a missing response.
    cached = usage.get('cache_read_input_tokens')
    created = usage.get('cache_creation_input_tokens')
    cached = cached if type(cached) is int and cached >= 0 else 0
    created = created if type(created) is int and created >= 0 else 0
    return ((incoming+created)*.30 + cached*.006 + outgoing*1.20)/1e6


def observer_summary(path):
    rows = log_records(path, '[observer:attempt]')
    seen = set()
    costs = []
    for row in rows:
        identity = row.get('attempt_id')
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError('missing or duplicate Observer attempt id')
        seen.add(identity)
        costs.append(token_cost_bound(row.get('usage'), row.get('model')))
    known = sum(value for value in costs if value is not None)
    return {'attempts': len(rows), 'requests': len({row.get('request_id') for row in rows}),
            'failed_attempts': sum(row.get('outcome') != 'success' for row in rows),
            'usage_missing_attempts': sum(value is None for value in costs),
            'known_cost_upper_bound_usd': known,
            'cost_upper_bound_usd': known if all(value is not None for value in costs) else None,
            'attempt_records': rows,
            'latency_ms': [row['elapsed_ms'] for row in rows if isinstance(row.get('elapsed_ms'), (int,float))],
            'pricing_basis': 'DeepSeek peak-rate upper bound; missing usage stays unknown',
            'pricing_source': 'https://api-docs.deepseek.com/quick_start/pricing/',
            'pricing_date': '2026-10-28'}


def event_wall_from_witness(receipt, monotonic_event_ns):
    lower,upper=receipt['monotonic_started_ns'],receipt['monotonic_completed_ns']
    if not lower<=monotonic_event_ns<=upper:
        raise ValueError('raw event falls outside the independently witnessed operation')
    return monotonic_event_ns+receipt['started_ns']-lower


def linked_alert_latency(database, receipts, emissions, trace_path=None):
    """Join receipt -> exact action -> evidence -> claim -> final emitted alert.

    Neither nearest timestamps nor a run's last evidence create a link.
    The endpoint is the collector's final broadcast attempt, not browser receipt.
    """
    unavailable = {'linked_operations': 0, 'total_operations': len(receipts), 'samples': [],
                   'endpoint': 'captured action to final collector broadcast attempt',
                   'browser_receipt_measured': False}
    if database is None or not Path(database).is_file():
        return {**unavailable, 'available': False, 'reason': 'no responsibility database'}
    observations, errors = controlled_capture._read_actions(database)
    if errors:
        return {**unavailable, 'available': False, 'reason': '; '.join(errors)}
    matched = controlled_capture._match_persisted(receipts, observations)
    raw_matched = None
    if trace_path is not None:
        raw,malformed=controlled_capture._read_trace(trace_path)
        raw_matched=controlled_capture._match_raw(receipts,raw) if raw is not None and not malformed else {}
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True) as conn:
        actions = {row[0]: (row[1],row[2]) for row in conn.execute(
            'SELECT action_id,timestamp_ns,run_id FROM actions')}
        evidence = {row[0]: (row[1],row[2]) for row in conn.execute(
            'SELECT evidence_id,action_id,run_id FROM correlation_evidence')}
        claims = {row[0]: (json.loads(row[1]),row[2]) for row in conn.execute(
            'SELECT claim_id,evidence_refs_json,run_id FROM responsibility_claims')}
    linked = {}
    for emission in emissions:
        if str(emission.get('phase','')).lower() != 'authoritative':
            continue
        end = emission.get('emitted_epoch_ns')
        if type(end) is not int:
            continue
        action_links = {}
        for claim_id in emission.get('claim_ids',[]):
            refs,run_id = claims.get(claim_id,([],None))
            for ref in refs:
                action_id,evidence_run = evidence.get(ref,(None,None))
                if run_id is not None and evidence_run == run_id:
                    action_links[action_id] = (claim_id,ref,run_id)
        for receipt in receipts:
            item = matched.get(receipt['receipt_id'])
            if item is None or item['action_id'] not in action_links:
                continue
            action_id = item['action_id']
            timestamp,run_id = actions[action_id]
            normalized_timestamp=timestamp
            clock_source='persisted_action_epoch_ns'
            if raw_matched is not None:
                raw=raw_matched.get(receipt['receipt_id'])
                if raw is None:
                    continue
                timestamp=event_wall_from_witness(receipt,raw['timestamp'])
                clock_source='raw_bpf_monotonic_plus_independent_receipt_clock_witness'
            claim_id,ref,claim_run = action_links[action_id]
            if run_id != claim_run or end < timestamp:
                continue
            # Exact identity/resource matching establishes correspondence.
            # Independent wall clock bounds reject an incoherent normalizer.
            if not receipt['started_ns']-5_000_000 <= timestamp <= receipt['completed_ns']+5_000_000:
                continue
            sample = {'receipt_id': receipt['receipt_id'], 'action_id': action_id,
                      'evidence_id': ref, 'claim_id': claim_id, 'alert_id': emission['alert_id'],
                      'event_epoch_ns': timestamp, 'emitted_epoch_ns': end,
                      'clock_source':clock_source,
                      'persisted_clock_delta_ns':normalized_timestamp-timestamp,
                      'latency_ms': (end-timestamp)/1e6,
                      'broadcast_delivered': emission.get('broadcast_delivered',False)}
            previous = linked.get(receipt['receipt_id'])
            if previous is None or end < previous['emitted_epoch_ns']:
                linked[receipt['receipt_id']] = sample
    return {**unavailable, 'available': True, 'linked_operations': len(linked),
            'samples': list(linked.values()), 'unlinked_operations': len(receipts)-len(linked)}


def analyze_condition(result, output):
    output = Path(output)
    stderr = output/'collector.stderr'
    observer = observer_summary(stderr)
    agents = []
    for path in sorted(output.glob('instance-*/agent-metrics.json')):
        agents.append(json.loads(path.read_text()))
    paths = [output/f'workload-{index}.stdout' for index in range(result['concurrency'])]
    receipts, errors = controlled_capture.load_receipts(paths)
    emissions = log_records(stderr, '[alert:emitted]')
    diagnostics = log_records(stderr, '[diagnostics:json]')
    agent_costs = [row['usage']['cost_upper_bound_usd'] for row in agents]
    agent_cost = sum(agent_costs) if len(agents)==result['concurrency'] else None
    observer_cost = observer['cost_upper_bound_usd']
    total = agent_cost+observer_cost if agent_cost is not None and observer_cost is not None else None
    value = {'schema': 'mtagentlens.overhead-supplement/v1',
             'condition_id': result['condition_id'], 'agents': agents,
             'observer': observer,
             'cost_upper_bound_usd': total,
             'alert_latency': linked_alert_latency(result.get('database_path'), receipts, emissions,
                                                   output/'trace.log' if result['profile']!='C0' else None),
             'receipt_errors': errors,
             'graph_update': diagnostics[-1].get('latency',{}).get('graph_update') if diagnostics else None}
    (output/'measurement-evidence.json').write_text(json.dumps(value,indent=2)+'\n')
    return value
