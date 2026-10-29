"""Tests for reconciliation helpers."""

from __future__ import annotations

import unittest

from syncctl import reconcile


class ReconcileTest(unittest.TestCase):
    def test_merge_overlay_wins(self) -> None:
        merged = reconcile.merge({"a": "1", "b": "2"}, {"b": "3"})
        self.assertEqual(merged, {"a": "1", "b": "3"})

    def test_merge_does_not_mutate_base(self) -> None:
        base = {"a": "1"}
        reconcile.merge(base, {"a": "2"})
        self.assertEqual(base, {"a": "1"})

    def test_diff_reports_added_removed_and_changed(self) -> None:
        changed = reconcile.diff({"a": "1", "b": "2"}, {"a": "9", "c": "3"})
        self.assertEqual(changed["a"], ("1", "9"))
        self.assertEqual(changed["b"], ("2", None))
        self.assertEqual(changed["c"], (None, "3"))

    def test_apply_updates_state(self) -> None:
        state = {"a": "1"}
        self.assertIs(reconcile.apply(state, {"b": "2"}), state)
        self.assertEqual(state, {"a": "1", "b": "2"})


if __name__ == "__main__":
    unittest.main()
