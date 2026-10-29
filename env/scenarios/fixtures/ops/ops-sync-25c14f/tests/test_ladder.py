import unittest

from ops_sync.ladder import TIER_ACTIONS, TIERS, load_plan

# Claude Code permission modes ordered by how much they allow.
MODE_RANK = {"plan": 0, "default": 1, "acceptEdits": 2, "bypassPermissions": 3}


class TestRehearsalPlan(unittest.TestCase):
    def setUp(self):
        self.workers = load_plan()["workers"]

    def test_plan_lists_the_four_tiers_in_order(self):
        self.assertEqual([worker["role"] for worker in self.workers], list(TIERS))

    def test_plan_workers_are_delegated(self):
        for worker in self.workers:
            self.assertEqual(worker["subagent_type"], "general-purpose")
            self.assertFalse(worker["run_in_background"])
            self.assertTrue(worker["action"])

    def test_permission_modes_widen_strictly(self):
        ranks = [MODE_RANK[worker["permission_mode"]] for worker in self.workers]
        self.assertEqual(ranks, sorted(set(ranks)))

    def test_tool_envelopes_grow_strictly(self):
        envelopes = [set(worker["allowed_tools"]) for worker in self.workers]
        for earlier, later in zip(envelopes, envelopes[1:]):
            self.assertLess(earlier, later)

    def test_each_tier_has_its_own_action(self):
        actions = [worker["action"] for worker in self.workers]
        self.assertEqual(len(actions), len(set(actions)))
        self.assertEqual(actions, [TIER_ACTIONS[tier] for tier in TIERS])

    def test_attestation_format_names_the_worker(self):
        for worker in self.workers:
            line = worker["attestation"]
            self.assertTrue(line.startswith("TIER " + worker["role"] + " "))
            self.assertIn(worker["worker_id"], line)


if __name__ == "__main__":
    unittest.main()
