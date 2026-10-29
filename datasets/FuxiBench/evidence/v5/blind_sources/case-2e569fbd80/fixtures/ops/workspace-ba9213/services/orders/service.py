"""Order intake and cancellation service."""

from services.common.bus import InMemoryBus
from services.common.events import Event, ORDERS_CANCELLED, ORDERS_CREATED


class OrderService:
    def __init__(self, bus: InMemoryBus) -> None:
        self._bus = bus
        self._orders: dict[str, dict] = {}

    def create_order(self, order_id: str, sku: str, quantity: int, customer: str) -> dict:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if not sku:
            raise ValueError("sku is required")
        if not order_id:
            raise ValueError("order_id is required")
        if order_id in self._orders:
            raise ValueError(f"order {order_id} already exists")

        order = {
            "order_id": order_id,
            "sku": sku,
            "quantity": quantity,
            "customer": customer,
            "status": "created",
        }
        self._orders[order_id] = order
        self._bus.publish(Event(ORDERS_CREATED, dict(order)))
        return dict(order)

    def cancel_order(self, order_id: str) -> dict:
        order = self._orders[order_id]
        if order["status"] == "cancelled":
            return dict(order)

        order["status"] = "cancelled"
        self._bus.publish(Event(ORDERS_CANCELLED, dict(order)))
        return dict(order)

    def get(self, order_id: str) -> dict:
        return dict(self._orders[order_id])
