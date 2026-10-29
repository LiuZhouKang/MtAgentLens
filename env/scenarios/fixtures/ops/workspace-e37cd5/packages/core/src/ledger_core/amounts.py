"""Monetary amounts.

Amounts are stored as integer minor units (cents) so that arithmetic stays
exact. Parsing accepts an optional leading sign, thousands separators and at
most two decimal places.
"""

from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_CURRENCIES = ("EUR", "GBP", "JPY", "USD")


class AmountError(ValueError):
    """Raised when an amount cannot be parsed or combined."""


@dataclass(frozen=True)
class Money:
    """An exact monetary amount in minor units of one currency."""

    minor: int
    currency: str = "EUR"

    def __post_init__(self) -> None:
        if self.currency not in SUPPORTED_CURRENCIES:
            raise AmountError(f"unsupported currency: {self.currency}")

    def __add__(self, other: "Money") -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        if other.currency != self.currency:
            raise AmountError(
                f"cannot add {self.currency} and {other.currency}"
            )
        return Money(self.minor + other.minor, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.minor, self.currency)

    def format(self) -> str:
        sign = "-" if self.minor < 0 else ""
        whole, cents = divmod(abs(self.minor), 100)
        return f"{sign}{whole}.{cents:02d} {self.currency}"


def parse_amount(text: str, currency: str = "EUR") -> Money:
    """Parse ``text`` into :class:`Money`.

    Accepts ``1.50``, ``-0.75``, ``+2`` and ``1,234.50``. Rejects more than two
    decimal places, empty input and anything that is not a plain decimal.
    """
    cleaned = text.strip().replace(",", "")
    if not cleaned:
        raise AmountError("empty amount")
    sign = 1
    if cleaned[0] in "+-":
        if cleaned[0] == "-":
            sign = -1
        cleaned = cleaned[1:]
    if cleaned.count(".") > 1:
        raise AmountError(f"malformed amount: {text}")
    whole, dot, fraction = cleaned.partition(".")
    if not whole.isdigit() or (dot and not fraction.isdigit()):
        raise AmountError(f"malformed amount: {text}")
    if len(fraction) > 2:
        raise AmountError(f"more than two decimal places: {text}")
    minor = int(whole) * 100 + int(fraction.ljust(2, "0") or "0")
    return Money(sign * minor, currency)
