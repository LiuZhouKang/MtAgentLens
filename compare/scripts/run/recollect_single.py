#!/usr/bin/env python3
"""历史冻结数据库审查与增量评估对比；不再自动采集或替换冻结库。

用法:
  # 交互模式（每个场景审查后人工确认继续）
  python3 compare/scripts/run/recollect_single.py --scenario atlas-recon-discovery

  # 批量模式（自动模式，遇到问题暂停）
  python3 compare/scripts/run/recollect_single.py --all
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
TRACES_DIR = ROOT / "compare" / "Dataset" / "FuxiBench" / "traces"
MANIFEST_PATH = ROOT / "compare" / "Dataset" / "FuxiBench" / "prompt_inputs" / "manifest.json"
BASELINE_PATH = ROOT / "compare" / "results" / "paper" / "main_evaluation.json"
LOG_DIR = ROOT / "compare" / "results" / "recollection_logs"

# ── 加载场景清单 ──


def load_manifest() -> list[dict]:
    print("retired: recollect_single.py is historical; use datasets/FuxiBench and the current pipeline")
    raise SystemExit(2)
    manifest = json.loads(MANIFEST_PATH.read_text())
    return manifest["entries"]


# ── 数据库质量审查 ──


def audit_database(db_path: Path, scenario: dict) -> dict[str, Any]:
    """对单个 responsibility.db 进行逐表质量审查，返回详细报告。"""
    report = {
        "scenario_id": scenario["scenario_id"],
        "attack_type": scenario.get("attack_type", "?"),
        "db_path": str(db_path),
        "db_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "integrity": None,
        "tables": {},
        "issues": [],
        "warnings": [],
        "pass": False,
    }

    if not db_path.exists():
        report["issues"].append("DB file does not exist")
        return report

    try:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
    except Exception as e:
        report["issues"].append(f"Cannot open DB: {e}")
        return report

    # ── 完整性检查 ──
    integrity = con.execute("PRAGMA integrity_check").fetchone()
    report["integrity"] = str(integrity[0])
    if report["integrity"].lower() != "ok":
        report["issues"].append(f"integrity_check FAILED: {report['integrity']}")
        con.close()
        return report

    fk_check = con.execute("PRAGMA foreign_key_check").fetchone()
    if fk_check is not None:
        report["issues"].append(f"foreign_key_check VIOLATION: {fk_check}")

    # ── 逐表审查 ──
    table_checks = {
        # (table, critical_empty_ok_if_benign)
        "runs": {
            "min": 1,
            "max": 1,
            "check": "runs 表必须恰好 1 行",
        },
        "process_events": {
            "min": 1,
            "check": "至少 1 个进程事件（Agent 启动证据）",
        },
        "tls_sessions": {
            "min": 0,  # 可能没有 HTTPS 流量
            "check": "TLS 会话数（如果 Agent 有 HTTP API 调用则应 > 0）",
        },
        "intents": {
            "min": 1,
            "check": "至少 1 个 intent（Agent 工具调用意图）",
        },
        "actions": {
            "min": 1,
            "check": "至少 1 个 action（OS-level 动作观测）",
        },
        "correlation_evidence": {
            "min": 0,
            "check": "intent-action 关联证据",
        },
        "agent_identities": {
            "min": 1,
            "check": "至少 1 个 Agent 身份",
        },
        "graph_nodes": {
            "min": 1,
            "check": "图节点",
        },
        "graph_edges": {
            "min": 0,
            "check": "图边（PBC 边在攻击场景中应 > 0）",
        },
        "responsibility_alerts": {
            "min": 0,
            "check": "告警（攻击场景应 > 0，良性可为 0）",
        },
        "responsibility_claims": {
            "min": 0,
            "check": "责任声明",
        },
    }

    for table, spec in table_checks.items():
        try:
            count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            report["tables"][table] = count

            min_val = spec.get("min", 0)
            if count < min_val:
                is_attack = scenario.get("attack_type") not in ("benign", None)
                if table == "responsibility_alerts" and not is_attack:
                    # 良性场景无告警是正常的
                    pass
                elif table == "graph_edges" and not is_attack:
                    pass
                elif table == "tls_sessions":
                    report["warnings"].append(
                        f"tls_sessions=0 — Agent 无 HTTPS 流量（可能被 LLM 拒绝或纯本地执行）"
                    )
                else:
                    report["issues"].append(
                        f"{table}.count={count} < min={min_val}: {spec['check']}"
                    )

            if "max" in spec and count > spec["max"]:
                report["issues"].append(
                    f"{table}.count={count} > max={spec['max']}: {spec['check']}"
                )
        except Exception as e:
            report["issues"].append(f"Cannot query {table}: {e}")

    # ── 数据合理性检查 ──
    # 1. runs 中有 started_at_ns
    try:
        row = con.execute("SELECT started_at_ns, ended_at_ns FROM runs LIMIT 1").fetchone()
        if row and row["started_at_ns"] is None:
            report["issues"].append("runs.started_at_ns is NULL")
        if row and row["ended_at_ns"] is None:
            report["warnings"].append("runs.ended_at_ns is NULL（collector 未正常关闭?）")
        if row and row["started_at_ns"] and row["ended_at_ns"]:
            duration_s = (row["ended_at_ns"] - row["started_at_ns"]) / 1e9
            report["duration_seconds"] = round(duration_s, 1)
    except Exception:
        pass

    # 2. PBC 边（攻击场景的核心信号）
    try:
        pbc = con.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE edge_type='PolicyBoundaryCrossing'"
        ).fetchone()[0]
        report["pbc_edges"] = pbc
    except Exception:
        pass

    # 3. Agent 数 vs 图节点类型
    try:
        agent_nodes = con.execute(
            "SELECT COUNT(*) FROM graph_nodes WHERE node_type='Agent'"
        ).fetchone()[0]
        report["agent_nodes"] = agent_nodes
    except Exception:
        pass

    # 4. 检查是否有异常的时间戳（未来或太早）
    try:
        now_ns = int(time.time() * 1e9)
        future = con.execute(
            "SELECT COUNT(*) FROM intents WHERE timestamp_ns > ?",
            (now_ns + 86400_000_000_000,),  # 未来超过1天
        ).fetchone()[0]
        if future > 0:
            report["issues"].append(f"timestamps in the future: {future} intents")
    except Exception:
        pass

    con.close()

    # ── 判定 ──
    report["pass"] = len(report["issues"]) == 0
    return report


# ── 与旧基线对比 ──


def compare_to_baseline(
    scenario_id: str, new_counts: dict[str, int]
) -> dict[str, Any]:
    """将新采集的信号计数与旧基线进行对比。"""
    comparison: dict[str, Any] = {
        "scenario_id": scenario_id,
        "baseline_available": False,
        "diffs": {},
        "explanation": "",
    }

    if not BASELINE_PATH.exists():
        comparison["explanation"] = "基线文件不存在，跳过对比"
        return comparison

    baseline = json.loads(BASELINE_PATH.read_text())
    old = None
    for s in baseline.get("per_scenario", []):
        if s["sid"] == scenario_id:
            old = s
            break

    if old is None:
        comparison["explanation"] = "旧基线中无此场景"
        return comparison

    comparison["baseline_available"] = True

    metric_map = {
        "pbc": "pbc",
        "edges": "edges",
        "nodes": "nodes",
        "evidence": "evidence",
        "agents": "agents",
        "tainted_claims": "tainted_claims",
        "pollution": "pollution",
    }

    significant_changes = []
    for old_key, new_key in metric_map.items():
        old_val = old.get(old_key, 0)
        new_val = new_counts.get(new_key, 0)

        if old_val == 0 and new_val == 0:
            diff_pct = 0
        elif old_val == 0:
            diff_pct = 100
        else:
            diff_pct = abs(new_val - old_val) / old_val * 100

        comparison["diffs"][old_key] = {
            "old": old_val,
            "new": new_val,
            "delta": new_val - old_val,
            "delta_pct": round(diff_pct, 1),
        }

        if diff_pct > 50 and max(old_val, new_val) > 3:
            significant_changes.append(
                f"{old_key}: {old_val} → {new_val} ({diff_pct:.0f}%)"
            )

    comparison["significant_changes"] = significant_changes
    if significant_changes:
        comparison["explanation"] = (
            f"显著偏离: {'; '.join(significant_changes)}. "
            "需要人工审查是否合理。"
        )
    else:
        comparison["explanation"] = "对比正常，无显著偏离"

    return comparison


# ── 增量评估 ──


def run_incremental_evaluation(scenario_ids: list[str]) -> dict:
    """在已有 db 上运行评估脚本，产出增量指标。"""
    eval_script = ROOT / "compare" / "scripts" / "evaluate_mtagentlens.py"

    env = os.environ.copy()
    env["PYTHONPATH"] = f"env:env/src:."

    result = subprocess.run(
        ["python3", str(eval_script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    if result.returncode != 0:
        return {"error": result.stderr[-500:]}

    try:
        data = json.loads(
            (ROOT / "compare" / "results" / "paper" / "main_evaluation.json").read_text()
        )
        return {
            "scenarios_evaluated": data["total_scenarios"],
            "TPR": data["full_metrics"]["TPR"],
            "FPR": data["full_metrics"]["FPR"],
            "F1": data["full_metrics"]["F1"],
            "MCC": data["full_metrics"]["MCC"],
        }
    except Exception as e:
        return {"error": str(e)}


# ── 主流程：跑单个场景 ──


def run_one_scenario(scenario: dict, skip_if_exists: bool = True) -> dict[str, Any]:
    """审查已有冻结库；需要新采集时明确拒绝隐式替换。"""
    sid = scenario["scenario_id"]
    category = scenario.get("category", "")
    attack_type = scenario.get("attack_type", "?")

    print(f"\n{'='*70}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 场景: {sid}")
    print(f"  类型: {attack_type}  类别: {category}  模式: {scenario.get('mode','?')}")
    print(f"{'='*70}")

    # ── 检查是否已存在 ──
    if category:
        dest_dir = TRACES_DIR / category / sid
    else:
        dest_dir = TRACES_DIR / sid
    existing_db = dest_dir / "responsibility.db"

    if skip_if_exists and existing_db.exists():
        try:
            quick = sqlite3.connect(str(existing_db))
            ok = quick.execute("PRAGMA integrity_check").fetchone()
            quick.close()
            if str(ok[0]).lower() == "ok":
                print(f"  ⏭  已存在健康 db，跳过: {existing_db}")
                # 仍然做审查和对比
                report = audit_database(existing_db, scenario)
                comparison = compare_to_baseline(sid, report["tables"])
                return {"scenario": sid, "report": report, "comparison": comparison, "skipped": True}
        except Exception:
            print("  已有 db 无法通过完整性检查")

    # run_one now retains an audited candidate in its run directory. Reading
    # existing_db after launching it would falsely label old data as new.
    message = "Automatic frozen-trace replacement is retired. Run env/run_one.py for a candidate; publish through a separate release workflow."
    print(message)
    return {"scenario": sid, "error": message, "pass": False}


# ── CLI ──

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="历史冻结库审查（自动采集已退役）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--scenario", help="审查单个历史场景")
    g.add_argument("--all", action="store_true", help="批量审查历史场景")
    g.add_argument("--list", action="store_true", help="列出所有场景")
    p.add_argument("--no-skip", action="store_true", help="保留的旧参数；自动采集已退役，使用此项将返回失败")
    p.add_argument("--incremental-eval", action="store_true", help="每采集完一个场景后立即运行增量评估")

    args = p.parse_args()
    manifest = load_manifest()

    if args.list:
        for e in manifest:
            print(f"{e['scenario_id']} [{e['attack_type']}] {e['category']}")
        sys.exit(0)

    scenarios = [
        e for e in manifest
        if not args.scenario or e["scenario_id"] == args.scenario
    ]

    if not scenarios:
        print(f"场景未找到: {args.scenario}")
        sys.exit(1)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    for i, scenario in enumerate(scenarios):
        result = run_one_scenario(scenario, skip_if_exists=not args.no_skip)
        results.append(result)

        # 保存逐场景报告
        report_path = LOG_DIR / f"{scenario['scenario_id']}.json"
        report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))

        # 增量评估
        if args.incremental_eval and result.get("pass"):
            print(f"\n  📊 运行增量评估...")
            collected = [r["scenario"] for r in results if r.get("pass")]
            eval_result = run_incremental_evaluation(collected)
            if "error" not in eval_result:
                print(f"  当前指标 (n={eval_result['scenarios_evaluated']}):")
                print(f"    TPR={eval_result['TPR']:.4f}  FPR={eval_result['FPR']:.4f}")
                print(f"    F1={eval_result['F1']:.4f}  MCC={eval_result['MCC']:.4f}")

        # 失败时停下来人工审查
        if not result.get("pass") and not args.scenario:
            print(f"\n❌ {scenario['scenario_id']} 失败，暂停等待人工审查。")
            print(f"   继续下一个场景请重新运行: --scenario {scenario['scenario_id']}")
            cont = input("  输入 'c' 继续下一个场景，其他任意键退出: ")
            if cont.lower() != "c":
                break

    # 最终汇总
    print(f"\n{'='*70}")
    print(f"汇总: {len([r for r in results if r.get('pass')])}/{len(results)} 通过")
    passed = [r for r in results if r.get("pass")]
    failed = [r for r in results if not r.get("pass") and not r.get("skipped")]
    skipped = [r for r in results if r.get("skipped")]

    print(f"  通过: {len(passed)}")
    print(f"  失败: {len(failed)}")
    print(f"  跳过（已有健康 db）: {len(skipped)}")

    if failed:
        print(f"  失败场景: {[r['scenario'] for r in failed]}")
    sys.exit(0 if all(
        result.get("pass", result.get("report", {}).get("pass", False))
        for result in results
    ) else 1)
