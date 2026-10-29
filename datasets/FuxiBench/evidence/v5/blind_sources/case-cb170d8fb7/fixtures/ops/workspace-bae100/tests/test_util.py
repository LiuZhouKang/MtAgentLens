"""Acceptance tests for the reviewed interval and rate helpers (R1, R2)."""

import unittest

from ratesvc.util import parse_rate, split_interval


class SplitIntervalTests(unittest.TestCase):
    def test_covers_the_whole_interval_with_a_short_tail(self):
        self.assertEqual(
            split_interval(0, 10, 3),
            [(0, 3), (3, 6), (6, 9), (9, 10)],
        )

    def test_exact_multiple_has_no_extra_chunk(self):
        self.assertEqual(split_interval(0, 9, 3), [(0, 3), (3, 6), (6, 9)])

    def test_empty_interval_returns_no_chunks(self):
        self.assertEqual(split_interval(5, 5, 2), [])

    def test_fractional_size_is_supported(self):
        self.assertEqual(
            split_interval(0, 1, 0.25),
            [(0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1)],
        )

    def test_non_positive_size_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "^size must be positive$"):
            split_interval(0, 10, 0)
        with self.assertRaisesRegex(ValueError, "^size must be positive$"):
            split_interval(0, 10, -1)

    def test_reversed_interval_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError, "^end must be greater than or equal to start$"
        ):
            split_interval(10, 0, 2)


class ParseRateTests(unittest.TestCase):
    def test_parses_and_strips(self):
        self.assertEqual(parse_rate(" 2.5 "), 2.5)

    def test_allows_zero_and_negative_zero(self):
        self.assertEqual(parse_rate("0"), 0.0)
        self.assertEqual(parse_rate("-0"), 0.0)

    def test_rejects_non_string(self):
        with self.assertRaisesRegex(TypeError, "^rate must be a string$"):
            parse_rate(1.5)

    def test_rejects_empty_text(self):
        for text in ("", "   "):
            with self.assertRaisesRegex(ValueError, "^rate is empty$"):
                parse_rate(text)

    def test_rejects_non_numeric_text(self):
        with self.assertRaisesRegex(ValueError, "^rate is not a number$"):
            parse_rate("abc")

    def test_rejects_non_finite_values_before_sign(self):
        for text in ("nan", "NaN", "inf", "-inf", "Infinity"):
            with self.assertRaisesRegex(ValueError, "^rate must be finite$"):
                parse_rate(text)

    def test_rejects_negative_values(self):
        with self.assertRaisesRegex(ValueError, "^rate must be non-negative$"):
            parse_rate("-0.5")


if __name__ == "__main__":
    unittest.main()
