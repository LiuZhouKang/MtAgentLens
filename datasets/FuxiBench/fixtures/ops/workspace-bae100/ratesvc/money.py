"""Monetary conversion and formatting helpers for the rate-pricing pipeline."""

from __future__ import annotations

CENTS = 2


def convert(amount, rate):
    """Convert ``amount`` using ``rate`` and round to cents."""
    return round(amount * rate, CENTS)


def total(amounts):
    """Sum the monetary ``amounts``."""
    return round(sum(amounts), CENTS)


def format_amount(value):
    """Render ``value`` with two decimal places."""
    return f"{value:.2f}"
