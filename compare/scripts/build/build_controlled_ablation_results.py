#!/usr/bin/env python3
"""Build the canonical controlled-ablation artifact skeleton.

The script is intentionally strict: it only emits a completed artifact when
the caller supplies completed replay-variant results. Without those inputs it
emits a blocked artifact that the paper readiness gate rejects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET = PROJECT_ROOT / "compare" / "Dataset" / "FuxiBench"
DEFAULT_OUTPUT = PROJECT_ROOT / "compare" / "results" / "paper" / "controlled_ablation_results.json"
DEFAULT_VARIANTS_INPUT = PROJECT_ROOT / "compare" / "results" / "paper" / "controlled_ablation_variant_runs.json"
EXPECTED_SCENARIOS = 67
MIN_VARIANTS = 3


def build_controlled_ablation_results(
    *,
    dataset_dir: Path = DEFAULT_DATASET,
    output_path: Path = DEFAULT_OUTPUT,
    variants_input: Path | None = None,
) -> dict[str, Any]:
    dataset_dir = dataset_dir.resolve()
    output_path = output_path.resolve()
    variant_runs = _load_variant_runs(variants_input) if variants_input else None
    artifact = _artifact_from_variant_runs(dataset_dir=dataset_dir, variant_runs=variant_runs)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact


def _artifact_from_variant_runs(*, dataset_dir: Path, variant_runs: dict[str, Any] | None) -> dict[str, Any]:
    provenance = {
        "dataset_hash": _tree_sha256(dataset_dir, include_suffixes=(".json", ".md", ".txt", ".db")),
        "git_commit": _git_commit(),
        "runner": "compare/scripts/build_controlled_ablation_results.py",
        "generated_at": datetime.now(UTC).isoformat(),
    }
    base: dict[str, Any] = {
        "schema": "mtagentprism.controlled_ablation_results.v1",
        "methodology": "sqlite_persisted_graph_action_family_feature_flags",
        "is_controlled_feature_flag_replay": True,
        "dataset": {"version": "FuxiBench", "scenario_count": _scenario_count(dataset_dir)},
        "provenance": provenance,
        "claim_scope": {
            "supported_claims": [
                "Frozen SQLite persisted-graph action-family feature-flag deltas for policy-boundary-crossing signals"
            ],
            "unsupported_claims": [
                "Live eBPF/TLS/process collector ablations",
                "Ring-buffer drop/loss ablations",
                "Raw SSE scanner and original intent-extraction ablations",
                "Argument-match-level ablations without complete persisted PBC-to-correlation provenance",
            ],
        },
    }

    if variant_runs is None:
        return {
            **base,
            "status": "blocked",
            "blocking_reasons": [
                "No completed replay-variant input was supplied.",
                "Run the Rust replay feature-flag variants over all frozen SQLite traces before marking completed.",
            ],
            "baseline": {
                "variant_id": "full_replay",
                "status": "missing",
                "runs_completed": 0,
                "metrics": {},
                "parity": {"status": "missing"},
            },
            "variants": [],
        }

    artifact = {**base, **variant_runs}
    artifact.setdefault("schema", base["schema"])
    artifact.setdefault("methodology", base["methodology"])
    artifact.setdefault("is_controlled_feature_flag_replay", True)
    artifact.setdefault("dataset", base["dataset"])
    artifact.setdefault("provenance", provenance)
    artifact.setdefault("claim_scope", base["claim_scope"])
    artifact["status"] = "completed" if _looks_completed(artifact) else "blocked"
    if artifact["status"] != "completed":
        artifact.setdefault("blocking_reasons", []).append(
            "Variant input does not satisfy completed controlled-ablation schema."
        )
    return artifact


def _looks_completed(artifact: dict[str, Any]) -> bool:
    baseline = artifact.get("baseline")
    variants = artifact.get("variants")
    if not isinstance(baseline, dict) or baseline.get("status") != "completed":
        return False
    if baseline.get("runs_completed") != EXPECTED_SCENARIOS:
        return False
    if not isinstance(baseline.get("metrics"), dict):
        return False
    if not isinstance(baseline.get("parity"), dict) or baseline["parity"].get("status") != "pass":
        return False
    if not isinstance(variants, list) or len(variants) < MIN_VARIANTS:
        return False
    for variant in variants:
        if not isinstance(variant, dict) or variant.get("status") != "completed":
            return False
        if variant.get("runs_completed") != EXPECTED_SCENARIOS:
            return False
        if not isinstance(variant.get("disabled_features"), list) or not variant["disabled_features"]:
            return False
        if not isinstance(variant.get("metrics"), dict):
            return False
        if not isinstance(variant.get("delta_vs_full"), dict):
            return False
        outcomes = variant.get("per_scenario_outcomes")
        if not isinstance(outcomes, list) or len(outcomes) != EXPECTED_SCENARIOS:
            return False
    return True


def _load_variant_runs(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _scenario_count(dataset_dir: Path) -> int:
    manifest_path = dataset_dir / "quality" / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    samples = manifest.get("samples")
    return len(samples) if isinstance(samples, list) else 0


def _tree_sha256(root: Path, *, include_suffixes: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return ""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in include_suffixes:
            continue
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build controlled-ablation paper artifact.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--variants-input", default=None)
    args = parser.parse_args(argv)
    artifact = build_controlled_ablation_results(
        dataset_dir=Path(args.dataset),
        output_path=Path(args.output),
        variants_input=Path(args.variants_input) if args.variants_input else None,
    )
    print(json.dumps({"output": args.output, "status": artifact["status"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
