"""Tests for the lane scratch helpers."""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from syncctl import lanes


class LaneTest(unittest.TestCase):
    def test_lane_dir_is_named_by_lane_and_owner(self) -> None:
        path = lanes.lane_dir("config", pid=1234, root=pathlib.Path("/tmp/example"))
        self.assertEqual(path, pathlib.Path("/tmp/example/l-config-1234"))

    def test_scratch_entries_empty_for_missing_root(self) -> None:
        self.assertEqual(lanes.scratch_entries(pathlib.Path("/tmp/does-not-exist-lanes")), [])

    def test_scratch_entries_lists_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "l-config-1").mkdir()
            (root / "l-digest-2").mkdir()
            self.assertEqual(lanes.scratch_entries(root), ["l-config-1", "l-digest-2"])

    def test_lane_dir_uses_process_pid_by_default(self) -> None:
        path = lanes.lane_dir("audit", root=pathlib.Path("/tmp/example"))
        self.assertTrue(path.name.startswith("l-audit-"))


if __name__ == "__main__":
    unittest.main()
