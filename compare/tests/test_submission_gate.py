from __future__ import annotations

from pathlib import Path

from compare.scripts.run.run_submission_package_gate import (
    scan_stale_claims,
    run_submission_gate,
)


def test_submission_gate_blocks_missing_evidence_without_pytest(tmp_path: Path) -> None:
    output = tmp_path / "SUBMISSION_GATE.json"

    artifact = run_submission_gate(
        output_path=output,
        regenerate=True,
        run_tests=False,
    )

    assert artifact["schema"] == "mtagentlens.submission_package_gate.v1"
    assert artifact["status"] == "blocked"
    assert artifact["readiness"]["overall_status"] == "blocked"
    assert artifact["submission_audit"]["overall_status"] == "blocked"
    assert artifact["stale_claim_scan"]["status"] == "blocker"
    assert artifact["stale_claim_scan"]["matches"] == []
    assert artifact["pytest"]["status"] == "skipped"
    assert output.exists()


def test_stale_claim_scan_covers_manuscript_entrypoints() -> None:
    artifact = scan_stale_claims()

    assert artifact["status"] == "blocker"
    assert artifact["missing_paths"]
    assert artifact["matches"] == []
    scanned = set(artifact["scanned_paths"])
    assert "compare/results/paper/README.md" in scanned
    assert "compare/results/paper/MANUSCRIPT_EXPERIMENT_APPENDIX.md" in scanned
    assert "compare/results/paper/REPRODUCIBILITY.md" in scanned
    assert "compare/docs/paper-experiment-quality-dossier-v3-2026-06-09.md" in scanned
