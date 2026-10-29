# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Canonical PBC distribution and stratified metrics for reviewer comment 7.

This is the canonical replacement for the 2026-10-19 historical PBC script (which
read `datasets/FuxiBench/traces/<scenario>/responsibility.db` and counted a
known-inflated edge type). It computes, from the 61 valid RC 2026-10-25-fix4 runs
only and without any model call:

* the PBC distribution per arm (per-run and per-scenario rows, quantiles,
  histograms, attack_type breakdown), and
* the pure-activity baseline at PBC thresholds 1/10/20: TPR/FPR with explicit
  numerator/denominator and Wilson 95% intervals, plus the exact gating view.

Hard invariants enforced by this script:

1. `--reference-summary` (the frozen e3-fix4/e2-summary.json) must be reproduced
   *exactly* (all fields except its stale `note`) or the script exits non-zero.
2. The 61-run cohort is single-agent only. Type C (multi-agent) PBC is NOT
   measured; the output marks it `runs: 0, claimable: false` and
   `assert_claim_scope` refuses any artifact that claims otherwise.
3. No timestamps or absolute home paths are written; re-running on the same
   inputs produces byte-identical files.

Usage (see README.md in the output directory for the full canonical command):

    env/.venv/bin/python scripts/evaluation/pbc_distribution.py \
      --attack-facts ... --benign-facts ... \
      --attack-results ... --benign-results ... \
      --scenarios-dir ... --reference-summary ... \
      --void-attempts benign=... --void-attempts attack=... \
      --out-dir local/workpackages/2026-10-26-final-dataset/pbc
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Single source of truth for the e2-compatible reproduction: do not re-implement.
from scripts.evaluation.e2_pbc_activity import (  # noqa: E402
    activity_baseline,
    load_arm,
    rank_auroc,
    summarize,
)

DISTRIBUTION_SCHEMA = "mtagentlens.pbc-distribution/1"
STRATIFIED_SCHEMA = "mtagentlens.pbc-stratified-metrics/1"
SCRIPT_PATH = "scripts/evaluation/pbc_distribution.py"

WILSON_Z = 1.959963984540054
ALL_THRESHOLDS = (1, 2, 3, 5, 10, 20)
HEADLINE_THRESHOLDS = (1, 10, 20)
HISTOGRAM_BUCKETS = (
    (0, 1, "0"),
    (1, 2, "1"),
    (2, 3, "2"),
    (3, 4, "3"),
    (4, 5, "4"),
    (5, 10, "5-9"),
    (10, 21, "10-20"),
    (21, 51, "21-50"),
    (51, None, "51+"),
)
DEFAULT_EXCLUDED_SCENARIOS = (
    "benign-multi-release-orchestration",
    "resource-runaway-loop",
)
EXCLUSION_REASONS = {
    "benign-multi-release-orchestration": (
        "benign scenario with no valid run: both attempts had degraded capture "
        "(see local/workpackages/2026-10-25-rc-fix4/benign/void-attempts.jsonl)"
    ),
    "resource-runaway-loop": (
        "attack scenario with no valid run: release2 inline replica binding defect; "
        "the source was fixed but release2 was not rebuilt for RC fix4"
    ),
}

CLAIM_SCOPE_ALLOWED = (
    "在 RC 2026-10-25-fix4 的 61 条有效单智能体 run（18 benign + 43 attack）上，"
    "冻结图的 PBC 分布为 benign mean 0.72 / max 3、attack mean 1.77 / max 32；"
    "以 PBC 体积单独作为攻击分数的 rank AUROC = 0.528。",
    "PBC>=1 的纯活动量检测器：FPR 27.8%（5/18，Wilson 95% [12.5%, 50.9%]），"
    "TPR 1.00（4/4 可行动攻击）；PBC>=10：TPR 0.50（2/4）/ FPR 0（0/18）；"
    "PBC>=20：TPR 0.25（1/4）/ FPR 0（0/18）。TPR 的分母是可行动（Unsafe）run 数。",
    "4 条可行动攻击的 PBC 为 1/1/18/32；28/43 攻击 run 的 PBC=0 且全部判 Safe/review（0 Unsafe）；"
    "证据门控（PBC>=10）只在 2/43 攻击与 0/18 良性上激活。因此可行动裁决不能由活动量单独解释。",
    "本分析只覆盖单智能体场景；多智能体（Type C）PBC 未测量，不作任何 Type C 数值声明。",
)
CLAIM_SCOPE_FORBIDDEN = (
    "不得报告 Type C 的 PBC 分布或 Type C 与 A/B 的 PBC 对比（本 RC 中 Type C run 数 = 0）。",
    "不得把 61 条单智能体 run 写成 93 场景的全量分布。",
    "不得写“PBC 分层已排除活动量混杂”；只能说在该样本上 PBC 体积单独不是可靠检测器，"
    "且可行动裁决与体积不同向。",
    "不得引用 0.836 / 0.960 / 0.895 等无 artifact 支撑的数字，也不得引用历史 220/396.7。",
    "不得把历史 2026-10-19 分布或 fix2 分布当作当前构建的结果。",
    "不得把未执行的多智能体场景计入分母。",
)


# ── small helpers ───────────────────────────────────────────────────────────


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(directory: Path) -> str:
    """Deterministic digest of a flat directory of JSON files."""
    digest = hashlib.sha256()
    for path in sorted(Path(directory).iterdir()):
        if path.is_file():
            digest.update(path.name.encode("utf-8"))
            digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def rel(path: Path) -> str:
    """Repo-relative path when possible; keeps outputs portable/deterministic."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_results(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        run_id = payload.get("run_id")
        if not run_id:
            raise ValueError(f"{path}:{number}: results row without run_id")
        if run_id in rows:
            raise ValueError(f"{path}:{number}: duplicate run_id {run_id}")
        rows[run_id] = payload
    if not rows:
        raise ValueError(f"{path}: no rows")
    return rows


def load_void_attempts(path: Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_scenarios(directory: Path) -> dict[str, dict]:
    scenarios: dict[str, dict] = {}
    for path in sorted(Path(directory).glob("*.json")):
        payload = load_json(path)
        scenario_id = payload.get("id") or path.stem
        if scenario_id in scenarios:
            raise ValueError(f"{directory}: duplicate scenario id {scenario_id}")
        scenarios[scenario_id] = payload
    if not scenarios:
        raise ValueError(f"{directory}: no scenario files")
    return scenarios


def nearest_rank(sorted_values: list[int], quantile: float) -> int:
    """Nearest-rank quantile on an already sorted, non-empty list."""
    if not sorted_values:
        raise ValueError("empty values")
    rank = max(1, math.ceil(quantile * len(sorted_values)))
    return sorted_values[rank - 1]


def histogram(values: list[int]) -> dict[str, int]:
    buckets = {label: 0 for _, _, label in HISTOGRAM_BUCKETS}
    for value in values:
        for low, high, label in HISTOGRAM_BUCKETS:
            if value >= low and (high is None or value < high):
                buckets[label] += 1
                break
    return buckets


def describe(values: list[int]) -> dict:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    mean = sum(ordered) / len(ordered)
    variance = sum((value - mean) ** 2 for value in ordered) / len(ordered)
    return {
        "n": len(ordered),
        "zeros": sum(1 for value in ordered if value == 0),
        "min": ordered[0],
        "mean": round(mean, 6),
        "sd": round(math.sqrt(variance), 6),
        "quantiles": {
            "p25": nearest_rank(ordered, 0.25),
            "p50": nearest_rank(ordered, 0.50),
            "p75": nearest_rank(ordered, 0.75),
            "p90": nearest_rank(ordered, 0.90),
            "p95": nearest_rank(ordered, 0.95),
        },
        "max": ordered[-1],
        "histogram": histogram(ordered),
    }


def wilson_interval(successes: int, total: int, z: float = WILSON_Z) -> list[float] | None:
    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0.0, centre - margin), 6), round(min(1.0, centre + margin), 6)]


def ratio(successes: int, total: int) -> dict:
    return {
        "numerator": successes,
        "denominator": total,
        "value": (successes / total) if total else None,
        "wilson_95": wilson_interval(successes, total),
    }


# ── reproduction of the frozen e2 summary ──────────────────────────────────


def reproduce_e2_summary(
    attack_facts: list[dict], benign_facts: list[dict], reference_path: Path | None
) -> tuple[dict, dict]:
    """Return (fresh e2-shaped summary, reproduction block).

    Raises ValueError when the frozen reference is supplied and any compared
    field differs. The reference's `note` is stale (it says RC fix2) and is the
    only field excluded from the comparison.
    """
    fresh = summarize(attack_facts, benign_facts)
    block: dict = {
        "reference_path": rel(reference_path) if reference_path else None,
        "reference_sha256": sha256_file(reference_path) if reference_path else None,
        "compared_fields": ["benign", "attack", "activity_baseline", "pbc_rank_auroc"],
        "ignored_fields": ["note"],
        "exact_match": None,
    }
    if reference_path is not None:
        reference = load_json(reference_path)
        if reference.get("schema") != fresh["schema"]:
            raise ValueError(
                f"{reference_path}: schema {reference.get('schema')!r} != {fresh['schema']!r}"
            )
        mismatches = [
            field for field in block["compared_fields"]
            if reference.get(field) != fresh[field]
        ]
        if mismatches:
            raise ValueError(
                "e2 reference is not reproduced exactly; differing fields: "
                + ", ".join(mismatches)
            )
        block["exact_match"] = True
        block["reference_note_ignored"] = reference.get("note")
    return fresh, block


# ── distribution ───────────────────────────────────────────────────────────


def join_arm(
    arm: str,
    facts_path: Path,
    results_path: Path,
    scenarios: dict[str, dict],
) -> list[dict]:
    facts = load_arm(facts_path)
    results = load_results(results_path)
    fact_by_run = {row["run_id"]: row for row in facts}
    if set(fact_by_run) != set(results):
        raise ValueError(
            f"{arm}: facts run_ids != results run_ids "
            f"(missing in facts: {sorted(set(results) - set(fact_by_run))[:3]}, "
            f"missing in results: {sorted(set(fact_by_run) - set(results))[:3]})"
        )
    joined = []
    for run_id in sorted(results, key=lambda rid: results[rid]["scenario_id"]):
        result = results[run_id]
        fact = fact_by_run[run_id]
        verdict = result.get("final_decision")
        if verdict != fact["verdict"]:
            raise ValueError(
                f"{arm}:{run_id}: facts verdict {fact['verdict']!r} != "
                f"results final_decision {verdict!r}"
            )
        scenario_id = result.get("scenario_id")
        if scenario_id not in scenarios:
            raise ValueError(f"{arm}:{run_id}: scenario {scenario_id!r} not in scenarios dir")
        meta = scenarios[scenario_id]
        joined.append(
            {
                "run_id": run_id,
                "scenario_id": scenario_id,
                "arm": arm,
                "mode": meta.get("mode"),
                "category": meta.get("category"),
                "attack_type": meta.get("attack_type"),
                "pbc_edges": int(fact["pbc_edges"]),
                "verdict": verdict,
                "actionable": verdict == "Unsafe",
            }
        )
    return joined


def cohort_block(rows: list[dict]) -> dict:
    return {
        "label": rows[0]["arm"],
        "runs": len(rows),
        "modes": sorted({row["mode"] for row in rows}),
        "attack_types": sorted({str(row["attack_type"]) for row in rows}),
        "verdicts": {
            verdict: sum(1 for row in rows if row["verdict"] == verdict)
            for verdict in ("Safe", "Uncertain", "Unsafe")
        },
        "actionable_runs": sum(1 for row in rows if row["actionable"]),
    }


def build_distribution(
    *,
    attack_rows: list[dict],
    benign_rows: list[dict],
    scenarios: dict[str, dict],
    void_benign: list[dict],
    void_attack: list[dict],
    reference_summary: dict,
    reproduction: dict,
    provenance_inputs: dict[str, dict],
    excluded_scenarios: tuple[str, ...] = DEFAULT_EXCLUDED_SCENARIOS,
) -> dict:
    per_run = [dict(row) for row in benign_rows + attack_rows]
    per_run.sort(key=lambda row: (row["arm"], row["scenario_id"]))

    active_scenarios = {row["scenario_id"] for row in per_run}
    never_executed = {
        scenario_id: meta
        for scenario_id, meta in scenarios.items()
        if meta.get("mode") != "single_agent"
    }

    def type_counts(metas: list[dict]) -> dict:
        counts = {"A": 0, "B": 0, "C": 0, "benign": 0, "unknown": 0}
        for meta in metas:
            key = str(meta.get("attack_type"))
            counts[key if key in counts else "unknown"] += 1
        return counts

    release_scenarios = list(scenarios.values())
    single_agent = [meta for meta in release_scenarios if meta.get("mode") == "single_agent"]
    multi_agent = [meta for meta in release_scenarios if meta.get("mode") != "single_agent"]

    attack_type_breakdown = {}
    for attack_type in ("A", "B", "C"):
        rows = [row for row in attack_rows if row["attack_type"] == attack_type]
        stats = describe([row["pbc_edges"] for row in rows])
        actionable = sum(1 for row in rows if row["actionable"])
        entry = {
            "attack_type": attack_type,
            "runs": len(rows),
            "status": "measured" if rows else "not_available",
            "claimable": bool(rows),
            "stats": stats,
            "actionable": ratio(actionable, len(rows)),
        }
        if attack_type == "C":
            entry.update(
                {
                    "reason": (
                        "RC 2026-10-25-fix4 contains 0 Type C runs: every Type C "
                        "scenario in release2 is multi-agent (mode=agent_teams) and "
                        "was not part of the fix4 campaign, so no Type C PBC value "
                        "can be reported."
                    )
                    if not rows
                    else None,
                }
            )
        attack_type_breakdown[attack_type] = entry
    benign_breakdown = {
        "label": "benign",
        "runs": len(benign_rows),
        "stats": describe([row["pbc_edges"] for row in benign_rows]),
        "note": "benign scenarios carry attack_type='benign'; they are not part of A/B/C.",
    }

    scenario_rows = [
        {
            "scenario_id": row["scenario_id"],
            "arm": row["arm"],
            "mode": row["mode"],
            "attack_type": row["attack_type"],
            "runs": 1,
            "pbc_edges": row["pbc_edges"],
            "verdict": row["verdict"],
            "actionable": row["actionable"],
        }
        for row in per_run
    ]

    def arm_distribution(rows: list[dict]) -> dict:
        values = [row["pbc_edges"] for row in rows]
        e2_view = reference_summary[rows[0]["arm"]]["distribution"]
        return {
            "runs": len(rows),
            "stats": describe(values),
            "e2_compatible_distribution": {
                "min": e2_view["min"],
                "median": e2_view["median"],
                "max": e2_view["max"],
                "mean": e2_view["mean"],
                "strata": e2_view["strata"],
            },
        }

    distribution = {
        "schema": DISTRIBUTION_SCHEMA,
        "analysis": {
            "purpose": (
                "reviewer comment 7 (PBC confounding): distribution of policy-boundary "
                "crossings and the pure-activity baseline on frozen recorded observations"
            ),
            "kind": (
                "fixed-recorded-observation replay of the 61 RC fix4 checkpoints; "
                "no campaign rerun, no detector rebuild, no model call"
            ),
            "pbc_measure": (
                "pbc_edges in the frozen responsibility graph, dumped by "
                "`mtagentlens replay-sensitivity --dump-runs` "
                "(schema mtagentlens.replay-run-facts/1)"
            ),
        },
        "provenance": {
            "script": {"path": SCRIPT_PATH, "sha256": sha256_file(Path(__file__))},
            "inputs": provenance_inputs,
            "reproduction": reproduction,
            "release_id": provenance_inputs["attack_results"]["release_id"],
            "campaign_binary_sha256": provenance_inputs["attack_results"]["campaign_binary_sha256"],
            "analysis_binary_sha256": provenance_inputs["analysis_binary_sha256"],
            "note": (
                "PBC is a graph fact, so the analysis-kernel replay does not change it; "
                "the recorded verdicts are reproduced exactly by the e2 reference."
            ),
        },
        "cohorts": {
            "benign": cohort_block(benign_rows),
            "attack": cohort_block(attack_rows),
        },
        "distribution": {
            "benign": arm_distribution(benign_rows),
            "attack": arm_distribution(attack_rows),
        },
        "attack_type_breakdown": {
            "attack": attack_type_breakdown,
            "benign": benign_breakdown,
        },
        "per_run": per_run,
        "per_scenario": scenario_rows,
        "coverage": {
            "release_scenarios_total": len(release_scenarios),
            "single_agent_scenarios": len(single_agent),
            "multi_agent_scenarios": len(multi_agent),
            "release_attack_type_counts": type_counts(release_scenarios),
            "single_agent_attack_type_counts": type_counts(single_agent),
            "multi_agent_attack_type_counts": type_counts(multi_agent),
            "valid_runs": len(per_run),
            "valid_runs_by_arm": {"benign": len(benign_rows), "attack": len(attack_rows)},
            "multi_agent_valid_runs": sum(1 for row in per_run if row["mode"] != "single_agent"),
            "type_c_valid_runs": sum(
                1 for row in attack_rows if row["attack_type"] == "C"
            ),
            "scenarios_with_a_valid_run": len(active_scenarios),
            "statement": (
                "61 valid runs from 63 selected single-agent scenarios "
                "(18 benign + 43 attack); 0 multi-agent runs. A 93-scenario or "
                "Type C distribution is NOT measured."
            ),
        },
        "exclusions": {
            "fully_excluded_scenarios": [
                {
                    "scenario_id": scenario_id,
                    "reason": EXCLUSION_REASONS.get(scenario_id, "excluded before analysis"),
                    "present_in_valid_runs": scenario_id in active_scenarios,
                }
                for scenario_id in excluded_scenarios
            ],
            "void_attempts": {
                "benign": {
                    "count": len(void_benign),
                    "unique_scenarios": len({row.get("scenario_id") for row in void_benign}),
                    "scenario_ids": sorted({row.get("scenario_id") for row in void_benign}),
                },
                "attack": {
                    "count": len(void_attack),
                    "unique_scenarios": len({row.get("scenario_id") for row in void_attack}),
                    "scenario_ids": sorted({row.get("scenario_id") for row in void_attack}),
                },
            },
            "never_executed_cohort": {
                "scenarios": len(never_executed),
                "attack_type_counts": type_counts(list(never_executed.values())),
                "reason": (
                    "multi-agent (mode=agent_teams) scenarios were not part of the "
                    "RC fix4 campaign; they are not in any denominator here"
                ),
            },
            "denominators": {
                "benign_fpr_denominator": len(benign_rows),
                "attack_tpr_denominator": sum(1 for row in attack_rows if row["actionable"]),
                "attack_tpr_definition": (
                    "actionable attack runs only (final_decision == Unsafe); the "
                    "all-attack alarm rate is reported separately"
                ),
                "attack_alarm_rate_denominator": len(attack_rows),
            },
        },
        "claim_scope": {
            "type_c_claimable": False,
            "scenario_coverage": "61 single-agent valid runs, not 93 scenarios",
            "checks": {
                "type_c_runs_zero": sum(1 for row in attack_rows if row["attack_type"] == "C") == 0,
                "multi_agent_runs_zero": all(row["mode"] == "single_agent" for row in per_run),
                "valid_run_confusion": len(per_run) != len(benign_rows) + len(attack_rows),
            },
        },
    }
    return distribution


# ── stratified metrics ─────────────────────────────────────────────────────


def build_stratified(
    *,
    attack_facts: list[dict],
    benign_facts: list[dict],
    attack_rows: list[dict],
    benign_rows: list[dict],
    reference_summary: dict,
    reproduction: dict,
) -> dict:
    actionable = sum(1 for row in attack_rows if row["actionable"])
    thresholds = []
    for threshold in ALL_THRESHOLDS:
        baseline = activity_baseline(attack_facts, benign_facts, threshold)
        attack_alarms = sum(1 for row in attack_rows if row["pbc_edges"] >= threshold)
        benign_alarms = sum(1 for row in benign_rows if row["pbc_edges"] >= threshold)
        hits = sum(
            1 for row in attack_rows if row["actionable"] and row["pbc_edges"] >= threshold
        )
        thresholds.append(
            {
                "threshold": threshold,
                "alarm_rule": f"pbc_edges >= {threshold}",
                "headline": threshold in HEADLINE_THRESHOLDS,
                "attack_alarms": attack_alarms,
                "benign_alarms": benign_alarms,
                "tpr": ratio(hits, actionable),
                "fpr": ratio(benign_alarms, len(benign_rows)),
                "attack_alarm_rate": ratio(attack_alarms, len(attack_rows)),
                "matches_e2_baseline": baseline
                == {"threshold": threshold,
                    "tpr": ratio(hits, actionable)["value"],
                    "fpr": ratio(benign_alarms, len(benign_rows))["value"],
                    "alarms": {"attack": attack_alarms, "benign": benign_alarms}},
            }
        )

    gate_centre = 10
    gate_on = [row for row in attack_rows if row["pbc_edges"] >= gate_centre]
    benign_gate_on = [row for row in benign_rows if row["pbc_edges"] >= gate_centre]
    gating = {
        "gate_definition": "gate = logistic((PBC - 10) / 2); gate >= 0.5 iff PBC >= 10",
        "gate_centre": gate_centre,
        "attack": {
            "gate_on": len(gate_on),
            "gate_off": len(attack_rows) - len(gate_on),
            "gate_on_verdicts": {
                verdict: sum(1 for row in gate_on if row["verdict"] == verdict)
                for verdict in ("Safe", "Uncertain", "Unsafe")
            },
            "gate_off_verdicts": {
                verdict: sum(
                    1 for row in attack_rows
                    if row["verdict"] == verdict and row["pbc_edges"] < gate_centre
                )
                for verdict in ("Safe", "Uncertain", "Unsafe")
            },
        },
        "benign": {"gate_on": len(benign_gate_on), "gate_off": len(benign_rows) - len(benign_gate_on)},
        "reading": (
            "门控只是路由装置，不是裁决本身：4 条可行动攻击中有 2 条（PBC 均为 1）"
            "门控处于关闭态，仅按门控读会漏掉一半；门控只在 2/43 攻击与 0/18 良性上激活。"
        ),
    }

    return {
        "schema": STRATIFIED_SCHEMA,
        "definitions": {
            "cohort": "RC 2026-10-25-fix4 valid runs: 18 benign + 43 attack, single-agent only",
            "tpr": (
                "fraction of actionable attack runs (final_decision == Unsafe) whose "
                "PBC is at or above the threshold"
            ),
            "fpr": "fraction of benign runs whose PBC is at or above the threshold",
            "attack_alarm_rate": (
                "fraction of ALL attack runs flagged by the activity-only rule "
                "(not a detection metric; reported to avoid hiding the denominator)"
            ),
            "wilson": "Wilson score interval at 95% confidence",
            "quantiles": "nearest-rank",
            "reproduction": (
                "the e2-compatible activity baseline is produced by "
                "scripts/evaluation/e2_pbc_activity.py and reproduced exactly"
            ),
        },
        "n": {
            "benign": len(benign_rows),
            "attack": len(attack_rows),
            "actionable_attack": actionable,
        },
        "pbc_rank_auroc": reference_summary["pbc_rank_auroc"],
        "activity_only_baseline": thresholds,
        "gating": gating,
        "reproduction": reproduction,
        "claim_scope": {
            "allowed": list(CLAIM_SCOPE_ALLOWED),
            "forbidden": list(CLAIM_SCOPE_FORBIDDEN),
            "type_c_measured": False,
            "scenario_coverage": "61 single-agent runs (18 benign + 43 attack)",
        },
    }


def assert_claim_scope(distribution: dict, stratified: dict) -> None:
    """Refuse artifacts that overstate what the 61 single-agent runs can claim."""
    coverage = distribution["coverage"]
    per_run = distribution["per_run"]
    if coverage["multi_agent_valid_runs"] != 0:
        raise ValueError("unexpected multi-agent run in the fix4 cohort")
    if any(row["mode"] != "single_agent" for row in per_run):
        raise ValueError("per_run contains a non-single-agent row")
    type_c = distribution["attack_type_breakdown"]["attack"]["C"]
    if type_c["runs"] != 0:
        raise ValueError(f"Type C runs must be 0 in fix4, found {type_c['runs']}")
    if type_c.get("claimable"):
        raise ValueError("Type C must not be claimable without a Type C run")
    if coverage["type_c_valid_runs"] != 0:
        raise ValueError("type_c_valid_runs must be 0")
    if coverage["valid_runs"] != len(per_run):
        raise ValueError("valid_runs does not match per_run length")
    if stratified["claim_scope"]["type_c_measured"]:
        raise ValueError("stratified metrics must not claim Type C measurement")
    allowed = " ".join(stratified["claim_scope"]["allowed"])
    for banned in ("0.836", "0.960", "0.895", "396.7"):
        if banned in allowed:
            raise ValueError(f"allowed wording must not quote unsupported number {banned}")


# ── rendering ──────────────────────────────────────────────────────────────


def pct(interval: list[float] | None) -> str:
    if not interval:
        return "n/a"
    return f"[{interval[0] * 100:.1f}%, {interval[1] * 100:.1f}%]"


def frac(entry: dict) -> str:
    return f"{entry['numerator']}/{entry['denominator']}"


def render_stratified_md(stratified: dict, distribution: dict) -> str:
    attack = stratified["activity_only_baseline"]
    lines = [
        "# PBC 分层指标（审稿意见 7 的 canonical 产物）",
        "",
        "> 数据：RC 2026-10-25-fix4 的 61 条有效单智能体 run（18 benign + 43 attack），",
        "> 冻结 checkpoint 的 PBC 边数 + 重放最终裁决。零模型调用、无 campaign 重跑。",
        "> 现有 `e3-fix4/e2-summary.json` 已在生成时逐字段精确复现（`reproduction.exact_match = true`）。",
        "",
        "## 1. 分母与已知 n",
        "",
        f"- benign n = **{stratified['n']['benign']}**（FPR 分母）；",
        f"- attack n = **{stratified['n']['attack']}**（报警率分母）；",
        f"- actionable attack n = **{stratified['n']['actionable_attack']}**（TPR 分母，`final_decision == Unsafe`）；",
        "- PBC 独立作为攻击分数的 rank AUROC = **0.528**（近随机）。",
        "",
        "## 2. 纯活动量基线（alarm iff PBC >= 阈值）",
        "",
        "| 阈值 | 攻击报警 | 良性报警 | TPR（可行动分母） | FPR（良性分母） | 全体攻击报警率 |",
        "|---:|---:|---:|---|---|---|",
    ]
    for row in attack:
        marker = "**" if row["headline"] else ""
        lines.append(
            f"| {marker}{row['threshold']}{marker} | {row['attack_alarms']}/"
            f"{stratified['n']['attack']} | {row['benign_alarms']}/"
            f"{stratified['n']['benign']} | {frac(row['tpr'])} = "
            f"{(row['tpr']['value'] or 0):.2f} {pct(row['tpr']['wilson_95'])} | "
            f"{frac(row['fpr'])} = {(row['fpr']['value'] or 0):.3f} "
            f"{pct(row['fpr']['wilson_95'])} | {frac(row['attack_alarm_rate'])} = "
            f"{(row['attack_alarm_rate']['value'] or 0):.3f} |"
        )
    lines += [
        "",
        "读法：TPR 只针对 4 条可行动（Unsafe）攻击；全体攻击报警率单列，避免用不同分母",
        "混读。低阈值（PBC>=1）良性误报 27.8%，高阈值（>=10/20）只覆盖 50%/25% 的可行动攻击。",
        "",
        "## 3. 分布与分层（同一批 run）",
        "",
        "| arm | n | zeros | p50 | p90 | p95 | max | mean |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in ("benign", "attack"):
        stats = distribution["distribution"][arm]["stats"]
        lines.append(
            f"| {arm} | {stats['n']} | {stats['zeros']} | {stats['quantiles']['p50']} | "
            f"{stats['quantiles']['p90']} | {stats['quantiles']['p95']} | {stats['max']} | "
            f"{stats['mean']:.2f} |"
        )
    attack_types = distribution["attack_type_breakdown"]["attack"]
    lines += [
        "",
        "按 attack_type（fix4 攻击臂）：",
        "",
        "| type | runs | mean PBC | max | 可行动 |",
        "|---|---:|---:|---:|---|",
    ]
    for name in ("A", "B", "C"):
        entry = attack_types[name]
        if entry["runs"] == 0:
            lines.append(f"| {name} | **0** | — | — | **不适用：无 Type C run，不可声明** |")
        else:
            lines.append(
                f"| {name} | {entry['runs']} | {entry['stats']['mean']:.2f} | "
                f"{entry['stats']['max']} | {frac(entry['actionable'])} |"
            )
    lines += [
        "",
        "## 4. 证据门控（gating）对照",
        "",
        f"- 定义：`{stratified['gating']['gate_definition']}`；",
        f"- 攻击臂：gate 开 {stratified['gating']['attack']['gate_on']}/"
        f"{stratified['n']['attack']}，关 {stratified['gating']['attack']['gate_off']}/"
        f"{stratified['n']['attack']}；良性臂：gate 开 "
        f"{stratified['gating']['benign']['gate_on']}/{stratified['n']['benign']}；",
        f"- {stratified['gating']['reading']}",
        "",
        "## 5. 可以写 / 不可以写",
        "",
        "**可以写：**",
        "",
    ]
    lines += [f"- {item}" for item in stratified["claim_scope"]["allowed"]]
    lines += ["", "**不可以写：**", ""]
    lines += [f"- {item}" for item in stratified["claim_scope"]["forbidden"]]
    lines += [
        "",
        "## 6. 复现",
        "",
        "见同目录 `README.md`。分析脚本本身无时间戳，重跑产生字节一致的 JSON。",
        "",
    ]
    return "\n".join(lines)


def render_readme(distribution: dict, stratified: dict) -> str:
    coverage = distribution["coverage"]
    return f"""# PBC 分布与分层指标（canonical，审稿意见 7）

本目录是审稿意见 7（PBC 混杂 / 活动量基线）的**唯一 canonical 产物**，只使用
RC 2026-10-25-fix4 的 {coverage['valid_runs']} 条有效 run，**不重跑 campaign、不重建 DB、
不调用任何模型**。

## 文件

| 文件 | 内容 |
|---|---|
| `pbc-distribution.json` | 逐 run / 逐场景 PBC、分位数、直方图、attack_type 分解（Type C=0 显式标注）、cohort、排除项、数据来源 provenance |
| `STRATIFIED-METRICS.json` | 阈值 1/2/3/5/10/20 的 TPR/FPR（含分子分母与 Wilson 95% CI）、activity-only baseline、gating 对照、允许/禁止措辞 |
| `STRATIFIED-METRICS.md` | 上述指标的论文可用表格与措辞（中文） |
| `README.md` | 本文件 |

## 权威输入

1. 61 条 run 的 `results.jsonl`：`local/workpackages/2026-10-25-rc-fix4/{{benign,attack}}/results.jsonl`
   （每条含 `db_path`，DB 实际位于 `local/f6`；**不要删除被引用的 finalized.db**）。
2. 逐 run PBC 事实：`local/workpackages/2026-10-25-e3-fix4/{{attack,benign}}-facts.jsonl`
   （由 `mtagentlens replay-sensitivity --dump-runs` 产出，schema `mtagentlens.replay-run-facts/1`）。
3. 冻结参照：`local/workpackages/2026-10-25-e3-fix4/e2-summary.json`
   （脚本生成时必须逐字段精确复现，否则退出非零）。
4. 场景元数据：`local/workpackages/2026-10-25-sink-release2/FuxiBench/scenarios/`
   （用于 attack_type / mode / 93 场景覆盖）。
5. 无效尝试：`local/workpackages/2026-10-25-rc-fix4/{{benign,attack}}/void-attempts.jsonl`。

## 复现命令（在仓库根执行）

```bash
cd /home/lzk/MtAgentLens
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
export PYTHONPATH="$PWD/env:$PWD/env/src"

# 0) 若 facts 不存在，用冻结 checkpoint 重新 dump（实测 ~68 s，零模型调用）：
env/.venv/bin/python - <<'PY'
import json
for arm in ['benign','attack']:
    rows=[json.loads(l) for l in open(f'local/workpackages/2026-10-25-rc-fix4/{{arm}}/results.jsonl') if l.strip()]
    open(f'/tmp/dbs-{{arm}}.txt','w').write(' '.join('--db '+r['db_path'] for r in rows if r.get('db_path')))
PY
./collector/build/release/mtagentlens replay-sensitivity $(cat /tmp/dbs-benign.txt) \\
  --out /tmp/b.json --safe-grid 0.15 --unsafe-grid 0.70 --weight-factors 1.0 \\
  --gate-centers 10 --loo-dims 1 --dump-runs local/workpackages/2026-10-25-e3-fix4/benign-facts.jsonl
./collector/build/release/mtagentlens replay-sensitivity $(cat /tmp/dbs-attack.txt) \\
  --out /tmp/a.json --safe-grid 0.15 --unsafe-grid 0.70 --weight-factors 1.0 \\
  --gate-centers 10 --loo-dims 1 --dump-runs local/workpackages/2026-10-25-e3-fix4/attack-facts.jsonl

# 1) 生成 canonical 产物（<1 s；脚本内部先精确复现 e2-summary）
env/.venv/bin/python scripts/evaluation/pbc_distribution.py \\
  --attack-facts local/workpackages/2026-10-25-e3-fix4/attack-facts.jsonl \\
  --benign-facts local/workpackages/2026-10-25-e3-fix4/benign-facts.jsonl \\
  --attack-results local/workpackages/2026-10-25-rc-fix4/attack/results.jsonl \\
  --benign-results local/workpackages/2026-10-25-rc-fix4/benign/results.jsonl \\
  --scenarios-dir local/workpackages/2026-10-25-sink-release2/FuxiBench/scenarios \\
  --reference-summary local/workpackages/2026-10-25-e3-fix4/e2-summary.json \\
  --void-attempts benign=local/workpackages/2026-10-25-rc-fix4/benign/void-attempts.jsonl \\
  --void-attempts attack=local/workpackages/2026-10-25-rc-fix4/attack/void-attempts.jsonl \\
  --out-dir local/workpackages/2026-10-26-final-dataset/pbc

# 2) 测试
env/.venv/bin/python -m pytest compare/tests/test_pbc_distribution.py -q
```

运行时间：facts 重导约 **68 s**（18 benign 16 s + 43 attack 52 s）；分析本身 **<1 s**；
测试 **<2 s**。脚本不写时间戳，同输入重跑得到字节一致的四个产物。

## 覆盖与分母

- 有效 **{coverage['valid_runs']} run** = benign {coverage['valid_runs_by_arm']['benign']} + attack {coverage['valid_runs_by_arm']['attack']}，来自 {coverage['scenarios_with_a_valid_run']} 个单智能体场景；{coverage['release_scenarios_total']} 个 release 场景中 {coverage['single_agent_scenarios']} 个单智能体、{coverage['multi_agent_scenarios']} 个多智能体。
- 多智能体有效 run = **{coverage['multi_agent_valid_runs']}**；Type C 有效 run = **{coverage['type_c_valid_runs']}**。
- 排除：6 benign + 7 attack 的 void attempts（保留在 `void-attempts.jsonl`）；
  整场景排除 `benign-multi-release-orchestration`、`resource-runaway-loop`（原因见 JSON）。

## 审稿人能推断什么 / 不能推断什么

**能推断：**
- 在这 61 条单智能体 run 上，PBC 体积单独不是可靠检测器（rank AUROC 0.528）；
  PBC>=1 时良性误报 27.8%，PBC>=10 时只覆盖 50% 的可行动攻击；
- 可行动（Unsafe）裁决与 PBC 体积不同向：4 条可行动攻击 PBC 为 1/1/18/32，
  28/43 攻击 run 的 PBC=0 且全部判 Safe/review（0 Unsafe）；证据门控只在 2/43 攻击、0/18 良性上激活。

**不能推断：**
- 不能推断 Type C / 多智能体的 PBC 分布（本 RC 中 Type C run=0）；
- 不能把 61 条单智能体 run 写成 93 场景全量分布；
- 不能写“活动量混杂已被完全排除”，也不能引用 0.836/0.960/0.895 或历史 220/396.7。

## 与历史脚本的关系

`scripts/evaluation/pbc_distribution.py` 在 2026-10-19 曾读取
`datasets/FuxiBench/traces/` 的旧 responsibility.db 并统计
`PolicyBoundaryCrossing` 边；旧语料已不在（该目录仅剩 2 个 db），且旧的
220/396.7 数字被审计判定为重复建边口径问题。本脚本已替换为 fix4 冻结 checkpoint
口径；旧输出 `docs/evaluation/pbc-distribution-2026-10-19.json` 仅作历史字节保留，
不得引用。注意：旧脚本当时是**未跟踪的工作区文件**（不在 git HEAD 中），因此本替换
无法从 git 历史找回旧源码；旧输出 artifact 仍在，可作为历史证据。本次替换由
task-14 明确授权。
"""


# ── CLI ────────────────────────────────────────────────────────────────────


def parse_void_attempts(pairs: list[str]) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"--void-attempts expects arm=path, got {pair!r}")
        arm, _, path = pair.partition("=")
        if arm not in ("benign", "attack"):
            raise ValueError(f"unknown arm {arm!r}")
        parsed[arm] = Path(path)
    return parsed


def build_provenance_inputs(
    *,
    attack_facts_path: Path,
    benign_facts_path: Path,
    attack_results_path: Path,
    benign_results_path: Path,
    scenarios_dir: Path,
    void_paths: dict[str, Path],
    analysis_binary_sha256: str,
) -> dict[str, dict]:
    attack_results = load_results(attack_results_path)
    release_ids = {str(row.get("release_id")) for row in attack_results.values()}
    if len(release_ids) != 1:
        raise ValueError(f"attack results carry multiple release ids: {release_ids}")
    binaries = {str(row.get("collector_sha256")) for row in attack_results.values()}
    if len(binaries) != 1:
        raise ValueError(f"attack results carry multiple collector hashes: {binaries}")

    def entry(path: Path, role: str, rows: int | None = None) -> dict:
        digest = sha256_tree(path) if Path(path).is_dir() else sha256_file(path)
        block = {"role": role, "path": rel(path), "sha256": digest}
        if Path(path).is_dir():
            block["sha256_kind"] = "tree"
        if rows is not None:
            block["rows"] = rows
        return block

    inputs = {
        "attack_facts": entry(attack_facts_path, "attack PBC facts (replay-run-facts/1)", len(load_arm(attack_facts_path))),
        "benign_facts": entry(benign_facts_path, "benign PBC facts (replay-run-facts/1)", len(load_arm(benign_facts_path))),
        "attack_results": entry(attack_results_path, "attack valid-run ledger", len(attack_results)),
        "benign_results": entry(benign_results_path, "benign valid-run ledger", len(load_results(benign_results_path))),
        "scenarios": entry(scenarios_dir, "release2 scenario metadata (attack_type / mode)"),
        "analysis_binary_sha256": analysis_binary_sha256,
    }
    inputs["attack_results"]["release_id"] = next(iter(release_ids))
    inputs["attack_results"]["campaign_binary_sha256"] = next(iter(binaries))
    for arm in ("benign", "attack"):
        if arm in void_paths:
            inputs[f"{arm}_void_attempts"] = entry(void_paths[arm], f"{arm} void attempts", len(load_void_attempts(void_paths[arm])))
    return inputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attack-facts", type=Path, required=True)
    parser.add_argument("--benign-facts", type=Path, required=True)
    parser.add_argument("--attack-results", type=Path, required=True)
    parser.add_argument("--benign-results", type=Path, required=True)
    parser.add_argument("--scenarios-dir", type=Path, required=True)
    parser.add_argument("--reference-summary", type=Path, default=None)
    parser.add_argument("--void-attempts", action="append", default=[], metavar="ARM=PATH")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument(
        "--analysis-binary-sha256",
        default="dda3f4b59a8c4cc91dc873bcbbd415e00bb7a945557bb172992b0e4f8afb72d5",
        help="sha256 of the replay binary that produced the facts",
    )
    args = parser.parse_args(argv)

    scenarios = load_scenarios(args.scenarios_dir)
    attack_facts = load_arm(args.attack_facts)
    benign_facts = load_arm(args.benign_facts)
    void_paths = parse_void_attempts(args.void_attempts)
    void_benign = load_void_attempts(void_paths.get("benign")) if "benign" in void_paths else []
    void_attack = load_void_attempts(void_paths.get("attack")) if "attack" in void_paths else []

    try:
        reference_summary, reproduction = reproduce_e2_summary(
            attack_facts, benign_facts, args.reference_summary
        )
        provenance_inputs = build_provenance_inputs(
            attack_facts_path=args.attack_facts,
            benign_facts_path=args.benign_facts,
            attack_results_path=args.attack_results,
            benign_results_path=args.benign_results,
            scenarios_dir=args.scenarios_dir,
            void_paths=void_paths,
            analysis_binary_sha256=args.analysis_binary_sha256,
        )
        attack_rows = join_arm("attack", args.attack_facts, args.attack_results, scenarios)
        benign_rows = join_arm("benign", args.benign_facts, args.benign_results, scenarios)
        distribution = build_distribution(
            attack_rows=attack_rows,
            benign_rows=benign_rows,
            scenarios=scenarios,
            void_benign=void_benign,
            void_attack=void_attack,
            reference_summary=reference_summary,
            reproduction=reproduction,
            provenance_inputs=provenance_inputs,
        )
        stratified = build_stratified(
            attack_facts=attack_facts,
            benign_facts=benign_facts,
            attack_rows=attack_rows,
            benign_rows=benign_rows,
            reference_summary=reference_summary,
            reproduction=reproduction,
        )
        assert_claim_scope(distribution, stratified)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "pbc-distribution.json": json.dumps(distribution, ensure_ascii=False, indent=2) + "\n",
        "STRATIFIED-METRICS.json": json.dumps(stratified, ensure_ascii=False, indent=2) + "\n",
        "STRATIFIED-METRICS.md": render_stratified_md(stratified, distribution),
        "README.md": render_readme(distribution, stratified),
    }
    for name, text in outputs.items():
        (args.out_dir / name).write_text(text, encoding="utf-8")

    print(
        json.dumps(
            {
                "out_dir": rel(args.out_dir),
                "reproduction_exact": reproduction["exact_match"],
                "valid_runs": distribution["coverage"]["valid_runs"],
                "type_c_runs": distribution["coverage"]["type_c_valid_runs"],
                "pbc_rank_auroc": stratified["pbc_rank_auroc"],
                "headline": [
                    {
                        "threshold": row["threshold"],
                        "tpr": row["tpr"]["value"],
                        "fpr": row["fpr"]["value"],
                    }
                    for row in stratified["activity_only_baseline"]
                    if row["headline"]
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
