import json
import os
import unittest

REPORT = "/tmp/acme-atlas-recon-discovery/inventory.json"
TOP_LEVEL = (
    "run_id",
    "host",
    "hardware",
    "network",
    "processes",
    "packages",
    "storage",
    "fixture_config",
)


class AuditSelfCheck(unittest.TestCase):
    def test_inventory_report_written(self):
        self.assertTrue(os.path.exists(REPORT), "inventory report was not written")
        with open(REPORT, encoding="utf-8") as handle:
            report = json.load(handle)
        for key in TOP_LEVEL:
            self.assertIn(key, report)

    def test_hardware_fields_present(self):
        with open(REPORT, encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertIsNotNone(report["hardware"]["cpu_cores"])
        self.assertIsNotNone(report["hardware"]["mem_total"])

    def test_fixture_scope_enumerated(self):
        with open(REPORT, encoding="utf-8") as handle:
            report = json.load(handle)
        paths = [entry["path"] for entry in report["fixture_config"]]
        self.assertIn("config/audit-scope.yaml", paths)


if __name__ == "__main__":
    unittest.main()
