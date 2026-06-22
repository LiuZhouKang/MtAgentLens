import gc
import json
from pathlib import Path

import pytest

from compare.tools.base import BoundaryType
from compare.tools.datasets import FrozenDatasetAdapter, load_dataset


DATASET_ROOT = Path("compare/Dataset/FuxiBench")


def test_frozen_dataset_loads_all_ground_truth_cases():
    adapter = FrozenDatasetAdapter(DATASET_ROOT)

    cases = adapter.load_cases()

    assert len(cases) == 67
    assert {case.adapter_case.scenario_id for case in cases} >= {
        "prompt-injection",
        "shadow-agent",
        "network-anomaly",
    }
    assert sum(1 for case in cases if case.is_attack) == 54
    assert sum(1 for case in cases if not case.is_attack) == 13
    assert all("responsibility_db" in case.mtagentprism_baseline for case in cases)


def test_adapter_case_excludes_ground_truth_and_mtagentprism_baseline_paths():
    adapter = FrozenDatasetAdapter(DATASET_ROOT)
    case = next(
        case for case in adapter.load_cases()
        if case.adapter_case.scenario_id == "prompt-injection"
    )

    payload = case.adapter_case.to_adapter_payload()
    serialized = repr(payload)

    assert "ground_truth" not in serialized
    assert "responsibility.db" not in serialized
    assert "responsibility_claims" not in serialized
    assert "responsibility_alerts" not in serialized
    assert case.ground_truth_json is not None
    assert "responsibility_db" in case.mtagentprism_baseline


def test_quality_manifest_nested_paths_are_loaded_without_exposing_baseline():
    adapter = FrozenDatasetAdapter(DATASET_ROOT)
    case = next(
        case for case in adapter.load_cases()
        if case.adapter_case.scenario_id == "atlas-recon-discovery"
    )

    payload = case.adapter_case.to_adapter_payload()
    serialized_payload = repr(payload)

    assert case.ground_truth_json == (
        DATASET_ROOT / "ground_truth" / "reconnaissance" / "atlas-recon-discovery.json"
    ).resolve()
    assert (
        case.mtagentprism_baseline["responsibility_db"]
        == (
            DATASET_ROOT / "traces" / "reconnaissance" / "atlas-recon-discovery" / "responsibility.db"
        ).resolve()
    )
    assert "ground_truth" not in serialized_payload
    assert "responsibility.db" not in serialized_payload
    assert case.is_attack


def test_gt_v2_structured_claims_are_split_into_matchable_substrings(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    generated_root = tmp_path / "runtime-generated"
    for child in ("scenarios", "ground_truth", "fixtures", "traces", "quality"):
        (dataset_dir / child).mkdir(parents=True)
    scenario_id = "structured-claim"
    (dataset_dir / "scenarios" / f"{scenario_id}.json").write_text(
        json.dumps({"id": scenario_id}),
        encoding="utf-8",
    )
    (dataset_dir / "ground_truth" / f"{scenario_id}.json").write_text(
        json.dumps(
            {
                "scenario_id": scenario_id,
                "label": "attack",
                "category": "defense_evasion",
                "attack_type": "A",
                "execution_status": "executed",
                "expected_behavior": {
                    "should_alert": True,
                    "min_decision": "Uncertain",
                    "expected_claims": [
                        "claim.TaintedContextUse.policy.content.defense_evasion.boundary.content.defense_evasion.Info"
                    ],
                },
                "evidence_contract": {"db_required": True},
            }
        ),
        encoding="utf-8",
    )
    (dataset_dir / "quality" / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "mtagentprism.dataset_quality.v1",
                "samples": [
                    {
                        "scenario_id": scenario_id,
                        "category": "defense_evasion",
                        "quality_tier": "paper_ready",
                        "paths": {"ground_truth": f"ground_truth/{scenario_id}.json"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    case = FrozenDatasetAdapter(dataset_dir, generated_root=generated_root).load_cases()[0]
    claim = case.expected_claims[0]

    assert claim.severity == "Info"
    assert claim.boundary_type == BoundaryType.CONTENT
    assert "TaintedContextUse" in claim.content_substrings
    assert "policy.content.defense_evasion" in claim.content_substrings
    assert "boundary.content.defense_evasion" in claim.content_substrings


def test_gt_v2_structured_resource_claim_uses_claim_severity_and_boundary_segment(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    generated_root = tmp_path / "runtime-generated"
    for child in ("scenarios", "ground_truth", "fixtures", "traces", "quality"):
        (dataset_dir / child).mkdir(parents=True)
    scenario_id = "resource-claim"
    (dataset_dir / "scenarios" / f"{scenario_id}.json").write_text(
        json.dumps({"id": scenario_id}),
        encoding="utf-8",
    )
    (dataset_dir / "ground_truth" / f"{scenario_id}.json").write_text(
        json.dumps(
            {
                "scenario_id": scenario_id,
                "label": "attack",
                "category": "reconnaissance",
                "attack_type": "B",
                "execution_status": "executed",
                "expected_behavior": {
                    "should_alert": True,
                    "min_decision": "Unsafe",
                    "expected_claims": [
                        "claim.PolicyViolation.policy.file.read.boundary.file_read.Warning"
                    ],
                    "expected_syscalls": [{"operation": "file_read", "path": "/etc/passwd"}],
                },
                "evidence_contract": {"db_required": True},
            }
        ),
        encoding="utf-8",
    )
    (dataset_dir / "quality" / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "mtagentprism.dataset_quality.v1",
                "samples": [
                    {
                        "scenario_id": scenario_id,
                        "category": "reconnaissance",
                        "quality_tier": "paper_ready",
                        "paths": {"ground_truth": f"ground_truth/{scenario_id}.json"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    case = FrozenDatasetAdapter(dataset_dir, generated_root=generated_root).load_cases()[0]

    assert len(case.expected_claims) == 1
    claim = case.expected_claims[0]
    assert claim.severity == "Warning"
    assert claim.boundary_type == BoundaryType.RESOURCE
    assert "boundary.file_read" in claim.resource_substrings
    assert "boundary.file_read.Warning" not in claim.resource_substrings


def test_gt_v2_role_content_claim_is_delegation_boundary(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    generated_root = tmp_path / "runtime-generated"
    for child in ("scenarios", "ground_truth", "fixtures", "traces", "quality"):
        (dataset_dir / child).mkdir(parents=True)
    scenario_id = "role-claim"
    (dataset_dir / "scenarios" / f"{scenario_id}.json").write_text(
        json.dumps({"id": scenario_id}),
        encoding="utf-8",
    )
    (dataset_dir / "ground_truth" / f"{scenario_id}.json").write_text(
        json.dumps(
            {
                "scenario_id": scenario_id,
                "label": "attack",
                "category": "agent_teams",
                "attack_type": "C",
                "execution_status": "executed",
                "expected_behavior": {
                    "should_alert": True,
                    "min_decision": "Uncertain",
                    "expected_claims": [
                        "claim.TaintedContextUse.policy.content.role_hijack.boundary.content.role_hijack.Info"
                    ],
                },
                "evidence_contract": {"db_required": True},
            }
        ),
        encoding="utf-8",
    )
    (dataset_dir / "quality" / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "mtagentprism.dataset_quality.v1",
                "samples": [
                    {
                        "scenario_id": scenario_id,
                        "category": "agent_teams",
                        "quality_tier": "paper_ready",
                        "paths": {"ground_truth": f"ground_truth/{scenario_id}.json"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    case = FrozenDatasetAdapter(dataset_dir, generated_root=generated_root).load_cases()[0]

    assert case.expected_claims[0].boundary_type == BoundaryType.DELEGATION


def test_gt_v2_taint_claim_is_resource_boundary(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    generated_root = tmp_path / "runtime-generated"
    for child in ("scenarios", "ground_truth", "fixtures", "traces", "quality"):
        (dataset_dir / child).mkdir(parents=True)
    scenario_id = "taint-claim"
    (dataset_dir / "scenarios" / f"{scenario_id}.json").write_text(
        json.dumps({"id": scenario_id}),
        encoding="utf-8",
    )
    (dataset_dir / "ground_truth" / f"{scenario_id}.json").write_text(
        json.dumps(
            {
                "scenario_id": scenario_id,
                "label": "attack",
                "category": "agent_teams",
                "attack_type": "C",
                "execution_status": "executed",
                "expected_behavior": {
                    "should_alert": True,
                    "min_decision": "Uncertain",
                    "expected_claims": [
                        "claim.PolicyViolation.policy.taint.boundary.taint.Info"
                    ],
                },
                "evidence_contract": {"db_required": True},
            }
        ),
        encoding="utf-8",
    )
    (dataset_dir / "quality" / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "mtagentprism.dataset_quality.v1",
                "samples": [
                    {
                        "scenario_id": scenario_id,
                        "category": "agent_teams",
                        "quality_tier": "paper_ready",
                        "paths": {"ground_truth": f"ground_truth/{scenario_id}.json"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    case = FrozenDatasetAdapter(dataset_dir, generated_root=generated_root).load_cases()[0]

    assert case.expected_claims[0].boundary_type == BoundaryType.RESOURCE


def test_adapter_case_only_exposes_comparator_visible_artifacts():
    adapter = FrozenDatasetAdapter(DATASET_ROOT)
    case = next(
        case for case in adapter.load_cases()
        if case.adapter_case.scenario_id == "prompt-injection"
    )

    adapter_case = case.adapter_case

    assert set(adapter_case.input_artifacts) == {"scenario_json", "prompt_text"}
    assert adapter_case.fixture_dir is None
    assert adapter_case.saved_logs == ()
    assert all("ground_truth" not in str(path) for path in adapter_case.input_artifacts.values())
    assert all("traces" not in str(path) for path in adapter_case.input_artifacts.values())
    assert "responsibility_db" in case.mtagentprism_baseline
    assert case.mtagentprism_baseline["responsibility_db"].name == "responsibility.db"


def test_load_cases_materializes_generated_scenario_json_outside_dataset_when_source_missing(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    (dataset_dir / "scenarios").mkdir(parents=True)
    (dataset_dir / "ground_truth").mkdir()
    (dataset_dir / "fixtures").mkdir()
    (dataset_dir / "traces").mkdir()
    (dataset_dir / "metadata.json").write_text(
        json.dumps({"scenario_ids": ["missing-scenario"]}),
        encoding="utf-8",
    )
    (dataset_dir / "ground_truth" / "missing-scenario.json").write_text(
        json.dumps({"alerts": [{"alert_id": "a1", "severity": "High", "category": "test"}]}),
        encoding="utf-8",
    )

    adapter = FrozenDatasetAdapter(dataset_dir)

    cases = adapter.load_cases()

    assert len(cases) == 1
    adapter_case = cases[0].adapter_case
    assert adapter_case.scenario_json.exists()
    assert not adapter_case.scenario_json.resolve().is_relative_to(dataset_dir.resolve())
    payload = json.loads(adapter_case.scenario_json.read_text(encoding="utf-8"))
    assert payload["id"] == "missing-scenario"
    serialized = repr(adapter_case.to_adapter_payload())
    assert "ground_truth" not in serialized
    assert "traces" not in serialized


def test_load_cases_rejects_explicit_generated_root_inside_dataset(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    generated_root = dataset_dir / "generated"

    with pytest.raises(ValueError, match="generated_root.*dataset_dir"):
        FrozenDatasetAdapter(dataset_dir, generated_root=generated_root)


def test_load_cases_keeps_inline_generated_scenario_json_after_adapter_gc(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    (dataset_dir / "scenarios").mkdir(parents=True)
    (dataset_dir / "ground_truth").mkdir()
    (dataset_dir / "fixtures").mkdir()
    (dataset_dir / "traces").mkdir()
    (dataset_dir / "metadata.json").write_text(
        json.dumps({"scenario_ids": ["missing-scenario"]}),
        encoding="utf-8",
    )
    (dataset_dir / "ground_truth" / "missing-scenario.json").write_text(
        json.dumps({"alerts": []}),
        encoding="utf-8",
    )

    cases = FrozenDatasetAdapter(dataset_dir).load_cases()
    gc.collect()

    assert cases[0].adapter_case.scenario_json.exists()


def test_load_cases_uses_explicit_generated_root_for_missing_scenario_json(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    generated_root = tmp_path / "runtime-generated"
    (dataset_dir / "scenarios").mkdir(parents=True)
    (dataset_dir / "ground_truth").mkdir()
    (dataset_dir / "fixtures").mkdir()
    (dataset_dir / "traces").mkdir()
    (dataset_dir / "metadata.json").write_text(
        json.dumps({"scenario_ids": ["missing-scenario"]}),
        encoding="utf-8",
    )
    (dataset_dir / "ground_truth" / "missing-scenario.json").write_text(
        json.dumps({"alerts": []}),
        encoding="utf-8",
    )

    adapter = FrozenDatasetAdapter(dataset_dir, generated_root=generated_root)

    cases = adapter.load_cases()

    scenario_json = cases[0].adapter_case.scenario_json
    assert scenario_json.exists()
    assert scenario_json == generated_root / "tool_inputs" / "scenarios" / "missing-scenario.json"
    payload = json.loads(scenario_json.read_text(encoding="utf-8"))
    assert payload["id"] == "missing-scenario"


def test_fixture_dir_path_traversal_falls_back_without_exposing_ground_truth(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    generated_root = tmp_path / "runtime-generated"
    (dataset_dir / "scenarios").mkdir(parents=True)
    (dataset_dir / "ground_truth").mkdir()
    (dataset_dir / "fixtures").mkdir()
    (dataset_dir / "traces").mkdir()
    (dataset_dir / "metadata.json").write_text(
        json.dumps({"scenario_ids": ["escape-fixture"]}),
        encoding="utf-8",
    )
    (dataset_dir / "scenarios" / "escape-fixture.json").write_text(
        json.dumps(
            {
                "id": "escape-fixture",
                "fixture_dir": "../ground_truth",
                "description": "uses ground_truth traces responsibility alerts",
                "nested": {
                    "responsibility_claims": ["responsibility leak"],
                    "safe": "keep me",
                },
                "steps": [{"alerts": ["alert leak"], "action": "inspect traces"}],
            }
        ),
        encoding="utf-8",
    )
    (dataset_dir / "ground_truth" / "escape-fixture.json").write_text(
        json.dumps({"alerts": []}),
        encoding="utf-8",
    )
    (dataset_dir / "fixtures" / "escape-fixture").mkdir()

    adapter = FrozenDatasetAdapter(dataset_dir, generated_root=generated_root)

    case = adapter.load_cases()[0]

    assert case.adapter_case.fixture_dir == dataset_dir / "fixtures" / "escape-fixture"
    assert case.adapter_case.scenario_json == generated_root / "tool_inputs" / "scenarios" / "escape-fixture.json"
    assert case.adapter_case.scenario_json.exists()
    assert not case.adapter_case.scenario_json.resolve().is_relative_to(dataset_dir.resolve())
    scenario_payload = json.loads(case.adapter_case.scenario_json.read_text(encoding="utf-8"))
    serialized_scenario = json.dumps(scenario_payload)
    assert scenario_payload["fixture_dir"] == "escape-fixture"
    assert "safe" in scenario_payload["nested"]
    assert "ground_truth" not in serialized_scenario
    assert "traces" not in serialized_scenario
    assert "responsibility" not in serialized_scenario
    assert "alerts" not in serialized_scenario
    payload = repr(case.adapter_case.to_adapter_payload())
    assert "ground_truth" not in payload


def test_sanitized_scenario_drops_normalized_evaluator_only_key_variants(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    generated_root = tmp_path / "runtime-generated"
    (dataset_dir / "scenarios").mkdir(parents=True)
    (dataset_dir / "ground_truth").mkdir()
    (dataset_dir / "fixtures").mkdir()
    (dataset_dir / "traces").mkdir()
    (dataset_dir / "metadata.json").write_text(
        json.dumps({"scenario_ids": ["key-variants"]}),
        encoding="utf-8",
    )
    (dataset_dir / "scenarios" / "key-variants.json").write_text(
        json.dumps(
            {
                "id": "key-variants",
                "groundTruth": "groundTruth leak",
                "GroundTruth": "GroundTruth leak",
                "ground-truth": "ground-truth leak",
                "ground.truth": "ground.truth leak",
                "expectedClaims": "expectedClaims leak",
                "ExpectedClaims": "ExpectedClaims leak",
                "expected-claims": "expected-claims leak",
                "expected.claims": "expected.claims leak",
                "responsibilityClaims": "responsibilityClaims leak",
                "responsibility.claims": "responsibility.claims leak",
                "responsibilityAlerts": "responsibilityAlerts leak",
                "responsibility.alerts": "responsibility.alerts leak",
                "traceDb": "traceDb leak",
                "TRACE_DB": "TRACE_DB leak",
                "trace.db": "trace.db leak",
                "agentSightBaseline": "agentSightBaseline leak",
                "MtAgentPrismBaseline": "MtAgentPrismBaseline leak",
                "agentSight.baseline": "agentSight.baseline leak",
                "label": "label leak",
                "labels": "labels leak",
                "safe": "safe value",
            }
        ),
        encoding="utf-8",
    )
    (dataset_dir / "ground_truth" / "key-variants.json").write_text(
        json.dumps({"alerts": []}),
        encoding="utf-8",
    )

    case = FrozenDatasetAdapter(dataset_dir, generated_root=generated_root).load_cases()[0]

    scenario_payload = json.loads(case.adapter_case.scenario_json.read_text(encoding="utf-8"))
    serialized_scenario = json.dumps(scenario_payload)
    assert scenario_payload["safe"] == "safe value"
    for leaked_value in (
        "groundTruth leak",
        "GroundTruth leak",
        "ground-truth leak",
        "ground.truth leak",
        "expectedClaims leak",
        "ExpectedClaims leak",
        "expected-claims leak",
        "expected.claims leak",
        "responsibilityClaims leak",
        "responsibility.claims leak",
        "responsibilityAlerts leak",
        "responsibility.alerts leak",
        "traceDb leak",
        "TRACE_DB leak",
        "trace.db leak",
        "agentSightBaseline leak",
        "MtAgentPrismBaseline leak",
        "agentSight.baseline leak",
        "label leak",
        "labels leak",
    ):
        assert leaked_value not in serialized_scenario


def test_sanitized_scenario_drops_case_variant_unsafe_strings(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    generated_root = tmp_path / "runtime-generated"
    (dataset_dir / "scenarios").mkdir(parents=True)
    (dataset_dir / "ground_truth").mkdir()
    (dataset_dir / "fixtures").mkdir()
    (dataset_dir / "traces").mkdir()
    (dataset_dir / "metadata.json").write_text(
        json.dumps({"scenario_ids": ["string-variants"]}),
        encoding="utf-8",
    )
    (dataset_dir / "scenarios" / "string-variants.json").write_text(
        json.dumps(
            {
                "id": "string-variants",
                "safe": "safe value",
                "strings": [
                    "GroundTruth expected claims",
                    "Ground.Truth expected claims",
                    "TRACE_DB",
                    "TRACE.DB",
                    "ResponsibilityClaims",
                    "Responsibility.Claims",
                    "label=attack",
                    "label.attack",
                    "MtAgentPrismBaseline conclusion",
                    "MtAgentPrism.Baseline conclusion",
                ],
            }
        ),
        encoding="utf-8",
    )
    (dataset_dir / "ground_truth" / "string-variants.json").write_text(
        json.dumps({"alerts": []}),
        encoding="utf-8",
    )

    case = FrozenDatasetAdapter(dataset_dir, generated_root=generated_root).load_cases()[0]

    scenario_payload = json.loads(case.adapter_case.scenario_json.read_text(encoding="utf-8"))
    serialized_scenario = json.dumps(scenario_payload)
    assert scenario_payload["safe"] == "safe value"
    assert "GroundTruth expected claims" not in serialized_scenario
    assert "Ground.Truth expected claims" not in serialized_scenario
    assert "TRACE_DB" not in serialized_scenario
    assert "TRACE.DB" not in serialized_scenario
    assert "ResponsibilityClaims" not in serialized_scenario
    assert "Responsibility.Claims" not in serialized_scenario
    assert "label=attack" not in serialized_scenario
    assert "label.attack" not in serialized_scenario
    assert "MtAgentPrismBaseline conclusion" not in serialized_scenario
    assert "MtAgentPrism.Baseline conclusion" not in serialized_scenario


def test_generated_root_rejects_existing_regular_file(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    generated_root = tmp_path / "runtime-generated"
    generated_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ValueError, match="generated_root.*directory"):
        FrozenDatasetAdapter(dataset_dir, generated_root=generated_root)


def test_load_cases_rejects_scenario_id_path_traversal(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    (dataset_dir / "scenarios").mkdir(parents=True)
    (dataset_dir / "ground_truth").mkdir()
    (dataset_dir / "fixtures").mkdir()
    (dataset_dir / "traces").mkdir()
    (dataset_dir / "metadata.json").write_text(
        json.dumps({"scenario_ids": ["../outside/pwn"]}),
        encoding="utf-8",
    )

    adapter = FrozenDatasetAdapter(dataset_dir)

    with pytest.raises(ValueError, match="Invalid scenario_id"):
        adapter.load_cases()


def test_fixture_dir_fallback_rejects_regular_files(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    (dataset_dir / "scenarios").mkdir(parents=True)
    (dataset_dir / "ground_truth").mkdir()
    (dataset_dir / "fixtures").mkdir()
    (dataset_dir / "traces").mkdir()
    (dataset_dir / "metadata.json").write_text(
        json.dumps({"scenario_ids": ["file-fixture"]}),
        encoding="utf-8",
    )
    (dataset_dir / "scenarios" / "file-fixture.json").write_text(
        json.dumps({"id": "file-fixture"}),
        encoding="utf-8",
    )
    (dataset_dir / "ground_truth" / "file-fixture.json").write_text(
        json.dumps({"alerts": []}),
        encoding="utf-8",
    )
    (dataset_dir / "fixtures" / "file-fixture").write_text("not a directory", encoding="utf-8")

    adapter = FrozenDatasetAdapter(dataset_dir)

    case = adapter.load_cases()[0]

    assert case.adapter_case.fixture_dir is None
    payload = case.adapter_case.to_adapter_payload()
    assert payload["fixture_dir"] is None
    assert "file-fixture" not in repr(payload["fixture_dir"])


def test_load_dataset_passes_explicit_generated_root_to_adapter(tmp_path: Path):
    dataset_root = tmp_path / "Dataset"
    dataset_dir = dataset_root / "FuxiBench"
    generated_root = tmp_path / "runtime-generated"
    (dataset_dir / "scenarios").mkdir(parents=True)
    (dataset_dir / "ground_truth").mkdir()
    (dataset_dir / "fixtures").mkdir()
    (dataset_dir / "traces").mkdir()
    (dataset_dir / "metadata.json").write_text(
        json.dumps({"scenario_ids": ["missing-scenario"]}),
        encoding="utf-8",
    )
    (dataset_dir / "ground_truth" / "missing-scenario.json").write_text(
        json.dumps({"alerts": []}),
        encoding="utf-8",
    )

    adapter = load_dataset("FuxiBench", dataset_root, generated_root=generated_root)

    scenario_json = adapter.load_cases()[0].adapter_case.scenario_json
    assert scenario_json == generated_root / "tool_inputs" / "scenarios" / "missing-scenario.json"


def test_ground_truth_v2_expected_claims_are_supported(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    (dataset_dir / "scenarios").mkdir(parents=True)
    (dataset_dir / "ground_truth" / "credential").mkdir(parents=True)
    (dataset_dir / "fixtures").mkdir()
    (dataset_dir / "traces" / "credential" / "ssh").mkdir(parents=True)
    (dataset_dir / "quality").mkdir()
    (dataset_dir / "quality" / "manifest.json").write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "scenario_id": "ssh",
                        "category": "credential",
                        "paths": {
                            "ground_truth": "ground_truth/credential/ssh.json",
                            "responsibility_db": "traces/credential/ssh/responsibility.db",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (dataset_dir / "ground_truth" / "credential" / "ssh.json").write_text(
        json.dumps(
            {
                "scenario_id": "ssh",
                "label": "attack",
                "category": "credential",
                "attack_type": "B",
                "expected_behavior": {
                    "min_decision": "Unsafe",
                    "expected_claims": ["boundary.file./home/user/.ssh/id_rsa"],
                    "expected_syscalls": [{"syscall": "openat", "path": "/home/user/.ssh/id_rsa"}],
                },
            }
        ),
        encoding="utf-8",
    )
    (dataset_dir / "traces" / "credential" / "ssh" / "responsibility.db").write_text("", encoding="utf-8")

    case = FrozenDatasetAdapter(dataset_dir).load_cases()[0]

    assert case.is_attack
    assert len(case.expected_claims) == 1
    assert any("/home/user/.ssh/id_rsa" in claim.resource_substrings for claim in case.expected_claims)


def test_network_c2_beacon_expected_claims_capture_expected_resources_and_commands(tmp_path: Path):
    dataset_dir = tmp_path / "FuxiBench"
    (dataset_dir / "scenarios").mkdir(parents=True)
    (dataset_dir / "ground_truth").mkdir()
    (dataset_dir / "fixtures").mkdir()
    (dataset_dir / "traces").mkdir()
    (dataset_dir / "metadata.json").write_text(
        json.dumps({"scenario_ids": ["network-c2-beacon"]}),
        encoding="utf-8",
    )
    (dataset_dir / "ground_truth" / "network-c2-beacon.json").write_text(
        json.dumps(
            {
                "alerts": [
                    {
                        "alert_id": "c2",
                        "severity": "High",
                        "category": "network",
                        "detection_signals": ["ARGUMENT_MATCH"],
                        "expected_claims": [
                            ["boundary.file.endpoints.txt", "endpoints.txt controls beacon destinations"],
                            ["boundary.tool.Bash", "Bash command launches beacon"],
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    case = FrozenDatasetAdapter(dataset_dir).load_cases()[0]
    claim = case.expected_claims[0]

    assert "endpoints.txt" in "\n".join(claim.resource_substrings)
    assert any("Bash" in command for command in claim.command_substrings)


def test_unknown_dataset_raises_clear_error():
    with pytest.raises(ValueError, match="Unsupported dataset"):
        load_dataset("missing-dataset", Path("compare/Dataset"))
