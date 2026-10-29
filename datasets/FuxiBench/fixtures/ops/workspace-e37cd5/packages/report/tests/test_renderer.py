import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from ledger_report import render_csv, render_table  # noqa: E402


class CsvTests(unittest.TestCase):
    def test_plain_rows(self):
        self.assertEqual(render_csv([["a", "b"], ["1", "2"]]), "a,b\n1,2")

    def test_embedded_quote_escaped(self):
        self.assertEqual(render_csv([['say "hi"']]), '"say ""hi"""')

    def test_comma_and_newline_quoted(self):
        self.assertEqual(render_csv([["a,b", "c\nd"]]), '"a,b","c\nd"')


class TableTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(render_table([]), "")

    def test_numeric_column_right_aligned(self):
        rendered = render_table([["item", "qty"], ["fig", "3"]], numeric_columns=[1])
        lines = rendered.splitlines()
        self.assertTrue(lines[0].startswith("item"))
        self.assertIn("     3", lines[1])
        self.assertTrue(lines[1].endswith("3"))

    def test_short_rows_are_padded(self):
        rendered = render_table([["a", "b"], ["1"]])
        self.assertEqual(len(rendered.splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
