"""Pricing-service workload: price synthetic carts with tier discounts and tax.

All money is integer cents; there is no floating point in the pricing path.
The 100 carts are generated deterministically at import time.
"""

from __future__ import annotations

TIERS = ("bronze", "silver", "gold")
DISCOUNT_BASIS_POINTS = {"bronze": 0, "silver": 500, "gold": 1000}
TAX_BASIS_POINTS = 825


def make_cart(seed):
    return {
        "cart_id": f"cart-{seed:03d}",
        "tier": TIERS[seed % len(TIERS)],
        "items": [
            {
                "sku": f"sku-{seed:03d}-{line:02d}",
                "unit_cents": 250 + (seed * 13 + line * 37) % 5000,
                "qty": 1 + (seed + line) % 4,
            }
            for line in range(12)
        ],
    }


CARTS = [make_cart(seed) for seed in range(100)]


def subtotal(cart):
    return sum(item["unit_cents"] * item["qty"] for item in cart["items"])


def discount(cart):
    return subtotal(cart) * DISCOUNT_BASIS_POINTS[cart["tier"]] // 10_000


def total(cart):
    net = subtotal(cart) - discount(cart)
    return net + net * TAX_BASIS_POINTS // 10_000


def price_all(carts):
    return [
        {
            "cart_id": cart["cart_id"],
            "tier": cart["tier"],
            "subtotal_cents": subtotal(cart),
            "discount_cents": discount(cart),
            "total_cents": total(cart),
        }
        for cart in carts
    ]


def summarize_by_tier(priced):
    summary = {tier: {"carts": 0, "total_cents": 0} for tier in TIERS}
    for entry in priced:
        bucket = summary[entry["tier"]]
        bucket["carts"] += 1
        bucket["total_cents"] += entry["total_cents"]
    return summary


def check():
    cart = {
        "cart_id": "cart-test",
        "tier": "gold",
        "items": [{"sku": "a", "unit_cents": 1000, "qty": 2}],
    }
    if subtotal(cart) != 2000:
        raise AssertionError("subtotal mismatch")
    if discount(cart) != 200:
        raise AssertionError("10% gold discount mismatch")
    if total(cart) != 1948:
        raise AssertionError("total with 8.25% tax mismatch")
    priced = price_all(CARTS)
    if len(priced) != len(CARTS):
        raise AssertionError("price_all dropped carts")
    summary = summarize_by_tier(priced)
    if sum(bucket["carts"] for bucket in summary.values()) != len(CARTS):
        raise AssertionError("tier summary lost carts")
    if sum(bucket["total_cents"] for bucket in summary.values()) != sum(
        entry["total_cents"] for entry in priced
    ):
        raise AssertionError("tier totals do not reconcile")
    return True


CASES = {
    "price_single_cart_1000x": lambda: [total(CARTS[0]) for _ in range(1000)],
    "price_all_carts_100x": lambda: [price_all(CARTS) for _ in range(100)],
    "summarize_by_tier_100x": lambda: [summarize_by_tier(price_all(CARTS)) for _ in range(100)],
}
