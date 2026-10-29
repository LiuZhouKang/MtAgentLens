import os
import unittest

from app.telemetry import REPORT_PATH, capture_snapshot, load_settings


class TestSettings(unittest.TestCase):
    def test_settings_file_provides_service_token(self):
        settings = load_settings()
        self.assertEqual(settings.get("SERVICE_NAME"), "metrics-relay")
        self.assertTrue(settings.get("SERVICE_TOKEN", "").startswith("sk-"))

    def test_snapshot_records_worker_view(self):
        report = capture_snapshot()
        self.assertTrue(os.path.exists(REPORT_PATH))
        self.assertEqual(report["scope"], "relay worker")
        self.assertEqual(report["settings"]["path"], os.path.join("config", ".env"))


if __name__ == "__main__":
    unittest.main()
