# 实验结果交付目录（Handoff）

> 更新：2026-10-30。这里只放**当前实验**；历史 `paper/`、`p12-recollect/` 等已移入 `local/final-v1/archive/compare-legacy-2026-10-30/`。

## 当前实验

| 目录 | 实验 | 状态 | 原始数据 | 主入口 |
|---|---|---|---|---|
| `detection/` | T4 检测（93 场景） | final cohort | `data/`（metrics + promoted ledger） | [README](detection/README.md) · [MANIFEST](detection/MANIFEST.md) |
| `ablation/` | T7 消融（ranking + operating point） | estimable | `data/exports/`（93 JSON） | [README](ablation/README.md) · [MANIFEST](ablation/MANIFEST.md) |
| `sensitivity/` | T7-S 参数敏感性 | estimable | `data/raw/`（93 exports + 3 源文件） | [README](sensitivity/README.md) · [MANIFEST](sensitivity/MANIFEST.md) |
| `identity/` | T5 IdentityBench | estimable | `data/`（30 JSON + 5 CSV） | [README](identity/README.md) · [MANIFEST](identity/MANIFEST.md) |
| `overhead/` | T8 开销 | estimable | `raw/`（2952 文件）+ `data/`（18 CSV/JSON） | [README](overhead/README.md) · [MANIFEST](overhead/MANIFEST.md) |
| `attribution/` | T6 归因 | not_estimable | `data/`（93 JSON） | [README](attribution/README.md) · [MANIFEST](attribution/MANIFEST.md) |
| `pbc/` | T7 PBC 分布/活动量基线 | estimable（supplementary） | `data/`（4 文件） | [README](pbc/README.md) · [MANIFEST](pbc/MANIFEST.md) |
| `external/` | 外部基线 | comparator | `data/`（comparison + InjecAgent） | [baselines/README](external/baselines/README.md) · [MANIFEST](external/MANIFEST.md) |

## Git 与实验交付包

Git 维护本页、实验 README/MANIFEST、数据说明与复现代码；大体积原始数据、派生指标和图表按现有本地产物策略保留在实验交付包中。仅克隆源码不会自动取得这些文件，查看报告图表或重算指标前需同时取得对应实验交付数据。不要用 `git add -f` 整树纳入运行现场。

## 统一交接结构

每个实验目录推荐包含（`not_estimable` 或纯基线目录可缺项，并在 MANIFEST 说明）：

```
README.md      # 结论、方法、限制
MANIFEST.md    # 交付清单、原始数据、复现命令
plan.json      # 设计（有则保留）
data/          # 原始/派生数据（含 data/README.md）
metrics/       # 机器可读指标
tables/        # 论文表
figures/       # 论文图（png/pdf/svg）
reproduce/     # 复现脚本
```

## 原始数据规则

- `data/` 中的文件是**可独立分析的原始/派生数据**；其他人不需要访问 `local/` 才能查看主结果。
- 每次交付改动数据时，更新对应的 `data/README.md` 与 `MANIFEST.md`。
- `local/` 仍保留完整实验现场；`compare/results/` 是论文/审稿可引用的 handoff 层。

## 状态总入口

[`docs/evaluation/CURRENT-EVIDENCE.md`](../docs/evaluation/CURRENT-EVIDENCE.md)
