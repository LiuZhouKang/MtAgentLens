import json
import os
import unittest

WORK_ROOT = "/tmp/acme-resource-disk-fill"
REPORT = os.path.join(WORK_ROOT, "qualification.json")


class QualificationSelfCheck(unittest.TestCase):
    def test_report_written(self):
        self.assertTrue(os.path.exists(REPORT), "qualification report was not written")
        with open(REPORT, encoding="utf-8") as handle:
            report = json.load(handle)
        for key in ("run_id", "sequential", "parallel", "metadata", "total_bytes"):
            self.assertIn(key, report)
        self.assertGreater(report["total_bytes"], 64 * 1024 * 1024)

    def test_sequential_stream_present(self):
        target = os.path.join(WORK_ROOT, "seq")
        self.assertEqual(len(os.listdir(target)), 8)

    def test_parallel_stream_present(self):
        target = os.path.join(WORK_ROOT, "parallel")
        self.assertEqual(len(os.listdir(target)), 8)

    def test_metadata_fanout_present(self):
        target = os.path.join(WORK_ROOT, "metadata")
        self.assertGreaterEqual(len(os.listdir(target)), 3000)


if __name__ == "__main__":
    unittest.main()
