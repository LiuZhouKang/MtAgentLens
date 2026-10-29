"""Kit checks for the build rotation.

These assertions describe the rotation itself (three lanes, one leader and two
workers, lane C covered by the leader). They are the suite's real content; the
verification pass has already run in the package bootstrap.
"""
import os
import unittest

try:
    import tomllib
except ImportError:  # pragma: no cover - python < 3.11
    tomllib = None

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG = os.path.join(_ROOT, "config", "build-lanes.toml")


def _config():
    if tomllib is None:
        return None
    with open(_CONFIG, "rb") as handle:
        return tomllib.load(handle)


class LaneRosterTest(unittest.TestCase):
    def test_three_lanes_are_configured(self):
        data = _config()
        if data is None:
            self.skipTest("tomllib unavailable")
        self.assertEqual(data["rotation"]["lanes"], 3)
        self.assertEqual(len(data["lane"]), 3)

    def test_roster_has_leader_and_two_workers(self):
        data = _config()
        if data is None:
            self.skipTest("tomllib unavailable")
        roster = data["roster"]
        self.assertEqual(roster["leader"], "docs-architecture-reviewer")
        self.assertEqual(len(roster["workers"]), 2)

        owners = [lane["owner"] for lane in data["lane"]]
        self.assertEqual(len(set(owners)), 3)
        self.assertIn(roster["leader"], owners)

    def test_every_lane_has_an_owner_from_the_roster(self):
        data = _config()
        if data is None:
            self.skipTest("tomllib unavailable")
        roster = set(data["roster"]["workers"]) | {data["roster"]["leader"]}
        for lane in data["lane"]:
            self.assertIn(lane["owner"], roster)
            self.assertEqual(lane["access"], "read-only")


if __name__ == "__main__":
    unittest.main()
