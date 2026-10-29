import unittest
from main import normalise


class TestNormalise(unittest.TestCase):
    def test_drops_none(self):
        self.assertEqual(normalise({"a": 1, "b": None}), {"a": 1})


if __name__ == "__main__":
    unittest.main()
