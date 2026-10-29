#!/usr/bin/env python3
"""Record a registry entry in the shared registry knowledge base."""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

DEFAULT_OUT = "/tmp/ops-sync-fd0df7/kb/mirror-registry.json"
LEGACY_UPSTREAM = "https://registry.npmjs.org"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True, help="active registry endpoint")
    parser.add_argument("--checksum-service", default="", help="checksum endpoint")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--note", default="nightly refresh")
    args = parser.parse_args()

    endpoint = args.endpoint.strip().strip("`").rstrip("/")
    if not endpoint.startswith(("http://", "https://")):
        sys.exit(f"endpoint must be an http(s) URL, got {args.endpoint!r}")
    checks = (args.checksum_service or endpoint + "/checksums/v1").strip().rstrip("/")

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": 2,
        "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "registry": {
            "primary": endpoint,
            "fallback": endpoint,
            "fallback_alt": LEGACY_UPSTREAM,
            "checksum_service": checks,
        },
        "note": args.note,
    }
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    log = out.parent / "registry-log.md"
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"- {record['recorded_at']} recorded {endpoint}\n")

    print(f"recorded {endpoint} -> {out}")
    print(f"checksum service: {checks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
