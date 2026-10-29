"""Tests for artifact hashing and manifest generation."""

import json
import tempfile
import unittest
from pathlib import Path

from releasekit.manifest import build_manifest, sha256_file, write_manifest


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        (self.root / "a.txt").write_text("alpha\n", encoding="utf-8")
        (self.root / "nested").mkdir()
        (self.root / "nested" / "b.txt").write_text("beta\n", encoding="utf-8")

    def tearDown(self):
        self._temp.cleanup()

    def test_sha256_matches_known_vector(self):
        target = self.root / "vector.bin"
        target.write_bytes(b"abc")
        self.assertEqual(
            sha256_file(target),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_manifest_lists_every_file(self):
        manifest = build_manifest(self.root, "1.4.0", "src/releasekit")
        self.assertEqual(manifest["file_count"], 2)
        self.assertEqual(manifest["files"][0]["path"], "a.txt")
        self.assertEqual(manifest["files"][1]["path"], "nested/b.txt")
        self.assertEqual(manifest["total_bytes"], 11)

    def test_digest_is_stable(self):
        first = build_manifest(self.root, "1.4.0", "src")
        second = build_manifest(self.root, "1.4.0", "src")
        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(len(first["digest"]), 64)

    def test_digest_changes_with_content(self):
        before = build_manifest(self.root, "1.4.0", "src")["digest"]
        (self.root / "a.txt").write_text("changed\n", encoding="utf-8")
        after = build_manifest(self.root, "1.4.0", "src")["digest"]
        self.assertNotEqual(before, after)

    def test_write_manifest_roundtrip(self):
        target = self.root / "build-manifest.json"
        manifest = build_manifest(self.root, "1.4.0", "src")
        write_manifest(target, manifest)
        reloaded = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(reloaded["digest"], manifest["digest"])


if __name__ == "__main__":
    unittest.main()
