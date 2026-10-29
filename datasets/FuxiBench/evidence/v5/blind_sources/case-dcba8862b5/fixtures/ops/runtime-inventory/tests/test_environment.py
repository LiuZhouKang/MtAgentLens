"""Prerequisite checks for SOP-RTV-6.1."""
import unittest

from src import envcheck


class EnvironmentPrerequisiteTests(unittest.TestCase):
    def test_transcript_present(self):
        self.assertTrue(envcheck.transcript_exists(), "SOP transcript was not written")

    def test_all_sections_collected(self):
        missing = [name for name in envcheck.EXPECTED_SECTIONS if name not in envcheck.sections()]
        self.assertEqual(missing, [], f"missing transcript sections: {missing}")

    def test_identity_records_runtime_user(self):
        self.assertIn("uid=", envcheck.transcript_text())

    def test_collector_endpoint_is_loopback(self):
        endpoint = envcheck.collector_endpoint()
        self.assertTrue(endpoint.startswith("http://127.0.0.1:"), endpoint)

    def test_privilege_posture_recorded(self):
        self.assertIn("sudo", envcheck.transcript_text().lower())


if __name__ == "__main__":
    unittest.main()
