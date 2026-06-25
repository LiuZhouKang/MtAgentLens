#!/usr/bin/env python3
"""Build a blinded independent-annotation package for FuxiBench."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATASET_ROOT = PROJECT_ROOT / "compare" / "Dataset" / "FuxiBench"
PROMPT_INPUTS = DATASET_ROOT / "prompt_inputs"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "compare" / "results" / "paper" / "release" / "annotation_package"
DEFAULT_SALT = "mtagentlens-frozen-v1.0.0-independent-annotation-2026-06-09"

ANNOTATOR_FIELDS = {
    "sample_id": "",
    "label": "",
    "attack_type": "",
    "category": "",
    "expected_claims": [],
    "expected_syscalls": [],
    "minimum_evidence": [],
    "confidence": "",
    "rationale": "",
    "needs_adjudication": True,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--salt", default=DEFAULT_SALT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    artifact = build_annotation_package(output_dir=args.output_dir, salt=args.salt)
    if args.json:
        print(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Generated {_display(args.output_dir)}")
        print(f"sample_count={artifact['sample_count']}")
        print(f"status={artifact['status']}")
    return 0


def build_annotation_package(*, output_dir: Path, salt: str = DEFAULT_SALT) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = _load_json(PROMPT_INPUTS / "manifest.json")
    entries = sorted(manifest["entries"], key=lambda item: item["scenario_id"])
    samples: list[dict[str, Any]] = []
    key_mapping: list[dict[str, Any]] = []

    for index, entry in enumerate(entries, start=1):
        sample_id = _sample_id(entry["scenario_id"], salt)
        prompt_text_path = PROMPT_INPUTS / entry["prompt_text_path"]
        prompt_text = prompt_text_path.read_text(encoding="utf-8")
        samples.append(
            {
                "sample_id": sample_id,
                "sample_index": index,
                "agent_count": entry["agent_count"],
                "mode": entry["mode"],
                "input_mode": entry["input_mode"],
                "fixture_file_count": entry["fixture_file_count"],
                "fixture_text_bytes": entry["fixture_text_bytes"],
                "prompt_text_sha256": entry["prompt_text_sha256"],
                "prompt_text": prompt_text,
                "source_visibility": {
                    "prompt_input_contract": "label-leak-free",
                    "scenario_id_hidden": True,
                    "label_hidden": True,
                    "attack_type_hidden": True,
                    "system_outputs_hidden": True,
                    "ground_truth_hidden": True,
                },
            }
        )
        key_mapping.append(
            {
                "sample_id": sample_id,
                "scenario_id": entry["scenario_id"],
                "label": _label_for(entry["attack_type"]),
                "attack_type": entry["attack_type"],
                "category": entry["category"],
                "source_scenario_path": entry["source_scenario_path"],
                "bundle_json_path": entry["bundle_json_path"],
                "prompt_text_path": entry["prompt_text_path"],
            }
        )

    _write_jsonl(output_dir / "blinded_samples.jsonl", samples)
    _write_jsonl(output_dir / "annotator_A.jsonl", [_annotator_stub(sample["sample_id"]) for sample in samples])
    _write_jsonl(output_dir / "annotator_B.jsonl", [_annotator_stub(sample["sample_id"]) for sample in samples])
    _write_jsonl(
        output_dir / "disagreements.jsonl",
        [
            {
                "status": "not_started",
                "note": "Populate after annotator_A.jsonl and annotator_B.jsonl are locked.",
            }
        ],
    )
    _write_jsonl(
        output_dir / "adjudicated_labels.jsonl",
        [
            {
                "status": "not_started",
                "note": "Populate after disagreements are reviewed by the adjudicator.",
            }
        ],
    )
    _write_json(
        output_dir / "sample_key_private.json",
        {
            "schema": "mtagentlens.annotation_sample_key.v1",
            "status": "private_not_for_annotators",
            "mapping": key_mapping,
            "warning": "Do not share this file with annotators before both blinded annotation files are locked.",
        },
    )
    _write_json(
        output_dir / "metrics.json",
        {
            "schema": "mtagentlens.annotation_metrics.v1",
            "status": "not_computed",
            "claim_boundary": "blind_annotation_quality_not_claimed",
            "required_before_claiming": [
                "locked annotator_A.jsonl",
                "locked annotator_B.jsonl",
                "disagreement adjudication",
                "Cohen kappa for binary label",
                "attack-type agreement",
                "expected-claim agreement",
            ],
            "metrics": {},
        },
    )
    _write_json(
        output_dir / "provenance.json",
        {
            "schema": "mtagentlens.annotation_package_provenance.v1",
            "status": "prepared_for_independent_annotation",
            "generated_at": _now_iso(),
            "dataset": "FuxiBench",
            "sample_count": len(samples),
            "source_manifest": _display(PROMPT_INPUTS / "manifest.json"),
            "salt_sha256": hashlib.sha256(salt.encode("utf-8")).hexdigest(),
            "blinding": _blinding_contract(),
            "git_commit": _git(["rev-parse", "HEAD"]),
            "git_dirty": bool(_git(["status", "--porcelain"])),
        },
    )
    (output_dir / "README.md").write_text(_readme(), encoding="utf-8")
    (output_dir / "codebook.md").write_text(_codebook(), encoding="utf-8")

    artifact = _manifest(output_dir, sample_count=len(samples))
    _write_json(output_dir / "manifest.json", artifact)
    return artifact


def _manifest(output_dir: Path, *, sample_count: int) -> dict[str, Any]:
    files = []
    for path in sorted(output_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name in {"manifest.json", "sample_key_private.json"}:
            continue
        files.append(
            {
                "path": path.name,
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
                "role": _role(path.name),
            }
        )
    return {
        "schema": "mtagentlens.annotation_package_manifest.v1",
        "status": "prepared_for_independent_annotation",
        "generated_at": _now_iso(),
        "dataset": "FuxiBench",
        "sample_count": sample_count,
        "claim_status": "non_claim_until_completed",
        "blinding": _blinding_contract(),
        "files": files,
        "manifest_sidecar": {
            "path": "manifest.json",
            "role": "self_not_hash_bound",
            "reason": "The manifest is generated from the public annotation workfiles and is not included in its own hash inventory.",
        },
        "private_sidecars": [
            {
                "path": "sample_key_private.json",
                "sha256": _sha256(output_dir / "sample_key_private.json"),
                "bytes": (output_dir / "sample_key_private.json").stat().st_size,
                "role": "restricted_private_annotation_key_not_public",
                "reason": "Contains the sample_id to scenario_id/label/attack_type mapping and must not be deposited with blinded annotator work packets.",
            }
        ],
        "non_claims": [
            "This package does not contain completed independent annotations.",
            "This package does not support blind annotation reliability claims until annotator and adjudication files are completed.",
            "sample_key_private.json must remain restricted and must not be shared with annotators before locked annotation submission.",
        ],
    }


def _blinding_contract() -> dict[str, Any]:
    return {
        "scenario_ids_hidden": True,
        "labels_hidden": True,
        "attack_types_hidden": True,
        "system_outputs_hidden": True,
        "ground_truth_hidden": True,
        "private_key_separated": True,
    }


def _annotator_stub(sample_id: str) -> dict[str, Any]:
    record = dict(ANNOTATOR_FIELDS)
    record["sample_id"] = sample_id
    record["expected_claims"] = []
    record["expected_syscalls"] = []
    record["minimum_evidence"] = []
    return record


def _sample_id(scenario_id: str, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}:{scenario_id}".encode("utf-8")).hexdigest()[:12].upper()
    return f"MTAP-BLIND-{digest}"


def _label_for(attack_type: str) -> str:
    return "benign" if attack_type == "benign" else "attack"


def _role(name: str) -> str:
    if name == "sample_key_private.json":
        return "private_annotation_key_not_for_annotators"
    if name.endswith(".jsonl"):
        return "annotation_workfile"
    if name.endswith(".json"):
        return "annotation_metadata"
    return "annotation_documentation"


def _readme() -> str:
    return """# MtAgentLens Independent Annotation Package

Status: prepared for independent annotation, not completed

This package contains blinded samples for a second-annotator/adjudication study
over the frozen 67-scenario benchmark. It is prepared from the label-leak-free
prompt-input manifest and intentionally excludes current ground-truth labels,
attack types, scenario IDs, MtAgentLens outputs, prompt-baseline outputs and
paper metrics from `blinded_samples.jsonl`.

Do not share `sample_key_private.json` with annotators before both annotator
files are locked. Until `annotator_A.jsonl`, `annotator_B.jsonl`,
`disagreements.jsonl`, `adjudicated_labels.jsonl` and `metrics.json` are
completed, the paper must not claim blind independent annotation reliability.
"""


def _codebook() -> str:
    return """# Annotation Codebook

Annotators should read each `prompt_text` and assign labels from the evidence
visible in the blinded sample only.

## Fields To Complete

| Field | Allowed values | Notes |
|---|---|---|
| `label` | `attack`, `benign`, `uncertain` | Whether the task/evidence represents malicious or benign behavior. |
| `attack_type` | `type_a_direct`, `type_b_indirect`, `type_c_multi_agent`, `benign`, `uncertain` | Infer only when supported by the blinded prompt and fixture content. |
| `category` | short string or `uncertain` | Scenario family inferred by the annotator. |
| `expected_claims` | list of strings | Responsibility claims expected from the visible evidence. |
| `expected_syscalls` | list of objects | Expected file/process/network actions when inferable. |
| `minimum_evidence` | list of strings | Evidence snippets that justify the decision. |
| `confidence` | `high`, `medium`, `low` | Annotator confidence. |
| `rationale` | string | Short evidence-grounded explanation. |
| `needs_adjudication` | boolean | True for any ambiguity or uncertainty. |

## Non-Claim Boundary

This package is an input to an independent annotation study. It is not the
study result. Do not report annotation agreement until two locked annotations,
adjudication and metrics are complete.
"""


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _git(args: list[str]) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
