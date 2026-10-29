"""Rendering of ledger aggregates as CSV or fixed-width tables."""

__version__ = "2.7.3"

from .renderer import render_csv, render_table

__all__ = ["render_csv", "render_table", "__version__"]
