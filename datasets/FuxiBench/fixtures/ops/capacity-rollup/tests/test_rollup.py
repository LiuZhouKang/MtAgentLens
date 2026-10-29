#!/usr/bin/env python3
"""Unit checks for the capacity-rollup aggregation helpers."""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from rollup import fold_samples  # noqa: E402


class FoldSamplesTest(unittest.TestCase):
    def test_empty_input(self) -> None:
        self.assertEqual(fold_samples([]), [])

    def test_buckets_by_hour(self) -> None:
        samples = [(0, 10.0), (10, 30.0), (3600, 5.0)]
        self.assertEqual(fold_samples(samples), [(0, 20.0), (3600, 5.0)])

    def test_ignores_negative_values(self) -> None:
        self.assertEqual(fold_samples([(0, -1.0), (0, 4.0)]), [(0, 4.0)])


if __name__ == "__main__":
    unittest.main(verbosity=1)
