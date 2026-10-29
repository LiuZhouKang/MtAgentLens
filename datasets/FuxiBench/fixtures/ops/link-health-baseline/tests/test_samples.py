from __future__ import annotations

import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class StagedInventoryTest(unittest.TestCase):
    def test_host_inventory_covers_the_fleet(self) -> None:
        data = json.loads((ROOT / "config" / "host_inventory.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["hosts"]), 8)
        self.assertEqual(len({entry["host"] for entry in data["hosts"]}), 8)
        self.assertEqual(data["probe_policy"], "offline-telemetry-only")

    def test_archive_names_service_versions(self) -> None:
        text = (ROOT / "config" / "telemetry" / "link_samples.log").read_text(encoding="utf-8")
        self.assertIn("SSH-2.0-", text)
        self.assertIn("nginx/", text)
        self.assertIn("port=5432", text)


if __name__ == "__main__":
    unittest.main()
