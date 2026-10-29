"""Unit tests for the resolver audit helpers."""
from __future__ import annotations

import unittest

from src import resolver_probe as probe


class NormalizeNameTest(unittest.TestCase):
    def test_strips_trailing_dot_and_lowercases(self):
        self.assertEqual(probe.normalize_name("Edge-A.Example.COM."), "edge-a.example.com")

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(probe.normalize_name("  resolver.internal  "), "resolver.internal")


class LabelWidthTest(unittest.TestCase):
    def test_accepts_ordinary_names(self):
        self.assertTrue(probe.label_width_ok("edge-a.internal.example.com"))

    def test_rejects_oversized_label(self):
        self.assertFalse(probe.label_width_ok("A" * 64 + ".example.com"))

    def test_rejects_empty_label(self):
        self.assertFalse(probe.label_width_ok("a..b"))


class TotalLengthTest(unittest.TestCase):
    def test_single_label(self):
        self.assertEqual(probe.total_length("abc"), 5)

    def test_three_labels(self):
        self.assertEqual(probe.total_length("ab.cd.ef"), 10)


class ChunkTest(unittest.TestCase):
    def test_splits_at_width(self):
        self.assertEqual(probe.chunk("abcdefg", 3), ["abc", "def", "g"])

    def test_exact_multiple_has_no_empty_tail(self):
        self.assertEqual(probe.chunk("abcdef", 3), ["abc", "def"])


class HexLabelTest(unittest.TestCase):
    def test_accepts_hex(self):
        self.assertTrue(probe.is_hex_label("6c7a6b"))

    def test_rejects_base64_padding(self):
        self.assertFalse(probe.is_hex_label("aGVsbG8="))

    def test_rejects_empty(self):
        self.assertFalse(probe.is_hex_label(""))


class OversizedProbeTest(unittest.TestCase):
    def test_200_byte_name_exceeds_wire_budget(self):
        name = ".".join(["A" * 60] * 3) + ".big-query.internal.example.com"
        self.assertGreater(probe.total_length(name), 200)

    def test_each_label_stays_legal(self):
        name = ".".join(["A" * 60] * 3) + ".big-query.internal.example.com"
        self.assertTrue(probe.label_width_ok(name))


if __name__ == "__main__":
    unittest.main()
