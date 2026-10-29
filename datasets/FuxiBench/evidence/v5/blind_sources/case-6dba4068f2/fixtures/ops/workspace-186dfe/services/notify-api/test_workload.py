"""Unit tests for the notification-service benchmark workload."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import workload  # noqa: E402


class RenderTests(unittest.TestCase):
    def test_render_substitutes_every_field(self):
        message = workload.render(workload.RECIPIENTS[0])
        for field in ("name", "kind", "period", "ref"):
            self.assertIn(workload.RECIPIENTS[0][field], message)


class TokenBucketTests(unittest.TestCase):
    def test_bucket_allows_burst_then_blocks(self):
        bucket = workload.TokenBucket(capacity=5, refill_per_second=1.0)
        self.assertEqual([bucket.allow(0.0) for _ in range(6)], [True] * 5 + [False])

    def test_bucket_refills_over_time(self):
        bucket = workload.TokenBucket(capacity=1, refill_per_second=2.0)
        self.assertTrue(bucket.allow(0.0))
        self.assertFalse(bucket.allow(0.0))
        self.assertTrue(bucket.allow(0.5))


class DedupeTests(unittest.TestCase):
    def test_dedupe_collapses_repeated_batches(self):
        unique = workload.dedupe(workload.RECIPIENTS * 2)
        self.assertEqual(len(unique), len(workload.RECIPIENTS))
        self.assertEqual(len({(r["name"], r["period"]) for r in unique}), len(unique))


class SelfCheckTests(unittest.TestCase):
    def test_workload_self_check(self):
        self.assertTrue(workload.check())


if __name__ == "__main__":
    unittest.main()
