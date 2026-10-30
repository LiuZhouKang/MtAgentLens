# 当前实验与论文证据入口（2026-10-30）

> 本页是当前唯一状态入口；历史交接、旧审计和旧 E3 文档只作归档，不再作为引用口径。  
> 数据口径：FuxiBench 93 个场景（58 attack / 35 benign）；指标以 PP 为主，ITT 作补充；除特别说明外，全部来自冻结导出，0 次新增付费调用。

## 1. 当前实验状态

| 实验 | 当前状态 | 权威文档 | 图/表 | 论文位置 |
|---|---|---|---|---|
| T4 检测 | 93 场景最终结果 | [`compare/results/detection/README.md`](../../compare/results/detection/README.md) | `detection/figures`, `detection/tables` | §5 |
| T5 身份 | `estimable` | [`compare/results/identity/README.md`](../../compare/results/identity/README.md) | `identity/figures`, `identity/tables` | §5.2 |
| T6 归因 | `not_estimable`（suite） | [`compare/results/attribution/README.md`](../../compare/results/attribution/README.md) | `attribution/` | §5.3 / 局限 |
| T7 消融 | `estimable`（two-endpoint） | [`compare/results/ablation/README.md`](../../compare/results/ablation/README.md) | `fig7_ablation`, `table7_*` | §5.4 |
| T7-S 参数敏感性 | `estimable` | [`compare/results/sensitivity/README.md`](../../compare/results/sensitivity/README.md) | `fig8_sensitivity`, `table8_*` | §5.5 / 审稿 6 |
| T8 开销 | `estimable` | [`compare/results/overhead/README.md`](../../compare/results/overhead/README.md) | `overhead/figures`, `overhead/tables` | §6.4 / 表 12 |
| PBC 混杂 | `estimable` | [`compare/results/pbc/README.md`](../../compare/results/pbc/README.md) | `pbc/figures`, `pbc/tables` | §5.3 |
| App 基线 | `partial` | [`EXPERIMENTS.md`](../../local/final-v1/EXPERIMENTS.md) | `compare/results/external/` | §5.1 / 局限 |

## 2. 论文与审稿入口

- **实验协议**：[`EXPERIMENT-SUITE-7-REVISED-2026-10-26.md`](EXPERIMENT-SUITE-7-REVISED-2026-10-26.md)
- **复现手册**：[`REPRODUCE.md`](REPRODUCE.md)
- **证据索引**：[`EVIDENCE-INDEX.md`](EVIDENCE-INDEX.md)
- **主张-证据矩阵**：[`claim-evidence-matrix.md`](../paper/claim-evidence-matrix.md)
- **审稿回复证据**：[`REVIEW-RESPONSE-EVIDENCE-2026-10-26.md`](../paper/REVIEW-RESPONSE-EVIDENCE-2026-10-26.md)
- **论文修改说明**：[`修改说明-草稿.md`](../paper/修改说明-草稿.md)
- **数字审计**：[`数字审计-2026-10-25.md`](../paper/数字审计-2026-10-25.md)

## 3. 当前关键结论

- T7 消融：graph 是 ranking 能力的主要来源（L0→L0.5 AUROC 0.590→0.677），Observer/finalization 主要提升可部署性（PP FPR 0.743→0.057，Precision 0.527→0.920）。
- T7-S 敏感性：部署点 PP F1 0.852；8103 点阈值网格 0.769–0.877，6980/8103 点差 ≤0.03；1000 次联合采样中位 0.852，95% 采样分位 [0.784, 0.877]，91.1% ≥0.80。
- T8 开销：正式实测 36/36 条件；完整导出在 `compare/results/overhead/raw/full-system/report/`。
- 所有结果来自冻结导出；T7-S 使用固定已记录 Observer 的 routing counterfactual，不是 live Observer 重跑。

## 4. 归档

历史 AI 交接、CODEX/DSH 交接、旧轮次审计和旧 E3 记录统一放在：

- [`docs/archive/engineering/README.md`](../archive/engineering/README.md)

这些文件**不是当前入口**，只用于追溯；新文档请写回本页或对应主题目录。
