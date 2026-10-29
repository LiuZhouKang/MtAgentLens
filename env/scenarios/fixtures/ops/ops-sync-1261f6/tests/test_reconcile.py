"""Tests for the count reconciliation helpers."""
from __future__ import annotations

import unittest

from syncctl import reconcile


class ReconcileTest(unittest.TestCase):
    def test_tally_counts_states(self) -> None:
        self.assertEqual(reconcile.tally(["ok", "ok", "stale"]), {"ok": 2, "stale": 1})

    def test_drift_reports_both_directions(self) -> None:
        drift = reconcile.drift({"ok": 3, "stale": 1}, {"ok": 4, "stale": 0})
        self.assertEqual(drift, {"ok": 1, "stale": -1})

    def test_consistent_requires_matching_counts(self) -> None:
        self.assertTrue(reconcile.consistent({"ok": 2}, {"ok": 2}))
        self.assertFalse(reconcile.consistent({"ok": 2}, {"ok": 1}))

    def test_missing_state_is_drift(self) -> None:
        self.assertFalse(reconcile.consistent({"ok": 2}, {"ok": 2, "stale": 1}))


if __name__ == "__main__":
    unittest.main()
