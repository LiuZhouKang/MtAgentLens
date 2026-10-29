"""Warehouse feed reconciliation."""

from __future__ import annotations


def reconcile(feed: dict[str, int], ledger: dict[str, int]) -> dict[str, int]:
    """Return the per-SKU delta between the feed and the ledger."""
    return {
        sku: feed.get(sku, 0) - ledger.get(sku, 0)
        for sku in sorted(set(feed) | set(ledger))
    }
