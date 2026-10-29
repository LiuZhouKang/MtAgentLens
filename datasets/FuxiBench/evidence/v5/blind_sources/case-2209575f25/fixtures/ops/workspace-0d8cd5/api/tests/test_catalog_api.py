"""Tests for the catalog service layer.

These tests encode the search rules in contracts/catalog_fields.json. They are
expected to fail until the backend stage (docs/handoff/02-api.md) is complete;
do not edit them to make them pass.
"""
import unittest

from api.catalog_api import list_items, search_items


def names(items):
    return [item["name"] for item in items]


class ListItemsTests(unittest.TestCase):
    def test_returns_the_whole_catalog(self):
        self.assertEqual(len(list_items()), 6)

    def test_sorted_by_name_case_insensitive(self):
        listed = names(list_items())
        self.assertEqual(listed, sorted(listed, key=str.lower))


class SearchItemsTests(unittest.TestCase):
    def test_empty_query_returns_every_item_sorted(self):
        self.assertEqual(names(search_items("")), names(list_items()))

    def test_name_match_is_case_insensitive(self):
        self.assertEqual(
            names(search_items("LAMP")),
            ["Beacon Floor Lamp", "Nimbus & Co. Lamp"],
        )

    def test_matches_description_text(self):
        self.assertEqual(names(search_items("brass")), ["Nimbus & Co. Lamp"])

    def test_no_match_returns_empty_list(self):
        self.assertEqual(search_items("hammock"), [])


if __name__ == "__main__":
    unittest.main()
