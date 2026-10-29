"""Tests for the chain record helpers."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from syncctl import chain


class ChainRecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "chain.json"
        lines = [
            {"protocol": "sync-verification", "chain": ["analyst", "operator", "auditor"]},
            {"phase": 1, "agent": "analyst", "canary": "c-1"},
            {"phase": 2, "agent": "operator", "canary": "c-1"},
        ]
        self.path.write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_header_is_first_line(self) -> None:
        self.assertEqual(chain.header(self.path)["protocol"], "sync-verification")

    def test_hops_skip_header(self) -> None:
        self.assertEqual([hop["agent"] for hop in chain.hops(self.path)], ["analyst", "operator"])

    def test_canary_forwarded_by_each_hop(self) -> None:
        self.assertEqual(chain.canaries(self.path), ["c-1", "c-1"])

    def test_blank_lines_are_ignored(self) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write("\n\n")
        self.assertEqual(len(chain.hops(self.path)), 2)


if __name__ == "__main__":
    unittest.main()
