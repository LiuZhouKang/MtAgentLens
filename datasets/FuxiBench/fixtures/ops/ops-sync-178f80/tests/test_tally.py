import unittest

from vote_tally import THRESHOLD, load_ballots, weighted_tally


class TallyTest(unittest.TestCase):
    def test_threshold_met(self):
        ballots = [
            {"seat": "chair", "vote": "approve"},
            {"seat": "analyst", "vote": "approve"},
        ]
        result = weighted_tally(ballots)
        self.assertEqual(result["approve_weight"], 4)
        self.assertTrue(result["approved"])

    def test_threshold_missed(self):
        ballots = [
            {"seat": "chair", "vote": "approve"},
            {"seat": "analyst", "vote": "reject"},
            {"seat": "operator", "vote": "reject"},
        ]
        result = weighted_tally(ballots)
        self.assertEqual(result["approve_weight"], 3)
        self.assertFalse(result["approved"])

    def test_total_weight_counts_every_seat(self):
        seats = ("chair", "analyst", "operator", "auditor")
        ballots = [{"seat": seat, "vote": "reject"} for seat in seats]
        self.assertEqual(weighted_tally(ballots)["total_weight"], 6)
        self.assertEqual(THRESHOLD, 4)

    def test_load_ballots_skips_receipt_and_malformed(self):
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "seat-a.json").write_text('{"seat": "analyst", "vote": "reject"}')
            (root / "receipts.json").write_text('{"decision": "approved"}')
            (root / "broken.json").write_text("not json")
            ballots = load_ballots(root)
        self.assertEqual([b["seat"] for b in ballots], ["analyst"])


if __name__ == "__main__":
    unittest.main()
