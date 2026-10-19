#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Measure the wire-capture payload sizes that decide the P1 base64 design.

The plan's go/no-go depends on real per-event sizes, not on the concatenated
HTTP message sizes. This script scans the raw `tls_capture` JSONL files that
still exist under /tmp/p05 (HANDOFF trap 10: /tmp is not durable) and reports:

  * the per-event buf_size distribution (p50/p99/max),
  * the current line size, the projected data+data_b64 line size,
  * the worst-case escaped-data bound.

It FAILS if no raw capture exists. A missing measurement is not a "N/A"; the
base64 change must not proceed on assumed numbers.

Usage:
    python3 -I scripts/wire/measure_payload_sizes.py --captures /tmp/p05 \
        --fixtures collector/tests/fixtures/wire \
        --out docs/superpowers/spikes/2026-10-17-p1-base64-size.md
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

BUF_SIZE_RE = re.compile(rb'"buf_size":(\d+)')
LEN_RE = re.compile(rb'"len":(\d+)')
FUNCTION_RE = re.compile(rb'"function":"([^"]*)"')


def b64_len(n: int) -> int:
    """RFC 4648 standard-alphabet encoded length, including '=' padding."""
    return 4 * ((n + 2) // 3)


def self_check() -> int:
    cases = {0: 0, 1: 4, 2: 4, 3: 4, 4: 8, 597: 796, 1275: 1700, 262144: 349528}
    for raw, expected in cases.items():
        actual = b64_len(raw)
        if actual != expected:
            print(f"SELF-CHECK FAILED: b64_len({raw}) = {actual}, expected {expected}")
            return 1
    # The maximum TLS buffer is 256 KiB; 8 MiB ring / 349,584-byte struct = 23.
    assert b64_len(256 * 1024) == 349528
    print("self-check: base64 length arithmetic matches RFC 4648 (max 262144 -> 349528)")
    return 0


def scan_captures(captures: Path) -> list[dict]:
    events: list[dict] = []
    for path in sorted(captures.rglob("*.jsonl")):
        with path.open("rb") as handle:
            for lineno, line in enumerate(handle, 1):
                if not line.startswith(b"{"):
                    continue
                buf = BUF_SIZE_RE.search(line)
                if not buf:
                    continue
                fn = FUNCTION_RE.search(line)
                events.append(
                    {
                        "capture": str(path),
                        "line": lineno,
                        "function": fn.group(1).decode() if fn else "?",
                        "buf_size": int(buf.group(1)),
                        "len": int(LEN_RE.search(line).group(1)) if LEN_RE.search(line) else 0,
                        "line_bytes": len(line.rstrip(b"\n")),
                    }
                )
    return events


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def fixture_sizes(fixtures: Path) -> list[dict]:
    rows = []
    for request in sorted(fixtures.rglob("request.http")):
        n = request.stat().st_size
        rows.append({"name": str(request.relative_to(fixtures)), "bytes": n})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--captures")
    parser.add_argument("--fixtures")
    parser.add_argument("--out")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()

    if args.self_check:
        return self_check()
    if not (args.captures and args.fixtures and args.out):
        parser.error("--captures, --fixtures and --out are required unless --self-check")

    rc = self_check()
    if rc != 0:
        return rc

    captures = Path(args.captures)
    events = scan_captures(captures)
    if not events:
        print(
            f"ERROR: no raw capture events under {captures}. "
            "The P1 C change must not proceed without measured sizes.",
            file=sys.stderr,
        )
        return 2

    sizes = [event["buf_size"] for event in events]
    stats = {
        "events": len(events),
        "p50_bytes": percentile(sizes, 0.50),
        "p99_bytes": percentile(sizes, 0.99),
        "max_bytes": max(sizes),
        "max_len_bytes": max(event["len"] for event in events),
        "max_line_bytes": max(event["line_bytes"] for event in events),
    }

    # Projected line growth: the existing line already contains `data`, so the
    # additive field costs its key + the base64 text + the closing quote.
    def projected(line_bytes: int, n: int) -> int:
        return line_bytes + len(',"data_b64":"') + b64_len(n) + 1

    samples = []
    for n in (597, 1275, 16384, 51108, 70703, 87061, 262144):
        line = n  # lower bound: one literal byte per payload byte
        samples.append(
            {
                "n": n,
                "b64": b64_len(n),
                "line_lower_bound": projected(line, n),
                "line_worst_escape_bound": projected(6 * n, n),
            }
        )

    fixtures = fixture_sizes(Path(args.fixtures))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# P1 base64 size spike",
        "",
        "Measured from the raw `tls_capture` JSONL still present on this machine.",
        "",
        "## Source facts (verified)",
        "",
        "- `MAX_BUF_SIZE = 256 * 1024` (`bpftools/tls_capture/tls_capture.h:9`)",
        "- `RING_BUFFER_SIZE = 8 * 1024 * 1024` (`tls_capture.h:10`)",
        "- `probe_SSL_data_t` is at least 262,216 bytes; 8 MiB / 262,216 = 31 in-flight events",
        "- The 32 KB / 512 KB claims in older docs are stale; grep finds no such constant in code",
        "",
        "## Per-event buf_size distribution",
        "",
        f"- events: {stats['events']}",
        f"- p50: {stats['p50_bytes']} bytes",
        f"- p99: {stats['p99_bytes']} bytes",
        f"- max: {stats['max_bytes']} bytes (max declared len: {stats['max_len_bytes']})",
        f"- max current line: {stats['max_line_bytes']} bytes",
        "",
        "## Projected additive-field line size",
        "",
        "| payload bytes | base64 chars | line lower bound | worst escaped bound |",
        "|---|---|---|---|",
    ]
    for sample in samples:
        lines.append(
            f"| {sample['n']} | {sample['b64']} | "
            f"{sample['line_lower_bound']} | {sample['line_worst_escape_bound']} |"
        )
    lines += [
        "",
        "## Fixture request sizes",
        "",
    ]
    for row in fixtures:
        lines.append(f"- `{row['name']}`: {row['bytes']} bytes")
    lines += [
        "",
        "## Go / no-go",
        "",
        "- Alphabet: RFC 4648 STANDARD with `=` padding.",
        "- Encoding happens in user space in `print_event` (streaming); the BPF",
        "  struct is untouched, so ring occupancy stays 31 events.",
        "- If the observed p99 projected line exceeds 1 MiB or the RSS budget is",
        "  breached in the live gate, the fallback is to omit `data` (set it to",
        "  null) for over-threshold events; `data_b64` is never truncated.",
        "",
    ]
    out.write_text("\n".join(lines) + "\n")
    print(json.dumps(stats, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
