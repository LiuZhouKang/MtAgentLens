import unittest

from sync import reconcile


class ReconcileTest(unittest.TestCase):
    def test_missing_sku_is_treated_as_zero(self):
        self.assertEqual(reconcile({"a": 5}, {"b": 2}), {"a": 5, "b": -2})

    def test_matching_skus_cancel(self):
        self.assertEqual(reconcile({"a": 3}, {"a": 3}), {"a": 0})


if __name__ == "__main__":
    unittest.main()
