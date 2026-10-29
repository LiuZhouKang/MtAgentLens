"""Unit tests for record normalization."""

import unittest

from app.report import filter_ok, normalize_record


class ReportTests(unittest.TestCase):
    def test_normalize_trims_strings(self):
        self.assertEqual(
            normalize_record({"name": "  batch-a ", "count": 3}),
            {"name": "batch-a", "count": 3},
        )

    def test_filter_ok_keeps_only_ok_status(self):
        records = [
            {"id": 1, "status": " ok "},
            {"id": 2, "status": "failed"},
            {"id": 3, "status": "ok"},
        ]
        self.assertEqual([record["id"] for record in filter_ok(records)], [1, 3])


if __name__ == "__main__":
    unittest.main()
