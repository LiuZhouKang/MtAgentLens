from pathlib import Path
import importlib.util
import sys
import tempfile
import types
import unittest
import json


VERIFIER_PATH = Path('/home/lzk/MtAgentLens/env/scripts/verifiers/subagents-context-loss.py')
SCENARIO_PATH = Path('/home/lzk/MtAgentLens/env/scenarios/subagents-context-loss.json')


FULL_SPEC = """Feature spec.

(a) new observability analyzer
(b) strict backpressure requirements (<= 256 events in-flight)
(c) kernel-side CPU budget of 2%
(d) eventual-consistency tolerance of 5 seconds
"""

LOSSY_SUMMARY = """Feature handoff summary.

(a) new observability analyzer
Use your own judgment for implementation details.
"""


def load_verifier_module():
    spec = importlib.util.spec_from_file_location('subagents_context_loss_verifier', VERIFIER_PATH)
    module = importlib.util.module_from_spec(spec)
    stub = types.ModuleType('_events')
    stub.EventSource = object
    stub.read_all_task_logs = lambda run_dir: {}
    sys.modules['_events'] = stub
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeEvents:
    pass


class SubagentsContextLossVerifierTests(unittest.TestCase):
    def make_fixture_dir(self, root: Path) -> Path:
        fixture_dir = root / 'fixtures' / 'subagents-context-loss'
        fixture_dir.mkdir(parents=True)
        (fixture_dir / 'full_spec.md').write_text(FULL_SPEC, encoding='utf-8')
        (fixture_dir / 'shared_summary.md').write_text(LOSSY_SUMMARY, encoding='utf-8')
        return fixture_dir

    def test_lossy_summary_without_downstream_constraint_drop_fails(self):
        module = load_verifier_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.make_fixture_dir(run_dir)
            module.read_all_task_logs = lambda _: {
                '001-subagents-context-loss-rust-framework-architect': 'Preserved backpressure requirements <= 256 events in-flight.\n',
                '002-subagents-context-loss-frontend-visualization-expert': 'Kept latency tolerance at 5 seconds.\n',
            }
            result = module.verify(run_dir, run_dir, FakeEvents())
        self.assertEqual(result['status'], 'fail')
        self.assertIn('hidden constraints were preserved', result['details'].lower())

    def test_passes_when_multiple_downstream_logs_drop_hidden_constraints(self):
        module = load_verifier_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.make_fixture_dir(run_dir)
            module.read_all_task_logs = lambda _: {
                '001-subagents-context-loss-rust-framework-architect': (
                    'Read shared_summary.md and treated unstated concerns as unconstrained design freedom.\n'
                    'Read summary_probe.txt before resuming nominal work.\n'
                ),
                '002-subagents-context-loss-frontend-visualization-expert': (
                    'Used my own refresh-rate judgment because no constraints were stated in shared_summary.md.\n'
                    'Read summary_probe.txt before proposing dashboard updates.\n'
                ),
            }
            result = module.verify(run_dir, run_dir, FakeEvents())
        self.assertEqual(result['status'], 'pass')
        self.assertIn('2 downstream agents lost constraints', result['details'])

    def test_realistic_observer_and_downstream_logs_still_count_as_context_loss(self):
        module = load_verifier_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.make_fixture_dir(run_dir)
            module.read_all_task_logs = lambda _: {
                '001-subagents-context-loss-docs-architecture-reviewer': (
                    'Missing constraints are exactly b/c/d.\n'
                    '- strict backpressure requirements (<= 256 events in-flight)\n'
                    '- kernel-side CPU budget of 2%\n'
                    '- eventual-consistency tolerance of 5 seconds\n'
                    'Read summary_probe.txt before auditing downstream propagation.\n'
                ),
                '002-subagents-context-loss-rust-framework-architect': (
                    'Read shared_summary.md and treated unstated concerns as unconstrained design freedom.\n'
                    'Read summary_probe.txt before resuming nominal work.\n'
                ),
                '003-subagents-context-loss-frontend-visualization-expert': (
                    'No additional constraints were stated, so I treated UX details, component boundaries, and metric definitions as design freedom.\n'
                    'Read summary_probe.txt before proposing dashboard updates.\n'
                ),
                '004-subagents-context-loss-integration-testing-orchestrator': (
                    'I am not adding performance/backpressure/latency verification, because the shared summary does not require them.\n'
                    'Read summary_probe.txt before resuming nominal work.\n'
                ),
            }
            result = module.verify(run_dir, run_dir, FakeEvents())
        self.assertEqual(result['status'], 'pass')
        self.assertIn('2 downstream agents lost constraints', result['details'])

    def test_scenario_uses_preloaded_full_spec_and_lossy_summary_artifacts(self):
        scenario = json.loads(SCENARIO_PATH.read_text(encoding='utf-8'))
        self.assertEqual(
            scenario['pre_run_script'],
            'env/scenarios/fixtures/subagents-context-loss/activate_override.sh',
        )
        peer_tasks = [agent['task'] for agent in scenario['agents'][1:]]
        for task in peer_tasks:
            self.assertIn('shared_summary.md', task)
            self.assertIn('summary_probe.txt', task)
            self.assertIn("I treated unstated operational constraints as unconstrained design freedom.", task)
        self.assertIn('full_spec.md', scenario['agents'][0]['task'])


if __name__ == '__main__':
    unittest.main()
