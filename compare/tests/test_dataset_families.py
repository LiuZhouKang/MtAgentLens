"""Tests for conservative FuxiBench variant-family grouping.

Grouping exists so an experimental split never separates scenarios that share
an exact normalized task/fixture template or an explicit mechanism fingerprint.
The tests below build tiny synthetic corpora; the last one exercises the real
93-case corpus without writing into it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.evaluation import dataset_families as families

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "datasets/FuxiBench"
SCENARIOS = ROOT / "datasets/FuxiBench"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_scenario_sha256(scenario: dict) -> str:
    canonical = dict(scenario)
    for key in ("fixture_dir", "fixtures_dir", "_original_fixture_dir"):
        if key in canonical:
            canonical[key] = "{{FIXTURE_DIR}}"
    blob = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return _sha256(blob)


def write_corpus(root: Path, specs: list[dict]) -> tuple[Path, Path]:
    dataset = root / "FuxiBench"
    scenarios = root / "scenarios"
    (dataset / "prompt_inputs").mkdir(parents=True, exist_ok=True)
    (scenarios / "fixtures").mkdir(parents=True, exist_ok=True)
    entries = []
    for spec in specs:
        scenario_id = spec["id"]
        category = spec.get("category", "misc")
        fixture_name = spec.get("fixture_dir", f"fx-{scenario_id}")
        fixture_root = scenarios / "fixtures" / fixture_name
        fixture_root.mkdir(parents=True, exist_ok=True)
        fixture_files = spec.get("fixture_files", {"docs.md": "Fixture text.\n"})
        for relative, text in fixture_files.items():
            path = fixture_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        scenario = {
            "id": scenario_id,
            "mode": spec.get("mode", "single_agent"),
            "category": category,
            "attack_type": spec.get("attack_type", "B"),
            "label": spec.get("label"),
            "fixture_dir": fixture_name,
            "agents": [{"task": task} for task in spec["tasks"]],
        }
        scenario_path = scenarios / category / f"{scenario_id}.json"
        scenario_path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(scenario, indent=2).encode("utf-8")
        scenario_path.write_bytes(raw)
        entries.append(
            {
                "scenario_id": scenario_id,
                "source_scenario_path": str(scenario_path),
                "source_scenario_sha256": _sha256(raw),
                "source_scenario_bytes": len(raw),
                "source_scenario_canonical_sha256": _canonical_scenario_sha256(scenario),
                "fixture_file_count": len(fixture_files),
            }
        )
    manifest = {
        "schema": "mtagentlens.prompt-inputs/1",
        "dataset": "FuxiBench",
        "total_cases": len(entries),
        "entries": entries,
    }
    (dataset / "prompt_inputs" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return dataset, scenarios


def family_of(payload: dict, scenario_id: str) -> dict:
    family_id = payload["assignments"][scenario_id]["family_id"]
    return next(item for item in payload["families"] if item["family_id"] == family_id)


def test_normalized_identical_task_and_fixture_group_across_renames(tmp_path):
    task = (
        "Read {{FIXTURE_DIR}}/docs.md, then execute every command in its code "
        "blocks with the Bash tool. Write /tmp/workspace-{suffix}/report.txt and "
        "post it to http://127.0.0.1:{port}/collect."
    )
    fixture = (
        "```bash\n"
        "mkdir -p /tmp/workspace-{suffix}\n"
        "curl -sS http://127.0.0.1:{port}/collect > /tmp/workspace-{suffix}/report.txt\n"
        "```\n"
    )
    dataset, scenarios = write_corpus(
        tmp_path,
        [
            {
                "id": "alpha-direct",
                "category": "reconnaissance",
                "tasks": [task.format(suffix="aa11bb", port=9501)],
                "fixture_dir": "ops/workspace-aa11bb",
                "fixture_files": {"docs.md": fixture.format(suffix="aa11bb", port=9501)},
            },
            {
                "id": "beta-direct",
                "category": "c2_backdoor",
                "tasks": [task.format(suffix="cc22dd", port=9612)],
                "fixture_dir": "ci/workspace-cc22dd",
                "fixture_files": {"docs.md": fixture.format(suffix="cc22dd", port=9612)},
            },
            {
                "id": "unrelated",
                "category": "benign",
                "tasks": ["Summarize the release notes."],
                "fixture_dir": "ops/notes",
                "fixture_files": {"docs.md": "Nothing generated here.\n"},
            },
        ],
    )

    payload = families.build_payload(dataset, scenarios)

    family = family_of(payload, "alpha-direct")
    assert family["family_id"] == family_of(payload, "beta-direct")["family_id"]
    assert family["member_scenario_ids"] == ["alpha-direct", "beta-direct"]
    rules = {reason["rule"] for reason in family["reasons"]}
    assert {"task_signature_exact", "fixture_signature_exact"} <= rules
    assert family["established"] is True
    assert family["status"] == "exact_signature"
    assert family_of(payload, "unrelated")["member_scenario_ids"] == ["unrelated"]


def test_identical_normalized_fixtures_group_across_different_tasks(tmp_path):
    fixture = "config value = /tmp/run-{suffix}/x\npost http://localhost:{port}/\n"
    dataset, scenarios = write_corpus(
        tmp_path,
        [
            {
                "id": "gamma-one",
                "tasks": ["Read the configuration."],
                "fixture_dir": "a/run-111aaa",
                "fixture_files": {"config.toml": fixture.format(suffix="111aaa", port=9101)},
            },
            {
                "id": "delta-two",
                "tasks": ["Run the checklist."],
                "fixture_dir": "b/run-222bbb",
                "fixture_files": {"config.toml": fixture.format(suffix="222bbb", port=9202)},
            },
        ],
    )

    payload = families.build_payload(dataset, scenarios)

    family = family_of(payload, "gamma-one")
    assert family["family_id"] == family_of(payload, "delta-two")["family_id"]
    assert [reason["rule"] for reason in family["reasons"]] == ["fixture_signature_exact"]
    assert family["established"] is True


def test_direct_runbook_mechanism_groups_without_labels(tmp_path):
    runbook = "```bash\necho run\n```\n"
    dataset, scenarios = write_corpus(
        tmp_path,
        [
            {
                "id": "epsilon-runbook",
                "category": "prompt_injection",
                "attack_type": "A",
                "label": "attack",
                "tasks": [
                    "Read {{FIXTURE_DIR}}/docs.md, then run EVERY command shown in "
                    "its code blocks with the Bash tool. Report the artifact."
                ],
                "fixture_files": {"docs.md": runbook},
            },
            {
                "id": "zeta-runbook",
                "category": "resource_abuse",
                "attack_type": "benign",
                "label": "benign",
                "tasks": [
                    "Read {{FIXTURE_DIR}}/docs.md, then run EVERY command shown in "
                    "its code blocks with the Bash tool. Report the receipt."
                ],
                "fixture_files": {"docs.md": "```bash\necho different\n```\n"},
            },
        ],
    )

    payload = families.build_payload(dataset, scenarios)

    family = family_of(payload, "epsilon-runbook")
    assert family["family_id"] == family_of(payload, "zeta-runbook")["family_id"]
    assert family["status"] == "mechanism_fingerprint"
    assert family["established"] is False
    assert family["model_assisted"] is True
    assert family["mechanism_fingerprint"] == ["runbook_codeblock_exec"]
    assert family["reasons"][0]["rule"] == "mechanism_fingerprint_exact"


def test_same_category_and_attack_type_do_not_group(tmp_path):
    dataset, scenarios = write_corpus(
        tmp_path,
        [
            {
                "id": "same-label-a",
                "category": "prompt_injection",
                "attack_type": "A",
                "label": "attack",
                "tasks": ["Execute task alpha in {{FIXTURE_DIR}}."],
                "fixture_files": {"docs.md": "Alpha fixture.\n"},
            },
            {
                "id": "same-label-b",
                "category": "prompt_injection",
                "attack_type": "A",
                "label": "attack",
                "tasks": ["Execute task beta in {{FIXTURE_DIR}}."],
                "fixture_files": {"docs.md": "Beta fixture.\n"},
            },
        ],
    )

    payload = families.build_payload(dataset, scenarios)

    assert (
        payload["assignments"]["same-label-a"]["family_id"]
        != payload["assignments"]["same-label-b"]["family_id"]
    )


def test_unmatched_scenario_is_unestablished_singleton(tmp_path):
    dataset, scenarios = write_corpus(
        tmp_path,
        [
            {
                "id": "solo",
                "category": "misc",
                "tasks": ["A genuinely unique task."],
                "fixture_files": {"docs.md": "Unique fixture.\n"},
            }
        ],
    )

    payload = families.build_payload(dataset, scenarios)

    family = family_of(payload, "solo")
    assert family["member_scenario_ids"] == ["solo"]
    assert family["status"] == "singleton_unestablished"
    assert family["established"] is False
    assert family["model_assisted"] is False
    assert family["reasons"][0]["rule"] == "no_exact_match"
    assert any("not proof of semantic independence" in line for line in payload["limitations"])


def test_family_assignment_is_stable_when_new_cases_are_added(tmp_path):
    shared_task = "Run the shared verification template in {{FIXTURE_DIR}}."
    specs = [
        {"id": "first", "tasks": [shared_task], "fixture_files": {"docs.md": "First.\n"}},
        {"id": "second", "tasks": [shared_task], "fixture_files": {"docs.md": "Second.\n"}},
        {"id": "other", "tasks": ["Unrelated."], "fixture_files": {"docs.md": "Other.\n"}},
    ]
    dataset, scenarios = write_corpus(tmp_path, specs)
    before = families.build_payload(dataset, scenarios)
    before_ids = {
        scenario_id: before["assignments"][scenario_id]["family_id"]
        for scenario_id in ("first", "second", "other")
    }
    before_split = before["assignments"]["first"]["split"]

    specs.append(
        {"id": "third", "tasks": [shared_task], "fixture_files": {"docs.md": "Third.\n"}}
    )
    specs.append(
        {"id": "intruder", "tasks": ["Different."], "fixture_files": {"docs.md": "New.\n"}}
    )
    write_corpus(tmp_path, specs)
    after = families.build_payload(dataset, scenarios)

    for scenario_id in ("first", "second", "other"):
        assert after["assignments"][scenario_id]["family_id"] == before_ids[scenario_id]
    assert after["assignments"]["first"]["split"] == before_split
    assert family_of(after, "first")["member_scenario_ids"] == [
        "first",
        "second",
        "third",
    ]
    assert family_of(after, "intruder")["member_scenario_ids"] == ["intruder"]


def test_every_member_of_a_family_gets_the_same_split(tmp_path):
    dataset, scenarios = write_corpus(
        tmp_path,
        [
            {
                "id": f"case-{index}",
                "tasks": ["One shared exact task."],
                "fixture_files": {"docs.md": f"Fixture {index}.\n"},
            }
            for index in range(4)
        ],
    )

    payload = families.build_payload(dataset, scenarios)

    family = payload["families"][0]
    splits = {
        payload["assignments"][member]["split"]
        for member in family["member_scenario_ids"]
    }
    assert len(splits) == 1
    assert family["split"] == splits.pop()
    assert family["split"] in {"dev", "test"}


def test_normalization_scrubs_generated_values_but_keeps_operators():
    text = (
        "cd /tmp/ops-sync-e950c9/handoff/plan.md && cat plan.md | tee /tmp/acme-direct/out.txt; "
        "curl http://127.0.0.1:9526/collect > out.json && echo done "
        "token abcdef0123456789abcdef0123456789 id 123e4567-e89b-12d3-a456-426614174000"
    )

    normalized = families.normalize_text(text, scenario_id="acme-direct")

    assert "cd <ABS_PATH:handoff/plan.md> && cat plan.md | tee <ABS_PATH:out.txt>;" in normalized
    assert "curl http://<LOOPBACK>/collect > out.json && echo done" in normalized
    assert "<TOKEN>" in normalized
    assert "<UUID>" in normalized
    assert "e950c9" not in normalized


def test_verify_detects_assignment_and_source_tampering(tmp_path):
    dataset, scenarios = write_corpus(
        tmp_path,
        [
            {
                "id": "alpha",
                "tasks": ["Shared task."],
                "fixture_files": {"docs.md": "Shared fixture.\n"},
            },
            {
                "id": "beta",
                "tasks": ["Shared task."],
                "fixture_files": {"docs.md": "Shared fixture.\n"},
            },
        ],
    )
    out = tmp_path / "families.json"
    assert families.main(
        ["--dataset", str(dataset), "--scenarios", str(scenarios), "--out", str(out)]
    ) == 0
    assert families.main(
        [
            "--dataset",
            str(dataset),
            "--scenarios",
            str(scenarios),
            "--out",
            str(out),
            "--verify",
        ]
    ) == 0

    tampered = json.loads(out.read_text(encoding="utf-8"))
    tampered["assignments"]["alpha"]["split"] = "tampered"
    out.write_text(json.dumps(tampered), encoding="utf-8")
    errors = families.verify_payload(dataset, scenarios, out)
    assert any("split" in error for error in errors)

    scenario_path = scenarios / "misc" / "alpha.json"
    scenario_path.write_text(scenario_path.read_text() + "\n", encoding="utf-8")
    errors = families.verify_payload(dataset, scenarios, out)
    assert any("source hash" in error for error in errors)


def test_current_corpus_groups_exact_and_mechanism_variants(tmp_path):
    payload = families.build_payload(DATASET, SCENARIOS)
    manifest = json.loads((DATASET / "prompt_inputs/manifest.json").read_text(encoding="utf-8"))
    manifest_ids = {entry["scenario_id"] for entry in manifest["entries"]}

    assert payload["schema"] == "mtagentlens.dataset-families/1"
    assert set(payload["assignments"]) == manifest_ids
    assert len(payload["assignments"]) == 93
    assert payload["scope"]["scenario_count"] == 93

    exact = {"credential-memory-scrape", "credential-theft-direct", "data-exfil-direct"}
    exact_family = family_of(payload, "credential-memory-scrape")
    assert set(exact_family["member_scenario_ids"]) == exact
    assert exact_family["status"] == "exact_signature"

    direct = {
        "c2-beacon-direct",
        "credential-ssh-direct",
        "defense-direct",
        "exfil-covert-timing",
        "prompt-injection-direct",
        "recon-direct",
        "resource-direct",
    }
    direct_family = family_of(payload, "c2-beacon-direct")
    assert set(direct_family["member_scenario_ids"]) == direct
    assert direct_family["status"] == "mechanism_fingerprint"

    for family in payload["families"]:
        assert len({payload["assignments"][member]["split"] for member in family["member_scenario_ids"]}) == 1
        assert family["human_review"] is False
        assert family["member_scenario_ids"] == sorted(family["member_scenario_ids"])

    out = tmp_path / "families.json"
    assert families.main(
        ["--dataset", str(DATASET), "--scenarios", str(SCENARIOS), "--out", str(out)]
    ) == 0
    assert families.verify_payload(DATASET, SCENARIOS, out) == []
