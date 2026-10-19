# MtAgentLens 文档

## 从这里开始

| 文档 | 说明 |
|------|------|
| [../README.md](../README.md) | 项目概述、快速开始、FuxiBench 结果 |
| [architecture.md](architecture.md) | 系统架构：eBPF 探针 → Rust 流式引擎 → 责任图 → 前端 |
| [HANDOFF.md](HANDOFF.md) | **当前交接**（新会话唯一入口） |
| [adr/README.md](adr/README.md) | 架构决策记录（ADR 0001–0014） |
| [capabilities.json](capabilities.json) | 机器可读能力清单与已知限制 |

## 设计与评估

| 文档 | 说明 |
|------|------|
| [design/experiment-design.md](design/experiment-design.md) | 实验设计 v5（RQ 驱动、标准指标、Bootstrap 范围） |
| [evaluation/measurement-isolation.md](evaluation/measurement-isolation.md) | 实验隔离：clean profile + inode 副本 + 唯一 comm |
| [evaluation/fpr-baseline.json](evaluation/fpr-baseline.json) | 隔离后的 20 场景良性 FPR（0/20） |
| [evaluation/overhead-baseline.json](evaluation/overhead-baseline.json) | 端到端开销基线 |
| [design/scoring-engine.md](design/scoring-engine.md) | 六维评分引擎 |
| [design/sse-content-scanner.md](design/sse-content-scanner.md) | SSE 内容扫描器 |
| [design/analyzer-dashboards.md](design/analyzer-dashboards.md) | Analyzer 仪表板 |
| [design/db-optimization.md](design/db-optimization.md) | DB 体积与索引 |
| [design/analyzers/](design/analyzers/) | Analyzer 管线契约与当前实现设计；未实现提案见 `superpowers/archive/design/` |

## 安全与论文

| 文档 | 说明 |
|------|------|
| [security/threat-model.md](security/threat-model.md) | 威胁模型与逐项缓解 |
| [security/openSSF-self-assessment.md](security/openSSF-self-assessment.md) | OpenSSF 自评 |
| [../SECURITY.md](../SECURITY.md) | 漏洞报告与残余风险 |
| [safi/README.md](safi/README.md) | SAFI：安全不对称文件注入框架 |
| [paper/](paper/) | 论文初稿（作者稿，指标以 `compare/results/paper/standard_evaluation_v5.json` 为准） |
| [决赛文档/](决赛文档/) | 比赛提交时的冻结文档（TeX/PDF/assets；数字与路径按当时状态保留） |
| [demo/](demo/) | 演示截图、视频与 [讲解稿](demo/讲解稿.md) |

## 规划历史

| 文档 | 说明 |
|------|------|
| [superpowers/README.md](superpowers/README.md) | 当前交接 + `archive/`（旧交接、plans、specs、spikes） |
