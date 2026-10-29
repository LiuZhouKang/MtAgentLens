# T7 消融：ranking 能力与 deployment gate（正式版）

> 93 个 FuxiBench 场景 · 全部来自冻结导出与生产 verdict · **0 次新增付费调用**  
> 入口：`compare/results/ablation/`。数据、指标、表、图、复现脚本均在此。

## 1. 核心结论

**T7 不是一个“每层 AUROC 单调上升”的 staircase，而是两个正式 endpoint：**

1. **Ranking capability**：graph reconstruction 是检测排序能力的主要来源；L0→L0.5 的 AUROC 为 **0.590→0.677（+0.087）**、AUPRC **0.651→0.753（+0.101）**；PBC/identity/evidence 与 Observer 不再提升 ranking。
2. **Deployment / operating point**：最终系统（L3 production）不是 recall booster，而是 **precision gate**。相对 L1 score@0.15，PP FPR **0.743→0.057**、Precision **0.527→0.920**、F1 **0.690→0.852**；TPR **1.000→0.793**。McNemar：L1-only **30** 个 alarm，L3-only **0** 个（p=1.9e-9）。

一句话：**graph 提供检测能力，Observer/finalization 提供可部署性。** 这比伪造一条单调 AUROC 曲线更准确，也更能回应审稿人。

## 2. Endpoint A：Ranking capability（AUROC / AUPRC）

| Layer | AUROC [95% CI] | AUPRC | ΔAUROC | Holm p |
|---|---|---|---|---|
| L0 raw activity | 0.590 [0.463, 0.713] | 0.651 | — | — |
| L0.5 + graph | **0.677 [0.562, 0.784]** | **0.753** | **+0.087** | 0.629 |
| L1 + PBC / pattern | 0.673 [0.557, 0.781] | 0.763 | −0.003 | 1.000 |
| L2 + identity / evidence | 0.673 [0.557, 0.781] | 0.763 | 0.000 | 1.000 |
| L3 full score | 0.676 [0.558, 0.786] | 0.758 | +0.003 | 1.000 |

- LaTeX：`tables/table7_ranking.tex`；Markdown：`tables/table7_ranking.md`。
- 关键：**graph 的 +0.087 AUROC / +0.101 AUPRC 方向一致，但 95% CI 跨 0（n=93），不能写成显著提升。** 排名的可靠结论是“L0.5 达到最高点，后续层没有额外 ranking 增益”，而不是“每层都显著”。
- L3 的 ranking_score 是 full-system detector score；**Observer 改变 verdict，不改变连续分数**，所以 L3 不应被期待提升 AUROC。

## 3. Endpoint B：Operating point（PP）

| System | TPR [95% CI] | FPR [95% CI] | Precision | F1 |
|---|---|---|---|---|
| L1 score @0.15 | 1.000 [0.883, 1.000] | 0.743 [0.579, 0.858] | 0.527 | 0.690 |
| **L3 production** | **0.793 [0.616, 0.902]** | **0.057 [0.016, 0.186]** | **0.920** | **0.852** |

- LaTeX：`tables/table7_operating.tex`；Markdown：`tables/table7_operating.md`。
- PP = 29 条 marker=true 攻击 + 35 条 benign；L1 是冻结阈值 0.15；L3 是同一 93 条 run 的生产 final verdict。
- L3 是 L1 alarm 的**严格子集**（L3-only=0）：它只删除警报，不新增警报；删除 30 个 benign false alarm、6 个 true alarm。因此不是“全面更优”，而是 **precision–recall trade-off**，且 F1/Precision/FPR 三项都明显更好。
- ITT 结果与 McNemar 明细在 `metrics/t7-metrics.json`。

## 4. Figure 7

![T7 ablation](figures/fig7_ablation.png)

[PDF（矢量）](figures/fig7_ablation.pdf) · [SVG](figures/fig7_ablation.svg) · [PNG 600 dpi](figures/fig7_ablation.png)

**Figure 7 | Two-endpoint ablation.**  
(**a**) AUROC and AUPRC of the model-free layer scores; error bars are 95% bootstrap intervals for AUROC. Graph construction (L0→L0.5) gives the largest ranking gain; later layers do not add ranking.  
(**b**) Operating point of L1 score@0.15 versus production L3 on executed attacks (29 attack, 35 benign). L3 removes 30 L1 false alarms and 6 true alarms, cutting FPR from 0.743 to 0.057 while raising precision from 0.527 to 0.920 and F1 from 0.690 to 0.852.

## 5. 为什么必须是两个 endpoint

- **Ranking** 衡量“分数能否把攻击排在 benign 前面”；
- **Operating point** 衡量“部署后误报/精确率是否可接受”；
- Observer 是 verdict-stage 组件，**不进入连续分数**；把它硬塞进 AUROC 会得到一条平线，并错误暗示“Observer 无用”。
- 反过来，只看 operating point 会掩盖 graph 的排序贡献。两个 endpoint 才能完整回答“每个组件贡献什么”。

## 6. 审稿人 Q&A

**Q: 为什么 L1/L2/L3 的 AUROC 不升？**  
A: L1/L2/L3 共享同一套 detector score；L2 的 identity/evidence 与 L3 的 Observer 主要作用于 verdict/归因，不改变 ranking。它们在 T5/T6 与 operating point 中评估，而不是用 AUROC 重复计分。

**Q: 那 Observer 到底有没有用？**  
A: 有，但作用在 deployment：它把 L1 的 FPR 从 74.3% 压到 5.7%，Precision 从 52.7% 提到 92.0%，F1 从 0.690 提到 0.852；代价是 PP TPR 从 1.000 降到 0.793。McNemar p=1.9e-9，且 L3 不新增任何 L1 没有的 alarm。

**Q: 为什么不用 live Observer 重跑 93？**  
A: 排名 endpoint 不依赖 Observer；operating point 使用**同一次 93 run 的生产 verdict**，比重放 Observer 更直接。live per-candidate review 的成本是数千次调用，且不改变 ranking；把它作为必跑项是设计错误。

**Q: graph 的提升显著吗？**  
A: 方向一致（+0.087 AUROC / +0.101 AUPRC），但 n=93 的 95% CI 跨 0，不能写成显著。我们不把“方向一致”包装成“显著提升”。

## 7. 局限（必须写）

1. **L2/L3 verdict state 是 partial**：scoped Observer review 的 candidate id 在 graph rebuild 后不匹配；ranking endpoint 不依赖 Observer，仍可估计。本文不把 partial verdict 写成完整 Observer 重放。
2. **L0 没有预先固定的 decision threshold**：只报 AUROC/AUPRC，不报 L0 的 TPR/FPR。
3. **L3 是 recorded production verdict**，不是新一轮 live Observer 执行；它来自同一 93 条 run，但不是独立复现。
4. **样本 93**：graph 的 CI 跨 0；operating point 的 CI 也较宽，结论应限定在本 cohort。
5. **PP 主口径**：29 条 marker=true 攻击；ITT 与未执行告警见 metrics 文件。

## 8. 文件与复现

```
compare/results/ablation/
  README.md
  plan.json
  data/exports/            # 93 个 mtagentlens.ablation-rebuild/v1 导出
  metrics/t7-metrics.json  # ranking + operating point + McNemar
  tables/table7_ranking.{tex,md}
  tables/table7_operating.{tex,md}
  figures/fig7_ablation.{png,pdf,svg}
  reproduce/build_metrics.py
  reproduce/render_assets.py
```

```bash
PYTHONPATH="." python compare/results/ablation/reproduce/build_metrics.py
PYTHONPATH="env:env/src:." python compare/results/ablation/reproduce/render_assets.py \
  --metrics compare/results/ablation/metrics/t7-metrics.json \
  --out compare/results/ablation
```

- 所有数字来自冻结导出 + 生产 verdict；本次 T7 没有新增付费调用。
- 中断的 per-candidate live review 只作为失败实验归档在 `local/final-v1/archive/ablation-live-partial/`，不进入论文表。

## 9. 交接

- 交付清单：[`MANIFEST.md`](MANIFEST.md)
- 原始数据：[`data/exports/`](data/exports/)（93 JSON，65 MB；说明见 [`data/README.md`](data/README.md)）
