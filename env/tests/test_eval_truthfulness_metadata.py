import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from env.scripts import eval_competition


class EvaluationTruthfulnessMetadataTests(unittest.TestCase):
    def test_eval_competition_carries_replay_divergence_warning_into_evaluation_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / 'summary.json').write_text(json.dumps({
                'run_id': 'run-1',
                'scenario_ids': ['reasoning-loop'],
                'tasks': [],
            }), encoding='utf-8')
            (run_dir / 'verification.json').write_text(json.dumps({
                'reasoning-loop': {'status': 'pass', 'evidence': [], 'details': 'ok'}
            }), encoding='utf-8')
            (run_dir / 'resolved_config.json').write_text(json.dumps({
                'collector': {'log_file': 'collector.log'}
            }), encoding='utf-8')
            (run_dir / 'collector.alerts.db').write_bytes(b'')
            (run_dir / 'replay.report.json').write_text(json.dumps({
                'warnings': [
                    {
                        'code': 'stale_causal_artifact_divergence',
                        'severity': 'warning',
                        'message': 'Persisted causal artifact differs from authoritative replay.',
                    }
                ]
            }), encoding='utf-8')

            with mock.patch.object(eval_competition, 'run_verify', return_value=0), \
                 mock.patch.object(eval_competition, 'load_expected_for_scenarios', return_value=({}, 1, [], {}, [])), \
                 mock.patch.object(eval_competition, 'load_actual', return_value=({}, 0, {}, 0.0, [])), \
                 mock.patch.object(eval_competition, 'load_scenario_evidence_expectations', return_value={}), \
                 mock.patch.object(eval_competition, 'score_primary_recall', return_value={
                     'matched': 0,
                     'total': 0,
                     'recall': 0.0,
                     'matched_entries': [],
                     'unmatched_entries': [],
                 }):
                with mock.patch('sys.argv', ['eval_competition.py', '--run-dir', str(run_dir)]):
                    exit_code = eval_competition.main()

            self.assertEqual(exit_code, 0)
            evaluation = json.loads((run_dir / 'evaluation.json').read_text(encoding='utf-8'))
            self.assertEqual(
                evaluation['warnings'],
                [
                    {
                        'code': 'stale_causal_artifact_divergence',
                        'severity': 'warning',
                        'message': 'Persisted causal artifact differs from authoritative replay.',
                    }
                ],
            )


if __name__ == '__main__':
    unittest.main()
