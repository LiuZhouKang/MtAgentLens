# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Check sampled process contexts; never assert a kernel PID/event mapping."""
import uuid

from tools.annotator.evidence import _loads_strict

NAMESPACES = {'pid', 'pid_for_children', 'time', 'time_for_children', 'mnt'}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _snapshot(value):
    _require(isinstance(value, dict) and set(value) == {
        'schema', 'pid', 'procfs_pid', 'procfs_start_ticks', 'nspid', 'boot_id',
        'namespaces', 'time_offsets_ns', 'pid_space_status'}, 'Invalid collector process context')
    _require(value['schema'] == 'mtagentlens.collector-process/v1', 'Unsupported collector process context')
    _require(type(value['pid']) is int and value['pid'] > 0, 'Invalid collector pid')
    for field in ('procfs_pid', 'procfs_start_ticks'):
        _require(value[field] is None or (type(value[field]) is int and value[field] > 0),
                 'Invalid collector procfs identity')
    nspid = value['nspid']
    _require(nspid is None or (isinstance(nspid, list) and nspid and
             all(type(pid) is int and pid > 0 for pid in nspid)), 'Invalid collector NSpid')
    boot = value['boot_id']
    if boot is not None:
        _require(isinstance(boot, str), 'Invalid collector boot id')
        try:
            canonical = str(uuid.UUID(boot))
        except ValueError as exc:
            raise ValueError('Invalid collector boot id') from exc
        _require(canonical == boot, 'Noncanonical collector boot id')
    spaces = value['namespaces']
    _require(isinstance(spaces, dict) and set(spaces) == NAMESPACES, 'Invalid collector namespaces')
    for space in spaces.values():
        _require(space is None or (isinstance(space, dict) and set(space) == {'device', 'inode'} and
                 type(space['device']) is int and space['device'] >= 0 and
                 type(space['inode']) is int and space['inode'] > 0), 'Invalid collector namespace identity')
    offsets = value['time_offsets_ns']
    _require(isinstance(offsets, dict) and set(offsets) == {'monotonic', 'boottime'} and
             all(offset is None or type(offset) is int for offset in offsets.values()), 'Invalid collector time offsets')
    status = value['pid_space_status']
    _require(status in ('observed', 'unavailable', 'pid_space_mismatch'), 'Invalid collector context status')
    if status == 'observed':
        _require(value['pid'] == value['procfs_pid'] and nspid == [value['pid']] and
                 value['procfs_start_ticks'] is not None, 'Inconsistent observed collector context')
    if status == 'pid_space_mismatch':
        _require((value['procfs_pid'] is not None and value['procfs_pid'] != value['pid']) or
                 (nspid is not None and nspid != [value['pid']]), 'Unsubstantiated collector PID mismatch')


def read_capture_quality(connection, run_id):
    """Read checkpoint metadata only under its recorded run/analysis contract."""
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'analysis_checkpoints' not in tables:
        return None
    rows = connection.execute("SELECT version,payload_json FROM analysis_checkpoints WHERE run_id=? AND checkpoint_kind='frozen-analysis'", (run_id,)).fetchall()
    if not rows:
        return None
    _require(len(rows) == 1, 'Ambiguous collector analysis checkpoint')
    from tools.annotator.payload_codec import decode_checkpoint_payload
    payload = _loads_strict(decode_checkpoint_payload(rows[0][1]))
    _require(isinstance(payload, dict), 'Invalid collector checkpoint')
    quality = payload.get('capture_quality')
    if not isinstance(quality, dict) or not ({'recording_context', 'process_namespace_mappings', 'initial_process_snapshot'} & set(quality)):
        return None
    _require(rows[0][0] == 1 and payload.get('run_id') == run_id and
             payload.get('contract') == 'frozen-evidence-reanalysis-v1', 'Collector context checkpoint mismatch')
    return quality


def read_collector_context(connection, run_id):
    """Absent historical context stays absent; no reconstruction from replay."""
    quality = read_capture_quality(connection, run_id)
    if quality is None or 'recording_context' not in quality:
        return None
    context = quality['recording_context']
    _require(isinstance(context, dict) and set(context) == {'schema', 'start', 'finish'} and
             context['schema'] == 'mtagentlens.collector-context/v1', 'Unsupported collector context')
    for name in ('start', 'finish'):
        # A runtime finalized without an initialized session cannot claim a start.
        if context[name] is not None:
            _snapshot(context[name])
    return context


def compare_process_context(rows, context):
    """Compare validated native and collector samples, without event alignment."""
    before, after = context['start'], context['finish']
    stable = before is not None and after is not None and before == after
    complete = stable and before['pid_space_status'] == 'observed' and before['boot_id'] is not None
    if complete:
        complete = all(before['namespaces'][name] is not None for name in NAMESPACES)
        complete &= all(value is not None for value in before['time_offsets_ns'].values())
    actors = []
    for row in rows:
        if row['event_type'] != 'process_started':
            continue
        reasons = []
        observation = row['details'].get('observation')
        if not complete:
            reasons.append('collector_context_incomplete_or_changed')
        if observation is None or observation['status'] != 'observed':
            reasons.append('native_process_context_unavailable')
        elif complete:
            if row['process_key']['boot_id'] != before['boot_id']:
                reasons.append('boot_id_mismatch')
            for entity in (observation['observer'], observation['target']):
                if entity['namespaces'] != before['namespaces']:
                    reasons.append('namespace_context_mismatch')
                if entity['time_offsets_ns'] != before['time_offsets_ns']:
                    reasons.append('time_offset_mismatch')
        actors.append({'actor_id': row['actor_id'], 'status': 'unproven' if reasons else 'consistent_samples',
                       'reasons': sorted(set(reasons))})
    return {'schema': 'mtagentlens.process-context-check/v1',
            'status': 'consistent_samples' if actors and all(actor['status'] == 'consistent_samples' for actor in actors) else 'unproven',
            'actors': actors, 'event_alignment_available': False,
            'limitations': ['kernel_pid_mapping_unverified', 'event_alignment_unverified', 'non_atomic_namespace_samples']}
