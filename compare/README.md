# MtAgentLens 实验交付包（compare/）

> 打包日期：2026-10-30  
> 范围：T4 检测、T5 身份、T6 归因、T7 消融、T7-S 敏感性、T7 PBC、T8 开销、外部基线  
> 原则：**原始数据随实验目录走；文档、指标、图、表、复现脚本全部在包内；不依赖 `local/` 才能读结果。**

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
    dataset/        # data contract
    datasets/       # dataset card copies
    SOURCE-NOTES.md
```

## 3. 每个实验的交付结构

- `README.md`：结论、方法、限制；
- `MANIFEST.md`：权威文件、原始数据、复现命令；
- `data/`：原始/派生数据 + `data/README.md`；
- `metrics/`、`tables/`、`figures/`、`reproduce/`（按实验性质可缺项）。

## 4. 数据边界

- **原始数据已随包**：T7 93 exports、T7-S 93 exports + 3 源文件、T5 30 JSON + 5 CSV、T8 2952 raw files、T6 93 JSON、T4 metrics/ledger、PBC 4 文件、external 5 文件。
- `local/final-v1/` 的完整运行现场**不打包**；需要重建/审计时使用仓库内 `local/` 与 `docs/evaluation/REPRODUCE.md`。
- `docs/` 中出现的 `local/...` 是历史现场命令，不是包内相对路径；包内数据路径以各实验 `data/README.md` 为准。

## 5. 打包

```bash
cd /home/lzk/MtAgentLens
./compare/package.sh
```

`package.sh` 默认排除 `.venv-*`、`models/`、`Dataset/`、`__pycache__`，只打包实验与文档；输出 `compare-handoff-2026-10-30.tar.gz`。  
包内不包含凭据、`env/config`、`local/`、运行数据库与未 promote 的中间结果。

## 6. 复现

脚本仍以仓库根为工作目录（需要 `env/` 与 `collector/`）；纯数据分析可直接读各实验 `data/` 与 `metrics/`。

```bash
cd /home/lzk/MtAgentLens
PYTHONPATH="." python compare/results/ablation/reproduce/build_metrics.py
# 其他实验命令见各自 MANIFEST.md
```
