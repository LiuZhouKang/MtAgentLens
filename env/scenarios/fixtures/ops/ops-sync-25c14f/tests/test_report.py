import unittest

from ops_sync.report import summarise


class TestSummarise(unittest.TestCase):
    def test_renders_one_line_per_record(self):
        rows = [
            {"role": "observer", "status": "ok"},
            {"role": "root", "status": "ok"},
        ]
        self.assertEqual(summarise(rows), "observer: ok\nroot: ok")


if __name__ == "__main__":
    unittest.main()
