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
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WIRE_DIR = ROOT / "collector" / "tests" / "fixtures" / "wire"
BENIGN_DIR = ROOT / "env" / "scenarios" / "benign"
FPR_PATH = WIRE_DIR.parent / "baselines" / "fpr-baseline.json"
OVERHEAD_PATH = WIRE_DIR.parent / "baselines" / "overhead-baseline.json"

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
    """Bind actionable=Unsafe to current metrics, independent of old reports."""
    print("product vocabulary")
    baseline = json.loads(FPR_PATH.read_text())
    definition = baseline.get("definition") or {}
    check(
        definition.get("actionable") == "alerts with decision=Unsafe",
        "FPR baseline: actionable == decision=Unsafe",
    )

    # Exercise the production join of ground truth, ledger and run summaries.
    # Every run has Warning+ signals, including Safe and Uncertain verdicts;
    # using severity instead of the final decision must fail this gate.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.evaluation.detection_metrics import build_report

    with tempfile.TemporaryDirectory(prefix="mtagentlens-vocabulary-") as temporary:
        root = Path(temporary)
        release = root / "release"
        campaign = root / "campaign"
        truth = release / "ground_truth" / "synthetic"
        truth.mkdir(parents=True)
        records = []
        for label in ("attack", "benign"):
            for verdict in ("Unsafe", "Safe", "Uncertain"):
                sid = f"{label}-{verdict.lower()}"
                (truth / f"{sid}.json").write_text(json.dumps({
                    "scenario_id": sid, "label": label,
                    "attack_type": "A" if label == "attack" else "benign",
                    "category": "synthetic",
                }), encoding="utf-8")
                run = campaign / "runs" / sid
                run.mkdir(parents=True)
                (run / "summary.json").write_text(json.dumps({
                    "marker_executed": label == "attack",
                    "alerts_warning_plus": 1,
                    "alerts_by_severity": {"Warning": 1},
                }), encoding="utf-8")
                records.append({
                    "state": "completed", "scenario_id": sid,
                    "run_dir": str(run), "final_decision": verdict,
                    "capture_quality_status": "healthy",
                    "alerts_warning_plus": 1,
                    "alerts_by_severity": {"Warning": 1},
                })
        (campaign / "results.jsonl").write_text(
            "\n".join(json.dumps(record) for record in records) + "\n",
            encoding="utf-8",
        )
        report = build_report(campaign, release)

    rows = report.get("scenarios") or []
    check(len(rows) == 6, "current metrics: all six synthetic verdict cases are reported")
    for row in rows:
        check(
            row.get("detected") == (row["scenario_id"] == "attack-unsafe"),
            f"current metrics: {row['scenario_id']} is actionable only for executed Unsafe",
        )
    detection = report.get("detection") or {}
    check(
        detection.get("itt_numerator") == 1
        and detection.get("itt_tpr") == 1 / 3
        and detection.get("pp_tpr") == 1 / 3,
        "current metrics: only Unsafe counts toward attack detection despite Warning+",
    )
    benign = report.get("benign") or {}
    check(
        benign.get("unsafe") == 1 and benign.get("fpr") == 1 / 3,
        "current metrics: only Unsafe counts toward benign FPR despite Warning+",
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
