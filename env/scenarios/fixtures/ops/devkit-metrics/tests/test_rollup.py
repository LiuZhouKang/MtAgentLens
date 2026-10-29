import unittest

from devkit_metrics import format_report, rollup


class TestRollup(unittest.TestCase):
    def test_sums_counts_by_kind(self):
        events = [{"kind": "test", "count": 2}, {"kind": "test"}, {"kind": "build"}]
        self.assertEqual(rollup(events), {"test": 3, "build": 1})

    def test_ignores_events_without_kind(self):
        self.assertEqual(rollup([{"count": 5}, {"kind": ""}]), {})

    def test_report_is_sorted_by_kind(self):
        self.assertEqual(format_report({"b": 2, "a": 1}), "a: 1\nb: 2")


if __name__ == "__main__":
    unittest.main()
