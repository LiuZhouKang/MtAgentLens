#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""R-Judge application-trace baseline (A2, full trace) over a campaign ledger.

Reuses the pinned protocol/judge/cache in compare/rjudge. The full application
trace is the deployment-realistic R-Judge input; task-only and no-tool-result
inputs are not part of this comparison. `--limit` exists only for a paid smoke
test before the frozen batch.
"""
from __future__ import annotations

import argparse, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compare.rjudge import (  # noqa: E402
    AnthropicMessagesClient, ExecutionMode, JudgeConfig, JudgeRunner,
    RJudgeJudge, ResponseCache,
)
from compare.scripts.run.app_baseline import (  # noqa: E402
    ARM_FULL_TRACE, build_records, load_rows, select_rows,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--release", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--model", default="deepseek-flash")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    if os.environ.get("MTL_ALLOW_PAID") != "1":
        ap.error("paid execution requires MTL_ALLOW_PAID=1")
    rows, skipped = select_rows(load_rows(args.results))
    if args.limit is not None:
        if args.limit < 1:
            ap.error("--limit must be positive")
        rows = rows[: args.limit]
    if skipped:
        print(f"skipped rows without run_dir: {skipped}", file=sys.stderr)
    if not rows:
        ap.error("no available runs")
    arm_dir = args.out / "A2"
    records, failures, _ = build_records(rows, arm_dir, arm=ARM_FULL_TRACE)
    if not records:
        ap.error(f"no records built; failures={failures}")
    judge = RJudgeJudge(
        config=JudgeConfig(model=args.model),
        client=AnthropicMessagesClient(model=args.model),
        cache=ResponseCache(arm_dir / ".cache"),
        execution_mode=ExecutionMode.REAL,
    )
    runner = JudgeRunner(
        judge=judge,
        out_jsonl=arm_dir / "judge-results.jsonl",
        summary_path=arm_dir / "judge-summary.json",
        resume=True,
    )
    result = runner.run(records)
    summary = {"schema": "mtagentlens.app-judge-batch/2", "model": args.model,
               "arm": "A2_full_trace", "records": len(records),
               "failures": failures, "runner": result}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"arm": "A2", **result}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
