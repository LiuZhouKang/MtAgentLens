# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Contract tests for the AblationBench metrics producer.

Synthetic exports are built from the frozen exporter contract
(collector/src/responsibility/replay/ablation.rs); they are not experimental
results and the tests never claim an effect.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from scripts.evaluation import ablation_experiment as producer
from scripts.evaluation import experiment_suite_audit as suite

LAYERS = producer.LAYERS
COHORT = producer.EXPECTED_COHORT


def _labels() -> dict[str, str]:
    return {f"scenario-{index:03d}": ("attack" if index < COHORT["attack"] else "benign")
            for index in range(COHORT["scenarios"])}


def _scores(seed: int = 7) -> dict[str, dict[str, float]]:
    rng = np.random.default_rng(seed)
    values = {}
    for scenario, label in _labels().items():
        noise = rng.random()
        values[scenario] = {
            "L0": noise,
            "L0.5": noise + (0.3 if label == "attack" else 0.0),
            "L1": noise + (0.6 if label == "attack" else 0.0),
            "L2": noise + (0.8 if label == "attack" else 0.0),
            "L3": noise + (1.0 if label == "attack" else 0.0),
        }
    return values


def _layer_payload(name: str, score: float, **overrides) -> dict:
    payload = {
        "layer": name,
        "state": "available",
        "score_kind": "continuous",
        "ranking_score": score,
        "score_definition": ("raw-activity-index:raw-os-action-occurrences"
                             if name == "L0" else "v9-six-dimension-fusion+platt+v2-gate"),
    }
    payload.update(overrides)
    return payload


def _export(name: str, scores: dict, **layer_overrides) -> dict:
    layers = [_layer_payload(layer, scores[layer],
                             **layer_overrides.get(layer, {})) for layer in LAYERS]
    return {
        "schema": producer.ABLATION_SCHEMA,
        "protocol": producer.PROTOCOL,
        "current_fingerprint": "f" * 64,
        "runs": [{
            "run_id": f"run-{name}",
            "read_checkpoint_graph": False,
            "read_checkpoint_identities": False,
            "layers": layers,
        }],
    }


def _collected(scores: dict | None = None) -> dict:
    """The collected form is the run report, exactly what collect() returns."""
    scores = scores or _scores()
    return {name: _export(name, values)["runs"][0] for name, values in scores.items()}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_full_staircase_produces_an_estimable_envelope():
    envelope = producer.build_metrics(_collected(), _labels(), "r" * 64, resamples=2000)
    assert envelope["schema"] == producer.METRICS_SCHEMA
    assert envelope["experiment"] == "ablation"
    assert envelope["estimable"] is True
    assert envelope["cohort"] == COHORT
    data = envelope["data"]
    assert data["state"] == "available"
    assert data["raw_reconstruction"] is True
    assert data["hard_sensitive_egress"] is False
    assert data["shared_thresholds"] is True
    assert set(data["layers"]) == set(LAYERS)
    # The fixture is monotone by construction; this is a contract check, not a result.
    assert data["layers"]["L0"]["auc"] < data["layers"]["L3"]["auc"]
    assert data["layers"]["L0"]["delta_vs_L0"] == 0.0
    stats = data["statistics"]
    assert stats["aggregation_unit"] == "scenario"
    assert stats["n_scenarios"] == COHORT["scenarios"]
    assert stats["bootstrap_resamples"] == 2000
    assert stats["holm_family"] == [f"{curr}_vs_{prev}_auc"
                                    for curr, prev in producer.CONTRASTS]
    assert set(stats["holm_adjusted_p"]) == set(stats["holm_family"])
    for interval in stats["confidence_intervals"].values():
        assert len(interval) == 2 and interval[0] <= interval[1]


def test_envelope_passes_the_suite_audit(tmp_path):
    labels = _labels()
    envelope = producer.build_metrics(_collected(), labels, "r" * 64, resamples=1000)
    producer_path = tmp_path / "producer.json"
    export_path = tmp_path / "export.json"
    metrics_path = tmp_path / "metrics.json"
    producer_path.write_text(json.dumps({"schema": "test/producer"}))
    export_path.write_text(json.dumps({"schema": "test/export"}))
    metrics_path.write_text(json.dumps(envelope))
    manifest = {
        "schema": suite.MANIFEST_SCHEMA,
        "release_id": "r" * 64,
        "cohort": dict(COHORT),
        "artifacts": [
            {"id": "ablation-producer", "experiment": "ablation", "stage": "producer",
             "path": producer_path.name, "sha256": _sha(producer_path)},
            {"id": "ablation-export", "experiment": "ablation", "stage": "export",
             "path": export_path.name, "sha256": _sha(export_path)},
            {"id": "ablation-metrics", "experiment": "ablation", "stage": "metrics",
             "path": metrics_path.name, "sha256": _sha(metrics_path),
             "schema": producer.METRICS_SCHEMA},
        ],
    }
    audit = suite.audit_manifest(manifest, base_dir=tmp_path)
    result = audit["experiments"]["ablation"]
    assert audit["valid"] is True
    assert result["status"] == "estimable", result["reasons"]


def test_l0_uses_the_ranking_index_not_the_kernel_score():
    scores = _scores()
    exports = _collected(scores)
    baseline = producer.build_metrics(exports, _labels(), "r" * 64, resamples=1000)
    # A kernel score is metadata for L0; changing it must not move the AUROC.
    for run in exports.values():
        run["layers"][0]["score"] = {"total": 0.999}
    assert producer.build_metrics(
        exports, _labels(), "r" * 64, resamples=1000)["data"]["layers"] == baseline["data"]["layers"]


@pytest.mark.parametrize("mutation,message", [
    ("schema", "schema"),
    ("protocol", "protocol"),
    ("graph", "frozen graph"),
    ("identities", "frozen identities"),
    ("state", "not available"),
    ("score_kind", "score_kind"),
    ("ranking_score", "ranking_score"),
    ("l0_definition", "definition"),
    ("missing_layer", "five layer"),
    ("duplicate_layer", "duplicate"),
    ("two_runs", "exactly one run"),
])
def test_export_contract_violations_are_refused(mutation, message):
    payload = _export("scenario-000", _scores()["scenario-000"])
    run = payload["runs"][0]
    if mutation == "schema":
        payload["schema"] = "other"
    elif mutation == "protocol":
        payload["protocol"] = "other"
    elif mutation == "graph":
        run["read_checkpoint_graph"] = True
    elif mutation == "identities":
        run["read_checkpoint_identities"] = True
    elif mutation == "state":
        run["layers"][0]["state"] = "partial"
    elif mutation == "score_kind":
        run["layers"][0]["score_kind"] = "binary"
    elif mutation == "ranking_score":
        run["layers"][0]["ranking_score"] = None
    elif mutation == "l0_definition":
        run["layers"][0]["score_definition"] = "other"
    elif mutation == "missing_layer":
        run["layers"].pop()
    elif mutation == "duplicate_layer":
        run["layers"].append(dict(run["layers"][0]))
    elif mutation == "two_runs":
        payload["runs"].append(dict(payload["runs"][0]))
    with pytest.raises(producer.ContractError, match=message):
        producer._require_run(payload, "scenario-000")


def test_collect_rejects_unknown_duplicate_and_missing_scenarios(tmp_path):
    labels = _labels()
    exports = tmp_path / "exports"
    exports.mkdir()
    scores = _scores()
    for name, values in scores.items():
        (exports / f"{name}.json").write_text(json.dumps(_export(name, values)))
    assert set(producer.collect(exports, labels)) == set(labels)

    (exports / "unknown.json").write_text(json.dumps(_export("unknown", scores["scenario-000"])))
    with pytest.raises(producer.ContractError, match="not in the release"):
        producer.collect(exports, labels)
    (exports / "unknown.json").unlink()

    data = producer.collect(exports, labels)
    data["scenario-999"] = data["scenario-000"]
    with pytest.raises(producer.ContractError, match="unknown scenario"):
        producer.build_metrics(data, labels, "r" * 64)

    with pytest.raises(producer.ContractError, match="missing ablation exports"):
        producer.build_metrics({"scenario-000": producer.collect(exports, labels)["scenario-000"]},
                               labels, "r" * 64)


def test_partial_layer_makes_the_envelope_explicitly_not_estimable():
    exports = _collected()
    exports["scenario-001"]["layers"][2]["state"] = "partial"
    with pytest.raises(producer.ContractError, match="not available"):
        producer.build_metrics(exports, _labels(), "r" * 64, resamples=1000)
    envelope = producer.not_estimable("r" * 64, "scenario-001: L1 state is 'partial'")
    assert envelope["estimable"] is False
    assert envelope["data"]["state"] == "unavailable"


def test_output_is_deterministic_and_bootstrap_seed_is_frozen():
    first = producer.build_metrics(_collected(), _labels(), "r" * 64, resamples=1000)
    second = producer.build_metrics(_collected(), _labels(), "r" * 64, resamples=1000)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["data"]["statistics"]["bootstrap_seed"] == producer.BOOTSTRAP_SEED


def test_cli_refuses_to_overwrite_an_existing_metrics_file(tmp_path):
    out = tmp_path / "metrics.json"
    out.write_text("{}")
    code = producer.main(["--release", str(tmp_path / "release"),
                          "--exports", str(tmp_path / "exports"), "--out", str(out)])
    assert code == 2
    assert out.read_text() == "{}"
