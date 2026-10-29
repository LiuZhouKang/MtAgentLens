# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Attach canonical capture quality to already-measured campaign rows.

A runner row records what the benchmark observed; only the collector's frozen
analysis checkpoint knows whether the capture was loss-free. This pass derives
`capture_quality_status` and `evaluation_status` for every row from that
checkpoint and writes a derived ledger. The original ledger is never modified,
and a run whose quality cannot be read stays `unknown` rather than being
silently promoted into the conditional-rate denominator.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.wire.fpr_runner import observation_state  # noqa: E402

_DERIVED = ("capture_quality_status", "evaluation_status")


def _load(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{path}: malformed JSON on line {number}: {exc}") from None
        if not isinstance(row, dict) or not isinstance(row.get("scenario_id"), str):
            raise ValueError(f"{path}: line {number} is not a campaign row")
        rows.append(row)
    return rows


def enrich(rows: list[dict]) -> list[dict]:
    enriched = []
    for row in rows:
        updated = {key: value for key, value in row.items() if key not in _DERIVED}
        db_path = row.get("db_path")
        state = observation_state(Path(db_path) if db_path else None)
        updated.update({key: state[key] for key in _DERIVED})
        if updated.get("run_id") is None and state.get("run_id") is not None:
            updated["run_id"] = state["run_id"]
        if state.get("artifact_error"):
            updated["quality_error"] = state["artifact_error"]
        if updated.get("artifact_error") and updated.get("final_decision") is None:
            updated["observed"] = False
        enriched.append(updated)
    return enriched


def write_ledger(path: Path, rows: list[dict]) -> None:
    path = Path(path)
    if path.exists():
        raise ValueError("derived ledger already exists; refusing to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        stream.flush()
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        rows = _load(args.results)
        enriched = enrich(rows)
        write_ledger(args.out, enriched)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    counts = {}
    for row in enriched:
        status = row["capture_quality_status"]
        counts[status] = counts.get(status, 0) + 1
    print(json.dumps({"rows": len(enriched), "capture_quality": counts, "out": str(args.out)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
