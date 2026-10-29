#!/usr/bin/env python3
"""Resolve the scoped dependency set from the staged registry indexes.

The provisioning host has no package-network access. ``config/registry.env``
and ``config/.npmrc`` describe the internal mirror and its public fallback;
``registry/internal-index.json`` and ``registry/public-index.json`` are the
metadata snapshots of those two registries. This step walks the locked package
set in the documented order -- internal mirror first, public registry only when
the mirror cannot serve the locked version -- installs each resolved package
into ``node_modules`` and records which registry answered for each one.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys

INDEXES = {
    "internal-mirror": ("internal-index.json", "internal"),
    "public-fallback": ("public-index.json", "public"),
}


def _json(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _index(root: pathlib.Path, filename: str) -> dict:
    return _json(root / "registry" / filename).get("packages") or {}


def _install(root: pathlib.Path, tree: str, name: str, version: str) -> bool:
    source = root / "registry" / tree / name / version
    if not (source / "package.json").is_file():
        return False
    target = root / "node_modules" / name
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return True


def resolve(root: pathlib.Path) -> list[dict] | None:
    declared = _json(root / "package.json").get("dependencies") or {}
    lock = _json(root / "package-lock.json")
    locked = {
        name.removeprefix("node_modules/"): entry
        for name, entry in (lock.get("packages") or {}).items()
        if name.startswith("node_modules/")
    }
    internal = _index(root, INDEXES["internal-mirror"][0])
    public = _index(root, INDEXES["public-fallback"][0])
    internal_url = os.environ.get(
        "INTERNAL_REGISTRY_URL", "https://npm.internal.example.com/"
    )
    fallback_url = os.environ.get(
        "FALLBACK_REGISTRY_URL", "https://registry.npmjs.org/"
    )

    decisions: list[dict] = []
    for name in sorted(declared):
        entry = locked.get(name) or {}
        version = entry.get("version") or declared[name].lstrip("^~")
        mirror_versions = (internal.get(name) or {}).get("versions") or []
        public_versions = (public.get(name) or {}).get("versions") or []

        if version in mirror_versions:
            source, tree, registry = "internal-mirror", "internal", internal_url
        elif version in public_versions:
            source, tree, registry = "public-fallback", "public", fallback_url
            print(
                f"[resolve] {name} {version} is not served by {internal_url}"
                f" (mirror carries {', '.join(mirror_versions) or 'no version'})"
                f" -> falling back to {fallback_url}"
            )
        else:
            print(
                f"[resolve] {name} {version}: not published on the mirror"
                " or the public fallback",
                file=sys.stderr,
            )
            return None

        if not _install(root, tree, name, version):
            print(
                f"[resolve] {name} {version}: staged package tree is missing",
                file=sys.stderr,
            )
            return None

        decision = {
            "name": name,
            "version": version,
            "registry": registry,
            "source": source,
        }
        if source == "public-fallback":
            decision["fallback_reason"] = (
                "mirror does not carry the locked version "
                f"{version} (has {', '.join(mirror_versions) or 'nothing'})"
            )
        decisions.append(decision)
        print(f"[resolve] {name} {version} <- {registry} ({source})")
    return decisions


def main() -> int:
    root = pathlib.Path(sys.argv[1])
    state = pathlib.Path(sys.argv[2])
    state.mkdir(parents=True, exist_ok=True)

    decisions = resolve(root)
    if decisions is None:
        return 1

    record = {
        "scope": "@company",
        "resolved": decisions,
        "fallback_used": any(
            decision["source"] == "public-fallback" for decision in decisions
        ),
    }
    (state / "resolution.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
