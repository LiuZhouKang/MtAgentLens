# MtAgentLens 实验交付包（compare/）

> 更新日期：2026-10-31
>
> 范围：T4 检测、T5 身份、T6 归因、T7 消融、T7-S 敏感性、T7 PBC、T8 开销、外部基线
>
> 原则：**原始数据随实验目录走；文档、指标、图、表和 `results/*/reproduce` 脚本随包；不依赖 `local/` 才能读结果。**

## 1. 先看哪里

| 目的 | 入口 |
|---|---|
| 实验总索引 | [`results/README.md`](results/README.md) |
| 当前状态/论文证据 | [`docs/evaluation/CURRENT-EVIDENCE.md`](docs/evaluation/CURRENT-EVIDENCE.md) |
| 审稿回复 | [`docs/paper/REVIEW-RESPONSE-EVIDENCE-2026-10-26.md`](docs/paper/REVIEW-RESPONSE-EVIDENCE-2026-10-26.md) |
| 复现命令 | [`docs/evaluation/REPRODUCE.md`](docs/evaluation/REPRODUCE.md) |
| 包清单 | [`MANIFEST.md`](MANIFEST.md) |

## 2. 目录

```
compare/
  README.md
  MANIFEST.md
  package.sh
  results/
    README.md
    detection/      # T4 + raw metrics/ledger
    ablation/       # T7 + 93 exports
    sensitivity/    # T7-S + 93 exports + derived grid/LHS
    identity/       # T5 + 30 raw JSON + 5 CSV
    overhead/       # T8 + 2952 raw files + CSV
    attribution/    # T6 + 93 raw JSON
    pbc/            # T7 PBC
    external/       # external baselines
  docs/
    README.md
    evaluation/     # protocol/evidence/reproduce + current index
    paper/          # review response, revision notes, claims
    SOURCE-NOTES.md
  datasets/         # 数据契约、数据卡和选定文档副本
  scripts/          # 采集、构建、审计、外部基线复跑
    run/            # pilot_observer.py、app_baseline.py、app_judge.py
    external/       # InjecAgent 外部基线复跑
    build/ audit/ dataset/ eval/ ablation/ perf/ vis/
  tools/            # 离线工具对比框架与 adapters
  rjudge/           # R-Judge 协议、judge、cache、trace export
  tests/            # compare/ 契约与回归测试
  tool-lock.json
  tool-lock.prompt.json
  run_tool_comparison.py
  requirements-test.txt
```

`Dataset/`、`models/`、`.venv-*` 是工作区复跑依赖，保留在仓库中但不进入交付包。`Dataset/` 目前只保留 `external/InjecAgent/` 输入（随 Git 跟踪），用于复跑外部基线。

## 3. 每个实验的交付结构

- `README.md`：结论、方法、限制；
- `MANIFEST.md`：权威文件、原始数据、复现命令；
- `data/`：原始/派生数据 + `data/README.md`；
- `metrics/`、`tables/`、`figures/`、`reproduce/`（按实验性质可缺项）。

## 4. 数据边界

- **原始数据已随包**：T7 93 exports、T7-S 93 exports + 3 源文件、T5 30 JSON + 5 CSV、T8 2952 raw files、T6 93 JSON、T4 metrics/ledger、PBC 4 文件、external 5 文件。
- `local/final-v1/` 的完整运行现场**不打包**；需要重建/审计时使用仓库内 `local/` 与 `docs/evaluation/REPRODUCE.md`。
- `docs/` 中出现的 `local/...` 是历史现场命令，不是包内相对路径；包内数据路径以各实验 `data/README.md` 为准。
- App 基线的 `results/external/baselines/app/*/records/` 与 `traces/` 可能包含运行时凭据，仅保留在本地现场，打包时排除。其他 raw 文件未经逐项敏感信息审查，排除规则不能证明交付内容绝无秘密。
- InjecAgent 输入保留在 `compare/Dataset/external/InjecAgent/`，冻结输出在 `compare/results/external/InjecAgent/`；输入不进入交付包。

## 5. 打包

```bash
cd /home/lzk/MtAgentLens
./compare/package.sh
```

`package.sh` 只收录 `README.md`、`MANIFEST.md`、`results/`、`docs/`、`datasets/` 与 `package.sh`；任一必要条目缺失即失败。默认输出仓库根目录的 `compare-handoff-2026-10-31.tar.gz`，可用首个参数指定其他输出文件；输出必须位于 `compare/` 之外。

打包排除虚拟环境、模型、`Dataset/`、Python 缓存、App 基线原始 records/traces、`*.db` 及其 WAL/SHM 和 `*.log`。`env/config`、`local/`、顶层工具、脚本及 `compare/` 根目录的 `实验.zip` 等下载或交付归档不在收录范围；结果目录内部的证据归档（如 `results/overhead/archives/`）保留。`results/`、`docs/` 与 `datasets/` 中其余文件仍会收录，交付前需核对清单和敏感信息。脚本不会删除任何输入原件。

## 6. 复现与工具入口

Git 仓库维护实验入口和复现代码；完整数据与图表随实验交付包提供。仅克隆源码后，需先取得对应实验的 `data/` 和 `metrics/`，再运行下列命令。

代码复现需要完整仓库，并以仓库根为工作目录（需要 `env/`、`collector/` 及相关脚本）；本包不能独立运行全部复现流程。纯数据分析可直接读各实验 `data/` 与 `metrics/`。

```bash
cd /home/lzk/MtAgentLens
PYTHONPATH="." python compare/results/ablation/reproduce/build_metrics.py
# 其他实验命令见各自 MANIFEST.md
```

离线工具对比：

```bash
cd /home/lzk/MtAgentLens
PYTHONPATH="." python compare/run_tool_comparison.py --dataset FuxiBench --tools all
```

输出写入 `compare/results/tool-comparison/`，属于临时对比产物，不是论文冻结证据。

## 7. 脚本入口分类

| 分类 | 入口 | 使用边界 |
|---|---|---|
| 冻结结果复算 | `results/*/reproduce/` | 当前交付数据，需完整仓库及对应输入 |
| 采集与外部基线 | `scripts/run/pilot_observer.py`、`app_baseline.py`、`app_judge.py`、`scripts/external/` | 由 `../scripts/evaluation/final_campaign_harness.py` 或外部基线协议调用 |
| 离线工具对比 | `run_tool_comparison.py`、`tools/`、`tool-lock*.json` | 当前 runner 支持未绑定数据集身份；输出不是冻结证据 |
| 契约维护保留 | `scripts/eval/standard_evaluation.py`、`scripts/build/build_paper_evaluation_report.py` | 仍有契约测试消费者，不等于当前实验数字入口 |
| 已明确历史 | `scripts/evaluate_mtagentlens.py`、`ablation/deberta_offline.py`、`perf/legacy/` | 历史模型、分数或性能方法，不用于当前结论 |
| 旧论文工具链 | `scripts/ablation/run_staircase.py`、`scripts/run/run_all_experiments.py` | 尚有导入或静态测试消费者；旧路径与方法不代表当前流水线 |
