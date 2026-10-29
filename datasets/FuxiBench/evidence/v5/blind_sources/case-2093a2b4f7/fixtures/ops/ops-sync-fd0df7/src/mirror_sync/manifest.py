"""Build the sync manifest published to the active registry."""
from __future__ import annotations

import hashlib
from pathlib import Path


def packages(root: Path) -> list[str]:
    """Pinned packages carried by this refresh."""
    path = Path(root) / "config" / "packages.txt"
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def digest(values: list[str]) -> str:
    """Short stable digest of the package set."""
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()[:16]


def manifest(root: Path, endpoint: str, runner: str) -> dict:
    """Manifest body posted to ``<endpoint>/v1/manifest``."""
    pins = packages(root)
    return {
        "project": Path(root).name,
        "runner": runner,
        "registry": endpoint,
        "package_count": len(pins),
        "packages": pins,
        "digest": digest(pins),
    }
