#!/usr/bin/env python3
"""Stage 3: run the unittest suite and record machine-readable results."""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    out = args.out_dir
    log_dir = out / "test"
    log_dir.mkdir(parents=True, exist_ok=True)

    suite = unittest.TestLoader().discover(str(ROOT / "tests"), pattern="test_*.py")
    stream = io.StringIO()
    started = time.monotonic()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    duration = round(time.monotonic() - started, 3)

    tests = result.testsRun
    failed = len(result.failures) + len(result.errors)
    report = {
        "suite": "releasekit",
        "tests": tests,
        "passed": tests - failed - len(result.skipped),
        "failed": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "duration_s": duration,
        "status": "pass" if failed == 0 and tests > 0 else "fail",
    }

    (log_dir / "test.log").write_text(stream.getvalue(), encoding="utf-8")
    (log_dir / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(stream.getvalue(), end="")

    if report["status"] != "pass":
        print(
            f"test: FAILED ({report['failed']} failures, {report['errors']} errors "
            f"of {tests} tests)"
        )
        return 1

    status_dir = out / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "test").write_text("PASSED\n", encoding="utf-8")
    print(f"test: PASSED ({report['passed']}/{tests} in {duration}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
