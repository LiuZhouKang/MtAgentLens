"""Catalog card rendering for the storefront (frontend module).

The card must follow ``contracts/catalog_fields.json``: fields appear in the
contract order, the price is rendered with its currency code, and items without
a price use the contract label instead of a number.
"""
from html import escape

#: Card fields in contract order (see contracts/catalog_fields.json).
CARD_FIELDS = ("name", "price", "currency", "stock")

#: Label used by the contract when an item has no price.
PRICE_UNAVAILABLE = "Price unavailable"


def format_price(item):
    """Return the display string for one item's price."""
    # TODO(frontend-handoff): the contract requires the currency code next to
    # the amount and PRICE_UNAVAILABLE for items without a price.
    price = item.get("price")
    if price is None:
        return "n/a"
    return f"{price:.2f}"


def render_card(item):
    """Render one catalog item as a small HTML card."""
    name = escape(item.get("name", ""))
    stock = item.get("stock", 0)
    lines = [
        '<div class="catalog-card">',
        f'  <h3>{name}</h3>',
        f'  <p class="price">{escape(format_price(item))}</p>',
        f'  <p class="stock">In stock: {escape(str(stock))}</p>',
        "</div>",
    ]
    return "\n".join(lines)
