#!/usr/bin/env python3
"""Find stronger local run artifacts for frozen dataset problem samples."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DETECTING_DECISIONS = {"Unsafe", "Uncertain"}


@dataclass(frozen=True)
class RunCandidate:
    scenario_id: str
    run_dir: Path
    db_path: Path
    claims: int
    detecting_alerts: int
    safe_alerts: int
    alerts: int
    actions: int
    evidence: int
    agents: int
    boundaries: int
    size_bytes: int
    expected_fixture_dir: str | None
    observed_fixture_hints: tuple[str, ...]
    fixture_match: bool
    cross_fixture_contamination: bool
    claim_policy_ids: tuple[str, ...]

    def score(self) -> tuple[int, int, int, int, int, int]:
        return (
            1 if self.fixture_match else 0,
            0 if not self.cross_fixture_contamination else -1,
            self.claims,
            self.detecting_alerts,
            self.evidence,
            self.actions,
            self.boundaries,
            self.size_bytes,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "run_dir": self.run_dir.as_posix(),
            "db_path": self.db_path.as_posix(),
            "claims": self.claims,
            "detecting_alerts": self.detecting_alerts,
            "safe_alerts": self.safe_alerts,
            "alerts": self.alerts,
            "actions": self.actions,
            "evidence": self.evidence,
            "agents": self.agents,
            "boundaries": self.boundaries,
            "size_bytes": self.size_bytes,
            "expected_fixture_dir": self.expected_fixture_dir,
            "observed_fixture_hints": list(self.observed_fixture_hints),
            "fixture_match": self.fixture_match,
            "cross_fixture_contamination": self.cross_fixture_contamination,
            "claim_policy_ids": list(self.claim_policy_ids),
            "score": list(self.score()),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("compare/Dataset/FuxiBench"))
    parser.add_argument("--runs-root", type=Path, action="append", default=[Path("runs"), Path("env/runs")])
    parser.add_argument(
        "--scenario-id",
        action="append",
        default=[],
        help="Additional scenario id to include even if it is no longer in an action queue.",
    )
    parser.add_argument("--apply", action="store_true", help="Copy selected responsibility.db files into dataset traces")
    args = parser.parse_args()

    dataset = args.dataset.resolve()
    manifest = json.loads((dataset / "quality" / "manifest.json").read_text(encoding="utf-8"))
    targets = sorted(set(target_scenario_ids(dataset)) | set(args.scenario_id))
    expected_fixtures = load_expected_fixture_dirs()
    candidates = collect_candidates(targets, [root.resolve() for root in args.runs_root], expected_fixtures)
    report = build_report(dataset, manifest, targets, candidates, load_rerun_adjudications(dataset))

    out_dir = dataset / "quality"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "replacement_run_candidates.json", report)

    if args.apply:
        applied = apply_replacements(dataset, manifest, report["recommendations"])
        report["applied"] = applied
        write_json(out_dir / "replacement_run_candidates.json", report)
        print(f"Applied {len(applied)} replacements")
    else:
        print(f"Wrote {out_dir / 'replacement_run_candidates.json'}")
    print_summary(report)
    return 0


def target_scenario_ids(dataset: Path) -> list[str]:
    quality = dataset / "quality"
    ids: set[str] = set()
    for filename in ("rerun_queue.jsonl", "exclude_review_queue.jsonl"):
        path = quality / filename
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                ids.add(json.loads(line)["scenario_id"])
    return sorted(ids)


def collect_candidates(
    targets: list[str],
    roots: list[Path],
    expected_fixtures: dict[str, str],
) -> dict[str, list[RunCandidate]]:
    by_id: dict[str, list[RunCandidate]] = {sid: [] for sid in targets}
    target_set = set(targets)
    for root in roots:
        if not root.exists():
            continue
        for sid_file in root.glob("*/.scenario_id"):
            sid = sid_file.read_text(encoding="utf-8").strip()
            if sid not in target_set:
                continue
            run_dir = sid_file.parent
            db_path = run_dir / "collector" / "trace.responsibility.db"
            if not db_path.exists():
                continue
            by_id[sid].append(read_candidate(sid, run_dir, db_path, expected_fixtures.get(sid)))
    for sid in by_id:
        by_id[sid].sort(key=lambda candidate: candidate.score(), reverse=True)
    return by_id


def read_candidate(
    scenario_id: str,
    run_dir: Path,
    db_path: Path,
    expected_fixture_dir: str | None = None,
) -> RunCandidate:
    con = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in con.execute("select name from sqlite_master where type='table'")
            if not row[0].startswith("sqlite_")
        }
        hints = observed_fixture_hints(con, tables)
        policy_ids = claim_policy_ids(con, tables)
        fixture_match = fixture_matches(expected_fixture_dir, hints)
        return RunCandidate(
            scenario_id=scenario_id,
            run_dir=run_dir,
            db_path=db_path,
            claims=count(con, tables, "responsibility_claims"),
            detecting_alerts=count_where(
                con,
                tables,
                "responsibility_alerts",
                "decision in ('Unsafe','Uncertain')",
            ),
            safe_alerts=count_where(con, tables, "responsibility_alerts", "decision = 'Safe'"),
            alerts=count(con, tables, "responsibility_alerts"),
            actions=count(con, tables, "actions"),
            evidence=count(con, tables, "correlation_evidence"),
            agents=count(con, tables, "agent_identities"),
            boundaries=count(con, tables, "subagent_boundaries"),
            size_bytes=db_path.stat().st_size,
            expected_fixture_dir=expected_fixture_dir,
            observed_fixture_hints=tuple(hints),
            fixture_match=fixture_match,
            cross_fixture_contamination=cross_fixture_contamination(
                scenario_id,
                expected_fixture_dir,
                hints,
                policy_ids,
            ),
            claim_policy_ids=tuple(policy_ids),
        )
    finally:
        con.close()


def count(con: sqlite3.Connection, tables: set[str], table: str) -> int:
    if table not in tables:
        return 0
    return int(con.execute(f"select count(*) from {table}").fetchone()[0])


def count_where(con: sqlite3.Connection, tables: set[str], table: str, where: str) -> int:
    if table not in tables:
        return 0
    return int(con.execute(f"select count(*) from {table} where {where}").fetchone()[0])


def build_report(
    dataset: Path,
    manifest: dict[str, Any],
    targets: list[str],
    candidates: dict[str, list[RunCandidate]],
    rerun_adjudications: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rerun_adjudications = rerun_adjudications or {}
    samples = {sample["scenario_id"]: sample for sample in manifest.get("samples", [])}
    recommendations = []
    for sid in targets:
        sample = samples.get(sid, {})
        best = candidates.get(sid, [None])[0]
        frozen = frozen_counts(dataset, sample)
        adjudication = rerun_adjudications.get(sid)
        frozen_mismatch = bool(frozen) and not frozen.get("fixture_match", True)
        if best is None:
            action = "rerun_required"
            reason = "no local run candidate found"
        elif frozen_mismatch and best.fixture_match:
            action = "restore_fixture_matched_run"
            reason = "current frozen DB does not reference the expected fixture"
        elif not best.fixture_match:
            action = "rerun_required"
            reason = "best local run does not reference the expected fixture"
        elif adjudication and adjudication.get("recommended_execution_status") == "executed_false_negative":
            action = "backend_false_negative"
            reason = "latest rerun verifier passed, but backend emitted no claims or detecting alerts"
        elif best.claims > 0 and best.detecting_alerts > 0:
            action = "replace_from_local_run"
            reason = "local run has claims and detecting alerts"
        else:
            action = "rerun_required"
            reason = "no local run candidate has claims and detecting alerts"
        recommendations.append(
            {
                "scenario_id": sid,
                "action": action,
                "reason": reason,
                "frozen": frozen,
                "best_candidate": best.to_json() if best else None,
                "rerun_adjudication": adjudication,
                "candidate_count": len(candidates.get(sid, [])),
            }
        )
    return {
        "dataset": dataset.as_posix(),
        "targets": targets,
        "recommendations": recommendations,
        "candidates": {
            sid: [candidate.to_json() for candidate in scenario_candidates]
            for sid, scenario_candidates in sorted(candidates.items())
        },
    }


def frozen_counts(dataset: Path, sample: dict[str, Any]) -> dict[str, int]:
    paths = sample.get("paths", {}) if isinstance(sample, dict) else {}
    value = paths.get("responsibility_db")
    if not isinstance(value, str):
        return {}
    db_path = dataset / value
    if not db_path.exists():
        return {}
    expected_fixture = load_expected_fixture_dirs().get(str(sample.get("scenario_id", "")))
    candidate = read_candidate(str(sample.get("scenario_id", "")), db_path.parent, db_path, expected_fixture)
    return {
        "claims": candidate.claims,
        "detecting_alerts": candidate.detecting_alerts,
        "safe_alerts": candidate.safe_alerts,
        "alerts": candidate.alerts,
        "actions": candidate.actions,
        "evidence": candidate.evidence,
        "agents": candidate.agents,
        "boundaries": candidate.boundaries,
        "size_bytes": candidate.size_bytes,
        "expected_fixture_dir": candidate.expected_fixture_dir,
        "observed_fixture_hints": list(candidate.observed_fixture_hints),
        "fixture_match": candidate.fixture_match,
        "cross_fixture_contamination": candidate.cross_fixture_contamination,
        "claim_policy_ids": list(candidate.claim_policy_ids),
    }


def apply_replacements(
    dataset: Path,
    manifest: dict[str, Any],
    recommendations: list[dict[str, Any]],
) -> list[dict[str, str]]:
    samples = {sample["scenario_id"]: sample for sample in manifest.get("samples", [])}
    applied = []
    for rec in recommendations:
        if rec["action"] not in {"replace_from_local_run", "restore_fixture_matched_run"}:
            continue
        sid = rec["scenario_id"]
        sample = samples[sid]
        destination = dataset / sample["paths"]["responsibility_db"]
        source = Path(rec["best_candidate"]["db_path"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        (destination.parent / ".scenario_id").write_text(sid, encoding="utf-8")
        applied.append(
            {
                "scenario_id": sid,
                "action": rec["action"],
                "source": source.as_posix(),
                "destination": destination.as_posix(),
                "expected_fixture_dir": rec["best_candidate"].get("expected_fixture_dir"),
                "observed_fixture_hints": rec["best_candidate"].get("observed_fixture_hints", []),
                "cross_fixture_contamination": str(rec["best_candidate"].get("cross_fixture_contamination", False)),
            }
        )
    return applied


def print_summary(report: dict[str, Any]) -> None:
    for rec in report["recommendations"]:
        best = rec["best_candidate"] or {}
        print(
            f"{rec['scenario_id']}: {rec['action']} "
            f"(claims={best.get('claims', 0)}, detecting={best.get('detecting_alerts', 0)}, "
            f"fixture_match={best.get('fixture_match')}, contamination={best.get('cross_fixture_contamination')}, "
            f"candidates={rec['candidate_count']})"
        )


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def load_expected_fixture_dirs() -> dict[str, str]:
    expected: dict[str, str] = {}
    scenarios_root = Path("env/scenarios")
    if not scenarios_root.exists():
        return expected
    for path in scenarios_root.rglob("*.json"):
        if "archive" in path.parts or "backups" in path.parts:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        scenario_id = data.get("id") or path.stem
        fixture_dir = data.get("fixture_dir")
        if isinstance(scenario_id, str) and isinstance(fixture_dir, str) and fixture_dir:
            expected[scenario_id] = fixture_dir.strip("/")
    return expected


def load_rerun_adjudications(dataset: Path) -> dict[str, dict[str, Any]]:
    path = dataset / "quality" / "rerun_adjudication.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        item["scenario_id"]: item
        for item in data.get("samples", [])
        if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
    }


def observed_fixture_hints(con: sqlite3.Connection, tables: set[str]) -> list[str]:
    if "intents" not in tables:
        return []
    columns = {row[1] for row in con.execute("pragma table_info(intents)")}
    if "tool_args_json" not in columns:
        return []
    hints: set[str] = set()
    for (raw,) in con.execute("select tool_args_json from intents limit 2000"):
        if not isinstance(raw, str):
            continue
        for match in re.finditer(r"env/scenarios/fixtures/([^\"\\\]\s`]+)", raw):
            hint = sanitize_fixture_hint(match.group(1))
            if hint:
                hints.add(hint)
    return sorted(hints)


def sanitize_fixture_hint(value: str) -> str:
    value = value.strip().strip("'\"`").replace("\\n", "\n")
    value = value.split("\n", 1)[0]
    value = value.rstrip("/.,;:)]}'\"`")
    value = value.replace("/**/*", "")
    value = value.replace("/*", "")
    while "//" in value:
        value = value.replace("//", "/")
    return value.strip("/")


def fixture_matches(expected_fixture_dir: str | None, hints: list[str]) -> bool:
    if not expected_fixture_dir:
        return True
    expected = expected_fixture_dir.strip("/")
    return any(hint == expected or hint.startswith(expected + "/") for hint in hints)


def claim_policy_ids(con: sqlite3.Connection, tables: set[str]) -> list[str]:
    if "responsibility_claims" not in tables:
        return []
    columns = {row[1] for row in con.execute("pragma table_info(responsibility_claims)")}
    if "policy_id" not in columns:
        return []
    return sorted({
        row[0]
        for row in con.execute("select policy_id from responsibility_claims where policy_id is not null")
        if isinstance(row[0], str) and row[0]
    })


def cross_fixture_contamination(
    scenario_id: str,
    expected_fixture_dir: str | None,
    hints: list[str],
    policy_ids: list[str] | tuple[str, ...] = (),
) -> bool:
    if expected_fixture_dir and hints:
        expected_root = expected_fixture_dir.strip("/").split("/")[0]
        expected = expected_fixture_dir.strip("/")
        unrelated = [
            hint
            for hint in hints
            if not (hint == expected or hint.startswith(expected + "/"))
            and not hint.startswith(expected_root + "/")
            and hint != expected_root
        ]
        if unrelated:
            return True
    return claim_policy_contamination(scenario_id, policy_ids)


def claim_policy_contamination(scenario_id: str, policy_ids: list[str] | tuple[str, ...]) -> bool:
    allowed_by_scenario = {
        "prompt-injection": {
            "policy.content.canary_disclosure",
            "policy.content.credential",
            "policy.content.injection",
        },
        "resource-disk-fill": {
            "policy.content.resource_abuse",
        },
    }
    allowed = allowed_by_scenario.get(scenario_id)
    if not allowed:
        return False
    return any(policy_id not in allowed for policy_id in policy_ids)


if __name__ == "__main__":
    raise SystemExit(main())
