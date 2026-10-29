#!/usr/bin/env python3
"""Micro-benchmark harness for the storefront performance suite.

Each service directory contains a ``workload.py`` module exposing:

* ``CASES``: mapping of case name -> zero-argument callable
* ``check()``: correctness self-check that raises ``AssertionError`` on mismatch

The harness runs the correctness check, one warm-up pass per case, then
``--iterations`` timed samples, and writes a JSON report with per-case
min/median/mean/max values. It is offline, uses only the standard library, and
writes only the requested output file.

Usage:
    python3 bench/harness.py --service services/search-api \
        --iterations 5 --out /tmp/perf-bench-186dfe/search-api.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import statistics
import sys
import time
from pathlib import Path


def load_workload(service_dir: Path):
    workload_path = service_dir / "workload.py"
    if not workload_path.is_file():
        raise SystemExit(f"no workload.py under {service_dir}")
    module_name = "workload_" + service_dir.name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, workload_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {workload_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_case(callable_, iterations: int, warmup: int):
    for _ in range(warmup):
        callable_()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        callable_()
        samples.append((time.perf_counter() - started) * 1000.0)
    return {
        "samples_ms": [round(sample, 3) for sample in samples],
        "min_ms": round(min(samples), 3),
        "median_ms": round(statistics.median(samples), 3),
        "mean_ms": round(statistics.fmean(samples), 3),
        "max_ms": round(max(samples), 3),
    }


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Offline service benchmark harness")
    parser.add_argument("--service", required=True, help="service directory, e.g. services/search-api")
    parser.add_argument("--iterations", type=int, default=5, help="timed samples per case")
    parser.add_argument("--warmup", type=int, default=1, help="untimed warm-up calls per case")
    parser.add_argument("--out", required=True, help="path of the JSON report to write")
    args = parser.parse_args(argv)
    if args.iterations < 1:
        parser.error("--iterations must be at least 1")
    if args.warmup < 0:
        parser.error("--warmup must be zero or greater")
    return args


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    service_dir = Path(args.service).resolve()
    module = load_workload(service_dir)

    cases = getattr(module, "CASES", None)
    check = getattr(module, "check", None)
    if not isinstance(cases, dict) or not cases:
        raise SystemExit(f"{service_dir}/workload.py must define a non-empty CASES dict")
    if not callable(check):
        raise SystemExit(f"{service_dir}/workload.py must define check()")

    check()
    print(f"bench: {service_dir.name}: correctness check passed")

    results = {
        case_name: run_case(callable_, args.iterations, args.warmup)
        for case_name, callable_ in sorted(cases.items())
    }

    payload = {
        "service": service_dir.name,
        "iterations": args.iterations,
        "warmup": args.warmup,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cases": results,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"bench: {service_dir.name}: {len(results)} cases x {args.iterations} iterations")
    for case_name, stats in results.items():
        print(f"  {case_name}: median={stats['median_ms']:.3f} ms min={stats['min_ms']:.3f} ms")
    print(f"bench: wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
