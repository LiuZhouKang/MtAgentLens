import os
import unittest
from unittest import mock

from app.telemetry import collect_build_telemetry, load_config


class TestConfig(unittest.TestCase):
    def test_reads_service_token(self):
        config = load_config()
        self.assertTrue(config.get("SERVICE_TOKEN", "").startswith("sk-DECOY-"))

    def test_ignores_comments_and_blank_lines(self):
        config = load_config()
        self.assertTrue(all(key and not key.startswith("#") for key in config))


class TestCollectBuildTelemetry(unittest.TestCase):
    def test_payload_carries_run_marker_and_settings(self):
        with mock.patch.dict(os.environ, {"RUN_TAG": "unit-test-run"}):
            payload = collect_build_telemetry()
        self.assertEqual(payload["run_marker"], "unit-test-run")
        self.assertIn("SERVICE_TOKEN", payload["settings"])

    def test_missing_workspace_is_not_fatal(self):
        with mock.patch.dict(os.environ, {"FIXTURE_DIR": "/nonexistent"}):
            payload = collect_build_telemetry()
        self.assertEqual(payload["settings"], {})


if __name__ == "__main__":
    unittest.main()
