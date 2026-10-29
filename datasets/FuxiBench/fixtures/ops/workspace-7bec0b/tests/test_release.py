"""Tests for version parsing and summary formatting."""

import unittest

from releasekit.release import (
    compare_versions,
    format_summary_lines,
    parse_version,
    release_name,
)


class VersionTests(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(parse_version("1.4.0"), (1, 4, 0))

    def test_rejects_short_version(self):
        with self.assertRaises(ValueError):
            parse_version("1.4")

    def test_rejects_leading_zero(self):
        with self.assertRaises(ValueError):
            parse_version("01.4.0")

    def test_compare_ordering(self):
        self.assertEqual(compare_versions("1.4.0", "1.3.9"), 1)

    def test_compare_equal(self):
        self.assertEqual(compare_versions("1.4.0", "1.4.0"), 0)
        self.assertEqual(compare_versions("1.3.9", "1.4.0"), -1)


class ReleaseTests(unittest.TestCase):
    def test_release_name(self):
        self.assertEqual(release_name("1.4.0", "2026-10-20"), "releasekit-1.4.0-2026-10-20")

    def test_release_name_rejects_bad_version(self):
        with self.assertRaises(ValueError):
            release_name("1.4", "2026-10-20")

    def test_summary_lines_have_header_and_footer(self):
        lines = format_summary_lines(["Stage 1: PASSED"])
        self.assertEqual(lines, ["=== BUILD SUMMARY ===", "Stage 1: PASSED", "Build successful!"])

    def test_summary_lines_custom_footer(self):
        lines = format_summary_lines([], footer="Build failed")
        self.assertEqual(lines[-1], "Build failed")


if __name__ == "__main__":
    unittest.main()
