#!/usr/bin/env python3
"""Write a sha256sum-compatible checksum file for manifest-bound artifacts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = PROJECT_ROOT / "compare" / "results" / "paper" / "submission_manifest.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "compare" / "results" / "paper" / "release" / "CHECKSUMS.sha256"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    artifact = build_checksums(manifest_path=args.manifest, output_path=args.output)
    if args.json:
        print(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Generated {_display(args.output)}")
        print(f"file_count={artifact['file_count']}")
    return 0


def build_checksums(
    *,
    manifest_path: Path = MANIFEST_PATH,
    output_path: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    entries = sorted(
        (
            str(item["sha256"]),
            str(item["path"]),
        )
        for item in manifest.get("files", [])
        if isinstance(item, dict) and item.get("sha256") and item.get("path")
    )
    output_path.write_text("".join(f"{digest}  {path}\n" for digest, path in entries), encoding="utf-8")
    return {
        "schema": "mtagentprism.submission_checksums.v1",
        "status": "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hash_source": _display(manifest_path),
        "output_path": str(output_path),
        "file_count": len(entries),
    }


def _display(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
