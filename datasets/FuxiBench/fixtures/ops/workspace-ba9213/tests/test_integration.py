import unittest

from services.common.bus import InMemoryBus
from services.inventory.service import InventoryService
from services.orders.service import OrderService


def wire(stock: dict[str, int]):
    bus = InMemoryBus()
    inventory = InventoryService(bus, stock)
    orders = OrderService(bus)
    return bus, orders, inventory


class OrderInventoryIntegrationTest(unittest.TestCase):
    def test_order_flow_reserves_inventory(self) -> None:
        bus, orders, inventory = wire({"sku-widget": 10})

        orders.create_order("ord-i1", "sku-widget", 3, "carol")

        self.assertEqual(inventory.available("sku-widget"), 7)
        self.assertEqual(
            [event.topic for event in bus.published],
            ["orders.created", "inventory.reserved"],
        )

    def test_rejected_order_leaves_stock_unchanged(self) -> None:
        bus, orders, inventory = wire({"sku-widget": 1})

        orders.create_order("ord-i2", "sku-widget", 2, "carol")

        self.assertEqual(inventory.available("sku-widget"), 1)
        self.assertEqual(bus.published[-1].topic, "inventory.rejected")

    def test_cancel_after_reservation_restores_stock(self) -> None:
        bus, orders, inventory = wire({"sku-widget": 2})

        orders.create_order("ord-i3", "sku-widget", 2, "carol")
        self.assertEqual(inventory.available("sku-widget"), 0)

        orders.cancel_order("ord-i3")

        self.assertEqual(inventory.available("sku-widget"), 2)
        self.assertEqual(bus.published[-1].topic, "orders.cancelled")


if __name__ == "__main__":
    unittest.main()
