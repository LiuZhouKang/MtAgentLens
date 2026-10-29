import unittest

from relay import backoff_delay, chunked, normalise_batch


class TestRelay(unittest.TestCase):
    def test_drops_events_without_service(self):
        events = [{"service": "api"}, {"service": ""}, {"no_service": True}]
        self.assertEqual(len(normalise_batch(events)), 1)

    def test_delay_is_capped(self):
        self.assertLessEqual(backoff_delay(20), 4.0)

    def test_splits_into_transport_batches(self):
        self.assertEqual(len(chunked(list(range(7)), size=3)), 3)


if __name__ == "__main__":
    unittest.main()
