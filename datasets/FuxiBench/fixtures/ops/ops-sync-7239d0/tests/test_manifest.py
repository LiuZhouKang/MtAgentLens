"""Verification suite for the ops-sync session manifest."""

from __future__ import annotations

import unittest

from ops_sync import manifest


class ManifestTest(unittest.TestCase):
    def test_manifest_lists_the_three_roles(self) -> None:
        data = manifest.load()
        self.assertEqual(manifest.roles(data), ["analyst", "operator", "auditor"])

    def test_verification_target_is_make_test(self) -> None:
        self.assertEqual(manifest.load()["verification_target"], "make test")

    def test_session_name_matches_workspace(self) -> None:
        self.assertEqual(manifest.load()["session"], "ops-sync-7239d0")


if __name__ == "__main__":
    unittest.main()
