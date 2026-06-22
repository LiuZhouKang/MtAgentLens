#!/usr/bin/env python3
"""Freeze the 10 representative scenarios into a v1.0.0 dataset."""

import json, os, shutil, sqlite3
from pathlib import Path
from datetime import date
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from mtagentprism_benchmark.loader.scenario_loader import load_scenario
from mtagentprism_benchmark.workspace import BenchmarkWorkspace

def main():
    workspace = BenchmarkWorkspace.discover()
    runs_dir = workspace.runs_dir
    scenarios_dir = workspace.scenarios_dir
    # Use workspace-relative paths for portability
    gt_dir = workspace.project_root / "docs" / "evaluation" / "expected_alerts"
    output_dir = workspace.root / "frozen_dataset_v1.0.0"

    # ── 10 scenarios in order ──
    scenario_ids = [
        "shadow-agent",
        "prompt-injection",
        "prompt-injection-classical",
        "network-anomaly",
        "atlas-recon-discovery",
        "subagents-handoff-injection",
        "agent-teams-role-hijack",
        "exfil-env-secrets",
        "leader-betrayal",
        "consensus-attack",
    ]

    # ── Build run_results list ──
    run_results = []
    missing = []

    for sid in scenario_ids:
        # Find the run directory for this scenario
        matching = sorted([
            d for d in os.listdir(runs_dir)
            if d.endswith(f"-{sid}") and os.path.isdir(os.path.join(runs_dir, d))
        ])
        if not matching:
            missing.append(sid)
            print(f"  MISSING: {sid}")
            continue

        run_dir = os.path.join(runs_dir, matching[-1])

        # Load scenario config
        sc_path = os.path.join(scenarios_dir, f"{sid}.json")
        if not os.path.exists(sc_path):
            missing.append(sid)
            print(f"  MISSING scenario JSON: {sid}")
            continue

        try:
            config = load_scenario(Path(sc_path), workspace.fixtures_dir)
        except Exception as e:
            print(f"  LOAD ERROR {sid}: {e}")
            missing.append(sid)
            continue

        run_results.append((config, Path(run_dir)))
        print(f"  OK: {sid}")

    print(f"\n  Total: {len(run_results)} scenarios, {len(missing)} missing")

    if missing:
        print(f"  Missing: {missing}")
        return

    # ── Build frozen dataset ──
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    release_dir = output_dir
    (release_dir / "scenarios").mkdir()
    (release_dir / "traces").mkdir()
    (release_dir / "ground_truth").mkdir()
    (release_dir / "splits").mkdir()

    # ── Metadata ──
    metadata = {
        "name": "AgentSight Benchmark v1.0.0",
        "version": "1.0.0",
        "release_date": str(date.today()),
        "num_scenarios": len(run_results),
        "scenario_ids": [s.id for s, _ in run_results],
        "modes": {"single_agent": 0, "agent_teams": 0, "subagents": 0},
        "total_ground_truth_alerts": 0,
        "detection_coverage": {
            "file_boundary": "HOME-aware path whitelist + pattern matching",
            "network_boundary": "suspicious target detection + ExecArg extraction",
            "content_boundary": "injection/forgery/suspicious command/credential patterns",
            "delegation_boundary": "cross-agent orchestration tool detection",
        },
        "known_limitations": [
            "SSL_write buffer truncation for large request bodies (>256KB)",
            "Semantic/behavioral attacks without file/network footprint",
            "DNS pre-condition for network connect() detection",
        ],
    }

    # Count modes
    for config, _ in run_results:
        metadata["modes"][config.mode.value] = metadata["modes"].get(config.mode.value, 0) + 1

    # ── Copy scenarios ──
    for config, _ in run_results:
        sc_path = os.path.join(scenarios_dir, f"{config.id}.json")
        if os.path.exists(sc_path):
            shutil.copy2(sc_path, release_dir / "scenarios" / f"{config.id}.json")

    # ── Copy fixtures ──
    fixtures_src = workspace.fixtures_dir
    fixtures_dst = release_dir / "fixtures"
    if fixtures_src.exists():
        # Only copy fixtures for our 10 scenarios
        fixtures_dst.mkdir()
        for config, _ in run_results:
            fixture_dir = fixtures_src / config.id
            if fixture_dir.exists():
                dst = fixtures_dst / config.id
                shutil.copytree(fixture_dir, dst)

    # ── Copy ground truth ──
    gt_dst = release_dir / "ground_truth"
    total_gt = 0
    for config, _ in run_results:
        gt_path = gt_dir / f"{config.id}.json"
        if gt_path.exists():
            shutil.copy2(gt_path, gt_dst / f"{config.id}.json")
            gt_data = json.loads(gt_path.read_text())
            gt_alerts = gt_data.get("alerts", [])
            total_gt += len(gt_alerts)
    metadata["total_ground_truth_alerts"] = total_gt

    # ── Copy traces (compact DBs) ──
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from compact_db import compact_db

    for config, run_dir in run_results:
        trace_dst = release_dir / "traces" / config.id
        trace_dst.mkdir()

        # Find the DB and compact it
        collector_dir = run_dir / "collector"
        for db_name in ["trace.responsibility.db", "trace.log.responsibility.db"]:
            db_src = collector_dir / db_name
            if db_src.exists():
                compact_db(str(db_src), str(trace_dst / "responsibility.db"))
                break

        # Copy summary
        summary_src = run_dir / "summary.json"
        if summary_src.exists():
            shutil.copy2(summary_src, trace_dst / "summary.json")

        # Copy agent logs
        logs_src = run_dir / "logs"
        if logs_src.exists():
            logs_dst = trace_dst / "logs"
            shutil.copytree(logs_src, logs_dst)

    # ── Splits: train/val/test (60/20/20) ──
    ids = sorted([s.id for s, _ in run_results])
    n = len(ids)
    n_train = max(1, int(n * 0.6))
    n_val = max(1, int(n * 0.2))

    splits_dir = release_dir / "splits"
    (splits_dir / "train_scenarios.json").write_text(
        json.dumps(ids[:n_train], indent=2) + "\n")
    (splits_dir / "val_scenarios.json").write_text(
        json.dumps(ids[n_train:n_train + n_val], indent=2) + "\n")
    (splits_dir / "test_scenarios.json").write_text(
        json.dumps(ids[n_train + n_val:], indent=2) + "\n")

    # ── Metadata ──
    (release_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")

    # ── README ──
    readme = f"""# AgentSight Benchmark v1.0.0

首个冻结数据集，包含 {len(run_results)} 个最具代表性的 Agent 异常检测场景。

## 快速验证

```bash
cd /path/to/frozen_dataset_v1.0.0
for scenario_dir in traces/*/; do
    name=$(basename "$scenario_dir")
    db="$scenario_dir/responsibility.db"
    if [ -f "$db" ]; then
        claims=$(sqlite3 "$db" "SELECT COUNT(*) FROM responsibility_claims;")
        alerts=$(sqlite3 "$db" "SELECT COUNT(*) FROM responsibility_alerts;")
        echo "$name: $claims claims, $alerts alerts"
    fi
done
```

## 场景列表

| # | 场景 | 模式 |
|---|------|------|
"""
    for i, (config, _) in enumerate(run_results, 1):
        readme += f"| {i} | {config.id} | {config.mode.value} |\n"

    readme += f"""
## 目录结构

- `scenarios/` — 场景定义（JSON）
- `fixtures/` — 场景 fixture 文件
- `traces/` — 每个场景的 eBPF trace（SQLite DB + 日志）
- `ground_truth/` — 预期告警标注
- `splits/` — train/val/test 划分
- `metadata.json` — 数据集元信息

## 检测覆盖

- **文件边界**: HOME 感知路径白名单 + 模式匹配
- **网络边界**: 可疑目标检测 + ExecArg URL 提取
- **内容边界**: 注入/伪造/可疑命令/凭证模式
- **委托边界**: 跨 agent 编排工具调用检测

## 已知局限

1. SSL_write 缓冲区截断（>256KB 请求体）
2. 无文件/网络痕迹的语义/行为攻击
3. DNS 前置条件（connect() 需 DNS 解析成功）

## License

MIT
"""
    (release_dir / "README.md").write_text(readme)

    # ── Print summary ──
    print(f"\nFrozen dataset: {release_dir}")
    print(f"  Scenarios: {len(ids)}")
    print(f"  Ground truth alerts: {total_gt}")
    print(f"  Splits: train={len(ids[:n_train])} val={len(ids[n_train:n_train+n_val])} test={len(ids[n_train+n_val:])}")

    # Size
    total_size = sum(f.stat().st_size for f in release_dir.rglob('*') if f.is_file())
    print(f"  Total size: {total_size / 1024 / 1024:.1f} MB")

if __name__ == "__main__":
    main()
