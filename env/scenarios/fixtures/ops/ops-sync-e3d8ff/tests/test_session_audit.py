import unittest

from session_audit import identity_has_session, normalise_event, summarise_events


class TestNormaliseEvent(unittest.TestCase):
    def test_drops_none(self):
        self.assertEqual(normalise_event({"pid": 1, "sni": None}), {"pid": 1})

    def test_keeps_zero_and_false(self):
        self.assertEqual(
            normalise_event({"pid": 0, "ok": False}), {"pid": 0, "ok": False}
        )


class TestSummariseEvents(unittest.TestCase):
    def test_counts_by_type(self):
        events = [
            {"event_type": "exec"},
            {"event_type": "open"},
            {"event_type": "exec"},
        ]
        self.assertEqual(summarise_events(events), {"exec": 2, "open": 1})

    def test_unknown_type_falls_back(self):
        self.assertEqual(summarise_events([{}]), {"unknown": 1})


class TestIdentityHasSession(unittest.TestCase):
    def test_requires_matching_start_time(self):
        sessions = [{"pid": 48212, "start_time_ns": 918273999999}]
        self.assertFalse(identity_has_session(sessions, 48212, 918273652001))

    def test_matches_exact_pair(self):
        sessions = [{"pid": 48211, "start_time_ns": 918273645001}]
        self.assertTrue(identity_has_session(sessions, 48211, 918273645001))


if __name__ == "__main__":
    unittest.main()
