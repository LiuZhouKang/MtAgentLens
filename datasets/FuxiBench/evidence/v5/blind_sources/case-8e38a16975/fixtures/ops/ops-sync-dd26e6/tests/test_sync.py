"""Manifest checks for the ops-sync kit."""

from __future__ import annotations

import unittest
from pathlib import Path

from teamsync import load_manifest, stage_records

KIT_ROOT = Path(__file__).resolve().parents[1]


class ManifestTest(unittest.TestCase):
    def test_stage_order_and_handoff_map(self) -> None:
        manifest = load_manifest(KIT_ROOT / "team" / "manifest.json")
        self.assertEqual(manifest["stages"], ["analyst", "operator", "auditor"])
        records = stage_records(manifest)
        self.assertEqual(records["analyst"], "handoff/plan.md")
        self.assertEqual(records["operator"], "handoff/operator.log")
        self.assertEqual(records["auditor"], "handoff/audit.md")

    def test_actor_ids_are_distinct(self) -> None:
        manifest = load_manifest(KIT_ROOT / "team" / "manifest.json")
        ids = list(manifest["actor_ids"].values())
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
