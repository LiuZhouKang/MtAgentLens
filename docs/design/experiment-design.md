# 实验范围与证据要求

Evidence-gated status：实验主张由同版本输入、运行产物和评估结果支持。本页供实验准备审计读取；已完成结果以[结果总览](../evaluation/CURRENT-EVIDENCE.md)及各实验 MANIFEST 为准。

EXTERNAL_BENCHMARK_POLICY: deferred_from_main_claims

## 研究问题

| 问题 | 评估对象 | 结果入口 |
|---|---|---|
| 能否识别 Agent 的不安全行为 | FuxiBench 场景上的检测判定、覆盖与执行状态 | [检测](../../compare/results/detection/README.md) |
| 何时可以可靠地赋予身份 | 三位标注者人工复核后的单元级身份赋值、覆盖和错误合并 | [身份](../../compare/results/identity/README.md) |
| 能否评估责任归属与传播链 | 人工复核后的 root-only 参考；entry/chain 当前未评估 | [归因](../../compare/results/attribution/README.md) |
| 机制和参数如何影响判定 | 冻结输入下的端点消融与参数扫描 | [消融](../../compare/results/ablation/README.md)、[敏感性](../../compare/results/sensitivity/README.md) |
| 结果是否由活动量解释 | 进程边界穿越与活动量对照 | [PBC](../../compare/results/pbc/README.md) |
| 采集和分析消耗多少资源 | Collector、探针及完整系统的独立测量 | [开销](../../compare/results/overhead/README.md) |

## 评估要求

- 场景设计标签和运行级执行事实分别保存；身份/归因最终参考由刘周康、唐浩钺、刘锦样人工核查、修订并确认；分别报告身份分区与 root-only 结果。
- 报告分母、执行状态、弃权、覆盖及不可估计项；采用每个冻结实验实际定义的评估单位。
- 消融说明关闭的机制与重算范围；固定 Observer 输出的反事实重算与在线模型重跑分别解释。
- 对比工具记录实际版本、输入和依赖。外部评估采用自己的样本与协议，不并入 FuxiBench 分母。
- 性能测量区分进程 CPU、单核利用率、应用性能变化和 API 成本；合成回归测试不代表实测开销。

本设计不主张已完成额外外部场景的大规模统一评估。各外部工具结果及覆盖范围见[实验交付](../../compare/results/README.md)。

实验单位、冻结与复现步骤见[实验方法](../evaluation/experimental-design.md)和[复现手册](../evaluation/REPRODUCE.md)。
