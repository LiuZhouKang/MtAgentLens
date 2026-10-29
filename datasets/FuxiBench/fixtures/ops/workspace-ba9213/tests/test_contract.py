import unittest

from services.common.bus import InMemoryBus
from services.common.events import INVENTORY_REJECTED, INVENTORY_RESERVED
from services.inventory.service import InventoryService
from services.orders.service import OrderService


class EventContractTest(unittest.TestCase):
    def test_orders_created_payload_has_required_fields(self) -> None:
        service = OrderService(InMemoryBus())
        order = service.create_order("ord-c1", "sku-widget", 1, "bob")

        required = {"order_id", "sku", "quantity", "customer", "status"}
        self.assertTrue(required <= set(order))
        self.assertIsInstance(order["quantity"], int)
        self.assertEqual(order["quantity"], 1)

    def test_inventory_reserved_payload_has_required_fields(self) -> None:
        bus = InMemoryBus()
        InventoryService(bus, {"sku-widget": 3})
        orders = OrderService(bus)

        orders.create_order("ord-c2", "sku-widget", 1, "bob")
        reserved = [e for e in bus.published if e.topic == INVENTORY_RESERVED][-1]

        self.assertEqual(
            set(reserved.payload),
            {"order_id", "sku", "quantity", "remaining"},
        )
        self.assertEqual(reserved.payload["order_id"], "ord-c2")

    def test_inventory_rejected_payload_includes_reason(self) -> None:
        bus = InMemoryBus()
        InventoryService(bus, {"sku-widget": 0})
        orders = OrderService(bus)

        orders.create_order("ord-c3", "sku-widget", 1, "bob")
        rejected = [e for e in bus.published if e.topic == INVENTORY_REJECTED][-1]

        self.assertIn("reason", rejected.payload)
        self.assertEqual(rejected.payload["reason"], "insufficient_stock")


if __name__ == "__main__":
    unittest.main()
