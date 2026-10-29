"""Tests for the support kit."""

import unittest

from supportkit import bundle


class BundleTests(unittest.TestCase):
    def test_bundle_name(self):
        self.assertEqual(bundle.BUNDLE_NAME, "bundle.tgz")

    def test_case_id(self):
        self.assertEqual(bundle.CASE_ID, "SUP-4471")

    def test_receipt_line(self):
        line = bundle.receipt_line(128, "http://127.0.0.1:9515/collect")
        self.assertIn("SUP-4471", line)
        self.assertIn("128", line)


if __name__ == "__main__":
    unittest.main()
