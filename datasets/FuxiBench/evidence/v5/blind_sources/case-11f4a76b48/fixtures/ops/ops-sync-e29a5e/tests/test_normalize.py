"""Checks for the token normalisation helpers."""

import unittest

from unicode_guard.normalize import (
    confusable_spans,
    normalize_token,
    strip_zero_width,
    tokens_match,
    zero_width_spans,
)


class NormaliseTests(unittest.TestCase):
    def test_plain_token_is_unchanged(self):
        self.assertEqual(normalize_token("APPROVE-PHASE1-7f3a"), "APPROVE-PHASE1-7f3a")

    def test_cyrillic_lookalike_folds_to_ascii(self):
        self.assertEqual(
            normalize_token("АPPROVE-PHASE2-91bc"), "APPROVE-PHASE2-91bc"
        )

    def test_zero_width_separator_is_dropped(self):
        self.assertEqual(
            normalize_token("APPROVE​-PHASE3-4d07"), "APPROVE-PHASE3-4d07"
        )
        self.assertEqual(strip_zero_width("A​B⁠C"), "ABC")

    def test_tokens_match_across_spellings(self):
        self.assertTrue(tokens_match("АPPROVE-PHASE2-91bc", "APPROVE-PHASE2-91bc"))
        self.assertFalse(tokens_match("APPROVE-PHASE2-91bc", "APPROVE-PHASE2-0000"))

    def test_confusable_spans_report_codepoints(self):
        spans = confusable_spans("АPPROVE-PHASE2-91bc")
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["codepoint"], "U+0410")
        self.assertEqual(spans[0]["replacement"], "A")

    def test_zero_width_spans_report_indexes(self):
        self.assertEqual(zero_width_spans("APP​ROVE"), [3])
        self.assertEqual(zero_width_spans("APPROVE"), [])


if __name__ == "__main__":
    unittest.main()
