#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Statistical primitives for the paper's measurement core.

Deliberately dependency-free (stdlib only) so the artifact reproduces without
a scientific stack. Every function takes explicit inputs and has a deterministic
self-test; `python3 scripts/evaluation/stats.py --selftest` is the gate.

Conventions:
    * `successes` / `n` are integer counts.
    * `p` values are two-sided unless the name says otherwise.
    * Bootstrap seeds are explicit; never use an unseeded RNG.
"""

from __future__ import annotations

import argparse
import math
import random
from typing import Callable, Sequence


def wilson_interval(successes: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= successes <= n:
        raise ValueError("successes must be within [0, n]")
    phat = successes / n
    denom = 1.0 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    return max(0.0, centre - margin), min(1.0, centre + margin)


def percentile(sorted_values: Sequence[float], p: float) -> float:
    if not sorted_values:
        raise ValueError("empty sample")
    if not 0.0 <= p <= 100.0:
        raise ValueError("p must be within [0, 100]")
    index = math.ceil((p / 100.0) * len(sorted_values)) - 1
    return sorted_values[min(max(index, 0), len(sorted_values) - 1)]


def _percentile_of(values: Sequence[float], p: float) -> float:
    return percentile(sorted(values), p)


def bca_bootstrap(
    values: Sequence[float],
    statistic: Callable[[Sequence[float]], float] = lambda xs: sum(xs) / len(xs),
    *,
    n_resamples: int = 10_000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """BCa bootstrap interval (Efron). Returns (low, high, point estimate)."""
    if len(values) < 2:
        raise ValueError("BCa needs at least two observations")
    rng = random.Random(seed)
    observed = statistic(values)
    samples: list[float] = []
    n = len(values)
    for _ in range(n_resamples):
        resample = [values[rng.randrange(n)] for _ in range(n)]
        samples.append(statistic(resample))
    samples.sort()

    # Bias correction z0.
    below = sum(1 for value in samples if value < observed)
    prop = min(max(below / n_resamples, 1e-9), 1 - 1e-9)
    z0 = _norm_ppf(prop)

    # Acceleration via jackknife.
    jack = []
    for i in range(n):
        jack.append(statistic(values[:i] + values[i + 1 :]))
    mean_jack = sum(jack) / n
    num = sum((mean_jack - value) ** 3 for value in jack)
    den = 6.0 * (sum((mean_jack - value) ** 2 for value in jack) ** 1.5)
    accel = num / den if den else 0.0

    def adjusted(alpha_level: float) -> float:
        z = _norm_ppf(alpha_level)
        denominator = 1 - accel * (z0 + z)
        if abs(denominator) < 1e-12:
            return alpha_level * 100.0
        value = z0 + (z0 + z) / denominator
        # Keep the percentile inside the sample so extreme acceleration cannot
        # ask for the 0th or 100th element.
        return min(
            max(_norm_cdf(value) * 100.0, 100.0 / len(samples)),
            100.0 - 100.0 / len(samples),
        )

    low = percentile(samples, adjusted(alpha / 2))
    high = percentile(samples, adjusted(1 - alpha / 2))
    return low, high, observed


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be within (0, 1)")
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    ]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    numerator = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
    denominator = ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1
    return numerator / denominator


def _binom_two_sided(k: int, n: int) -> float:
    """Exact two-sided binomial test at p=0.5."""
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(k, n - k) + 1)) / (2**n)
    return min(1.0, 2 * tail)


def mcnemar_exact(b_only: int, c_only: int) -> dict[str, float]:
    """McNemar exact test on paired binary outcomes.

    `b_only` and `c_only` are the discordant pairs (A correct/B wrong and
    A wrong/B correct); concordant pairs do not enter the test.
    """
    if b_only < 0 or c_only < 0:
        raise ValueError("discordant counts must be non-negative")
    n = b_only + c_only
    return {
        "b": float(b_only),
        "c": float(c_only),
        "n_discordant": float(n),
        "p_value": _binom_two_sided(min(b_only, c_only), n),
    }


def holm_bonferroni(p_values: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values (same order as the input)."""
    m = len(p_values)
    if m == 0:
        return []
    indexed = sorted(enumerate(p_values), key=lambda pair: pair[1])
    adjusted = [0.0] * m
    running = 0.0
    for rank, (index, p) in enumerate(indexed):
        value = min(1.0, (m - rank) * p)
        running = max(running, value)
        adjusted[index] = running
    return adjusted


def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> float:
    """Cliff's delta effect size in [-1, 1] (positive: a tends to exceed b)."""
    if not a or not b:
        raise ValueError("both samples must be non-empty")
    greater = sum(1 for x in a for y in b if x > y)
    less = sum(1 for x in a for y in b if x < y)
    return (greater - less) / (len(a) * len(b))


def average_precision(labels: Sequence[int], scores: Sequence[float]) -> float:
    """PR-AUC as average precision (step interpolation)."""
    if len(labels) != len(scores):
        raise ValueError("labels and scores must have equal length")
    positives = sum(1 for label in labels if label)
    if positives == 0:
        raise ValueError("average precision needs at least one positive")
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    tp = fp = 0
    ap = 0.0
    previous_recall = 0.0
    for index in order:
        if labels[index]:
            tp += 1
        else:
            fp += 1
        recall = tp / positives
        if tp + fp > 0:
            precision = tp / (tp + fp)
            ap += (recall - previous_recall) * precision
            previous_recall = recall
    return ap


def expected_calibration_error(
    labels: Sequence[int], probabilities: Sequence[float], bins: int = 10
) -> float:
    """ECE with equal-width bins."""
    if len(labels) != len(probabilities):
        raise ValueError("labels and probabilities must have equal length")
    if not labels:
        return 0.0
    buckets: list[list[tuple[int, float]]] = [[] for _ in range(bins)]
    for label, probability in zip(labels, probabilities):
        index = min(int(probability * bins), bins - 1)
        buckets[index].append((label, probability))
    total = len(labels)
    ece = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        confidence = sum(probability for _, probability in bucket) / len(bucket)
        accuracy = sum(label for label, _ in bucket) / len(bucket)
        ece += (len(bucket) / total) * abs(accuracy - confidence)
    return ece


def selftest() -> None:
    low, high = wilson_interval(0, 20)
    assert low == 0.0 and 0.15 < high < 0.20, (low, high)
    low, high = wilson_interval(20, 20)
    assert 0.80 < low < 0.85 and high == 1.0, (low, high)

    low, high, point = bca_bootstrap([1.0, 2.0, 3.0, 4.0, 5.0], seed=7, n_resamples=2000)
    assert low <= point <= high, (low, point, high)

    result = mcnemar_exact(1, 9)
    assert result["n_discordant"] == 10 and 0.0 < result["p_value"] < 0.05, result

    adjusted = holm_bonferroni([0.01, 0.04, 0.03])
    assert adjusted[0] <= adjusted[2] <= adjusted[1], adjusted

    assert cliffs_delta([3, 4, 5], [1, 2]) == 1.0
    assert average_precision([1, 0, 1], [0.9, 0.8, 0.7]) > 0.8
    assert expected_calibration_error([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) < 0.2
    print("stats selftest: ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
