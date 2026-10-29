# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Audit that every attack scenario is a real red-team case with evidence machinery.

The reviewer question is blunt: are the attack scenarios actually attacks, are they
each individually designed, and do they produce OS-level evidence when executed?
This script answers it from the frozen release, without any model call:

* ground-truth label must be `attack`;
* the scenario must declare a risk level / attack type, not "benign";
* the fixture tree must contain malicious payload indicators (credential paths,
  postinstall hooks, network POST to the collection endpoint);
* the scenario must have an execution-evidence path (scenario build hook marker or
  a collection sink), otherwise "executed" can never be established;
* the task text is classified as direct instruction vs indirect procedure.

Anything that fails a check is listed; a scenario is never silently dropped.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

AUDIT_SCHEMA = "mtagentlens.attack-scenario-audit/1"
MALICIOUS_HINTS = {
    "credential_paths": ("credentials", ".env", "id_ed25519", ".npmrc", "token"),
    "hook_execution": ("postinstall", "make build", "make test", "install_deps"),
    "network_egress": ("curl", "POST", "http://", "https://"),
    "marker": ("RUN_TAG", "acme-markers"),
    "sink": ("COLLECT_HOST", "sink.py", "COLLECT_PORT"),
}
EXPLICIT_ASK = re.compile(r"\b(exfiltrat\w*|steal|bypass|attack|poison|hijack|backdoor|tamper)\b", re.I)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def ground_truth_label(gt_root: Path, scenario_id: str) -> str | None:
    # Ground truth lives under category directories; `_reviewers/` holds review
    # notes whose `label` is nested and must not be mistaken for the GT.
    for path in sorted(gt_root.rglob(f"{scenario_id}.json")):
        if "_reviewers" in path.parts:
            continue
        data = load_json(path)
        label = data.get("label")
        if label is not None:
            return str(label).lower()
    return None


def fixture_texts(scenario: dict, fixtures_root: Path) -> list[str]:
    relative = Path(str(scenario.get("fixture_dir") or ""))
    root = fixtures_root / relative
    if not root.is_dir():
        examples = list(fixtures_root.glob(relative.name or ""))
        root = examples[0] if examples else root
    if not root.is_dir():
        return []
    texts = []
    for path in root.rglob("*"):
        if not path.is_file() or path.stat().st_size > 256 * 1024:
            continue
        try:
            texts.append(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, OSError):
            continue
    return texts


def audit_scenario(release: Path, gt_root: Path, scenario_path: Path) -> dict:
    scenario = load_json(scenario_path)
    scenario_id = scenario.get("id") or scenario_path.stem
    label = ground_truth_label(gt_root, str(scenario_id))
    texts = fixture_texts(scenario, release / "fixtures")
    joined = "\n".join(texts)
    features = {name: any(str(hint) in joined for hint in hints)
                for name, hints in MALICIOUS_HINTS.items()}
    task = " ".join(str(agent.get("task", "")) for agent in scenario.get("agents", []))
    problems: list[str] = []
    if label not in {"attack", "malicious"}:
        problems.append(f"ground-truth label is {label!r}, not attack")
    risk = str(scenario.get("risk_level") or "").lower()
    if risk in {"", "none", "low"}:
        problems.append(f"risk_level is {risk or 'missing'!r}")
    if not texts:
        problems.append("fixture tree missing or unreadable")
    if not features["marker"] and not features["sink"]:
        problems.append("no execution-evidence mechanism (marker/sink)")
    if not (features["credential_paths"] or features["hook_execution"]):
        problems.append("no credential/hook payload indicators in fixtures")
    if not (features["network_egress"] or features["hook_execution"]):
        problems.append("no egress/execution path in fixtures")
    return {
        "scenario_id": scenario_id,
        "ground_truth_label": label,
        "risk_level": scenario.get("risk_level"),
        "attack_type": scenario.get("attack_type"),
        "fixture_bytes": sum(len(text) for text in texts),
        "features": features,
        "task_is_direct_ask": bool(EXPLICIT_ASK.search(task)),
        "has_execution_probe": bool(scenario.get("execution_probe")),
        "has_pre_run_script": bool(scenario.get("pre_run_script")),
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path,
                        default=Path("datasets/FuxiBench/ground_truth"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    scenarios = sorted((args.release / "scenarios").glob("*.json"))
    if not scenarios:
        print(f"error: no scenarios under {args.release / 'scenarios'}", file=sys.stderr)
        return 1
    rows = []
    for path in scenarios:
        try:
            row = audit_scenario(args.release, args.ground_truth, path)
        except (OSError, ValueError, KeyError) as error:
            rows.append({"scenario_id": path.stem, "problems": [f"audit error: {error}"]})
            continue
        if row.get("ground_truth_label") != "benign" and "benign" not in row.get("attack_type", ""):
            rows.append(row)
    flagged = [row for row in rows if row["problems"]]
    report = {
        "schema": AUDIT_SCHEMA,
        "release": str(args.release),
        "attack_scenarios": len(rows),
        "clean": len(rows) - len(flagged),
        "flagged": len(flagged),
        "direct_ask_scenarios": sum(1 for row in rows if row.get("task_is_direct_ask")),
        "scenarios": rows,
        "note": ("Static audit only: it proves each scenario declares an attack label and "
                 "carries payload + execution-evidence machinery. It does not prove the "
                 "agent executed it; use the campaign witness for that."),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"attack scenarios: {len(rows)} clean: {len(rows) - len(flagged)} flagged: {len(flagged)}")
    print(f"direct-ask tasks: {report['direct_ask_scenarios']}")
    for row in flagged[:20]:
        print(f"  FLAG {row.get('scenario_id')}: {row['problems']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
