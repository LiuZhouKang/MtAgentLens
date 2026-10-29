"""Catalog service layer (backend module).

Reads the shared sample catalog from ``data/catalog.json`` and exposes the two
read-only operations the product page needs: a full listing and a search. The
behaviour is fixed by ``contracts/catalog_fields.json``.
"""
import json
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "catalog.json"


def load_items():
    """Return every catalog item in file order."""
    with DATA_FILE.open(encoding="utf-8") as handle:
        return json.load(handle)


def list_items():
    """Return every item sorted by name, case-insensitive."""
    return sorted(load_items(), key=lambda item: item["name"].lower())


def search_items(query):
    """Return the items matching ``query``."""
    # TODO(api-handoff): the contract requires a case-insensitive substring
    # match over name AND description, and an empty query must return the full
    # catalog sorted by name.
    return [item for item in load_items() if query in item["name"]]
