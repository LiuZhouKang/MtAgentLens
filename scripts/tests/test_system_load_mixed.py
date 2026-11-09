# SPDX-License-Identifier: Apache-2.0 OR MIT
import json
import pytest
from scripts.evaluation import system_load_mixed as mixed


def _executable(tmp_path, name):
    path = tmp_path / name
    path.write_text('#!/bin/sh\nexit 0\n')
    path.chmod(0o700)
    return path


def _plan(tmp_path, **overrides):
    collector = _executable(tmp_path, 'collector')
    workload = _executable(tmp_path, 'paced_ssl')
    kwargs = dict(collector=collector, workload=workload, out=tmp_path / 'out',
                  concurrency=[1, 4], iterations=20, interval_ms=100)
    kwargs.update(overrides)
    return mixed.make_plan(**kwargs)


def test_plan_rejects_invalid_concurrency_and_iterations(tmp_path):
    with pytest.raises(ValueError, match='distinct'):
        _plan(tmp_path, concurrency=[1, 1])
    with pytest.raises(ValueError, match='between 1 and 32'):
        _plan(tmp_path, concurrency=[0, 4])
    with pytest.raises(ValueError, match='iterations'):
        _plan(tmp_path, iterations=5)
    with pytest.raises(ValueError, match='interval-ms'):
        _plan(tmp_path, interval_ms=1)


def test_plan_freezes_hashes_and_operation_mix(tmp_path):
    plan = _plan(tmp_path)
    assert plan['schema'] == mixed.SCHEMA
    assert plan['tls_enabled'] is True and plan['process_enabled'] is True
    assert plan['observer_enabled'] is False
    assert plan['operation_mix'] == {
        'tls_echo_per_iteration': 1, 'file_open_per_iteration': 1,
        'exec_every_iterations': 10}
    assert len(plan['collector_sha256']) == 64
    assert plan['workload_sha256'] == mixed.sha256(plan['workload'])


def test_collector_command_requests_tls_process_and_no_observer(tmp_path):
    command = mixed.collector_command('/bin/collector', 'paced_ssl', '/tmp/trace.log')
    assert command[1:4] == ['trace', '--ssl=true', '--process=true']
    assert command[command.index('--comm') + 1] == 'paced_ssl'
    assert '--disable-responsibility-observer' in command
    assert '--capture-only' not in command
    assert command[command.index('-o') + 1] == '/tmp/trace.log'


def test_expected_operations_cover_every_worker_and_iteration():
    operations = mixed.expected_operations(3, 4)
    assert len(operations) == 12
    assert operations[0]['path'] == 'fixture-w0/op-0000'
    assert operations[-1]['path'] == 'fixture-w2/op-0003'
    assert mixed.exec_expectation(100) == 10
    assert mixed.exec_expectation(15) == 1


def _quality(**overrides):
    source = {'lost': 0, 'rate_limited': 0, 'exit_status': 0,
              'final_stats_seen': True, 'drain_complete': True,
              'stdout_skipped': 0, 'stdout_error': False, 'stderr_error': False,
              'forced_kill': False}
    sources = {'Process:1': dict(source), 'SSL:2': dict(source)}
    analysis = {'persist_dropped': 0, 'persist_closed': 0, 'persist_failures': 0,
                'raw_log_failures': 0, 'persist_flush_failed': False,
                'event_counts': {'process': 10, 'ssl': 20}}
    quality = {'sources': sources, 'analysis': analysis}
    for path, value in overrides.items():
        node = quality
        keys = path.split('__')
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return quality


def test_capture_health_accepts_both_sources_and_reports_losses():
    health = mixed.capture_health(_quality())
    assert health['healthy'] is True
    assert health['lost_events'] == 0
    assert health['event_counts'] == {'process': 10, 'ssl': 20}
    unhealthy = _quality()
    unhealthy['sources']['SSL:2']['lost'] = 5
    unhealthy['sources']['SSL:2']['final_stats_seen'] = False
    health = mixed.capture_health(unhealthy)
    assert health['healthy'] is False
    assert health['ssl_lost_events'] == 5
    assert health['lost_events'] == 5


def test_capture_health_requires_process_and_ssl_telemetry():
    missing_ssl = _quality()
    del missing_ssl['sources']['SSL:2']
    with pytest.raises(ValueError, match='Process and SSL'):
        mixed.capture_health(missing_ssl)
