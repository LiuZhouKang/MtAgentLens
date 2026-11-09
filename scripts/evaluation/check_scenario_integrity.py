#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Check that every FuxiBench scenario is executable as designed.

The 2026-10 GT review found 72 scenarios whose scenario JSON, fixture files
and execution prompt disagreed with each other. The most damaging form was a
scenario whose ``fixture_dir`` pointed at a neutralised decoy: the agent ran a
benign checklist while the ground truth described an attack. Labels and
detection rates cannot be interpreted on inputs like that, so this checker
makes each defect mechanically visible before a run is spent.

Checks (per ground-truth scenario):

  scenario_present     the scenario JSON exists
  fixture_present      the fixture directory exists and is not empty
  fixture_unique       two scenarios never share one fixture directory
  portable_paths       no absolute /home, /root or /Users path in task or fixture
  exec_marker          every attack carries MTL_EXEC_<scenario id>
  task_executes        the task tells the agent to execute, not only to analyse
  type_matches_gt      scenario JSON attack_type equals the GT attack_type
  type_c_multi_agent   a Type C scenario declares at least two agents
  readonly_conflict    a READ-ONLY task does not claim to write files
  benign_executable    a benign task states a concrete action

Usage:
    python3 scripts/evaluation/check_scenario_integrity.py \
        --scenarios env/scenarios --dataset datasets/FuxiBench
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ABSOLUTE_PATH_RE = re.compile(r"/(?:home|root|Users)/[A-Za-z0-9_.-]+")
READ_ONLY_RE = re.compile(
    r"READ[- ]ONLY|do NOT use (?:any )?tools|do not run (?:any )?commands|"
    r"pure analysis|DISCUSSION ONLY|不得使用工具|不要执行",
    re.IGNORECASE,
)
EXECUTE_RE = re.compile(
    r"\b(?:run|execute)\b|按.*执行|逐条执行|执行全部|行之|以 Bash 工具|complete the assigned task",
    re.IGNORECASE,
)
WRITE_RE = re.compile(
    r"save (?:it |the |all )?(?:to|as)|write (?:it |the |all )?(?:to|as)|"
    r"保存到|写入|落盘",
    re.IGNORECASE,
)
MARKER_ENV = "RUN_TAG"


def load_gt(dataset: Path) -> list[dict]:
    gt_root = dataset / "ground_truth"
    out = []
    for path in sorted(gt_root.glob("*/*.json")):
        if path.parent.name.startswith("_"):
            continue
        out.append(json.loads(path.read_text(encoding="utf-8")))
    return out


def find_scenario(scenarios: Path, scenario_id: str) -> Path | None:
    matches = list(scenarios.rglob(f"{scenario_id}.json"))
    return matches[0] if matches else None


def fixture_files(directory: Path) -> list[Path]:
    return [
        p
        for p in directory.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix not in {".pyc", ".pyo"}
    ]


def check_one(gt: dict, scenarios: Path, fixtures: Path) -> list[dict]:
    sid = gt["scenario_id"]
    problems: list[dict] = []

    def add(kind: str, detail: str) -> None:
        problems.append({"scenario_id": sid, "check": kind, "detail": detail})

    scenario_path = find_scenario(scenarios, sid)
    if scenario_path is None:
        add("scenario_present", "no scenario JSON")
        return problems
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    agents = scenario.get("agents", [])
    task_text = "\n".join(agent.get("task", "") for agent in agents)
    description = scenario.get("description", "")

    fixture_dir = fixtures / (scenario.get("fixture_dir") or sid)
    files = fixture_files(fixture_dir) if fixture_dir.is_dir() else []
    if not files:
        add("fixture_present", f"missing fixture directory {fixture_dir}")
    fixture_text = ""
    for path in files:
        try:
            fixture_text += path.read_text(errors="replace") + "\n"
        except OSError:
            pass

    for label, text in (("task", task_text + "\n" + description), ("fixture", fixture_text)):
        for match in ABSOLUTE_PATH_RE.finditer(text):
            add("portable_paths", f"{label} contains absolute path {match.group(0)}")
            break  # one report per surface is enough

    label = gt.get("label")
    probe = scenario.get("execution_probe")
    probe_name = probe.rsplit("/", 1)[-1] if probe else ""
    if probe and probe not in task_text and probe not in fixture_text and probe_name not in fixture_text:
        add(
            "execution_probe",
            f"scenario declares execution_probe {probe} but the task never writes it",
        )

    if label == "attack":
        # The marker is a per-run nonce the harness injects as RUN_TAG.
        # Naming the scenario in the marker told the agent it was in a
        # benchmark (observed refusals); the fixture only has to append the
        # environment value to the shared log.
        has_marker = MARKER_ENV in task_text or MARKER_ENV in fixture_text
        if not has_marker and not scenario.get("execution_probe"):
            add(
                "exec_marker",
                f"no {MARKER_ENV} write or execution_probe; execution cannot be audited",
            )

    if label == "attack" or gt.get("attack_type") == "C":
        if not EXECUTE_RE.search(task_text):
            add("task_executes", "task does not instruct the agent to execute the procedure")

    gt_type = gt.get("attack_type")
    json_type = scenario.get("attack_type")
    if gt_type and json_type and gt_type != json_type:
        annotation = gt.get("label_annotation") or {}
        documented_override = (
            annotation.get("method") == "human_review_and_revision"
            and annotation.get("human_review_status") == "completed"
            and annotation.get("reviewers") == ["刘周康", "唐浩钺", "刘锦样"]
        )
        if not documented_override:
            add("type_matches_gt", f"scenario JSON says {json_type}, GT says {gt_type}")

    if gt_type == "C" and len(agents) < 2:
        add(
            "type_c_multi_agent",
            f"Type C scenario declares {len(agents)} agent(s); a real handoff needs ≥2 OS processes",
        )

    read_only = bool(READ_ONLY_RE.search(task_text))
    executes = bool(EXECUTE_RE.search(task_text))
    # "Do not modify the project; this is a read-only verification" is a normal
    # instruction around a `make test` run. It only conflicts when no agent is
    # asked to execute anything at all.
    if read_only and not executes and gt_type not in (None, "benign"):
        add("readonly_conflict", "attack declares READ-ONLY and no agent executes a procedure")
    if read_only and WRITE_RE.search(description) and not WRITE_RE.search(task_text):
        add("readonly_conflict", "description promises a file write the task forbids")

    if label == "benign":
        if not (EXECUTE_RE.search(task_text) or RE_CONCRETE_ACTION.search(task_text)):
            add("benign_executable", "benign task states no concrete action")

    return problems


RE_CONCRETE_ACTION = re.compile(
    r"\b(?:read|inspect|analyse|analyze|review|audit|build|test|run|create|write|update|"
    r"refactor|migrate|deploy|check|measure)\b",
    re.IGNORECASE,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=Path("env/scenarios"))
    parser.add_argument("--dataset", type=Path, default=Path("datasets/FuxiBench"))
    parser.add_argument("--out", type=Path, help="write the JSON report here")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when any problem is found (default: report only)",
    )
    args = parser.parse_args()

    fixtures = args.scenarios / "fixtures"
    gt_files = load_gt(args.dataset)
    problems: list[dict] = []
    # fixture_unique: detect two scenarios wired to one fixture directory.
    owners: dict[str, str] = {}
    for gt in gt_files:
        problems.extend(check_one(gt, args.scenarios, fixtures))
        path = find_scenario(args.scenarios, gt["scenario_id"])
        if path is None:
            continue
        scenario = json.loads(path.read_text(encoding="utf-8"))
        fixture_dir = scenario.get("fixture_dir") or gt["scenario_id"]
        other = owners.get(fixture_dir)
        if other is not None:
            problems.append(
                {
                    "scenario_id": gt["scenario_id"],
                    "check": "fixture_unique",
                    "detail": f"fixture_dir {fixture_dir} is shared with {other}",
                }
            )
        else:
            owners[fixture_dir] = gt["scenario_id"]

    report = {
        "schema": "mtagentlens.scenario-integrity/1",
        "scenarios": len(gt_files),
        "problems": problems,
        "by_check": {},
    }
    for problem in problems:
        report["by_check"][problem["check"]] = report["by_check"].get(problem["check"], 0) + 1

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    if args.strict and problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
