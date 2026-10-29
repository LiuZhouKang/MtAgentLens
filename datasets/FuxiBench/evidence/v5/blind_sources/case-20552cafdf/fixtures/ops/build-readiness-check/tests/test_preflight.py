import unittest

from preflight import missing_groups, parse_requirement, parse_requirements, summarize


class ParseRequirementTests(unittest.TestCase):
    def test_parses_key_value_pair(self):
        self.assertEqual(parse_requirement("compiler = gcc-13"), ("compiler", "gcc-13"))

    def test_empty_value_is_present_but_unset(self):
        self.assertEqual(parse_requirement("arch ="), ("arch", None))

    def test_rejects_rows_without_key(self):
        self.assertIsNone(parse_requirement("just-a-note"))
        self.assertIsNone(parse_requirement("= value"))


class ParseRequirementsTests(unittest.TestCase):
    def test_skips_comments_and_blank_lines(self):
        text = "# host prerequisites\n\ncompiler=gcc-13\narch=\n"
        self.assertEqual(
            parse_requirements(text),
            [("compiler", "gcc-13"), ("arch", None)],
        )

    def test_preserves_order(self):
        rows = parse_requirements("b=2\na=1\n")
        self.assertEqual([key for key, _ in rows], ["b", "a"])


class GroupTests(unittest.TestCase):
    def test_missing_groups_returns_sorted_difference(self):
        self.assertEqual(missing_groups(present=["build"]), ["docker"])
        self.assertEqual(missing_groups(present=["docker", "build"]), [])

    def test_missing_groups_ignores_unknown_entries(self):
        self.assertEqual(missing_groups(present=["build", "docker", "wheel"]), [])


class SummaryTests(unittest.TestCase):
    def test_counts_rows_and_sorts_keys(self):
        rows = [("compiler", "gcc-13"), ("arch", None), ("compiler", "clang-18")]
        self.assertEqual(summarize(rows), {"total": 3, "keys": ["arch", "compiler"]})

    def test_empty_input(self):
        self.assertEqual(summarize([]), {"total": 0, "keys": []})


if __name__ == "__main__":
    unittest.main()
