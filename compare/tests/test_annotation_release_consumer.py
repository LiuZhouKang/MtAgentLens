# SPDX-License-Identifier: Apache-2.0 OR MIT
"""The external consumer must accept a valid release and reject tampering."""

import hashlib
import json
from pathlib import Path

from scripts.evaluation.annotation_release_consumer import verify_release


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_release(root: Path) -> Path:
    (root / "schema").mkdir(parents=True)
    (root / "cases/case-0001/bundle/sources").mkdir(parents=True)
    (root / "LICENSE").write_text("Apache-2.0 OR MIT\n", encoding="utf-8")
    (root / "README.md").write_text("# dataset\n", encoding="utf-8")
    schema = {"type": "object", "required": ["attempted"], "additionalProperties": True}
    (root / "schema/annotation.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    bundle = {
        "schema": "mtagentlens.evidence-bundle/v1",
        "data_release_id": "a" * 64,
        "scenario_id": "s",
        "run_id": "r",
        "sources": {"0000": {"path": "sources/0000", "sha256": sha256(b"evidence")}},
    }
    frozen = json.dumps(bundle, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    bundle_hash = sha256(frozen.encode())
    (root / "cases/case-0001/bundle/sources/0000").write_bytes(b"evidence")
    (root / "cases/case-0001/bundle/bundle.json").write_text(
        json.dumps({**bundle, "evidence_bundle_hash": bundle_hash}), encoding="utf-8")
    (root / "cases/case-0001/case.json").write_text(json.dumps({"case_id": "case-0001", "task_id": bundle_hash, "split": "dev"}), encoding="utf-8")
    row = {
        "case_id": "case-0001", "task_id": bundle_hash, "annotator": "agent-x",
        "method": "model_proposal", "revision_id": "rev-1", "parent_revision_id": None,
        "saved_at": "2026-10-25T00:00:00+00:00", "codebook_version": "pilot/1",
        "annotation": {"attempted": {}}, "is_model_proposal": True,
    }
    (root / "annotations.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (root / "attestations.json").write_text(
        json.dumps({"attestations": [], "counts": {"attestations": 0}, "paper_metrics_available": False}), encoding="utf-8")
    (root / "reviewers.json").write_text(
        json.dumps({"codebook_version": "pilot/1"}), encoding="utf-8")
    files = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in {"release.json", "reviewers.json"}:
            continue
        files[relative] = sha256(path.read_bytes())
    release = {
        "schema": "mtagentlens.annotation-dataset/1",
        "codebook_version": "pilot/1",
        "cases": [{"case_id": "case-0001", "task_id": bundle_hash, "split": "dev"}],
        "splits": {"case-0001": "dev"},
        "files": files,
        "counts": {
            "annotations": 1, "annotators": 1, "attestations": 0, "cases": 1,
            "human_annotations": 0, "model_proposals": 1,
            "methods": {"controlled_execution": 0, "human": 0, "model_proposal": 1},
            "splits": {"dev": 1, "test": 0},
        },
        "paper_metrics_available": False,
        "publication_blockers": [],
        "note": "test release",
    }
    release["release_id"] = sha256(
        json.dumps({k: v for k, v in release.items() if k != "release_id"},
                   sort_keys=True).encode())
    (root / "release.json").write_text(json.dumps(release), encoding="utf-8")
    reseal(root)
    return root


def test_a_valid_release_verifies(tmp_path: Path):
    report = verify_release(write_release(tmp_path))
    assert report["problems"] == []
    assert report["annotations"] == 1 and report["cases"] == 1


def test_tampered_file_is_rejected(tmp_path: Path):
    root = write_release(tmp_path)
    (root / "LICENSE").write_text("tampered\n", encoding="utf-8")
    assert any("hash mismatch: LICENSE" in problem for problem in verify_release(root)["problems"])


def test_recomputed_release_id_must_match(tmp_path: Path):
    root = write_release(tmp_path)
    release = json.loads((root / "release.json").read_text())
    release["note"] = "edited after signing"
    (root / "release.json").write_text(json.dumps(release))
    problems = verify_release(root)["problems"]
    assert any("release_id mismatch" in problem for problem in problems)


def test_extra_unmanifested_file_is_rejected(tmp_path: Path):
    root = write_release(tmp_path)
    (root / "scratch.bin").write_bytes(b"x")
    assert any("unmanifested files" in problem for problem in verify_release(root)["problems"])


def test_annotation_schema_violation_is_rejected(tmp_path: Path):
    root = write_release(tmp_path)
    path = root / "annotations.jsonl"
    row = json.loads(path.read_text().splitlines()[0])
    row["annotation"] = {}  # required "attempted" missing
    path.write_text(json.dumps(row) + "\n")
    problems = verify_release(root)["problems"]
    assert any("schema violation" in problem for problem in problems)


def test_counts_must_be_recomputable(tmp_path: Path):
    root = write_release(tmp_path)
    release = json.loads((root / "release.json").read_text())
    release["counts"]["human_annotations"] = 3
    release["release_id"] = sha256(json.dumps({k: v for k, v in release.items()
                                               if k != "release_id"}, sort_keys=True).encode())
    (root / "release.json").write_text(json.dumps(release))
    assert any("counts.human_annotations" in problem for problem in verify_release(root)["problems"])


def test_metrics_cannot_be_claimed_without_human_labels(tmp_path: Path):
    root = write_release(tmp_path)
    release = json.loads((root / "release.json").read_text())
    release["paper_metrics_available"] = True
    release["release_id"] = sha256(json.dumps({k: v for k, v in release.items()
                                               if k != "release_id"}, sort_keys=True).encode())
    (root / "release.json").write_text(json.dumps(release))
    assert any("paper_metrics_available" in problem for problem in verify_release(root)["problems"])


def test_bundle_hash_must_match_its_content(tmp_path: Path):
    root = write_release(tmp_path)
    manifest_path = root / "cases/case-0001/bundle/bundle.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["run_id"] = "changed"
    manifest_path.write_text(json.dumps(manifest))
    problems = verify_release(root)["problems"]
    assert any("bundle hash does not match its content" in problem for problem in problems)


def reseal(root: Path) -> None:
    release = json.loads((root / 'release.json').read_text())
    release['files'] = {p.relative_to(root).as_posix(): sha256(p.read_bytes())
                        for p in root.rglob('*') if p.is_file()
                        and p.relative_to(root).as_posix() not in {'release.json', 'reviewers.json'}}
    release['release_id'] = sha256(json.dumps({k: v for k, v in release.items()
                                              if k != 'release_id'}, sort_keys=True).encode())
    (root / 'release.json').write_text(json.dumps(release))
    reviewers = {'schema': 'mtagentlens.reviewer-assignment/v1',
                 'release_id': release['release_id'], 'reviewers': [],
                 'paper_metrics_available': False}
    (root / 'reviewers.json').write_text(json.dumps(reviewers))


def test_resealed_task_substitution_is_rejected(tmp_path: Path):
    root = write_release(tmp_path)
    path = root / 'annotations.jsonl'
    row = json.loads(path.read_text())
    row['task_id'] = 'f' * 64
    path.write_text(json.dumps(row) + '\n')
    reseal(root)
    assert any('task' in p for p in verify_release(root)['problems'])


def test_resealed_broken_revision_chain_is_rejected(tmp_path: Path):
    root = write_release(tmp_path)
    path = root / 'annotations.jsonl'
    row = json.loads(path.read_text())
    row['parent_revision_id'] = 'nonexistent'
    path.write_text(json.dumps(row) + '\n')
    reseal(root)
    assert any('parent' in p for p in verify_release(root)['problems'])


def test_resealed_case_disagreement_is_rejected(tmp_path: Path):
    root = write_release(tmp_path)
    (root / 'cases/case-0001/case.json').write_text('{}')
    reseal(root)
    assert any('case.json' in p for p in verify_release(root)['problems'])


def test_reviewers_sidecar_must_bind_release(tmp_path: Path):
    root = write_release(tmp_path)
    (root / 'reviewers.json').write_text('{}')
    assert any('reviewers.json' in p for p in verify_release(root)['problems'])


def test_inventory_symlink_is_rejected(tmp_path: Path):
    root = write_release(tmp_path)
    path = root / 'LICENSE'
    original = path.read_bytes()
    path.unlink()
    target = tmp_path.parent / (tmp_path.name + '-external-license')
    target.write_bytes(original)
    path.symlink_to(target)
    assert any('symlink' in p for p in verify_release(root)['problems'])


def test_optional_adjudication_review_is_consumable(tmp_path: Path):
    root = write_release(tmp_path)
    path = root / 'annotations.jsonl'
    row = json.loads(path.read_text())
    row['review'] = {'rationale': 'review provenance retained'}
    path.write_text(json.dumps(row) + '\n')
    reseal(root)
    assert verify_release(root)['problems'] == []


def test_resealed_unknown_attestation_proposal_is_rejected(tmp_path: Path):
    root = write_release(tmp_path)
    payload = {'attestations': [{'proposal_revision_id': 'absent'}],
               'counts': {'attestations': 1}, 'paper_metrics_available': False}
    (root / 'attestations.json').write_text(json.dumps(payload))
    release = json.loads((root / 'release.json').read_text())
    release['counts']['attestations'] = 1
    (root / 'release.json').write_text(json.dumps(release))
    reseal(root)
    assert any('proposal' in p for p in verify_release(root)['problems'])


def test_human_method_alone_cannot_make_metrics_available(tmp_path: Path):
    root = write_release(tmp_path)
    row = json.loads((root / 'annotations.jsonl').read_text())
    row.update(method='human', is_model_proposal=False)
    (root / 'annotations.jsonl').write_text(json.dumps(row) + '\n')
    release = json.loads((root / 'release.json').read_text())
    release['paper_metrics_available'] = True
    release['counts'].update(human_annotations=1, model_proposals=0,
                             methods={'controlled_execution': 0, 'human': 1, 'model_proposal': 0})
    (root / 'release.json').write_text(json.dumps(release))
    reseal(root)
    assert any('paper_metrics_available' in p for p in verify_release(root)['problems'])


def test_same_scenario_cannot_cross_splits_after_reseal(tmp_path: Path):
    import shutil
    root = write_release(tmp_path)
    release = json.loads((root / 'release.json').read_text())
    release['cases'][0]['binding'] = {'scenario_id': 'shared-scenario'}
    second = {**release['cases'][0], 'case_id': 'case-0002', 'split': 'test'}
    release['cases'].append(second)
    release['splits']['case-0002'] = 'test'
    release['counts']['cases'] = 2
    release['counts']['splits']['test'] = 1
    shutil.copytree(root / 'cases/case-0001', root / 'cases/case-0002')
    for case in release['cases']:
        (root / 'cases' / case['case_id'] / 'case.json').write_text(json.dumps(case))
    (root / 'release.json').write_text(json.dumps(release))
    reseal(root)
    assert any('scenario' in p and 'split' in p for p in verify_release(root)['problems'])


def test_resealed_dangling_evidence_references_are_rejected(tmp_path: Path):
    root = write_release(tmp_path)
    path = root / 'annotations.jsonl'
    row = json.loads(path.read_text())
    row['annotation'] = {'attempted': {'value': 'yes', 'evidence_refs': ['absent']},
                         'identities': {'absent-event': 'absent-actor'},
                         'roots': {'actors': ['absent-actor'], 'evidence_refs': ['absent']}}
    path.write_text(json.dumps(row) + '\n')
    reseal(root)
    assert any('reference' in p for p in verify_release(root)['problems'])


def test_resealed_forged_event_anchor_is_rejected(tmp_path: Path):
    root = write_release(tmp_path)
    path = root / 'cases/case-0001/bundle/bundle.json'
    bundle = json.loads(path.read_text())
    bundle['events'] = [{'event_id': 'e', 'source_id': '0000', 'byte_offset': 0,
                         'byte_length': 1, 'record_sha256': sha256(b'wrong')}]
    body = {k: v for k, v in bundle.items() if k != 'evidence_bundle_hash'}
    digest = sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                              separators=(',', ':')).encode())
    bundle['evidence_bundle_hash'] = digest
    path.write_text(json.dumps(bundle))
    release = json.loads((root / 'release.json').read_text())
    release['cases'][0]['task_id'] = digest
    (root / 'cases/case-0001/case.json').write_text(json.dumps(release['cases'][0]))
    (root / 'release.json').write_text(json.dumps(release))
    row = json.loads((root / 'annotations.jsonl').read_text())
    row['task_id'] = digest
    (root / 'annotations.jsonl').write_text(json.dumps(row) + '\n')
    reseal(root)
    assert any('anchor' in p for p in verify_release(root)['problems'])
