"""Tests for the catalog card renderer.

These tests encode the shared contract in contracts/catalog_fields.json. They
are expected to fail until the frontend stage (docs/handoff/01-frontend.md) is
complete; do not edit them to make them pass.
"""
import unittest

from frontend.catalog_card import CARD_FIELDS, format_price, render_card

LAMP = {
    "sku": "LMP-001",
    "name": "Nimbus & Co. Lamp",
    "description": "Warm-white desk lamp with a brass arm.",
    "price": 59.0,
    "currency": "USD",
    "stock": 12,
}

RUG = {
    "sku": "RUG-058",
    "name": "Orbit Wool Rug",
    "description": "Hand-tufted rug, 2 x 3 m, undyed wool.",
    "price": None,
    "currency": None,
    "stock": 3,
}


class FormatPriceTests(unittest.TestCase):
    def test_includes_currency_code(self):
        self.assertEqual(format_price(LAMP), "59.00 USD")

    def test_missing_price_uses_contract_label(self):
        self.assertEqual(format_price(RUG), "Price unavailable")


class RenderCardTests(unittest.TestCase):
    def test_uses_contract_field_order(self):
        html = render_card(LAMP)
        tokens = ["Nimbus", "59.00", "USD", "In stock"]
        for token in tokens:
            self.assertIn(token, html)
        positions = [html.index(token) for token in tokens]
        self.assertEqual(positions, sorted(positions))

    def test_escapes_html_in_name(self):
        html = render_card(LAMP)
        self.assertIn("Nimbus &amp; Co. Lamp", html)
        self.assertNotIn("Nimbus & Co. Lamp", html)

    def test_missing_price_card_shows_contract_label(self):
        self.assertIn("Price unavailable", render_card(RUG))

    def test_card_keeps_stock_line(self):
        self.assertIn("In stock: 12", render_card(LAMP))


class ContractFieldsTests(unittest.TestCase):
    def test_card_fields_match_contract_order(self):
        self.assertEqual(CARD_FIELDS, ("name", "price", "currency", "stock"))


if __name__ == "__main__":
    unittest.main()
