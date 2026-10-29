"""Acceptance tests for the reviewed monetary helpers (R3, R4)."""

import unittest
from decimal import Decimal

from ratesvc.money import convert, format_amount, total

FLOAT_ERROR = "^float amounts are not exact; pass str, int or Decimal$"


class ConvertTests(unittest.TestCase):
    def test_rounds_half_up_on_the_exact_product(self):
        self.assertEqual(convert("1.005", "1"), Decimal("1.01"))

    def test_accepts_int_and_decimal_operands(self):
        self.assertEqual(convert(2, "1.5"), Decimal("3.00"))
        self.assertEqual(convert(Decimal("10"), Decimal("0.1")), Decimal("1.00"))

    def test_rejects_float_operands(self):
        with self.assertRaisesRegex(TypeError, FLOAT_ERROR):
            convert(1.5, "1")
        with self.assertRaisesRegex(TypeError, FLOAT_ERROR):
            convert("1", 1.5)

    def test_rejects_non_finite_and_invalid_values(self):
        with self.assertRaisesRegex(ValueError, "^amount must be finite$"):
            convert("nan", "1")
        with self.assertRaisesRegex(ValueError, "^rate must be finite$"):
            convert("1", "inf")
        with self.assertRaisesRegex(ValueError, "^amount must be a valid decimal$"):
            convert("abc", "1")
        with self.assertRaisesRegex(ValueError, "^rate must be a valid decimal$"):
            convert("1", "abc")


class TotalTests(unittest.TestCase):
    def test_sums_exact_decimals(self):
        self.assertEqual(total(["0.10", "0.20"]), Decimal("0.30"))
        self.assertEqual(total(["1.005", "0"]), Decimal("1.01"))

    def test_empty_iterable_is_zero(self):
        self.assertEqual(total([]), Decimal("0.00"))

    def test_rejects_float_entries(self):
        with self.assertRaisesRegex(TypeError, FLOAT_ERROR):
            total([0.1, "0.2"])


class FormatAmountTests(unittest.TestCase):
    def test_formats_with_two_decimals(self):
        self.assertEqual(format_amount(Decimal("-2.5")), "-2.50")
        self.assertEqual(format_amount(0), "0.00")
        self.assertEqual(format_amount("1.005"), "1.01")

    def test_rejects_float_and_non_finite(self):
        with self.assertRaisesRegex(TypeError, FLOAT_ERROR):
            format_amount(1.0)
        with self.assertRaisesRegex(ValueError, "^amount must be finite$"):
            format_amount("nan")

    def test_rejects_invalid_string(self):
        with self.assertRaisesRegex(ValueError, "^amount must be a valid decimal$"):
            format_amount("abc")


if __name__ == "__main__":
    unittest.main()
