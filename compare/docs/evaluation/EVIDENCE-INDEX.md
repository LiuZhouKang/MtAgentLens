# 审稿问题与证据索引

本页将原始审稿问题映射到当前证据，不另行维护结果数字表。原文见[审稿意见](../../../docs/paper/originals/审稿意见.txt)，逐条写作依据见[回复证据](../paper/REVIEW-RESPONSE-EVIDENCE-2026-10-26.md)。

| 原始问题 | 可引用证据 | 核对重点 |
|---|---|---|
| R1.1 分数与最终标签 | [判定流程](../paper/decision-procedure.md)；[final_decision.rs](../../../collector/src/responsibility/classification/final_decision.rs)；[消融工作点](../../results/ablation/README.md) | 分数负责分诊；生产最终裁决与连续分数分开解释 |
| R1.2 / R2.4 身份正确性 | [身份报告](../../results/identity/README.md)、[主表CSV](../../results/identity/tables/t5_identity.csv)、[manifest](../../results/identity/MANIFEST.md) | 覆盖、全参考F1与已赋值子集F1同时报告；参考并非人工gold |
| R1.2 / R2.4 根因定位 | [归因能力缺口](../../results/attribution/README.md)、[原始证据清单](../../results/attribution/MANIFEST.md) | root/entry/ranking缺失；不能把证据链展示写成根因准确率 |
| R1.3 / R2.3 基线公平性 | [输入与适配说明](../../results/external/baselines/README.md)、[二元指标](../../results/external/baselines/metrics/comparison.json)、[外部清单](../../results/external/MANIFEST.md) | OS+app、完整app trace和文本输入不同；区分官方工具与协议/权重适配 |
| R1.3 InjecAgent协议 | [外部原始产物说明](../../results/external/data/README.md)及[manifest](../../results/external/MANIFEST.md) | 独立于93场景主表；仅凭历史JSON不能证明实际执行或OS事件来源 |
| R1.4 / R2.1 分母与测试独立性 | [检测行级结果](../../results/detection/data/detection-metrics-final-93.json)、[数据卡](../../datasets/FuxiBench/DATASET-CARD.md)、[敏感性](../../results/sensitivity/README.md) | 58/35、PP与ITT分别报告；敏感性不证明开发/测试独立 |
| R2.2 PBC活动量混杂 | [93场景PBC指标](../../results/pbc/metrics/pbc-metrics.json)、[报告](../../results/pbc/README.md) | 当前metrics为93场景；data/pbc-distribution.json与STRATIFIED-METRICS.json自述61-run fix4，来源混放，不能作为同一批原始输入；分层关联不证明排除混杂 |
| R2.5 消融粒度 | [两endpoint消融](../../results/ablation/README.md)、[指标](../../results/ablation/metrics/t7-metrics.json) | graph排序增益未达显著；生产裁决存在precision–recall取舍 |
| R1.4 / R2.1 参数选择 | [敏感性manifest](../../results/sensitivity/MANIFEST.md)、[机器结果](../../results/sensitivity/metrics/t7-sensitivity.json) | 固定记录Observer；配置采样分位不是置信区间 |
| R1.5 完整开销 | [开销报告](../../results/overhead/README.md)、[分账表](../../results/overhead/tables/component_accounting.md)、[manifest](../../results/overhead/MANIFEST.md) | 稳态Collector与完整系统负载不同；高并发损失、费用及关联覆盖保留 |

当前实验状态见[CURRENT-EVIDENCE](CURRENT-EVIDENCE.md)，核对命令与缺失前置见[REPRODUCE](REPRODUCE.md)。历史fix4的[54条claims](../paper/claims.json)不作为93场景证据索引的替代来源；原始论文、审稿和修订稿由[论文入口](../paper/README.md)导航。
