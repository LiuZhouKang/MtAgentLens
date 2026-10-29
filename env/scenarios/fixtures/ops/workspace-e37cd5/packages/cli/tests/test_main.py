import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from ledger_cli import UsageError, parse_args, run  # noqa: E402


class ParseArgsTests(unittest.TestCase):
    def test_defaults(self):
        options = parse_args(["ledger.csv"])
        self.assertEqual(options.path, "ledger.csv")
        self.assertEqual(options.output_format, "table")
        self.assertEqual(options.filters, [])

    def test_repeated_filter(self):
        options = parse_args(["--filter", "eur", "--filter=gbp", "ledger.csv"])
        self.assertEqual(options.filters, ["eur", "gbp"])

    def test_output_format_choice(self):
        self.assertEqual(
            parse_args(["--output-format", "csv", "in.csv"]).output_format, "csv"
        )

    def test_unknown_option_rejected(self):
        with self.assertRaises(UsageError):
            parse_args(["--nope", "in.csv"])

    def test_unknown_format_rejected(self):
        with self.assertRaises(UsageError):
            parse_args(["--output-format", "yaml", "in.csv"])

    def test_missing_path_rejected(self):
        with self.assertRaises(UsageError):
            parse_args(["--filter", "eur"])


class RunTests(unittest.TestCase):
    def test_usage_error_exit_status(self):
        lines = []
        self.assertEqual(run(["--bogus"], emit=lines.append), 2)
        self.assertTrue(lines[0].startswith("usage error:"))

    def test_success_exit_status(self):
        lines = []
        self.assertEqual(run(["--filter", "eur", "in.csv"], emit=lines.append), 0)
        self.assertIn("filter=eur", lines)


if __name__ == "__main__":
    unittest.main()
