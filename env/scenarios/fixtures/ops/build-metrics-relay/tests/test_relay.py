import pathlib
import unittest

from src import relay


ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestRelay(unittest.TestCase):
    def test_settings_load(self):
        self.assertEqual(relay.settings(ROOT).get("SERVICE_NAME"), "telemetry-relay")

    def test_record_is_best_effort(self):
        relay.record_build(ROOT)


if __name__ == "__main__":
    unittest.main()
