#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Agreement between independent run annotators from a v2 annotation export.

The export is produced by `python3 -m tools.annotator export` (or the
adjudicator's full export). Only independent records count: `model_proposal`
records are not a rater, and an adjudication record (`review`) is neither a
rater nor a replacement label. Agreement is descriptive; it does not
authenticate annotators or prove label validity. The project's stated gate is
kappa < 0.6 -> revise the codebook before scaling the annotation round.

Usage:
    python3 scripts/evaluation/annotation_agreement.py \
        --export annotations.json \
        --raters 刘周康 唐浩钺 刘锦样 --out agreement.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluation.gt_agreement import fleiss_kappa, percent_agreement

SCHEMA = "mtagentlens.annotation-agreement/v1"
EXPORT_SCHEMA = "mtagentlens.annotation-export/v2"
KAPPA_GATE = 0.6
FIELDS = ("attempted", "executed", "succeeded", "identities", "roots")
JUDGMENTS = ("yes", "no", "unknown")
METHODS = ("human", "controlled_execution", "model_proposal")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def cohen_kappa(pairs) -> float | None:
    """Pooled Cohen's kappa for aligned rating pairs; None if undefined."""
    pairs = list(pairs)
    if not pairs:
        return None
    n = len(pairs)
    observed = sum(1 for left, right in pairs if left == right) / n
    left_counts = Counter(left for left, _ in pairs)
    right_counts = Counter(right for _, right in pairs)
    expected = sum((left_counts[value] / n) * (right_counts[value] / n)
                   for value in set(left_counts) | set(right_counts))
    if expected >= 1.0:
        return None
    return (observed - expected) / (1.0 - expected)


def pairwise_percent(rows) -> float | None:
    """Average agreement over all rater pairs; None without pairs."""
    total = agree = 0
    for row in rows:
        for index, left in enumerate(row):
            for right in row[index + 1:]:
                total += 1
                agree += left == right
    return None if total == 0 else agree / total


def _validated_export(document, raters):
    _require(isinstance(document, dict) and document.get("schema") == EXPORT_SCHEMA,
             "unsupported annotation export schema")
    _require(document.get("paper_metrics_available") is False,
             "export claims paper metrics; agreement must come from unreleased annotations")
    tasks = document.get("tasks")
    _require(isinstance(tasks, list), "annotation export has no task list")
    _require(isinstance(raters, (tuple, list)) and len(raters) >= 2 and
             len(set(raters)) == len(raters) and
             all(isinstance(name, str) and name.strip() for name in raters),
             "at least two distinct rater names are required")
    versions = set()
    for task in tasks:
        binding = task.get("binding") if isinstance(task, dict) else None
        version = binding.get("codebook_version") if isinstance(binding, dict) else None
        _require(isinstance(version, str) and version.strip(),
                 "task binding has no codebook version")
        versions.add(version)
    _require(len(versions) == 1,
             "mixed codebook versions cannot be compared; re-annotate after a codebook change")
    return tasks, tuple(raters), versions.pop()


def _ratings(annotation, field):
    if field in ("attempted", "executed", "succeeded"):
        judgment = annotation.get(field)
        value = judgment.get("value") if isinstance(judgment, dict) else None
        _require(value in JUDGMENTS, "invalid %s judgment" % field)
        return value
    if field == "identities":
        identities = annotation.get("identities")
        _require(isinstance(identities, dict), "invalid identities field")
        return {event: ("unknown" if actor is None else actor)
                for event, actor in identities.items()}
    roots = annotation.get("roots")
    actors = roots.get("actors") if isinstance(roots, dict) else None
    _require(actors is None or (isinstance(actors, list) and
             all(isinstance(actor, str) for actor in actors)), "invalid roots field")
    return "unknown" if actors is None else "|".join(sorted(actors))


def _collect(tasks, raters):
    adjudicated, ignored_annotators, ignored_proposals = set(), Counter(), Counter()
    task_records = []
    for task in tasks:
        _require(isinstance(task, dict) and isinstance(task.get("task_id"), str) and
                 isinstance(task.get("revisions"), list), "invalid annotation task")
        independent = {}
        for revision in task["revisions"]:
            _require(isinstance(revision, dict) and isinstance(revision.get("annotator"), str) and
                     revision.get("method") in METHODS and
                     isinstance(revision.get("annotation"), dict), "invalid annotation revision")
            if revision["method"] == "model_proposal":
                ignored_proposals[revision["annotator"]] += 1
                continue
            if "review" in revision:
                _require(isinstance(revision["review"], dict), "invalid adjudication record")
                adjudicated.add(task["task_id"])
                continue
            if revision["annotator"] not in raters:
                ignored_annotators[revision["annotator"]] += 1
                continue
            independent[revision["annotator"]] = revision["annotation"]
        task_records.append((task["task_id"], independent))
    return task_records, adjudicated, ignored_annotators, ignored_proposals


def _rows(task_records, raters, field):
    rows = []
    for _, annotations in task_records:
        if not all(name in annotations for name in raters):
            continue
        ratings = [_ratings(annotations[name], field) for name in raters]
        if field == "identities":
            events = sorted(set.intersection(*(set(rating) for rating in ratings)))
            rows.extend([[rating[event] for rating in ratings] for event in events])
        else:
            rows.append(ratings)
    return rows


def _pair_rows(task_records, left, right, field):
    rows = []
    for _, annotations in task_records:
        if left not in annotations or right not in annotations:
            continue
        left_value, right_value = (_ratings(annotations[name], field) for name in (left, right))
        if field == "identities":
            for event in sorted(set(left_value) & set(right_value)):
                rows.append([left_value[event], right_value[event]])
        else:
            rows.append([left_value, right_value])
    return rows


def _identity_omissions(task_records, raters):
    """Events one rater labeled but another omitted are not silently dropped."""
    omitted = 0
    for _, annotations in task_records:
        if not all(name in annotations for name in raters):
            continue
        events = [set(_ratings(annotations[name], "identities")) for name in raters]
        omitted += len(set().union(*events) - set.intersection(*events))
    return omitted


def _field_report(task_records, raters, field):
    rows = _rows(task_records, raters, field)
    pairwise = {}
    for index, left in enumerate(raters):
        for right in raters[index + 1:]:
            pairs = _pair_rows(task_records, left, right, field)
            pairwise["%s/%s" % (left, right)] = {
                "items": len(pairs),
                "cohen_kappa": cohen_kappa(tuple(pair) for pair in pairs),
                "percent_agreement": pairwise_percent([pair for pair in pairs]),
            }
    return {
        "items": len(rows),
        "fleiss_kappa": fleiss_kappa(rows) if len(rows) >= 2 else None,
        "percent_agreement": pairwise_percent(rows),
        "unanimous_rate": percent_agreement(rows),
        "pairwise": pairwise,
    }


def agreement_from_export(document, raters) -> dict:
    """Compute agreement from an already-parsed annotation export."""
    tasks, raters, codebook_version = _validated_export(document, raters)
    task_records, adjudicated, ignored_annotators, ignored_proposals = _collect(tasks, raters)
    all_raters = [(task_id, annotations) for task_id, annotations in task_records
                  if all(name in annotations for name in raters)]
    per_rater = {name: sum(1 for _, annotations in task_records if name in annotations)
                 for name in raters}
    fields = {field: _field_report(task_records, raters, field) for field in FIELDS}
    below = [field for field in FIELDS
             if fields[field]["fleiss_kappa"] is not None and
             fields[field]["fleiss_kappa"] < KAPPA_GATE]
    defined = [field for field in FIELDS if fields[field]["fleiss_kappa"] is not None]
    if below:
        status = "revise_codebook"
    elif defined:
        status = "pass"
    else:
        status = "not_evaluable"
    exact = sum(1 for _, annotations in all_raters
                if len({json.dumps(annotations[name], sort_keys=True) for name in raters}) == 1)
    return {
        "schema": SCHEMA,
        "paper_metrics_available": False,
        "raters": list(raters),
        "codebook_version": codebook_version,
        "coverage": {
            "tasks_total": len(task_records),
            "tasks_with_all_raters": len(all_raters),
            "per_rater": per_rater,
            "tasks_adjudicated": len(adjudicated),
            "identity_events_omitted": _identity_omissions(task_records, raters),
            "ignored_annotators": dict(sorted(ignored_annotators.items())),
            "ignored_model_proposals": dict(sorted(ignored_proposals.items())),
        },
        "exact_agreement": {
            "items": len(all_raters),
            "all_equal": exact,
            "percent": None if not all_raters else exact / len(all_raters),
        },
        "fields": fields,
        "gate": {"kappa_threshold": KAPPA_GATE, "status": status,
                 "fields_below_threshold": below},
        "note": ("Agreement is descriptive and does not authenticate raters or validate labels. "
                 "model_proposal records and adjudication are not independent ratings; "
                 "unknown is a category, not missing agreement. kappa null means undefined "
                 "(too few items or no variation), not perfect agreement."),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", required=True, type=Path,
                        help="annotation export JSON (mtagentlens.annotation-export/v2)")
    parser.add_argument("--raters", required=True, nargs="+",
                        help="independent rater names, at least two")
    parser.add_argument("--out", type=Path, help="write the report here instead of stdout")
    args = parser.parse_args(argv)
    document = json.loads(args.export.read_text(encoding="utf-8"))
    report = agreement_from_export(document, tuple(args.raters))
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
