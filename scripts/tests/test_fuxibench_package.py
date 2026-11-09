# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Contracts for the FuxiBench 2.0 two-type migration and portable package."""
import json
from pathlib import Path

from scripts.evaluation.build_fuxibench_dataset import migrate_record, portable
from scripts.evaluation.validate_fuxibench_package import EXPECTED, gt_records

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "datasets/FuxiBench"


def base_record(**overrides):
    record = {
        "schema": "mtagentlens.ground_truth.v4",
        "scenario_id": "s",
        "category": "c",
        "label": "attack",
        "attack_type": "B",
        "expected_behaviors": [{"behavior_id": "b"}],
        "payload": {"payload_channel_classification": "B"},
        "execution_check": {"x": 1},
        "min_decision": "Unsafe",
        "should_alert": True,
        "benign_activity": None,
        "annotation": {"reviewer_agent_labels": ["a", "b", "c"], "method": "model_proposal",
                       "collector_output_consulted": False},
        "quality": {"detection_excluded": False, "needs_human_review": False},
        "label_annotation": {"v5_label": "attack", "v5_attack_type": "B"},
    }
    record.update(overrides)
    return record


def test_old_b_becomes_new_a_and_old_c_becomes_new_b():
    assert migrate_record(base_record())["attack_type"] == "A"
    assert migrate_record(base_record(attack_type="C", label_annotation={"v5_label": "attack", "v5_attack_type": "C"}))["attack_type"] == "B"


def test_excluded_attack_becomes_audited_benign_with_legacy_contract():
    record = migrate_record(base_record(quality={"detection_excluded": True, "needs_human_review": False}))
    assert record["label"] == "benign" and record["attack_type"] == "benign"
    assert record["expected_behaviors"] == []
    assert record["legacy_v4_detection_contract"]["expected_behaviors"]
    assert record["benign_activity"]["summary"]
    assert record["quality"]["detection_excluded"] is True
    assert record["taxonomy_version"] == "fuxibench-2type/1"


def test_external_paths_are_rewritten_but_tmp_semantics_survive():
    cleaned = portable({"a": "/home/lzk/MtAgentLens/env/scenarios/fixtures/x", "b": "/tmp/run"})
    assert cleaned["a"] == "fixtures/x"
    assert cleaned["b"] == "/tmp/run"


def test_package_counts_and_taxonomy_if_built():
    if not PACKAGE.exists():
        return
    records = gt_records(PACKAGE)
    assert len(records) == EXPECTED["records"]
    assert sum(r["label"] == "attack" for r in records) == EXPECTED["attack"]
    assert sum(r["label"] == "benign" for r in records) == EXPECTED["benign"]
    included = [r for r in records if not (r.get("quality") or {}).get("detection_excluded")]
    assert len(included) == EXPECTED["included_attack"] + EXPECTED["included_benign"]
    assert {r["attack_type"] for r in included} == {"A", "B", "benign"}


def test_path_sanitization_preserves_network_semantics():
    from scripts.evaluation.package_fuxibench import clean_text
    text = 'bind 127.0.0.1; external 93.184.216.34; /home/lzk/MtAgentLens/env/scenarios/fixtures/x'
    assert clean_text(text) == 'bind 127.0.0.1; external 93.184.216.34; fixtures/x'


def test_trace_builder_rejects_source_destination_overlap_before_deletion(tmp_path, monkeypatch):
    import pytest
    from scripts.evaluation import package_fuxibench as builder
    monkeypatch.setattr(builder, 'PACKAGE', tmp_path)
    monkeypatch.setattr(builder, 'TRACES', tmp_path / 'traces')
    (tmp_path / 'traces').mkdir()
    sentinel = tmp_path / 'traces/observed.json'
    sentinel.write_text('{"host": "127.0.0.1"}')
    with pytest.raises(ValueError, match='overlap'):
        builder.build_traces()
    assert sentinel.read_text() == '{"host": "127.0.0.1"}'


def test_missing_trace_source_preserves_existing_output(tmp_path, monkeypatch):
    import pytest
    from scripts.evaluation import package_fuxibench as builder
    monkeypatch.setattr(builder, 'PACKAGE', tmp_path)
    monkeypatch.setattr(builder, 'TRACES', tmp_path / 'missing')
    (tmp_path / 'traces').mkdir()
    sentinel = tmp_path / 'traces/observed.json'
    sentinel.write_text('original')
    with pytest.raises(ValueError, match='missing'):
        builder.build_traces()
    assert sentinel.read_text() == 'original'


def test_clean_room_copy_has_independent_inodes(tmp_path):
    from scripts.evaluation.validate_fuxibench_package import copy_clean_room
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'record').write_text('observed')
    target = tmp_path / 'target'
    copy_clean_room(source, target)
    assert (source / 'record').stat().st_ino != (target / 'record').stat().st_ino
    (target / 'record').write_text('changed')
    assert (source / 'record').read_text() == 'observed'




def test_failed_build_preserves_existing_package_and_trace_source(tmp_path, monkeypatch):
    import pytest
    from scripts.evaluation import package_fuxibench as builder
    package = tmp_path / 'FuxiBench'
    (package / 'ground_truth/c').mkdir(parents=True)
    (package / 'ground_truth/c/s.json').write_text(json.dumps(base_record()))
    (package / 'metadata.json').write_text('{}')
    (package / 'evidence').mkdir()
    (package / 'traces').mkdir()
    (package / 'traces/run.json').write_text('{"host": "127.0.0.1"}')
    release = tmp_path / 'release'
    for name in ('scenarios', 'fixtures', 'bench', 'prompt_inputs'):
        (release / name).mkdir(parents=True)
    def fail():
        raise RuntimeError('injected failure')
    monkeypatch.setattr(builder, 'build_docs_metadata', fail)
    monkeypatch.setattr('sys.argv', ['package_fuxibench', '--force', '--package', str(package),
                       '--source-package', str(package), '--source-release', str(release),
                       '--source-traces', str(package / 'traces')])
    with pytest.raises(RuntimeError, match='injected failure'):
        builder.main()
    assert (package / 'traces/run.json').read_text() == '{"host": "127.0.0.1"}'
    assert json.loads((package / 'ground_truth/c/s.json').read_text()) == base_record()
    assert not list(tmp_path.glob('.FuxiBench-build-*'))


def test_provenance_rebase_preserves_first_original_path_and_hash(tmp_path, monkeypatch):
    import hashlib
    from scripts.evaluation import repair_package_provenance as repair
    monkeypatch.setattr(repair, 'PKG', tmp_path)
    (tmp_path / 'evidence').mkdir()
    (tmp_path / 'evidence/v5').mkdir()
    target = tmp_path / 'evidence/v5/a.txt'
    target.write_text('portable')
    ref = {'path': 'evidence/v5/gt/runs/full-93/a.txt', 'sha256': 'original'}
    repair.rebase_and_hash(ref)
    first = dict(ref)
    repair.rebase_and_hash(ref)
    assert ref == first
    assert ref['original_path'] == 'evidence/v5/gt/runs/full-93/a.txt'
    assert ref['original_sha256'] == 'original'
    assert ref['sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()


def test_provenance_does_not_follow_paths_outside_package(tmp_path, monkeypatch):
    from scripts.evaluation import repair_package_provenance as repair
    package = tmp_path / 'package'
    package.mkdir()
    outside = tmp_path / 'secret.txt'
    outside.write_text('outside')
    monkeypatch.setattr(repair, 'PKG', package)
    assert repair.resolve_ref_path('../secret.txt') is None
    ref = {'path': str(outside), 'sha256': 'original'}
    repair.repair_ref_dict(ref)
    assert ref['sha256'] == 'original'


def test_provenance_final_source_hash_and_repeat_are_stable(tmp_path, monkeypatch):
    import hashlib
    from scripts.evaluation import repair_package_provenance as repair
    monkeypatch.setattr(repair, 'PKG', tmp_path)
    (tmp_path / 'evidence/v5').mkdir(parents=True)
    (tmp_path / 'evidence/v5/source.txt').write_text('observed')
    v5 = tmp_path / 'evidence/v5/gt-v5.json'
    v5.write_text(json.dumps({'records': [{'scenario_id': 's'}], 'references': [
        {'path': 'evidence/v5/gt/runs/full-93/source.txt', 'sha256': 'old'}]}))
    gt = tmp_path / 'ground_truth/c/s.json'
    gt.parent.mkdir(parents=True)
    gt.write_text(json.dumps({'scenario_id': 's'}))
    repair.main()
    first = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    record = json.loads(gt.read_text())
    assert record['label_annotation']['source']['sha256'] == hashlib.sha256(v5.read_bytes()).hexdigest()
    repair.main()
    second = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert first == second


def test_rebuilt_prompts_bind_current_fixture_and_detect_drift(tmp_path, monkeypatch):
    from scripts.evaluation import package_fuxibench as builder
    from scripts.evaluation.validate_fuxibench_package import validate_prompt_sources
    monkeypatch.setattr(builder, 'PACKAGE', tmp_path)
    (tmp_path / 'scenarios').mkdir()
    (tmp_path / 'fixtures/demo').mkdir(parents=True)
    (tmp_path / 'ground_truth/c').mkdir(parents=True)
    (tmp_path / 'ground_truth/c/s.json').write_text(json.dumps(base_record()))
    (tmp_path / 'scenarios/s.json').write_text(json.dumps({
        'id': 's', 'fixture_dir': 'demo', 'agents': [{'task': 'inspect {{FIXTURE_DIR}}'}]}))
    fixture = tmp_path / 'fixtures/demo/README.md'
    fixture.write_text('local 127.0.0.1 external 93.184.216.34\n')
    builder.build_prompts()
    assert validate_prompt_sources(tmp_path) == []
    prompt = tmp_path / 'prompt_inputs/cases/s/prompt.txt'
    assert '127.0.0.1' in prompt.read_text()
    assert str(tmp_path) not in (tmp_path / 'prompt_inputs/cases/s/bundle.json').read_text()
    fixture.write_text('changed after frozen prompt')
    assert validate_prompt_sources(tmp_path)






def test_provenance_does_not_guess_missing_reference_by_basename(tmp_path, monkeypatch):
    import pytest
    from scripts.evaluation import repair_package_provenance as repair
    monkeypatch.setattr(repair, 'PKG', tmp_path)
    (tmp_path / 'a.txt').write_text('unrelated')
    assert repair.resolve_ref_path('missing/a.txt') is None
    with pytest.raises(ValueError, match='unresolved'):
        repair.rebase_and_hash({'path': 'missing/a.txt', 'sha256': 'original'})




def test_execution_inputs_accept_unversioned_directory_and_current_fixture(tmp_path):
    from env.tests.test_release_input_chain import source_release
    from scripts.evaluation.validate_fuxibench_package import validate_execution_inputs
    package, _, _ = source_release(tmp_path)
    (package / "release.json").unlink()
    (package / "fixtures/neutral/input.txt").write_text("updated fixture")
    assert validate_execution_inputs(package) == []


def test_validate_reports_structure_without_requiring_release_manifest(tmp_path):
    from scripts.evaluation.validate_fuxibench_package import validate
    result = validate(tmp_path, require_traces=False)
    assert "missing release.json" not in result["problems"]
    assert not result["valid"]
    assert result["counts"]["records"] == 0


def test_traces_are_declared_review_only(tmp_path):
    from scripts.evaluation.validate_fuxibench_package import validate_trace_declarations

    package = tmp_path / "pkg"
    traces = package / "traces"
    traces.mkdir(parents=True)
    (traces / "a.db").write_bytes(b"")
    (traces / "TRACES-NOTICE.md").write_text(
        "The database schema is preserved for replay.\n", encoding="utf-8")
    problems = []
    validate_trace_declarations(
        package, {"traces_collected": 0, "traces_included": True}, problems)
    assert any("traces_replayable" in item for item in problems), problems
    assert any("traces_collected" in item for item in problems), problems
    assert any("replay" in item for item in problems), problems

    problems.clear()
    (traces / "TRACES-NOTICE.md").write_text(
        "Review-only: the schemas predate the current collector.\n", encoding="utf-8")
    validate_trace_declarations(
        package, {"traces_collected": 1, "traces_replayable": False}, problems)
    assert problems == []










def test_builder_places_licenses_in_documented_locations(tmp_path, monkeypatch):
    from scripts.evaluation import package_fuxibench as builder
    source = tmp_path / "source"
    source.mkdir()
    (source / "metadata.json").write_text("{}")
    package = tmp_path / "package"
    (package / "ground_truth/c").mkdir(parents=True)
    (package / "ground_truth/c/s.json").write_text(json.dumps(base_record()))
    monkeypatch.setattr(builder, "SOURCE_PACKAGE", source)
    monkeypatch.setattr(builder, "PACKAGE", package)
    builder.build_docs_metadata()
    assert (package / "LICENSE").is_file()
    assert (package / "docs/licenses/LICENSE-APACHE").is_file()
    assert (package / "docs/licenses/LICENSE-MIT").is_file()
    assert not (package / "LICENSE-DATA").exists()
    metadata = json.loads((package / "metadata.json").read_text())
    assert "paper_metrics_available" not in metadata
    assert "publication_blockers" not in metadata
