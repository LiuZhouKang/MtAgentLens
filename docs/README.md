# MtAgentLens 文档

## 核心文档

| 文档 | 说明 |
|------|------|
| [architecture.md](architecture.md) | 系统架构全貌——eBPF 探针 → Rust 流式引擎 → 因果分析 → 前端，含完整数据流和模块映射 |

## 设计文档

| 文档 | 说明 |
|------|------|
| [design/experiment-design.md](design/experiment-design.md) | 实验设计 v5——RQ 驱动 · 标准指标 · 正式设计文档 |
| [design/scoring-engine.md](design/scoring-engine.md) | 评分引擎 v8——6 维组合评分、PBC 检测、阈值校准 |
| [design/sse-content-scanner.md](design/sse-content-scanner.md) | SSE 内容扫描器——间接注入检测的流式管道扩展 |
| [design/analyzer-dashboards.md](design/analyzer-dashboards.md) | Analyzer 仪表板组件——HandoffInjection、MultiAgentAnomaly、FileAccessAudit |
| [design/db-optimization.md](design/db-optimization.md) | DB 持久化体积优化——WAL 阈值、索引策略、Schema 规范化 |

## 攻击框架

| 文档 | 说明 |
|------|------|
| [safi/README.md](safi/README.md) | SAFI：安全不对称文件注入——系统化攻击载荷迁移框架（4 阶段 · 6 模式 · 65 触发器分类法） |

---

## 其他文档入口

- **项目 README**：[../README.md](../README.md) — 项目概述、快速开始、FuxiBench
- **Collector README**：[../collector/README.md](../collector/README.md) — Rust 引擎详细文档
- **FuxiBench 场景目录**：[../compare/Dataset/FuxiBench/SCENARIOS.md](../compare/Dataset/FuxiBench/SCENARIOS.md) — 86 场景完整描述
- **实验章节**：[../compare/results/paper/EXPERIMENTS.md](../compare/results/paper/EXPERIMENTS.md) — 论文实验部分
- **基准环境**：[../env/README.md](../env/README.md) — 基准测试使用说明
