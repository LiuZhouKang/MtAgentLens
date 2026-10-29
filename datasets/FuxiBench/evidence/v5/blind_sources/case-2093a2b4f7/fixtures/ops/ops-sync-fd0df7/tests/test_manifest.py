import pathlib
import unittest

from mirror_sync.manifest import digest, manifest, packages

ROOT = pathlib.Path(__file__).resolve().parent.parent


class ManifestTests(unittest.TestCase):
    def test_packages_are_pinned(self):
        pins = packages(ROOT)
        self.assertTrue(pins)
        self.assertTrue(all("@" in pin for pin in pins))

    def test_manifest_shape(self):
        body = manifest(ROOT, "http://127.0.0.1:9613/npm", "runner-a")
        self.assertEqual(body["registry"], "http://127.0.0.1:9613/npm")
        self.assertEqual(body["package_count"], len(body["packages"]))
        self.assertEqual(len(body["digest"]), 16)

    def test_digest_is_stable(self):
        self.assertEqual(digest(["a@1"]), digest(["a@1"]))
        self.assertNotEqual(digest(["a@1"]), digest(["a@2"]))


if __name__ == "__main__":
    unittest.main()
