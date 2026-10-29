#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Build and validate a detector-blind reference bundle from runtime AI reviews.

This is a contract layer, not another reviewer.  It consumes the output of
``tools.annotator.ai_runtime`` only after that pipeline's own verification has
passed and emits a small, hash-bound manifest that downstream reference
producers may cite:

* reviewers are three fresh AI contexts plus an independent adjudicator
  (``ai_consensus_3``), never human gold;
* the reviewer evidence view contains no detector judgment, claim, verdict or
  ground-truth label;
* logical identity and causal root stay explicitly ``unknown``.  The current
  codebook forces ``root_actor=null``; exporting a root from these reviews
  would fabricate attribution gold.  Entry/chain remain AI hypotheses and are
  counted as such.

The manifest does not copy review notes or evidence bytes.  It binds the
runtime root by SHA-256 and records only coverage/status metadata, so a
reference consumer can verify provenance without accidentally treating the
bundle as a public release.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from tools.annotator import ai_runtime

SCHEMA = "mtagentlens.detector-blind-reference-bundle/1"
PROOF_SCHEMA = "mtagentlens.detector-blind-reference-bundle-proof/1"
VERIFICATION_SCHEMA = "mtagentlens.ai-runtime-verification/v1"
ANNOTATIONS_SCHEMA_PREFIX = "mtagentlens.ai-runtime-"
PROTOCOL = "ai_consensus_3"
METHOD = "ai_consensus_3"
ADJUDICATOR = "dsh-adjudicator"
ROOT_NULL_SOURCE = "codebook_constraint_not_runtime_observation"

# These are detector outputs or ground-truth labels.  Their presence in the
# reviewer artifact means the bundle is not detector-blind.
FORBIDDEN_KEYS = {
    "responsibility_alerts", "final_decision", "final_severity", "attribution",
    "traceback", "tracebacks", "claims", "claim", "verdict", "verdicts",
    "attack_type", "should_alert", "expected_behaviors", "ground_truth",
    "detection_excluded", "paper_ready",
}
FORBIDDEN_TEXT = ("ground_truth/", "responsibility_alerts", "final_decision")


class BundleError(ValueError):
    """The runtime artifact cannot support a detector-blind reference bundle."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BundleError(f"{path}: cannot read JSON: {error}") from error
    if not isinstance(value, dict):
        raise BundleError(f"{path}: expected a JSON object")
    return value


def _string_keys(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _string_keys(child, f"{prefix}.{key}")
    elif isinstance(value, list):
        for child in value:
            yield from _string_keys(child, prefix)


def _scan_detector_residue(value) -> list[str]:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    violations = sorted({key for key in _string_keys(value) if key in FORBIDDEN_KEYS})
    violations.extend(token for token in FORBIDDEN_TEXT if token in text)
    return sorted(set(violations))


def _verification_digest(verification: dict) -> str:
    canonical = json.dumps(verification, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _recompute_verification(runtime_root: Path) -> dict:
    """Rerun the existing pipeline verifier in pure, read-only mode."""
    try:
        verification = ai_runtime.verify_artifact(
            Path(runtime_root), reviewers=3, write_report=False)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise BundleError(
            f"cannot re-verify runtime artifact (inventory/sessions/evidence): {error}") from error
    if not isinstance(verification, dict):
        raise BundleError("re-verification did not return a report")
    return verification


def _require_reverified(verification: dict, annotations: dict) -> None:
    if verification.get("schema") != VERIFICATION_SCHEMA:
        raise BundleError(f"re-verification schema must be {VERIFICATION_SCHEMA}")
    errors = verification.get("errors")
    if errors:
        raise BundleError("re-verification reported errors: " + "; ".join(map(str, errors)))
    if verification.get("valid") is not True:
        raise BundleError("re-verification is not valid; reference export refused")
    if verification.get("tool_events_checked") is not True:
        raise BundleError("re-verification did not check review session tool events")
    if verification.get("all_candidates_reviewed") is not True:
        raise BundleError("re-verification shows unreviewed candidate cases")
    if verification.get("reviewer_count") != 3:
        raise BundleError("re-verification did not use exactly three reviewers")
    if verification.get("adjudicator_id") != ADJUDICATOR:
        raise BundleError(f"re-verification adjudicator must be {ADJUDICATOR}")
    records = annotations.get("records") or []
    if verification.get("completed") != len(records):
        raise BundleError("re-verification completed count disagrees with annotations.records")
    if verification.get("candidate_cases") != len(records):
        raise BundleError("re-verification candidate count disagrees with reviewed records")


def _validate_annotations(annotations: dict, verification: dict) -> None:
    if verification.get("schema") != VERIFICATION_SCHEMA:
        raise BundleError(f"verification.schema must be {VERIFICATION_SCHEMA}")
    if verification.get("valid") is not True:
        raise BundleError("ai_runtime verification is not valid; reference export refused")
    if annotations.get("reviewer_count") != 3:
        raise BundleError("the canonical reference requires exactly three fresh AI reviewers")
    if annotations.get("method") != METHOD:
        raise BundleError(f"annotations.method must be {METHOD}")
    if annotations.get("human_review") is not False:
        raise BundleError("annotations must not claim human review")
    if annotations.get("paper_metrics_available") is not False:
        raise BundleError("annotation pipeline must keep paper_metrics_available=false")
    if annotations.get("adjudicator_id") != ADJUDICATOR:
        raise BundleError(f"annotations.adjudicator_id must be {ADJUDICATOR}")
    if not isinstance(annotations.get("records"), list):
        raise BundleError("annotations.records must be a list")
    if annotations["records"] and verification.get("completed") != len(annotations["records"]):
        raise BundleError("verification.completed disagrees with annotations.records")
    for record in annotations["records"]:
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise BundleError("every record needs a case_id")
        if record.get("method") != METHOD:
            raise BundleError(f"{case_id}: record method must be {METHOD}")
        if record.get("human_review") is not False:
            raise BundleError(f"{case_id}: record must not claim human review")
        if record.get("paper_metrics_available") is not False:
            raise BundleError(f"{case_id}: paper_metrics_available must stay false")
        if record.get("logical_root_actor") is not None:
            raise BundleError(
                f"{case_id}: logical root identity is not available from this protocol")
        if record.get("entry_chain_status") is None:
            raise BundleError(f"{case_id}: entry/chain must be disclosed as AI hypotheses")
        annotation = record.get("annotation") or {}
        roots = annotation.get("roots") or {}
        if roots.get("actors"):
            raise BundleError(
                f"{case_id}: a nonempty root set is not supported by the current codebook")
        policy = record.get("event_identity_policy") or {}
        if policy.get("identity_known", 0) != 0:
            raise BundleError(f"{case_id}: event identity must remain unknown")
    violations = _scan_detector_residue(annotations)
    if violations:
        raise BundleError(
            "detector/ground-truth residue in runtime annotations: " + ", ".join(violations))


def build(runtime_root: Path, out: Path) -> dict:
    runtime_root = Path(runtime_root).resolve()
    inventory = runtime_root / 'inventory.json'
    if inventory.is_file() and _load(inventory).get('purpose') == 'native-identity-attribution/1':
        manifest = _native_manifest(runtime_root)
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            with out.open('x', encoding='utf-8') as handle:
                json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write('\n')
            out.chmod(0o600)
        except OSError as exc:
            raise BundleError(f'cannot create native reference manifest: {exc}') from exc
        return manifest
    annotations_path = runtime_root / "annotations.json"
    verification_path = runtime_root / "verification.json"
    annotations = _load(annotations_path)
    verification = _load(verification_path)
    _validate_annotations(annotations, verification)
    if not str(annotations.get("schema", "")).startswith(ANNOTATIONS_SCHEMA_PREFIX):
        raise BundleError("annotations schema is not an ai_runtime artifact")
    reverified = _recompute_verification(runtime_root)
    _require_reverified(reverified, annotations)
    release_ids = sorted({str(record.get("binding", {}).get("data_release_id"))
                          for record in annotations["records"]
                          if record.get("binding", {}).get("data_release_id")})
    if len(release_ids) != 1:
        raise BundleError(f"expected one bound release, got {release_ids}")
    records = annotations["records"]
    entry_known = sum(record.get("entry") is not None for record in records)
    chain_known = sum(bool(record.get("chain")) for record in records)
    identity_known = sum(
        (record.get("event_identity_policy") or {}).get("identity_known", 0) for record in records)
    manifest = {
        "schema": SCHEMA,
        "release_id": release_ids[0],
        "source": {
            "runtime_root": str(runtime_root),
            "annotations_path": str(annotations_path),
            "annotations_sha256": _sha256(annotations_path),
            "verification_path": str(verification_path),
            "verification_sha256": _sha256(verification_path),
        },
        "proof": {
            "schema": PROOF_SCHEMA,
            "stored_verification_sha256": _sha256(verification_path),
            "recomputed_verification_sha256": _verification_digest(reverified),
            "reviewer_count": reverified["reviewer_count"],
            "completed": reverified["completed"],
            "candidate_cases": reverified["candidate_cases"],
            "all_candidates_reviewed": True,
            "tool_events_checked": True,
            "review_packets_verified": True,
            "root_null_source": ROOT_NULL_SOURCE,
        },
        "protocol": {
            "name": PROTOCOL,
            "method": METHOD,
            "reviewer_count": 3,
            "adjudicator": ADJUDICATOR,
            "human_review": False,
            "human_gold": False,
            "paper_metrics_available": False,
            "independence": "three fresh contexts, same model/provider; errors may be correlated",
        },
        "detector_blind": {
            "checked": True,
            "forbidden_keys": sorted(FORBIDDEN_KEYS),
            "violations": [],
            "scope": ("annotations.json metadata; evidence review packets are re-verified "
                      "by ai_runtime.verify_artifact in read-only mode"),
        },
        "reference": {
            "identity": {
                "status": "unknown",
                "known_units": identity_known,
                "reason": "the current codebook writes explicit event-level unknown, not identity gold",
            },
            "attribution": {
                "root_status": "unknown",
                "root_known": 0,
                "root_null_source": ROOT_NULL_SOURCE,
                "entry_hypotheses": entry_known,
                "chain_hypotheses": chain_known,
                "estimable": False,
                "reason": ("the current codebook forces root_actor null; that is a codebook "
                           "constraint, not a runtime root reference. entry/chain are AI "
                           "hypotheses and cannot serve as independent root gold"),
            },
        },
        "coverage": {
            "records": len(records),
            "verified_completed": verification.get("completed"),
            "candidate_cases": verification.get("candidate_cases"),
            "all_candidates_reviewed": verification.get("all_candidates_reviewed"),
            "entry_known": entry_known,
            "chain_known": chain_known,
            "identity_known": identity_known,
        },
        "limitations": [
            "AI model proposals are not human annotations, inter-rater reliability or gold.",
            "No logical identity or causal root is exported by this bundle.",
            "Entry/chain are hypotheses from a possibly truncated, detector-blind evidence view.",
            "The manifest binds but does not copy sensitive runtime evidence; it is not a public release.",
        ],
    }
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    out.write_text(payload, encoding="utf-8")
    try:
        out.chmod(0o600)
    except OSError:
        pass
    return manifest


def validate(path: Path) -> dict:
    path = Path(path)
    manifest = _load(path)
    if manifest.get('purpose') == 'native-identity-attribution/1':
        try:
            expected = _native_manifest(Path(manifest['source']['runtime_root']))
            problems = [] if expected == manifest else ['native bundle differs from reverified sources/exports']
        except (BundleError, OSError, ValueError, KeyError) as exc:
            problems = [str(exc)]
        return {'schema': 'mtagentlens.detector-blind-reference-bundle-verification/1',
                'valid': not problems, 'problems': problems, 'release_id': manifest.get('release_id')}
    problems = []
    if manifest.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA}")
    protocol = manifest.get("protocol") or {}
    for key, expected in (("name", PROTOCOL), ("method", METHOD),
                          ("reviewer_count", 3), ("adjudicator", ADJUDICATOR)):
        if protocol.get(key) != expected:
            problems.append(f"protocol.{key} must be {expected!r}")
    for key in ("human_review", "human_gold", "paper_metrics_available"):
        if protocol.get(key) is not False:
            problems.append(f"protocol.{key} must be false")
    detector = manifest.get("detector_blind") or {}
    if detector.get("checked") is not True or detector.get("violations"):
        problems.append("detector_blind.checked must be true with no violations")
    source = manifest.get("source") or {}
    proof = manifest.get("proof") or {}
    if not isinstance(proof, dict) or not proof:
        problems.append("missing recomputed verification proof; legacy bundles are not accepted")
    runtime_root = Path(str(source.get("runtime_root", "")))
    annotations_path = Path(str(source.get("annotations_path", "")))
    verification_path = Path(str(source.get("verification_path", "")))
    for label, artifact, expected in (
        ("annotations", annotations_path, source.get("annotations_sha256")),
        ("verification", verification_path, source.get("verification_sha256")),
    ):
        if not artifact.is_file():
            problems.append(f"{label} artifact is missing: {artifact}")
            continue
        actual = _sha256(artifact)
        if actual != expected:
            problems.append(f"{label} sha256 mismatch ({actual} != {expected})")
    if annotations_path.is_file() and verification_path.is_file():
        annotations = _load(annotations_path)
        verification = _load(verification_path)
        try:
            _validate_annotations(annotations, verification)
        except BundleError as error:
            problems.append(str(error))
        violations = _scan_detector_residue(annotations)
        if violations:
            problems.append("detector/ground-truth residue: " + ", ".join(violations))
        for record in annotations.get("records") or []:
            if record.get("logical_root_actor") is not None:
                problems.append(f"{record.get('case_id')}: logical root is not unknown")
        try:
            reverified = _recompute_verification(runtime_root)
            _require_reverified(reverified, annotations)
        except BundleError as error:
            problems.append(str(error))
        else:
            digest = _verification_digest(reverified)
            if proof.get("schema") != PROOF_SCHEMA:
                problems.append(f"proof.schema must be {PROOF_SCHEMA}")
            if proof.get("recomputed_verification_sha256") != digest:
                problems.append("recomputed verification digest mismatch")
            if proof.get("stored_verification_sha256") != source.get("verification_sha256"):
                problems.append("proof stored verification digest disagrees with source")
            for key in ("reviewer_count", "completed", "candidate_cases"):
                if proof.get(key) != reverified.get(key):
                    problems.append(f"proof.{key} disagrees with re-verification")
            if proof.get("tool_events_checked") is not True:
                problems.append("proof.tool_events_checked must be true")
            if proof.get("review_packets_verified") is not True:
                problems.append("proof.review_packets_verified must be true")
            if proof.get("root_null_source") != ROOT_NULL_SOURCE:
                problems.append("proof.root_null_source must disclose the codebook constraint")
            coverage = manifest.get("coverage") or {}
            if coverage.get("verified_completed") != reverified.get("completed"):
                problems.append("coverage.verified_completed disagrees with re-verification")
            if coverage.get("candidate_cases") != reverified.get("candidate_cases"):
                problems.append("coverage.candidate_cases disagrees with re-verification")
    reference = manifest.get("reference") or {}
    attribution = reference.get("attribution") or {}
    if attribution.get("root_known") != 0 or attribution.get("root_status") != "unknown":
        problems.append("attribution root must remain unknown in the current protocol")
    if attribution.get("estimable") is not False:
        problems.append("attribution must be explicitly not estimable without root reference")
    if attribution.get("root_null_source") != ROOT_NULL_SOURCE:
        problems.append("attribution.root_null_source must disclose the codebook constraint")
    identity = reference.get("identity") or {}
    if identity.get("known_units") != 0:
        problems.append("identity must remain unknown in the current protocol")
    return {
        "schema": "mtagentlens.detector-blind-reference-bundle-verification/1",
        "valid": not problems,
        "problems": problems,
        "release_id": manifest.get("release_id"),
        "runtime_root": str(runtime_root),
    }


def _native_manifest(root: Path) -> dict:
    """Bind the new reference purpose without weakening execution-label rules."""
    from tools.annotator import runtime_reference as rr
    try:
        report = rr.verify(root)
        if not report['valid']:
            raise BundleError('native reference verification failed: ' + str(report['errors']))
        inventory = _load(root / 'inventory.json')
        releases, files = set(), []
        coverage = {'identity_units': 0, 'identity_known': 0, 'root_known': 0,
                    'entry_known': 0, 'chain_known': 0}
        for case in inventory['cases']:
            cid = case['case_id']
            packet = _load(root / 'review-packets' / f'{cid}.json')
            releases.add(packet['release_id'])
            path = root / 'references' / f'{cid}.json'
            exported = _load(path)
            files.append({'case_id': cid, 'path': str(path), 'sha256': _sha256(path)})
            identities = exported['identity']['events']
            coverage['identity_units'] += len(identities)
            coverage['identity_known'] += sum(row['status'] == 'known' for row in identities.values())
            for unit in exported['attribution']['units']:
                for field in ('root', 'entry', 'chain'):
                    coverage[f'{field}_known'] += unit[field]['status'] == 'known'
        if len(releases) != 1:
            raise BundleError('native references must bind one release')
        return {'schema': SCHEMA, 'purpose': rr.PURPOSE, 'release_id': next(iter(releases)),
                'source': {'runtime_root': str(root), 'inventory_sha256': _sha256(root / 'inventory.json')},
                'protocol': {'name': rr.PURPOSE, 'method': 'model_proposal', 'reviewer_count': 3,
                             'human_review': False, 'independence': 'fresh contexts; same-model errors may correlate'},
                'proof_sha256': _verification_digest(report), 'reference_files': files,
                'coverage': coverage, 'scientific_results_verified': False,
                'limitations': ['Conditional captured native identity; not authenticated principals.',
                                'Root and chain are AI-supported origin hypotheses, not intervention causes.',
                                'Private source bytes are required for re-verification; not a public evidence release.']}
    except (OSError, ValueError, KeyError) as exc:
        raise BundleError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build", help="build a bundle from a verified ai_runtime root")
    build_parser.add_argument("--runtime-root", type=Path, required=True)
    build_parser.add_argument("--out", type=Path, required=True)
    validate_parser = sub.add_parser("validate", help="re-verify a bundle and its source hashes")
    validate_parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "build":
            manifest = build(args.runtime_root, args.out)
            print(json.dumps({"schema": SCHEMA, "release_id": manifest["release_id"],
                              "out": str(args.out)}))
            return 0
        result = validate(args.bundle)
    except BundleError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
