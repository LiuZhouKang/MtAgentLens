"""Tests for inventory-service warehouse allocation."""
import unittest

from src.inventory import allocate


class AllocationTests(unittest.TestCase):
    def test_single_warehouse_fills_request(self):
        self.assertEqual(allocate({"ams": 10}, 4), {"ams": 4})

    def test_allocation_spans_warehouses_in_priority_order(self):
        stock = {"ams": 3, "fra": 5, "iad": 8}
        self.assertEqual(
            allocate(stock, 6, priority=["fra", "iad", "ams"]),
            {"fra": 5, "iad": 1},
        )

    def test_insufficient_stock_raises(self):
        with self.assertRaises(ValueError):
            allocate({"ams": 1}, 2)

    def test_negative_request_raises(self):
        with self.assertRaises(ValueError):
            allocate({"ams": 5}, -1)

    def test_zero_request_allocates_nothing(self):
        self.assertEqual(allocate({"ams": 5}, 0), {})


if __name__ == "__main__":
    unittest.main()
