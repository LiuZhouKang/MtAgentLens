# 当前结果的正文整合稿

本稿提供可供作者整合的中文段落，尚未声明已经修改或合入Word。检测使用93场景批次，开销使用独立冻结批次；图号、节号和参考文献须由作者按最终稿核对。原稿与审稿原文保留在[originals](originals/)，逐条回复依据见[回复证据](REVIEW-RESPONSE-EVIDENCE-2026-10-26.md)。

## 摘要段落

多智能体工作流的任务意图、进程执行和资源访问分散在不同观测层，给安全审计与事后调查带来困难。本文提出MtAgentLens，在无需修改被监测Agent代码或接入SDK的条件下，通过eBPF采集进程、文件、网络和受支持TLS运行事实，结合应用意图、身份解析与责任图组织关联证据，并形成运行级安全裁决。在FuxiBench的93场景冻结结果中，58个攻击场景有29个具有执行标记，检测命中23个；ITT与执行条件TPR分别为0.397和0.793，35个良性场景产生2个Unsafe输出。身份与归因最终参考由刘周康、唐浩钺、刘锦样人工核查、修订并确认。IdentityBench 完整条件覆盖 39.0%，已赋值子集配对 F1=1.000；AttributionBench root-only Root@1=22.7%、Root@3=100%、MRR=0.538。消融分析分别量化排序分数与生产裁决工作点，独立开销实验报告采集、完整系统、模型费用和高并发来源丢弃。结果表明系统能够将跨层行为组织为可复核证据；AttributionBench 当前只评价 root 候选定位；Entry 与 Chain 尚未评估。

## 方法：分数、证据与裁决

连续分数用于候选分诊与路由；最终标签由硬证据及绑定具体候选的语义审阅结果聚合。对分数与标签分别计量，可以区分排序能力、生产误报和待复核输出。身份结果保留已赋值与未决状态，责任输出保存意图、动作及相关证据，使调查能够回到实际观测依据。具体路由与聚合流程见[判定流程](decision-procedure.md)，系统模块见[架构](../architecture.md)。

## 实验设置与检测结果

当前检测集合包含58个attack和35个benign，taxonomy为A47/B11。ITT使用全部攻击场景；PP使用29个marker=true攻击与35个良性。检测命中要求执行标记、Unsafe及健康采集同时成立。marker表示执行代理证据，不等同攻击目标完成；PP结果须与50.0%的攻击执行覆盖一起解释。

ITT TPR为23/58（0.397），PP TPR为23/29（0.793），良性误报为2/35（0.057）。良性另有21个Uncertain输出；6个marker=false攻击产生Unsafe，单列为未执行告警，不增加TP。检测表同时呈现区间、执行覆盖和未决输出，完整行级记录见[检测报告](../../compare/results/detection/README.md)。

## 基线与观测信息

主比较包含任务文本协议、任务与fixture文本分类器、完整应用轨迹裁判以及OS加应用观测系统。PP中MtAgentLens的TPR/FPR为0.793/0.057，R-Judge完整app trace为0.552/0.029。两者分别呈现较高召回与较低误报的工作点；配对比较的Holm校正p=0.212。该实验提供不同观测边界下的结果比较，输入范围与实现适配随表说明。方法身份及数值见[实际基线协议](意见3-应用层基线-全球调研与落地方案.md)。

## 身份、消融与敏感性

IdentityBench 与 AttributionBench 的最终参考已由刘周康、唐浩钺、刘锦样人工核查、修订并确认。IdentityBench 报告单元级身份分区结果；AttributionBench 报告 root-only 候选指标（Root@1=22.7%、Root@2=54.5%、Root@3=100%、MRR=0.538）。Entry 与 Chain 未评估，见[IdentityBench](../../compare/results/identity/README.md)与[AttributionBench](../../compare/results/attribution/README.md)。

消融将排序与生产裁决分开：L0到L0.5的AUROC由0.590增至0.677，差异未达显著；L1阈值到生产L3的PP FPR由0.743降至0.057、Precision由0.527升至0.920，同时TPR由1.000降至0.793。这说明责任图排序与最终裁决筛选承担不同作用。固定记录Observer的8,103点阈值分析及1,000次联合采样进一步给出参数扰动范围，见[消融](../../compare/results/ablation/README.md)与[敏感性](../../compare/results/sensitivity/README.md)。

## 成本与质量

开销实验区分固定本地负载下的Collector稳态与真实Agent、Observer参与的完整系统批次。三次稳态运行单核CPU均值为0.661%；完整系统36/36条件报告资源、费用、延迟与关联覆盖。32并发存在Process来源丢弃13,439–16,442，凭据动作的证据关联覆盖为23/96。两组负载及窗口不同，分别用于解释采集成本与完整系统扩展边界，见[分账表](../../compare/results/overhead/tables/component_accounting.md)。2026-11-01 补充了同一 C1 负载的并发扩展（1/4/8/16/32，合成 TLS+file+exec，无模型调用）：full-runtime（无 Observer）下 1 并发为 2.7% 单核 / 平均 PSS 114.4 MiB，32 并发为 0.307 核 / 124.8 MiB，受控文件与 TLS 事件零丢弃；capture-only 稳态的 0.661% 与 full-runtime 的 2.7% 是不同处理阶段，不能当作回归或改善。该表只解释稳态采集→归因运行时→SQLite 通路，不能替代真实 Agent + Observer 批次。见[开销报告](../../compare/results/overhead/README.md)与[合成并发图](../../compare/results/overhead/figures/synthetic_concurrency_mixed_2026-11-01.png)。

## 讨论与结论

系统的实证贡献在于跨层观测、可核查的行为证据以及区分排序和裁决的评价方式。当前结果限定于这一冻结cohort；尚无独立开发集划分证据，参数敏感性不替代数据独立性。IdentityBench 与 AttributionBench 的结果基于三位标注者人工复核并冻结的参考；结果范围分别限定为身份分区和 root-only 候选定位。参数敏感性不替代数据独立性。

数字版本见[主张矩阵](claim-evidence-matrix.md)。历史fix4的54条[claims](claims.json)仍保留原测量身份，不用于支持本稿93场景数字。作者整合后还需核对正文、图表、引用和逐点回复的一致性。
