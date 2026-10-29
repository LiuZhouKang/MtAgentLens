import unittest

from services.common.bus import InMemoryBus
from services.common.events import (
    Event,
    INVENTORY_REJECTED,
    INVENTORY_RESERVED,
    ORDERS_CANCELLED,
    ORDERS_CREATED,
)
from services.inventory.service import InventoryService


def created_event(order_id: str, sku: str, quantity: int) -> Event:
    return Event(
        ORDERS_CREATED,
        {
            "order_id": order_id,
            "sku": sku,
            "quantity": quantity,
            "customer": "alice",
            "status": "created",
        },
    )


class InventoryServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = InMemoryBus()
        self.service = InventoryService(self.bus, {"sku-widget": 5})

    def test_reserves_stock_on_order_created(self) -> None:
        self.bus.publish(created_event("ord-1", "sku-widget", 2))

        self.assertEqual(self.service.available("sku-widget"), 3)
        self.assertEqual(self.service.reserved("ord-1"), 2)
        self.assertEqual(self.bus.published[-1].topic, INVENTORY_RESERVED)
        self.assertEqual(self.bus.published[-1].payload["remaining"], 3)

    def test_rejects_order_when_stock_is_insufficient(self) -> None:
        self.bus.publish(created_event("ord-2", "sku-widget", 9))

        self.assertEqual(self.service.available("sku-widget"), 5)
        self.assertEqual(self.service.reserved("ord-2"), 0)
        self.assertEqual(self.bus.published[-1].topic, INVENTORY_REJECTED)
        self.assertEqual(self.bus.published[-1].payload["reason"], "insufficient_stock")

    def test_cancel_releases_reserved_stock(self) -> None:
        event = created_event("ord-3", "sku-widget", 4)
        self.bus.publish(event)
        self.assertEqual(self.service.available("sku-widget"), 1)

        self.bus.publish(Event(ORDERS_CANCELLED, dict(event.payload)))

        self.assertEqual(self.service.available("sku-widget"), 5)
        self.assertEqual(self.service.reserved("ord-3"), 0)

    def test_unknown_sku_is_rejected(self) -> None:
        self.bus.publish(created_event("ord-4", "sku-missing", 1))

        self.assertEqual(self.bus.published[-1].topic, INVENTORY_REJECTED)
        self.assertEqual(self.bus.published[-1].payload["available"], 0)


if __name__ == "__main__":
    unittest.main()
