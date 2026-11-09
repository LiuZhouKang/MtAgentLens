# 当前实验状态

本页导航实验状态及其适用范围。具体数字、统计方法和输入清单由各实验 README、MANIFEST 与机器结果维护；离线核对见[复现手册](REPRODUCE.md)，审稿问题映射见[证据索引](EVIDENCE-INDEX.md)。

## 数据范围

当前检测 cohort 为 FuxiBench **93 个场景：58 attack、35 benign**，当前 taxonomy 为 A47/B11，`detection_excluded=0`。ITT 纳入全部58条攻击；PP 使用29条 `marker=true` 攻击与35条良性。marker 是执行代理，不能等同攻击目标完成。数据来源与标注边界见[数据卡](../../datasets/FuxiBench/DATASET-CARD.md)。

## 实验状态

| 实验 | 状态与范围 | 结果入口 |
|---|---|---|
| 检测 T4 | 93场景；同时报告ITT、PP、良性误报与待复核 | [检测](../../compare/results/detection/README.md) |
| 身份 T5 | 人工复核完成；30 个多智能体场景、1,800 个单元；覆盖与分区一致性并列报告 | [IdentityBench](../../compare/results/identity/README.md) |
| 归因 T6 | 人工复核完成；root-only 结果可估计，Entry/Chain 未评估 | [AttributionBench](../../compare/results/attribution/README.md) |
| 消融 T7 | 可估计；连续排序与生产裁决工作点两个endpoint | [消融](../../compare/results/ablation/README.md) |
| 敏感性 T7-S | 可估计；8,103点阈值网格、逐项扰动、1,000次联合采样；固定记录Observer | [敏感性](../../compare/results/sensitivity/README.md) |
| 开销 T8 | 可估计；36/36完整系统条件与3次Collector稳态补测，分别解释 | [开销](../../compare/results/overhead/README.md) |
| PBC活动量 | 93场景派生指标可读；交付data中另含旧61-run分析，来源须分开 | [PBC](../../compare/results/pbc/README.md) |
| 外部基线 | 93场景已输出二元比较；输入范围和实现适配不同 | [基线](../../compare/results/external/baselines/README.md) |

结果交付清单见 [compare/results](../../compare/results/README.md)。各实验的实际运行、模型调用和费用按对应批次记录。

## 版本与证据边界

- [claims.json](../paper/claims.json) 的54条登记属于 **RC 2026-10-25-fix4**，其61个有效运行不是本页93场景主结果。历史校验通过只能证明该批登记与对应artifact一致。
- [开销claims](../paper/claims-overhead-2026-10-28.json)属于独立开销批次，不混入检测分母。冻结协议保留在[实验协议](EXPERIMENT-SUITE-7-REVISED-2026-10-26.md)。
- IdentityBench 与 AttributionBench 的最终参考已由刘周康、唐浩钺、刘锦样人工核查、修订并确认。身份评价单元级主体分区；归因评价 root 候选，Entry/Chain 不在当前评分范围内。
- 当前PBC指标为93场景，但 `compare/results/pbc/data/pbc-distribution.json` 与 `STRATIFIED-METRICS.json` 自述输入仍为旧fix4的61个运行；复现与论文引文须明确版本。

论文可保留的贡献及其限定见[主张矩阵](../paper/claim-evidence-matrix.md)。
