"""Unit tests for the search-service benchmark workload."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import workload  # noqa: E402


class TokenizeTests(unittest.TestCase):
    def test_lowercases_and_keeps_inner_hyphens(self):
        self.assertEqual(
            workload.tokenize("Alpha, BETA-2; gamma."),
            ["alpha", "beta-2", "gamma"],
        )


class IndexTests(unittest.TestCase):
    def test_index_lists_documents_for_each_term(self):
        index = workload.build_index(["one two", "two three"])
        self.assertEqual(index["two"], [0, 1])
        self.assertEqual(index["one"], [0])
        self.assertNotIn("four", index)

    def test_index_does_not_duplicate_document_ids(self):
        index = workload.build_index(["same same same"])
        self.assertEqual(index["same"], [0])


class RankingTests(unittest.TestCase):
    def test_ranking_orders_by_hit_count_then_document_id(self):
        index = workload.build_index(["alpha", "alpha beta", "beta"])
        self.assertEqual(
            workload.rank("alpha beta", index),
            [(1, 2), (0, 1), (2, 1)],
        )

    def test_workload_self_check(self):
        self.assertTrue(workload.check())


if __name__ == "__main__":
    unittest.main()
