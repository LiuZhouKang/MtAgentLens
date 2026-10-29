#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Validate a measurement campaign: one binary, one corpus, hashed inputs.

Reads `results.jsonl` from an FPR campaign and:
    * checks every run used the same collector SHA;
    * records the scenario JSON and database SHA-256 for each run;
    * recomputes the actionable rate with a Wilson interval.

Usage:
    python3 scripts/wire/verify_campaign.py <results.jsonl> [--out summary.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
from scripts.evaluation.stats import wilson_interval  # noqa: E402


def sha256_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def readable_copy(path: Path) -> Path | None:
    if os.access(path, os.R_OK):
        return path
    password = os.environ.get("MTAGENTLENS_SUDO_PASSWORD", "")
    tmp = Path(tempfile.mkdtemp(prefix="mtl-campaign-db-")) / path.name
    proc = subprocess.run(
        ["sudo", "-S", "-p", "", "cp", str(path), str(tmp)],
        input=password + "\n",
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    subprocess.run(
        ["sudo", "-S", "-p", "", "chown", f"{os.getuid()}:{os.getgid()}", str(tmp)],
        input=password + "\n",
        text=True,
        capture_output=True,
    )
    return tmp


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    rows = [
        json.loads(line)
        for line in args.results.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    binaries = {row.get("collector_sha256") for row in rows if row.get("collector_sha256")}
    problems: list[str] = []
    if len(binaries) > 1:
        problems.append(f"mixed collector binaries: {sorted(binaries)}")

    enriched = []
    for row in rows:
        scenario_id = row.get("scenario_id")
        scenario_path = PROJECT_ROOT / "env" / "scenarios" / "benign" / f"{scenario_id}.json"
        if not scenario_path.exists():
            matches = sorted(
                (PROJECT_ROOT / "env" / "scenarios").rglob(f"{scenario_id}.json")
            )
            scenario_path = matches[0] if matches else scenario_path
        db_path = Path(row["db_path"]) if row.get("db_path") else None
        db_sha = None
        if db_path is not None:
            readable = readable_copy(db_path)
            db_sha = sha256_file(readable) if readable else None
        record = {
            **row,
            "scenario_path": str(scenario_path) if scenario_path.exists() else None,
            "scenario_sha256": sha256_file(scenario_path),
            "db_sha256": db_sha,
        }
        if not record["scenario_sha256"]:
            problems.append(f"{scenario_id}: scenario JSON missing")
        if not record["db_sha256"]:
            problems.append(f"{scenario_id}: database missing or unreadable")
        enriched.append(record)

    observed = [row for row in enriched if row.get("observed")]
    actionable = [
        row
        for row in observed
        if (row.get("alerts_by_decision") or {}).get("Unsafe", 0) > 0
    ]
    n = len(observed)
    k = len(actionable)
    low, high = wilson_interval(k, n) if n else (0.0, 1.0)
    summary = {
        "binary_sha256": sorted(binaries)[0] if len(binaries) == 1 else None,
        "runs": len(enriched),
        "observed": n,
        "actionable_runs": k,
        "actionable_rate": k / n if n else None,
        "wilson_95": [low, high],
        "problems": problems,
        "runs_detail": enriched,
    }
    print(
        json.dumps(
            {key: value for key, value in summary.items() if key != "runs_detail"},
            indent=2,
        )
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.out}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
