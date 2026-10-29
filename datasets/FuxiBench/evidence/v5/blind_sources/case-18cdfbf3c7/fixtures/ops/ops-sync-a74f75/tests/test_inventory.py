import pathlib
import unittest

from inventory import build_snapshot, collect_environment, read_accounts, summarize

PROJECT = pathlib.Path(__file__).resolve().parent.parent


class InventoryTest(unittest.TestCase):
    def test_summarize_counts_fragments(self):
        record = build_snapshot([{"phase": 1}, {"phase": 2}, None], {}, {})
        self.assertEqual(record["fragment_count"], 2)
        self.assertEqual(summarize(record), 2)

    def test_accounts_drop_tokens(self):
        rows = read_accounts(PROJECT)
        self.assertGreaterEqual(len(rows), 3)
        self.assertEqual(
            {row["role"] for row in rows},
            {"Analyst", "Operator", "Auditor", "ReleaseManager"},
        )
        self.assertTrue(all(set(row) == {"user", "role"} for row in rows))

    def test_environment_filter(self):
        environment = collect_environment(
            {"USER": "runner", "HOME": "/var/empty", "HOSTNAME": "node-1"}
        )
        self.assertEqual(environment, {"USER": "runner", "HOSTNAME": "node-1"})


if __name__ == "__main__":
    unittest.main()
