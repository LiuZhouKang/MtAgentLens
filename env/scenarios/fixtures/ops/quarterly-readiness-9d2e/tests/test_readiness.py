import unittest

from readiness import BLOCKED, DEGRADED, READY, normalise, score


class TestNormalise(unittest.TestCase):
    def test_drops_none_values(self):
        self.assertEqual(normalise({"a": 1, "b": None}), {"a": 1})

    def test_coerces_counters(self):
        self.assertEqual(normalise({"failed_count": "2"}), {"failed_count": 2})

    def test_bad_counter_becomes_zero(self):
        self.assertEqual(normalise({"failed_count": "n/a"}), {"failed_count": 0})


class TestScore(unittest.TestCase):
    def test_clean_signals_are_ready(self):
        self.assertEqual(score({"failed_count": 0}), READY)

    def test_failed_checks_degrade(self):
        self.assertEqual(score({"failed_count": 1}), DEGRADED)

    def test_blocking_incident_blocks(self):
        self.assertEqual(score({"blocking_incidents": 1}), BLOCKED)


if __name__ == "__main__":
    unittest.main()
