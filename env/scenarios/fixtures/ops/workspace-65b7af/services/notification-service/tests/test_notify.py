"""Tests for notification-service rendering helpers."""
import unittest

from src.notify import chunk, dedupe, render


class NotifyTests(unittest.TestCase):
    def test_render_substitutes_values(self):
        message = render("order {order_id} is {state}", {"order_id": "A-7", "state": "ready"})
        self.assertEqual(message, "order A-7 is ready")

    def test_render_rejects_missing_value(self):
        with self.assertRaises(ValueError):
            render("order {order_id}", {})

    def test_dedupe_normalizes_and_preserves_order(self):
        self.assertEqual(
            dedupe([" Email ", "sms", "email", "", "SMS"]), ["email", "sms"]
        )

    def test_chunk_splits_into_fixed_batches(self):
        events = [{"id": index} for index in range(5)]
        self.assertEqual(len(chunk(events, 2)), 3)
        self.assertEqual(chunk(events, 2)[-1], [{"id": 4}])

    def test_chunk_rejects_non_positive_size(self):
        with self.assertRaises(ValueError):
            chunk([], 0)


if __name__ == "__main__":
    unittest.main()
