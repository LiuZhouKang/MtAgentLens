import unittest

from cache_state import cache_usage


class TestCacheUsage(unittest.TestCase):
    def test_empty_cache(self):
        self.assertEqual(cache_usage([]), {"entries": 0, "bytes": 0})

    def test_sums_entry_sizes(self):
        self.assertEqual(cache_usage([10, 32, 8]), {"entries": 3, "bytes": 50})


if __name__ == "__main__":
    unittest.main()
