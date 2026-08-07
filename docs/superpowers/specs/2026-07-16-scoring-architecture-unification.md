# MtAgentLens 评分架构统一设计规格

## 问题诊断

两个并行评分轨道从不互通，Observer LLM 完全无效（ΔAUROC=0, p=0.9999）。

## 变更概览

### 1. Observer LLM 重构（最高杠杆）

**Prompt 重构**：从仅传数值分数改为传递三部分内容
- 语义操作描述（intent tool + args）
- 渐进追踪状态（EWMA + CUSUM + baseline deviation）
- 缓存上下文（L2 异常率）

**证据负担反转**：默认判 Safe，判 Unsafe 需引用具体操作证据

**缓存 prior 修复**：classify_provisional 接受外部 CachePrior 参数

### 2. 评分统一

**核心公式**：agent_score = α·EWMA_fused + β·baseline_deviation + γ·static_score

**阶段权重**：
- 冷启动（触发<3次）：α=0, β=0, γ=1.0
- 预热（基线样本<50）：α=0.3, β=0, γ=0.7
- 稳定（基线≥50样本）：α=0.4, β=0.3, γ=0.3

**维度激活**：dim_weight[i] = base_weight[i] × min(ewma[i] / 0.15, 1.0)

### 3. BaselineTracker 扩展

新增追踪维度：PBC rate、intent pattern、evidence quality distribution

### 4. 反馈闭环

- Observer 置信度加权反馈到 CalibrationMonitor.online_update()
- Phase 2 结果用于 AUC 追踪
- L2 缓存跨 session 持久化

### 5. 评估脚本修复

- 移除 Observer 调整的 has_unsafe_rules 门控
- 使用 progressive 分数替代静态分数

## 不变约束

- 不改变 Phase 1/2 架构
- 不改变 API 契约
- 不改变 eBPF 层
- 不改变数据库 schema
- 所有现有测试必须继续通过
