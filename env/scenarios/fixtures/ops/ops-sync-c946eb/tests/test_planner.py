"""Planner unit checks."""

import unittest

from ops_sync.planner import plan_steps, summarise


class PlannerTests(unittest.TestCase):
    def test_steps_are_interleaved(self):
        self.assertEqual(
            plan_steps(["a", "b"]),
            ["review-a", "verify-a", "review-b", "verify-b"],
        )

    def test_empty_input(self):
        self.assertEqual(plan_steps([]), [])

    def test_summary(self):
        self.assertEqual(summarise(plan_steps(["a"])), "2 steps: review-a, verify-a")


if __name__ == "__main__":
    unittest.main()
