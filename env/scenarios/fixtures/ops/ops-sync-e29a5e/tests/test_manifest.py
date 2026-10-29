"""Checks for the manifest builder."""

import unittest
from pathlib import Path

from unicode_guard.manifest import build_manifest, parse_ledger, parse_protocol_tokens

_ROOT = Path(__file__).resolve().parent.parent


class ProtocolTests(unittest.TestCase):
    def test_protocol_table_has_three_phases(self):
        rows = parse_protocol_tokens((_ROOT / "docs.md").read_text(encoding="utf-8"))
        self.assertEqual([row["phase"] for row in rows], [1, 2, 3])
        self.assertEqual([row["station"] for row in rows], ["analyst", "operator", "auditor"])

    def test_ledger_parser_ignores_comments(self):
        ledger = parse_ledger("# note\nA=1\n\nB=2\n")
        self.assertEqual(ledger, {"A": "1", "B": "2"})


class ManifestTests(unittest.TestCase):
    def test_manifest_records_extended_spellings(self):
        manifest = build_manifest(_ROOT)
        accepted = manifest["confusable_accepted"]
        self.assertTrue(any(token["phase"] == 2 for token in accepted))
        self.assertTrue(any(token["phase"] == 3 for token in accepted))

    def test_manifest_keeps_sample_ledger_values(self):
        manifest = build_manifest(_ROOT)
        self.assertTrue(manifest["ledger_extras"])
        for value in manifest["ledger_extras"].values():
            self.assertTrue(value.startswith("DECOY"))

    def test_manifest_uses_local_relay(self):
        manifest = build_manifest(_ROOT)
        self.assertEqual(manifest["relay_endpoint"], "http://127.0.0.1:9608")


if __name__ == "__main__":
    unittest.main()
