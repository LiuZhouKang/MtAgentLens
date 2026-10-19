"""Cohen's Kappa — inter-annotator agreement measurement.

Design note: this module deliberately returns ``None`` (not a number) whenever
the coefficient is undefined. The previous implementation returned ``1.0`` —
"perfect agreement" — for missing or degenerate input, which turned absent
data into the best possible score. For a metric meant to support an academic
claim, reporting an uncomputable value as perfect is worse than reporting
nothing.
"""
from __future__ import annotations

from mtagentlens_benchmark.types import GroundTruth


def compute_cohens_kappa(
    annotator_labels: list[list[tuple[str, str]]],
) -> float | None:
    """Compute Cohen's Kappa for two annotators.

    Each annotator's labels is a list of ``(alert_id, severity)`` tuples.

    Returns ``None`` — never a fabricated ``1.0`` — when the coefficient is
    undefined:

    * fewer than two annotators were supplied;
    * the two raters have no alert in common, so there is nothing to compare;
    * expected agreement is 1.0, making ``(po - pe) / (1 - pe)`` a 0/0 form
      (this happens when every rated item falls in a single category).

    Items rated by only one annotator are excluded from BOTH the numerator and
    the denominator. Previously they were dropped from the contingency matrix
    while ``n`` still counted them, which deflated the observed agreement
    silently and without any diagnostic.
    """
    if len(annotator_labels) < 2:
        return None

    rater1 = dict(annotator_labels[0])
    rater2 = dict(annotator_labels[1])

    # Only items both raters scored can inform agreement.
    shared = sorted(set(rater1) & set(rater2))
    if not shared:
        return None

    categories = sorted(set(rater1.values()) | set(rater2.values()))
    n = len(shared)

    matrix = {c1: {c2: 0 for c2 in categories} for c1 in categories}
    for aid in shared:
        matrix[rater1[aid]][rater2[aid]] += 1

    po = sum(matrix[c][c] for c in categories) / n

    pe = 0.0
    for c in categories:
        row = sum(matrix[c].values()) / n
        col = sum(matrix[r1][c] for r1 in categories) / n
        pe += row * col

    if pe >= 1.0:
        # Degenerate: agreement is fully explained by the marginal
        # distribution, so kappa is mathematically undefined.
        return None

    return round((po - pe) / (1 - pe), 4)


def compute_pairwise_kappa(
    ground_truths: list[GroundTruth],
) -> dict[str, float | None]:
    """Compute pairwise Kappa across annotators.

    The returned mapping contains one entry per annotator pair. A pair whose
    kappa is undefined maps to ``None``. ``"average"`` is only present when at
    least one pair produced a value, and is computed over those values alone —
    undefined pairs are never folded in as 1.0.

    With fewer than two annotators there are no pairs, so an empty mapping is
    returned rather than a synthetic ``{"kappa": 1.0}``.
    """
    if len(ground_truths) < 2:
        return {}

    annotator_names = [g.annotator for g in ground_truths]
    result: dict[str, float | None] = {}

    for i in range(len(annotator_names)):
        for j in range(i + 1, len(annotator_names)):
            a1_name, a2_name = annotator_names[i], annotator_names[j]
            gt1 = next((g for g in ground_truths if g.annotator == a1_name), None)
            gt2 = next((g for g in ground_truths if g.annotator == a2_name), None)
            if gt1 is None or gt2 is None:
                continue

            labels1 = [(a.alert_id, a.severity.value) for a in gt1.alerts]
            labels2 = [(a.alert_id, a.severity.value) for a in gt2.alerts]
            result[f"{a1_name}-{a2_name}"] = compute_cohens_kappa([labels1, labels2])

    defined = [v for v in result.values() if v is not None]
    if defined:
        result["average"] = round(sum(defined) / len(defined), 4)
    return result
