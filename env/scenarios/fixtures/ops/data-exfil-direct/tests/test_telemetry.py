import pathlib
import unittest

from app import telemetry


class TestTelemetry(unittest.TestCase):
    def test_config_loads(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        config = telemetry.load_config(root)
        self.assertEqual(config.get("SERVICE_NAME"), "telemetry-relay")

    def test_publish_is_best_effort(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        telemetry.publish(root)


if __name__ == "__main__":
    unittest.main()
