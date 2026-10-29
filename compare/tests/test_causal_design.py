"""Design proposals preserve source evidence and never impersonate run truth."""
import json
from pathlib import Path

import pytest

from scripts.evaluation.build_causal_design import build_proposal, verify_proposal


def test_proposal_quotes_roles_and_carrier_without_inventing_runtime_chain(tmp_path):
    scenario = tmp_path / "scenario.json"
    truth = tmp_path / "truth.json"
    scenario.write_text(json.dumps({"id": "case", "agents": [
        {"role_file": "analyst.md", "task": "Write the plan."},
        {"role_file": "operator.md", "task": "Read the plan."}]}))
    truth.write_text(json.dumps({"scenario_id": "case", "label": "attack",
        "payload": {"carrier": "docs.md"}, "expected_behaviors": [{
            "behavior_id": "handoff", "description": "Untrusted approval in plan.",
            "boundary_type": "DelegationBoundary", "evidence_requirements": "Confirm both actors."}]}))
    proposal = build_proposal(scenario, truth, source_root=tmp_path)
    assert proposal["scope"] == "scenario_design"
    assert proposal["status"] == "model_proposal"
    assert proposal["eligible_for_runtime_metrics"] is False
    assert proposal["runtime_reference"] is None
    assert proposal["design_hypothesis"]["root_agent"] is None
    assert proposal["design_hypothesis"]["causal_edges"] == []
    assert proposal["design_hypothesis"]["entry_point_candidate"]["quote"] == "docs.md"
    assert [r["role_file"] for r in proposal["roles"]] == ["analyst.md", "operator.md"]
    assert "expected_commands" not in json.dumps(proposal)
    verify_proposal(proposal, source_root=tmp_path)


def test_evidence_changes_or_wrong_quotes_invalidate_proposal(tmp_path):
    scenario, truth = tmp_path / "s.json", tmp_path / "g.json"
    scenario.write_text(json.dumps({"id": "case", "task": "Do work"}))
    truth.write_text(json.dumps({"scenario_id": "case", "label": "benign"}))
    proposal = build_proposal(scenario, truth, source_root=tmp_path)
    assert proposal["status"] == "unknown"
    scenario.write_text(json.dumps({"id": "case", "task": "Different work"}))
    with pytest.raises(ValueError, match="hash"):
        verify_proposal(proposal, source_root=tmp_path)


def test_scenario_identity_must_match(tmp_path):
    scenario, truth = tmp_path / "s.json", tmp_path / "g.json"
    scenario.write_text(json.dumps({"id": "other"}))
    truth.write_text(json.dumps({"scenario_id": "case"}))
    with pytest.raises(ValueError, match="identity"):
        build_proposal(scenario, truth, source_root=tmp_path)


def test_modified_quotes_and_promoting_to_runtime_truth_are_rejected(tmp_path):
    scenario, truth = tmp_path / 's.json', tmp_path / 'g.json'
    scenario.write_text(json.dumps({'id': 'case'}))
    truth.write_text(json.dumps({'scenario_id': 'case', 'payload': {'carrier': 'docs.md'}}))
    proposal = build_proposal(scenario, truth, source_root=tmp_path)
    proposal['design_hypothesis']['entry_point_candidate']['quote'] = 'invented entry'
    with pytest.raises(ValueError, match='quote'):
        verify_proposal(proposal, source_root=tmp_path)
    proposal = build_proposal(scenario, truth, source_root=tmp_path)
    proposal['eligible_for_runtime_metrics'] = True
    with pytest.raises(ValueError, match='contract'):
        verify_proposal(proposal, source_root=tmp_path)
