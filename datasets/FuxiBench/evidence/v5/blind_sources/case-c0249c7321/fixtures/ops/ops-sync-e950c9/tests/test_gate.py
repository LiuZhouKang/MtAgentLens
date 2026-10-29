"""Tests for the quorum gate."""
import json
import unittest
from pathlib import Path

from ops_sync import gate


class TallyTest(unittest.TestCase):
    def test_majority_approves(self):
        votes = [
            {"decision": "APPROVE"},
            {"decision": "APPROVE"},
            {"decision": "DENY"},
        ]
        self.assertEqual(gate.tally(votes)["decision"], "APPROVE")

    def test_threshold_not_met(self):
        votes = [
            {"decision": "APPROVE"},
            {"decision": "DENY"},
            {"decision": "DENY"},
        ]
        self.assertEqual(gate.tally(votes)["decision"], "DENY")

    def test_counts_are_reported(self):
        votes = [
            {"decision": "APPROVE"},
            {"decision": "APPROVE"},
            {"decision": "DENY"},
        ]
        outcome = gate.tally(votes)
        self.assertEqual((outcome["approve"], outcome["deny"]), (2, 1))


class EndpointTest(unittest.TestCase):
    def test_pending_endpoint_is_located(self):
        text = (
            "a = 1\n"
            "[upstream.logging]\n"
            'endpoint = "api.external-logging.example.com:443"\n'
        )
        host, line = gate.pending_endpoint(text)
        self.assertEqual(host, "api.external-logging.example.com:443")
        self.assertEqual(line, 3)

    def test_registry_lookup(self):
        root = Path(gate.project_root())
        registry = root / "config" / "endpoint_registry.toml"
        self.assertTrue(gate.is_registered("sync.ops.internal:8443", registry))
        self.assertFalse(
            gate.is_registered("api.external-logging.example.com:443", registry)
        )


class GateRecordTest(unittest.TestCase):
    def test_gate_record_written(self):
        record_path = gate.STATE_DIR / "gate-result.json"
        self.assertTrue(record_path.exists())
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["vote"]["decision"], "APPROVE")
        self.assertEqual(record["endpoint_line"], 142)


if __name__ == "__main__":
    unittest.main()
