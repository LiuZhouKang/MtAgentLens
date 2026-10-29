from copy import deepcopy
import pytest

from compare.scripts.dataset.normalize_gt_provenance import normalize_annotation, normalize_gt


@pytest.mark.parametrize("annotation", [{"method": "human"}, {"human_annotation": True}])
def test_migration_preserves_actual_human_records(annotation):
    with pytest.raises(ValueError, match="human"):
        normalize_annotation(annotation)


def test_ai_review_is_not_attributed_to_named_humans():
    original = {"annotators": ["Alice", "Bob", "Carol"], "method": "three independent reviewers"}
    result = normalize_annotation(original)
    assert result["reviewer_agent_labels"] == original["annotators"]
    assert "annotators" not in result
    assert result["method"] == "model_proposal"
    assert result["human_annotation"] is False
    assert result["independence_verified"] is False
    assert result["legacy_method"] == original["method"]
    assert normalize_annotation(result) == result
    assert original["method"] == "three independent reviewers"


def test_resolved_ai_review_keeps_human_review_pending_and_semantics():
    original = {"annotation": {"annotators": ["A", "B", "C"], "method": "legacy"},
                "label": "attack", "attack_type": "C", "expected_behaviors": [{"behavior_id": "one"}],
                "quality": {"legacy_needs_human_review": True, "notes": "historical text",
                            "review": {"status": "resolved", "human_review": "waived_by_author"}}}
    before = deepcopy(original)
    result = normalize_gt(original)
    assert result["quality"]["needs_human_review"] is True
    assert result["quality"]["review"]["human_review_status"] == "not_performed"
    assert result["quality"]["review"]["notes_disclosure"]["status"] == "historical_unrevalidated"
    for field in ("label", "attack_type", "expected_behaviors"):
        assert result[field] == before[field]
    assert result["quality"]["notes"] == before["quality"]["notes"]
    assert normalize_gt(result) == result
    assert original == before
