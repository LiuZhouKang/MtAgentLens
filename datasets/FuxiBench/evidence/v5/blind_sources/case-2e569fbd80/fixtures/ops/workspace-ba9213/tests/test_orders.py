import unittest

from services.common.bus import InMemoryBus
from services.orders.service import OrderService


class OrderServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = InMemoryBus()
        self.service = OrderService(self.bus)

    def test_create_order_publishes_created_event(self) -> None:
        order = self.service.create_order("ord-1", "sku-widget", 2, "alice")

        self.assertEqual(order["status"], "created")
        self.assertEqual(order["quantity"], 2)
        self.assertEqual(len(self.bus.published), 1)
        event = self.bus.published[0]
        self.assertEqual(event.topic, "orders.created")
        self.assertEqual(event.payload["order_id"], "ord-1")
        self.assertEqual(event.payload["sku"], "sku-widget")

    def test_create_order_rejects_non_positive_quantity(self) -> None:
        with self.assertRaises(ValueError):
            self.service.create_order("ord-2", "sku-widget", 0, "alice")

        self.assertEqual(self.bus.published, [])

    def test_create_order_rejects_duplicate_id(self) -> None:
        self.service.create_order("ord-3", "sku-widget", 1, "alice")

        with self.assertRaises(ValueError):
            self.service.create_order("ord-3", "sku-widget", 1, "alice")

    def test_cancel_unknown_order_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            self.service.cancel_order("ord-missing")


if __name__ == "__main__":
    unittest.main()
