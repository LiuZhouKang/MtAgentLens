# compare/ — MtAgentPrism 论文实验与对比评估

MtAgentPrism 在 FuxiBench（伏羲基准）86 场景上的完整实验包。

## 目录

```
compare/
├── README.md
├── tool-lock.json                   ← 静态工具锁
├── tool-lock.prompt.json            ← Prompt 基线工具锁
├── run_tool_comparison.py           ← 工具对比入口
│
├── Dataset/                         ← 数据集
│   ├── FuxiBench/                   ← Frozen 基准（86 评估场景）
│   │   ├── SCENARIOS.md             ← 完整场景目录
│   │   ├── README.md
│   │   ├── QUALITY_STANDARD.md
│   │   ├── metadata.json
│   │   ├── ground_truth/            ← 86 个 GT JSON
│   │   ├── prompt_inputs/
│   │   └── traces/
│   └── external/                    ← 外部数据集
│       └── InjecAgent/
│
├── scripts/
│   ├── build/          ← 构建脚本（评估报告、消融结果、投稿包等）
│   ├── audit/          ← 审计脚本（就绪检查、投稿审计、trace 脱敏）
│   ├── run/            ← 实验运行脚本（InjecAgent、LLM Guard、标注研究等）
│   ├── vis/            ← 可视化（论文表格、Canonical 图、性能图）
│   ├── eval/           ← 评估脚本
│   ├── perf/           ← 性能基准（v2 版本为当前，legacy/ 为旧版）
│   ├── dataset/        ← 数据集管理（质量审计、GT 生成等）
│   └── ablation/       ← 消融实验（DeBERTa、Platt 调参、阶梯实验）
│
├── tools/              ← 对比工具适配器
│   ├── base.py, registry.py, runner.py, evaluator.py, datasets.py
│   └── adapters/       ← 各工具适配器
│
├── tests/              ← 测试套件
├── results/
│   ├── paper/          ← 论文核心结果 + EXPERIMENTS.md + tables/
│   ├── perf/           ← 性能基准结果 + charts/ + tables/
│   └── external/       ← 外部实验（InjecAgent + prompt_baselines）
│
├── models/             ← 本地模型（gitignored，3.1 GB）
├── .venv-llm-guard/    ← Python 虚拟环境（gitignored）
├── .venv-prompt-guard/ ← Python 虚拟环境（gitignored）
└── .venv-nemo-guardrails/ ← Python 虚拟环境（gitignored）
```

## 常用命令

```bash
# 就绪审计
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=env:env/src:. \
  python3 compare/scripts/audit/audit_paper_experiment_readiness.py --allow-blockers --json

# 全部测试
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=env:env/src:. \
  python3 -m pytest compare/tests/ -q

# 生成论文表格
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  python3 compare/scripts/vis/nature_tables.py

# 生成论文图
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  python3 compare/scripts/vis/generate_canonical_figures.py

# 生成性能图
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  python3 compare/scripts/vis/generate_perf_figures.py

# 构建性能结果
MTAGENTPRISM_SUDO_PASSWORD=<password> PYTHONDONTWRITEBYTECODE=1 \
  python3 compare/scripts/perf/build_performance_results.py
```

## 关键入口

| 想做什么 | 去这里 |
|---|---|
| 阅读实验章节 | `results/paper/EXPERIMENTS.md` |
| 查看完整场景目录 | `Dataset/FuxiBench/SCENARIOS.md` |
| 查看主评估结果 | `results/paper/main_evaluation.json` |
| 查看消融结果 | `results/paper/ablation_results.json` |
| 查看 InjecAgent 结果 | `results/external/InjecAgent/results.json` |
| 查看性能结果 | `results/perf/perf_report.json` |
