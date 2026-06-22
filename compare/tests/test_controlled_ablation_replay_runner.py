from __future__ import annotations

import json
import subprocess
from pathlib import Path

from compare.scripts.run.run_controlled_ablation_replay import (
    compute_metrics,
    parse_replay_output,
    run_controlled_ablation_replay,
)


REPLAY_OUTPUT = """
=== Replay via PersistenceReader ===
Evidence produced: 64
Alerts: 1
Identities: 5
Claims: 1
Tracebacks: 1
Phase1 alerts: 2
Phase2 alerts: 1
Confirmed: 0
Changed: 0
Dismissed: 2
Discovered: 1
Precision: 0.000
Recall: 0.000
F1: 0.000
Graph nodes: 20
Graph edges: 76
mode=controlled_replay
status=completed
"""


def test_parse_replay_output_extracts_counts_and_status() -> None:
    parsed = parse_replay_output(REPLAY_OUTPUT)

    assert parsed["evidence_count"] == 64
    assert parsed["alert_count"] == 1
    assert parsed["claim_count"] == 1
    assert parsed["graph_edge_count"] == 76
    assert parsed["mode"] == "controlled_replay"
    assert parsed["status"] == "completed"


def test_compute_metrics_uses_claim_count_detection() -> None:
    outcomes = [
        {
            "status": "completed",
            "is_attack": True,
            "detected": True,
            "expected_claims": 1,
            "matched_claims": 1,
            "claim_count": 1,
            "alert_count": 1,
            "evidence_count": 2,
        },
        {
            "status": "completed",
            "is_attack": False,
            "detected": False,
            "expected_claims": 0,
            "matched_claims": 0,
            "claim_count": 0,
            "alert_count": 0,
            "evidence_count": 1,
        },
    ]

    metrics = compute_metrics(outcomes)

    assert metrics["precision"] == 1.0
    assert metrics["scenario_recall"] == 1.0
    assert metrics["claim_coverage"] == 1.0
    assert metrics["true_positive"] == 1
    assert metrics["true_negative"] == 1


def test_runner_emits_completed_variant_input_with_mocked_replay(
    tmp_path: Path, monkeypatch
) -> None:
    dataset = _fake_dataset(tmp_path)
    output = tmp_path / "variant_runs.json"

    def fake_run(command, **kwargs):  # noqa: ANN001
        assert "--run-dir" in command
        assert "--persisted-graph-replay" in command
        scenario_id = Path(command[command.index("--run-dir") + 1]).name
        claims = 0 if scenario_id == "benign-api-doc" else 1
        stdout = REPLAY_OUTPUT.replace("Claims: 1", f"Claims: {claims}")
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    artifact = run_controlled_ablation_replay(
        dataset_dir=dataset,
        output_path=output,
        replay_bin=Path("/tmp/replay"),
        build_replay=False,
        strict=False,
    )

    assert artifact["status"] == "completed"
    assert artifact["baseline"]["parity"]["status"] == "pass"
    assert artifact["baseline"]["runs_completed"] == 2
    assert len(artifact["variants"]) == 9
    assert artifact["methodology"] == "sqlite_persisted_graph_action_family_feature_flags"
    assert all(variant["runs_completed"] == 2 for variant in artifact["variants"])
    assert output.exists()


def test_runner_blocks_when_baseline_has_false_positive(tmp_path: Path, monkeypatch) -> None:
    dataset = _fake_dataset(tmp_path)

    def fake_run(command, **kwargs):  # noqa: ANN001
        return subprocess.CompletedProcess(command, 0, stdout=REPLAY_OUTPUT, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    artifact = run_controlled_ablation_replay(
        dataset_dir=dataset,
        output_path=tmp_path / "variant_runs.json",
        replay_bin=Path("/tmp/replay"),
        build_replay=False,
        strict=False,
    )

    assert artifact["status"] == "blocked"
    assert artifact["baseline"]["parity"]["status"] == "fail"
    assert "precision != 1.0" in artifact["baseline"]["parity"]["problems"]


def _fake_dataset(tmp_path: Path) -> Path:
    dataset = tmp_path / "dataset"
    quality = dataset / "quality"
    quality.mkdir(parents=True)
    samples = [
        _sample("atlas-recon-discovery", label="attack", expected_claims=1),
        _sample("benign-api-doc", label="benign", expected_claims=0),
    ]
    (quality / "manifest.json").write_text(
        json.dumps({"samples": samples}) + "\n", encoding="utf-8"
    )
    for scenario_id in ("atlas-recon-discovery", "benign-api-doc"):
        trace_dir = dataset / "traces" / "fake_category" / scenario_id
        trace_dir.mkdir(parents=True)
        (trace_dir / "responsibility.db").write_text("", encoding="utf-8")
    return dataset


def _sample(scenario_id: str, *, label: str, expected_claims: int) -> dict[str, object]:
    return {
        "scenario_id": scenario_id,
        "category": "fake",
        "ground_truth_summary": {
            "label": label,
            "attack_type": "benign" if label == "benign" else "B",
            "expected_claims_count": expected_claims,
        },
    }
