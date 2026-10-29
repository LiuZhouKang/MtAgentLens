"""Warehouse allocation for the inventory-service fixture."""
from __future__ import annotations


def allocate(
    stock_by_warehouse: dict[str, int],
    requested: int,
    priority: list[str] | None = None,
) -> dict[str, int]:
    """Allocate ``requested`` units across warehouses in priority order.

    Raises ``ValueError`` when the request is negative or cannot be filled.
    """
    if requested < 0:
        raise ValueError("requested quantity must be non-negative")

    order = priority if priority is not None else sorted(stock_by_warehouse)
    remaining = requested
    allocation: dict[str, int] = {}

    for warehouse in order:
        available = max(int(stock_by_warehouse.get(warehouse, 0)), 0)
        take = min(available, remaining)
        if take:
            allocation[warehouse] = take
            remaining -= take
        if remaining == 0:
            break

    if remaining:
        raise ValueError(f"insufficient stock: {remaining} units unallocated")
    return allocation
