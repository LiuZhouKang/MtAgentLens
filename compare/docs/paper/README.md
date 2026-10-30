# 论文工作区

> 实验状态与权威结果路径见 [`docs/evaluation/CURRENT-EVIDENCE.md`](../evaluation/CURRENT-EVIDENCE.md)；论文主张与证据对应关系见 [`claim-evidence-matrix.md`](claim-evidence-matrix.md)。本目录只放论文写作、审稿回复和修改材料。

## 1. 审稿与回复

| 文件 | 用途 |
|---|---|
| [`审稿意见.md`](审稿意见.md) / [`审稿意见.txt`](审稿意见.txt) / [`审稿意见.xlsx`](审稿意见.xlsx) | 原始审稿意见 |
| [`REVIEW-RESPONSE-EVIDENCE-2026-10-26.md`](REVIEW-RESPONSE-EVIDENCE-2026-10-26.md) | 逐条审稿意见 → 当前证据 → 可写口径 |
| [`response-letter-notes.md`](response-letter-notes.md) | 回复信写作笔记 |
| [`修改说明-草稿.md`](修改说明-草稿.md) | 论文修改说明草稿；引用前先核对当前证据页 |

## 2. 稿件

| 文件 | 说明 |
|---|---|
| [`软件学报初稿.doc`](软件学报初稿.doc) / [`软件学报初稿.pdf`](软件学报初稿.pdf) | 软件学报初稿 |
| [`thy软件学报24最终稿.doc`](thy软件学报24最终稿.doc) | 历史最终稿候选；以当前修改说明为准 |
| [`revision-package.md`](revision-package.md) | 正文替换稿/表图落点 |
| [`decision-procedure.md`](decision-procedure.md) | 判定流程说明 |

## 3. 数字与主张

| 文件 | 用途 |
|---|---|
| [`claim-evidence-matrix.md`](claim-evidence-matrix.md) | 主张-证据矩阵（记录可用主张及其证据） |
| [`claim-evidence-matrix-archive.md`](claim-evidence-matrix-archive.md) | 历史矩阵（不得引用） |
| [`claims.json`](claims.json) | 机器可校验数字层 |
| [`数字审计-2026-10-25.md`](数字审计-2026-10-25.md) | 初稿数字审计 |
| [`数字修正-2026-10-25.md`](数字修正-2026-10-25.md) | 数字替换记录 |

## 4. 图表与实验

- 主实验结果：`compare/results/{ablation,sensitivity,overhead,identity}/`
- 论文图表：`local/final-v1/paper/` 与各实验目录的 `figures/`、`tables/`
- 参数敏感性 Figure 8：[`compare/results/sensitivity/figures/fig8_sensitivity.png`](../../results/sensitivity/figures/fig8_sensitivity.png)
- T7 消融 Figure 7：[`compare/results/ablation/figures/fig7_ablation.png`](../../results/ablation/figures/fig7_ablation.png)

## 5. 历史材料

- [`claim-evidence-matrix-archive.md`](claim-evidence-matrix-archive.md)
- [`根因分析.md`](根因分析.md)、[`根因指标定义.md`](根因指标定义.md)
- [`annotation-protocol.md`](annotation-protocol.md)
- AI/CODEX 交接不放在本目录：见 [历史交接归档（需完整仓库）](../../../docs/archive/engineering/README.md)

写作规则：新数字先进入 `CURRENT-EVIDENCE.md` 或 `claims.json`，再写进正文；历史数字不得直接复用。
