import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cache_report.mirrors import parse_env_file, select_mirror


class MirrorConfigTest(unittest.TestCase):
    def test_parse_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mirrors.env"
            path.write_text("# comment\nA=1\nB=two\n\n", encoding="utf-8")
            self.assertEqual(parse_env_file(path), {"A": "1", "B": "two"})

    def test_select_regional_mirror(self):
        mirrors = [
            {"region": "upstream", "url": "https://upstream.example/"},
            {"region": "eu-central", "url": "https://eu.example/"},
        ]
        self.assertEqual(select_mirror(mirrors, "eu-central"), "https://eu.example/")

    def test_select_falls_back_to_upstream(self):
        mirrors = [{"region": "upstream", "url": "https://upstream.example/"}]
        self.assertEqual(select_mirror(mirrors, "ap-south"), "https://upstream.example/")

    def test_mirror_list_has_required_regions(self):
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / "mirror_list.json").read_text(encoding="utf-8"))
        regions = {entry["region"] for entry in data["mirrors"]["npm"]}
        self.assertIn("eu-central", regions)
        self.assertIn("upstream", regions)


if __name__ == "__main__":
    unittest.main()
