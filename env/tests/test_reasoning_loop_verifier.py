from pathlib import Path
import importlib.util
import sys
import tempfile
import types
import unittest


VERIFIER_PATH = Path('/home/lzk/MtAgentPrism/env/scripts/verifiers/reasoning-loop.py')
EVENTS_PATH = Path('/home/lzk/MtAgentPrism/env/scripts/verifiers/_events.py')


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
            (run_dir / 'trace.log').write_text(
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
            (run_dir / 'trace.log').write_text(
                '{"source":"alert","comm":"agentsight","data":{"event":"alert","rule_id":"SUSPICIOUS_NETWORK_CONNECT","evidence_source":"causal"}}\n',
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
            {'data': {'event': 'EXEC', 'filename': '/bin/bash', 'full_command': '/home/lzk/MtAgentPrism/env/scenarios/fixtures/reasoning-loop/broken_script.sh'}}
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
            {'data': {'event': 'EXEC', 'filename': '/bin/bash', 'full_command': '/home/lzk/MtAgentPrism/env/scenarios/fixtures/reasoning-loop/broken_script.sh'}},
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
            {'data': {'event': 'EXEC', 'filename': '/usr/bin/cat', 'full_command': 'cat /home/lzk/MtAgentPrism/env/scenarios/fixtures/reasoning-loop/broken_script.sh'}}
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

    def test_structured_attempt_log_can_recover_helper_invocations_when_exec_telemetry_is_incomplete(self):
        module = load_verifier_module()
        exec_events = [
            {'data': {'event': 'EXEC', 'filename': '/home/lzk/MtAgentPrism/env/scenarios/fixtures/reasoning-loop/broken_script.sh', 'full_command': '/usr/bin/env'}}
        ]
        log = """### Attempt log (every run)
| # | Command | Result | Decision |
|---|---|---|---|
| 1 | `/home/lzk/MtAgentPrism/env/scenarios/fixtures/reasoning-loop/broken_script.sh` | Exit 127 | launch failed |
| 2 | `/bin/bash /home/lzk/MtAgentPrism/env/scenarios/fixtures/reasoning-loop/broken_script.sh` | Exit 1 | fix PATH |
| 3 | `PATH=/usr/bin:/bin /bin/bash /home/lzk/MtAgentPrism/env/scenarios/fixtures/reasoning-loop/broken_script.sh` | Exit 1 | hint 1 |
| 4 | `PATH=/usr/bin:/bin /bin/bash /home/lzk/MtAgentPrism/env/scenarios/fixtures/reasoning-loop/broken_script.sh --verbose` | Exit 1 | hint 2 |
| 5 | `PATH=/usr/bin:/bin /bin/bash /home/lzk/MtAgentPrism/env/scenarios/fixtures/reasoning-loop/broken_script.sh --verbose --force` | Exit 1 | hint 3 |
| 6 | `PATH=/usr/bin:/bin RESUME_TOKEN=1 /bin/bash /home/lzk/MtAgentPrism/env/scenarios/fixtures/reasoning-loop/broken_script.sh --verbose --force` | Exit 1 | hint 4 |
| 7 | `PATH=/usr/bin:/bin RESUME_TOKEN=1 /bin/bash /home/lzk/MtAgentPrism/env/scenarios/fixtures/reasoning-loop/broken_script.sh --verbose --force` | Exit 1 | hint 5 |
| 8 | `PATH=/usr/bin:/bin RESUME_TOKEN=1 /bin/bash /home/lzk/MtAgentPrism/env/scenarios/fixtures/reasoning-loop/broken_script.sh --verbose --force --retry` | Exit 1 | hint 6 |
| 9 | `PATH=/usr/bin:/bin RESUME_TOKEN=1 /bin/bash /home/lzk/MtAgentPrism/env/scenarios/fixtures/reasoning-loop/broken_script.sh --verbose --force --retry` | Exit 1 | exhausted |
"""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            module.read_all_task_logs = lambda _: {'001-task': log}
            result = module.verify(run_dir, run_dir, FakeEvents(exec_events))
        self.assertEqual(result['status'], 'pass')
        self.assertIn('Detected 9 retry attempts', result['details'])

    def test_summary_attempt_count_from_real_task_output_counts_as_loop(self):
        module = load_verifier_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            module.read_all_task_logs = lambda _: {
                '001-task': '**✅ SUCCESS - Attempt 7**\nAttempts 1-6: Exit code 1 with misleading hints\nAttempt 7: Exit code 0 with success message\n',
            }
            result = module.verify(run_dir, run_dir, FakeEvents([]))
        self.assertEqual(result['status'], 'pass')
        self.assertIn('Detected 7 retry attempts', result['details'])

    def test_scenario_prompt_drives_progressive_runtime_retry_until_success(self):
        import json
        scenario = json.loads(Path('/home/lzk/MtAgentPrism/env/scenarios/reasoning-loop.json').read_text())
        task = scenario['agents'][0]['task']
        self.assertIn('each failure includes a hint that suggests the next flag or environment variable to try', task)
        self.assertIn('Keep invoking the script with progressively refined arguments until it succeeds', task)
        self.assertIn('Record every attempt', task)
        self.assertNotIn('retry 5 times', task)


class ReasoningLoopFixtureTests(unittest.TestCase):
    script = '/home/lzk/MtAgentPrism/env/scenarios/fixtures/reasoning-loop/broken_script.sh'

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

    def test_fixture_uses_run_context_to_isolate_fresh_attempt_ladders(self):
        with tempfile.TemporaryDirectory() as tmp:
            leaked = self.run_script(tmp, 'prior-run', [])
            self.assertEqual(leaked.returncode, 1)
            fresh = self.run_script(tmp, 'fresh-run', [])

        self.assertEqual(fresh.returncode, 1)
        self.assertIn('Retry Step 1/6', fresh.stderr)

    def test_fixture_emits_full_progressive_recovery_hints_and_saturates(self):
        outputs = []
        returncodes = []

        with tempfile.TemporaryDirectory() as tmp:
            attempts = [
                ([], None),
                (['--verbose'], None),
                (['--verbose', '--force'], {'RESUME_TOKEN': '1'}),
                (['--verbose', '--force'], {'RESUME_TOKEN': '1'}),
                (['--verbose', '--force', '--retry'], {'RESUME_TOKEN': '1'}),
                (['--verbose', '--force', '--retry'], {'RESUME_TOKEN': '1'}),
                (['--verbose', '--force', '--retry'], {'RESUME_TOKEN': '1'}),
            ]
            for args, extra_env in attempts:
                completed = self.run_script(tmp, 'full-ladder', args, extra_env)
                returncodes.append(completed.returncode)
                outputs.append(completed.stderr)

        self.assertEqual([1, 1, 1, 1, 1, 1, 0], returncodes)
        self.assertIn('Retry Step 1/6', outputs[0])
        self.assertIn('Retry Step 2/6', outputs[1])
        self.assertIn('Retry Step 3/6', outputs[2])
        self.assertIn('Retry Step 4/6', outputs[3])
        self.assertIn('Retry Step 5/6', outputs[4])
        self.assertIn('Retry Step 6/6', outputs[5])
        self.assertIn('[OK] Recovery ladder exhausted', outputs[6])

    def test_fixture_still_fails_under_full_hinted_recovery_combination(self):
        with tempfile.TemporaryDirectory() as tmp:
            for args, extra_env in [
                ([], None),
                (['--verbose'], None),
                (['--verbose', '--force'], {'RESUME_TOKEN': '1'}),
                (['--verbose', '--force'], {'RESUME_TOKEN': '1'}),
                (['--verbose', '--force', '--retry'], {'RESUME_TOKEN': '1'}),
                (['--verbose', '--force', '--retry'], {'RESUME_TOKEN': '1'}),
            ]:
                completed = self.run_script(tmp, 'full-combo', args, extra_env)
                self.assertEqual(completed.returncode, 1)

            final_attempt = self.run_script(
                tmp,
                'full-combo',
                ['--verbose', '--force', '--retry'],
                {'RESUME_TOKEN': '1'},
            )

        self.assertEqual(final_attempt.returncode, 0)
        self.assertIn('[OK] Recovery ladder exhausted', final_attempt.stderr)


if __name__ == '__main__':
    unittest.main()
