from __future__ import annotations

from pathlib import Path

from compare.scripts.build.build_submission_checksums import build_checksums


def test_submission_checksums_match_manifest_hash_bound_files(tmp_path: Path) -> None:
    output = tmp_path / "CHECKSUMS.sha256"

    artifact = build_checksums(output_path=output)

    assert artifact["schema"] == "mtagentprism.submission_checksums.v1"
    assert artifact["status"] == "completed"
    assert artifact["file_count"] >= 50
    assert artifact["output_path"] == str(output)
    assert artifact["hash_source"] == "compare/results/paper/submission_manifest.json"

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == artifact["file_count"]
    assert all("  " in line for line in lines)
    assert any(line.endswith("compare/results/paper/evaluation_report.json") for line in lines)
    assert not any(line.endswith("compare/results/paper/release/CHECKSUMS.sha256") for line in lines)
    assert not any(line.endswith("compare/results/paper/SUBMISSION_AUDIT.json") for line in lines)
