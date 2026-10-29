#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Static integrity gate for the P0.5 baselines.

`scripts/verify.sh` runs this. It answers one question: *are the committed
baselines still describing the repository in front of us?* It does not pretend
to re-measure live FPR or live overhead (both need sudo and an LLM endpoint);
it proves that the fixtures, hashes, scenario set, and recomputed statistics are
internally consistent, and it refuses to pass if anything is missing or stale.

Exit codes: 0 = all checks passed, 1 = at least one check failed.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WIRE_DIR = ROOT / "collector" / "tests" / "fixtures" / "wire"
BENIGN_DIR = ROOT / "env" / "scenarios" / "benign"
FPR_PATH = ROOT / "docs" / "evaluation" / "fpr-baseline.json"
OVERHEAD_PATH = ROOT / "docs" / "evaluation" / "overhead-baseline.json"

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"  ok   {message}")
    else:
        print(f"  FAIL {message}")
        failures.append(message)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denominator = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, center - half), min(1.0, center + half))


def check_wire_manifest() -> None:
    print("wire corpus manifest")
    manifest_path = WIRE_DIR / "MANIFEST.json"
    if not manifest_path.exists():
        check(False, f"missing {manifest_path.relative_to(ROOT)}")
        return
    manifest = json.loads(manifest_path.read_text())
    check(manifest.get("schema") == "mtagentlens.wire-fixture/1", "manifest schema")
    fixtures = manifest.get("fixtures") or {}
    check(len(fixtures) >= 3, f"manifest lists {len(fixtures)} fixture(s) (expected >= 3)")

    for name, fixture in fixtures.items():
        files = fixture.get("files") or {}
        check(bool(files), f"{name}: files is non-empty")
        for relative, expected in files.items():
            path = WIRE_DIR / name / relative
            if not path.exists():
                check(False, f"{name}/{relative}: missing")
                continue
            payload = path.read_bytes()
            check(
                len(payload) == expected.get("bytes"),
                f"{name}/{relative}: {len(payload)} bytes matches manifest",
            )
            digest = hashlib.sha256(payload).hexdigest()
            check(
                digest == expected.get("sha256"),
                f"{name}/{relative}: sha256 matches manifest",
            )
        golden = WIRE_DIR / name / "golden.sse"
        if golden.exists():
            check(golden.stat().st_size > 0, f"{name}: golden.sse is non-empty")

    # The fixtures must actually be consumed by a test, not just committed.
    tests = list((ROOT / "collector" / "tests").glob("*.rs"))
    referencing = [
        path.name
        for path in tests
        if "fixtures/wire" in path.read_text(errors="replace")
    ]
    check(
        len(referencing) >= 2,
        f"wire fixtures are referenced by {len(referencing)} test file(s): {referencing}",
    )


def check_transport_lines() -> None:
    """Both wire formats must decode to the same first-hand bytes."""
    print("transport round-trip")
    fixture_dir = WIRE_DIR / "transport"
    payload_path = fixture_dir / "payload.bin"
    if not payload_path.exists():
        check(False, f"missing {payload_path.relative_to(ROOT)}")
        return
    payload = payload_path.read_bytes()

    import sys as _sys

    _sys.path.insert(0, str(ROOT / "scripts" / "wire"))
    try:
        from extract_wire import ExtractionError, extract_payload, parse_meta
    except ImportError as exc:  # pragma: no cover - environment failure
        check(False, f"cannot import extract_wire: {exc}")
        return

    for name in ("capture-base64.jsonl", "capture-escaped.jsonl"):
        path = fixture_dir / name
        if not path.exists():
            check(False, f"missing {path.relative_to(ROOT)}")
            continue
        line = path.read_bytes().splitlines()[0]
        meta = parse_meta(line)
        try:
            decoded = extract_payload(line, meta["buf_size"])
        except ExtractionError as exc:
            check(False, f"{name}: {exc}")
            continue
        check(decoded == payload, f"{name}: decodes to payload.bin")
        check(
            len(decoded or b"") == meta["buf_size"],
            f"{name}: decoded length equals buf_size",
        )


def check_fpr_baseline() -> None:
    print("FPR baseline")
    if not FPR_PATH.exists():
        check(False, f"missing {FPR_PATH.relative_to(ROOT)}")
        return
    baseline = json.loads(FPR_PATH.read_text())
    check(baseline.get("schema") == "mtagentlens.fpr-baseline/1", "fpr schema")

    scenarios = sorted(path.stem for path in BENIGN_DIR.glob("*.json"))
    total = baseline.get("runs_completed")
    check(
        baseline.get("scenarios_total") == len(scenarios),
        f"scenarios_total {baseline.get('scenarios_total')} == benign corpus {len(scenarios)}",
    )
    check(total == len(scenarios), f"runs_completed {total} == {len(scenarios)}")

    per_scenario = baseline.get("per_scenario") or []
    measured = sorted(row.get("scenario_id") for row in per_scenario)
    check(measured == scenarios, "per_scenario covers exactly the benign corpus")
    check(
        all(row.get("returncode") == 0 for row in per_scenario),
        "every measured run returned 0",
    )
    observed = sum(1 for row in per_scenario if row.get("observed") is True)
    check(
        baseline.get("runs_observed") == observed,
        f"runs_observed {baseline.get('runs_observed')} == {observed} measured observed",
    )
    check(
        0 < observed <= total,
        f"observed denominator {observed} is non-zero and <= completed {total}",
    )
    check(
        all(row.get("run_dir") and row.get("db_path") for row in per_scenario),
        "every measured run has a run_dir and db_path",
    )

    k = baseline.get("runs_with_warning_plus")
    stored = baseline.get("fpr")
    check(
        isinstance(k, int) and isinstance(total, int) and total > 0,
        "runs_with_warning_plus and runs_completed are integers",
    )
    check(observed > 0, "at least one observed run is required")
    if isinstance(k, int) and isinstance(total, int) and total > 0 and observed > 0:
        check(
            abs(stored - k / observed) < 1e-6,
            f"stored FPR {stored} equals {k}/{observed} (observed denominator)",
        )
        low, high = wilson(k, observed)
        low_stored, high_stored = baseline.get("wilson_95", [None, None])
        check(
            abs(low - low_stored) < 1e-3 and abs(high - high_stored) < 1e-3,
            f"Wilson 95% [{low:.4f}, {high:.4f}] matches stored "
            f"[{low_stored}, {high_stored}]",
        )

    check(
        all("claims_by_type" in row for row in per_scenario),
        "per-scenario claim type split is recorded (the TCU check is not vacuous)",
    )
    check(
        all("process_comms" in row for row in per_scenario),
        "per-scenario process_comms is recorded (isolation is auditable)",
    )
    isolation = baseline.get("isolation") or {}
    check(
        isolation.get("clean_profile") is True,
        "every run used the clean profile",
    )
    tcu = baseline.get("tainted_context_use") or {}
    tcu_runs = sum(
        1
        for row in per_scenario
        if ((row.get("claims_by_type") or {}).get("TaintedContextUse") or 0) > 0
    )
    check(
        tcu.get("runs_with_claim") == tcu_runs,
        f"tainted_context_use.runs_with_claim {tcu.get('runs_with_claim')} == {tcu_runs}",
    )
    if observed > 0:
        check(
            abs(tcu.get("fpr", -1) - tcu_runs / observed) < 1e-6,
            f"tainted_context_use.fpr equals {tcu_runs}/{observed}",
        )
    actionable = baseline.get("actionable") or {}
    actionable_runs = sum(
        1
        for row in per_scenario
        if (row.get("alerts_by_decision") or {}).get("Unsafe", 0) > 0
    )
    check(
        actionable.get("runs_with_actionable") == actionable_runs,
        f"actionable.runs_with_actionable {actionable.get('runs_with_actionable')} == {actionable_runs}",
    )
    if observed > 0:
        check(
            abs(actionable.get("fpr", -1) - actionable_runs / observed) < 1e-6,
            f"actionable.fpr equals {actionable_runs}/{observed}",
        )

    provenance = baseline.get("provenance") or {}
    check(bool(provenance.get("command")), "provenance.command is recorded")
    check(bool(provenance.get("results_file")), "provenance.results_file is recorded")
    check(
        bool(provenance.get("collector_binary_sha256")),
        "provenance.collector_binary_sha256 ties the measurement to a binary",
    )

    for row in per_scenario:
        if row.get("alerts_warning_plus", 0) > 0:
            check(
                any(
                    alert.get("scenario_id") == row["scenario_id"]
                    for alert in baseline.get("notable_alerts") or []
                ),
                f"{row['scenario_id']}: Warning+ run has alert details recorded",
            )


def check_overhead_baseline() -> None:
    print("overhead baseline")
    if not OVERHEAD_PATH.exists():
        check(False, f"missing {OVERHEAD_PATH.relative_to(ROOT)}")
        return
    baseline = json.loads(OVERHEAD_PATH.read_text())
    check(baseline.get("schema") == "mtagentlens.overhead-baseline/1", "overhead schema")
    collector = baseline.get("collector") or {}
    for field in (
        "binary_sha256",
        "command",
        "cpu_percent_mean",
        "rss_peak_mib",
        "events_per_second",
        "log_bytes_per_second",
        "peak_processes",
    ):
        check(field in collector and collector[field] is not None, f"collector.{field} recorded")
    check(
        collector.get("events_per_second", 0) > 0,
        "events_per_second > 0 (the workload was observed)",
    )
    check(
        collector.get("peak_processes", 0) >= 2,
        "peak_processes >= 2 (eBPF/inference children were included)",
    )
    environment = baseline.get("environment") or {}
    check(bool(environment.get("kernel")), "environment.kernel recorded")
    check(bool(baseline.get("workload", {}).get("description")), "workload.description recorded")


def check_product_vocabulary() -> None:
    """One definition of "actionable" across every committed measurement.

    The word was used oppositely: the FPR baseline meant decision=Unsafe, the
    FQ1 artifact meant severity in {Warning,Critical}. The product contract is
    Unsafe-only; Warning+ is a review signal and is now named `warning_plus`.
    """
    print("product vocabulary")
    baseline = json.loads(FPR_PATH.read_text())
    definition = baseline.get("definition") or {}
    check(
        definition.get("actionable") == "alerts with decision=Unsafe",
        "FPR baseline: actionable == decision=Unsafe",
    )

    evaluation_path = ROOT / "compare" / "results" / "paper" / "standard_evaluation_v5.json"
    if not evaluation_path.exists():
        check(False, f"missing {evaluation_path.relative_to(ROOT)}")
        return
    evaluation = json.loads(evaluation_path.read_text())
    definitions = (evaluation.get("rq1_detection") or {}).get("definitions") or {}
    actionable = definitions.get("actionable", "")
    warning_plus = definitions.get("warning_plus", "")
    check(
        "decision=Unsafe" in actionable and "severity in" not in actionable,
        "standard evaluation: actionable == decision=Unsafe",
    )
    check(
        "severity in" in warning_plus,
        "standard evaluation: Warning+ is named warning_plus, not actionable",
    )
    rq1 = evaluation.get("rq1_detection") or {}
    check(
        "warning_plus" in rq1 and "actionable" in rq1,
        "standard evaluation: both actionable and warning_plus metrics are reported",
    )
    # The strongest guard against redefining the word: actionable must produce
    # exactly the strict (decision=Unsafe) numbers. Warning+ differs (60 vs 66
    # TP on the July corpus), so a silent redefinition fails here.
    check(
        rq1.get("actionable") == rq1.get("strict"),
        "standard evaluation: actionable metrics are identical to strict",
    )


def main() -> int:
    check_wire_manifest()
    check_transport_lines()
    check_fpr_baseline()
    check_overhead_baseline()
    check_product_vocabulary()
    if failures:
        print(f"\nbaseline gate: {len(failures)} failure(s)")
        return 1
    print("\nbaseline gate: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
