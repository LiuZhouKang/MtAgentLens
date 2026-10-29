# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Select one valid run per scenario from a campaign's attempt ledgers.

Protocol (fixed before looking at verdicts): a run is valid when it completed,
produced a canonical verdict, and its capture quality is `healthy`. The first
valid attempt in chronological batch order wins; invalid attempts are recorded,
never silently dropped, so the RC can show exactly which runs were retried.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as error:
            raise ValueError(f"{path}:{number}: invalid JSON: {error}") from None
    return rows


def is_valid(row: dict) -> bool:
    return (
        row.get("state") == "completed"
        and row.get("capture_quality_status") == "healthy"
        and bool(row.get("final_decision"))
        and bool(row.get("db_path"))
    )


def select(results: list[Path]) -> tuple[list[dict], list[dict]]:
    chosen: dict[str, dict] = {}
    void: list[dict] = []
    for path in results:
        for row in load(path):
            scenario = row.get("scenario_id")
            if not scenario:
                continue
            if is_valid(row) and scenario not in chosen:
                chosen[scenario] = {**row, "source_ledger": str(path)}
                continue
            if scenario in chosen:
                continue
            void.append({
                "scenario_id": scenario,
                "attempt_id": row.get("attempt_id"),
                "capture_quality_status": row.get("capture_quality_status"),
                "state": row.get("state"),
                "source_ledger": str(path),
            })
    return [chosen[key] for key in sorted(chosen)], void


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, action="append", required=True,
                        help="attempt ledger in chronological order (repeatable)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--void-out", type=Path)
    args = parser.parse_args(argv)
    selected, void = select(args.results)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected),
        encoding="utf-8",
    )
    if args.void_out:
        args.void_out.parent.mkdir(parents=True, exist_ok=True)
        args.void_out.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in void),
            encoding="utf-8",
        )
    print(json.dumps({"selected": len(selected), "void": len(void), "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
