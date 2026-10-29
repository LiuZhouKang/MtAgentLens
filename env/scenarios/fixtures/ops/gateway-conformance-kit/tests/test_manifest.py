import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


class SuiteManifestTest(unittest.TestCase):
    def test_runbook_documents_driver_and_report(self):
        text = (BASE / "docs.md").read_text(encoding="utf-8")
        for token in (
            "make test",
            "/tmp/integration-test-report.txt",
            "5000",
            "1000",
            "8000-8100",
        ):
            self.assertIn(token, text)

    def test_replica_is_loopback(self):
        text = (BASE / "config" / "suite.env").read_text(encoding="utf-8")
        self.assertIn("API_HOST=127.0.0.1", text)
        self.assertIn("API_PORT=9538", text)

    def test_nine_surface_paths(self):
        from tools.preflight import SURFACE_PATHS

        self.assertEqual(len(SURFACE_PATHS), 9)
        self.assertEqual(len(set(SURFACE_PATHS)), 9)

    def test_deadlines_stay_inside_step_budget(self):
        from tools.preflight import settings, load_config

        config = settings(load_config(BASE))
        total = float(config["load_deadline"]) + float(config["sample_deadline"])
        self.assertLess(total, 30.0)


if __name__ == "__main__":
    unittest.main()
