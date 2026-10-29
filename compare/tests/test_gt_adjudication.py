"""Applying an adjudication dossier must be honest, scoped and idempotent."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.evaluation.apply_gt_adjudication import apply_dossier


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _dataset(tmp_path: Path, *, attack_type: str = "C") -> tuple[Path, Path]:
    dataset = tmp_path / "dataset"
    scenarios = tmp_path / "scenarios"
    _write(
        dataset / "ground_truth" / "multi_agent_attack" / "case-one.json",
        {
            "schema": "mtagentlens.ground_truth.v4",
            "scenario_id": "case-one",
            "label": "attack",
            "attack_type": attack_type,
            "payload": {"payload_channel_classification": attack_type},
            "annotation": {"annotators": ["刘周康", "唐浩钺", "刘锦样"]},
            "quality": {"needs_human_review": True, "notes": ["old note"]},
        },
    )
    _write(dataset / "ground_truth" / "_reviewers" / "case-one.json", {"reviewers": ["raw"]})
    _write(
        scenarios / "multi_agent_attack" / "case-one.json",
        {"id": "case-one", "attack_type": attack_type, "agents": []},
    )
    return dataset, scenarios


def _entry(**overrides: object) -> dict:
    base = {
        "scenario_id": "case-one",
        "verdict": "close_with_documented_limitations",
        "proposed_type": "B",
        "type_changed": True,
        "verified": True,
        "evidence": ["case-one tests can be run by one agent"],
        "limitations": ["fixture docs overstate the external endpoint"],
        "residual_risks": ["loopback sink is documented as local"],
    }
    base.update(overrides)
    return base


def test_resolution_records_automated_review_without_claiming_human_signoff(tmp_path: Path) -> None:
    dataset, scenarios = _dataset(tmp_path)
    apply_dossier([_entry()], dataset_dir=dataset, scenarios_root=scenarios)

    gt = json.loads(
        (dataset / "ground_truth" / "multi_agent_attack" / "case-one.json").read_text()
    )
    assert gt["attack_type"] == "B"
    assert gt["payload"]["payload_channel_classification"] == "B"
    assert gt["quality"].get("needs_human_review") is True
    assert gt["quality"]["legacy_needs_human_review"] is True
    review = gt["quality"]["review"]
    assert review["status"] == "resolved"
    assert review["method"] == "rule-based-adjudication-v1"
    assert review["human_review"] == "not_performed"
    assert review["evidence"] and review["limitations"]
    assert review["type_change"] == {"from": "C", "to": "B", "rule": "protocol §2.2"}
    assert gt["annotation"]["annotators"] == ["刘周康", "唐浩钺", "刘锦样"]
    assert json.loads((dataset / "ground_truth" / "_reviewers" / "case-one.json").read_text()) == {
        "reviewers": ["raw"]
    }
    scenario = json.loads(
        (scenarios / "multi_agent_attack" / "case-one.json").read_text()
    )
    assert scenario["attack_type"] == "B"


def test_blocked_items_keep_the_flag(tmp_path: Path) -> None:
    dataset, scenarios = _dataset(tmp_path)
    apply_dossier(
        [_entry(verdict="blocked", verified=False)],
        dataset_dir=dataset,
        scenarios_root=scenarios,
    )
    gt = json.loads(
        (dataset / "ground_truth" / "multi_agent_attack" / "case-one.json").read_text()
    )
    assert gt["quality"]["needs_human_review"] is True
    assert gt["quality"]["review"]["status"] == "blocked"
    assert gt["attack_type"] == "C"


def test_unverified_type_change_is_refused(tmp_path: Path) -> None:
    dataset, scenarios = _dataset(tmp_path)
    with pytest.raises(ValueError, match="verified"):
        apply_dossier(
            [_entry(verified=False)],
            dataset_dir=dataset,
            scenarios_root=scenarios,
        )


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    dataset, scenarios = _dataset(tmp_path)
    before = (dataset / "ground_truth" / "multi_agent_attack" / "case-one.json").read_bytes()
    apply_dossier(
        [_entry()], dataset_dir=dataset, scenarios_root=scenarios, dry_run=True
    )
    after = (dataset / "ground_truth" / "multi_agent_attack" / "case-one.json").read_bytes()
    assert before == after


def test_applying_twice_is_idempotent(tmp_path: Path) -> None:
    dataset, scenarios = _dataset(tmp_path)
    apply_dossier([_entry()], dataset_dir=dataset, scenarios_root=scenarios)
    first = (dataset / "ground_truth" / "multi_agent_attack" / "case-one.json").read_bytes()
    apply_dossier([_entry()], dataset_dir=dataset, scenarios_root=scenarios)
    second = (dataset / "ground_truth" / "multi_agent_attack" / "case-one.json").read_bytes()
    assert first == second


def test_automated_resolution_does_not_clear_human_release_blocker(tmp_path: Path, synthetic_dataset: Path) -> None:
    import shutil

    from compare.tools.dataset_release import build_release

    source = tmp_path / "dataset"
    shutil.copytree(synthetic_dataset, source)
    fixture_target = source / "scenarios" / "fixtures"
    if not fixture_target.exists():
        shutil.copytree(source / "fixtures", fixture_target)
    gt = sorted((source / "ground_truth").glob("*.json"))[0]
    data = json.loads(gt.read_text(encoding="utf-8"))
    data["quality"] = {"needs_human_review": True}
    gt.write_text(json.dumps(data), encoding="utf-8")

    blocked = build_release(source / "scenarios", source / "ground_truth", tmp_path / "rel-blocked")
    assert any("unresolved human review" in item for item in blocked["publication_blockers"])

    data["quality"]["review"] = {
        "status": "resolved",
        "method": "rule-based-adjudication-v1",
        "human_review": "waived_by_author",
        "evidence": ["fixture is single-agent"],
    }
    gt.write_text(json.dumps(data), encoding="utf-8")
    resolved = build_release(source / "scenarios", source / "ground_truth", tmp_path / "rel-resolved")
    assert any("unresolved human review" in item for item in resolved["publication_blockers"])


def test_normalize_removes_only_false_flags(tmp_path: Path) -> None:
    from scripts.evaluation.apply_gt_adjudication import normalize_false_flags

    dataset, _scenarios = _dataset(tmp_path)
    clear = dataset / "ground_truth" / "benign" / "case-two.json"
    _write(clear, {"scenario_id": "case-two", "quality": {"needs_human_review": False}})
    open_gt = dataset / "ground_truth" / "case" / "case-three.json"
    _write(open_gt, {"scenario_id": "case-three", "quality": {"needs_human_review": True}})

    removed = normalize_false_flags(dataset_dir=dataset)

    assert removed == 1
    assert "needs_human_review" not in json.loads(clear.read_text())["quality"]
    assert json.loads(open_gt.read_text())["quality"]["needs_human_review"] is True


def test_long_payload_classification_rationale_is_preserved(tmp_path: Path) -> None:
    dataset, scenarios = _dataset(tmp_path)
    path = dataset / "ground_truth" / "multi_agent_attack" / "case-one.json"
    gt = json.loads(path.read_text(encoding="utf-8"))
    gt["payload"]["payload_channel_classification"] = "C（多数裁决）：长解释保留"
    _write(path, gt)

    apply_dossier([_entry()], dataset_dir=dataset, scenarios_root=scenarios)

    updated = json.loads(path.read_text(encoding="utf-8"))
    assert updated["payload"]["payload_channel_classification"] == "B"
    assert "长解释保留" in updated["payload"]["payload_channel_notes"]


def test_adjudication_does_not_invent_a_human_waiver(tmp_path):
    dataset, scenarios = _dataset(tmp_path)
    apply_dossier([_entry()], dataset_dir=dataset, scenarios_root=scenarios)
    gt = json.loads((dataset / 'ground_truth/multi_agent_attack/case-one.json').read_text())
    assert gt['quality']['review']['human_review'] == 'not_performed'


def test_invalid_later_entry_does_not_partially_modify_dataset(tmp_path):
    dataset, scenarios = _dataset(tmp_path)
    target = dataset / 'ground_truth/multi_agent_attack/case-one.json'
    before = target.read_bytes()
    with pytest.raises(ValueError):
        apply_dossier([_entry(), _entry(scenario_id='bad', verdict='invented')],
                      dataset_dir=dataset, scenarios_root=scenarios)
    assert target.read_bytes() == before


@pytest.mark.parametrize('verified', ['yes', 1])
def test_verified_must_be_literal_true_even_without_type_change(tmp_path, verified):
    dataset, scenarios = _dataset(tmp_path)
    with pytest.raises(ValueError, match='verified'):
        apply_dossier([_entry(proposed_type='C', verified=verified)],
                      dataset_dir=dataset, scenarios_root=scenarios)
