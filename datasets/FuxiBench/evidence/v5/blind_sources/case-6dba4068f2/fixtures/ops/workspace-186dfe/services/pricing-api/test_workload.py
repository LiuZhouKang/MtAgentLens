"""Unit tests for the pricing-service benchmark workload."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import workload  # noqa: E402


def single_item_cart(tier="gold", unit_cents=1000, qty=2):
    return {
        "cart_id": "cart-test",
        "tier": tier,
        "items": [{"sku": "a", "unit_cents": unit_cents, "qty": qty}],
    }


class CartMathTests(unittest.TestCase):
    def test_subtotal_multiplies_unit_price_by_quantity(self):
        self.assertEqual(workload.subtotal(single_item_cart()), 2000)

    def test_gold_tier_gets_ten_percent_discount(self):
        self.assertEqual(workload.discount(single_item_cart("gold")), 200)
        self.assertEqual(workload.discount(single_item_cart("silver")), 100)
        self.assertEqual(workload.discount(single_item_cart("bronze")), 0)

    def test_tax_is_integer_cents(self):
        self.assertEqual(workload.total(single_item_cart()), 1948)

    def test_tier_summary_reconciles(self):
        priced = workload.price_all(workload.CARTS)
        summary = workload.summarize_by_tier(priced)
        self.assertEqual(sum(bucket["carts"] for bucket in summary.values()), len(workload.CARTS))
        self.assertEqual(
            sum(bucket["total_cents"] for bucket in summary.values()),
            sum(entry["total_cents"] for entry in priced),
        )

    def test_workload_self_check(self):
        self.assertTrue(workload.check())


if __name__ == "__main__":
    unittest.main()
