#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Evaluate MtAgentLens against the frozen FuxiBench dataset.

This is the bridge between the artifact databases and `compare.tools.evaluator`.
It reads the *actionable* alert contract (ADR-0015: `decision == "Unsafe"`) so
the paper's main table cannot silently count review-only signals as detections.

The previous module was lost during the repository cleanup. This version is
rebuilt against the current `compare.tools` interfaces and keeps no machine
absolute paths.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from compare.tools.base import (  # noqa: E402
    BoundaryType,
    EvaluationCase,
    FindingRecord,
    ToolMetadata,
)
from compare.tools.datasets import FrozenDatasetAdapter  # noqa: E402
from compare.tools.evaluator import evaluate_tool_results  # noqa: E402

MTAGENTLENS_TOOL = ToolMetadata(
    id="mtagentlens",
    name="MtAgentLens",
    tool_class="runtime_ebpf",
    source_url="builtin://collector",
    version="working-tree",
    license="Apache-2.0 OR MIT",
    install=(),
    adapter="mtagentlens",
    input_modes=("os_events",),
    score_scope="main_offline",
    inclusion_rationale=(
        "eBPF runtime observation over the frozen trace corpus; actionable "
        "scope is decision=Unsafe (review signals excluded)."
    ),
)

# Alert severity is stored as DisplayProfile/severity enum names.
_SEVERITY = {"Critical": "Critical", "Warning": "Warning", "Info": "Info"}

_CATEGORY_BY_CLAIM = {
    "PolicyViolation": "policy_violation",
    "TaintedContextUse": "tainted_context_use",
    "SuspiciousDelegation": "suspicious_delegation",
    "UnauthorizedCapabilityUse": "unauthorized_capability_use",
    "BlockedAttempt": "blocked_attempt",
    "ResponsibilityAnomaly": "responsibility_anomaly",
}

_BOUNDARY_BY_CLAIM = {
    "PolicyViolation": BoundaryType.RESOURCE,
    "UnauthorizedCapabilityUse": BoundaryType.RESOURCE,
    "TaintedContextUse": BoundaryType.CONTENT,
    "SuspiciousDelegation": BoundaryType.DELEGATION,
}


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _alert_rows(db_path: Path, actionable_only: bool) -> list[dict]:
    """Read the canonical binary verdict and its exact linked final alert."""
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        if "final_decision" not in columns:
            raise ValueError(f"{db_path}: no canonical final_decision; recollect this historical trace")
        runs = [dict(row) for row in conn.execute("SELECT * FROM runs")]
        if len(runs) != 1:
            raise ValueError(f"{db_path}: expected exactly one run, found {len(runs)}")
        run = runs[0]
        decision = run.get("final_decision")
        if decision not in ("Safe", "Unsafe", "Uncertain"):
            raise ValueError(f"{db_path}: run has no finalized canonical verdict")
        if actionable_only and decision != "Unsafe":
            return []
        # A verdict without linked attribution remains a binary observation.
        row = {"alert_id": "run:" + str(run["run_id"]), "run_id": run["run_id"],
            "timestamp_ns": run.get("ended_at_ns") or run.get("started_at_ns") or 0,
            "decision": decision, "severity": run.get("final_severity") or "Info",
            "confidence": run.get("final_confidence"), "summary": "Canonical run verdict",
            "primary_agent_id": None, "engine_section_json": None}
        if run.get("final_alert_id"):
            alert_columns = {r[1] for r in conn.execute("PRAGMA table_info(responsibility_alerts)")}
            if {"alert_id", "run_id"} <= alert_columns:
                linked = conn.execute("SELECT * FROM responsibility_alerts WHERE run_id=? AND alert_id=?",
                    (run["run_id"], run["final_alert_id"])).fetchall()
                if len(linked) == 1:
                    final = dict(linked[0])
                    if final.get("decision") != decision:
                        raise ValueError("Canonical verdict disagrees with its linked final alert")
                    row.update(final)
        return [row]
    finally:
        conn.close()


def _claim_context(db_path: Path, alert: dict) -> tuple[list[dict], bool]:
    """Claim IDs carried by the final alert are the attribution boundary."""
    try:
        engine = json.loads(alert.get("engine_section_json") or "{}")
    except (TypeError, ValueError):
        return [], False
    if not isinstance(engine, dict):
        return [], False
    ids = engine.get("claim_ids")
    if not isinstance(ids, list) or any(not isinstance(value, str) for value in ids) or len(ids) != len(set(ids)):
        return [], False
    if not ids:
        return [], False
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(responsibility_claims)")}
        if not {"claim_id", "run_id", "subject_agent_id", "claim_type", "boundary_id", "severity", "confidence"} <= columns:
            return [], False
        placeholders = ",".join("?" for _ in ids)
        rows = [dict(row) for row in conn.execute(
            f"SELECT * FROM responsibility_claims WHERE run_id=? AND claim_id IN ({placeholders})",
            [alert["run_id"], *ids])]
        if len(rows) != len(ids):
            return [], False
        return rows, True
    finally:
        conn.close()


def _flatten(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _flatten(child)]
    if isinstance(value, list):
        return [text for child in value for text in _flatten(child)]
    return []


def _claim_arguments(db_path: Path, run_id: str, claim: dict) -> str:
    """Resolve exact persisted evidence IDs to their original action/intent args."""
    ids = json.loads(claim.get("evidence_refs_json") or "[]")
    if not isinstance(ids, list) or not ids or any(not isinstance(value, str) for value in ids):
        return ""
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"correlation_evidence", "actions", "intents"} <= tables:
            return ""
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(f"SELECT a.normalized_args_json, i.tool_args_json FROM correlation_evidence e "
            "JOIN actions a ON a.run_id=e.run_id AND a.action_id=e.action_id "
            "JOIN intents i ON i.run_id=e.run_id AND i.intent_id=e.intent_id "
            f"WHERE e.run_id=? AND e.evidence_id IN ({placeholders})", [run_id, *ids])
        return " ".join(text for row in rows for raw in row for text in _flatten(json.loads(raw)))
    finally:
        conn.close()


def _boundary_type(claim: dict) -> BoundaryType | None:
    kind = claim.get("claim_type")
    if kind in ("TaintedContextUse", "SuspiciousDelegation"):
        return _BOUNDARY_BY_CLAIM[kind]
    boundary = str(claim.get("boundary_id") or "")
    for prefix, value in (("network", BoundaryType.NETWORK), ("process", BoundaryType.EXEC_ARG),
            ("tool", BoundaryType.EXEC_ARG), ("content", BoundaryType.CONTENT),
            ("context", BoundaryType.CONTENT), ("delegation", BoundaryType.DELEGATION),
            ("file", BoundaryType.RESOURCE), ("credential", BoundaryType.RESOURCE)):
        if boundary.startswith("boundary." + prefix + "."):
            return value
    return None


def load_mtagentlens_findings(
    cases: Iterable[EvaluationCase], *, actionable_only: bool = True,
) -> Iterable[FindingRecord]:
    """Binary verdict findings and precisely linked claim findings are distinct."""
    for case in cases:
        db_path = case.mtagentlens_baseline.get("responsibility_db")
        if db_path is None or not Path(db_path).exists():
            raise ValueError(f"{case.adapter_case.scenario_id}: no observed responsibility database")
        db_path = Path(db_path)
        for row in _alert_rows(db_path, actionable_only):
            claims, attribution_available = _claim_context(db_path, row)
            yield FindingRecord(MTAGENTLENS_TOOL.id, case.adapter_case.dataset_name, case.adapter_case.scenario_id,
                "run:" + str(row["run_id"]), _SEVERITY.get(str(row["severity"]), "Info"), "run_verdict", None,
                None, None, None, json.dumps({"decision": row["decision"], "attribution_available": attribution_available}),
                float(row.get("confidence") or 0.0), db_path, None)
            for claim in claims:
                args = _claim_arguments(db_path, row["run_id"], claim)
                kind = str(claim["claim_type"])
                yield FindingRecord(MTAGENTLENS_TOOL.id, case.adapter_case.dataset_name, case.adapter_case.scenario_id,
                    str(claim["claim_id"]), _SEVERITY.get(str(claim["severity"]), "Info"),
                    _CATEGORY_BY_CLAIM.get(kind, kind), _boundary_type(claim),
                    str(claim["boundary_id"]), args or None, args or None,
                    json.dumps({"claim": claim, "final_alert_id": row["alert_id"]}, ensure_ascii=False),
                    float(claim["confidence"]), db_path, str(claim["subject_agent_id"]))


def evaluate_dataset(
    dataset_dir: Path,
    *,
    actionable_only: bool = True,
) -> dict[str, object]:
    """Load the frozen dataset, read its DBs, and score MtAgentLens."""
    cases = list(FrozenDatasetAdapter(Path(dataset_dir)).load_cases())
    from scripts.evaluation.run_artifacts import run_observation_state
    findings, observed_cases, unknown, uncertain, quality_limited, attribution_unavailable = [], [], [], [], [], []
    for case in cases:
        try:
            db = case.mtagentlens_baseline.get("responsibility_db")
            if db is None:
                raise ValueError("No observed responsibility database")
            state = {"scenario_id": case.adapter_case.scenario_id, **run_observation_state(Path(db))}
            if state["evaluation_status"] == "uncertain":
                uncertain.append(state)
                continue
            if state["evaluation_status"] == "quality_limited":
                quality_limited.append(state)
                continue
            if state["evaluation_status"] != "eligible":
                unknown.append(state)
                continue
            case_findings = list(load_mtagentlens_findings([case], actionable_only=actionable_only))
        except (ValueError, sqlite3.DatabaseError) as exc:
            unknown.append({"scenario_id": case.adapter_case.scenario_id, "reason": str(exc)})
            continue
        if any(f.category == "run_verdict" and json.loads(f.evidence).get("attribution_available") is False for f in case_findings):
            attribution_unavailable.append(case.adapter_case.scenario_id)
        findings.extend(case_findings)
        observed_cases.append(case)
    metrics = evaluate_tool_results(MTAGENTLENS_TOOL, observed_cases, findings)
    missing = any(case.is_attack and not case.expected_claims for case in cases)
    release_path = Path(dataset_dir) / "release.json"
    release = json.loads(release_path.read_text()) if release_path.exists() else {}
    ready = not missing and not unknown and not uncertain and not quality_limited and not attribution_unavailable and release.get("paper_metrics_available") is True
    reason = ("ground truth has no expected_claims; recall/coverage would be meaningless" if missing
              else "uncertain, unknown, or quality-limited runs are excluded from effect denominators" if (unknown or uncertain or quality_limited)
              else "canonical binary verdicts lack exact linked claim context for attribution" if attribution_unavailable
              else "versioned release and resolved annotation review are required" if not ready else None)
    return {"total_cases": len(cases), "attack_cases": sum(c.is_attack for c in cases), "benign_cases": sum(not c.is_attack for c in cases),
        "unknown_runs": unknown, "uncertain_runs": uncertain, "quality_limited_runs": quality_limited,
        "observed_cases": len(observed_cases), "attribution_unavailable_scenarios": attribution_unavailable,
        "quality_gate": {"paper_metrics_available": ready, "reason": reason},
        "metrics": metrics, "findings": {"total": len(findings)}, "release_id": release.get("release_id")}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=PROJECT_ROOT / "compare" / "Dataset" / "FuxiBench",
    )
    parser.add_argument(
        "--include-review",
        action="store_true",
        help="count all alerts instead of only decision=Unsafe",
    )
    args = parser.parse_args()
    metrics = evaluate_dataset(args.dataset, actionable_only=not args.include_review)
    print(json.dumps(metrics, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
