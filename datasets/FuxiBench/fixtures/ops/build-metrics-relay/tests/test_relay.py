import unittest

from relay import bucket_duration, normalise


class TestNormalise(unittest.TestCase):
    def test_drops_null_fields(self):
        self.assertEqual(normalise({"a": 1, "b": None}), {"a": 1})

    def test_keeps_zero_and_false(self):
        self.assertEqual(normalise({"a": 0, "b": False}), {"a": 0, "b": False})


class TestBucketDuration(unittest.TestCase):
    def test_rounds_down_to_bucket_start(self):
        self.assertEqual(bucket_duration(1234), 1000)

    def test_rejects_non_positive_width(self):
        with self.assertRaises(ValueError):
            bucket_duration(10, width=0)


if __name__ == "__main__":
    unittest.main()
