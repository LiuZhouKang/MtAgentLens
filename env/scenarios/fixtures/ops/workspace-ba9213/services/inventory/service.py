"""Inventory reservation service."""

from services.common.bus import InMemoryBus
from services.common.events import (
    Event,
    INVENTORY_REJECTED,
    INVENTORY_RESERVED,
    ORDERS_CANCELLED,
    ORDERS_CREATED,
)


class InventoryService:
    def __init__(self, bus: InMemoryBus, stock: dict[str, int] | None = None) -> None:
        self._bus = bus
        self._stock = dict(stock or {})
        self._reservations: dict[str, int] = {}
        bus.subscribe(ORDERS_CREATED, self._on_order_created)
        bus.subscribe(ORDERS_CANCELLED, self._on_order_cancelled)

    def available(self, sku: str) -> int:
        return self._stock.get(sku, 0)

    def reserved(self, order_id: str) -> int:
        return self._reservations.get(order_id, 0)

    def _on_order_created(self, event: Event) -> None:
        order_id = event.payload["order_id"]
        sku = event.payload["sku"]
        quantity = event.payload["quantity"]
        available = self._stock.get(sku, 0)

        if available < quantity:
            self._bus.publish(
                Event(
                    INVENTORY_REJECTED,
                    {
                        "order_id": order_id,
                        "sku": sku,
                        "quantity": quantity,
                        "reason": "insufficient_stock",
                        "available": available,
                    },
                )
            )
            return

        self._stock[sku] = available - quantity
        self._reservations[order_id] = quantity
        self._bus.publish(
            Event(
                INVENTORY_RESERVED,
                {
                    "order_id": order_id,
                    "sku": sku,
                    "quantity": quantity,
                    "remaining": self._stock[sku],
                },
            )
        )

    def _on_order_cancelled(self, event: Event) -> None:
        order_id = event.payload["order_id"]
        quantity = self._reservations.pop(order_id, None)
        if quantity is None:
            return

        sku = event.payload["sku"]
        self._stock[sku] = self._stock.get(sku, 0) + quantity
