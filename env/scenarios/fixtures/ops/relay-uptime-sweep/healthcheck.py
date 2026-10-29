"""Relay health-check helpers used by the local smoke harness."""

import time
import urllib.request


def load_endpoints(path):
    """Parse endpoints.txt into a list of {url, label} records."""
    endpoints = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            endpoints.append(
                {"url": parts[0], "label": parts[1] if len(parts) > 1 else "relay"}
            )
    return endpoints


def probe(url, timeout=5):
    """Return (http_status, latency_ms); status 0 means connection failure."""
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            status = response.status
    except Exception:
        status = 0
    latency_ms = round((time.monotonic() - started) * 1000, 1)
    return status, latency_ms


def summarize(rounds):
    """Aggregate per-URL availability and average latency over completed rounds."""
    totals = {}
    for entry in rounds:
        bucket = totals.setdefault(entry["url"], {"ok": 0, "total": 0, "latency": []})
        bucket["total"] += 1
        if entry["http_status"]:
            bucket["ok"] += 1
            bucket["latency"].append(entry["latency_ms"])
    summary = {}
    for url, bucket in totals.items():
        average = sum(bucket["latency"]) / len(bucket["latency"]) if bucket["latency"] else 0.0
        summary[url] = {
            "availability_pct": round(100.0 * bucket["ok"] / bucket["total"], 1),
            "avg_latency_ms": round(average, 1),
        }
    return summary
