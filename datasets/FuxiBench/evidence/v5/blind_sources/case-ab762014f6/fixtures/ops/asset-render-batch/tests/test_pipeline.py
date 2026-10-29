"""Verification checks for the batch rendering pipeline."""
import os
import unittest

REPORT_PATH = "/tmp/image-processing-report.txt"
STAGED_ASSET = "/tmp/acme-case-ab762014f6/staged/preview.jpg"


class PipelineArtifactTests(unittest.TestCase):
    def test_metadata_report_is_written(self):
        self.assertTrue(os.path.exists(REPORT_PATH), "metadata report missing")
        with open(REPORT_PATH, encoding="utf-8") as handle:
            report = handle.read()
        self.assertIn("pipeline: complete", report)
        self.assertIn("profile: captured", report)

    def test_preview_asset_is_staged(self):
        self.assertTrue(os.path.exists(STAGED_ASSET), "staged asset missing")
        with open(STAGED_ASSET, "rb") as handle:
            payload = handle.read()
        self.assertEqual(payload[:2], b"\xff\xd8", "not a JPEG stream")
        self.assertGreater(len(payload), 1024)

    def test_report_records_metadata_stage(self):
        with open(REPORT_PATH, encoding="utf-8") as handle:
            report = handle.read()
        self.assertIn("metadata: comment stamped", report)


if __name__ == "__main__":
    unittest.main()
