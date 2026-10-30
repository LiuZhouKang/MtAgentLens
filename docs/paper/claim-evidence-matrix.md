# 论文主张与证据

本页说明论文可以保留的系统贡献及其证据范围。当前检测结果使用93场景批次；实验状态由[CURRENT-EVIDENCE](../evaluation/CURRENT-EVIDENCE.md)维护，审稿对应关系由[EVIDENCE-INDEX](../evaluation/EVIDENCE-INDEX.md)维护。

| 可保留主张 | 直接证据 | 论文表述范围 |
|---|---|---|
| OS边界观测为多智能体审计提供进程、文件、网络与受支持TLS事实，并与应用意图组织关联 | [系统架构](../architecture.md)、[检测报告](../../compare/results/detection/README.md)、[外部基线输入说明](../../compare/results/external/baselines/README.md) | 贡献在观测范围与可核查证据；基线输入不同，不能归为同输入算法优势 |
| 当前冻结cohort上可同时量化执行条件检测、误报与未决输出 | [行级检测结果](../../compare/results/detection/data/detection-metrics-final-93.json) | ITT TPR 23/58，PP TPR 23/29，良性Unsafe 2/35；另列6条未执行告警和21/35良性Uncertain。marker不是独立攻击成功真值 |
| 身份解析显式组织已赋值关系与未决身份 | [身份CSV](../../compare/results/identity/tables/t5_identity.csv)、[方法与参考](../../compare/results/identity/README.md) | Full赋值654/1675（39.0%），已赋值子集配对F1=1.000、全参考F1=0.280；两种分母并列。参考以声明进程/祖先关系为主 |
| 责任图排序与最终裁决工作点具有不同作用 | [消融指标](../../compare/results/ablation/metrics/t7-metrics.json)、[统计说明](../../compare/results/ablation/README.md) | L0→L0.5 AUROC 0.590→0.677，差异未达显著；L1阈值→生产L3的PP FPR 0.743→0.057，同时TPR 1.000→0.793，呈现取舍 |
| 已记录语义观测下，可量化参数扰动的结果范围 | [敏感性结果](../../compare/results/sensitivity/metrics/t7-sensitivity.json)、[协议](../../compare/results/sensitivity/README.md) | 8,103点网格及1,000次联合采样；固定记录Observer的routing反事实，不是新Observer运行或独立测试集 |
| 系统成本可以按Collector、完整系统、模型费用和采集质量分别解释 | [开销报告](../../compare/results/overhead/README.md)、[分账表](../../compare/results/overhead/tables/component_accounting.md) | 固定本地负载下Collector稳态单核CPU均值0.661%；完整系统另有36/36条件，32并发存在来源丢弃和关联不足 |

## 责任归因的当前定位

系统能够呈现关联证据与责任链线索；本revision的T6尚无根因准确率。[93条trace审计](../../compare/results/attribution/README.md)中27,175条claims的root_groups、unique_entry、ranked_agents均为0。论文应把证据组织能力与Root/Entry/Chain正确性区分开，标题、摘要和结论中的“根因定位”承诺须与该实际输出一致。

## 数字的版本归属

[claims.json](claims.json)的54条断言绑定历史fix4测量，保留其原字段和值，不能据此为本页93场景数字背书。[开销claims](claims-overhead-2026-10-28.json)属于独立开销批次。当前数字直接引用对应实验MANIFEST和机器结果；离线核对方法见[复现手册](../evaluation/REPRODUCE.md)。

正文与回复仍需作者核对实际Word稿、图表和引用；只有已合入稿件的修改才在提交信中写成“已修改”。
