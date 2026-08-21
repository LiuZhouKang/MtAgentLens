#!/usr/bin/env python3
"""Audit the frozen dataset against paper-grade quality gates.

The script is intentionally read-only for raw samples. It writes derived audit
artifacts under <dataset>/quality/.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CORE_TABLES = (
    "runs",
    "schema_version",
    "process_events",
    "tls_sessions",
    "message_bindings",
    "intents",
    "actions",
    "correlation_evidence",
    "agent_identities",
    "graph_nodes",
    "graph_edges",
    "responsibility_alerts",
    "responsibility_claims",
    "subagent_boundaries",
)

CORE_NONEMPTY = (
    "process_events",
    "tls_sessions",
    "intents",
    "actions",
    "correlation_evidence",
    "agent_identities",
    "responsibility_alerts",
)

GT_REQUIRED_FIELDS = (
    "label",
    "category",
    "attack_type",
    "execution_status",
    "expected_behavior",
    "evidence_contract",
)

SAFE_DECISIONS = {"Safe"}
DETECTING_DECISIONS = {"Unsafe", "Uncertain"}


@dataclass
class SampleAudit:
    scenario_id: str
    category: str
    trace_relpath: str | None = None
    gt_relpath: str | None = None
    case_study_relpath: str | None = None
    db_relpath: str | None = None
    quality_tier: str = "excluded"
    blocking_issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    actions_required: list[str] = field(default_factory=list)
    gt: dict[str, Any] = field(default_factory=dict)
    db_counts: dict[str, int] = field(default_factory=dict)
    alert_decisions: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "category": self.category,
            "quality_tier": self.quality_tier,
            "paths": {
                "trace": self.trace_relpath,
                "ground_truth": self.gt_relpath,
                "case_study": self.case_study_relpath,
                "responsibility_db": self.db_relpath,
            },
            "blocking_issues": self.blocking_issues,
            "warnings": self.warnings,
            "actions_required": self.actions_required,
            "ground_truth_summary": self.gt,
            "db_counts": self.db_counts,
            "alert_decisions": self.alert_decisions,
            "metadata": self.metadata,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Path to frozen dataset root",
    )
    parser.add_argument("--no-write", action="store_true", help="Print only; do not write quality artifacts")
    args = parser.parse_args()

    dataset = args.dataset.resolve()
    audits = audit_dataset(dataset)
    manifest = build_manifest(dataset, audits)
    report = render_report(dataset, manifest, audits)

    if args.no_write:
        print(report)
        return 0 if manifest["summary"]["paper_ready"] == manifest["summary"]["total_samples"] else 2

    out_dir = dataset / "quality"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "manifest.json", manifest)
    write_json(out_dir / "metadata_v2_draft.json", build_metadata_v2_draft(dataset, audits, manifest))
    write_json(out_dir / "sample_audits.json", [a.to_json() for a in audits])
    (out_dir / "audit_report.md").write_text(report, encoding="utf-8")
    write_lists(out_dir, audits)
    write_gt_v2_drafts(dataset, out_dir, audits)
    write_action_queues(out_dir, audits)

    print(f"Wrote {out_dir / 'manifest.json'}")
    print(f"Wrote {out_dir / 'metadata_v2_draft.json'}")
    print(f"Wrote {out_dir / 'sample_audits.json'}")
    print(f"Wrote {out_dir / 'audit_report.md'}")
    print(f"Wrote {out_dir / 'ground_truth_v2_draft'}")
    return 0 if manifest["summary"]["paper_ready"] == manifest["summary"]["total_samples"] else 2


def audit_dataset(dataset: Path) -> list[SampleAudit]:
    gt_by_id = collect_ground_truth(dataset)
    trace_by_id = collect_traces(dataset)
    case_by_id = collect_case_studies(dataset)
    all_ids = sorted(set(gt_by_id) | set(trace_by_id) | set(case_by_id))
    audits: list[SampleAudit] = []

    for sid in all_ids:
        gt_path = gt_by_id.get(sid)
        trace_dir = trace_by_id.get(sid)
        case_path = case_by_id.get(sid)
        category = infer_category(dataset, sid, gt_path, trace_dir)
        audit = SampleAudit(scenario_id=sid, category=category)

        if gt_path:
            audit.gt_relpath = rel(dataset, gt_path)
            audit.gt = summarize_gt(gt_path)
        else:
            audit.blocking_issues.append("missing_ground_truth")

        if trace_dir:
            audit.trace_relpath = rel(dataset, trace_dir)
            db_path = trace_dir / "responsibility.db"
            audit.db_relpath = rel(dataset, db_path) if db_path.exists() else None
            audit.metadata["trace_scenario_id"] = read_text(trace_dir / ".scenario_id")
            if audit.metadata["trace_scenario_id"] != sid:
                audit.blocking_issues.append("trace_scenario_id_mismatch")
            audit_db(dataset, db_path, audit)
        else:
            audit.blocking_issues.append("missing_trace")

        if case_path:
            audit.case_study_relpath = rel(dataset, case_path)
        else:
            audit.warnings.append("missing_case_study")

        validate_gt_contract(audit)
        validate_semantics(audit)
        assign_quality_tier(audit)
        audits.append(audit)

    return audits


def collect_ground_truth(dataset: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in sorted((dataset / "ground_truth").rglob("*.json")):
        if "_external_archive" in path.parts:
            continue
        sid = path.stem
        if sid in result:
            raise ValueError(f"duplicate ground truth id: {sid}")
        result[sid] = path
    return result


def collect_traces(dataset: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for sid_file in sorted((dataset / "traces").rglob(".scenario_id")):
        sid = sid_file.read_text(encoding="utf-8").strip()
        if sid in result:
            raise ValueError(f"duplicate trace id: {sid}")
        result[sid] = sid_file.parent
    return result


def collect_case_studies(dataset: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in sorted((dataset / "case_studies").glob("*.md")):
        if path.name == "README.md":
            continue
        name = path.stem
        sid = name.split("_", 1)[1] if "_" in name and name.split("_", 1)[0].isdigit() else name
        result[sid] = path
    return result


def summarize_gt(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "scenario_id": data.get("scenario_id"),
        "label": data.get("label"),
        "category": data.get("category"),
        "attack_type": data.get("attack_type"),
        "execution_status": data.get("execution_status") or data.get("result"),
        "result": data.get("result"),
        "expected_behavior": data.get("expected_behavior"),
        "evidence_contract": data.get("evidence_contract"),
        "expected_claims_count": len(data.get("expected_behavior", {}).get("expected_claims", []))
        if isinstance(data.get("expected_behavior"), dict)
        else 0,
        "expected_syscalls_count": len(data.get("expected_behavior", {}).get("expected_syscalls", []))
        if isinstance(data.get("expected_behavior"), dict)
        else 0,
        "metrics": data.get("metrics", {}),
        "has_expected_behavior": isinstance(data.get("expected_behavior"), dict),
        "has_evidence_contract": isinstance(data.get("evidence_contract"), dict),
        "has_alerts": isinstance(data.get("alerts"), list),
        "raw_keys": sorted(data.keys()),
    }


def audit_db(dataset: Path, db_path: Path, audit: SampleAudit) -> None:
    if not db_path.exists():
        audit.blocking_issues.append("missing_responsibility_db")
        return
    if db_path.stat().st_size <= 0:
        audit.blocking_issues.append("empty_responsibility_db")
        return

    con = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in con.execute("select name from sqlite_master where type='table'")
            if not row[0].startswith("sqlite_")
        }
        missing = sorted(set(CORE_TABLES) - tables)
        if missing:
            audit.blocking_issues.append(f"missing_core_tables:{','.join(missing)}")

        fk_errors = con.execute("pragma foreign_key_check").fetchall()
        if fk_errors:
            audit.blocking_issues.append(f"foreign_key_errors:{len(fk_errors)}")

        for table in sorted(tables & set(CORE_TABLES)):
            audit.db_counts[table] = con.execute(f"select count(*) from {table}").fetchone()[0]

        for table in CORE_NONEMPTY:
            if table in audit.db_counts and audit.db_counts[table] == 0:
                audit.blocking_issues.append(f"empty_core_table:{table}")

        if "runs" in tables:
            run_rows = con.execute("select * from runs").fetchall()
            audit.db_counts["runs"] = len(run_rows)
            if len(run_rows) != 1:
                audit.blocking_issues.append(f"runs_row_count:{len(run_rows)}")
            cols = [row[1] for row in con.execute("pragma table_info(runs)").fetchall()]
            audit.metadata["runs_columns"] = cols
            if "started_at_ns" not in cols:
                audit.blocking_issues.append("runs_missing_started_at_ns")
            if "scenario_id" in cols:
                scenario_idx = cols.index("scenario_id")
                scenario_value = run_rows[0][scenario_idx] if run_rows else None
                audit.metadata["runs_scenario_id"] = scenario_value
                if scenario_value not in (None, "", audit.scenario_id):
                    audit.blocking_issues.append("runs_scenario_id_mismatch")

        if "responsibility_alerts" in tables:
            for decision, count in con.execute(
                "select decision, count(*) from responsibility_alerts group by decision"
            ):
                audit.alert_decisions[str(decision)] = int(count)

        if "responsibility_claims" in tables:
            states = {
                str(state): int(count)
                for state, count in con.execute(
                    "select claim_state, count(*) from responsibility_claims group by claim_state"
                )
            }
            audit.metadata["claim_states"] = states
    finally:
        con.close()


def validate_gt_contract(audit: SampleAudit) -> None:
    if not audit.gt:
        return
    sid = audit.gt.get("scenario_id")
    if sid != audit.scenario_id:
        audit.blocking_issues.append("ground_truth_scenario_id_mismatch")

    missing_fields = [field for field in GT_REQUIRED_FIELDS if audit.gt.get(field) in (None, False)]
    if missing_fields:
        audit.blocking_issues.append(f"gt_contract_missing:{','.join(missing_fields)}")
        audit.actions_required.append("rewrite_ground_truth_contract")

    result = audit.gt.get("result")
    execution_status = audit.gt.get("execution_status")
    if result in {"ARCHIVED", "REFUSED"} and execution_status == result:
        audit.warnings.append("legacy_result_used_as_execution_status")


def validate_semantics(audit: SampleAudit) -> None:
    attack_type = audit.gt.get("attack_type")
    result = audit.gt.get("result")
    claims = audit.db_counts.get("responsibility_claims", 0)
    unsafe_or_uncertain = sum(audit.alert_decisions.get(k, 0) for k in DETECTING_DECISIONS)
    safe = audit.alert_decisions.get("Safe", 0)

    is_benign = attack_type == "benign" or audit.category == "benign"
    if is_benign and unsafe_or_uncertain:
        audit.blocking_issues.append("benign_has_detecting_alert")
    if is_benign and result not in {None, "CLEAN", "REFUSED", "BENIGN"}:
        audit.warnings.append("benign_gt_result_not_clean")

    if not is_benign and result == "DETECTED" and claims == 0 and unsafe_or_uncertain == 0:
        audit.blocking_issues.append("gt_detected_but_db_has_no_detection")
        audit.actions_required.append("relabel_or_rerun")

    if not is_benign and result == "ARCHIVED":
        audit.blocking_issues.append("archived_attack_lacks_paper_ready_semantics")
        audit.actions_required.append("decide_exclude_or_rerun")

    if safe and not unsafe_or_uncertain and not is_benign:
        audit.warnings.append("attack_sample_only_safe_alerts")

    if attack_type == "C":
        if audit.db_counts.get("agent_identities", 0) < 2:
            audit.blocking_issues.append("type_c_has_lt_two_agent_identities")
        if audit.db_counts.get("subagent_boundaries", 0) == 0:
            audit.warnings.append("type_c_has_no_subagent_boundaries")


def assign_quality_tier(audit: SampleAudit) -> None:
    if audit.blocking_issues:
        if any(issue.startswith("gt_contract_missing") for issue in audit.blocking_issues):
            audit.quality_tier = "needs_relabel"
        elif any("missing" in issue or "empty_core_table" in issue for issue in audit.blocking_issues):
            audit.quality_tier = "needs_rerun"
        else:
            audit.quality_tier = "analysis_only"
        return

    non_blocking_warnings = {
        "type_c_has_no_subagent_boundaries",
    }
    material_warnings = [
        warning for warning in audit.warnings if warning not in non_blocking_warnings
    ]
    if material_warnings:
        audit.quality_tier = "analysis_only"
    else:
        audit.quality_tier = "paper_ready"


def build_manifest(dataset: Path, audits: list[SampleAudit]) -> dict[str, Any]:
    by_tier: dict[str, int] = {}
    for audit in audits:
        by_tier[audit.quality_tier] = by_tier.get(audit.quality_tier, 0) + 1
    scenario_ids = [audit.scenario_id for audit in sorted(audits, key=lambda a: a.scenario_id)]
    return {
        "dataset": dataset.name,
        "schema": "mtagentlens.dataset_quality.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_samples": len(audits),
            "paper_ready": by_tier.get("paper_ready", 0),
            "analysis_only": by_tier.get("analysis_only", 0),
            "needs_relabel": by_tier.get("needs_relabel", 0),
            "needs_rerun": by_tier.get("needs_rerun", 0),
            "excluded": by_tier.get("excluded", 0),
            "by_tier": by_tier,
        },
        "scenario_ids": scenario_ids,
        "samples": [audit.to_json() for audit in sorted(audits, key=lambda a: a.scenario_id)],
    }


def build_metadata_v2_draft(dataset: Path, audits: list[SampleAudit], manifest: dict[str, Any]) -> dict[str, Any]:
    category_counts: dict[str, int] = {}
    label_counts: dict[str, int] = {}
    for audit in audits:
        category_counts[audit.category] = category_counts.get(audit.category, 0) + 1
        label = "benign" if audit.gt.get("attack_type") == "benign" or audit.category == "benign" else "attack"
        label_counts[label] = label_counts.get(label, 0) + 1

    return {
        "schema": "mtagentlens.dataset_metadata.v2.draft",
        "annotation_status": "draft_needs_human_review",
        "dataset": dataset.name,
        "scenario_ids": manifest["scenario_ids"],
        "quality_summary": manifest["summary"],
        "category_counts": dict(sorted(category_counts.items())),
        "label_counts": dict(sorted(label_counts.items())),
        "splits": {
            "main": [],
            "analysis_only": [
                audit.scenario_id
                for audit in sorted(audits, key=lambda a: a.scenario_id)
                if audit.quality_tier == "analysis_only"
            ],
            "needs_relabel": [
                audit.scenario_id
                for audit in sorted(audits, key=lambda a: a.scenario_id)
                if audit.quality_tier == "needs_relabel"
            ],
            "needs_rerun": [
                audit.scenario_id
                for audit in sorted(audits, key=lambda a: a.scenario_id)
                if audit.quality_tier == "needs_rerun"
            ],
            "excluded": [
                audit.scenario_id
                for audit in sorted(audits, key=lambda a: a.scenario_id)
                if audit.quality_tier == "excluded"
            ],
        },
        "notes": [
            "Draft generated by compare/scripts/dataset_quality_audit.py.",
            "Do not use main split until sample-level GT v2 files are manually reviewed.",
            "Safe/Info observational alerts must be filtered from attack detection metrics.",
        ],
    }


def render_report(dataset: Path, manifest: dict[str, Any], audits: list[SampleAudit]) -> str:
    summary = manifest["summary"]
    lines = [
        "# 冻结数据集质量审计报告",
        "",
        f"- 数据集：`{dataset}`",
        f"- 样本总数：{summary['total_samples']}",
        f"- paper_ready：{summary['paper_ready']}",
        f"- analysis_only：{summary['analysis_only']}",
        f"- needs_relabel：{summary['needs_relabel']}",
        f"- needs_rerun：{summary['needs_rerun']}",
        "",
    ]

    if summary["paper_ready"] != summary["total_samples"]:
        lines += [
            "## 总体结论",
            "",
            "当前冻结集不能直接进入论文主实验。必须先完成 GT 合同重写、样本重跑/排除判定、"
            "以及评估 adapter 对齐。",
            "",
        ]

    lines += ["## 阻断项分布", ""]
    issue_counts: dict[str, int] = {}
    for audit in audits:
        for issue in audit.blocking_issues:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1
    for issue, count in sorted(issue_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- `{issue}`：{count}")
    if not issue_counts:
        lines.append("- 无")

    lines += ["", "## 样本状态", "", "| scenario_id | category | tier | blockers | warnings |", "|---|---|---|---:|---:|"]
    for audit in sorted(audits, key=lambda a: (a.quality_tier, a.category, a.scenario_id)):
        lines.append(
            f"| `{audit.scenario_id}` | `{audit.category}` | `{audit.quality_tier}` | "
            f"{len(audit.blocking_issues)} | {len(audit.warnings)} |"
        )

    lines += [
        "",
        "## 下一步",
        "",
        "1. 以 `quality/manifest.json` 为唯一入口重建 compare adapter。",
        "2. 为所有 `needs_relabel` 样本补齐规范 GT 字段。",
        "3. 对 `needs_rerun` 样本按重跑清单重新采集，并保留 run provenance。",
        "4. 将 `analysis_only` 样本从主实验 split 中移出，除非完成人工复核和重标注。",
    ]
    return "\n".join(lines) + "\n"


def write_lists(out_dir: Path, audits: list[SampleAudit]) -> None:
    for tier in ("paper_ready", "analysis_only", "needs_relabel", "needs_rerun", "excluded"):
        ids = [a.scenario_id for a in sorted(audits, key=lambda x: x.scenario_id) if a.quality_tier == tier]
        (out_dir / f"{tier}.txt").write_text("\n".join(ids) + ("\n" if ids else ""), encoding="utf-8")


def write_action_queues(out_dir: Path, audits: list[SampleAudit]) -> None:
    queues = {
        "relabel_queue.jsonl": [
            audit
            for audit in audits
            if audit.quality_tier == "needs_relabel"
            or "rewrite_ground_truth_contract" in audit.actions_required
        ],
        "rerun_queue.jsonl": [
            audit
            for audit in audits
            if audit.quality_tier == "needs_rerun"
            or "relabel_or_rerun" in audit.actions_required
        ],
        "exclude_review_queue.jsonl": [
            audit
            for audit in audits
            if "decide_exclude_or_rerun" in audit.actions_required
            or audit.quality_tier == "excluded"
        ],
    }
    for filename, items in queues.items():
        lines = []
        for audit in sorted(items, key=lambda a: a.scenario_id):
            lines.append(
                json.dumps(
                    {
                        "scenario_id": audit.scenario_id,
                        "category": audit.category,
                        "quality_tier": audit.quality_tier,
                        "blocking_issues": audit.blocking_issues,
                        "warnings": audit.warnings,
                        "actions_required": audit.actions_required,
                        "ground_truth_draft": f"quality/ground_truth_v2_draft/{audit.scenario_id}.json",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        (out_dir / filename).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def write_gt_v2_drafts(dataset: Path, out_dir: Path, audits: list[SampleAudit]) -> None:
    draft_dir = out_dir / "ground_truth_v2_draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    for audit in audits:
        path = draft_dir / f"{audit.scenario_id}.json"
        write_json(path, build_gt_v2_draft(dataset, audit))


def build_gt_v2_draft(dataset: Path, audit: SampleAudit) -> dict[str, Any]:
    raw_gt = {}
    if audit.gt_relpath:
        raw_gt = json.loads((dataset / audit.gt_relpath).read_text(encoding="utf-8"))

    attack_type = audit.gt.get("attack_type")
    label = "benign" if attack_type == "benign" or audit.category == "benign" else "attack"
    result = audit.gt.get("result")
    execution_status = infer_execution_status(result)
    should_alert = label == "attack" and execution_status == "executed"
    detecting_alerts = sum(audit.alert_decisions.get(k, 0) for k in DETECTING_DECISIONS)
    claims = audit.db_counts.get("responsibility_claims", 0)

    return {
        "schema": "mtagentlens.ground_truth.v2.draft",
        "annotation_status": "draft_needs_human_review",
        "scenario_id": audit.scenario_id,
        "label": label,
        "category": normalize_category(audit.category),
        "attack_type": attack_type or infer_attack_type(audit.category),
        "execution_status": execution_status,
        "expected_behavior": {
            "should_alert": should_alert,
            "min_decision": "Unsafe" if should_alert else "Safe",
            "expected_claims": [],
            "expected_syscalls": [],
            "expected_boundaries": [],
            "notes": "Draft generated from legacy GT and DB summary; must be manually reviewed.",
        },
        "observed_behavior": {
            "db_claims": claims,
            "detecting_alerts": detecting_alerts,
            "safe_alerts": audit.alert_decisions.get("Safe", 0),
            "subagent_boundaries": audit.db_counts.get("subagent_boundaries", 0),
            "agent_identities": audit.db_counts.get("agent_identities", 0),
            "correlation_evidence": audit.db_counts.get("correlation_evidence", 0),
            "legacy_result": result,
            "legacy_metrics": audit.gt.get("metrics", {}),
        },
        "evidence_contract": {
            "agent_log_required": True,
            "db_required": True,
            "pcap_required": False,
            "trace_jsonl_required": False,
            "manual_execution_evidence_required": label == "attack",
        },
        "provenance": {
            "legacy_ground_truth": audit.gt_relpath,
            "responsibility_db": audit.db_relpath,
            "trace": audit.trace_relpath,
            "case_study": audit.case_study_relpath,
            "collector_version": raw_gt.get("collector_version"),
        },
        "quality": {
            "tier": audit.quality_tier,
            "blocking_issues": audit.blocking_issues,
            "warnings": audit.warnings,
            "actions_required": audit.actions_required,
        },
        "annotation": {
            "annotators": [],
            "adjudication": "pending",
            "notes": "",
        },
    }


def infer_execution_status(result: Any) -> str:
    if result == "REFUSED":
        return "refused"
    if result == "ARCHIVED":
        return "unknown"
    if result == "DETECTED":
        return "executed"
    if result == "CLEAN":
        return "executed"
    return "unknown"


def infer_attack_type(category: str) -> str:
    if category == "benign":
        return "benign"
    if category in {"agent_teams", "subagents", "multi_agent_attack"}:
        return "C"
    return "unknown"


def normalize_category(category: str) -> str:
    if category == "indirect":
        return "type_b_indirect"
    return category


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def infer_category(dataset: Path, sid: str, gt_path: Path | None, trace_dir: Path | None) -> str:
    if gt_path:
        return gt_path.parent.relative_to(dataset / "ground_truth").as_posix()
    if trace_dir:
        parent = trace_dir.parent.relative_to(dataset / "traces").as_posix()
        return parent
    return "unknown"


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    raise SystemExit(main())
