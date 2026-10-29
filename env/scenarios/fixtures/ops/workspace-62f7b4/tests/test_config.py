"""Unit tests for environment-driven settings."""

import os
import unittest
from unittest import mock

from app.config import load_settings, parse_timeout


class ConfigurationTests(unittest.TestCase):
    def test_default_timeout(self):
        self.assertEqual(load_settings({}).timeout_seconds, 30)

    def test_default_mode(self):
        self.assertEqual(load_settings({}).mode, "strict")

    def test_timeout_with_seconds_suffix(self):
        self.assertEqual(parse_timeout("45s"), 45)

    def test_timeout_without_suffix(self):
        self.assertEqual(parse_timeout("12"), 12)

    def test_mode_override(self):
        self.assertEqual(load_settings({"REPORT_MODE": "lenient"}).mode, "lenient")

    def test_timeout_from_runner_environment(self):
        with mock.patch.dict(os.environ, {"REPORT_TIMEOUT": "45s"}):
            self.assertEqual(load_settings().timeout_seconds, 45)


if __name__ == "__main__":
    unittest.main()
