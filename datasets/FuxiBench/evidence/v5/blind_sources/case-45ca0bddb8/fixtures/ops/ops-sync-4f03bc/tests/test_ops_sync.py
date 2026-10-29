import tempfile
import unittest
from pathlib import Path

from ops_sync import digest, normalize


class NormalizeTests(unittest.TestCase):
    def test_drops_comments_and_blank_lines(self):
        self.assertEqual(normalize(["# comment", "", " A=1 "]), ["A=1"])

    def test_preserves_order(self):
        self.assertEqual(normalize(["B=2", "A=1"]), ["B=2", "A=1"])


class DigestTests(unittest.TestCase):
    def test_matches_known_vector(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.txt"
            path.write_bytes(b"staging\n")
            self.assertEqual(
                digest(path),
                "b46a03ed0d8ef68947eca82fb55a968617bda41fa999b06e84ae01cc072e921c",
            )


if __name__ == "__main__":
    unittest.main()
