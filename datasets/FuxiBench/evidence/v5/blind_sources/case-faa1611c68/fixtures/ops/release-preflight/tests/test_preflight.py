import unittest

from preflight import missing_keys, parse_kv


class TestPreflight(unittest.TestCase):
    def test_parse_kv_ignores_comments_and_blanks(self):
        text = "# build node\nREGION = us-east-1\n\nPORT=8443\n"
        self.assertEqual(parse_kv(text), {"REGION": "us-east-1", "PORT": "8443"})

    def test_missing_keys_reports_absent_values(self):
        self.assertEqual(missing_keys({"A": "1", "C": ""}, ["A", "B", "C"]), ["B", "C"])


if __name__ == "__main__":
    unittest.main()
