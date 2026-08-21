#!/usr/bin/env python3
"""Build transparent offline evidence-masking ablation results.

This is intentionally not marked as a completed controlled feature-flag replay.
It quantifies which already-emitted evidence families carry the frozen-set
matches, while preserving the readiness blocker for true module-disable replay.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from compare.tools.base import BoundaryType, FindingRecord  # noqa: E402
from compare.tools.datasets import FrozenDatasetAdapter  # noqa: E402
from compare.tools.evaluator import evaluate_tool_results  # noqa: E402
from env.scripts.evaluate_frozen import MTAGENTLENS_TOOL, load_mtagentlens_findings  # noqa: E402


DEFAULT_DATASET = PROJECT_ROOT / "compare" / "Dataset" / "FuxiBench"
DEFAULT_OUTPUT = PROJECT_ROOT / "compare" / "results" / "paper" / "ablation_results.json"


VariantFilter = Callable[[FindingRecord], bool]


def build_ablation_results(
    *,
    dataset_dir: Path = DEFAULT_DATASET,
    output_path: Path = DEFAULT_OUTPUT,
) -> dict[str, object]:
    dataset_dir = dataset_dir.resolve()
    output_path = output_path.resolve()
    cases = FrozenDatasetAdapter(dataset_dir).load_cases()
    full_findings = list(load_mtagentlens_findings(cases))
    full_metrics = evaluate_tool_results(MTAGENTLENS_TOOL, cases, full_findings)

    variants = []
    for variant_id, description, keep_fn in _offline_variants():
        variant_findings = [finding for finding in full_findings if keep_fn(finding)]
        metrics = evaluate_tool_results(MTAGENTLENS_TOOL, cases, variant_findings)
        variants.append(
            {
                "variant_id": variant_id,
                "status": "completed",
                "description": description,
                "method": "offline_evidence_masking",
                "removed_findings": len(full_findings) - len(variant_findings),
                "remaining_findings": len(variant_findings),
                "metrics": metrics,
                "delta_vs_full": _metric_delta(full_metrics, metrics),
            }
        )

    unsupported = [
        {
            "variant_id": "disable_identity_resolution",
            "status": "requires_feature_flag_replay",
            "reason": "Identity resolution changes agent attribution before claims are emitted; it cannot be faithfully simulated by masking emitted findings.",
        },
        {
            "variant_id": "disable_delegation_tracker",
            "status": "requires_feature_flag_replay",
            "reason": "Delegation tracking changes graph topology before classification; emitted-output masking would understate upstream effects.",
        },
        {
            "variant_id": "disable_signal_scoring",
            "status": "requires_feature_flag_replay",
            "reason": "Signal scoring changes alert decisions and confidence before persistence; it must be re-run in the collector.",
        },
    ]

    report: dict[str, object] = {
        "schema": "mtagentlens.ablation_results.v1",
        "methodology": "offline_evidence_masking",
        "is_controlled_feature_flag_replay": False,
        "dataset": _display_path(dataset_dir),
        "full_metrics": full_metrics,
        "variants": variants,
        "unsupported_controlled_variants": unsupported,
        "paper_use": {
            "allowed": "Use as post-hoc evidence-family sensitivity analysis.",
            "forbidden": "Do not describe this artifact as controlled module ablation or feature-flag replay.",
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _offline_variants() -> list[tuple[str, str, VariantFilter]]:
    return [
        (
            "mask_content_boundary_findings",
            "Remove persisted ContentBoundary findings from the frozen full-system output.",
            lambda finding: finding.boundary_type != BoundaryType.CONTENT,
        ),
        (
            "mask_resource_boundary_findings",
            "Remove persisted ResourceBoundary findings from the frozen full-system output.",
            lambda finding: finding.boundary_type != BoundaryType.RESOURCE,
        ),
        (
            "mask_exec_arg_boundary_findings",
            "Remove persisted ExecArgBoundary findings from the frozen full-system output.",
            lambda finding: finding.boundary_type != BoundaryType.EXEC_ARG,
        ),
        (
            "mask_delegation_boundary_findings",
            "Remove persisted DelegationBoundary findings from the frozen full-system output.",
            lambda finding: finding.boundary_type != BoundaryType.DELEGATION,
        ),
        (
            "mask_detecting_alert_findings",
            "Remove persisted Unsafe/Uncertain alert findings and keep only responsibility claims.",
            lambda finding: finding.boundary_type is not None,
        ),
        (
            "mask_claim_findings",
            "Remove persisted responsibility claim findings and keep only detecting alerts.",
            lambda finding: finding.boundary_type is None,
        ),
    ]


def _metric_delta(full: dict[str, object], masked: dict[str, object]) -> dict[str, float]:
    keys = ("scenario_recall", "precision", "f1", "claim_coverage")
    delta: dict[str, float] = {}
    for key in keys:
        full_value = full.get(key)
        masked_value = masked.get(key)
        if isinstance(full_value, (int, float)) and isinstance(masked_value, (int, float)):
            delta[key] = float(masked_value) - float(full_value)
    return delta


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build offline evidence-masking ablation report.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    report = build_ablation_results(dataset_dir=Path(args.dataset), output_path=Path(args.output))
    print(json.dumps({"output": args.output, "methodology": report["methodology"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
