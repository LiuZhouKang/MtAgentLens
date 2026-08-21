#!/usr/bin/env python3
"""
Lightweight regression scaffold for competition phase.
Compares expected_alerts/*.json against an alerts SQLite DB.

Usage:
  python env/scripts/eval_expected_alerts.py --expected docs/evaluation/expected_alerts --db run.log.alerts.db --run-dir env/runs/<run-id>
"""

import argparse
import json
import sqlite3
import tempfile
from pathlib import Path
from collections import Counter

from accountability_evidence import validate_accountability_evidence, validate_runtime_alert_evidence
from eval_competition import determine_run_scenarios


EXCLUSION_RULES = {
    "exclude_uncertain_confidence": True,
    "exclude_info_severity": True,
    "source_of_truth": "docs/evaluation/expected_alerts/*.json",
}


def _should_include_expected_alert(alert: dict) -> bool:
    return alert.get("confidence") != "uncertain" and str(alert.get("severity", "")).upper() != "INFO"


def load_expected(expected_dir: Path, scenario_ids: list[str] | None = None):
    scenarios = {}
    manifest = []
    missing_files = []
    files = []
    if scenario_ids is None:
        files = sorted(expected_dir.glob("*.json"))
    else:
        files = [expected_dir / f"{scenario_id}.json" for scenario_id in scenario_ids]

    for p in files:
        if not p.exists():
            missing_files.append(str(p))
            continue
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        alerts = data.get("alerts", [])
        included = [a for a in alerts if _should_include_expected_alert(a)]
        excluded_info = sum(1 for a in alerts if str(a.get("severity", "")).upper() == "INFO")
        excluded_uncertain = sum(1 for a in alerts if a.get("confidence") == "uncertain" and str(a.get("severity", "")).upper() != "INFO")
        excluded_other = len(alerts) - len(included) - excluded_info - excluded_uncertain
        scenarios[p.stem] = {
            "categories": [a.get("category") for a in included],
            "count": len(included),
        }
        manifest.append(
            {
                "scenario_id": p.stem,
                "expected_file": str(p),
                "expected_total": len(included),
                "expected_by_category": dict(Counter(a.get("category") for a in included)),
                "excluded_counts": {
                    "info": excluded_info,
                    "uncertain": excluded_uncertain,
                    "other": excluded_other,
                },
            }
        )
    return scenarios, manifest, missing_files


def load_actual(db_path: Path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT category, severity, evidence_source, evidence_json FROM alerts")
        rows = cur.fetchall()
    except sqlite3.OperationalError:
        try:
            cur.execute("SELECT category, severity, evidence_source FROM alerts")
            rows = [(cat, sev, source, None) for cat, sev, source in cur.fetchall()]
        except sqlite3.OperationalError:
            cur.execute("SELECT category, severity FROM alerts")
            rows = [(cat, sev, None, None) for cat, sev in cur.fetchall()]
    conn.close()

    cats = Counter()
    alerts = []
    for cat, sev, evidence_source, evidence_json in rows:
        if str(sev).upper() == "INFO":
            continue
        evidence = None
        if evidence_json is not None:
            try:
                evidence = json.loads(evidence_json)
            except (TypeError, ValueError, json.JSONDecodeError):
                evidence = None
        if not validate_runtime_alert_evidence(evidence_source, evidence):
            continue
        cats[cat] += 1
        alerts.append({"category": cat, "severity": sev, "evidence_source": evidence_source, "evidence": evidence})
    return cats, sum(cats.values()), alerts


def resolve_scenarios_or_error(args, ap: argparse.ArgumentParser) -> list[str]:
    explicit = [scenario_id for scenario_id in args.scenario if scenario_id]
    if explicit:
        return explicit
    if args.run_dir:
        return determine_run_scenarios(args.run_dir.resolve())
    if args.all_scenarios:
        return []
    ap.error("authoritative usage requires --run-dir or at least one --scenario; use --all-scenarios only for non-run-bound diagnostic mode")


def _run_self_tests() -> int:
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        expected_dir = root / "expected"
        expected_dir.mkdir()
        (expected_dir / "scenario-a.json").write_text(
            json.dumps(
                {
                    "alerts": [
                        {"category": "prompt_injection", "severity": "CRITICAL", "confidence": "certain"},
                        {"category": "reasoning_loop", "severity": "INFO", "confidence": "certain"},
                        {"category": "context_pollution", "severity": "WARNING", "confidence": "uncertain"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        (expected_dir / "scenario-b.json").write_text(
            json.dumps(
                {
                    "alerts": [
                        {"category": "reasoning_loop", "severity": "WARNING", "confidence": "certain"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        expected, manifest, missing = load_expected(expected_dir)
        assert expected["scenario-a"]["count"] == 1
        assert expected["scenario-a"]["categories"] == ["prompt_injection"]
        assert expected["scenario-b"]["count"] == 1
        assert manifest[0]["excluded_counts"] == {"info": 1, "uncertain": 1, "other": 0}
        assert missing == []

        subset, subset_manifest, subset_missing = load_expected(expected_dir, ["scenario-b", "scenario-missing"])
        assert subset == {"scenario-b": {"categories": ["reasoning_loop"], "count": 1}}
        assert subset_manifest[0]["expected_file"].endswith("scenario-b.json")
        assert len(subset_missing) == 1 and subset_missing[0].endswith("scenario-missing.json")

        run_dir = root / "env" / "runs" / "run-1"
        run_dir.mkdir(parents=True)
        (run_dir / "summary.json").write_text(json.dumps({"scenario_ids": ["scenario-b"]}), encoding="utf-8")

        parser = argparse.ArgumentParser()
        parser.add_argument("--scenario", action="append", default=[])
        parser.add_argument("--run-dir", type=Path)
        parser.add_argument("--all-scenarios", action="store_true")

        args = parser.parse_args(["--scenario", "scenario-a"])
        assert resolve_scenarios_or_error(args, parser) == ["scenario-a"]

        args = parser.parse_args(["--run-dir", str(run_dir)])
        assert resolve_scenarios_or_error(args, parser) == ["scenario-b"]

        args = parser.parse_args(["--all-scenarios"])
        assert resolve_scenarios_or_error(args, parser) == []

        db_path = root / "alerts.db"
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE alerts (category TEXT, severity TEXT, evidence_source TEXT, evidence_json TEXT)")
        cur.executemany(
            "INSERT INTO alerts(category, severity, evidence_source, evidence_json) VALUES (?, ?, ?, ?)",
            [
                ("prompt_injection", "CRITICAL", "causal", json.dumps({"claim_state": "observed", "policy_id": "policy.file.read", "boundary_id": "boundary.file_read", "reason": "file-read boundary match", "accountability_evidence": {"kind": "accountability_evidence", "evidence_refs": ["evidence:prompt_injection"], "directness": "direct", "boundary_id": "boundary.file_read", "causal_link": {"link_id": "link-prompt"}}})),
                ("malformed_causal", "WARNING", "causal", json.dumps({"claim_state": "observed", "policy_id": "policy.file.read", "boundary_id": "boundary.file_read", "reason": "missing nested evidence"})),
                ("invalid_claim_state", "WARNING", "causal", json.dumps({"claim_state": "wrong-claim-state", "policy_id": "policy.file.read", "boundary_id": "boundary.file_read", "reason": "bad claim state", "accountability_evidence": {"kind": "accountability_evidence", "evidence_refs": ["evidence:bad-claim"], "directness": "direct", "boundary_id": "boundary.file_read", "causal_link": {"link_id": "link-bad-claim"}}})),
                ("bare_causal", "CRITICAL", "causal", json.dumps({"kind": "accountability_evidence", "evidence_refs": ["evidence:bare"], "directness": "direct", "boundary_id": "boundary.file_read", "causal_link": {"link_id": "link-bare"}})),
                ("reasoning_loop", "INFO", "anomaly", None),
                ("reasoning_loop", "WARNING", None, None),
            ],
        )
        conn.commit()
        conn.close()

        actual_cats, actual_total, actual_alerts = load_actual(db_path)
        assert actual_total == 2
        assert actual_cats == Counter({"prompt_injection": 1, "reasoning_loop": 1})
        assert actual_alerts == [
            {"category": "prompt_injection", "severity": "CRITICAL", "evidence_source": "causal", "evidence": {"claim_state": "observed", "policy_id": "policy.file.read", "boundary_id": "boundary.file_read", "reason": "file-read boundary match", "accountability_evidence": {"kind": "accountability_evidence", "evidence_refs": ["evidence:prompt_injection"], "directness": "direct", "boundary_id": "boundary.file_read", "causal_link": {"link_id": "link-prompt"}}}},
            {"category": "reasoning_loop", "severity": "WARNING", "evidence_source": None, "evidence": None},
        ]
        assert not any(alert["category"] == "malformed_causal" for alert in actual_alerts)
        assert not any(alert["category"] == "invalid_claim_state" for alert in actual_alerts)
        assert not any(alert["category"] == "bare_causal" for alert in actual_alerts)
        assert validate_runtime_alert_evidence(actual_alerts[0]["evidence_source"], actual_alerts[0]["evidence"])
        assert not validate_runtime_alert_evidence(
            "causal",
            {
                "kind": "accountability_evidence",
                "evidence_refs": ["evidence:bare"],
                "directness": "direct",
                "boundary_id": "boundary.file_read",
                "causal_link": {"link_id": "link-bare"},
            },
        )
        assert not validate_runtime_alert_evidence("causal", {"kind": "trace"})
        assert not validate_runtime_alert_evidence(
            "causal",
            {
                "claim_state": "observed",
                "policy_id": "policy.file.read",
                "boundary_id": "boundary.file_read",
                "reason": "missing nested evidence",
            },
        )
        assert not validate_runtime_alert_evidence(
            "causal",
            {
                "claim_state": "wrong-claim-state",
                "policy_id": "policy.file.read",
                "boundary_id": "boundary.file_read",
                "reason": "bad claim state",
                "accountability_evidence": {
                    "kind": "accountability_evidence",
                    "evidence_refs": ["evidence:bad-claim"],
                    "directness": "direct",
                    "boundary_id": "boundary.file_read",
                    "causal_link": {"link_id": "link-bad-claim"},
                },
            },
        )
        assert not validate_accountability_evidence(
            {
                "kind": "accountability_evidence",
                "evidence_refs": ["evidence:missing-link"],
                "directness": "direct",
                "boundary_id": "boundary.file_read",
            }
        )

    print("eval_expected_alerts.py self-tests passed")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected", required=False)
    ap.add_argument("--db", required=False)
    ap.add_argument("--scenario", action="append", default=[])
    ap.add_argument("--run-dir", type=Path)
    ap.add_argument("--all-scenarios", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _run_self_tests()

    if not args.expected or not args.db:
        ap.error("--expected and --db are required unless --self-test is used")

    repo_root = Path(__file__).resolve().parents[2]
    expected_path = (repo_root / Path(args.expected)).resolve() if not Path(args.expected).is_absolute() else Path(args.expected)
    db_path = (repo_root / Path(args.db)).resolve() if not Path(args.db).is_absolute() else Path(args.db)

    scenario_ids = resolve_scenarios_or_error(args, ap)
    expected, manifest, missing_files = load_expected(expected_path, scenario_ids or None)
    actual_cats, actual_total, _actual_alerts = load_actual(db_path)

    print("=== Expected Alerts Summary ===")
    total_expected = sum(v["count"] for v in expected.values())
    scope_label = scenario_ids if scenario_ids else sorted(expected.keys())
    print(f"scenarios_in_scope={scope_label}")
    print(f"authoritative_scope={not args.all_scenarios}")
    print(f"expected_source_root={expected_path}")
    print(f"expected_count={total_expected}")
    print(f"exclusion_rules={json.dumps(EXCLUSION_RULES, ensure_ascii=False)}")
    print("expected_source_manifest:")
    for entry in manifest:
        print(
            "  "
            + json.dumps(
                entry,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    if missing_files:
        print("missing_expected_files:")
        for path in missing_files:
            print(f"  {path}")
    print("expected_by_category:")
    expected_cat_counter = Counter()
    for v in expected.values():
        expected_cat_counter.update(v["categories"])
    for k, v in expected_cat_counter.items():
        print(f"  {k}: {v}")

    print("\n=== Actual Alerts Summary ===")
    print(f"actual_db={db_path}")
    print(f"actual_count={actual_total}")
    print("actual_by_category:")
    for k, v in actual_cats.items():
        print(f"  {k}: {v}")

    print("\n=== Delta ===")
    all_cats = sorted(set(expected_cat_counter) | set(actual_cats))
    for cat in all_cats:
        exp = expected_cat_counter.get(cat, 0)
        act = actual_cats.get(cat, 0)
        print(f"  {cat}: expected={exp}, actual={act}, delta={act-exp}")


if __name__ == "__main__":
    raise SystemExit(main())
