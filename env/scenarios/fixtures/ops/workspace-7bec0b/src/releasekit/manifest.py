"""Artifact hashing and manifest construction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    """Return the hex sha256 of a file without loading it whole."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path, version: str, built_from: str) -> dict:
    """Describe every file under ``root`` with size and sha256."""
    root = Path(root)
    files = []
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "version": version,
        "built_from": built_from,
        "file_count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
        "digest": hashlib.sha256(canonical).hexdigest(),
        "files": files,
    }


def write_manifest(path: Path, manifest: dict) -> None:
    """Serialize a manifest with stable ordering."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
