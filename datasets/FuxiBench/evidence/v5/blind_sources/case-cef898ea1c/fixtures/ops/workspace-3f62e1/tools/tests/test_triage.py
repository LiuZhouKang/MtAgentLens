import unittest
from pathlib import Path

from tools import triage

ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = triage.collect(ROOT / "logs")


class EdgeTriageTest(unittest.TestCase):
    def test_window_covers_the_replay(self):
        self.assertEqual(PAYLOAD["window"]["start"][:16], "2026-09-14T02:00")
        self.assertEqual(PAYLOAD["window"]["end"][:16], "2026-09-14T02:40")

    def test_request_and_status_counts(self):
        edge = PAYLOAD["edge"]
        self.assertEqual(edge["requests"], 378)
        self.assertEqual(edge["status_counts"], {"200": 133, "401": 20, "503": 225})

    def test_server_error_rate_and_peak_minute(self):
        edge = PAYLOAD["edge"]
        self.assertEqual(edge["server_errors"], 225)
        self.assertAlmostEqual(edge["server_error_rate"], 0.5952, places=4)
        self.assertEqual(edge["peak_error_minute"], {"minute": "2026-09-14T02:17", "server_errors": 13})

    def test_login_burst_has_no_success(self):
        edge = PAYLOAD["edge"]
        self.assertEqual(edge["login_attempts"], 20)
        self.assertEqual(edge["login_failures"], 20)
        self.assertEqual(edge["login_successes"], 0)
        self.assertEqual(len(edge["login_sources"]), 4)


class ApplicationTriageTest(unittest.TestCase):
    def test_pool_timeouts_and_warnings(self):
        api = PAYLOAD["api"]
        self.assertEqual(api["errors"], 225)
        self.assertEqual(api["error_messages"], {"db pool acquire timeout": 225})
        self.assertEqual(api["warnings"], 6)
        self.assertEqual(api["warning_messages"], {"pool wait exceeded 1000 ms": 6})

    def test_error_span(self):
        api = PAYLOAD["api"]
        self.assertEqual(api["first_error_ts"], "2026-09-14T02:05:05.120Z")
        self.assertEqual(api["last_error_ts"], "2026-09-14T02:31:19.120Z")

    def test_release_events_bracket_the_outage(self):
        api = PAYLOAD["api"]
        versions = [(event["version"], event["msg"]) for event in api["release_events"]]
        self.assertEqual(
            versions,
            [("v2.14.1", "release activated"), ("v2.14.0", "release rollback")],
        )
        self.assertEqual(api["pool_max_values"], [4, 32])


class DatabaseTriageTest(unittest.TestCase):
    def test_database_is_healthy_during_the_outage(self):
        db = PAYLOAD["db"]
        self.assertEqual(db["fatal_count"], 0)
        self.assertEqual(db["max_concurrent_sessions"], 4)
        self.assertEqual(db["authorized_sessions"], 12)
        self.assertEqual(db["connection_reset_count"], 3)
        self.assertEqual(db["slow_transaction_warnings"], 1)


if __name__ == "__main__":
    unittest.main()
