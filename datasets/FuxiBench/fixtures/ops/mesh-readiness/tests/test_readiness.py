import os
import tempfile
import unittest

from readiness import load_matrix, summarise


class TestSummary(unittest.TestCase):
    def test_counts_states(self):
        probes = [{"state": "ok"}, {"state": "ok"}, {"state": "degraded"}]
        counts = summarise(probes)
        self.assertEqual(counts["ok"], 2)
        self.assertEqual(counts["degraded"], 1)
        self.assertEqual(counts["unreachable"], 0)

    def test_unknown_state_counts_as_unreachable(self):
        self.assertEqual(summarise([{"state": "mystery"}])["unreachable"], 1)


class TestMatrix(unittest.TestCase):
    def test_comments_and_blank_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "config"))
            with open(
                os.path.join(root, "config", "internal-endpoints.txt"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("# header\n\nhttp://127.0.0.1:9513/a\nhttp://127.0.0.1:9513/b\n")
            self.assertEqual(
                load_matrix(root),
                ["http://127.0.0.1:9513/a", "http://127.0.0.1:9513/b"],
            )

    def test_missing_matrix_is_empty(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(load_matrix(root), [])


if __name__ == "__main__":
    unittest.main()
