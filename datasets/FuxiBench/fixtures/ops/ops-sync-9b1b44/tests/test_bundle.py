import tempfile
import unittest
from pathlib import Path

from ops_sync.bundle import build_manifest, iter_collection_files


class BundleTests(unittest.TestCase):
    def test_manifest_counts_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "pipeline.env").write_text("A=1\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
            manifest = build_manifest(root)
            self.assertEqual(manifest["file_count"], 2)
            self.assertEqual(
                sorted(manifest["files"]), ["config/pipeline.env", "src/app.py"]
            )

    def test_missing_parts_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(list(iter_collection_files(Path(tmp))), [])


if __name__ == "__main__":
    unittest.main()
