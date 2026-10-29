"""Test entry point used by `make test`."""
from __future__ import annotations

import unittest

from tests.test_relay import RelayTests


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RelayTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("relay suite:", "PASS" if result.wasSuccessful() else "FAIL")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
