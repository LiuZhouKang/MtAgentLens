# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Freeze a release candidate: one binary, one dataset, one recorded toolchain.

The point is to make "which code and which inputs produced the numbers?" a file
lookup instead of a recollection. Everything here is read-only over the working
tree and refuses to invent missing inputs: a missing hash is an error, never a
null that later reads as "not applicable".
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

SCHEMA = "mtagentlens.release-candidate/1"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_HELPER_NAMES = ("tls_capture", "process", "stdiocap")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_file(path: Path, label: str) -> Path:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    return path


def _run(*command: str, cwd: Path = PROJECT_ROOT) -> str:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"command failed: {' '.join(command)}: {exc}") from None
    return result.stdout.strip()


def _try_run(*command: str, cwd: Path = PROJECT_ROOT) -> str | None:
    try:
        return _run(*command, cwd=cwd)
    except ValueError:
        return None


def git_state(root: Path) -> dict:
    """HEAD plus a dirty flag; a release candidate must not hide either."""
    head = _run("git", "rev-parse", "HEAD", cwd=root)
    branch = _run("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    status = _run("git", "status", "--porcelain", cwd=root)
    changes = [line for line in status.splitlines() if line.strip()]
    return {"head": head, "branch": branch, "dirty": bool(changes), "changed_paths": len(changes)}


def dataset_identity(release_root: Path) -> dict:
    """The release manifest is validated by its own module, not re-parsed here."""
    release_root = Path(release_root)
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from compare.tools.dataset_release import validate_release

    release = validate_release(release_root)
    manifest = _require_file(release_root / "release.json", "dataset release manifest")
    return {
        "path": str(release_root.resolve()),
        "release_id": release["release_id"],
        "cases": sorted(release["case_bindings"]),
        "case_count": len(release["case_bindings"]),
        "manifest_sha256": _sha256(manifest),
        "paper_metrics_available": bool(release.get("paper_metrics_available")),
        "publication_blockers": list(release.get("publication_blockers") or []),
    }


def freeze(binary: Path, release_root: Path, *, verify_log: Path | None = None,
           helper_root: Path | None = None, root: Path = PROJECT_ROOT) -> dict:
    binary = _require_file(binary, "collector binary")
    helper_root = Path(helper_root or root / "bpftools" / ".output" / "bin")
    helpers = {}
    for name in _HELPER_NAMES:
        path = _require_file(helper_root / name, f"eBPF helper {name}")
        helpers[name] = {"sha256": _sha256(path), "size": path.stat().st_size}
    record = {
        "schema": SCHEMA,
        "binary": {
            "path": str(binary.resolve()),
            "sha256": _sha256(binary),
            "size": binary.stat().st_size,
            "mtime": binary.stat().st_mtime,
        },
        "ebpf_helpers": helpers,
        "dataset": dataset_identity(Path(release_root)),
        # Toolchain versions are context, not evidence: an absent compiler is
        # recorded as null instead of failing the freeze.
        "toolchain": {
            "rustc": _try_run("rustc", "--version", cwd=Path(root)),
            "python": platform.python_version(),
            "kernel": platform.release(),
            "node": _try_run("node", "--version", cwd=Path(root)),
        },
        "git": git_state(Path(root)),
        "verification": {"gate_log": None},
        "paper_metrics_available": False,
        "note": ("Input and build identity only. Metric claims must cite the experiment "
                 "outputs recorded alongside this file."),
    }
    if verify_log is not None:
        verify_log = _require_file(verify_log, "verification log")
        text = verify_log.read_text(encoding="utf-8", errors="replace")
        record["verification"] = {
            "gate_log_sha256": _sha256(verify_log),
            "gate_log_bytes": verify_log.stat().st_size,
            "gates_passed": "verify: all gates passed" in text,
            "tail": [line for line in text.splitlines() if "PASS" in line or "FAIL" in line][-20:],
        }
    return record


def write_record(path: Path, record: dict) -> None:
    """Publish once; a release candidate record is never silently rewritten."""
    path = Path(path)
    if path.exists():
        raise ValueError("release candidate record already exists; refusing to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
