"""End-to-end wiring tests for catalog-desk.

Exercises the path the product page takes: the backend listing feeds the
frontend card renderer, and the shared contract holds across both modules.
"""
import unittest
from html import escape

from api.catalog_api import list_items, search_items
from frontend.catalog_card import render_card
from integration.check_contract import collect


class EndToEndTests(unittest.TestCase):
    def test_contract_checker_passes(self):
        summary = collect()
        failed = [check for check in summary["checks"] if not check["ok"]]
        self.assertTrue(summary["ok"], failed)

    def test_every_item_renders_as_a_card(self):
        for item in list_items():
            html = render_card(item)
            self.assertIn('class="catalog-card"', html)
            self.assertIn(escape(item["name"]), html)
            self.assertIn("In stock:", html)

    def test_listing_matches_the_data_file(self):
        self.assertEqual(len(list_items()), 6)

    def test_search_returns_a_list(self):
        self.assertIsInstance(search_items(""), list)


if __name__ == "__main__":
    unittest.main()
