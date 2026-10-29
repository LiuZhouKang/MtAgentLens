import unittest

from ops_sync.ledger import tally_verdicts, verdict_line


class VerdictLineTests(unittest.TestCase):
    def test_reads_last_verdict(self):
        text = "draft\nVERDICT: HOLD\nnotes\nVERDICT: APPROVE\n"
        self.assertEqual(verdict_line(text), "VERDICT: APPROVE")

    def test_missing_verdict_is_empty(self):
        self.assertEqual(verdict_line("no verdict here"), "")

    def test_ignores_surrounding_whitespace(self):
        self.assertEqual(verdict_line("  VERDICT: APPROVE  "), "VERDICT: APPROVE")


class TallyTests(unittest.TestCase):
    def test_counts_approvals(self):
        texts = ["VERDICT: APPROVE", "VERDICT: HOLD", "VERDICT: APPROVE"]
        self.assertEqual(tally_verdicts(texts), 2)

    def test_empty_input(self):
        self.assertEqual(tally_verdicts([]), 0)


if __name__ == "__main__":
    unittest.main()
