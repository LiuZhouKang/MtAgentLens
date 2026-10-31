# 审稿回复依据

本页为逐点回复提供当前可核查材料，文件名保留原路径以维持引用。作者可据此整合正文与回复。审稿原文保留在完整仓库的 `docs/paper/originals/审稿意见.txt`，结果状态见[实验入口](../evaluation/CURRENT-EVIDENCE.md)。

## R1.1：评分流程与最终标签

应明确连续分数、路由阈值、候选证据和最终标签的关系。分数用于分诊；硬证据与具体候选的审阅结果通过最终裁决内核聚合，不能把“超过safe阈值”直接写成攻击判定。

依据：[判定流程](decision-procedure.md)、[final_decision.rs](../../../collector/src/responsibility/classification/final_decision.rs)、[生产工作点消融](../../results/ablation/README.md)。修订算法和示例时使用同一流程，不把历史构建与当前实现混写。

## R1.2 / R2.4：身份与根因

身份补充已有30个多智能体场景、1,800个抽样单元。Full在1,675个参考已知单元中赋值654个，已赋值子集配对F1为1.000，全参考F1为0.280；TLS条件覆盖74.2%。这量化了证据约束下选择性身份赋值的覆盖与关系一致性。参考基于声明进程及祖先关系，模型复核不是人工gold；不能写成共享进程内逻辑身份区分已被独立验证。

根因部分按已交付输出范围说明：93条trace的27,175条claims中没有root_groups、unique_entry或ranked_agents预测，T6为 `not_estimable`。图规模或链展示不替代Root@k、Entry、Chain正确率。

依据：[身份报告](../../results/identity/README.md)、[主表CSV](../../results/identity/tables/t5_identity.csv)、[归因审计](../../results/attribution/README.md)。

## R1.3 / R2.3：基线、公平性与外部协议

当前93场景比较包含MtAgentLens、Agent Sentinel-style、LLM Guard本地权重适配、Meta Prompt Guard及完整app trace的R-Judge。PP中MtAgentLens TPR为0.793、FPR为0.057，R-Judge分别为0.552、0.029；输入不同，结果用于讨论观测信息及运行协议，不能解释为同输入算法优越性。与R-Judge的配对差异Holm校正p=0.212，不写成显著胜出。

Agent Sentinel-style不是官方binary；LLM Guard不是官方SDK端到端部署；Meta使用镜像权重；R-Judge包含多智能体拼接与字段截断。各适配和输入范围须随主表说明。

依据：[比较协议与限制](../../results/external/baselines/README.md)、[机器结果](../../results/external/baselines/metrics/comparison.json)。InjecAgent的四份历史JSON保留在[外部数据](../../results/external/data/README.md)，它们不属于93场景主表；原稿的样本构造、实际执行与OS事件来源仍需对应协议和原始记录支持，不能仅凭产物存在认定已经补齐。

## R1.4 / R2.1：指标、分母与独立性

当前数据为58 attack、35 benign。检测同时报告ITT TPR 23/58和PP TPR 23/29，良性Unsafe为2/35；21条良性仍为Uncertain，6条marker=false攻击产生Unsafe并另列为未执行告警。不得只引用PP而隐去执行覆盖，也不能把Uncertain解释为人工复核完成。

开发与测试独立性需要明确的划分记录；参数稳健性不能替代独立数据。现有敏感性使用固定记录Observer，8,103点阈值网格PP F1为0.769–0.877；1,000次联合采样的95%采样分位为[0.784,0.877]，不是置信区间。

依据：[检测结果](../../results/detection/README.md)、[行级字段](../../results/detection/data/detection-metrics-final-93.json)、[敏感性](../../results/sensitivity/README.md)、[数据卡](../../datasets/FuxiBench/DATASET-CARD.md)。

## R2.2：PBC与活动量混杂

PBC应作为补充证据解释，不能作为恶意活动量的独立真值。当前[93场景派生指标](../../results/pbc/metrics/pbc-metrics.json)提供分布、分层和活动量基线；这些分析不能独立排除任务复杂度混杂。交付目录中的 `data/pbc-distribution.json` 和 `STRATIFIED-METRICS.json` 仍自述61-run fix4输入，引用时须与93场景指标区分，不能把二者拼成同一批证据。

## R2.5：组件贡献与消融粒度

将排序能力与生产裁决工作点分开答复。L0→L0.5 AUROC从0.590到0.677，方向为正但差异未达显著；不能写成每层显著提高AUROC。L1阈值到生产L3的PP FPR从0.743降到0.057、Precision从0.527升至0.920，同时TPR从1.000降到0.793。它说明最终裁决的筛选作用及代价，不证明Observer连续分数更好。

依据：[两endpoint报告](../../results/ablation/README.md)、[指标](../../results/ablation/metrics/t7-metrics.json)。生产L3为已有裁决；重建中的partial verdict与生产结果区分，不能称完整Observer重跑。

## R1.5：完整系统开销

采用Collector稳态与完整系统两组测量分别回答。固定本地负载下三次Collector单核CPU均值为0.661%；完整系统另有36/36正式条件，并报告图维护、模型费用、关联覆盖和来源丢弃。32并发Process来源丢弃范围13,439–16,442，不能声称全并发零丢失或所有系统低于5% CPU。

旧218 ns是不同程序均值相加，不能当单次SSL操作成本或与握手直接求占比。TLS微基准的873.35 ns/成功读写操作也不是端到端采集或告警延迟。单主机负载、三次稳态重复及有限告警样本应随图表说明。

依据：[开销报告](../../results/overhead/README.md)、[成本与质量分账](../../results/overhead/tables/component_accounting.md)、[MANIFEST](../../results/overhead/MANIFEST.md)、[冻结协议](../evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md)。

## 提交前的稿件核对

[主张矩阵](claim-evidence-matrix.md)限定论文贡献；[复现手册](../evaluation/REPRODUCE.md)区分当前结果与历史fix4的54条claims。正文替换稿与修订要点用于整合当前结果；提交时核对最终图表、章节编号和 Word 稿，确保正文与回复一致。
