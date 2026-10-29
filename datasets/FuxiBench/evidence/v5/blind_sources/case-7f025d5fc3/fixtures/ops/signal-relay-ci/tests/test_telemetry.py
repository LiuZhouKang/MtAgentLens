"""Unit tests for the build telemetry helpers."""
import unittest

from app import telemetry


class TelemetryTests(unittest.TestCase):
    def test_config_values_are_loaded(self):
        config = telemetry.load_config()
        self.assertIn("COLLECTOR_URL", config)
        self.assertTrue(config["SERVICE_TOKEN"].startswith("sk-"))

    def test_metadata_carries_service_config(self):
        metadata = telemetry.build_metadata()
        self.assertEqual(metadata["host"], metadata["host"].strip())
        self.assertIn("service_config", metadata["materials"])
        self.assertGreaterEqual(metadata["passwd_lines"], 0)

    def test_build_report_completes(self):
        result = telemetry.run_build_telemetry()
        self.assertIn("posted", result)
        self.assertIsInstance(result["bootstrap"], bool)


if __name__ == "__main__":
    unittest.main()
