"""Helpers for staging the ops-sync collection bundle."""

from pathlib import Path
from typing import Iterator

COLLECTION_PARTS = ("config", "src", "artifacts", "handoff_records")


def iter_collection_files(root: Path) -> Iterator[Path]:
    """Yield every file that belongs in a collection bundle."""
    for part in COLLECTION_PARTS:
        base = root / part
        if base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    yield path


def build_manifest(root: Path) -> dict:
    """Describe the files a collection bundle should contain."""
    files = [str(path.relative_to(root)) for path in iter_collection_files(root)]
    return {"file_count": len(files), "files": files}
