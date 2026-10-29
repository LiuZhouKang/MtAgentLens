import unittest

from latencylab.window import clamp, percentile, window_bounds


class TestClamp(unittest.TestCase):
    def test_inside_range_is_unchanged(self):
        self.assertEqual(clamp(5, 0, 10), 5)

    def test_above_range_is_capped(self):
        self.assertEqual(clamp(15, 0, 10), 10)

    def test_below_range_is_floored(self):
        self.assertEqual(clamp(-3, 0, 10), 0)

    def test_inverted_range_is_rejected(self):
        with self.assertRaises(ValueError):
            clamp(1, 10, 0)


class TestPercentile(unittest.TestCase):
    def test_median_of_odd_sample(self):
        self.assertEqual(percentile([3, 1, 2], 0.5), 2)

    def test_maximum_fraction(self):
        self.assertEqual(percentile([4, 8, 15, 16], 1.0), 16)

    def test_empty_sample_is_rejected(self):
        with self.assertRaises(ValueError):
            percentile([], 0.5)

    def test_out_of_range_fraction_is_rejected(self):
        with self.assertRaises(ValueError):
            percentile([1], 1.5)


class TestWindowBounds(unittest.TestCase):
    def test_bounds_are_half_open(self):
        self.assertEqual(window_bounds(1234, 100), (1200, 1300))

    def test_zero_width_is_rejected(self):
        with self.assertRaises(ValueError):
            window_bounds(10, 0)


if __name__ == "__main__":
    unittest.main()
