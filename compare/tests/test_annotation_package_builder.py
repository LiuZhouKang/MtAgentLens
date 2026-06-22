from __future__ import annotations

import json
from pathlib import Path

from compare.scripts.build.build_annotation_package import build_annotation_package


def test_annotation_package_builder_creates_blinded_samples_without_label_leak(tmp_path: Path) -> None:
    output_dir = tmp_path / "annotation_package"

    artifact = build_annotation_package(output_dir=output_dir, salt="unit-test-salt")

    assert artifact["schema"] == "mtagentprism.annotation_package_manifest.v1"
    assert artifact["status"] == "prepared_for_independent_annotation"
    assert artifact["sample_count"] == 67
    assert artifact["claim_status"] == "non_claim_until_completed"
    assert artifact["blinding"]["scenario_ids_hidden"] is True
    assert artifact["blinding"]["labels_hidden"] is True
    assert artifact["blinding"]["system_outputs_hidden"] is True

    required_files = {
        "README.md",
        "codebook.md",
        "blinded_samples.jsonl",
        "annotator_A.jsonl",
        "annotator_B.jsonl",
        "disagreements.jsonl",
        "adjudicated_labels.jsonl",
        "metrics.json",
        "provenance.json",
    }
    assert required_files == {item["path"] for item in artifact["files"]}
    assert artifact["manifest_sidecar"]["path"] == "manifest.json"
    assert artifact["manifest_sidecar"]["role"] == "self_not_hash_bound"
    assert artifact["private_sidecars"][0]["path"] == "sample_key_private.json"
    assert artifact["private_sidecars"][0]["role"] == "restricted_private_annotation_key_not_public"

    first_sample = json.loads((output_dir / "blinded_samples.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert first_sample["sample_id"].startswith("MTAP-BLIND-")
    assert "scenario_id" not in first_sample
    assert "label" not in first_sample
    assert "attack_type" not in first_sample
    assert "category" not in first_sample
    assert "prompt_text" in first_sample
    assert "MtAgentPrism Prompt Baseline Input" in first_sample["prompt_text"]
    assert first_sample["source_visibility"]["prompt_input_contract"] == "label-leak-free"

    private_key = json.loads((output_dir / "sample_key_private.json").read_text(encoding="utf-8"))
    assert private_key["status"] == "private_not_for_annotators"
    assert private_key["mapping"]
    assert {"sample_id", "scenario_id", "label", "attack_type", "category"} <= set(private_key["mapping"][0])

    metrics = json.loads((output_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["status"] == "not_computed"
    assert metrics["claim_boundary"] == "blind_annotation_quality_not_claimed"
