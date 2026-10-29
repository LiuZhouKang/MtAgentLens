"""Rollup helpers for local developer activity counters."""


def rollup(events):
    """Sum ``count`` per ``kind``; events without a kind are ignored."""
    totals = {}
    for event in events:
        kind = event.get("kind")
        if not kind:
            continue
        totals[kind] = totals.get(kind, 0) + int(event.get("count", 1))
    return totals


def format_report(totals):
    """Render a stable, sorted ``kind: total`` report."""
    return "\n".join(f"{kind}: {value}" for kind, value in sorted(totals.items()))
