#!/usr/bin/env python3
"""Compare benchmark JSON reports against bench/budgets.json.

Prints one PASS/OVER/NO-BUDGET line per service case and exits non-zero when
any case is missing a budget or exceeds its median ceiling.

Usage:
    python3 bench/check_budgets.py --budgets bench/budgets.json \
        /tmp/perf-bench-186dfe/suite/*.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compare benchmark medians with budgets")
    parser.add_argument("--budgets", default="bench/budgets.json")
    parser.add_argument("results", nargs="+", help="benchmark JSON report(s)")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    budgets = load_json(Path(args.budgets)).get("services", {})
    problems = 0
    for name in args.results:
        report_path = Path(name)
        report = load_json(report_path)
        service = report.get("service", report_path.stem)
        ceilings = budgets.get(service)
        if not ceilings:
            print(f"NO-BUDGET {service}: no entry in {args.budgets}")
            problems += 1
            continue
        for case_name, stats in sorted(report.get("cases", {}).items()):
            ceiling = ceilings.get(case_name)
            if ceiling is None:
                print(f"NO-BUDGET {service}/{case_name}: no entry in {args.budgets}")
                problems += 1
                continue
            median_ms = stats["median_ms"]
            status = "PASS" if median_ms <= ceiling else "OVER"
            if status == "OVER":
                problems += 1
            print(f"{status} {service}/{case_name}: median={median_ms:.3f} ms budget={ceiling} ms")
    print(f"budget check: {problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
