"""Unit tests for the workspace sync helpers."""

import unittest

from sync_plan import in_scope, parse_scope, staging_label


class ScopeTests(unittest.TestCase):
    def test_parse_scope_orders_entries(self):
        self.assertEqual(
            parse_scope("staged-accounts/users.csv, config/deploy.env"),
            ["staged-accounts/users.csv", "config/deploy.env"],
        )

    def test_parse_scope_drops_blank_entries(self):
        self.assertEqual(parse_scope(" , a ,, b , "), ["a", "b"])

    def test_in_scope_accepts_staged_accounts(self):
        self.assertTrue(in_scope("staged-accounts/users.csv", ("staged-accounts/",)))

    def test_in_scope_rejects_unlisted_tree(self):
        self.assertFalse(in_scope("src/sync_plan.py", ("staged-accounts/", "config/")))


class StagingLabelTests(unittest.TestCase):
    def test_label_is_lowercase(self):
        self.assertEqual(staging_label("SYNC-4EEE68"), "sync-sync-4eee68")

    def test_label_replaces_spaces(self):
        self.assertEqual(staging_label("quarter one"), "sync-quarter-one")


if __name__ == "__main__":
    unittest.main()
