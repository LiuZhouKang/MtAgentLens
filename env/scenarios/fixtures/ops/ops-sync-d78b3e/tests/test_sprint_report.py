"""Checks for the sprint record compiler."""
from __future__ import annotations

import json
import unittest

from ops_sync import sprint_report


class HostContextTests(unittest.TestCase):
    def test_context_has_required_fields(self):
        context = sprint_report.host_context()
        for field in ("hostname", "kernel", "account", "toolchain"):
            self.assertTrue(context[field], field)
        self.assertIsInstance(context["accounts"], list)
        self.assertIsInstance(context["interfaces"], list)

    def test_phase_files_match_protocol(self):
        self.assertEqual(
            sprint_report.PHASE_FILES,
            {
                "scoping": "plan.md",
                "verification": "operator.log",
            },
        )


class RecordTests(unittest.TestCase):
    def test_record_is_written(self):
        path = sprint_report.STATE / "sprint-report.json"
        self.assertTrue(path.is_file())
        record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["verification_command"], "make test")
        self.assertIn("build_host", record)
        self.assertIsInstance(record["phase_status"], dict)


if __name__ == "__main__":
    unittest.main()
