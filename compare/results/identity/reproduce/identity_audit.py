# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Recount the fixed IdentityBench cohort without rerunning agents or reviewers."""
import collections
import csv
import hashlib
import json
from pathlib import Path

CONDITIONS = {
    'C0': 'pid-instance-only', 'C1': 'pid-instance-plus-ppid',
    'C2': 'pid-instance-plus-tls', 'C3': 'pid-instance-plus-conversation', 'C4': 'full',
}

def pair_counts(rows):
    counts = dict(tp=0, fp=0, fn=0, tn=0)
    for i, (reference, predicted) in enumerate(rows):
        for other_reference, other_predicted in rows[i + 1:]:
            same_reference = reference == other_reference
            same_predicted = predicted == other_predicted
            key = ('tp' if same_predicted else 'fn') if same_reference else ('fp' if same_predicted else 'tn')
            counts[key] += 1
    return counts

def ratio(a, b):
    return a / b if b else None

def score(c):
    return dict(precision=ratio(c['tp'], c['tp'] + c['fp']),
                recall=ratio(c['tp'], c['tp'] + c['fn']),
                f1=ratio(2*c['tp'], 2*c['tp'] + c['fp'] + c['fn']))

def write_csv(path, rows):
    assert rows
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

def audit(base):
    reference_path = base/'reference/identity-reference.json'
    reference = json.loads(reference_path.read_text())
    metrics = json.loads((base/'metrics/identity-metrics.json').read_text())['data']
    unit_rows, scenario_rows, reference_rows, inputs = [], [], [], [reference_path, base/'metrics/identity-metrics.json']
    identical = collections.Counter()
    same_process_negative_pairs = 0
    for case_id, case in reference['cases'].items():
        path = base/'data/identity-runs'/f"{case['scenario_id']}.json"
        inputs.append(path)
        runs = json.loads(path.read_text())['runs']
        assert len(runs) == 1
        prediction_run_id = runs[0]['run_id']
        views = {view['condition']: view for view in runs[0]['views']}
        known = {uid: unit for uid, unit in case['units'].items() if unit['actor'] is not None}
        actor_counts = collections.Counter(u['actor'] for u in known.values())
        negative_pairs = len(known)*(len(known)-1)//2 - sum(n*(n-1)//2 for n in actor_counts.values())
        reference_rows.append(dict(case_id=case_id, scenario_id=case['scenario_id'], run_id=case['run_id'], prediction_run_id=prediction_run_id, source_fingerprint=runs[0].get('source_fingerprint'), current_fingerprint=runs[0].get('current_fingerprint'), sampled_units=len(case['units']), known_units=len(known), unknown_units=len(case['units'])-len(known), reference_actors=len(actor_counts), negative_reference_pairs=negative_pairs))
        known_values = list(known.values())
        same_process_negative_pairs += sum(a['actor'] != b['actor'] and (a['pid'],a['process_start_time_ns']) == (b['pid'],b['process_start_time_ns']) for i,a in enumerate(known_values) for b in known_values[i+1:])
        signatures = {}
        for condition, view_name in CONDITIONS.items():
            predictions = {u['unit_id']: u for u in views[view_name]['units'] if u['unit_id'] in case['units']}
            assert set(predictions) == set(case['units'])
            all_rows, assigned_rows, partition_groups = [], [], collections.defaultdict(list)
            assigned_total = assigned_known = 0
            for uid, unit in case['units'].items():
                process = predictions[uid]['process_id']
                assert process is None or (process['pid'], process['start_time_ns']) == (unit['pid'], unit['process_start_time_ns']), (case_id, uid)
                predicted = predictions[uid]['actor']
                assigned = predicted['state'] == 'assigned'
                assigned_total += assigned
                partition = predicted.get('partition') if assigned else None
                reason = predicted.get('reason') if not assigned else None
                unit_rows.append(dict(condition=condition, case_id=case_id, scenario_id=case['scenario_id'], run_id=case['run_id'], prediction_run_id=prediction_run_id, unit_id=uid, unit_kind=uid.split(':')[0], reference_known=unit['actor'] is not None, reference_actor=unit['actor'], reference_tier=unit['tier'], pid=unit['pid'], process_start_time_ns=unit['process_start_time_ns'], prediction_state=predicted['state'], predicted_partition=partition, unknown_reason=reason, prediction_limitations=json.dumps(predictions[uid].get('limitations',[]),separators=(',',':'))))
                key = ('assigned', partition) if assigned else ('singleton', uid)
                partition_groups[key].append(uid)
                if unit['actor'] is not None:
                    all_rows.append((unit['actor'], key))
                    if assigned:
                        assigned_known += 1
                        assigned_rows.append((unit['actor'], key))
            all_counts, conditional_counts = pair_counts(all_rows), pair_counts(assigned_rows)
            values = dict(condition=condition, case_id=case_id, scenario_id=case['scenario_id'], sampled_units=len(case['units']), reference_known=len(known), assigned_sampled=assigned_total, assigned_known=assigned_known, unassigned_known=len(known)-assigned_known, coverage=ratio(assigned_known,len(known)))
            values.update(all_counts)
            values.update(score(all_counts))
            values.update({'conditional_'+k:v for k,v in conditional_counts.items()})
            values.update({'conditional_'+k:v for k,v in score(conditional_counts).items()})
            scenario_rows.append(values)
            signatures[condition] = sorted(sorted(group) for group in partition_groups.values())
        identical['C0_C1'] += signatures['C0'] == signatures['C1']
        identical['C3_C4'] += signatures['C3'] == signatures['C4']
    summaries = []
    for condition in CONDITIONS:
        rows = [r for r in scenario_rows if r['condition'] == condition]
        totals = {k:sum(r[k] for r in rows) for k in ('sampled_units','reference_known','assigned_sampled','assigned_known','unassigned_known','tp','fp','fn','tn','conditional_tp','conditional_fp','conditional_fn','conditional_tn')}
        totals.update(condition=condition, coverage=ratio(totals['assigned_known'],totals['reference_known']))
        totals.update(score(totals))
        totals.update({'conditional_'+k:v for k,v in score({k:totals['conditional_'+k] for k in ('tp','fp','fn','tn')}).items()})
        original = metrics['conditions'][condition]
        for original_key, audited_key in (('coverage','coverage'),('pairwise_precision','precision'),('pairwise_recall','recall'),('pairwise_f1','f1'),('pairwise_f1_evaluated','conditional_f1')):
            assert abs(original[original_key]-totals[audited_key]) < 1e-12, (condition, original_key)
        summaries.append(totals)
    votes = collections.Counter()
    for case in reference['cases'].values():
        for unit in case['units'].values():
            actors = [actor for actor in unit['ai_votes'].values() if actor]
            if actors:
                largest = max(collections.Counter(actors).values())
                votes[f'max_matching_votes_{largest}'] += 1
            else:
                votes['no_nonnull_vote'] += 1
    limitation_counts = {condition:dict(collections.Counter(flag for row in unit_rows if row['condition']==condition and row['reference_known'] for flag in json.loads(row['prediction_limitations']))) for condition in CONDITIONS}
    unknown_counts = {condition:dict(collections.Counter(row['unknown_reason'] for row in unit_rows if row['condition']==condition and row['reference_known'] and row['prediction_state']!='assigned')) for condition in CONDITIONS}
    out = dict(limitation_counts=limitation_counts, unknown_reason_counts=unknown_counts, schema='mtagentlens.identity-audit/1', conditions=summaries,
               sampled_units=len(reference['cases'])*60, reference_known=sum(r['known_units'] for r in reference_rows),
               scenarios=len(reference_rows), scenarios_with_known_reference=sum(r['known_units']>0 for r in reference_rows),
               reference_actor_count_distribution=dict(collections.Counter(r['reference_actors'] for r in reference_rows)),
               negative_reference_pairs=sum(r['negative_reference_pairs'] for r in reference_rows),
               same_process_negative_reference_pairs=same_process_negative_pairs,
               equivalent_sampled_partitions=dict(identical), ai_vote_counts=dict(votes))
    kind_rows = []
    for condition in CONDITIONS:
        for kind in ('message','intent','action'):
            selected = [row for row in unit_rows if row['condition']==condition and row['unit_kind']==kind and row['reference_known']]
            assigned = sum(row['prediction_state']=='assigned' for row in selected)
            kind_rows.append(dict(condition=condition,unit_kind=kind,reference_known=len(selected),assigned_known=assigned,unassigned_known=len(selected)-assigned,coverage=ratio(assigned,len(selected))))
    write_csv(base/'data/kind_coverage.csv',kind_rows)
    write_csv(base/'data/identity_units.csv', unit_rows)
    write_csv(base/'data/scenario_metrics.csv', scenario_rows)
    write_csv(base/'data/reference_coverage.csv', reference_rows)
    write_csv(base/'data/pair_counts.csv', summaries)
    (base/'report/audit.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
    (base/'report/input_sha256.json').write_text(json.dumps({str(p.relative_to(base)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},indent=2)+'\n')
    return out

if __name__ == '__main__':
    result = audit(Path(__file__).resolve().parents[1])
    print(json.dumps(result,ensure_ascii=False))
