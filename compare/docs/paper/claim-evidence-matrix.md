# 论文主张与证据

本页说明论文可以保留的系统贡献及其证据范围。当前检测结果使用93场景批次；实验状态由[CURRENT-EVIDENCE](../evaluation/CURRENT-EVIDENCE.md)维护，审稿对应关系由[EVIDENCE-INDEX](../evaluation/EVIDENCE-INDEX.md)维护。

| 可保留主张 | 直接证据 | 论文表述范围 |
|---|---|---|
| OS边界观测为多智能体审计提供进程、文件、网络与受支持TLS事实，并与应用意图组织关联 | [系统架构](../../../docs/architecture.md)、[检测报告](../../results/detection/README.md)、[外部基线输入说明](../../results/external/baselines/README.md) | 贡献在观测范围与可核查证据；基线输入不同，不能归为同输入算法优势 |
| 当前冻结cohort上可同时量化执行条件检测、误报与未决输出 | [行级检测结果](../../results/detection/data/detection-metrics-final-93.json) | ITT TPR 23/58，PP TPR 23/29，良性Unsafe 2/35；另列6条未执行告警和21/35良性Uncertain。marker不是独立攻击成功真值 |
| 身份解析显式组织已赋值关系与未决身份 | [IdentityBench](../../results/identity/README.md)、[身份参考](../../results/identity/MANIFEST.md) | 30 个场景、1,800 个单元；Full 覆盖 654/1,675（39.0%），已赋值子集配对 F1=1.000、全参考 F1=0.280；参考由三位标注者人工核查、修订并确认 |
| 责任图排序与最终裁决工作点具有不同作用 | [消融指标](../../results/ablation/metrics/t7-metrics.json)、[统计说明](../../results/ablation/README.md) | L0→L0.5 AUROC 0.590→0.677，差异未达显著；L1阈值→生产L3的PP FPR 0.743→0.057，同时TPR 1.000→0.793，呈现取舍 |
| 已记录语义观测下，可量化参数扰动的结果范围 | [敏感性结果](../../results/sensitivity/metrics/t7-sensitivity.json)、[协议](../../results/sensitivity/README.md) | 8,103点网格及1,000次联合采样；固定记录Observer的routing反事实，不是新Observer运行或独立测试集 |
| 系统成本可以按Collector、完整系统、模型费用和采集质量分别解释 | [开销报告](../../results/overhead/README.md)、[分账表](../../results/overhead/tables/component_accounting.md) | 固定本地负载下Collector稳态单核CPU均值0.661%；完整系统另有36/36条件，32并发存在来源丢弃和关联不足 |

## 责任归因的当前定位

系统能够呈现关联证据与责任链线索。IdentityBench 的人工复核参考支持单元级身份分区评价；AttributionBench 的人工复核参考支持 root-only 候选定位评价（Root@1=22.7%、Root@2=54.5%、Root@3=100%、MRR=0.538）。Entry/Chain 尚未评估。见[IdentityBench](../../results/identity/README.md)与[AttributionBench](../../results/attribution/README.md)。

## 数字的版本归属

[claims.json](claims.json)的54条断言绑定历史fix4测量，保留其原字段和值，不能据此为本页93场景数字背书。[开销claims](claims-overhead-2026-10-28.json)属于独立开销批次。当前数字直接引用对应实验MANIFEST和机器结果；离线核对方法见[复现手册](../evaluation/REPRODUCE.md)。

正文与回复仍需作者核对实际Word稿、图表和引用；只有已合入稿件的修改才在提交信中写成“已修改”。
