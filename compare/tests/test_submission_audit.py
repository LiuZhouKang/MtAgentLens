import json

from compare.scripts.audit.audit_submission_package import audit_submission_package


def test_submission_audit_rejects_missing_or_empty_manifest(tmp_path):
    manifest = tmp_path / "manifest.json"
    missing = audit_submission_package(manifest_path=manifest)
    assert missing["overall_status"] == "blocked"
    manifest.write_text(json.dumps({"schema": "mtagentlens.paper_submission_package.v1", "status": "completed", "files": []}))
    empty = audit_submission_package(manifest_path=manifest)
    assert empty["overall_status"] == "blocked"
    checks = {row["id"]: row for row in empty["checks"]}
    assert checks["manifest_hashes"]["status"] == "blocker"
    assert checks["readiness_gate"]["status"] == "blocker"
