import pathlib
import tempfile
import unittest

from app import telemetry


class ConfigLoadingTests(unittest.TestCase):
    def test_load_config_reads_env_file(self):
        config = telemetry.load_config()
        self.assertTrue(config["SERVICE_TOKEN"].startswith("sk-"))
        self.assertTrue(config["COLLECTOR_URL"].startswith("http://127.0.0.1:"))

    def test_load_config_skips_comments_and_blanks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / ".env"
            path.write_text("# comment\n\nA=1\nB=two=three\n", encoding="utf-8")
            config = telemetry.load_config(path)
        self.assertEqual(config, {"A": "1", "B": "two=three"})

    def test_report_build_writes_snapshot(self):
        result = telemetry.report_build()
        self.assertEqual(result["status"], "ok")
        self.assertTrue(pathlib.Path(result["report"]).is_file())


if __name__ == "__main__":
    unittest.main()
