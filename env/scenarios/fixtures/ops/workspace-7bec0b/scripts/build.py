#!/usr/bin/env python3
"""Stage 2: stage sources, byte-compile them and write the build manifest."""

from __future__ import annotations

import argparse
import compileall
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from releasekit.manifest import build_manifest, write_manifest


def stage_package(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    out = args.out_dir
    dist = out / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)

    stage_package(ROOT / "src" / "releasekit", dist / "lib" / "releasekit")
    etc = dist / "etc"
    etc.mkdir(parents=True)
    shutil.copy2(ROOT / "config" / "release.env.example", etc / "release.env.example")

    if not compileall.compile_dir(str(dist / "lib"), quiet=1, force=True):
        print("build: FAILED (byte-compilation)")
        return 1

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    manifest = build_manifest(dist, version, "src/releasekit")
    write_manifest(dist / "build-manifest.json", manifest)

    status_dir = out / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / "build").write_text("PASSED\n", encoding="utf-8")

    print(
        f"build: staged {manifest['file_count']} files "
        f"({manifest['total_bytes']} bytes), digest {manifest['digest'][:16]}"
    )
    print("build: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
