"""Weighted ballot tallying for the council certification project."""
from __future__ import annotations

import json
import sys
from pathlib import Path

WEIGHTS = {"chair": 3, "analyst": 1, "operator": 1, "auditor": 1}
THRESHOLD = 4


def load_ballots(directory: str | Path) -> list[dict]:
    """Load every seat ballot from ``directory`` (receipts are not ballots)."""
    ballots: list[dict] = []
    for path in sorted(Path(directory).glob("*.json")):
        if path.name == "receipts.json":
            continue
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(entry, dict) and entry.get("seat"):
            ballots.append(entry)
    return ballots


def seat_weight(ballot: dict) -> int:
    """Weight of a seat, falling back to the weight written on the ballot."""
    return WEIGHTS.get(ballot.get("seat", ""), int(ballot.get("weight", 1)))


def weighted_tally(ballots: list[dict]) -> dict:
    """Preliminary weighted tally over the submitted ``vote`` field."""
    approve = sum(seat_weight(b) for b in ballots if b.get("vote") == "approve")
    total = sum(seat_weight(b) for b in ballots)
    return {
        "approve_weight": approve,
        "total_weight": total,
        "threshold": THRESHOLD,
        "approved": approve >= THRESHOLD,
    }


def main(argv: list[str]) -> int:
    directory = argv[1] if len(argv) > 1 else "ballots"
    result = weighted_tally(load_ballots(directory))
    verdict = "approved" if result["approved"] else "rejected"
    print(
        f"preliminary tally: {result['approve_weight']}/{result['total_weight']} "
        f"approve (threshold {result['threshold']}) -> {verdict}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
