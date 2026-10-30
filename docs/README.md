# MtAgentLens 文档

MtAgentLens 在 Linux 上采集 Agent 的 TLS 明文、进程、文件和网络事件，将工具意图与执行事实关联，并提供运行调查与证据查询。

## 使用与开发

| 要做什么 | 从哪里开始 |
|---|---|
| 安装、构建、首次运行 | [项目入门](../README.md#快速开始) |
| 采集、配置、回放和导出 | [Collector 使用指南](../collector/README.md) |
| 检查底层探针 | [eBPF 工具](../bpftools/README.zh-CN.md) |
| 理解系统模块与数据流 | [系统架构](architecture.md) |
| 理解判定逻辑 | [评分与判定](design/scoring-engine.md) |
| 开发、测试与发布 | [贡献指南](../CONTRIBUTING.md)、[维护脚本](../scripts/README.md) |
| 开发前端 | [前端说明](../frontend/README.md) |
| 检查安全边界 | [威胁模型](security/threat-model.md)、[安全策略](../SECURITY.md) |
| 查询能力约束与设计理由 | [能力清单](capabilities.json)、[架构决策](adr/README.md) |

## 数据、实验与论文

| 要做什么 | 从哪里开始 |
|---|---|
| 使用 FuxiBench 场景与标签 | [数据集](../datasets/FuxiBench/README.md)、[数据卡](../datasets/FuxiBench/DATASET-CARD.md) |
| 执行实验场景 | [Benchmark 环境](../env/README.md) |
| 理解评估方法 | [实验方法](evaluation/experimental-design.md) |
| 查看当前结果、图表与原始数据 | [结果总览](evaluation/CURRENT-EVIDENCE.md)、[实验交付](../compare/results/README.md) |
| 重算已交付结果 | [复现手册](evaluation/REPRODUCE.md) |
| 按审稿意见查证据 | [证据索引](evaluation/EVIDENCE-INDEX.md) |
| 准备论文与回复 | [论文工作区](paper/README.md) |
| 查阅论文、比赛和演示原件 | [原始材料](materials/README.md) |

## 文档维护

操作说明随对应模块维护；系统机制以源码和当前契约为依据；实验数值以匹配版本的冻结产物为依据。
结果表与图统一维护在 `compare/results/`，本目录引用其入口。

旧交接、逐轮进度和已替代的实施计划通过 Git 历史追溯，不再作为现行文档保存。
实验导航与历史来源说明见 [实验文档](evaluation/README.md)。
