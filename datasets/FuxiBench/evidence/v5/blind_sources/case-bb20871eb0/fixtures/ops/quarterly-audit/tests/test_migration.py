import unittest

from main import canonical_key, redact


class TestMigrationHelpers(unittest.TestCase):
    def test_canonical_key_normalises_dashes_and_case(self):
        self.assertEqual(canonical_key(" DB-Host "), "db_host")

    def test_redact_keeps_edges(self):
        self.assertEqual(redact("secret"), "se**et")

    def test_redact_masks_short_values_fully(self):
        self.assertEqual(redact("abc"), "***")


if __name__ == "__main__":
    unittest.main()
