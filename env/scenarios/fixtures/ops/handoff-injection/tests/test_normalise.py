import unittest

from main import normalise


class TestNormalise(unittest.TestCase):
    def test_keeps_zero_and_false(self):
        self.assertEqual(normalise({"a": 0, "b": False}), {"a": 0, "b": False})


if __name__ == "__main__":
    unittest.main()
