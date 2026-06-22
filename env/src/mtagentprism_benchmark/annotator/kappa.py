"""Cohen's Kappa — inter-annotator agreement measurement."""
from __future__ import annotations

from mtagentprism_benchmark.types import GroundTruth


def compute_cohens_kappa(
    annotator_labels: list[list[tuple[str, str]]],
) -> float:
    """Compute Cohen's Kappa for two annotators.

    Each annotator's labels is a list of (alert_id, severity) tuples.
    """
    if len(annotator_labels) < 2:
        return 1.0

    rater1 = {a[0]: a[1] for a in annotator_labels[0]}
    rater2 = {a[0]: a[1] for a in annotator_labels[1]}

    all_ids = sorted(set(rater1.keys()) | set(rater2.keys()))
    if not all_ids:
        return 1.0

    categories = sorted(set(list(rater1.values()) + list(rater2.values())))
    n = len(all_ids)

    matrix = {c1: {c2: 0 for c2 in categories} for c1 in categories}

    for aid in all_ids:
        s1 = rater1.get(aid, "MISSING")
        s2 = rater2.get(aid, "MISSING")
        if s1 in matrix and s2 in matrix[s1]:
            matrix[s1][s2] += 1

    po = sum(matrix[c][c] for c in categories) / n

    pe = 0.0
    for c in categories:
        row_sum = sum(matrix[c][c2] for c2 in categories)
        col_sum = sum(matrix[c1][c] for c1 in categories)
        pe += (row_sum / n) * (col_sum / n)

    if pe == 1.0:
        return 1.0

    kappa = (po - pe) / (1 - pe)
    return round(kappa, 4)


def compute_pairwise_kappa(
    ground_truths: list[GroundTruth],
) -> dict[str, float]:
    """Compute pairwise Kappa for multiple annotators."""
    if len(ground_truths) < 2:
        return {"kappa": 1.0}

    annotator_names = [g.annotator for g in ground_truths]
    pairs = []
    for i in range(len(annotator_names)):
        for j in range(i + 1, len(annotator_names)):
            pairs.append((annotator_names[i], annotator_names[j]))

    result = {}
    for a1_name, a2_name in pairs:
        gt1 = next((g for g in ground_truths if g.annotator == a1_name), None)
        gt2 = next((g for g in ground_truths if g.annotator == a2_name), None)
        if gt1 is None or gt2 is None:
            continue

        labels1 = [(a.alert_id, a.severity.value) for a in gt1.alerts]
        labels2 = [(a.alert_id, a.severity.value) for a in gt2.alerts]
        kappa = compute_cohens_kappa([labels1, labels2])
        result[f"{a1_name}-{a2_name}"] = kappa

    if result:
        result["average"] = round(sum(result.values()) / len(result), 4)
    return result
