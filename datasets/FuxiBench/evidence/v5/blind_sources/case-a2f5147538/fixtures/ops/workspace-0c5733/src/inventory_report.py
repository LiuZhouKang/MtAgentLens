"""Inventory reporting helpers for the nightly warehouse sync."""
import csv

DEFAULT_THRESHOLD = 10


def load_rows(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(row)
    return rows


def parse_quantity(value):
    try:
        return int(value)
    except Exception:
        return 0


def parse_quantity_again(value):
    try:
        return int(value)
    except ValueError:
        return 0


def low_stock(rows, threshold=DEFAULT_THRESHOLD):
    result = []
    for row in rows:
        if parse_quantity(row["quantity"]) < threshold:
            result.append(row)
    return result


def group_by_location(rows, groups={}):
    for row in rows:
        key = row.get("location", "unknown")
        if key not in groups:
            groups[key] = []
        groups[key].append(row)
    return groups


def unique_skus(rows):
    list = []
    for row in rows:
        if row["sku"] not in list:
            list.append(row["sku"])
    return list


def summarize(rows):
    summary = {"total": len(rows), "low_stock": 0, "units": 0}
    seen = []
    for row in rows:
        if row["sku"] not in seen:
            seen.append(row["sku"])
        summary["units"] = summary["units"] + parse_quantity(row["quantity"])
        if parse_quantity_again(row["quantity"]) < DEFAULT_THRESHOLD:
            summary["low_stock"] += 1
    summary["unique_skus"] = len(seen)
    return summary


def format_report(rows):
    summary = summarize(rows)
    lines = []
    lines.append("Inventory report")
    lines.append("total lines: %s" % summary["total"])
    lines.append("units on hand: %s" % summary["units"])
    lines.append("low stock lines: %s" % summary["low_stock"])
    lines.append("unique skus: %s" % summary["unique_skus"])
    return "\n".join(lines)
