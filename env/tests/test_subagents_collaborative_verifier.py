"""Tests for the subagents-collaborative chain verifier."""

import re
from pathlib import Path
import importlib.util
import sys
import tempfile
import types
import unittest


VERIFIER_PATH = Path('/home/lzk/MtAgentLens/env/scripts/verifiers/subagents-collaborative.py')


def _make_stub_read_all_task_logs(run_dir):
    """Return a read_all_task_logs function that reads from the given run_dir."""
    import os
    def read_all_task_logs(logs_dir):
        """Read task logs from logs_dir (or run_dir/logs)."""
        if isinstance(logs_dir, str):
            logs_dir = Path(logs_dir)
        # The verifier passes run_dir; task logs are in run_dir/logs/
        base_dir = logs_dir / "logs" if not str(logs_dir).endswith('/logs') else logs_dir
        if not base_dir.exists():
            return {}
        result = {}
        for log_file in sorted(base_dir.glob("*.log")):
            task_id = log_file.stem
            try:
                result[task_id] = log_file.read_text(encoding="utf-8")
            except Exception:
                result[task_id] = ""
        return result
    return read_all_task_logs


def load_verifier_module():
    spec = importlib.util.spec_from_file_location('subagents_collaborative_verifier', VERIFIER_PATH)
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

    def count_events(self, source=None, comm=None, pid=None):
        return 0

    def ssl_events(self, comm=None, pid=None):
        return []

    def all_events(self, comm=None, pid=None):
        return []


class SubagentsCollaborativeVerifierTests(unittest.TestCase):

    def setUp(self):
        self.module = load_verifier_module()
        self.scenario_dir = Path('/home/lzk/MtAgentLens/env/scenarios')

    def make_task_log(self, run_dir: Path, task_id: str, content: str) -> Path:
        """Create a simulated task log file.

        If task_id starts with 'NNN-', auto-resolves the 3-digit index to
        the canonical AGENT_ROLE_MAP name so the verifier's read_agent_outputs
        finds the file.
        """
        logs_dir = run_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        m = re.match(r'^(\d{3})-(.+)', task_id)
        if m:
            agent_idx = int(m.group(1))
            role = self.module.AGENT_ROLE_MAP.get(agent_idx, "unknown")
            prefix = f"{agent_idx:03d}-subagents-collaborative-{role}"
        else:
            prefix = task_id
        log_file = logs_dir / f"{prefix}.log.attempt-1"
        log_file.write_text(content, encoding='utf-8')
        return log_file

    def make_summary_json(self, run_dir: Path, content: dict) -> Path:
        """Create a simulated summary.json."""
        import json
        summary_file = run_dir / "summary.json"
        summary_file.write_text(json.dumps(content), encoding='utf-8')
        return summary_file

    def _patch_read_all_task_logs(self, run_dir: Path):
        """Replace module's read_all_task_logs with one that reads from run_dir."""
        self.module.read_all_task_logs = _make_stub_read_all_task_logs(run_dir)

    # --- C1: Contract coherence tests ---

    def test_c1_passes_with_complete_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.make_task_log(run_dir, '001-agent-a', (
                "## Handoff Contract\n\n"
                "feature_id: obs-pipeline-v2\n"
                "scope: components_affected: collector/, bpf/\n"
                "acceptance_criteria:\n"
                "  - Criterion one\n"
                "  - Criterion two\n"
                "quality_gates: pass criteria defined\n"
                "context_bridge: key decisions documented\n"
                "ownership: phase_A: first, phase_B: second\n"
            ))
            self._patch_read_all_task_logs(run_dir)
            result = self.module.verify_c1_contract_coherence(run_dir, self.scenario_dir)

        self.assertEqual(result['status'], 'pass')
        self.assertIn('Contract', str(result['evidence']))

    def test_c1_fails_with_empty_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            result = self.module.verify_c1_contract_coherence(run_dir, self.scenario_dir)

        self.assertEqual(result['status'], 'indeterminate')
        self.assertIn('Cannot verify', result['details'])

    def test_c1_degraded_with_few_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.make_task_log(run_dir, '001-agent-a', (
                "feature_id: test\n"
            ))
            self._patch_read_all_task_logs(run_dir)
            result = self.module.verify_c1_contract_coherence(run_dir, self.scenario_dir)

        self.assertEqual(result['status'], 'fail')

    # --- C2: Design consistency tests ---

    def test_c2_passes_with_analyzer_design(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.make_task_log(run_dir, '001-agent-a', 'handoff contract doc')
            self.make_task_log(run_dir, '002-agent-b', (
                "Analyzer Design\n\n"
                "Events: file_access type struct { path: string, pid: int }\n"
                "Pipeline: input → transform → output\n"
                "Schema field: target_path of type string\n"
            ))
            self._patch_read_all_task_logs(run_dir)
            result = self.module.verify_c2_design_consistency(run_dir)

        self.assertEqual(result['status'], 'pass')

    def test_c2_indeterminate_with_no_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            result = self.module.verify_c2_design_consistency(run_dir)

        self.assertEqual(result['status'], 'indeterminate')

    # --- C3: Frontend coverage tests ---

    def test_c3_passes_with_frontend_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.make_task_log(run_dir, '003-agent-c', (
                "Dashboard contract\n\n"
                "Interface FileAccessEvent { source: string }\n"
                "Component props: event, isHighlighted\n"
                "Timeline visualization with drill-down detail panel\n"
                "Toggle filter for cross-boundary events\n"
                "Risk: path disclosure mitigated by truncation\n"
                "Assumption: backend emits source discriminator\n"
            ))
            self._patch_read_all_task_logs(run_dir)
            result = self.module.verify_c3_frontend_coverage(run_dir)

        self.assertEqual(result['status'], 'pass')

    def test_c3_degraded_with_minimal_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.make_task_log(run_dir, '003-agent-c', (
                "Simple event type\n"
            ))
            self._patch_read_all_task_logs(run_dir)
            result = self.module.verify_c3_frontend_coverage(run_dir)

        self.assertIn(result['status'], ['degraded', 'fail'])

    # --- C4: Chain integrity tests ---

    def test_c4_passes_with_full_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.make_task_log(run_dir, '001-agent-a', 'handoff contract ownership boundaries')
            self.make_task_log(run_dir, '002-agent-b', 'analyzer schema event type backend contract')
            self.make_task_log(run_dir, '003-agent-c', 'interface props filter dashboard risk assumption')
            self._patch_read_all_task_logs(run_dir)
            result = self.module.verify_c4_chain_integrity(run_dir)

        self.assertEqual(result['status'], 'pass')

    def test_c4_indeterminate_with_single_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.make_task_log(run_dir, '001-agent-a', 'standalone output')
            self._patch_read_all_task_logs(run_dir)
            result = self.module.verify_c4_chain_integrity(run_dir)

        self.assertEqual(result['status'], 'indeterminate')

    def test_c4_fails_with_no_cross_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.make_task_log(run_dir, '001-agent-a', 'aaaa')
            self.make_task_log(run_dir, '002-agent-b', 'bbbb')
            self._patch_read_all_task_logs(run_dir)
            result = self.module.verify_c4_chain_integrity(run_dir)

        self.assertEqual(result['status'], 'fail')

    # --- C5: Artifact completeness tests ---

    def test_c5_passes_with_required_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / 'trace.log').write_text('event data', encoding='utf-8')
            self.make_summary_json(run_dir, {'scenario_ids': ['subagents-collaborative']})
            result = self.module.verify_c5_artifact_completeness(run_dir, FakeEvents())

        self.assertEqual(result['status'], 'pass')

    def test_c5_degraded_with_missing_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            result = self.module.verify_c5_artifact_completeness(run_dir, FakeEvents())

        self.assertEqual(result['status'], 'degraded')

    # --- C6: Handoff artifact format tests ---

    def test_c6_passes_when_no_handoff_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            result = self.module.verify_c6_handoff_artifacts(run_dir)

        self.assertEqual(result['status'], 'pass')
        self.assertIn('No structured handoff', str(result['evidence']))

    def test_c6_detects_missing_required_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            handoff_dir = run_dir / ".kiro" / "handoffs"
            handoff_dir.mkdir(parents=True)
            hf = handoff_dir / "test-handoff.yaml"
            hf.write_text(
                "handoff_id: test-001\n"
                "feature_name: Test Feature\n"
                "from_phase: design\n"
                # missing to_phase
            )
            result = self.module.verify_c6_handoff_artifacts(run_dir)

        self.assertIn(result['status'], ['degraded', 'fail'])
        self.assertTrue(any('to_phase' in e for e in result.get('evidence', [])))

    def test_c6_passes_with_valid_handoff_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            handoff_dir = run_dir / ".kiro" / "handoffs"
            handoff_dir.mkdir(parents=True)
            hf = handoff_dir / "test-design.yaml"
            hf.write_text(
                "handoff_id: test-design-001\n"
                "feature_name: Test Feature\n"
                "from_phase: design\n"
                "to_phase: implementation\n"
                "timestamp: 2026-05-14T12:00:00Z\n"
                "\n"
                "deliverables:\n"
                "  - path: some/doc.md\n"
                "    type: doc\n"
                "    status: complete\n"
                "\n"
                "quality_gates:\n"
                "  - gate_name: design-review\n"
                "    status: pass\n"
            )
            result = self.module.verify_c6_handoff_artifacts(run_dir)

        self.assertEqual(result['status'], 'pass')

    def test_c6_checks_phase_transition_validity(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            handoff_dir = run_dir / ".kiro" / "handoffs"
            handoff_dir.mkdir(parents=True)
            hf = handoff_dir / "bad-transition.yaml"
            hf.write_text(
                "handoff_id: bad-001\n"
                "feature_name: Bad Feature\n"
                "from_phase: testing\n"
                "to_phase: design  # invalid: can't go back\n"
                "timestamp: 2026-05-14T12:00:00Z\n"
                "\n"
                "deliverables:\n"
                "  - path: some/doc.md\n"
                "    type: doc\n"
                "    status: complete\n"
                "\n"
                "quality_gates:\n"
                "  - gate_name: test-gate\n"
                "    status: pass\n"
            )
            result = self.module.verify_c6_handoff_artifacts(run_dir)

        # Should detect invalid transition testing→design
        self.assertIn(result['status'], ['degraded', 'fail'])

    # --- Aggregate verify() tests ---

    def test_verify_aggregate_passes_with_complete_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            self.make_task_log(run_dir, '001-agent-a', (
                "feature_id: obs-v2\nscope: components_affected: collector/\n"
                "acceptance_criteria: c1\nacceptance_criteria: c2\n"
                "quality_gates: pass/fail defined\ncontext_bridge: decisions\n"
                "ownership boundaries: phase1, phase2, phase3\n"
            ))
            self.make_task_log(run_dir, '002-agent-b', (
                "Analyzer: file_access_analyzer\nSchema: { path: string, pid: int, flags: int }\n"
                "Pipeline: ebpf_event → parse → enrich → emit\n"
                "handoff contract requirements tracked\n"
            ))
            self.make_task_log(run_dir, '003-agent-c', (
                "Interface FileAccessEvent { source: file_access, path: string }\n"
                "Component: FileAccessRow with drill-down\n"
                "Filter toggle: showCrossBoundary\n"
                "Risk: volume, assumptions: backend provides root\n"
            ))
            (run_dir / 'trace.log').write_text('event line 1', encoding='utf-8')
            self.make_summary_json(run_dir, {'scenario_ids': ['subagents-collaborative']})
            self._patch_read_all_task_logs(run_dir)

            result = self.module.verify(run_dir, self.scenario_dir, FakeEvents())

        self.assertEqual(result['status'], 'pass')
        self.assertIn('C1', result['layers'])
        self.assertIn('C2', result['layers'])
        self.assertIn('C3', result['layers'])
        self.assertIn('C4', result['layers'])
        self.assertIn('C5', result['layers'])
        self.assertIn('C6', result['layers'])

    def test_verify_aggregate_fails_on_core_break(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            # Only agent C output available — A never produced contract, B never produced design
            self.make_task_log(run_dir, '003-agent-c', 'some random output')
            (run_dir / 'trace.log').write_text('event data', encoding='utf-8')
            self.make_summary_json(run_dir, {'scenario_ids': ['test']})
            self._patch_read_all_task_logs(run_dir)

            result = self.module.verify(run_dir, self.scenario_dir, FakeEvents())

        self.assertIn(result['status'], ['fail', 'degraded'])


if __name__ == '__main__':
    unittest.main()
