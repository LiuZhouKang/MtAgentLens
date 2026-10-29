"""Contract conformance checker for catalog-desk.

Validates that the shared field contract, the sample catalog data and the public
surface of both modules agree. It deliberately checks *shape and surface*, not
the search semantics that the backend suite owns.

Run directly (``python3 integration/check_contract.py``) to print a JSON summary
and exit non-zero when any check fails.
"""
import json
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_FILE = ROOT / "contracts" / "catalog_fields.json"
DATA_FILE = ROOT / "data" / "catalog.json"

# Running this file directly puts integration/ on sys.path, not the project
# root, so the frontend and api packages would not be importable.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_CONTRACT_KEYS = (
    "version",
    "card_fields",
    "price_unavailable_label",
    "item_fields",
    "rules",
)


def collect():
    """Run every contract check and return a JSON-ready summary."""
    contract = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
    items = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    checks = []

    def record(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    missing = [key for key in REQUIRED_CONTRACT_KEYS if key not in contract]
    record("contract_required_keys", not missing, "missing: " + ", ".join(missing) if missing else "")

    from frontend.catalog_card import CARD_FIELDS, PRICE_UNAVAILABLE, render_card

    card_fields = contract.get("card_fields", [])
    record(
        "frontend_card_fields_match_contract",
        tuple(card_fields) == tuple(CARD_FIELDS),
        f"contract={card_fields} frontend={list(CARD_FIELDS)}",
    )
    record(
        "frontend_price_label_matches_contract",
        contract.get("price_unavailable_label") == PRICE_UNAVAILABLE,
        f"contract={contract.get('price_unavailable_label')!r} frontend={PRICE_UNAVAILABLE!r}",
    )

    item_fields = contract.get("item_fields", [])
    incomplete = [
        item.get("sku", "?")
        for item in items
        if any(field not in item for field in item_fields)
    ]
    record("data_item_fields_complete", not incomplete, "incomplete: " + ", ".join(incomplete) if incomplete else "")

    skus = [item.get("sku") for item in items]
    record("data_sku_unique", len(skus) == len(set(skus)), f"{len(skus)} items")

    bad_pairing = [
        item.get("sku", "?")
        for item in items
        if (item.get("price") is None) != (item.get("currency") is None)
    ]
    record(
        "data_price_currency_pairing",
        not bad_pairing,
        "price and currency must both be set or both be null; offenders: " + ", ".join(bad_pairing)
        if bad_pairing
        else "",
    )

    bad_stock = [
        item.get("sku", "?")
        for item in items
        if not isinstance(item.get("stock"), int) or item["stock"] < 0
    ]
    record("data_stock_non_negative_int", not bad_stock, "offenders: " + ", ".join(bad_stock) if bad_stock else "")

    from api.catalog_api import list_items

    listed = list_items()
    listed_names = [item.get("name", "") for item in listed]
    record(
        "api_list_items_complete_and_sorted",
        len(listed) == len(items) and listed_names == sorted(listed_names, key=str.lower),
        f"{len(listed)} of {len(items)} items",
    )

    unrendered = []
    for item in items:
        try:
            html = render_card(item)
        except Exception as exc:  # rendering must not raise for any catalog item
            unrendered.append(f"{item.get('sku', '?')}: {type(exc).__name__}: {exc}")
            continue
        if escape(item.get("name", "")) not in html:
            unrendered.append(f"{item.get('sku', '?')}: name missing from card")
    record("frontend_renders_every_item", not unrendered, "; ".join(unrendered))

    return {
        "contract_version": contract.get("version"),
        "card_fields": card_fields,
        "items": len(items),
        "checks": checks,
        "ok": all(check["ok"] for check in checks),
    }


def main():
    summary = collect()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
