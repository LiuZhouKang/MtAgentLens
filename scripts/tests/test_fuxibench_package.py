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


def test_restore_uses_verified_annotation_source_without_rewriting_external_ips(tmp_path):
    import hashlib
    from scripts.evaluation.package_fuxibench import restore_annotation_inputs
    source = tmp_path / 'repo'
    source_file = source / 'env/scenarios/fixtures/demo/config.txt'
    source_file.parent.mkdir(parents=True)
    source_file.write_text('local=127.0.0.1\nexternal=93.184.216.34\n')
    package = tmp_path / 'package'
    target = package / 'fixtures/demo/config.txt'
    target.parent.mkdir(parents=True)
    target.write_text('local=93.184.216.34\nexternal=93.184.216.34\n')
    annotation_map = tmp_path / 'case-map.json'
    annotation_map.write_text(json.dumps({'cases': [{'sources': [{'kind': 'fixture', 'origin': {
        'path': 'env/scenarios/fixtures/demo/config.txt',
        'sha256': hashlib.sha256(source_file.read_bytes()).hexdigest()}}]}]}))
    report = restore_annotation_inputs(package, annotation_map, source)
    assert target.read_bytes() == source_file.read_bytes()
    assert report['restored'] == ['fixtures/demo/config.txt']
    source_file.write_text('unverified newer bytes')
    import pytest
    with pytest.raises(ValueError, match='hash mismatch'):
        restore_annotation_inputs(package, annotation_map, source)
    assert target.read_text() == 'local=127.0.0.1\nexternal=93.184.216.34\n'


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


def test_annotation_restore_sanitizes_db_paths_without_changing_ip_or_source(tmp_path):
    import hashlib
    import sqlite3
    from scripts.evaluation.package_fuxibench import restore_annotation_inputs
    from scripts.evaluation.validate_fuxibench_package import validate_annotation_sources
    source = tmp_path / 'repo'
    original = source / 'env/scenarios/fixtures/demo/trace.db'
    original.parent.mkdir(parents=True)
    con = sqlite3.connect(original)
    con.execute('CREATE TABLE events (data TEXT)')
    con.execute('INSERT INTO events VALUES (?)', ('/home/lzk/MtAgentLens/a 127.0.0.1',))
    con.commit()
    con.close()
    original_bytes = original.read_bytes()
    annotation_map = tmp_path / 'map.json'
    annotation_map.write_text(json.dumps({'cases': [{'sources': [{'kind': 'fixture', 'origin': {
        'path': 'env/scenarios/fixtures/demo/trace.db',
        'sha256': hashlib.sha256(original_bytes).hexdigest()}}]}]}))
    package = tmp_path / 'package'
    restore_annotation_inputs(package, annotation_map, source)
    copied = package / 'fixtures/demo/trace.db'
    con = sqlite3.connect(copied)
    assert con.execute('SELECT data FROM events').fetchone()[0] == 'a 127.0.0.1'
    con.close()
    assert b'/home/lzk' not in copied.read_bytes()
    assert original.read_bytes() == original_bytes
    assert validate_annotation_sources(package) == []
    copied.write_bytes(b'changed')
    assert validate_annotation_sources(package)


def test_annotation_verification_cannot_omit_files(tmp_path):
    from scripts.evaluation.validate_fuxibench_package import validate_annotation_sources
    (tmp_path / 'ground_truth/c').mkdir(parents=True)
    (tmp_path / 'ground_truth/c/s.json').write_text(json.dumps(base_record()))
    report = tmp_path / 'evidence/annotation-source-verification.json'
    report.parent.mkdir()
    report.write_text(json.dumps({'schema': 'mtagentlens.annotation-source-verification/1',
        'scenario_ids': ['s'], 'verified_files': 0, 'files': []}))
    assert validate_annotation_sources(tmp_path)


def test_provenance_does_not_guess_missing_reference_by_basename(tmp_path, monkeypatch):
    import pytest
    from scripts.evaluation import repair_package_provenance as repair
    monkeypatch.setattr(repair, 'PKG', tmp_path)
    (tmp_path / 'a.txt').write_text('unrelated')
    assert repair.resolve_ref_path('missing/a.txt') is None
    with pytest.raises(ValueError, match='unresolved'):
        repair.rebase_and_hash({'path': 'missing/a.txt', 'sha256': 'original'})


def test_gt_reference_checker_rejects_wrong_hash_pointer_and_quote(tmp_path):
    import hashlib
    from scripts.evaluation.validate_fuxibench_package import validate_gt_references
    source = tmp_path / 'source.json'
    source.write_text(json.dumps({'task': 'Read the report'}))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    gt = tmp_path / 'ground_truth/c/s.json'
    gt.parent.mkdir(parents=True)
    reference = {'path': 'source.json', 'sha256': digest, 'json_pointer': '/task', 'quote': 'Read the report'}
    record = {'scenario_id': 's', 'label_annotation': {'source': {'path': 'source.json', 'sha256': digest},
        'evidence': {'references': [reference]}}}
    gt.write_text(json.dumps(record))
    assert validate_gt_references(tmp_path) == []
    for key, value in [('sha256', 'wrong'), ('json_pointer', '/absent'), ('quote', 'Invented text')]:
        broken = json.loads(json.dumps(record))
        broken['label_annotation']['evidence']['references'][0][key] = value
        gt.write_text(json.dumps(broken))
        assert validate_gt_references(tmp_path)


def test_package_fixture_binding_matches_real_execution_input_audit(tmp_path, monkeypatch):
    from env.tests.test_release_input_chain import source_release
    from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
    from scripts.evaluation import package_fuxibench as builder
    package, _, config = source_release(tmp_path)
    gt = package / 'ground_truth/c/scenario.json'
    gt.parent.mkdir(parents=True)
    gt.write_text(json.dumps(base_record(scenario_id='scenario')))
    monkeypatch.setattr(builder, 'PACKAGE', package)
    manifest = builder.build_manifest()
    ReleaseInputAudit(package, manifest, config, tmp_path / 'run')


def test_execution_gate_materializes_exact_bound_fixtures_without_running_setup(tmp_path):
    from env.tests.test_release_input_chain import source_release
    from scripts.evaluation.validate_fuxibench_package import validate_execution_inputs
    package, _, _ = source_release(tmp_path)
    assert validate_execution_inputs(package) == []
    (package / 'fixtures/neutral/input.txt').write_text('unbound newer bytes')
    problems = validate_execution_inputs(package)
    assert problems and 'scenario' in problems[0]


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


def _verification_package(tmp_path, *, schema, changes, annotation=None):
    package = tmp_path / "pkg"
    (package / "evidence").mkdir(parents=True)
    (package / "scenarios").mkdir()
    (package / "ground_truth" / "cat").mkdir(parents=True)
    (package / "fixtures").mkdir()
    (package / "roles").mkdir()
    scenario = package / "scenarios/s.json"
    scenario.write_text('{"id": "s"}', encoding="utf-8")
    (package / "ground_truth/cat/s.json").write_text(
        json.dumps({"scenario_id": "s", "label": "attack", "attack_type": "A"}), encoding="utf-8")
    (package / "fixtures/docs.md").write_text("fixture\n", encoding="utf-8")
    role = package / "roles/dev.md"
    role.write_text("You are a developer.\n", encoding="utf-8")
    import hashlib
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    files = [
        {"path": "scenarios/s.json", "kind": "scenario_tasks",
         "annotation_sha256": annotation or sha(scenario), "packaged_sha256": sha(scenario)},
        {"path": "fixtures/docs.md", "kind": "fixture",
         "annotation_sha256": sha(package / "fixtures/docs.md"),
         "packaged_sha256": sha(package / "fixtures/docs.md")},
        {"path": "roles/dev.md", "kind": "role",
         "annotation_sha256": sha(role), "packaged_sha256": sha(role)},
    ]
    report = {"schema": schema, "scenario_ids": ["s"], "verified_files": len(files),
              "files": files, "restored": [], "post_annotation_changes": changes}
    (package / "evidence/annotation-source-verification.json").write_text(
        json.dumps(report), encoding="utf-8")
    return package


def test_v2_annotation_verification_accepts_a_listed_change(tmp_path):
    from scripts.evaluation.validate_fuxibench_package import validate_annotation_sources
    import hashlib
    package = _verification_package(tmp_path, schema="mtagentlens.annotation-source-verification/2",
                                    changes=[], annotation="a" * 64)
    scenario = package / "scenarios/s.json"
    current = hashlib.sha256(scenario.read_bytes()).hexdigest()
    report = json.loads((package / "evidence/annotation-source-verification.json").read_text())
    report["post_annotation_changes"] = [{"path": "scenarios/s.json", "kind": "scenario_tasks",
                                          "from": "a" * 64, "to": current, "reason": "metadata repair"}]
    (package / "evidence/annotation-source-verification.json").write_text(json.dumps(report))
    assert validate_annotation_sources(package) == []


def test_v2_annotation_verification_rejects_a_change_without_a_reason(tmp_path):
    from scripts.evaluation.validate_fuxibench_package import validate_annotation_sources
    import hashlib
    package = _verification_package(tmp_path, schema="mtagentlens.annotation-source-verification/2",
                                    changes=[], annotation="a" * 64)
    current = hashlib.sha256((package / "scenarios/s.json").read_bytes()).hexdigest()
    report = json.loads((package / "evidence/annotation-source-verification.json").read_text())
    report["post_annotation_changes"] = [{"path": "scenarios/s.json", "kind": "scenario_tasks",
                                          "from": "a" * 64, "to": current, "reason": ""}]
    (package / "evidence/annotation-source-verification.json").write_text(json.dumps(report))
    problems = validate_annotation_sources(package)
    assert any("no reason" in item for item in problems), problems


def test_v2_annotation_verification_rejects_a_change_that_does_not_match_bytes(tmp_path):
    from scripts.evaluation.validate_fuxibench_package import validate_annotation_sources
    package = _verification_package(tmp_path, schema="mtagentlens.annotation-source-verification/2",
                                    changes=[{"path": "scenarios/s.json", "kind": "scenario_tasks",
                                              "from": "a" * 64, "to": "b" * 64, "reason": "x"}],
                                    annotation="a" * 64)
    problems = validate_annotation_sources(package)
    assert any("does not match packaged bytes" in item for item in problems), problems
