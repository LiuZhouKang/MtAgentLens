from __future__ import annotations

import json
from pathlib import Path

from compare.scripts.audit.audit_submission_package import audit_submission_package
from compare.scripts.build.build_submission_checksums import build_checksums
from compare.scripts.build.build_submission_package_manifest import build_manifest


def _refresh_submission_sidecars() -> None:
    manifest_path = Path("compare/results/paper/submission_manifest.json")
    manifest_path.write_text(
        json.dumps(build_manifest(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    build_checksums()


def test_submission_audit_accepts_current_ready_package_with_bounded_release_status() -> None:
    _refresh_submission_sidecars()
    artifact = audit_submission_package()

    assert artifact["schema"] == "mtagentprism.submission_package_audit.v1"
    assert artifact["overall_status"] == "ready_with_external_author_actions"
    assert artifact["summary"]["pass"] >= 8
    assert artifact["summary"].get("blocker", 0) == 0
    assert artifact["manifest"]["file_count"] >= 400
    assert artifact["manifest"]["hash_mismatches"] == []
    assert artifact["readiness"]["overall_status"] == "ready"

    checks = {check["id"]: check for check in artifact["checks"]}
    assert checks["manifest_hashes"]["status"] == "pass"
    assert checks["checksum_sidecar"]["status"] == "pass"
    assert checks["canonical_figures"]["status"] == "pass"
    assert checks["manuscript_experiment_appendix"]["status"] == "pass"
    assert checks["release_materials"]["status"] == "pass"
    assert checks["public_dataset_scope"]["status"] == "pass"
    assert checks["code_reproducibility_scope"]["status"] == "pass"
    assert checks["trace_redaction_boundary"]["status"] == "pass"
    assert checks["annotation_boundary"]["status"] == "pass"
    assert checks["private_annotation_key_boundary"]["status"] == "pass"
    assert checks["claim_boundaries"]["status"] == "pass"

    assert checks["trace_redaction_boundary"]["evidence"]["trace_public_release_ready"] is False
    assert checks["annotation_boundary"]["evidence"]["claim_boundary"] in {"blind_annotation_quality_not_claimed", "blind_annotation_quality_claimed"}
    assert "external benchmark validation" in checks["claim_boundaries"]["evidence"]["unsupported_claims"]
