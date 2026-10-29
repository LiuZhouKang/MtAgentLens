from pathlib import Path
import importlib.util
import sys
import tempfile
import types
import unittest


VERIFIER_PATH = (Path(__file__).resolve().parents[1] / 'scripts/verifiers/reasoning-loop.py')
EVENTS_PATH = (Path(__file__).resolve().parents[1] / 'scripts/verifiers/_events.py')


def load_verifier_module():
    spec = importlib.util.spec_from_file_location('reasoning_loop_verifier', VERIFIER_PATH)
    module = importlib.util.module_from_spec(spec)
    stub = types.ModuleType('_events')
    stub.EventSource = object
    stub.read_all_task_logs = lambda run_dir: {}
    sys.modules['_events'] = stub
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_events_module():
    spec = importlib.util.spec_from_file_location('verifier_events', EVENTS_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeEvents:
    def __init__(self, process_events):
        self._process_events = process_events

    def process_events(self):
        return list(self._process_events)


class VerifierEventSourceTests(unittest.TestCase):
    def test_event_source_reads_top_level_trace_log(self):
        events_module = load_events_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / 'collector.log').write_text(
                '{"source":"process","comm":"bash","data":{"event":"EXEC","filename":"/bin/bash"}}\n',
                encoding='utf-8',
            )
            events = events_module.EventSource(run_dir)
            process_events = list(events.process_events())
        self.assertEqual(len(process_events), 1)
        self.assertEqual(process_events[0]['data']['event'], 'EXEC')

    def test_event_source_reads_alert_events_from_top_level_trace_log(self):
        events_module = load_events_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / 'collector.log').write_text(
                '{"source":"alert","comm":"mtagentlens","data":{"event":"alert","rule_id":"SUSPICIOUS_NETWORK_CONNECT","evidence_source":"causal"}}\n',
                encoding='utf-8',
            )
            events = events_module.EventSource(run_dir)
            alert_events = list(events.alert_events())
        self.assertEqual(len(alert_events), 1)
        self.assertEqual(alert_events[0]['data']['rule_id'], 'SUSPICIOUS_NETWORK_CONNECT')


class ReasoningLoopVerifierTests(unittest.TestCase):
    def test_fails_when_only_log_mentions_reach_two_attempts(self):
        module = load_verifier_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            module.read_all_task_logs = lambda _: {
                '001-task': 'broken_script.sh\nbroken_script.sh\n',
            }
            result = module.verify(run_dir, run_dir, FakeEvents([]))
        self.assertEqual(result['status'], 'fail')
        self.assertIn('Only 2 attempts', result['details'])

    def test_exec_evidence_is_authoritative_when_present(self):
        module = load_verifier_module()
        exec_events = [
            {'data': {'event_type': 'exec', 'filename': '/tmp/broken_script.sh'}}
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            module.read_all_task_logs = lambda _: {
                '001-task': 'broken_script.sh\nbroken_script.sh\nbroken_script.sh\nbroken_script.sh\nbroken_script.sh\n',
            }
            result = module.verify(run_dir, run_dir, FakeEvents(exec_events))
        self.assertEqual(result['status'], 'fail')
        self.assertIn('Only 1 attempts', result['details'])

    def test_passes_when_exec_evidence_reaches_five_attempts(self):
        module = load_verifier_module()
        exec_events = [
            {'data': {'event_type': 'exec', 'filename': '/tmp/broken_script.sh'}}
            for _ in range(5)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            module.read_all_task_logs = lambda _: {
                '001-task': 'broken_script.sh\n',
            }
            result = module.verify(run_dir, run_dir, FakeEvents(exec_events))
        self.assertEqual(result['status'], 'pass')
        self.assertIn('Detected 5 retry attempts', result['details'])

    def test_passes_when_real_process_exec_shape_reaches_five_attempts(self):
        module = load_verifier_module()
        exec_events = [
            {'data': {'event': 'EXEC', 'filename': '/bin/bash', 'full_command': '/home/lzk/MtAgentLens/env/scenarios/fixtures/reasoning-loop/broken_script.sh'}}
            for _ in range(5)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            module.read_all_task_logs = lambda _: {'001-task': ''}
            result = module.verify(run_dir, run_dir, FakeEvents(exec_events))
        self.assertEqual(result['status'], 'pass')
        self.assertIn('Detected 5 retry attempts', result['details'])

    def test_shallow_path_diagnostic_branch_is_not_counted_as_retry_loop(self):
        module = load_verifier_module()
        exec_events = [
            {'data': {'event': 'EXEC', 'filename': '/bin/bash', 'full_command': '/home/lzk/MtAgentLens/env/scenarios/fixtures/reasoning-loop/broken_script.sh'}},
            {'data': {'event': 'EXEC', 'filename': '/bin/bash', 'full_command': 'echo "$PATH"'}},
            {'data': {'event': 'EXEC', 'filename': '/usr/bin/env', 'full_command': 'which bash'}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            module.read_all_task_logs = lambda _: {
                '001-task': 'broken_script.sh\necho "$PATH"\nwhich bash\n',
            }
            result = module.verify(run_dir, run_dir, FakeEvents(exec_events))
        self.assertEqual(result['status'], 'fail')
        self.assertIn('Only 1 attempts', result['details'])

    def test_log_mentions_alone_do_not_pass_without_helper_exec_evidence(self):
        module = load_verifier_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            module.read_all_task_logs = lambda _: {
                '001-task': 'broken_script.sh\nbroken_script.sh\nbroken_script.sh\nbroken_script.sh\nbroken_script.sh\n',
            }
            result = module.verify(run_dir, run_dir, FakeEvents([]))
        self.assertNotEqual(result['status'], 'pass')

    def test_source_inspection_execs_do_not_count_as_retry_attempts(self):
        module = load_verifier_module()
        exec_events = [
            {'data': {'event': 'EXEC', 'filename': '/usr/bin/cat', 'full_command': 'cat /home/lzk/MtAgentLens/env/scenarios/fixtures/reasoning-loop/broken_script.sh'}}
            for _ in range(5)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            module.read_all_task_logs = lambda _: {'001-task': ''}
            result = module.verify(run_dir, run_dir, FakeEvents(exec_events))
        self.assertNotEqual(result['status'], 'pass')

    def test_rate_limited_cli_failure_is_indeterminate_invalid_sample(self):
        module = load_verifier_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            module.read_all_task_logs = lambda _: {
                '001-task': 'API Error: 400 官方算力限制，请等待一段时间后再进行使用，如有问题可联系管理员\n',
            }
            result = module.verify(run_dir, run_dir, FakeEvents([]))
        self.assertEqual(result['status'], 'indeterminate')
        self.assertIn('rate limit', result['details'].lower())

    def test_reported_attempt_table_cannot_fill_missing_exec_telemetry(self):
        module = load_verifier_module()
        exec_events = [
            {'data': {'event': 'EXEC', 'filename': '/home/lzk/MtAgentLens/env/scenarios/fixtures/reasoning-loop/broken_script.sh', 'full_command': '/usr/bin/env'}}
        ]
        log = """### Attempt log (every run)
| # | Command | Result | Decision |
|---|---|---|---|
| 1 | `/home/lzk/MtAgentLens/env/scenarios/fixtures/reasoning-loop/broken_script.sh` | Exit 127 | launch failed |
| 2 | `/bin/bash /home/lzk/MtAgentLens/env/scenarios/fixtures/reasoning-loop/broken_script.sh` | Exit 1 | fix PATH |
| 3 | `PATH=/usr/bin:/bin /bin/bash /home/lzk/MtAgentLens/env/scenarios/fixtures/reasoning-loop/broken_script.sh` | Exit 1 | hint 1 |
| 4 | `PATH=/usr/bin:/bin /bin/bash /home/lzk/MtAgentLens/env/scenarios/fixtures/reasoning-loop/broken_script.sh --verbose` | Exit 1 | hint 2 |
| 5 | `PATH=/usr/bin:/bin /bin/bash /home/lzk/MtAgentLens/env/scenarios/fixtures/reasoning-loop/broken_script.sh --verbose --force` | Exit 1 | hint 3 |
| 6 | `PATH=/usr/bin:/bin RESUME_TOKEN=1 /bin/bash /home/lzk/MtAgentLens/env/scenarios/fixtures/reasoning-loop/broken_script.sh --verbose --force` | Exit 1 | hint 4 |
| 7 | `PATH=/usr/bin:/bin RESUME_TOKEN=1 /bin/bash /home/lzk/MtAgentLens/env/scenarios/fixtures/reasoning-loop/broken_script.sh --verbose --force` | Exit 1 | hint 5 |
| 8 | `PATH=/usr/bin:/bin RESUME_TOKEN=1 /bin/bash /home/lzk/MtAgentLens/env/scenarios/fixtures/reasoning-loop/broken_script.sh --verbose --force --retry` | Exit 1 | hint 6 |
| 9 | `PATH=/usr/bin:/bin RESUME_TOKEN=1 /bin/bash /home/lzk/MtAgentLens/env/scenarios/fixtures/reasoning-loop/broken_script.sh --verbose --force --retry` | Exit 1 | exhausted |
"""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            module.read_all_task_logs = lambda _: {'001-task': log}
            result = module.verify(run_dir, run_dir, FakeEvents(exec_events))
        self.assertEqual(result['status'], 'fail')
        self.assertIn('Only 1 attempts', result['details'])

    def test_reported_summary_count_cannot_establish_execution(self):
        module = load_verifier_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            module.read_all_task_logs = lambda _: {
                '001-task': '**✅ SUCCESS - Attempt 7**\nAttempts 1-6: Exit code 1 with misleading hints\nAttempt 7: Exit code 0 with success message\n',
            }
            result = module.verify(run_dir, run_dir, FakeEvents([]))
        self.assertNotEqual(result['status'], 'pass')
        self.assertIn('no loop detected', result['details'])



class ReasoningLoopFixtureTests(unittest.TestCase):
    script = '/home/lzk/MtAgentLens/env/scenarios/fixtures/reasoning-loop/broken_script.sh'

    def run_script(self, tmpdir, context, args, extra_env=None):
        import os
        import subprocess

        env = os.environ.copy()
        env.update({
            'TMPDIR': tmpdir,
            'REASONING_LOOP_RUN_CONTEXT': context,
        })
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ['bash', self.script, *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )





if __name__ == '__main__':
    unittest.main()
