"""Guard prompt-level baselines against a frozen manifest that went stale.

The prompt baselines (`evaluate_prompt_tools.py`, LLM Guard, Meta Prompt Guard,
DeBERTa) read both the task text and the label from
``prompt_inputs/manifest.json``. When scenarios or ground truth are rewritten
after the manifest was frozen, those scripts keep producing numbers that look
valid but score an old task against an old label.

This module makes that drift a loud failure instead of a silent result. It does
not rewrite any data: rebuilding the bundle is a separate, reviewed step
(``compare/scripts/build/build_prompt_input_bundle.py``), and the dataset freeze
must happen after the ground-truth review closes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class PromptManifestDrift(RuntimeError):
    """The frozen prompt manifest no longer matches the scenarios it names."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ground_truth_index(dataset_root: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    root = dataset_root / "ground_truth"
    if not root.is_dir():
        return index
    for path in sorted(root.rglob("*.json")):
        if any(part.startswith("_") for part in path.relative_to(root).parts):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        scenario_id = data.get("scenario_id")
        if isinstance(scenario_id, str) and scenario_id:
            index[scenario_id] = data
    return index


def find_drift(
    entries: list[dict[str, Any]],
    *,
    repo_root: Path,
    dataset_root: Path,
) -> list[str]:
    """Return one human-readable problem per stale or inconsistent entry."""
    problems: list[str] = []
    ground_truth = _ground_truth_index(dataset_root)

    for entry in entries:
        scenario_id = str(entry.get("scenario_id") or "<unnamed>")
        raw_path = entry.get("source_scenario_path")
        if not isinstance(raw_path, str) or not raw_path:
            problems.append(f"{scenario_id}: manifest entry has no source_scenario_path")
        else:
            # Frozen prompt bundles record source paths relative to the dataset
            # root (build_prompt_input_bundle._display_path); historical bundles
            # used the repository root. Resolve dataset-relative first, then
            # keep the legacy repository-relative fallback.
            candidates = [dataset_root / raw_path, repo_root / raw_path]
            scenario_path = next((p for p in candidates if p.is_file()), candidates[0])
            if not scenario_path.is_file():
                problems.append(
                    f"{scenario_id}: scenario source is missing ({raw_path})"
                )
            else:
                recorded = entry.get("source_scenario_sha256")
                actual = _sha256(scenario_path)
                if recorded != actual:
                    problems.append(
                        f"{scenario_id}: source_scenario_sha256 mismatch "
                        f"(manifest={recorded}, on-disk={actual})"
                    )

        truth = ground_truth.get(scenario_id)
        if truth is None:
            problems.append(f"{scenario_id}: no ground truth file for this scenario")
            continue
        for field in ("label", "attack_type"):
            if field not in entry:
                continue
            if entry.get(field) != truth.get(field):
                problems.append(
                    f"{scenario_id}: {field} mismatch "
                    f"(manifest={entry.get(field)!r}, ground_truth={truth.get(field)!r})"
                )

    return problems


def require_current(
    entries: list[dict[str, Any]],
    *,
    repo_root: Path,
    dataset_root: Path,
    allow_stale: bool = False,
) -> None:
    """Refuse to score a prompt baseline against a stale manifest.

    Pass ``allow_stale=True`` only to reproduce a historical number; the call
    then prints every problem to stderr so the run cannot pretend to be current.
    """
    problems = find_drift(entries, repo_root=repo_root, dataset_root=dataset_root)
    if not problems:
        return
    preview = "\n".join(f"  - {problem}" for problem in problems[:10])
    more = "" if len(problems) <= 10 else f"\n  ... and {len(problems) - 10} more"
    message = (
        "prompt_inputs/manifest.json is stale: it does not match the scenarios "
        f"and ground truth it claims to describe ({len(problems)} problem(s)).\n"
        f"{preview}{more}\n"
        "Rebuild the bundle with compare/scripts/build/build_prompt_input_bundle.py "
        "and refresh the hashes before producing baseline numbers. "
        "Pass --allow-stale only to reproduce a historical result."
    )
    if allow_stale:
        import sys

        print(f"WARNING: scoring a stale prompt manifest\n{message}", file=sys.stderr)
        return
    raise PromptManifestDrift(message)
