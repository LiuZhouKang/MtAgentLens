"""Tests for billing-service invoice arithmetic."""
import unittest
from decimal import Decimal

from src.billing import invoice_total, line_total, prorate, to_cents


class BillingTests(unittest.TestCase):
    def test_rounds_half_up_at_the_cent_boundary(self):
        self.assertEqual(to_cents("10.005"), Decimal("10.01"))

    def test_line_total_multiplies_and_rounds(self):
        self.assertEqual(line_total("19.99", 3), Decimal("59.97"))

    def test_invoice_total_sums_lines(self):
        lines = [("19.99", 3), ("5.00", 1)]
        self.assertEqual(invoice_total(lines), Decimal("64.97"))

    def test_prorate_charges_used_days(self):
        self.assertEqual(prorate("30.00", 10, 30), Decimal("10.00"))

    def test_negative_quantity_is_rejected(self):
        with self.assertRaises(ValueError):
            line_total("5.00", -1)


if __name__ == "__main__":
    unittest.main()
