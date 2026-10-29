"""E7 spec: 20 difficult-benign scenarios planned, none executed yet."""

import json
from pathlib import Path

SPEC = (Path(__file__).resolve().parents[1]
        / "scenarios" / "_drafts" / "e7-difficult-benign" / "spec.json")


def load_spec() -> dict:
    return json.loads(SPEC.read_text(encoding="utf-8"))


def test_spec_is_structural_and_honest():
    spec = load_spec()
    assert spec["schema"] == "mtagentlens.e7-difficult-benign/1"
    assert spec["status"] == "spec_only", "numbers require an executed campaign"
    assert len(spec["classes"]) == 4
    rows = [scenario for group in spec["classes"] for scenario in group["scenarios"]]
    assert len(rows) == 20
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids)), "scenario ids must be unique"
    for group in spec["classes"]:
        assert len(group["scenarios"]) == 5, group["id"]
        assert group["expected_pbc_min"] >= 1
    for row in rows:
        assert row["task"].strip() and row["benign_argument"].strip()
        assert row["id"].startswith("e7-")


def test_spec_is_not_a_release_scenario_directory():
    # Drafts live under scenarios/_drafts/... and must not look like a runnable
    # category directory; no scenario JSON exists next to the spec yet.
    directory = SPEC.parent
    assert directory.name == "e7-difficult-benign"
    assert sorted(path.name for path in directory.iterdir()) == ["spec.json"]
