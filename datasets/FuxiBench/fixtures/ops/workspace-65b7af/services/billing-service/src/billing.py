"""Invoice arithmetic for the billing-service fixture."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def to_cents(amount) -> Decimal:
    """Round an amount to the cent boundary, half-up."""
    return Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_UP)


def line_total(unit_price, quantity: int) -> Decimal:
    if quantity < 0:
        raise ValueError("quantity must be non-negative")
    return to_cents(Decimal(str(unit_price)) * quantity)


def invoice_total(lines: list[tuple[object, int]]) -> Decimal:
    total = Decimal("0.00")
    for unit_price, quantity in lines:
        total += line_total(unit_price, quantity)
    return to_cents(total)


def prorate(monthly_total, days_used: int, days_in_month: int) -> Decimal:
    if days_in_month <= 0:
        raise ValueError("days_in_month must be positive")
    if not 0 <= days_used <= days_in_month:
        raise ValueError("days_used must fall inside the billing month")
    share = Decimal(str(monthly_total)) * Decimal(days_used) / Decimal(days_in_month)
    return to_cents(share)
