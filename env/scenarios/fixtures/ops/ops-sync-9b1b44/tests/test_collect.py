import tempfile
import unittest
from pathlib import Path

from ops_sync.collect import stage


class CollectTests(unittest.TestCase):
    def test_stage_writes_manifest_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            (root / "config").mkdir(parents=True)
            (root / "config" / "pipeline.env").write_text("A=1\n", encoding="utf-8")
            out = stage(root, Path(tmp) / "out")
            self.assertTrue((out / "manifest.json").is_file())
            self.assertEqual(
                (out / "index.txt").read_text(encoding="utf-8").strip(),
                "config/pipeline.env",
            )


if __name__ == "__main__":
    unittest.main()
