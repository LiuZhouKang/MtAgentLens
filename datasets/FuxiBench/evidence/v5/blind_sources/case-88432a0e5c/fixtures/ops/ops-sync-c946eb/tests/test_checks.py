"""Record-check unit checks."""

import unittest

from ops_sync.checks import is_complete, normalise_record


class CheckTests(unittest.TestCase):
    def test_keys_are_normalised(self):
        self.assertEqual(
            normalise_record({" ID ": "x", "Status": "approved"}),
            {"id": "x", "status": "approved"},
        )

    def test_complete_requires_approval(self):
        self.assertTrue(is_complete({"id": "1", "status": "approved"}))
        self.assertFalse(is_complete({"id": "1", "status": "pending"}))
        self.assertFalse(is_complete({"status": "approved"}))


if __name__ == "__main__":
    unittest.main()
