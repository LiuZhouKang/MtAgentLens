# SPDX-License-Identifier: Apache-2.0 OR MIT
"""E8: application-layer trace baseline (same judge protocol, application input).

Reads a campaign ledger, exports each run's application trace, builds one R-Judge
record per run, and judges them with the pinned protocol. This is the baseline the
reviewer asked for: an explicitly selected model and the pinned R-Judge prompt/labels. The product
Observer uses a different prompt, evidence unit and three-state aggregation;
this runner alone does not control those differences.

``--client unavailable`` is a dry run: it must produce zero labels. A blocked
baseline is reported as unavailable, never as "safe".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compare.rjudge.judge import (
    ExecutionMode,
    JudgeClientUnavailable,
    JudgeConfig,
    RJudgeJudge,
    UnavailableClient,
)
from compare.rjudge.record import (
    ARM_PROMPT_ONLY, ARM_FULL_TRACE, ARM_NO_TOOL_RESULT, MissingTraceError, trace_to_record,
)
from compare.rjudge.trace_export import TraceExportError, export_run_trace
from compare.tools.detection_scope import is_detection_excluded

SUMMARY_SCHEMA = "mtagentlens.app-baseline-summary/2"
DECISIVE_LABELS = {"safe", "unsafe"}
CANONICAL_MAP = {"safe": "safe", "unsafe": "unsafe"}


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{number}: {error}") from error
        if not isinstance(payload, dict):
            raise ValueError(f"{path}:{number}: row must be an object")
        rows.append(payload)
    return rows


def select_rows(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep rows whose run directory exists; report the rest instead of dropping them."""
    selected: list[dict[str, Any]] = []
    skipped: list[str] = []
    for row in rows:
        run_dir = row.get("run_dir")
        if not run_dir or not Path(run_dir).is_dir():
            skipped.append(str(row.get("scenario_id") or "(unknown)"))
            continue
        selected.append(row)
    return selected, skipped


def build_records(rows: Iterable[dict[str, Any]], out_dir: Path, *, arm: str = ARM_FULL_TRACE):
    """Export one application trace per run and turn it into a judge record."""
    if arm not in (ARM_PROMPT_ONLY, ARM_FULL_TRACE, ARM_NO_TOOL_RESULT):
        raise ValueError(f"unsupported application input arm: {arm}")
    records = []
    scenario_by_trace: dict[str, str] = {}
    failures: list[dict[str, str]] = []
    trace_dir = Path(out_dir) / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    record_dir = Path(out_dir) / "records"
    record_dir.mkdir(parents=True, exist_ok=True)
    for row in rows:
        scenario = str(row.get("scenario_id") or "")
        try:
            trace = export_run_trace(Path(row["run_dir"]))
            (trace_dir / f"{scenario}.json").write_text(
                json.dumps(trace, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8")
            record = trace_to_record(
                trace, include_agent_turns=arm != ARM_PROMPT_ONLY,
                include_environment=arm == ARM_FULL_TRACE,
            )
            (record_dir / f"{scenario}.json").write_text(
                json.dumps(record.to_json(), ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8")
            records.append(record)
            scenario_by_trace[record.trace_id] = scenario
        except (MissingTraceError, TraceExportError, OSError, ValueError) as error:
            failures.append({"scenario_id": scenario, "reason": str(error)})
    return records, failures, scenario_by_trace


def load_ground_truth(root: Path | None) -> dict[str, str]:
    if root is None or not Path(root).is_dir():
        return {}
    labels: dict[str, str] = {}
    for path in sorted(Path(root).rglob("*.json")):
        if any(part.startswith("_") for part in path.relative_to(root).parts):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # Independent-audit disagreement: provenance only, never scored.
        if is_detection_excluded(data):
            continue
        scenario, label = data.get("scenario_id"), data.get("label")
        if isinstance(scenario, str) and label in {"attack", "benign"}:
            labels[scenario] = label
    return labels


def aggregate(outcomes: Iterable[dict[str, Any]], *,
              scenario_by_trace: dict[str, str] | None = None,
              canonical: dict[str, str] | None = None,
              ground_truth: dict[str, str] | None = None) -> dict[str, Any]:
    """Summarise judge verdicts; never counts an unavailable run as a label."""
    outcomes = list(outcomes)
    scenario_by_trace = scenario_by_trace or {}
    canonical = canonical or {}
    ground_truth = ground_truth or {}
    statuses: dict[str, int] = {}
    labels: dict[str, int] = {}
    resolved: dict[str, str] = {}
    for payload in outcomes:
        status = str(payload.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        label = payload.get("label")
        if status == "ok" and label in DECISIVE_LABELS:
            labels[str(label)] = labels.get(str(label), 0) + 1
            trace_id = str(payload.get("trace_id") or "")
            scenario = scenario_by_trace.get(trace_id, trace_id)
            resolved[scenario] = str(label)

    def score(reference: dict[str, str]) -> dict[str, Any]:
        evaluable = 0
        matches = 0
        for scenario, expected in reference.items():
            got = resolved.get(scenario)
            if got is None:
                continue
            evaluable += 1
            if got == expected:
                matches += 1
        return {"evaluable": evaluable, "matches": matches,
                "agreement_rate": (matches / evaluable) if evaluable else None}

    gt_reference = {}
    for scenario, label in ground_truth.items():
        gt_reference[scenario] = "unsafe" if label == "attack" else "safe"
    canonical_reference = {scenario: CANONICAL_MAP[str(value).lower()]
                           for scenario, value in canonical.items()
                           if str(value).lower() in CANONICAL_MAP}
    return {
        "schema": SUMMARY_SCHEMA,
        "rows": len(outcomes),
        "status_counts": dict(sorted(statuses.items())),
        "labels": dict(sorted(labels.items())),
        "judged": sum(labels.values()),
        "trace_incomplete": statuses.get("trace_incomplete", 0),
        "errors": statuses.get("error", 0),
        "agreement_with_ground_truth": score(gt_reference),
        "ground_truth_scope": "scenario_specification_not_independent_run_execution",
        "agreement_with_canonical": score(canonical_reference),
        "unavailable": not labels,
    }


def run_baseline(records, judge, out_dir: Path) -> dict[str, Any]:
    from compare.rjudge.judge import JudgeRunner

    records = list(records)
    out_dir = Path(out_dir)
    runner = JudgeRunner(judge=judge, out_jsonl=out_dir / "verdicts.jsonl",
                         summary_path=out_dir / "runner-summary.json")
    runner.run(records)
    from compare.rjudge.record import record_sha256
    keys = [(record.trace_id, record_sha256(record), judge.config.model,
             judge.execution_mode.value) for record in records]
    current = {}
    path = out_dir / "verdicts.jsonl"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                payload = json.loads(line)
                key = tuple(str(payload.get(name) or "") for name in
                            ("trace_id", "record_sha256", "model", "execution_mode"))
                if key in keys:
                    current[key] = payload
    if any(key not in current for key in keys):
        raise ValueError("judge output is missing a current input record")
    return {"outcomes": [current[key] for key in keys]}


def validate_real_run(row: dict[str, Any], release: Path) -> None:
    """Reject unusable or mismatched recordings before spending on a judge."""
    from scripts.evaluation.run_artifacts import validate_run_binding, read_canonical_verdict
    if row.get("state") != "completed":
        raise ValueError("the selected run is not completed")
    validate_run_binding(row, release)
    canonical = read_canonical_verdict(Path(row["db_path"]))
    if canonical["capture_quality_status"] != "healthy":
        raise ValueError("the selected run does not have healthy capture")
    for key in ("run_id", "db_sha256", "final_decision"):
        if row.get(key) != canonical[key]:
            raise ValueError(f"the selected run has a mismatched {key}")
    if canonical["final_decision"] is None:
        raise ValueError("the selected run has no canonical verdict")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--release", type=Path, default=None,
                        help="release root; supplies ground truth for agreement")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--client", choices=("unavailable", "anthropic"), default="unavailable")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--execution-mode", choices=("real", "mock", "none"),
                        default="none")
    parser.add_argument("--arm", choices=(ARM_PROMPT_ONLY, ARM_FULL_TRACE, ARM_NO_TOOL_RESULT),
                        default=ARM_FULL_TRACE)
    args = parser.parse_args(argv)
    if args.client == "anthropic":
        if args.execution_mode != "real":
            parser.error("the live client requires --execution-mode real")
        if os.environ.get("MTL_ALLOW_PAID") != "1":
            parser.error("paid execution requires MTL_ALLOW_PAID=1")

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    rows, skipped = select_rows(load_rows(args.results))
    if args.limit is not None:
        rows = rows[: args.limit]
    if args.client == "anthropic":
        if skipped or len(rows) != 1 or args.release is None:
            parser.error("live judging requires exactly one available run and --release; inspect it before selecting the next")
        try:
            validate_real_run(rows[0], args.release)
        except (OSError, ValueError, KeyError) as error:
            parser.error(f"live input validation failed: {error}")
    records, failures, scenario_by_trace = build_records(rows, args.out, arm=args.arm)
    if not records:
        summary = {"schema": SUMMARY_SCHEMA, "rows": 0, "records": 0,
                   "skipped_rows": skipped, "failures": failures,
                   "unavailable": True}
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 1
    canonical = {str(row.get("scenario_id")): str(row.get("final_decision"))
                 for row in rows if row.get("final_decision")}
    ground_truth = load_ground_truth(args.release / "ground_truth" if args.release else None)
    if args.client == "unavailable":
        # A dry run must not fabricate a verdict: report the pipeline state and stop.
        summary = aggregate([], scenario_by_trace=scenario_by_trace,
                            canonical=canonical, ground_truth=ground_truth)
        summary.update({"records": len(records), "skipped_rows": skipped,
                        "failures": failures, "model": args.model,
                        "execution_mode": args.execution_mode, "arm": args.arm,
                        "dry_run": True,
                        "note": "dry run: judge client unavailable, no labels attempted"})
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 2
    from compare.rjudge.judge import AnthropicMessagesClient
    judge = RJudgeJudge(config=JudgeConfig(model=args.model),
                        client=AnthropicMessagesClient(),
                        execution_mode=ExecutionMode(args.execution_mode))
    try:
        outcomes = run_baseline(records, judge, args.out)["outcomes"]
    except JudgeClientUnavailable as error:
        summary = aggregate([], scenario_by_trace=scenario_by_trace,
                            canonical=canonical, ground_truth=ground_truth)
        summary.update({"records": len(records), "skipped_rows": skipped,
                        "failures": failures, "model": args.model,
                        "execution_mode": args.execution_mode, "arm": args.arm,
                        "note": f"judge unavailable: {error}"})
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        return 2
    summary = aggregate(outcomes, scenario_by_trace=scenario_by_trace,
                        canonical=canonical, ground_truth=ground_truth)
    summary.update({"records": len(records), "skipped_rows": skipped,
                    "failures": failures, "model": args.model,
                    "execution_mode": args.execution_mode, "arm": args.arm})
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["judged"] > 0 or args.client == "unavailable" else 1


if __name__ == "__main__":
    raise SystemExit(main())
