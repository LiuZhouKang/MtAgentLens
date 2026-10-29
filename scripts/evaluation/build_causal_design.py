#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Build evidence-linked scenario design proposals, never runtime causal truth."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "mtagentlens.causal-design-proposal/1"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _quote(path: Path, pointer: str, value: object, source_root: Path) -> dict:
    return {"path": path.resolve().relative_to(source_root.resolve()).as_posix(),
            "sha256": _digest(path), "json_pointer": pointer, "quote": value}


def build_proposal(scenario_path: Path, truth_path: Path, *, source_root: Path) -> dict:
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    sid = truth["scenario_id"]
    if scenario.get("id") != sid:
        raise ValueError("scenario identity does not match ground truth")
    roles = []
    for i, agent in enumerate(scenario.get("agents") or []):
        roles.append({"design_role_index": i, "role_file": agent.get("role_file"),
            "task_evidence": _quote(scenario_path, f"/agents/{i}", agent, source_root)})
    carrier = (truth.get("payload") or {}).get("carrier")
    entry = _quote(truth_path, "/payload/carrier", carrier, source_root) if carrier else None
    hypotheses = []
    for i, behavior in enumerate(truth.get("expected_behaviors") or []):
        hypotheses.append({"behavior_id": behavior.get("behavior_id"),
            "boundary_type": behavior.get("boundary_type"),
            "description_evidence": _quote(truth_path, f"/expected_behaviors/{i}/description",
                behavior.get("description"), source_root),
            "required_runtime_evidence": _quote(truth_path,
                f"/expected_behaviors/{i}/evidence_requirements", behavior.get("evidence_requirements"), source_root)})
    return {"schema": SCHEMA, "scenario_id": sid, "scope": "scenario_design",
        "status": "model_proposal" if hypotheses or entry else "unknown",
        "provenance": {"producer_kind": "deterministic_source_extraction", "human_review": "not_performed",
            "collector_output_consulted": False, "independent_runtime_validation": False},
        "eligible_for_runtime_metrics": False, "runtime_reference": None,
        "sources": [{"path": scenario_path.resolve().relative_to(source_root.resolve()).as_posix(),
                     "sha256": _digest(scenario_path)},
                    {"path": truth_path.resolve().relative_to(source_root.resolve()).as_posix(),
                     "sha256": _digest(truth_path)}],
        "roles": roles, "design_hypothesis": {"entry_point_candidate": entry,
            "root_agent": None, "causal_edges": [], "behavior_hypotheses": hypotheses},
        "limitations": ["Source-grounded design expectations are not independent executed-run observations.",
            "Role order is not a causal edge; role names are not collector agent identities.",
            "A carrier quote is an entry candidate, not proof of ingestion or influence.",
            "Benign or absent design hypotheses do not establish a negative runtime causal label."],
        "adjudication_requirements": ["Bind a specific run_id and immutable raw execution artifacts.",
            "Map each design role to observed identities using independent execution evidence.",
            "Locate source ingestion, authorization boundary and downstream action with evidence IDs.",
            "Record independently reviewed root agent, entry and each causal edge, or abstain per field.",
            "Do not copy detector claims, rankings or reconstructed chains as reference truth."]}


def verify_proposal(proposal: dict, *, source_root: Path) -> None:
    if (proposal.get("schema") != SCHEMA or proposal.get("scope") != "scenario_design"
            or proposal.get("eligible_for_runtime_metrics") is not False
            or proposal.get("runtime_reference") is not None):
        raise ValueError("invalid design proposal contract")
    def visit(value):
        if isinstance(value, dict):
            if "path" in value and "sha256" in value:
                path = (source_root / value["path"]).resolve()
                if not path.is_relative_to(source_root.resolve()):
                    raise ValueError("evidence path escapes source root")
                if _digest(path) != value["sha256"]:
                    raise ValueError(f"evidence hash mismatch: {value['path']}")
                if "json_pointer" in value:
                    actual = json.loads(path.read_text(encoding="utf-8"))
                    for component in value["json_pointer"].split("/")[1:]:
                        key = component.replace("~1", "/").replace("~0", "~")
                        actual = actual[int(key)] if isinstance(actual, list) else actual[key]
                    if actual != value["quote"]:
                        raise ValueError("evidence quote mismatch")
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(proposal)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "datasets/FuxiBench")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((args.dataset / "prompt_inputs/manifest.json").read_text())
    truths = {}
    for path in (args.dataset / "ground_truth").rglob("*.json"):
        if any(p.startswith("_") for p in path.relative_to(args.dataset / "ground_truth").parts):
            continue
        value = json.loads(path.read_text())
        sid = value["scenario_id"]
        if sid in truths:
            raise ValueError(f"duplicate ground truth identity: {sid}")
        truths[sid] = path
    counts = {"total": 0, "multi_agent": 0, "model_proposal": 0, "unknown": 0,
              "runtime_references": 0}
    for entry in manifest["entries"]:
        sid = entry["scenario_id"]
        target = args.dataset / "ground_truth/_causal" / f"{sid}.json"
        if args.verify:
            proposal = json.loads(target.read_text())
            if proposal.get("scenario_id") != sid:
                raise ValueError("causal proposal identity mismatch")
        else:
            proposal = build_proposal((ROOT / entry["source_scenario_path"]) if str(entry["source_scenario_path"]).startswith("datasets/") else (args.dataset / entry["source_scenario_path"]), truths[sid], source_root=args.dataset)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        verify_proposal(proposal, source_root=args.dataset)
        counts["total"] += 1
        counts["multi_agent"] += len(proposal["roles"]) > 1
        counts[proposal["status"]] += 1
    print(json.dumps(counts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
