# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Join native launches to kernel EXEC witnesses, independently of detector IDs."""
import json

from scripts.evaluation.collector_context import read_capture_quality, _require

_INCOMPLETE_REASONS = frozenset({
    'runner_clock_context_unavailable',
    'runner_clock_context_incomplete',
    'runner_boot_id_unavailable',
})


def read_kernel_mappings(connection, run_id):
    quality = read_capture_quality(connection, run_id)
    if quality is None or 'process_namespace_mappings' not in quality:
        return None
    mappings = quality['process_namespace_mappings']
    _require(isinstance(mappings, dict) and set(mappings) == {'schema','records','dropped'} and
             mappings['schema'] in ('mtagentlens.kernel-process-mappings/v1','mtagentlens.kernel-process-mappings/v2'), 'Invalid kernel mapping schema')
    _require(type(mappings['dropped']) is int and mappings['dropped'] >= 0,
             'Invalid kernel mapping overflow count')
    _require(isinstance(mappings['records'], list) and len(mappings['records']) <= 8192,
             'Invalid kernel mapping records')
    seen = set()
    for record in mappings['records']:
        fields = {'pid','start_time_ns','pid_namespace','pid_namespace_error'}
        if mappings['schema'] == 'mtagentlens.kernel-process-mappings/v2':
            fields |= {'pgid','cgroup_id'}
        _require(isinstance(record, dict) and set(record) == fields, 'Invalid kernel mapping fields')
        if 'pgid' in fields:
            for field, maximum in (('pgid',4294967295),('cgroup_id',18446744073709551615)):
                _require(record[field] is None or (type(record[field]) is int and
                         0 < record[field] <= maximum), 'Invalid kernel EXEC metadata')
        _require(all(type(record[key]) is int and record[key] > 0 for key in ('pid','start_time_ns')),
                 'Invalid kernel process identity')
        error, namespace = record['pid_namespace_error'], record['pid_namespace']
        _require(type(error) is int and error <= 0, 'Invalid kernel namespace helper status')
        if namespace is not None:
            _require(error == 0 and isinstance(namespace, dict) and set(namespace) == {'device','inode','tgid'} and
                     all(type(namespace[key]) is int and namespace[key] > 0 for key in namespace),
                     'Invalid mapped kernel PID namespace')
        encoded = json.dumps(record, sort_keys=True, separators=(',',':'))
        _require(encoded not in seen, 'Duplicate kernel mapping record')
        seen.add(encoded)
    return mappings


def _complete_context(value):
    """A context whose namespace and clock fields were all readable."""
    return (all(item is not None for item in value['namespaces'].values()) and
            all(item is not None for item in value['time_offsets_ns'].values()))


def _witness_problem(witness, observation, boot_id):
    """Compare the pre-launch runner sample with the post-launch observer view.

    Missing endpoint fields are an unavailable witness, not evidence that the
    runner changed namespace; only two complete endpoints can disagree.
    """
    if witness is None:
        return None
    if witness['status'] != 'observed':
        return 'runner_clock_context_unavailable'
    pre, post = witness['observer'], observation['observer']
    if not _complete_context(pre) or not _complete_context(post):
        return 'runner_clock_context_incomplete'
    if witness['boot_id'] is None or boot_id is None:
        return 'runner_boot_id_unavailable'
    if witness['boot_id'] != boot_id:
        return 'runner_boot_changed_across_launch'
    if (pre['procfs_pid'], pre['pid'], pre['nspid']) != (post['procfs_pid'], post['pid'], post['nspid']):
        return 'runner_pid_space_changed_across_launch'
    if pre['namespaces'] != post['namespaces']:
        return 'runner_namespace_changed_across_launch'
    if pre['time_offsets_ns'] != post['time_offsets_ns']:
        return 'runner_clock_offset_changed_across_launch'
    return None


def align_kernel_processes(rows, context, mappings):
    """Match top-level invocation processes only; no attack/root-cause labels."""
    actors, attempts, witnesses, witness_incomplete = [], {}, [], False
    for row in rows:
        if row['event_type'] == 'launch_attempt':
            witness = row['details'].get('runner_context')
            if witness is not None:
                witnesses.append(witness)
                # Witness usability is independent of the later observation:
                # an unreadable endpoint stays incomplete even if the child
                # itself could not be observed.
                witness_incomplete = witness_incomplete or (
                    witness['status'] != 'observed'
                    or witness['boot_id'] is None
                    or not _complete_context(witness['observer'])
                )
            attempts[row['actor_id']] = {'timestamp_ns': row['timestamp_ns'],
                                         'runner_context': witness}
        if row['event_type'] != 'process_started':
            continue
        reasons, candidates, window = [], {}, None
        attempt = attempts[row['actor_id']]
        observation = row['details'].get('observation')
        before = context['start'] if context else None
        after = context['finish'] if context else None
        if not before or not after or before != after or before['pid_space_status'] != 'observed':
            reasons.append('collector_context_unavailable_or_changed')
        if mappings['dropped']:
            reasons.append('kernel_mapping_capture_incomplete')
        if observation is None or observation['status'] != 'observed':
            reasons.append('native_process_context_unavailable')
        else:
            problem = _witness_problem(attempt['runner_context'], observation,
                                       row['process_key']['boot_id'])
            if problem is not None:
                reasons.append(problem)
                witness_incomplete = witness_incomplete or problem in _INCOMPLETE_REASONS
        if not reasons:
            namespace = observation['target']['namespaces']['pid']
            offset = observation['observer']['time_offsets_ns']['monotonic']
            if row['process_key']['boot_id'] is None or row['process_key']['boot_id'] != before['boot_id']:
                reasons.append('boot_id_mismatch_or_unavailable')
            if namespace is None:
                reasons.append('target_namespace_unavailable')
            if offset is None:
                reasons.append('runner_clock_offset_unavailable')
            if not reasons:
                window = [attempt['timestamp_ns']-offset, row['timestamp_ns']-offset]
                tgid = observation['target']['nspid'][-1]
                for record in mappings['records']:
                    mapped = record['pid_namespace']
                    if mapped == dict(namespace, tgid=tgid) and record['pid_namespace_error'] == 0 and \
                            window[0] <= record['start_time_ns'] <= window[1]:
                        identity = (record['pid'], record['start_time_ns'])
                        candidates[identity] = {'pid': identity[0], 'start_time_ns': identity[1]}
                if not candidates:
                    reasons.append('no_matching_kernel_process')
                elif len(candidates) != 1:
                    reasons.append('ambiguous_kernel_process')
        status = ('matched' if not reasons else 'ambiguous' if 'ambiguous_kernel_process' in reasons else 'unproven')
        actors.append({'actor_id':row['actor_id'], 'native_pid':row['process_key']['pid'], 'status':status,
                       'kernel_process':next(iter(candidates.values())) if status == 'matched' else None,
                       'kernel_monotonic_birth_window_ns':window, 'reasons':reasons})
    # Even a unique process-start match is not a tool-call/action/root-cause alignment.
    if not witnesses:
        clock_limitation = 'runner_clock_namespace_assumed_stable_across_launch'
    elif witness_incomplete:
        clock_limitation = 'runner_clock_witness_incomplete'
    else:
        clock_limitation = 'runner_clock_endpoints_sampled_continuity_unproven'
    limitations = ['not_internal_agent_identity','not_attack_or_root_cause_truth',
                   'non_atomic_samples', clock_limitation]
    return {'schema':'mtagentlens.kernel-process-alignment/v1', 'scope':'top_level_invocation_process',
            'status':'matched' if actors and all(actor['status']=='matched' for actor in actors) else 'unproven',
            'actors':actors, 'event_alignment_available':False,
            'limitations':limitations}


def read_initial_snapshot(connection, run_id):
    quality=read_capture_quality(connection,run_id)
    if quality is None or 'initial_process_snapshot' not in quality:
        return None
    snapshot=quality['initial_process_snapshot']
    _require(isinstance(snapshot,dict) and set(snapshot)=={'schema','records','dropped'} and
             snapshot['schema']=='mtagentlens.kernel-process-snapshot/v1','Invalid initial snapshot schema')
    _require(type(snapshot['dropped']) is int and snapshot['dropped']>=0 and
             isinstance(snapshot['records'],list) and len(snapshot['records'])<=8192,'Invalid snapshot record limits')
    fields={'pid','start_time_ns','parent_pid','parent_start_time_ns','comm','pgid','cgroup_id',
            'kernel_timestamp_ns','monitor_pid_namespace','monitor_pid_namespace_error'}
    for record in snapshot['records']:
        _require(isinstance(record,dict) and set(record)==fields,'Invalid snapshot record fields')
        _require(all(type(record[key]) is int and record[key]>0 for key in ('pid','start_time_ns')) and
                 isinstance(record['comm'],str),'Invalid snapshot process identity')
        for field,maximum in (('parent_pid',4294967295),('parent_start_time_ns',18446744073709551615),
                              ('pgid',4294967295),('cgroup_id',18446744073709551615),('kernel_timestamp_ns',18446744073709551615)):
            value=record[field]
            minimum=0 if field.startswith('parent_') else 1
            _require(value is None or (type(value) is int and minimum<=value<=maximum),'Invalid snapshot numeric metadata')
        namespace,error=record['monitor_pid_namespace'],record['monitor_pid_namespace_error']
        _require(type(error) is int and error<=0,'Invalid snapshot namespace status')
        if namespace is None:
            _require(error<0,'Missing successful snapshot mapping')
        else:
            _require(error==0 and isinstance(namespace,dict) and set(namespace)=={'device','inode','tgid'} and
                     all(type(value) is int and value>0 for value in namespace.values()),'Invalid monitor namespace mapping')
    return snapshot
