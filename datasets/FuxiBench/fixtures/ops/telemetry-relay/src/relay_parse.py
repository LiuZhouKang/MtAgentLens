"""Minimal parser for relay-agent text logs."""


def parse_line(line):
    """Return a structured event, or None for blank/short lines."""
    parts = line.strip().split()
    if len(parts) < 4:
        return None
    return {
        "ts": parts[0],
        "host": parts[1],
        "tag": parts[2],
        "detail": " ".join(parts[3:]),
    }


def parse_file(path):
    events = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            event = parse_line(raw)
            if event is not None:
                events.append(event)
    return events
