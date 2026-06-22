from __future__ import annotations

from pathlib import Path

from compare.scripts.build.build_submission_package_manifest import build_manifest


def test_submission_manifest_binds_results_figures_tables_and_docs() -> None:
    manifest = build_manifest()

    assert manifest["schema"] == "mtagentprism.paper_submission_package.v1"
    assert manifest["status"] == "completed"
    assert manifest["dataset"]["total_scenarios"] == 67
    assert manifest["dataset"]["attack_scenarios"] == 54
    assert manifest["dataset"]["benign_scenarios"] == 13
    assert manifest["dataset"]["prompt_input_contract"] == "label-leak-free frozen prompt_inputs manifest"

    files = {item["path"]: item for item in manifest["files"]}
    required_paths = {
        "compare/results/paper/evaluation_report.json",
        "compare/results/paper/controlled_ablation_results.json",
        "compare/results/backend_health/capture_health.json",
        "compare/results/perf/performance_results.json",
        "compare/results/paper/MANUSCRIPT_EXPERIMENT_APPENDIX.md",
        "compare/results/paper/REPRODUCIBILITY.md",
        "compare/results/paper/README.md",
        "compare/results/paper/release/DATACITE_DRAFT.json",
        "compare/results/paper/release/AUTHOR_SUBMISSION_ACTIONS.md",
        "compare/results/paper/release/AVAILABILITY_STATEMENTS.md",
        "compare/results/paper/release/DATA_DICTIONARY.md",
        "compare/results/paper/release/TRACE_REDACTION_POLICY.md",
        "compare/results/paper/release/TRACE_REDACTION_AUDIT.json",
        "compare/results/paper/release/ANNOTATION_ADJUDICATION_TEMPLATE.md",
        "compare/results/paper/release/REPOSITORY_DEPOSIT_CHECKLIST.md",
        "compare/results/paper/release/annotation_package/manifest.json",
        "compare/results/paper/release/annotation_package/blinded_samples.jsonl",
        "compare/results/paper/release/annotation_package/annotator_A.jsonl",
        "compare/results/paper/release/annotation_package/annotator_B.jsonl",
        "compare/results/paper/paper_tables.tex",
        "compare/results/paper/figures/figure_manifest.json",
        "compare/docs/paper-experiment-quality-dossier-v3-2026-06-09.md",
        "docs/design/experiment-design.md",
        "compare/Dataset/FuxiBench/metadata.json",
        "compare/Dataset/FuxiBench/prompt_inputs/manifest.json",
        "compare/Dataset/FuxiBench/quality/manifest.json",
        "compare/Dataset/FuxiBench/ground_truth/benign/benign-api-doc.json",
        "compare/scripts/run/run_submission_package_gate.py",
        "compare/tools/base.py",
        "collector/Cargo.lock",
    }
    assert required_paths <= set(files)
    for path, entry in files.items():
        assert Path(path).exists()
        assert len(entry["sha256"]) == 64
        assert entry["bytes"] > 0

    assert "compare/results/paper/ablation_results.json" not in files
    assert "compare/results/paper/ablation_results.json" in manifest["not_for_main_claims"]
    assert any(item["role"] == "figure_source_data" for item in files.values())
    assert any(item["role"] == "prompt_baseline_result" for item in files.values())
    assert any(item["role"] == "release_metadata" for item in files.values())
    assert any(item["role"] == "release_documentation" for item in files.values())
    assert any(item["role"] == "annotation_package_data" for item in files.values())
    assert any(item["role"] == "annotation_package_documentation" for item in files.values())
    assert not any(path.endswith("sample_key_private.json") for path in files)
    assert not any("/traces/" in path for path in files)
    assert not any("/_external_archive/" in path for path in files)
    assert any(item["role"] == "public_dataset_ground_truth" for item in files.values())
    assert any(item["role"] == "public_dataset_prompt_input" for item in files.values())
    assert any(item["role"] == "code_reproducibility_source" for item in files.values())
    assert any(item["role"] == "code_dependency_lock" for item in files.values())
    assert "compare/results/paper/SUBMISSION_AUDIT.json" not in files
    assert manifest["submission_audit"]["path"] == "compare/results/paper/SUBMISSION_AUDIT.json"
    assert manifest["submission_audit"]["role"] == "post_manifest_sidecar_not_hash_bound"
    assert "compare/results/paper/release/CHECKSUMS.sha256" not in files
    assert manifest["checksums"]["path"] == "compare/results/paper/release/CHECKSUMS.sha256"
    assert manifest["checksums"]["role"] == "post_manifest_sidecar_not_hash_bound"
    assert manifest["submission_gate"]["path"] == "compare/results/paper/SUBMISSION_GATE.json"
    assert manifest["submission_gate"]["role"] == "post_manifest_sidecar_not_hash_bound"
    restricted = {item["id"]: item for item in manifest["restricted_sidecars"]}
    assert restricted["annotation_sample_key_private"]["path"] == (
        "compare/results/paper/release/annotation_package/sample_key_private.json"
    )
    assert restricted["annotation_sample_key_private"]["role"] == "restricted_sidecar_not_public_hash_bound"


def test_submission_manifest_keeps_claim_boundaries_and_outputs_explicit() -> None:
    manifest = build_manifest()

    supported = set(manifest["claim_boundaries"]["supported_claims"])
    unsupported = set(manifest["claim_boundaries"]["unsupported_claims"])

    assert "67-scenario frozen MtAgentPrism offline evaluation" in supported
    assert "external benchmark validation" in unsupported
    assert "universal prompt-guard paradigm ceiling" in unsupported
    assert "production LLM-agent end-to-end latency" in unsupported

    figures = {figure["id"]: figure for figure in manifest["figures"]}
    assert set(figures) == {
        "fig1_main_metrics",
        "fig2_controlled_ablation",
        "fig3_backend_health",
        "fig4_performance",
    }
    for figure in figures.values():
        assert figure["claim"]
        for key in ("svg", "pdf", "png", "source_data"):
            assert figure["outputs"][key]

    table_labels = set(manifest["tables"]["labels"])
    assert "tab:main_completed" in table_labels
    assert "tab:performance_time_to_detect" in table_labels

    command_ids = {item["id"] for item in manifest["reproduction_commands"]}
    assert "submission_audit" in command_ids
    assert "submission_checksums" in command_ids
    assert "submission_gate" in command_ids
    assert "controlled_ablation" in command_ids
    assert "backend_capture_health" in command_ids
    assert "performance" in command_ids
    assert "trace_redaction_audit" in command_ids
    assert "annotation_package" in command_ids


def test_submission_manifest_tracks_release_materials_without_overclaiming() -> None:
    manifest = build_manifest()

    data_availability = manifest["data_availability"]
    code_availability = manifest["code_availability"]

    assert data_availability["status"] == "minimum_dataset_and_result_package_hash_bound_with_author_placeholders"
    assert code_availability["status"] == "reproducibility_code_hash_bound_archive_doi_required"
    assert "MIT" in code_availability["license"]

    release_materials = data_availability["release_materials"]
    assert release_materials == {
        "annotation_adjudication_template": (
            "compare/results/paper/release/ANNOTATION_ADJUDICATION_TEMPLATE.md"
        ),
        "author_submission_actions": "compare/results/paper/release/AUTHOR_SUBMISSION_ACTIONS.md",
        "availability_statements": "compare/results/paper/release/AVAILABILITY_STATEMENTS.md",
        "data_dictionary": "compare/results/paper/release/DATA_DICTIONARY.md",
        "datacite_draft": "compare/results/paper/release/DATACITE_DRAFT.json",
        "repository_deposit_checklist": (
            "compare/results/paper/release/REPOSITORY_DEPOSIT_CHECKLIST.md"
        ),
        "trace_redaction_audit": "compare/results/paper/release/TRACE_REDACTION_AUDIT.json",
        "trace_redaction_policy": "compare/results/paper/release/TRACE_REDACTION_POLICY.md",
    }
    assert data_availability["checksum_sidecar"] == "compare/results/paper/release/CHECKSUMS.sha256"

    required_actions = set(data_availability["required_author_actions"])
    assert "Create public DOI or anonymous reviewer link." in required_actions
    assert (
        "Publish only redacted responsibility databases or use a controlled-access route because the archived trace redaction audit blocks public release of raw responsibility.db files."
        in required_actions
    )
    assert (
        "Complete locked annotator files, disagreement adjudication, and metrics in the prepared annotation package before claiming blind annotation quality."
        in required_actions
    )

    annotation = manifest["annotation_availability"]
    assert annotation["status"] in {"package_prepared_annotation_not_completed", "package_completed_annotation_study_finished"}
    assert annotation["sample_count"] == 67
    assert annotation["claim_boundary"] in {"blind_annotation_quality_not_claimed", "blind_annotation_quality_claimed"}
    assert "restricted sidecar" in annotation["private_key"]
