#!/usr/bin/env python3
"""Refresh / verify prompt-input hashes against the bytes on disk.

Why this exists: the manifest and every bundle carried `prompt_text_sha256`
values computed from the pre-rename (MtAgentPrism) text. After the global
rename the bytes changed but the recorded hashes did not, so the old check
matched 0/86 cases. This script does not rewrite any prompt: it binds the
hashes to the current files, records the legacy value for provenance, and
adds the scenario JSON hashes the runtime actually reads.

Usage:
    python3 compare/scripts/build/refresh_prompt_input_hashes.py --check
    python3 compare/scripts/build/refresh_prompt_input_hashes.py --write

`--check` exits non-zero on any drift; `--write` updates manifest.json and
each bundle.json in place.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BASE_DIR = REPO_ROOT / "datasets" / "FuxiBench" / "prompt_inputs"


def dataset_root() -> Path:
    """Dataset that owns the bundle.

    Entries record ``source_scenario_path`` relative to the dataset, so resolution
    must start at the dataset root. The builder records it in
    ``generated_from.dataset_dir``; older manifests and synthetic fixtures that
    omit it fall back to the bundle's parent directory.
    """
    manifest_path = MANIFEST_PATH
    if manifest_path.is_file():
        recorded = (load_json(manifest_path).get("generated_from") or {}).get("dataset_dir")
        if recorded:
            candidate = Path(recorded)
            return candidate if candidate.is_absolute() else REPO_ROOT / candidate
    return BASE_DIR.parent
MANIFEST_PATH = BASE_DIR / "manifest.json"
HASH_CONTRACT = "sha256-bytes/v1"
SCENARIO_PATH_KEYS = ("fixtures_dir", "fixture_dir", "_original_fixture_dir")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, value: Any) -> None:
    # Match the existing on-disk style: sorted keys, two-space indent.
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def scenario_git_rev(path: Path, repo_root: Path = REPO_ROOT) -> str:
    """Commit that holds the exact bytes recorded for this scenario.

    Deliberately not `HEAD`: recording HEAD would make every unrelated commit
    invalidate the hash contract. But `git log -1 -- <path>` alone is wrong for
    a dirty worktree: it names the last commit that touched the file even when
    the working copy differs. An audit that resolves provenance by commit would
    then inspect bytes the manifest never saw. Mark those `dirty:<sha>` and
    untracked files `uncommitted`.
    """
    try:
        rel = path.resolve().relative_to(repo_root)
    except ValueError:
        return "unknown"

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    try:
        rev = git("log", "-1", "--format=%H", "--", str(rel))
        if not rev:
            return "uncommitted"
        worktree_blob = git("hash-object", str(rel))
        committed_blob = git("rev-parse", f"{rev}:{rel.as_posix()}")
        return rev if worktree_blob == committed_blob else f"dirty:{rev}"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def canonical_scenario_hash(path: Path) -> str:
    """Hash the scenario independent of machine-specific fixture paths.

    The runtime substitutes `{{FIXTURE_DIR}}`; the scenario file may also
    carry absolute paths from the machine that produced it. Both are
    normalised to the same placeholder before hashing so two clones agree.
    """
    data = load_json(path)
    if isinstance(data, dict):
        for key in SCENARIO_PATH_KEYS:
            if key in data:
                data[key] = "{{FIXTURE_DIR}}"
    canonical = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return sha256_bytes(canonical)


def expected_fields(entry: dict[str, Any]) -> dict[str, Any]:
    scenario_id = entry["scenario_id"]

    prompt_path = BASE_DIR / entry["prompt_text_path"]
    prompt_raw = prompt_path.read_bytes()
    text = prompt_raw.decode("utf-8")
    newline = "CRLF" if "\r\n" in text else "CR" if "\r" in text else "LF"

    scenario_path = dataset_root() / entry["source_scenario_path"]
    scenario_raw = scenario_path.read_bytes()
    scenario = json.loads(scenario_raw)
    if scenario.get("id") != scenario_id:
        raise SystemExit(
            f"scenario id mismatch for {scenario_id}: "
            f"{scenario_path} declares {scenario.get('id')!r}"
        )

    return {
        "prompt_text_sha256": sha256_bytes(prompt_raw),
        "prompt_text_bytes": len(prompt_raw),
        "prompt_text_encoding": "utf-8",
        "prompt_text_newline": newline,
        "hash_contract": HASH_CONTRACT,
        "source_scenario_sha256": sha256_bytes(scenario_raw),
        "source_scenario_bytes": len(scenario_raw),
        "source_scenario_canonical_sha256": canonical_scenario_hash(scenario_path),
        "source_scenario_git_rev": scenario_git_rev(scenario_path),
    }


def refresh(write: bool) -> list[str]:
    manifest = load_json(MANIFEST_PATH)
    entries = manifest["entries"]
    problems: list[str] = []

    # A new checksum cannot make stale task material current.
    from compare.scripts.build.build_prompt_input_bundle import (
        _agent_tasks, _collect_fixture_files, _render_prompt_text, _resolve_fixture_dir,
    )
    for entry in entries:
        sid = entry["scenario_id"]
        scenario = load_json(dataset_root() / entry["source_scenario_path"])
        fixture_dir = _resolve_fixture_dir(scenario, dataset_root())
        fixtures = _collect_fixture_files(fixture_dir) if fixture_dir else []
        expected_files = [{"relative_path": rel, "sha256": digest, "bytes": size}
                          for rel, _text, digest, size in fixtures]
        bundle = load_json(BASE_DIR / entry["bundle_json_path"])
        if bundle.get("fixture", {}).get("files") != expected_files:
            problems.append(f"{sid}: fixture bytes differ; rebuild prompt inputs")
        rendered = _render_prompt_text(scenario=scenario, agents=_agent_tasks(scenario),
                                      fixture_dir=fixture_dir, fixture_files=fixtures)
        if (BASE_DIR / entry["prompt_text_path"]).read_bytes() != rendered.encode("utf-8"):
            problems.append(f"{sid}: prompt differs from current tasks/fixtures; rebuild prompt inputs")
    if write and problems:
        raise ValueError("Cannot refresh stale content; rebuild prompt inputs first: " + problems[0])

    prepared = [(entry, expected_fields(entry), BASE_DIR / entry["bundle_json_path"],
                 load_json(BASE_DIR / entry["bundle_json_path"])) for entry in entries]
    for entry, fields, bundle_path, bundle in prepared:
        scenario_id = entry["scenario_id"]

        if write:
            current = entry.get("prompt_text_sha256")
            if current != fields["prompt_text_sha256"]:
                # Preserve the old value: it is the hash of the text that
                # produced the frozen prompt baseline, not a bug to hide.
                entry.setdefault("prompt_text_sha256_at_run_legacy", current)
            entry.update(fields)
            bundle["prompt_text_sha256"] = fields["prompt_text_sha256"]
            dump_json(bundle_path, bundle)
            continue

        for field, expected in fields.items():
            actual = entry.get(field)
            if actual != expected:
                problems.append(
                    f"{scenario_id}: {field} mismatch (manifest={actual!r}, "
                    f"on-disk={expected!r})"
                )
        if bundle.get("prompt_text_sha256") != fields["prompt_text_sha256"]:
            problems.append(
                f"{scenario_id}: bundle prompt_text_sha256 does not match the "
                "prompt bytes"
            )

    if write:
        dump_json(MANIFEST_PATH, manifest)
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="verify; non-zero on drift")
    mode.add_argument("--write", action="store_true", help="rewrite hashes in place")
    args = parser.parse_args()

    problems = refresh(write=args.write)
    if args.check and problems:
        for problem in problems:
            print(f"DRIFT {problem}", file=sys.stderr)
        print(f"{len(problems)} hash mismatch(es)", file=sys.stderr)
        return 1
    if args.check:
        print(f"ok: {len(load_json(MANIFEST_PATH)['entries'])} entries match on-disk bytes")
    else:
        print(f"wrote hashes for {len(load_json(MANIFEST_PATH)['entries'])} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
