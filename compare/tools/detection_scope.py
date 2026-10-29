"""Detection-excluded scope shared by every FuxiBench metric walker.

FuxiBench 2.0 canonical GT keeps 93 records. Nine of them are independently
adjudicated benign disagreements: their historical v4 detection contract is
retained for provenance only under ``quality.detection_excluded=true`` and they
must never enter detection denominators (TPR/FPR/F1, McNemar, bootstrap, PBC,
execution-conditioned recall).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_ROOT = ROOT / "datasets"
DEFAULT_DATASET_DIR = DEFAULT_DATASET_ROOT / "FuxiBench"
DATASET_RELEASE_SCHEMA = "mtagentlens.dataset-release/1"
CURRENT_ATTACK_TYPES = {"A", "B"}
LEGACY_ATTACK_TYPES = {"C"}
CURRENT_LABELS = {"attack", "benign"}


def is_detection_excluded(gt: object) -> bool:
    """True when an independently adjudicated disagreement must not be scored."""
    if not isinstance(gt, dict):
        return False
    quality = gt.get("quality")
    return isinstance(quality, dict) and quality.get("detection_excluded") is True


def require_dataset_dir(dataset_dir: Path | None = None) -> Path:
    """Return the canonical FuxiBench 2.0 package dir or fail with a clear hint."""
    path = Path(dataset_dir) if dataset_dir is not None else DEFAULT_DATASET_DIR
    if not (path / "ground_truth").is_dir() or not (path / "scenarios").is_dir():
        raise ValueError(
            f"FuxiBench 2.0 package not found at {path}; use the self-contained "
            f"{DEFAULT_DATASET_DIR} (datasets/FuxiBench) instead of the retired "
            "compare/Dataset tree")
    return path


def load_ground_truth(ground_truth_dir: Path) -> dict[str, dict]:
    """Load scenario GT keyed by scenario_id, skipping ``_reviewers`` sidecars."""
    root = Path(ground_truth_dir)
    if not root.is_dir():
        raise ValueError(f"missing ground truth dir: {root}; use "
                         f"{DEFAULT_DATASET_DIR / 'ground_truth'} (FuxiBench 2.0)")
    records: dict[str, dict] = {}
    for path in sorted(root.rglob("*.json")):
        if path.name.startswith("_") or any(
                part.startswith("_") for part in path.relative_to(root).parts):
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("scenario_id"):
            continue
        records[str(data["scenario_id"])] = data
    return records


def detection_excluded_ids(ground_truth_dir: Path) -> set[str]:
    return {scenario_id for scenario_id, gt in load_ground_truth(ground_truth_dir).items()
            if is_detection_excluded(gt)}


def included_ground_truth(ground_truth_dir: Path) -> dict[str, dict]:
    return {scenario_id: gt for scenario_id, gt in load_ground_truth(ground_truth_dir).items()
            if not is_detection_excluded(gt)}


def scope_counts(ground_truth_dir: Path) -> dict:
    """Counts used by guards/tests: 93 records, 9 excluded, 84 scored."""
    records = load_ground_truth(ground_truth_dir)
    excluded = {sid for sid, gt in records.items() if is_detection_excluded(gt)}
    included = [gt for gt in records.values() if not is_detection_excluded(gt)]
    return {
        "records": len(records),
        "detection_excluded": len(excluded),
        "scored": len(included),
        "scored_attack": sum(1 for gt in included if gt.get("label") == "attack"),
        "scored_benign": sum(1 for gt in included if gt.get("label") != "attack"),
        "excluded_scenario_ids": sorted(excluded),
    }


def validate_current_attack_type(attack_type: object, *, allow_legacy_c: bool = False) -> str:
    """Enforce the two-type FuxiBench 2.0 taxonomy (A/B/benign).

    Legacy Type C ("multi-agent" under the old taxonomy) is only accepted for
    explicitly historical inputs; it is never a current attack type.
    """
    if attack_type == "benign":
        return "benign"
    if attack_type in CURRENT_ATTACK_TYPES:
        return str(attack_type)
    if attack_type in LEGACY_ATTACK_TYPES:
        if allow_legacy_c:
            return str(attack_type)
        raise ValueError(
            "legacy Type C is not a current FuxiBench 2.0 attack type (taxonomy "
            "fuxibench-2type/1 uses A/B/benign); pass allow_legacy_type_c=True "
            "only when loading an explicitly historical v4 corpus")
    raise ValueError(f"unsupported attack_type {attack_type!r}; FuxiBench 2.0 uses A/B/benign")
