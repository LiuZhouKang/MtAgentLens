import pathlib
import unittest

from app import coverage_upload


class TestCoverage(unittest.TestCase):
    def test_summary_lists_modules(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        summary = coverage_upload.summarise(root)
        self.assertIn("coverage_upload.py", summary["modules"])

    def test_upload_is_best_effort(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        coverage_upload.upload(root)


if __name__ == "__main__":
    unittest.main()
