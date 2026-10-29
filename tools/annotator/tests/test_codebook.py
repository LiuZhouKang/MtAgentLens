# SPDX-License-Identifier: Apache-2.0 OR MIT
"""The codebook document and the enforced validator must not drift apart."""

from pathlib import Path

import pytest

from tools.annotator.records import _validate_annotation

CODEBOOK = Path("tools/annotator/PILOT-CODEBOOK.md")


def _text() -> str:
    return CODEBOOK.read_text(encoding="utf-8")


def test_codebook_is_finalised_not_a_draft():
    text = _text()
    assert "版本：`pilot/1`" in text
    assert "状态：**pilot 轮次定稿**" in text
    assert "pilot/1-draft` → `pilot/1" in text
    # The old draft banner must not survive anywhere in the document.
    assert "待团队确认" not in text
    assert "草案，待" not in text


def test_codebook_states_the_rules_that_the_validator_enforces():
    text = _text()
    for rule in ("单调链", "attempted ≥ executed ≥ succeeded", "空列表", "空根因",
                 "κ ≥ 0.60", "model_proposal"):
        assert rule in text, rule
    assert "codebook_version" in text


@pytest.mark.parametrize("chain,valid", [
    (("yes", "no", "no"), True),
    (("yes", "yes", "unknown"), True),
    (("no", "no", "no"), True),
    (("no", "no", "unknown"), False),
    (("unknown", "unknown", "yes"), False),
])
def test_documented_chain_matches_the_validator(chain, valid):
    attempted, executed, succeeded = chain
    annotation = {
        "attempted": {"value": attempted, "evidence_refs": ["event-1"]},
        "executed": {"value": executed, "evidence_refs": ["event-1"]},
        "succeeded": {"value": succeeded, "evidence_refs": ["event-1"]},
        "identities": {"event-1": None},
        "roots": {"actors": [] if attempted == "no" else None,
                  "evidence_refs": ["event-1"] if attempted == "no" else []},
        "notes": "Documentation contract fixture.",
    }
    bundle = {"events": [{"event_id": "event-1"}], "actors": ["actor-a"]}
    if valid:
        _validate_annotation(annotation, bundle)
    else:
        with pytest.raises(ValueError):
            _validate_annotation(annotation, bundle)
