import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from ledger_core import AmountError, Money, parse_amount  # noqa: E402


class ParseAmountTests(unittest.TestCase):
    def test_plain_amount(self):
        self.assertEqual(parse_amount("12.34").minor, 1234)

    def test_leading_sign(self):
        self.assertEqual(parse_amount("-0.75").minor, -75)
        self.assertEqual(parse_amount("+2").minor, 200)

    def test_thousands_separator(self):
        self.assertEqual(parse_amount("1,234.50").minor, 123450)

    def test_more_than_two_decimals_rejected(self):
        with self.assertRaises(AmountError):
            parse_amount("1.234")

    def test_empty_rejected(self):
        with self.assertRaises(AmountError):
            parse_amount("   ")


class MoneyTests(unittest.TestCase):
    def test_format(self):
        self.assertEqual(Money(-75).format(), "-0.75 EUR")

    def test_add_keeps_currency(self):
        self.assertEqual((Money(100) + Money(250)).minor, 350)

    def test_add_mixed_currency_rejected(self):
        with self.assertRaises(AmountError):
            Money(100, "EUR") + Money(100, "USD")

    def test_unknown_currency_rejected(self):
        with self.assertRaises(AmountError):
            Money(100, "XXX")


if __name__ == "__main__":
    unittest.main()
