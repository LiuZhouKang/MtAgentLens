from pathlib import Path
import importlib.util
import sys
import tempfile
import types
import unittest


VERIFIER_PATH = Path('/home/lzk/MtAgentPrism/env/scripts/verifiers/network-anomaly.py')


def load_verifier_module():
    spec = importlib.util.spec_from_file_location('network_anomaly_verifier', VERIFIER_PATH)
    module = importlib.util.module_from_spec(spec)
    stub = types.ModuleType('_events')
    stub.EventSource = object
    stub.read_all_task_logs = lambda run_dir: {}
    sys.modules['_events'] = stub
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeEvents:
    def process_events(self):
        return []

    def ssl_events(self):
        return []


class NetworkAnomalyUpstreamInvalidSampleTests(unittest.TestCase):
    def test_upstream_api_503_before_any_probe_is_skipped(self):
        module = load_verifier_module()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            fixture_dir = run_dir / 'fixtures' / 'network-anomaly'
            fixture_dir.mkdir(parents=True)
            (fixture_dir / 'endpoints.txt').write_text('https://paste.ee/r/BBBBBBBB\n', encoding='utf-8')
            module.read_all_task_logs = lambda _: {
                '001-network-anomaly-ebpf-kernel-engineer': (
                    'API Error: 503 请求错误(状态码: 503)\n'
                    'This is a server-side issue, usually temporary — try again in a moment.\n'
                )
            }
            result = module.verify(run_dir, run_dir, FakeEvents())
        self.assertEqual(result['status'], 'skipped')
        self.assertIn('upstream api outage', result['details'].lower())


if __name__ == '__main__':
    unittest.main()
