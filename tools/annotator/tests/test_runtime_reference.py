# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Independent reference protocol; synthetic evidence is not experimental truth.

Identity uses a deterministic 60-unit cohort with actor anchors for captured
intents outside the sample. Attribution is two-phase: a per-run scope packet
(messages + intents + compact security-action index) and a bounded localize
packet materialised from the chosen target action.
"""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tools.annotator import ai_runtime as ar
from tools.annotator import runtime_reference as rr

DB_SHA = 'a' * 64
REAL_DB = Path('/home/lzk/MtAgentLens/env/runs/20261028-034200-36deb0defe42/'
               'collector/collector.responsibility.db')


def unit_intent(occurrence='i1', *, pid=11, start=100, timestamp=10,
                conversation='session-a', native='{"format":"Anthropic","id":"call-a"}',
                authority='RequestHistoryAssistantToolUse',
                args='[{"Content":"read report"}]', message='m1'):
    return {'unit_id': f'intent:{occurrence}', 'kind': 'intent', 'occurrence_id': occurrence,
            'pid': pid, 'process_start_time_ns': start, 'timestamp_ns': timestamp,
            'message_id': message, 'tool_name': 'Bash', 'tool_args_json': args,
            'declared_conversation_id': conversation, 'authority_source': authority,
            'native_tool_call_json': native}


def unit_action(occurrence='a1', *, pid=11, start=100, timestamp=20,
                action_type='NetworkConnect', args='[{"Url":{"host":"203.0.113.7","port":443}}]'):
    return {'unit_id': f'action:{occurrence}', 'kind': 'action', 'occurrence_id': occurrence,
            'pid': pid, 'process_start_time_ns': start, 'timestamp_ns': timestamp,
            'action_type': action_type, 'normalized_args_json': args,
            'outcome': 'succeeded', 'syscall_ret': 3}


def unit_message(occurrence, *, pid=11, start=100, timestamp=0):
    return {'unit_id': f'message:{occurrence}', 'kind': 'message', 'occurrence_id': occurrence,
            'message_id': occurrence, 'pid': pid, 'process_start_time_ns': start,
            'timestamp_ns': timestamp}


def evidence():
    return [{'ref': 'source-1', 'source': 'trajectory',
             'data': {'text': 'observed tool content'}}]


def process_row(pid=11, start=100, parent_pid=None, parent_start=None, timestamp=5,
                event_id=1, comm='bash'):
    return {'process_event_id': event_id, 'pid': pid, 'start_time_ns': start,
            'parent_pid': parent_pid, 'parent_start_time_ns': parent_start,
            'comm': comm, 'timestamp_ns': timestamp}


def identity_packet(units=None, packet_evidence=None):
    return rr.make_packet('case-0000', 'run-1', 'r' * 64,
                          [unit_intent(), unit_action()] if units is None else units,
                          evidence() if packet_evidence is None else packet_evidence,
                          kind='identity')


def scope_packet(units=None, packet_evidence=None):
    return rr.make_packet('case-0000', 'run-1', 'r' * 64,
                          [unit_intent(), unit_action()] if units is None else units,
                          evidence() if packet_evidence is None else packet_evidence,
                          kind='scope')


def localize_packet(units=None, *, target='a1', links=(('i1', 'a1'),),
                    linked_actions=(), processes=None, packet_evidence=None):
    return rr.make_packet('case-0000', 'run-1', 'r' * 64,
                          [unit_intent(), unit_action()] if units is None else units,
                          evidence() if packet_evidence is None else packet_evidence,
                          kind='localize', links=links, target_action_id=target,
                          linked_actions=linked_actions,
                          processes=[process_row()] if processes is None else processes)


def actor_of(packet, process=(11, 100)):
    return next(key for key, item in packet['actor_candidates'].items()
                if list(process) in item['processes'])


def identity_vote(packet, actor=None, *, unknown=()):
    actor = actor_of(packet) if actor is None else actor
    anchor = ['raw:intent:i1']
    row = {'case_id': packet['case_id'], 'identities': {},
           'notes': 'Synthetic proposal; no accuracy claim'}
    for unit in packet['units']:
        if unit['unit_id'] in unknown:
            row['identities'][unit['unit_id']] = {
                'actor': None, 'evidence_refs': [], 'notes': 'ambiguous'}
        else:
            refs = anchor + (['raw:' + unit['unit_id']]
                             if unit['unit_id'] != 'intent:i1' else [])
            row['identities'][unit['unit_id']] = {
                'actor': actor, 'evidence_refs': refs, 'notes': 'synthetic native scope'}
    return row


def scope_vote(packet, *, actor=None, attack_executed='yes', target_action='a1',
               agents=None, decisive=('raw:intent:i1',)):
    actor = actor_of(packet) if actor is None else actor
    return {'case_id': packet['case_id'], 'attack_executed': attack_executed,
            'scope': {'agents': [actor] if agents is None else list(agents),
                      'decisive_event_refs': list(decisive)},
            'target_action': target_action,
            'notes': 'Synthetic run-level scope; no accuracy claim'}


def localize_vote(packet, *, actor=None, root_status='known',
                  step_ref='raw:intent:i1', entry_node='source-1',
                  edges=(('raw:intent:i1', 'raw:action:a1'),), chain_status='known'):
    actor = actor_of(packet) if actor is None else actor
    chain_refs = sorted({ref for edge in edges for ref in edge})
    return {'case_id': packet['case_id'],
            'root': ({'status': 'known', 'actors': [actor], 'step_ref': step_ref,
                      'evidence_refs': ['raw:intent:i1']}
                     if root_status == 'known' else
                     {'status': 'unknown', 'evidence_refs': []}),
            'entry': ({'status': 'known', 'node_id': entry_node,
                       'evidence_refs': [entry_node]}
                      if entry_node else {'status': 'unknown', 'evidence_refs': []}),
            'chain': ({'status': 'known', 'edges': [list(edge) for edge in edges],
                       'evidence_refs': chain_refs}
                      if chain_status == 'known' else
                      {'status': 'unknown', 'evidence_refs': []}),
            'notes': 'Synthetic localize; no accuracy claim'}


def resolved(packet, vote):
    return rr.resolve_votes(packet, {name: copy.deepcopy(vote) for name in rr.REVIEWERS})


def export(identity, scope, localize, *, identity_vote_obj=None, scope_vote_obj=None,
           localize_vote_obj=None, localize_obj=True):
    identity_vote_obj = identity_vote(identity) if identity_vote_obj is None else identity_vote_obj
    scope_vote_obj = scope_vote(scope) if scope_vote_obj is None else scope_vote_obj
    if localize_vote_obj is None and localize is not None and localize_obj:
        localize_vote_obj = localize_vote(localize)
    localize_resolved = (resolved(localize, localize_vote_obj)
                         if localize_vote_obj is not None else None)
    return rr.reference_exports(
        identity, resolved(identity, identity_vote_obj),
        scope, resolved(scope, scope_vote_obj),
        localize, localize_resolved,
        adjudicator_session={'identity': None, 'scope': None, 'localize': None},
        source_database_sha256=DB_SHA)


# --------------------------------------------------------------------------- #
# Identity cohort + anchors.
# --------------------------------------------------------------------------- #

def test_identity_packet_samples_exactly_twenty_per_kind_plus_hard_subset():
    units = [unit_message(f'm{index}', timestamp=index) for index in range(50)]
    units += [unit_intent(f'i{index}', timestamp=index, native=json.dumps(
        {'format': 'Anthropic', 'id': f'call-{index}'})) for index in range(30)]
    units += [unit_action(f'a{index}', timestamp=index, action_type='FileOpen',
                          args='[{"Path":"/tmp/x"}]') for index in range(100)]
    units += [unit_intent('hard-i1', pid=22, start=200, timestamp=1000,
                          conversation='session-b',
                          native='{"format":"Anthropic","id":"call-b"}'),
              unit_intent('hard-i2', pid=22, start=200, timestamp=1001,
                          conversation='session-c',
                          native='{"format":"Anthropic","id":"call-c"}')]
    units += [unit_action('hard-a1', pid=22, start=200, timestamp=1002,
                          action_type='FileOpen', args='[{"Path":"/tmp/y"}]'),
              unit_action('hard-a2', pid=22, start=200, timestamp=1003,
                          action_type='FileOpen', args='[{"Path":"/tmp/z"}]')]

    packet = rr.make_packet('case-0000', 'run-1', 'r' * 64, units, [], kind='identity')
    sampled = {unit['unit_id']: unit for unit in packet['units']}
    counts = {kind: sum(unit['kind'] == kind for unit in packet['units'])
              for kind in ('message', 'intent', 'action')}
    assert counts['message'] == 20
    assert counts['intent'] >= 21 and counts['action'] >= 21
    assert len(packet['units']) < len(units)
    for unit in units:
        if unit['unit_id'].startswith(('intent:hard-', 'action:hard-')):
            assert unit['unit_id'] in sampled
    regular = sorted((unit for unit in sampled.values()
                      if unit['unit_id'].startswith('message:m')),
                     key=lambda unit: unit['timestamp_ns'])
    assert [unit['timestamp_ns'] for unit in regular] == [
        0, 2, 5, 7, 10, 12, 15, 18, 20, 23, 25, 28, 30, 33, 36, 38, 41, 43, 46, 49]
    sampling = packet['sampling']
    assert sampling['per_kind']['message'] == {'source': 50, 'target': 20, 'sampled': 20}
    assert sampling['hard_subset_units'] == 4
    assert 'evenly spaced' in sampling['rule']
    again = rr.make_packet('case-0000', 'run-1', 'r' * 64, copy.deepcopy(units), [],
                           kind='identity')
    assert json.dumps(packet, sort_keys=True) == json.dumps(again, sort_keys=True)


def test_anchor_intents_outside_the_sample_still_support_actor_candidates():
    units = [unit_intent('i1', timestamp=1),
             unit_action('a1', timestamp=50, action_type='FileOpen', args='[{"Path":"/tmp/a"}]')]
    # The only captured intent anchor for this process is outside the 20-unit sample.
    units.append(unit_intent('anchor', pid=22, start=200, timestamp=50,
                             conversation='session-anchor', message='ma',
                             native='{"format":"Anthropic","id":"call-anchor"}'))
    for index in range(40):
        units.append(unit_intent(f'x{index}', timestamp=100 + index,
                                 message=f'mx{index}', args='[]',
                                 native=json.dumps({'format': 'Anthropic',
                                                    'id': f'other-{index}'})))
    units.append(unit_action('b1', pid=22, start=200, timestamp=51,
                             action_type='FileOpen', args='[{"Path":"/tmp/b"}]'))
    packet = rr.make_packet('case-0000', 'run-1', 'r' * 64, units, [], kind='identity')
    sampled = {unit['unit_id'] for unit in packet['units']}
    assert 'intent:anchor' not in sampled
    anchor_actor = actor_of(packet, (22, 200))
    anchor_refs = {anchor['ref'] for anchor in packet['anchor_context']}
    assert anchor_refs
    assert set(packet['actor_candidates'][anchor_actor]['evidence_refs']) & anchor_refs
    # A sampled action from that process can be resolved through the anchor ref.
    vote = identity_vote(packet, unknown={unit['unit_id'] for unit in packet['units']})
    vote['identities']['action:b1'] = {
        'actor': anchor_actor,
        'evidence_refs': ['raw:action:b1',
                          next(ref for ref in packet['actor_candidates'][anchor_actor]['evidence_refs'])],
        'notes': 'anchor outside sample'}
    rr.validate_vote(vote, packet)


# --------------------------------------------------------------------------- #
# Scope packet: all messages/intents + compact security index.
# --------------------------------------------------------------------------- #

def test_scope_packet_indexes_security_actions_without_giant_timeline():
    units = [unit_message('m1'), unit_intent()]
    units += [unit_action(f'a{index}', timestamp=20 + index, action_type='FileOpen',
                          args='[{"Path":"/tmp/report"}]') for index in range(5440)]
    units += [unit_action('net', timestamp=6000, action_type='NetworkConnect'),
              unit_action('write', timestamp=6001, action_type='FileWrite',
                          args='[{"Path":"/tmp/out"}]'),
              unit_action('exec', timestamp=6002, action_type='ProcessExec',
                          args='[{"Command":"curl"}]'),
              unit_action('del', timestamp=6003, action_type='FileDelete',
                          args='[{"Path":"/tmp/x"}]'),
              unit_action('ren', timestamp=6004, action_type='FileRename',
                          args='[{"Path":"/tmp/y"}]')]
    packet = rr.make_packet('case-0000', 'run-1', 'r' * 64, units, evidence(), kind='scope')
    assert {unit['kind'] for unit in packet['units']} == {'message', 'intent'}
    assert len(packet['units']) == 2
    index = {row['action_id'] for row in packet['action_index']}
    assert index == {'net', 'write', 'exec', 'del', 'ren'}  # not all 5,445 actions
    assert packet['selection']['action_index'] == 5
    assert 'not timeline events' in packet['selection']['rule']
    assert json.dumps(packet['events']).count('raw:action:') == 0
    assert len(json.dumps(packet, ensure_ascii=False).encode()) < 700_000


def test_scope_packet_carries_target_and_actor_roster():
    packet = scope_packet()
    row = packet['action_index'][0]
    assert row['action_id'] == 'a1'
    assert row['action_type'] == 'NetworkConnect'
    assert row['target'] == '203.0.113.7:443'
    assert actor_of(packet)
    assert 'targets' not in packet


def test_scope_vote_selects_one_index_action_and_never_localises():
    packet = scope_packet()
    vote = scope_vote(packet)
    assert set(vote) == {'case_id', 'attack_executed', 'scope', 'target_action', 'notes'}
    assert vote['target_action'] == 'a1'
    rr.validate_vote(vote, packet)
    vote['target_action'] = 'action:a1'
    with pytest.raises(ValueError, match='index'):
        rr.validate_vote(vote, packet)
    vote = scope_vote(packet, attack_executed='no', target_action=None)
    rr.validate_vote(vote, packet)
    vote['target_action'] = 'a1'
    with pytest.raises(ValueError, match='attack_executed'):
        rr.validate_vote(vote, packet)


def test_scope_decisive_refs_accept_action_index_ids():
    packet = scope_packet()
    vote = scope_vote(packet, decisive=('a1',))
    rr.validate_vote(vote, packet)


def test_scope_target_cannot_be_a_redacted_index_row():
    units = [unit_intent(), unit_action(action_type='NetworkConnect',
                                        args='[{"Path":"/ground_truth/x"}]')]
    packet = scope_packet(units)
    assert packet['action_index'][0]['redacted'] is True
    with pytest.raises(ValueError, match='redacted'):
        rr.validate_vote(scope_vote(packet), packet)


# --------------------------------------------------------------------------- #
# Localize packet: bounded neighbourhood, at most 400 events.
# --------------------------------------------------------------------------- #

def test_localize_packet_is_bounded_and_neighbourhood_scoped():
    units = [unit_message('m1'), unit_intent()]
    units += [unit_action('a1', timestamp=20)]
    units += [unit_action(f'x{index}', timestamp=30 + index, action_type='FileOpen',
                          args='[{"Path":"/tmp/linked"}]') for index in range(500)]
    packet = localize_packet(units, linked_actions=units[2:])
    assert packet['target_action'] == 'a1'
    assert len(packet['events']) <= 400
    assert packet['selection']['event_count'] == len(packet['events'])
    assert packet['selection']['omitted']['linked_actions'] > 0
    assert 'never silently dropped' in packet['selection']['rule']
    assert any(event['source'] == 'correlation_evidence' for event in packet['events'])
    assert any(event['source'] == 'process_events' for event in packet['events'])


def test_localize_core_overflow_fails_explicitly():
    units = [unit_intent(), unit_action('a1')]
    too_many = [('i1', 'a1')] * 401  # one target with 401 observed correlation rows
    with pytest.raises(ValueError, match='cap|400'):
        rr.make_packet('case-0000', 'run-1', 'r' * 64, units, [], kind='localize',
                       links=too_many, target_action_id='a1', processes=[process_row()])


def test_localize_vote_returns_root_entry_chain_and_unknown():
    packet = localize_packet()
    vote = localize_vote(packet)
    assert set(vote) == {'case_id', 'root', 'entry', 'chain', 'notes'}
    rr.validate_vote(vote, packet)
    unknown = localize_vote(packet, root_status='unknown', entry_node=None,
                            chain_status='unknown')
    rr.validate_vote(unknown, packet)


@pytest.mark.parametrize('mutation', [
    'invented_root_ref', 'invented_entry_ref', 'unrelated_root',
    'cycle', 'reverse_time', 'unknown_root_with_actors', 'invented_actor',
])
def test_localize_vote_rejects_unsupported_localisation(mutation):
    packet = localize_packet()
    vote = localize_vote(packet)
    if mutation == 'invented_root_ref':
        vote['root']['evidence_refs'] = ['invented-ref']
    elif mutation == 'invented_entry_ref':
        vote['entry'] = {'status': 'known', 'node_id': 'invented-ref',
                         'evidence_refs': ['invented-ref']}
    elif mutation == 'unrelated_root':
        other = unit_intent('i2', pid=22, start=200, timestamp=11,
                            conversation='session-b',
                            native='{"format":"Anthropic","id":"call-b"}')
        packet = localize_packet([unit_intent(), unit_action(), other],
                                 linked_actions=[other])
        vote = localize_vote(packet)
        vote['root'] = {'status': 'known', 'actors': [actor_of(packet, (22, 200))],
                        'step_ref': 'raw:intent:i2',
                        'evidence_refs': ['raw:intent:i2']}
    elif mutation == 'cycle':
        units = [unit_intent(timestamp=10), unit_action(timestamp=10)]
        packet = localize_packet(units)
        vote = localize_vote(packet, edges=(('raw:intent:i1', 'raw:action:a1'),
                                            ('raw:action:a1', 'raw:intent:i1')))
    elif mutation == 'reverse_time':
        vote = localize_vote(packet, edges=(('raw:action:a1', 'raw:intent:i1'),))
    elif mutation == 'unknown_root_with_actors':
        vote['root'] = {'status': 'unknown', 'evidence_refs': [], 'actors': ['guessed']}
    else:
        vote['root']['actors'] = ['invented-actor']
    with pytest.raises(ValueError):
        rr.validate_vote(vote, packet)


def test_invented_root_step_is_downgraded_to_unknown():
    packet = localize_packet()
    vote = localize_vote(packet)
    vote['root']['step_ref'] = 'invented-ref'
    normalized = rr.validate_vote(vote, packet)
    assert normalized['root']['step_ref'] is None


def test_any_identity_scope_or_localize_disagreement_needs_adjudication():
    identity = identity_packet()
    votes = {name: copy.deepcopy(identity_vote(identity)) for name in rr.REVIEWERS}
    votes[rr.REVIEWERS[-1]]['identities']['action:a1']['actor'] = None
    assert rr.resolve_votes(identity, votes)['needs_adjudication'] is True

    scope = scope_packet()
    votes = {name: copy.deepcopy(scope_vote(scope)) for name in rr.REVIEWERS}
    votes[rr.REVIEWERS[-1]]['target_action'] = None
    assert rr.resolve_votes(scope, votes)['needs_adjudication'] is True

    localize = localize_packet()
    votes = {name: copy.deepcopy(localize_vote(localize)) for name in rr.REVIEWERS}
    votes[rr.REVIEWERS[-1]]['entry'] = {'status': 'unknown', 'evidence_refs': []}
    assert rr.resolve_votes(localize, votes)['needs_adjudication'] is True
    resolved_votes = rr.resolve_votes(localize, votes, adjudication=localize_vote(localize))
    assert resolved_votes['adjudicated'] is True


# --------------------------------------------------------------------------- #
# Exports.
# --------------------------------------------------------------------------- #

def test_export_attributes_once_per_executed_run_only():
    identity, scope = identity_packet(), scope_packet()
    localize = localize_packet()
    artifacts = export(identity, scope, localize)
    exported_identity = artifacts['identity']
    assert set(exported_identity['events']) == {unit['unit_id'] for unit in identity['units']}
    exported = artifacts['attribution']
    assert len(exported['units']) == 1
    unit = exported['units'][0]
    assert unit['target_action_id'] == 'a1'
    assert unit['attack_executed'] == 'yes'
    assert unit['root']['status'] == 'known'
    assert unit['root']['step_ref'] == 'raw:intent:i1'
    assert exported['runs_without_target'] == []
    from compare.tools.attribution_reference import validate_reference
    exported['frozen'] = True
    parsed = validate_reference(exported)
    assert parsed['units'][0]['root']['step_ref'] == 'raw:intent:i1'


@pytest.mark.parametrize('attack_executed,target,reason', [
    ('no', None, 'attack_executed_no'),
    ('unknown', None, 'attack_executed_unknown'),
    ('yes', None, 'no_observed_target_action'),
])
def test_runs_without_a_target_are_recorded_not_attributed(attack_executed, target, reason):
    identity, scope = identity_packet(), scope_packet()
    artifacts = export(identity, scope, None,
                       scope_vote_obj=scope_vote(scope, attack_executed=attack_executed,
                                                 target_action=target))
    exported = artifacts['attribution']
    assert exported['units'] == []
    assert exported['runs_without_target'] == [{
        'run_id': 'run-1', 'attack_executed': attack_executed,
        'target_action': None, 'reason': reason}]
    from compare.tools.attribution_reference import validate_reference
    exported['frozen'] = True
    parsed = validate_reference(exported)
    assert parsed['units'] == []


# --------------------------------------------------------------------------- #
# Packets reject detector residue; prepare/finalize/verify.
# --------------------------------------------------------------------------- #

def test_packet_rejects_detector_fields_and_duplicate_raw_units():
    for kind in ('identity', 'scope'):
        units = copy.deepcopy([unit_intent(), unit_action()])
        units[0]['agent_id'] = 'detector'
        with pytest.raises(ValueError, match='field'):
            rr.make_packet('case-0', 'run', 'r' * 64, units, [], kind=kind)
        with pytest.raises(ValueError, match='duplicate'):
            rr.make_packet('case-0', 'run', 'r' * 64,
                           [unit_intent(), unit_action(), unit_action()], [], kind=kind)


def test_raw_label_residue_is_hidden_without_dropping_sampled_units():
    units = [unit_intent(), unit_action(),
             unit_action('a2', timestamp=30,
                         args='[{"Path":"/ground_truth/answer.txt"}]')]
    packet = identity_packet(units)
    assert 'ground_truth' not in json.dumps(packet)
    assert len(packet['units']) == 3
    assert next(unit for unit in packet['units']
                if unit['unit_id'] == 'action:a2')['redacted'] is True


def test_pid_without_native_scope_is_not_logical_identity():
    units = copy.deepcopy([unit_intent(), unit_action()])
    for unit in units:
        unit.pop('declared_conversation_id', None)
        unit.pop('native_tool_call_json', None)
    packet = identity_packet(units)
    assert packet['actor_candidates'] == {}
    vote = {'case_id': packet['case_id'],
            'identities': {unit['unit_id']: {'actor': 'native:guessed',
                                             'evidence_refs': [], 'notes': 'n'}
                           for unit in packet['units']},
            'notes': 'synthetic'}
    with pytest.raises(ValueError, match='evidence|actor'):
        rr.validate_vote(vote, packet)


def test_multiple_native_scopes_in_one_process_forces_action_abstention():
    units = [unit_intent(), unit_action(),
             unit_intent('i2', timestamp=12, conversation='session-b',
                         native='{"format":"Anthropic","id":"call-b"}')]
    packet = identity_packet(units)
    vote = identity_vote(packet)
    vote['identities']['intent:i2'] = {'actor': None, 'evidence_refs': [],
                                       'notes': 'unknown'}
    with pytest.raises(ValueError, match='scope'):
        rr.validate_vote(vote, packet)


def write_review(root, role, kind, value, session=None):
    prompt, hashes = ar.review_input(root, [value['case_id']], kind=kind,
                                     adjudicate=role == rr.ADJUDICATOR)
    path = root / 'reviews' / f"{role}-{kind}-{value['case_id']}.jsonl"
    path.parent.mkdir(exist_ok=True)
    path.write_text('\n'.join(json.dumps(row) for row in [
        {'type': 'session', 'sessionId': session or f'synthetic-{role}-{kind}-{value["case_id"]}'},
        {'type': 'final', 'text': json.dumps([value])}]))
    prompt_path = root / 'prompts' / (path.stem + '.txt')
    prompt_path.parent.mkdir(exist_ok=True)
    prompt_path.write_text(prompt)
    ar.dump(path.with_suffix('.metadata.json'), {
        'reviewer': role, 'kind': kind, 'cases': [value['case_id']], 'reviewers': 3,
        'adjudication': role == rr.ADJUDICATOR,
        'review_packet_sha256': hashes, 'prompt_sha256': ar.sha(prompt.encode()),
        'returncode': 0, 'stdout_sha256': ar.sha(path.read_bytes()),
        'fresh_session': True, 'model': 'deepseek-flash',
        'provider': 'deepseek-official'})
    return path


def prepared(tmp_path):
    source, _ = source_runtime(tmp_path.parent / (tmp_path.name + '-source'))
    rr.prepare(source, tmp_path)
    return {kind: rr.load_packet(tmp_path, kind, 'case-0000')
            for kind in ('identity', 'scope')}


def write_phase_one(root, packets, *, target='a1'):
    for role in rr.REVIEWERS:
        write_review(root, role, 'identity', identity_vote(packets['identity']))
        write_review(root, role, 'scope',
                     scope_vote(packets['scope'], target_action=target))


def write_localize(root, packets, *, vote=None):
    localize = rr.load_packet(root, 'localize', packets['scope']['case_id'])
    unknown = {'case_id': localize['case_id'],
               'root': {'status': 'unknown', 'evidence_refs': []},
               'entry': {'status': 'unknown', 'evidence_refs': []},
               'chain': {'status': 'unknown', 'evidence_refs': []},
               'notes': 'synthetic unknown localize'}
    for role in rr.REVIEWERS:
        write_review(root, role, 'localize', unknown if vote is None else vote)


def source_database(root):
    inventory = json.loads((root / 'inventory.json').read_text())
    return Path(inventory['cases'][0]['source']['database'])


def test_reference_rubrics_do_not_relax_execution_protocol(tmp_path):
    packets = prepared(tmp_path)
    prompts = {kind: ar.review_input(tmp_path, ['case-0000'], kind=kind)[0]
               for kind in ('identity', 'scope')}
    assert rr.IDENTITY_RUBRIC in prompts['identity']
    assert rr.SCOPE_RUBRIC in prompts['scope']
    assert 'ONE scope judgment per run' in prompts['scope']
    assert 'root_actor MUST be null' not in prompts['identity']
    assert 'root_actor MUST be null' not in prompts['scope']
    assert 'root_actor MUST be null' in ar.RUBRIC


def test_prepare_writes_identity_and_scope_packets(tmp_path):
    source, out = source_runtime(tmp_path)
    db = source / 'run/collector/finalized.db'
    assert rr.prepare(source, out)['model_calls'] == 0
    identity = json.loads((out / 'identity-packets/case-0000.json').read_text())
    scope = json.loads((out / 'scope-packets/case-0000.json').read_text())
    alias = json.loads((out / 'review-packets/case-0000.json').read_text())
    assert identity['schema'] == rr.PACKET_SCHEMA
    assert scope['schema'] == rr.SCOPE_PACKET_SCHEMA
    assert {unit['unit_id'] for unit in identity['units']} == {
        'message:m1', 'intent:i1', 'action:a1', 'action:a2'}
    assert {unit['kind'] for unit in scope['units']} == {'message', 'intent'}
    assert {row['action_id'] for row in scope['action_index']} == {'a2'}
    assert alias == identity
    assert 'detector-only' not in json.dumps(identity)
    assert str(db) not in json.dumps(identity) and str(db) not in json.dumps(scope)
    assert not (out / 'localize-packets').exists()  # phase two waits for the scope vote
    with pytest.raises(ValueError, match='already prepared'):
        rr.prepare(source, out)


def test_finalize_is_two_phase_then_verifies(tmp_path):
    packets = prepared(tmp_path)
    write_phase_one(tmp_path, packets, target='a2')
    first = rr.finalize(tmp_path)
    assert first['valid'] is False
    assert first['pending'] == {'localize': ['case-0000']}
    assert not (tmp_path / 'references').exists()
    localize_file = tmp_path / 'localize-packets/case-0000.json'
    assert localize_file.is_file()
    localize = json.loads(localize_file.read_text())
    assert len(localize['events']) <= 400
    write_localize(tmp_path, packets)
    result = rr.finalize(tmp_path)
    assert result['valid'] is True, (result['errors'], result['pending'])
    frozen = json.loads((tmp_path / 'references/case-0000.json').read_text())
    assert frozen['identity']['frozen'] is True and frozen['attribution']['frozen'] is True
    assert len(frozen['attribution']['units']) == 1
    assert rr.verify(tmp_path)['valid'] is True
    frozen['attribution']['units'][0]['root'] = {'status': 'unknown',
                                                 'evidence_refs': []}
    (tmp_path / 'references/case-0000.json').write_text(json.dumps(frozen))
    assert rr.verify(tmp_path)['valid'] is False


def test_reused_session_or_changed_source_prevents_export(tmp_path):
    packets = prepared(tmp_path)
    for role in rr.REVIEWERS:
        write_review(tmp_path, role, 'identity', identity_vote(packets['identity']),
                     session='same-context')
        write_review(tmp_path, role, 'scope', scope_vote(packets['scope']),
                     session='same-context')
    assert rr.finalize(tmp_path)['valid'] is False
    assert not (tmp_path / 'references').exists()
    write_phase_one(tmp_path, packets, target='a2')
    source_database(tmp_path).write_text('changed')
    assert rr.finalize(tmp_path)['valid'] is False
    assert not (tmp_path / 'references').exists()


def source_runtime(tmp_path, *, authority_source='RequestHistoryAssistantToolUse',
                   case_ids=('case-0000',), detector_marker=None):
    """Frozen runtime whose schema-5 database uses the production writer encoding."""
    import sqlite3
    source, out = tmp_path / 'source', tmp_path / 'reference'
    cases, records, source_sha = [], [], {}
    for index, case_id in enumerate(case_ids):
        db = source / f'run{index}/collector/finalized.db'
        db.parent.mkdir(parents=True)
        run_id = f'run-{index + 1}'
        with sqlite3.connect(db) as conn:
            conn.executescript(f'''PRAGMA user_version=5;
            CREATE TABLE intents(intent_id TEXT,pid INTEGER,process_start_time_ns INTEGER,
              timestamp_ns INTEGER,run_id TEXT,message_id TEXT,tool_name TEXT,tool_args_json TEXT,
              native_tool_call_json TEXT,declared_conversation_id TEXT);
            CREATE TABLE actions(action_id TEXT,pid INTEGER,process_start_time_ns INTEGER,
              timestamp_ns INTEGER,run_id TEXT,action_type TEXT,normalized_args_json TEXT,outcome TEXT,syscall_ret INTEGER);
            CREATE TABLE message_bindings(message_id TEXT,pid INTEGER,start_time_ns INTEGER,timestamp_ns INTEGER,run_id TEXT);
            CREATE TABLE correlation_evidence(evidence_id TEXT PRIMARY KEY,intent_id TEXT,action_id TEXT,
              match_level TEXT,confidence REAL,phase TEXT,timestamp_ns INTEGER,run_id TEXT);
            CREATE TABLE process_events(process_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              pid INTEGER,start_time_ns INTEGER,parent_pid INTEGER,parent_start_time_ns INTEGER,
              comm TEXT,timestamp_ns INTEGER,run_id TEXT);
            CREATE TABLE responsibility_claims(claim_id TEXT,verdict TEXT);
            INSERT INTO responsibility_claims VALUES('detector-only','Unsafe');
            INSERT INTO intents VALUES('i1',11,100,10,'{run_id}','m1','Bash','[]',
              '{{"format":"Anthropic","id":"call-a"}}','session-a');
            INSERT INTO actions VALUES('a1',11,100,20,'{run_id}','FileOpen','[]','succeeded',3);
            INSERT INTO actions VALUES('a2',11,100,25,'{run_id}','NetworkConnect',
              '[{{"Url":{{"host":"203.0.113.7","port":443}}}}]','succeeded',0);
            INSERT INTO message_bindings VALUES('m1',11,100,10,'{run_id}');
            INSERT INTO correlation_evidence VALUES('e1','i1','a1','Exact',1.0,'Authoritative',20,'{run_id}');
            INSERT INTO process_events(pid,start_time_ns,parent_pid,parent_start_time_ns,comm,timestamp_ns,run_id)
              VALUES(11,100,NULL,NULL,'bash',5,'{run_id}');
            ALTER TABLE intents ADD COLUMN authority_source TEXT DEFAULT '{authority_source}';''')
            if detector_marker is not None:
                marker = str(detector_marker).replace("'", "''")
                conn.executescript(f'''
                CREATE TABLE final_decision(decision_id TEXT, verdict TEXT);
                CREATE TABLE identities(identity_id TEXT, actor_id TEXT);
                CREATE TABLE attribution(attribution_id TEXT, root_actor TEXT);
                CREATE TABLE tracebacks(traceback_id TEXT, payload TEXT);
                INSERT INTO final_decision VALUES('d1','{marker}');
                INSERT INTO identities VALUES('x1','{marker}');
                INSERT INTO attribution VALUES('r1','{marker}');
                INSERT INTO tracebacks VALUES('t1','{marker}');
                UPDATE actions SET normalized_args_json='[{{"Path":"/ground_truth/{marker}.txt"}}]';''')
        original = {'case_id': case_id,
                    'events': [{'ref': 'source-1', 'source': 'trajectory',
                                'data': {'text': 'observed tool content'}}],
                    'selection': {'truncated': False}}
        packet_path = source / 'packets' / f'{case_id}.json'
        ar.dump(packet_path, original)
        cases.append({'case_id': case_id,
                      'binding': {'data_release_id': 'r' * 64, 'scenario_id': f's{index}'},
                      'packet_sha256': ar.sha(packet_path.read_bytes())})
        records.append({'status': 'included', 'case_id': case_id,
                        'ledger': str(db.parent.parent / 'runner-events.jsonl')})
        source_sha[str(db.resolve())] = ar.sha(db.read_bytes())
    ar.dump(source / 'inventory.json', {'cases': cases, 'records': records,
                                        'source_sha256': source_sha})
    return source, out


def test_persisted_pascal_case_authority_source_restores_native_actor_anchors(tmp_path):
    source, out = source_runtime(tmp_path)
    rr.prepare(source, out)
    packet = rr.load_packet(out, 'identity', 'case-0000')
    expected = 'native:' + hashlib.sha256(json.dumps(
        ['session-a', 'Anthropic'], ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()[:24]
    assert list(packet['actor_candidates']) == [expected]
    rr.validate_vote(identity_vote(packet), packet)
    units = copy.deepcopy([unit_intent(), unit_action('a1', action_type='FileOpen',
                                                     args='[{"Path":"/tmp/a"}]')])
    for unit in units:
        if unit['kind'] == 'intent':
            unit['authority_source'] = 'request_history_assistant_tool_use'
    derived = identity_packet(units)
    assert list(derived['actor_candidates']) == [expected]


def test_detector_tables_and_ground_truth_paths_never_reach_reviewer_views(tmp_path):
    marker = 'DETECTOR-ONLY-MARKER-9f2c'
    source, out = source_runtime(tmp_path, detector_marker=marker)
    rr.prepare(source, out)
    for kind in ('identity', 'scope'):
        packet_bytes = (out / f'{kind}-packets/case-0000.json').read_bytes()
        assert marker.encode() not in packet_bytes
        assert b'ground_truth' not in packet_bytes
        packet = json.loads(packet_bytes)
        assert (any(unit.get('redacted') for unit in packet['units'])
                or any(row.get('redacted') for row in packet.get('action_index') or []))
        assert marker not in json.dumps(packet['actor_candidates'])
        prompt, _ = ar.review_input(out, ['case-0000'], kind=kind)
        assert marker not in prompt and 'ground_truth' not in prompt


def test_hidden_shared_session_event_is_rejected(tmp_path):
    packets = prepared(tmp_path)
    for role in rr.REVIEWERS:
        for kind, value in (('identity', identity_vote(packets['identity'])),
                            ('scope', scope_vote(packets['scope']))):
            path = write_review(tmp_path, role, kind, value)
            path.write_text(json.dumps({'type': 'session',
                                        'sessionId': 'shared-old-context'}) + '\n' + path.read_text())
            metadata = json.loads(path.with_suffix('.metadata.json').read_text())
            metadata['stdout_sha256'] = ar.sha(path.read_bytes())
            ar.dump(path.with_suffix('.metadata.json'), metadata)
    assert rr.finalize(tmp_path)['valid'] is False
    assert not (tmp_path / 'references').exists()


def test_prepare_failure_does_not_leave_partial_reference_directory(tmp_path):
    source, out = source_runtime(tmp_path)
    path = source / 'inventory.json'
    inventory = json.loads(path.read_text())
    other = copy.deepcopy(inventory['cases'][0])
    other['case_id'] = 'case-0001'
    other['packet_sha256'] = '0' * 64
    inventory['cases'].append(other)
    record = copy.deepcopy(inventory['records'][0])
    record['case_id'] = 'case-0001'
    inventory['records'].append(record)
    ar.dump(source / 'packets/case-0001.json', {'case_id': 'case-0001', 'events': []})
    ar.dump(path, inventory)
    with pytest.raises(ValueError, match='packet'):
        rr.prepare(source, out)
    assert not out.exists()


def test_prepared_projection_is_rederived_from_source_not_just_hashed(tmp_path):
    packets = prepared(tmp_path)
    units = copy.deepcopy(packets['identity']['units'])
    next(unit for unit in units if unit['kind'] == 'action')['timestamp_ns'] += 1
    changed = rr.make_packet('case-0000', 'run-1', 'r' * 64, units,
                             [event for event in packets['identity']['events']
                              if event['source'] != 'raw_occurrence'], kind='identity')
    changed['sanitization'] = packets['identity']['sanitization']
    ar.dump(tmp_path / 'identity-packets/case-0000.json', changed)
    ar.dump(tmp_path / 'review-packets/case-0000.json', changed)
    inventory = json.loads((tmp_path / 'inventory.json').read_text())
    inventory['cases'][0]['identity_packet_sha256'] = ar.sha(
        (tmp_path / 'identity-packets/case-0000.json').read_bytes())
    inventory['cases'][0]['review_packet_sha256'] = inventory['cases'][0]['identity_packet_sha256']
    ar.dump(tmp_path / 'inventory.json', inventory)
    write_phase_one(tmp_path, packets, target='a2')
    assert rr.finalize(tmp_path)['valid'] is False
    assert not (tmp_path / 'references').exists()


def test_direct_review_transport_cannot_bypass_native_paid_guard(tmp_path, monkeypatch):
    packets = prepared(tmp_path)
    monkeypatch.delenv('MTL_ALLOW_PAID', raising=False)
    monkeypatch.setattr(ar.subprocess, 'run',
                        lambda *a, **kw: pytest.fail('must not start provider'))
    with pytest.raises(ValueError, match='MTL_ALLOW_PAID'):
        ar.run_batch(tmp_path, [{'case_id': packets['identity']['case_id']}],
                     rr.REVIEWERS[0], reviewers=3, kind='identity')
    assert not (tmp_path / 'reviews').exists()


def test_native_review_preflights_source_changes_before_any_paid_call(tmp_path, monkeypatch):
    packets = prepared(tmp_path)
    monkeypatch.setenv('MTL_ALLOW_PAID', '1')
    source_database(tmp_path).write_text('changed before review')
    monkeypatch.setattr(ar.subprocess, 'run',
                        lambda *a, **kw: pytest.fail('source drift must stop provider'))
    with pytest.raises(ValueError, match='source'):
        ar.run_batch(tmp_path, [{'case_id': packets['identity']['case_id']}],
                     rr.REVIEWERS[0], reviewers=3, kind='identity')
    assert not (tmp_path / 'reviews').exists()


def test_reviewer_session_cannot_run_on_adjudicator_prompt(tmp_path):
    packets = prepared(tmp_path)
    disagree = scope_vote(packets['scope'], target_action='a2')
    disagree['target_action'] = None
    for role in rr.REVIEWERS:
        write_review(tmp_path, role, 'identity', identity_vote(packets['identity']))
        write_review(tmp_path, role, 'scope',
                     disagree if role == rr.REVIEWERS[-1]
                     else scope_vote(packets['scope'], target_action='a2'))
    assert rr.finalize(tmp_path)['valid'] is False
    assert (tmp_path / 'disputes.json').is_file()
    for path in (tmp_path / 'reviews').glob('*.jsonl'):
        path.unlink()
        path.with_suffix('.metadata.json').unlink()
    for role in rr.REVIEWERS:
        for kind, value in (('identity', identity_vote(packets['identity'])),
                            ('scope', scope_vote(packets['scope'], target_action='a2'))):
            prompt, hashes = ar.review_input(tmp_path, [value['case_id']], kind=kind,
                                             adjudicate=True)
            path = tmp_path / 'reviews' / f"{role}-{kind}-{value['case_id']}.jsonl"
            path.write_text('\n'.join(json.dumps(row) for row in [
                {'type': 'session', 'sessionId': f'synthetic-{role}-{kind}'},
                {'type': 'final', 'text': json.dumps([value])}]))
            (tmp_path / 'prompts' / (path.stem + '.txt')).write_text(prompt)
            ar.dump(path.with_suffix('.metadata.json'), {
                'reviewer': role, 'kind': kind, 'cases': [value['case_id']],
                'reviewers': 3, 'adjudication': True,
                'review_packet_sha256': hashes,
                'prompt_sha256': ar.sha(prompt.encode()), 'returncode': 0,
                'stdout_sha256': ar.sha(path.read_bytes()), 'fresh_session': True,
                'model': 'deepseek-flash', 'provider': 'deepseek-official'})
    assert rr.finalize(tmp_path)['valid'] is False
    assert not (tmp_path / 'references').exists()


def test_finalize_rejects_a_cohort_truncated_from_the_bound_raw_source(tmp_path):
    source, out = source_runtime(tmp_path, case_ids=('case-0000', 'case-0001'))
    rr.prepare(source, out)
    inventory = json.loads((out / 'inventory.json').read_text())
    assert [case['case_id'] for case in inventory['cases']] == ['case-0000', 'case-0001']
    packets = {kind: rr.load_packet(out, kind, 'case-0000')
               for kind in ('identity', 'scope')}
    write_phase_one(out, packets, target='a2')
    inventory['cases'] = [case for case in inventory['cases']
                          if case['case_id'] == 'case-0000']
    ar.dump(out / 'inventory.json', inventory)
    for kind in ('identity', 'scope'):
        (out / f'{kind}-packets/case-0001.json').unlink()
    (out / 'review-packets/case-0001.json').unlink()
    assert rr.finalize(out)['valid'] is False
    assert not (out / 'references').exists()


def test_reference_cli_prepare_finalize_verify_exit_codes(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    source, _ = source_runtime(tmp_path)
    out = tmp_path / 'cli-reference'
    env = {key: value for key, value in os.environ.items() if key != 'MTL_ALLOW_PAID'}

    def cli(*arguments):
        return subprocess.run([sys.executable, '-m', 'tools.annotator.ai_runtime', *arguments],
                              cwd=repo, capture_output=True, text=True, env=env)

    prepared_run = cli('reference-prepare', '--source-root', str(source), '--root', str(out))
    assert prepared_run.returncode == 0, prepared_run.stderr
    assert json.loads(prepared_run.stdout)['model_calls'] == 0
    packets = {kind: rr.load_packet(out, kind, 'case-0000')
               for kind in ('identity', 'scope')}
    write_phase_one(out, packets, target='a2')
    pending = cli('reference-finalize', '--root', str(out))
    assert pending.returncode == 1, pending.stderr
    assert json.loads(pending.stdout)['pending'] == {'localize': ['case-0000']}
    write_localize(out, packets)
    finalized = cli('reference-finalize', '--root', str(out))
    assert finalized.returncode == 0, finalized.stderr
    assert json.loads(finalized.stdout)['valid'] is True
    verified = cli('reference-verify', '--root', str(out))
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)['valid'] is True
    export = out / 'references/case-0000.json'
    payload = json.loads(export.read_text())
    payload['attribution']['units'][0]['root'] = {'status': 'unknown',
                                                  'evidence_refs': []}
    export.write_text(json.dumps(payload))
    drifted = cli('reference-verify', '--root', str(out))
    assert drifted.returncode == 1, drifted.stderr
    assert json.loads(drifted.stdout)['valid'] is False


def test_finalized_native_reference_binds_replay_source_database(tmp_path):
    from compare.tools import attribution_reference as attribution_adapter
    from compare.tools import identity_reference as identity_adapter
    packets = prepared(tmp_path)
    write_phase_one(tmp_path, packets, target='a2')
    assert rr.finalize(tmp_path)['valid'] is False
    write_localize(tmp_path, packets)
    assert rr.finalize(tmp_path)['valid'] is True
    digest = ar.sha(source_database(tmp_path).read_bytes())
    frozen = json.loads((tmp_path / 'references/case-0000.json').read_text())
    identity = identity_adapter.validate_reference(frozen['identity'])
    assert identity['source_database_sha256'] == digest
    attribution = attribution_adapter.validate_reference(frozen['attribution'])
    assert attribution['provenance']['source_database_sha256'] == digest
    assert attribution['runs'] == ['run-1']
    run_id = frozen['identity']['run_id']
    fingerprint = 'fp-1'
    experiment = {
        'schema': identity_adapter.EXPERIMENT_SCHEMA,
        'current_fingerprint': fingerprint,
        'source_database_sha256': digest,
        'runs': [{'run_id': run_id, 'source_fingerprint': fingerprint,
                  'current_fingerprint': fingerprint,
                  'views': [{'condition': identity_adapter.DEFAULT_CONDITION,
                             'units': [{'unit_id': unit_id,
                                        'actor': {'state': 'unknown'}}
                                       for unit_id in identity['events']]}]}]}
    projected = identity_adapter._project_experiment(
        experiment, identity, condition=identity_adapter.DEFAULT_CONDITION)
    assert set(projected['events']) == set(identity['events'])
    identity_adapter.validate_prediction(projected, identity)
    payload = json.loads((tmp_path / 'references/case-0000.json').read_text())
    payload['identity']['source_database_sha256'] = 'f' * 64
    (tmp_path / 'references/case-0000.json').write_text(json.dumps(payload))
    assert rr.verify(tmp_path)['valid'] is False


def test_native_reference_bundle_routes_to_recomputed_native_proof(tmp_path):
    from scripts.evaluation import reference_bundle as rb
    packets = prepared(tmp_path)
    root = tmp_path
    write_phase_one(root, packets, target='a2')
    assert rr.finalize(root)['valid'] is False
    write_localize(root, packets)
    assert rr.finalize(root)['valid'] is True
    bundle_path = tmp_path / 'bundle.json'
    bundle = rb.build(root, bundle_path)
    assert bundle['purpose'] == rr.PURPOSE
    assert bundle['coverage']['identity_known'] == len(packets['identity']['units'])
    assert rb.validate(bundle_path)['valid']
    path = root / 'references/case-0000.json'
    payload = json.loads(path.read_text())
    payload['identity']['events']['action:a1']['actor'] = 'invented'
    path.write_text(json.dumps(payload))
    assert rb.validate(bundle_path)['valid'] is False


# --------------------------------------------------------------------------- #
# Real multi-agent run regression (local data, skipped when absent).
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not REAL_DB.is_file(), reason='local real multi-agent run database is absent')
def test_real_multi_agent_run_scope_and_localize_packets():
    base = Path(tempfile.mkdtemp(prefix='p2-real-'))
    source = base / 'source'
    (source / 'run/collector').mkdir(parents=True)
    db = source / 'run/collector/finalized.db'
    os.link(REAL_DB, db)
    original = {'case_id': 'case-0000',
                'events': [{'ref': 'source-1', 'source': 'task_brief',
                            'data': {'text': 'observed task brief'}},
                           {'ref': 'source-2', 'source': 'trajectory',
                            'data': {'text': 'observed trajectory'}}],
                'selection': {'truncated': False}}
    ar.dump(source / 'packets/case-0000.json', original)
    ar.dump(source / 'inventory.json', {
        'cases': [{'case_id': 'case-0000',
                   'binding': {'data_release_id': 'r' * 64, 'scenario_id': 'real-multi'},
                   'packet_sha256': ar.sha((source / 'packets/case-0000.json').read_bytes())}],
        'records': [{'status': 'included', 'case_id': 'case-0000',
                     'ledger': str(source / 'run/runner-events.jsonl')}],
        'source_sha256': {str(db.resolve()): ar.sha(db.read_bytes())}})
    out = base / 'out'
    result = rr.prepare(source, out)  # must not raise on 17,873 actions
    assert result['cases'] == 1
    identity_bytes = (out / 'identity-packets/case-0000.json').stat().st_size
    scope_bytes = (out / 'scope-packets/case-0000.json').stat().st_size
    assert identity_bytes < 700_000
    assert scope_bytes < 700_000
    scope = rr.load_packet(out, 'scope', 'case-0000')
    assert len(scope['action_index']) >= 250  # security-relevant actions only
    assert len(scope['units']) >= 200
    target_index = next(row for row in scope['action_index']
                        if row['action_type'] == 'NetworkConnect')
    index = rr.materialize_localize(out, {'case-0000': target_index['action_id']},
                                    write=True)
    localize = rr.load_packet(out, 'localize', 'case-0000')
    assert len(localize['events']) <= 400
    assert localize['target_action'] == target_index['action_id']
    assert index['cases']['case-0000']['packet_bytes'] < 700_000
    # Synthetic scope -> localize vote roundtrip without any model call.
    scope_vote_obj = {'case_id': 'case-0000', 'attack_executed': 'yes',
                      'scope': {'agents': [], 'decisive_event_refs': []},
                      'target_action': target_index['action_id'],
                      'notes': 'synthetic real-data scope roundtrip'}
    rr.validate_vote(scope_vote_obj, scope)
    localize_vote_obj = {'case_id': 'case-0000',
                         'root': {'status': 'unknown', 'evidence_refs': []},
                         'entry': {'status': 'unknown', 'evidence_refs': []},
                         'chain': {'status': 'unknown', 'evidence_refs': []},
                         'notes': 'synthetic real-data localize roundtrip'}
    rr.validate_vote(localize_vote_obj, localize)
    identity = rr.load_packet(out, 'identity', 'case-0000')
    identity_vote_obj = {'case_id': 'case-0000',
                         'identities': {unit['unit_id']: {
                             'actor': None, 'evidence_refs': [],
                             'notes': 'synthetic unknown'}
                             for unit in identity['units']},
                         'notes': 'synthetic real-data identity roundtrip'}
    artifacts = export(
        identity, scope, localize, identity_vote_obj=identity_vote_obj,
        scope_vote_obj=scope_vote_obj, localize_vote_obj=localize_vote_obj)
    assert len(artifacts['attribution']['units']) == 1
    assert artifacts['attribution']['units'][0]['target_action_id'] == \
        target_index['action_id']
