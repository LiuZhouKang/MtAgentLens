# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Independent AI-resolved attribution reference -> AttributionResults adapter.

The reference is a frozen, explicitly versioned resolution produced by three
fresh reviewers (method ``ai_consensus_3``, ``human_review=false``,
``detector_blind=true``).  It is supplied independently and is never inferred
from predictions.  The alignment file maps *canonical* reference actor/node ids
to *captured* AttributionResults ids and carries its own raw-capture source and
hash plus evidence anchors.

This adapter only projects the existing AttributionResults fields:

* root_groups (set) and ranked_agents (single only) -> Root@1 + RootSet P/R/F1;
* unique_entry -> entry point accuracy;
* typed directed edges are validated, while chain P/R/F1 and depth are
  explicitly untyped directed node-pair metrics (cycles fail closed);
* @3/MRR are not supported by the single-candidate kernel and stay null.

Reference ids are resolved only through the independent alignment into the
captured id namespace; predicted ids are never looked up in the alignment.
Unknown or unmapped *reference* fields abstain field-wise. A known, aligned
reference is always scored: wrong, missing, ambiguous or truncated predictions
are false positives, misses, or both. Scores are agreement with an AI-resolved
reference, not accuracy against human gold.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from compare.tools.root_cause_metrics import _chain_depth, _f1, _mean

REFERENCE_SCHEMA = "mtagentlens.attribution-reference/1"
ALIGNMENT_SCHEMA = "mtagentlens.attribution-alignment/1"
PREDICTION_SCHEMA = "mtl-attribution/1"
EXPERIMENT_SCHEMA = "mtagentlens.attribution-experiment/v1"
REPORT_SCHEMA = "mtagentlens.attribution-reference-report/1"
ADAPTER_VERSION = 1

_FIELDS = ("root", "entry", "chain")
_FORBIDDEN_REFERENCE_KEYS = {
    "claim_id",
    "claim_ids",
    "ranked_agents",
    "prediction",
    "predictions",
}
_FORBIDDEN_ALIGNMENT_KEYS = {"claim_id", "claim_ids", "label", "labels", "auto_match"}


class _Unaligned(Exception):
    """A mapped id is absent or cannot be validated; abstain, do not guess."""


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_str(value: Any, label: str) -> str:
    if not _is_nonempty_str(value):
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _require_digest(value: Any, label: str) -> str:
    digest = _require_str(value, label)
    if len(digest) != 64 or any(
            character not in "0123456789abcdefABCDEF" for character in digest):
        raise ValueError(f"{label} must be a 64-character hex SHA256")
    return digest


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(encoded)


def _validate_provenance(value: Any) -> dict[str, Any]:
    provenance = _require_object(value, "reference.provenance")
    if provenance.get("method") != "ai_consensus_3":
        raise ValueError("reference.provenance.method must be ai_consensus_3")
    if provenance.get("human_review") is not False:
        raise ValueError("reference.provenance.human_review must be exactly false")
    if provenance.get("detector_blind") is not True:
        raise ValueError("reference.provenance.detector_blind must be exactly true")
    reviewers = provenance.get("reviewer_ids")
    if not isinstance(reviewers, list) or len(reviewers) != 3:
        raise ValueError("reference.provenance.reviewer_ids must contain exactly three reviewers")
    if any(not _is_nonempty_str(reviewer) for reviewer in reviewers):
        raise ValueError("reference.provenance.reviewer_ids must be nonempty strings")
    if len(set(reviewers)) != 3:
        raise ValueError("reference.provenance.reviewer_ids must be distinct")
    if "adjudicator_id" in provenance and provenance["adjudicator_id"] is not None:
        adjudicator = _require_str(
            provenance["adjudicator_id"], "reference.provenance.adjudicator_id"
        )
        if adjudicator in reviewers:
            raise ValueError(
                "reference.provenance.adjudicator_id must be distinct from the reviewers"
            )
    if "source_database_sha256" in provenance:
        _require_digest(
            provenance["source_database_sha256"],
            "reference.provenance.source_database_sha256",
        )
    return dict(provenance)


def _validate_reference_field(
    value: Any, field: str, run_id: str, action_id: str
) -> dict[str, Any]:
    field_value = _require_object(value, f"{run_id}/{action_id}.{field}")
    status = field_value.get("status")
    if status not in {"known", "unknown"} or isinstance(status, bool):
        raise ValueError(
            f"{run_id}/{action_id}.{field}.status must be the string 'known' or 'unknown'"
        )
    result: dict[str, Any] = {"status": status}
    if status == "unknown":
        return result
    if field == "root":
        actors = field_value.get("actors")
        if not isinstance(actors, list) or not actors:
            raise ValueError(f"{run_id}/{action_id}.root known requires nonempty actors")
        if any(not _is_nonempty_str(actor) for actor in actors):
            raise ValueError(f"{run_id}/{action_id}.root actors must be nonempty strings")
        if len(set(actors)) != len(actors):
            raise ValueError(f"{run_id}/{action_id}.root actors must be unique")
        result["actors"] = list(actors)
        # Runtime reference v2 records the decisive root step; it is preserved
        # for step-level consumers but not scored by this adapter.
        if "step_ref" in field_value:
            result["step_ref"] = _require_str(
                field_value.get("step_ref"), f"{run_id}/{action_id}.root.step_ref"
            )
    elif field == "entry":
        result["node_id"] = _require_str(
            field_value.get("node_id"), f"{run_id}/{action_id}.entry.node_id"
        )
    else:
        edges = field_value.get("edges")
        if not isinstance(edges, list):
            raise ValueError(f"{run_id}/{action_id}.chain known requires an edges list")
        parsed: list[tuple[str, str]] = []
        for edge in edges:
            if not isinstance(edge, list) or len(edge) != 2:
                raise ValueError(f"{run_id}/{action_id}.chain edge must be [source, target]")
            source = _require_str(edge[0], f"{run_id}/{action_id}.chain source")
            target = _require_str(edge[1], f"{run_id}/{action_id}.chain target")
            parsed.append((source, target))
        if len(set(parsed)) != len(parsed):
            raise ValueError(f"{run_id}/{action_id}.chain edges must be unique")
        _chain_depth(parsed)
        result["edges"] = [[source, target] for source, target in parsed]
    return result


def _validate_runs_without_target(value: Any) -> list[dict[str, Any]]:
    """Coverage rows for benign/unknown/unlocalised runs; they must stay unlabelled."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("reference.runs_without_target must be a list")
    rows: list[dict[str, Any]] = []
    for raw_row in value:
        row = _require_object(raw_row, "runs_without_target row")
        run_id = _require_str(row.get("run_id"), "runs_without_target run_id")
        attack = row.get("attack_executed")
        if attack not in {"yes", "no", "unknown"} or isinstance(attack, bool):
            raise ValueError(
                "runs_without_target.attack_executed must be the string yes/no/unknown")
        if row.get("target_action") is not None:
            raise ValueError(
                "runs_without_target.target_action must be null; a target run belongs in units")
        reason = row.get("reason")
        if reason is not None and not _is_nonempty_str(reason):
            raise ValueError("runs_without_target.reason must be a nonempty string or null")
        rows.append({"run_id": run_id, "attack_executed": attack,
                     "target_action": None, "reason": reason})
    return rows


def validate_reference(value: Any) -> dict[str, Any]:
    reference = _require_object(value, "reference")
    if reference.get("schema") != REFERENCE_SCHEMA:
        raise ValueError(f"reference.schema must be {REFERENCE_SCHEMA}")
    if reference.get("version") != 1 or isinstance(reference.get("version"), bool):
        raise ValueError("reference.version must be 1")
    if reference.get("frozen") is not True:
        raise ValueError("reference.frozen must be exactly true")
    provenance = _validate_provenance(reference.get("provenance"))
    units_value = reference.get("units")
    if not isinstance(units_value, list):
        raise ValueError("reference.units must be a list")
    runs_without_target = _validate_runs_without_target(reference.get("runs_without_target"))
    if not units_value and not runs_without_target:
        raise ValueError(
            "reference.units must be a nonempty list unless runs_without_target "
            "records targetless coverage")

    units: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw_unit in units_value:
        unit = _require_object(raw_unit, "reference unit")
        forbidden = sorted(_FORBIDDEN_REFERENCE_KEYS & set(unit))
        if forbidden:
            raise ValueError(
                "reference must be independently supplied; prediction fields are not "
                f"allowed: {forbidden}"
            )
        run_id = _require_str(unit.get("run_id"), "reference unit run_id")
        target = _require_str(
            unit.get("target_action_id"), "reference unit target_action_id"
        )
        key = (run_id, target)
        if key in seen:
            raise ValueError(f"Duplicate unit {run_id}/{target} in reference")
        seen.add(key)
        parsed_unit = {
            "run_id": run_id,
            "target_action_id": target,
            "root": _validate_reference_field(unit.get("root"), "root", run_id, target),
            "entry": _validate_reference_field(unit.get("entry"), "entry", run_id, target),
            "chain": _validate_reference_field(unit.get("chain"), "chain", run_id, target),
        }
        units.append(parsed_unit)

    target_runs = {unit["run_id"] for unit in units}
    targetless_runs = {row["run_id"] for row in runs_without_target}
    overlap = sorted(target_runs & targetless_runs)
    if overlap:
        raise ValueError(
            f"run(s) {overlap} cannot carry both a target unit and a targetless coverage row")
    coverage = reference.get("coverage")
    if coverage is not None and not isinstance(coverage, dict):
        raise ValueError("reference.coverage must be a JSON object")
    return {
        "schema": REFERENCE_SCHEMA,
        "version": 1,
        "frozen": True,
        "provenance": provenance,
        "units": units,
        "runs": sorted(target_runs),
        "runs_without_target": runs_without_target,
        "coverage": coverage,
    }


def _canonical_ids(reference: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for unit in reference["units"]:
        if unit["root"]["status"] == "known":
            ids.update(unit["root"]["actors"])
        if unit["entry"]["status"] == "known":
            ids.add(unit["entry"]["node_id"])
        if unit["chain"]["status"] == "known":
            for source, target in unit["chain"]["edges"]:
                ids.update((source, target))
    return ids


def validate_alignment(value: Any, reference: Mapping[str, Any]) -> dict[str, Any]:
    alignment = _require_object(value, "alignment")
    if alignment.get("schema") != ALIGNMENT_SCHEMA:
        raise ValueError(f"alignment.schema must be {ALIGNMENT_SCHEMA}")
    if alignment.get("version") != 1 or isinstance(alignment.get("version"), bool):
        raise ValueError("alignment.version must be 1")
    raw = _require_object(alignment.get("raw_capture"), "alignment.raw_capture")
    raw_source = _require_str(raw.get("source"), "alignment.raw_capture.source")
    raw_sha = _require_str(raw.get("sha256"), "alignment.raw_capture.sha256")
    if len(raw_sha) != 64 or any(character not in "0123456789abcdefABCDEF" for character in raw_sha):
        raise ValueError("alignment.raw_capture.sha256 must be a 64-character hex digest")

    mappings = alignment.get("mappings")
    if not isinstance(mappings, list):
        raise ValueError("alignment.mappings must be a list")
    canonical_ids = _canonical_ids(reference)
    actor_map: dict[str, str] = {}
    node_map: dict[str, str] = {}
    seen_canonical: set[tuple[str, str]] = set()
    seen_captured: set[tuple[str, str]] = set()

    for raw_mapping in mappings:
        mapping = _require_object(raw_mapping, "alignment mapping")
        forbidden = sorted(_FORBIDDEN_ALIGNMENT_KEYS & set(mapping))
        if forbidden:
            raise ValueError(
                "alignment must not auto-match by claim id or label: "
                f"{forbidden}"
            )
        canonical = _require_str(mapping.get("canonical_id"), "alignment canonical_id")
        captured = _require_str(mapping.get("captured_id"), "alignment captured_id")
        kind = mapping.get("kind")
        if kind not in {"actor", "node"}:
            raise ValueError("alignment mapping kind must be 'actor' or 'node'")
        evidence = mapping.get("evidence_refs")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("alignment mapping requires nonempty evidence_refs")
        if any(not _is_nonempty_str(item) for item in evidence):
            raise ValueError("alignment evidence_refs must be nonempty strings")
        if canonical not in canonical_ids:
            raise ValueError(
                f"alignment maps canonical id {canonical!r} that is absent from the reference"
            )
        if (canonical, kind) in seen_canonical:
            raise ValueError(f"Duplicate {kind} mapping for canonical id {canonical!r}")
        if (kind, captured) in seen_captured:
            raise ValueError(f"Duplicate {kind} captured id {captured!r}")
        seen_canonical.add((canonical, kind))
        seen_captured.add((kind, captured))
        if kind == "actor":
            actor_map[canonical] = captured
        else:
            node_map[canonical] = captured

    return {
        "schema": ALIGNMENT_SCHEMA,
        "version": 1,
        "raw_capture": {"source": raw_source, "sha256": raw_sha},
        "mappings": mappings,
        "actor_map": actor_map,
        "node_map": node_map,
    }


def _root_actors(claim: Mapping[str, Any]) -> list[str]:
    actors: list[str] = []
    for group in claim.get("root_groups") or []:
        for actor in group.get("actors") or []:
            if actor not in actors:
                actors.append(actor)
    for actor in claim.get("ranked_agents") or []:
        if actor not in actors:
            actors.append(actor)
    return actors


def _validate_claim(claim_value: Any, result_run: str) -> dict[str, Any]:
    claim = _require_object(claim_value, "prediction claim")
    if claim.get("schema") not in (None, PREDICTION_SCHEMA):
        raise ValueError(f"prediction claim schema must be {PREDICTION_SCHEMA}")
    claim_id = _require_str(claim.get("claim_id"), "prediction claim_id")
    run_id = _require_str(claim.get("run_id"), "prediction claim run_id")
    if run_id != result_run:
        raise ValueError(
            f"prediction claim {claim_id} run {run_id!r} does not match result run {result_run!r}"
        )

    occurrences = claim.get("occurrences")
    if not isinstance(occurrences, list):
        raise ValueError(f"{claim_id}: occurrences must be a list")
    node_ids: set[str] = set()
    for occurrence in occurrences:
        occurrence = _require_object(occurrence, f"{claim_id} occurrence")
        node_id = _require_str(occurrence.get("node_id"), f"{claim_id} occurrence node_id")
        _require_str(occurrence.get("kind"), f"{claim_id} occurrence kind")
        if node_id in node_ids:
            raise ValueError(f"{claim_id}: duplicate occurrence node_id {node_id!r}")
        node_ids.add(node_id)
        action_id = occurrence.get("action_id")
        if action_id is not None and not _is_nonempty_str(action_id):
            raise ValueError(f"{claim_id}: occurrence action_id must be a nonempty string or null")

    edges = claim.get("edges")
    if not isinstance(edges, list):
        raise ValueError(f"{claim_id}: edges must be a list")
    edge_ids: set[str] = set()
    typed_pairs: set[tuple[str, str, str]] = set()
    directed_pairs: list[tuple[str, str]] = []
    for edge in edges:
        edge = _require_object(edge, f"{claim_id} edge")
        edge_id = _require_str(edge.get("edge_id"), f"{claim_id} edge_id")
        if edge_id in edge_ids:
            raise ValueError(f"{claim_id}: duplicate edge_id {edge_id!r}")
        edge_ids.add(edge_id)
        edge_type = _require_str(edge.get("edge_type"), f"{claim_id} edge_type")
        source = _require_str(edge.get("source"), f"{claim_id} edge source")
        target = _require_str(edge.get("target"), f"{claim_id} edge target")
        if source not in node_ids or target not in node_ids:
            raise ValueError(f"{claim_id}: edge {edge_id!r} endpoints must be occurrence nodes")
        support_refs = edge.get("support_refs")
        if support_refs is not None:
            if not isinstance(support_refs, list) or any(
                not _is_nonempty_str(ref) for ref in support_refs
            ):
                raise ValueError(f"{claim_id}: edge support_refs must be nonempty strings")
        pair = (source, target, edge_type)
        if pair in typed_pairs:
            raise ValueError(f"{claim_id}: duplicate typed edge {source!r}->{target!r}")
        typed_pairs.add(pair)
        directed_pairs.append((source, target))
    _chain_depth(directed_pairs)

    ranked = claim.get("ranked_agents")
    if ranked is not None:
        if not isinstance(ranked, list) or any(not _is_nonempty_str(actor) for actor in ranked):
            raise ValueError(f"{claim_id}: ranked_agents must be a list of nonempty strings")
        if len(set(ranked)) != len(ranked):
            raise ValueError(f"{claim_id}: duplicate ranked agent")
    unique_entry = claim.get("unique_entry")
    if unique_entry is not None:
        unique_entry = _require_str(unique_entry, f"{claim_id} unique_entry")
        if unique_entry not in node_ids:
            raise ValueError(f"{claim_id}: unique_entry must be an occurrence node_id")

    groups = claim.get("root_groups") or []
    if not isinstance(groups, list):
        raise ValueError(f"{claim_id}: root_groups must be a list")
    for group in groups:
        group = _require_object(group, f"{claim_id} root_group")
        actors = group.get("actors") or []
        if not isinstance(actors, list) or any(not _is_nonempty_str(actor) for actor in actors):
            raise ValueError(f"{claim_id}: root_group actors must be nonempty strings")

    return claim


def _prediction_results(value: Any) -> list[dict[str, Any]]:
    document = _require_object(value, "predictions")
    if "claims" in document:
        results = [document]
    elif isinstance(document.get("results"), list):
        results = document["results"]
    elif isinstance(document.get("predictions"), list):
        results = document["predictions"]
    else:
        raise ValueError("predictions must be an AttributionResults object or a results list")
    by_run: dict[str, dict[str, Any]] = {}
    for raw_result in results:
        result = _require_object(raw_result, "AttributionResults")
        if result.get("schema") != PREDICTION_SCHEMA:
            raise ValueError(f"prediction schema must be {PREDICTION_SCHEMA}")
        run_id = _require_str(result.get("run_id"), "prediction run_id")
        if run_id in by_run:
            raise ValueError(f"Duplicate prediction run_id {run_id!r}")
        claims = result.get("claims")
        if not isinstance(claims, list):
            raise ValueError(f"{run_id}: claims must be a list")
        seen_claims: set[str] = set()
        parsed_claims: list[dict[str, Any]] = []
        for raw_claim in claims:
            claim = _validate_claim(raw_claim, run_id)
            if claim["claim_id"] in seen_claims:
                raise ValueError(f"{run_id}: duplicate claim_id {claim['claim_id']!r}")
            seen_claims.add(claim["claim_id"])
            parsed_claims.append(claim)
        by_run[run_id] = {**result, "schema": PREDICTION_SCHEMA, "run_id": run_id, "claims": parsed_claims}
    return [by_run[run_id] for run_id in sorted(by_run)]


def _select_claim(
    unit: Mapping[str, Any], results_by_run: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    result = results_by_run.get(unit["run_id"])
    if result is None:
        return {"kind": "missing", "claim": None, "claim_id": None, "ambiguous_claim_ids": []}
    matches = [
        claim
        for claim in result["claims"]
        if any(
            occurrence.get("action_id") == unit["target_action_id"]
            for occurrence in claim["occurrences"]
        )
    ]
    if not matches:
        return {"kind": "missing", "claim": None, "claim_id": None, "ambiguous_claim_ids": []}
    if len(matches) > 1:
        return {
            "kind": "ambiguous",
            "claim": None,
            "claim_id": None,
            "ambiguous_claim_ids": [claim["claim_id"] for claim in matches],
        }
    return {
        "kind": "selected",
        "claim": matches[0],
        "claim_id": matches[0]["claim_id"],
        "ambiguous_claim_ids": [],
    }


def _resolve_root(
    field: Mapping[str, Any], alignment: Mapping[str, Any]
) -> dict[str, Any]:
    actors = field["actors"]
    missing = [actor for actor in actors if actor not in alignment["actor_map"]]
    if missing:
        raise _Unaligned(f"unmapped reference actor(s): {missing}")
    return {"captured_actors": [alignment["actor_map"][actor] for actor in actors]}


def _resolve_entry(
    field: Mapping[str, Any], alignment: Mapping[str, Any]
) -> dict[str, Any]:
    canonical = field["node_id"]
    captured = alignment["node_map"].get(canonical)
    if captured is None:
        raise _Unaligned(f"unmapped reference node {canonical!r}")
    return {"captured_node": captured}


def _resolve_chain(
    field: Mapping[str, Any], alignment: Mapping[str, Any]
) -> dict[str, Any]:
    edges: list[tuple[str, str]] = []
    for source, target in field["edges"]:
        captured_source = alignment["node_map"].get(source)
        captured_target = alignment["node_map"].get(target)
        if captured_source is None or captured_target is None:
            missing = [node for node in (source, target) if node not in alignment["node_map"]]
            raise _Unaligned(f"unmapped reference chain node(s): {sorted(set(missing))}")
        edges.append((captured_source, captured_target))
    return {"captured_edges": edges, "reference_depth": _chain_depth(edges)}


def _root_scores(
    resolved: Mapping[str, Any], claim: Mapping[str, Any]
) -> dict[str, float]:
    reference_actors = set(resolved["captured_actors"])
    predicted_actors = set(_root_actors(claim))
    ranked = claim.get("ranked_agents")
    at_one = (
        1.0
        if isinstance(ranked, list) and len(ranked) == 1 and ranked[0] in reference_actors
        else 0.0
    )
    intersection = predicted_actors & reference_actors
    precision = len(intersection) / len(predicted_actors) if predicted_actors else 0.0
    recall = len(intersection) / len(reference_actors)
    return {"root_at_1": at_one, "root_precision": precision, "root_recall": recall}


def _entry_scores(
    resolved: Mapping[str, Any], claim: Mapping[str, Any]
) -> dict[str, float]:
    return {"entry_hit": 1.0 if claim.get("unique_entry") == resolved["captured_node"] else 0.0}


def _chain_scores(
    resolved: Mapping[str, Any], claim: Mapping[str, Any]
) -> dict[str, float]:
    reference_edges = set(resolved["captured_edges"])
    predicted_edges = {(edge["source"], edge["target"]) for edge in claim["edges"]}
    if reference_edges:
        hits = len(reference_edges & predicted_edges)
        precision = hits / len(predicted_edges) if predicted_edges else 0.0
        recall = hits / len(reference_edges)
    else:
        # status=known with edges=[] is an observed zero-edge chain: not unknown.
        precision = 1.0 if not predicted_edges else 0.0
        recall = 1.0
    predicted_depth = _chain_depth([(edge["source"], edge["target"]) for edge in claim["edges"]])
    return {
        "untyped_chain_precision": precision,
        "untyped_chain_recall": recall,
        "untyped_chain_depth_error": float(abs(predicted_depth - resolved["reference_depth"])),
    }


def _missing_scores(field: str, resolved: Mapping[str, Any]) -> dict[str, float]:
    if field == "root":
        return {"root_at_1": 0.0, "root_precision": 0.0, "root_recall": 0.0}
    if field == "entry":
        return {"entry_hit": 0.0}
    return {
        "untyped_chain_precision": 0.0,
        "untyped_chain_recall": 0.0,
        "untyped_chain_depth_error": float(resolved["reference_depth"]),
    }


def _evaluate_unit(
    unit: Mapping[str, Any],
    alignment: Mapping[str, Any],
    selection: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, tuple[str, Any]]]:
    record: dict[str, Any] = {
        "run_id": unit["run_id"],
        "target_action_id": unit["target_action_id"],
        "selected_claim_id": selection["claim_id"],
        "ambiguous_claim_ids": list(selection["ambiguous_claim_ids"]),
        "fields": {},
    }
    events: dict[str, tuple[str, Any]] = {}
    claim = selection["claim"]
    for field in _FIELDS:
        spec = unit[field]
        if spec["status"] == "unknown":
            record["fields"][field] = {"status": "unknown_reference"}
            events[field] = ("unknown", None)
            continue
        try:
            if field == "root":
                resolved = _resolve_root(spec, alignment)
            elif field == "entry":
                resolved = _resolve_entry(spec, alignment)
            else:
                resolved = _resolve_chain(spec, alignment)
        except _Unaligned as reason:
            record["fields"][field] = {"status": "unaligned", "reason": str(reason)}
            events[field] = ("unaligned", str(reason))
            continue
        if selection["kind"] == "ambiguous":
            scores = _missing_scores(field, resolved)
            record["fields"][field] = {"status": "ambiguous_prediction", **scores}
            events[field] = ("ambiguous", scores)
            continue
        if claim is None:
            record["fields"][field] = {"status": "missing_prediction"}
            events[field] = ("missing", _missing_scores(field, resolved))
            continue
        if field == "root":
            scores = _root_scores(resolved, claim)
        elif field == "entry":
            scores = _entry_scores(resolved, claim)
        else:
            scores = _chain_scores(resolved, claim)
        record["fields"][field] = {"status": "evaluated", **scores}
        events[field] = ("evaluated", scores)

    statuses = [record["fields"][field]["status"] for field in _FIELDS]
    if selection["kind"] == "ambiguous":
        record["status"] = "ambiguous_prediction"
    elif selection["kind"] == "missing":
        record["status"] = "missing_prediction"
    elif "evaluated" in statuses:
        record["status"] = "evaluated"
    elif "unaligned" in statuses:
        record["status"] = "unaligned"
    else:
        record["status"] = "unknown_reference"
    return record, events


def _empty_denominator() -> dict[str, Any]:
    return {
        "known": 0,
        "aligned": 0,
        "scored": 0,
        "evaluated": 0,
        "misses": 0,
        "unaligned": 0,
        "ambiguous": 0,
        "unknown": 0,
        "coverage": None,
    }


def _accumulate(denominator: dict[str, Any], state: str) -> None:
    if state == "unknown":
        denominator["unknown"] += 1
        return
    denominator["known"] += 1
    if state == "unaligned":
        denominator["unaligned"] += 1
        return
    denominator["aligned"] += 1
    denominator["scored"] += 1
    if state == "missing":
        denominator["misses"] += 1
        return
    if state == "ambiguous":
        denominator["misses"] += 1
        denominator["ambiguous"] += 1
        return
    denominator["evaluated"] += 1


def _project_experiment(
    value: Any, reference: Mapping[str, Any]
) -> dict[str, Any]:
    """Project the actual replay attribution experiment into predictions.

    Only the existing ``mtl-attribution/1`` results are copied. The wrapper must
    bind the exact source database SHA256 that the independent reference also
    carries; otherwise the projection fails instead of falling back to a
    normalized or unbound prediction.
    """
    experiment = _require_object(value, "attribution experiment")
    if experiment.get("schema") != EXPERIMENT_SCHEMA:
        raise ValueError(f"attribution experiment schema must be {EXPERIMENT_SCHEMA}")
    current = _require_str(
        experiment.get("current_fingerprint"),
        "attribution experiment.current_fingerprint")
    wrapper_db = _require_digest(
        experiment.get("source_database_sha256"),
        "attribution experiment.source_database_sha256")
    reference_db = reference["provenance"].get("source_database_sha256")
    if reference_db is None:
        raise ValueError(
            "attribution experiment requires "
            "reference.provenance.source_database_sha256")
    if wrapper_db.lower() != reference_db.lower():
        raise ValueError(
            "source_database_sha256 mismatch between attribution experiment "
            "and reference")

    runs = experiment.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("attribution experiment.runs must be a nonempty list")
    parsed_runs = []
    seen: set[str] = set()
    for raw_run in runs:
        run = _require_object(raw_run, "attribution experiment run")
        run_id = _require_str(run.get("run_id"), "attribution experiment run_id")
        if run_id in seen:
            raise ValueError(f"Duplicate attribution experiment run_id {run_id!r}")
        seen.add(run_id)
        source_fingerprint = _require_str(
            run.get("source_fingerprint"),
            "attribution experiment run.source_fingerprint")
        run_fingerprint = _require_str(
            run.get("current_fingerprint"),
            "attribution experiment run.current_fingerprint")
        if source_fingerprint != current:
            raise ValueError(
                "attribution experiment current_fingerprint is stale against "
                "run.source_fingerprint")
        if run_fingerprint != current:
            raise ValueError(
                "attribution experiment current_fingerprint mismatches "
                "run.current_fingerprint")
        parsed_runs.append(run)

    by_run = {run["run_id"]: run for run in parsed_runs}
    results = []
    for run_id in reference["runs"]:
        run = by_run.get(run_id)
        if run is None:
            raise ValueError(
                f"attribution experiment has no run_id {run_id!r}")
        attributions = _require_object(
            run.get("attributions"),
            f"attribution experiment run {run_id!r}.attributions")
        if attributions.get("schema") != PREDICTION_SCHEMA:
            raise ValueError(
                f"attribution experiment run {run_id!r} attributions schema "
                f"must be {PREDICTION_SCHEMA}")
        if attributions.get("run_id") != run_id:
            raise ValueError(
                f"attribution experiment run {run_id!r} attributions run_id "
                "does not match")
        results.append(attributions)
    return {"results": results}


def evaluate_reference(
    reference_value: Any,
    alignment_value: Any,
    predictions_value: Any,
    *,
    source_hashes: Mapping[str, str] | None = None,
    input_paths: Mapping[str, str] | None = None,
    raw_capture_verified: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate supplied JSON values without deriving any reference content."""
    reference = validate_reference(reference_value)
    alignment = validate_alignment(alignment_value, reference)
    results = _prediction_results(predictions_value)
    results_by_run = {result["run_id"]: result for result in results}
    reference_runs = set(reference["runs"])
    extra_runs = sorted(set(results_by_run) - reference_runs)
    if extra_runs:
        raise ValueError(
            f"prediction run(s) {extra_runs} do not match reference run(s) {sorted(reference_runs)}"
        )

    denominators = {field: _empty_denominator() for field in _FIELDS}
    arrays: dict[str, list[float]] = {
        "root_at_1": [],
        "root_precision": [],
        "root_recall": [],
        "entry_hit": [],
        "untyped_chain_precision": [],
        "untyped_chain_recall": [],
        "untyped_chain_depth_error": [],
    }
    unit_records: list[dict[str, Any]] = []
    abstentions: list[dict[str, Any]] = []

    for unit in reference["units"]:
        selection = _select_claim(unit, results_by_run)
        record, events = _evaluate_unit(unit, alignment, selection)
        unit_records.append(record)
        for field in _FIELDS:
            state, payload = events[field]
            _accumulate(denominators[field], state)
            if state in {"ambiguous", "unaligned", "missing"}:
                abstentions.append(
                    {
                        "run_id": unit["run_id"],
                        "target_action_id": unit["target_action_id"],
                        "field": field,
                        "code": {
                            "ambiguous": "ambiguous_claims",
                            "unaligned": "unmapped_id",
                            "missing": "missing_prediction",
                        }[state],
                        "reason": (
                            f"multiple claims matched target_action_id: {record['ambiguous_claim_ids']}"
                            if state == "ambiguous"
                            else str(payload) if payload is not None else state
                        ),
                    }
                )
            if state in {"missing", "ambiguous", "evaluated"}:
                for key, value in payload.items():
                    arrays[key].append(value)

    for denominator in denominators.values():
        if denominator["known"]:
            denominator["coverage"] = denominator["evaluated"] / denominator["known"]

    root_at_one = _mean(arrays["root_at_1"])
    root_precision = _mean(arrays["root_precision"])
    root_recall = _mean(arrays["root_recall"])
    chain_precision = _mean(arrays["untyped_chain_precision"])
    chain_recall = _mean(arrays["untyped_chain_recall"])
    metrics = {
        "root_agent_at_1": root_at_one,
        "root_set_precision": root_precision,
        "root_set_recall": root_recall,
        "root_set_f1": _f1(root_precision, root_recall),
        "entry_point_accuracy": _mean(arrays["entry_hit"]),
        "untyped_chain_precision": chain_precision,
        "untyped_chain_recall": chain_recall,
        "untyped_chain_f1": _f1(chain_precision, chain_recall),
        "untyped_chain_depth_mae": _mean(arrays["untyped_chain_depth_error"]),
        # The attribution kernel emits zero or one candidate. A top-k ordering
        # is not available, so these stay unreported rather than fabricated.
        "root_agent_at_3": None,
        "mrr": None,
        "ranking_reported": False,
        "metrics_not_reported": ["root_agent_at_3", "mrr"],
    }

    paths = input_paths or {}
    hashes = source_hashes or {}
    inputs = {
        "reference": {
            "path": paths.get("reference"),
            "sha256": hashes.get("reference") or _canonical_sha(reference_value),
        },
        "alignment": {
            "path": paths.get("alignment"),
            "sha256": hashes.get("alignment") or _canonical_sha(alignment_value),
            "raw_capture": {
                **dict(alignment["raw_capture"]),
                "verified": False,
                **(dict(raw_capture_verified) if raw_capture_verified else {}),
            },
        },
        "predictions": {
            "path": paths.get("predictions"),
            "sha256": hashes.get("predictions") or _canonical_sha(predictions_value),
            "runs": sorted(results_by_run),
        },
    }

    evaluated_units = sum(record["status"] == "evaluated" for record in unit_records)
    return {
        "schema": REPORT_SCHEMA,
        "version": ADAPTER_VERSION,
        "inputs": inputs,
        "reference": {
            "schema": reference["schema"],
            "version": reference["version"],
            "frozen": reference["frozen"],
            "provenance": reference["provenance"],
            "unit_count": len(reference["units"]),
            "runs": reference["runs"],
            "runs_without_target": reference["runs_without_target"],
            "coverage": reference["coverage"],
        },
        "evaluation": {
            "units_total": len(unit_records),
            "units_evaluated": evaluated_units,
            "units_missing_prediction": sum(
                record["status"] == "missing_prediction" for record in unit_records
            ),
            "units_ambiguous": sum(
                record["status"] == "ambiguous_prediction" for record in unit_records
            ),
            "units_unaligned": sum(
                any(field["status"] == "unaligned" for field in record["fields"].values())
                for record in unit_records
            ),
            "units_unknown_only": sum(
                record["status"] == "unknown_reference" for record in unit_records
            ),
            "runs": sorted(results_by_run),
        },
        "metrics": metrics,
        "denominators": denominators,
        "units": unit_records,
        "conventions": {
            "selection": (
                "claims are selected only when an occurrence action_id equals "
                "target_action_id; multiple matching claims are a miss, not an "
                "exclusion, for known reference fields"
            ),
            "root_projection": (
                "Root@1 uses ranked_agents only when exactly one actor is present; "
                "RootSet P/R/F1 uses root_groups actors union ranked_agents; no "
                "ordering is inferred from the set"
            ),
            "typed_edges": (
                "predicted edges are validated as nonempty typed directed occurrence "
                "edges, but the reference has no edge-type vocabulary; chain metrics "
                "are explicitly untyped directed node-id pair metrics "
                "(untyped_chain_*), not typed F1"
            ),
            "evidence_refs": (
                "alignment evidence_refs are declared independent anchors; they are "
                "not semantically auto-verified unless a JSON-pointer verifier is "
                "implemented"
            ),
            "chain_known_empty": (
                "status=known with edges=[] is a known-empty zero-edge chain "
                "(depth 0, P/R/F1=1 against an empty prediction); status=unknown is "
                "excluded and never scored as zero"
            ),
            "top_k": (
                "@3 and MRR are not supported by the single-candidate attribution "
                "kernel; they are null and ranking_reported=false"
            ),
        },
        "disclosure": {
            "reference_method": "ai_consensus_3",
            "human_review": False,
            "human_gold": False,
            "accuracy_claimed": False,
            "note": (
                "Scores measure agreement with an independently supplied AI-resolved "
                "reference; they are not accuracy against human gold."
            ),
        },
        "abstentions": abstentions,
    }


def _verify_raw_capture(alignment_path: Path, alignment_value: Any) -> dict[str, Any]:
    alignment = _require_object(alignment_value, "alignment")
    raw = _require_object(alignment.get("raw_capture"), "alignment.raw_capture")
    source_value = _require_str(raw.get("source"), "alignment.raw_capture.source")
    declared = _require_str(raw.get("sha256"), "alignment.raw_capture.sha256")
    source = Path(source_value)
    if not source.is_absolute():
        source = alignment_path.parent / source
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise ValueError(
            f"alignment.raw_capture source cannot be read: {source}: {exc}"
        ) from exc
    actual = _sha256(data)
    if actual.lower() != declared.lower():
        raise ValueError(
            f"alignment.raw_capture hash drift for {source}: "
            f"declared {declared}, actual {actual}"
        )
    return {"verified": True, "actual_sha256": actual, "resolved_source": str(source)}


def evaluate_files(
    reference_path: str | Path,
    alignment_path: str | Path,
    predictions_path: str | Path,
) -> dict[str, Any]:
    """Read the three inputs and verify the raw capture before evaluating."""
    paths = {
        "reference": Path(reference_path),
        "alignment": Path(alignment_path),
        "predictions": Path(predictions_path),
    }
    raw = {name: path.read_bytes() for name, path in paths.items()}
    values = {name: json.loads(data) for name, data in raw.items()}
    hashes = {name: _sha256(data) for name, data in raw.items()}
    verification = _verify_raw_capture(paths["alignment"], values["alignment"])
    reference = validate_reference(values["reference"])
    predictions_value = values["predictions"]
    schema = predictions_value.get("schema") if isinstance(predictions_value, dict) else None
    if schema == EXPERIMENT_SCHEMA:
        predictions_value = _project_experiment(predictions_value, reference)
    return evaluate_reference(
        reference,
        values["alignment"],
        predictions_value,
        source_hashes=hashes,
        input_paths={name: str(path) for name, path in paths.items()},
        raw_capture_verified=verification,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare an independently resolved attribution reference against "
            "AttributionResults; read-only, JSON on stdout."
        )
    )
    parser.add_argument("--reference", required=True)
    parser.add_argument("--alignment", required=True)
    parser.add_argument("--predictions", required=True)
    args = parser.parse_args(argv)
    report = evaluate_files(args.reference, args.alignment, args.predictions)
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
