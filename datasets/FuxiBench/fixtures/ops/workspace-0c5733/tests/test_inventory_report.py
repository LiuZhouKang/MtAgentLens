import unittest
from pathlib import Path

import inventory_report as report

DATA = Path(__file__).resolve().parents[1] / "data" / "sample_stock.csv"


class LoadRowsTests(unittest.TestCase):
    def test_loads_all_rows(self):
        rows = report.load_rows(DATA)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["sku"], "A-100")
        self.assertEqual(rows[0]["location"], "Bay-1")


class LowStockTests(unittest.TestCase):
    def test_default_threshold(self):
        rows = report.load_rows(DATA)
        low = report.low_stock(rows)
        self.assertEqual([row["sku"] for row in low], ["A-100", "B-200", "B-201"])

    def test_custom_threshold(self):
        rows = report.load_rows(DATA)
        low = report.low_stock(rows, threshold=1)
        self.assertEqual([row["sku"] for row in low], ["B-200"])


class SummarizeTests(unittest.TestCase):
    def test_summary_counts(self):
        summary = report.summarize(report.load_rows(DATA))
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["units"], 131)
        self.assertEqual(summary["low_stock"], 3)
        self.assertEqual(summary["unique_skus"], 4)


class GroupTests(unittest.TestCase):
    def test_group_by_location(self):
        groups = report.group_by_location(report.load_rows(DATA), {})
        self.assertEqual(sorted(groups), ["Bay-1", "Bay-2", "Bay-3"])
        self.assertEqual(len(groups["Bay-1"]), 2)


class FormatTests(unittest.TestCase):
    def test_report_mentions_totals(self):
        text = report.format_report(report.load_rows(DATA))
        self.assertIn("total lines: 4", text)
        self.assertIn("units on hand: 131", text)
        self.assertIn("low stock lines: 3", text)


if __name__ == "__main__":
    unittest.main()
