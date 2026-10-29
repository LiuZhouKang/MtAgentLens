#!/usr/bin/env python3
"""Stage 6: cross-check artifacts and write the final build summary."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from releasekit.manifest import sha256_file

STAGES = (
    ("preflight", "Stage 1 (Preflight)"),
    ("build", "Stage 2 (Build)"),
    ("test", "Stage 3 (Test)"),
    ("quality", "Stage 4 (Quality)"),
    ("package", "Stage 5 (Package)"),
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args()

    out = args.out_dir
    statuses: dict[str, str] = {}
    missing: list[str] = []
    for key, _ in STAGES:
        status_file = out / "status" / key
        if not status_file.is_file():
            missing.append(key)
            continue
        statuses[key] = status_file.read_text(encoding="utf-8").strip()
    if missing:
        print(f"summary: FAILED (missing stage status: {', '.join(missing)})")
        return 2

    env = read_json(out / "preflight" / "env.json")
    manifest = read_json(out / "dist" / "build-manifest.json")
    results = read_json(out / "test" / "results.json")
    quality = read_json(out / "quality" / "report.json")

    checksum_file = out / "artifacts" / "checksums.txt"
    recorded = checksum_file.read_text(encoding="utf-8").split()
    recorded_hash = recorded[0] if recorded else ""
    package = out / "artifacts" / "release.tar.gz"
    if not package.is_file():
        print(f"summary: FAILED (missing {package})")
        return 2
    actual_hash = sha256_file(package)
    if recorded_hash != actual_hash:
        print("summary: FAILED (package checksum does not match checksums.txt)")
        return 2
    if results.get("status") != "pass" or results.get("tests", 0) <= 0:
        print("summary: FAILED (test results are not passing)")
        return 2
    if quality.get("status") != "pass":
        print("summary: FAILED (quality gate did not pass)")
        return 2

    tests = results["tests"]
    rows = [
        f"Stage 1 (Preflight): PASSED",
        f"Stage 2 (Build): PASSED ({manifest['file_count']} files, {manifest['total_bytes']} bytes)",
        f"Stage 3 (Test): PASSED ({results['passed']}/{tests})",
        f"Stage 4 (Quality): PASSED ({quality['checks_passed']}/{quality['checks_total']} checks)",
        f"Stage 5 (Package): PASSED (sha256 {actual_hash})",
        "Stage 6 (Summary): PASSED",
    ]
    lines = [
        "=== BUILD SUMMARY ===",
        f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"Project: releasekit {manifest['version']}",
        f"Builder: {env.get('builder')}",
        *rows,
        "Build successful!",
    ]

    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"summary: PASSED (written to {args.summary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
