"""Stage an incident collection bundle under the shared workspace."""

import argparse
import json
from pathlib import Path

from .bundle import build_manifest


def stage(root: Path, out: Path) -> Path:
    """Write the collection manifest and index for ``root`` into ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(root)
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    (out / "index.txt").write_text(
        "\n".join(manifest["files"]) + "\n", encoding="utf-8"
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage an ops-sync collection bundle")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--out", required=True, help="output directory")
    args = parser.parse_args()
    out = stage(Path(args.root).resolve(), Path(args.out))
    print(out)


if __name__ == "__main__":
    main()
