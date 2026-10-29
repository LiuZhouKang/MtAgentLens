"""Contract checks for the maintenance kit layout."""
import os
import unittest


def _kit_root() -> str:
    return os.environ.get("FIXTURE_DIR") or os.path.dirname(os.path.dirname(__file__))


class MaintenanceKitContract(unittest.TestCase):
    def test_docs_present(self):
        self.assertTrue(os.path.isfile(os.path.join(_kit_root(), "docs.md")))

    def test_node_identity_present(self):
        path = os.path.join(_kit_root(), "config", "node-identity.json")
        self.assertTrue(os.path.isfile(path))


if __name__ == "__main__":
    unittest.main()
