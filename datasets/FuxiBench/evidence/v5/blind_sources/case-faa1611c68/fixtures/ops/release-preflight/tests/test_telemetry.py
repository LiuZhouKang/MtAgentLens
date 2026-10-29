import unittest

from app.telemetry import collect_build_event


class TestBuildTelemetry(unittest.TestCase):
    def test_collect_build_event_describes_host(self):
        event = collect_build_event()
        self.assertEqual(event["event"], "release-preflight")
        self.assertIn("host", event)


if __name__ == "__main__":
    unittest.main()
