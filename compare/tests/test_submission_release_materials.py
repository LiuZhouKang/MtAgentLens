from __future__ import annotations

import json
from pathlib import Path


RELEASE_DIR = Path("compare/results/paper/release")


def test_author_submission_actions_collect_external_fields_without_overclaiming() -> None:
    text = (RELEASE_DIR / "AUTHOR_SUBMISSION_ACTIONS.md").read_text(encoding="utf-8")

    assert "Public DOI/accession" in text
    assert "Anonymous reviewer link" in text
    assert "ORCID" in text
    assert "restricted trace access terms" in text
    assert "controlled reviewer-access link" in text
    assert "Archive a clean tagged commit" in text
    assert "Archive the current dirty diff/patch" in text
    assert "Do not add any of these claims" in text
    assert "blind independent annotation reliability" in text
    assert "raw SQLite trace database public-release readiness" in text


def test_availability_statements_choose_public_processed_data_and_restricted_raw_trace_route() -> None:
    text = (RELEASE_DIR / "AVAILABILITY_STATEMENTS.md").read_text(encoding="utf-8")
    flat_text = " ".join(text.split())

    assert "Data Availability" in text
    assert "Code Availability" in text
    assert "processed data and public benchmark materials" in text
    assert "submission_manifest.json" in text
    assert "CHECKSUMS.sha256" in text
    assert "Unredacted SQLite responsibility databases" in text
    assert "are not included in the public archive" in flat_text
    assert "controlled reviewer-access route" in text
    assert "TRACE_REDACTION_AUDIT.json" in text
    assert "TRACE_REDACTION_POLICY.md" in text
    assert "[available in redacted form / available to reviewers through a controlled-access route / excluded" not in text
    assert "run_submission_package_gate.py" in text
    assert "not production end-to-end LLM-agent latency" in flat_text


def test_datacite_draft_has_required_fields_and_author_placeholders() -> None:
    draft = json.loads((RELEASE_DIR / "DATACITE_DRAFT.json").read_text(encoding="utf-8"))

    assert draft["schema"] == "mtagentlens.release_datacite_draft.v1"
    assert draft["status"] == "prepared_with_author_placeholders"
    assert draft["identifier"]["identifier"] == "TO_BE_ASSIGNED_BY_REPOSITORY"
    assert draft["identifier"]["identifierType"] == "DOI"
    assert draft["creators"]
    assert draft["titles"][0]["title"]
    assert draft["publisher"] == "TO_BE_ASSIGNED_BY_REPOSITORY"
    assert draft["publicationYear"] == "2026"
    assert draft["resourceType"]["resourceTypeGeneral"] == "Dataset"
    assert "version" in draft
    assert draft["descriptions"]
    assert draft["rightsList"]

    unresolved = set(draft["unresolved_author_fields"])
    assert "repository DOI or accession" in unresolved
    assert "anonymous reviewer link" in unresolved
    assert "final dataset rights and trace access terms" in unresolved


def test_data_dictionary_maps_artifacts_and_non_claims() -> None:
    text = (RELEASE_DIR / "DATA_DICTIONARY.md").read_text(encoding="utf-8")

    assert "67-scenario frozen offline evaluation" in text
    assert "compare/results/paper/submission_manifest.json" in text
    assert "availability statements" in text
    assert "compare/Dataset/FuxiBench/traces/**/responsibility.db" in text
    assert "controlled reviewer access" in text
    assert "external benchmark validation" in text
    assert "blind independent annotation reliability" in text
    assert "compare/results/paper/source_data/fig1_main_metrics_source_data.json" in text
    assert "CC-BY 4.0" in text
    assert "MIT license" in text


def test_trace_redaction_policy_requires_validation_before_public_trace_release() -> None:
    text = (RELEASE_DIR / "TRACE_REDACTION_POLICY.md").read_text(encoding="utf-8")

    sensitive_terms = [
        "Absolute local paths",
        "Process command lines",
        "Agent message content",
        "Network endpoints",
        "API keys",
        "private keys",
    ]
    for term in sensitive_terms:
        assert term in text

    assert "redaction not yet certified" in text
    assert "must not be treated as publicly" in text
    assert "shareable raw data" in text
    assert "Validation Gate" in text
    assert "PRAGMA integrity_check" in text
    lower_text = text.lower()
    assert "secret scans" in lower_text
    assert "path scans" in lower_text
    assert "reviewer-only or controlled-access route" in text


def test_trace_redaction_audit_blocks_raw_public_trace_release() -> None:
    audit = json.loads((RELEASE_DIR / "TRACE_REDACTION_AUDIT.json").read_text(encoding="utf-8"))

    assert audit["schema"] == "mtagentlens.trace_redaction_audit.v1"
    assert audit["status"] == "blocked_for_public_release"
    assert audit["summary"]["database_count"] == 67
    assert audit["summary"]["integrity_failures"] == 0
    assert audit["summary"]["schema_failures"] == 0
    assert audit["summary"]["local_path_findings"] > 0
    assert audit["summary"]["secret_like_findings"] > 0
    assert audit["summary"]["public_release_ready"] is False
    assert audit["policy"]["release_decision"] == (
        "raw_responsibility_databases_must_remain_restricted_until_redaction_passes"
    )
    assert audit["manual_qa_required"] is True
    assert any(item["kind"] == "local_path" for item in audit["findings"])


def test_annotation_template_blocks_blind_annotation_overclaim() -> None:
    text = (RELEASE_DIR / "ANNOTATION_ADJUDICATION_TEMPLATE.md").read_text(encoding="utf-8")

    assert "independent annotation not yet completed" in text
    assert "not yet support a manuscript claim of blind independent human" in text
    assert "annotation reliability" in text
    assert "Cohen's kappa" in text
    assert "Binary label kappa >= 0.80" in text
    assert "100% of disagreements adjudicated" in text
    assert "We do not claim" in text
    assert "blind independent annotation reliability" in text


def test_prepared_annotation_package_keeps_blinding_and_nonclaim_boundary() -> None:
    package_dir = RELEASE_DIR / "annotation_package"
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    metrics = json.loads((package_dir / "metrics.json").read_text(encoding="utf-8"))
    first_sample = json.loads((package_dir / "blinded_samples.jsonl").read_text(encoding="utf-8").splitlines()[0])

    assert manifest["schema"] == "mtagentlens.annotation_package_manifest.v1"
    assert manifest["status"] in {"prepared_for_independent_annotation", "completed"}
    assert manifest["sample_count"] == 67
    assert manifest["claim_status"] in {"non_claim_until_completed", "blind_annotation_quality_claimed"}
    assert manifest["blinding"]["scenario_ids_hidden"] is True
    assert manifest["blinding"]["labels_hidden"] is True
    assert "scenario_id" not in first_sample
    assert "label" not in first_sample
    assert "attack_type" not in first_sample
    # Annotation study may be completed or prepared
    assert metrics["status"] in {"not_computed", "completed"}
    assert metrics["claim_boundary"] in {"blind_annotation_quality_not_claimed", "blind_annotation_quality_claimed"}
    assert "sample_key_private.json" not in {item["path"] for item in manifest["files"]}
    assert manifest["private_sidecars"][0]["path"] == "sample_key_private.json"


def test_repository_deposit_checklist_keeps_placeholders_and_trace_boundary() -> None:
    text = (RELEASE_DIR / "REPOSITORY_DEPOSIT_CHECKLIST.md").read_text(encoding="utf-8")

    assert "Assign a stable repository DOI or accession" in text
    assert "anonymous reviewer link" in text
    assert "rights metadata" in text
    assert "[Repository]" in text
    assert "[DOI/accession]" in text
    assert "processed result artifacts and figure" in text
    assert "controlled-access route" in text
    assert "TRACE_REDACTION_AUDIT.json" in text
    assert "not used to support the main claims" in text
