"""Versioned event contracts shared by the orders and inventory services."""

from dataclasses import dataclass
from typing import Any


ORDERS_CREATED = "orders.created"
ORDERS_CANCELLED = "orders.cancelled"
INVENTORY_RESERVED = "inventory.reserved"
INVENTORY_REJECTED = "inventory.rejected"


@dataclass(frozen=True)
class Event:
    """A single message on the platform bus."""

    topic: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.topic:
            raise ValueError("event topic must not be empty")
        if not isinstance(self.payload, dict):
            raise TypeError("event payload must be a dict")
