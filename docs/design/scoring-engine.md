# MtAgentPrism 评分机制 v8 设计文档
**状态**: 正式实施文档 · **日期**: 2026-06-12 · **基于**: v7 缺陷分析 + 学界/业界调研

## 1. 设计动机

### 1.1 v7 的六个量化缺陷

| # | 缺陷 | 量化证据 | 影响 |
|---|------|---------|------|
| D1 | 4/7 维度高度共线 (r>0.6) | rule×boundary=0.892 | 加权和退化为重复计数 |
| D2 | 29/54 攻击 rule=0 | rule 占 20% 权重 | 结构性劣势 |
| D3 | repetition 恒为 0 | replay 硬编码 | 10% 权重完全失效 |
| D4 | 18 个攻击路由为 direct_safe | 平均总分 0.058 | 33% 攻击被误放行 |
| D5 | behavioral 依赖 File PBC | D2/D4/D5/D6 统计 File edges | behavioral-only 攻击 behavioral=0 |
| D6 | 一次性评分无时间维度 | classify_authoritative 只调用一次 | 渐进式攻击无法区分 |

### 1.2 设计目标

1. **修复 D1-D6**——所有缺陷必须有可验证的修复
2. **保持向后兼容**——ClassificationEngine API 不变，现有调用方无感知
3. **消融可测量**——每个变更在消融实验中产生可量化的效果
4. **渐进式评分**——引入时间维度，支持 event-level 累积

## 2. 架构变更

### 2.1 变更范围

```
受影响文件:
  ✎ classification/scoring.rs     (~100 行修改)
  ✎ classification/engine.rs      (~20 行修改)
  ✎ classification/types.rs       (~10 行修改)
  ✎ replay/executor.rs            (~50 行修改)
  ✎ claims/scoring.rs             (~10 行修改)
  ✚ scoring/progressive.rs         (新增 ~300 行)
  ✚ scoring/mod.rs                 (新增 ~10 行)
  ✚ types/v8_types.rs              (新增 ~50 行)

不受影响:
  ✓ RuleEngine, PatternMatcher, GraphBuilder
  ✓ ClaimBuilder, AlertBuilder, TracebackBuilder
  ✓ IdentityResolver, EvidenceCorrelator
  ✓ 前端、server、persistence、runtime (除接入点外)
```

### 2.2 核心新增组件

```
┌─────────────────────────────────────────────────────────┐
│                  v8 Unified Scoring                      │
│                                                         │
│  ① ProgressiveScorer (新)                                │
│     ├─ CUSUM 渐进累积检测                                 │
│     ├─ EWMA 自适应基线                                    │
│     └─ 时序衰减 (半衰期=5min)                              │
│                                                         │
│  ② Enhanced Behavioral Scoring (修改)                     │
│     ├─ D7: Process fan-out  (不依赖 File PBC)             │
│     └─ D8: Edge saturation (不依赖 File PBC)              │
│                                                         │
│  ③ Evidence Weighted Median (修改)                        │
│     └─ 替代均值，对噪声不敏感                               │
│                                                         │
│  ④ repetition_count 修复 (修改)                           │
│     └─ 在 replay 中从 graph edges 统计实际重复              │
│                                                         │
│  ⑤ heuristic_only 证据过滤 (修改)                          │
│     └─ AI 管道关闭时同步过滤 evidence + content_boundary    │
│                                                         │
│  ⑥ ScoreBreakdownV8 (新)                                 │
│     └─ 每维度贡献分解 + CUSUM/EWMA 状态                    │
└─────────────────────────────────────────────────────────┘
```

## 3. 详细设计

### 3.1 修复 repetition_count (D3)

**当前代码** (`replay/executor.rs:339`):
```rust
let (classification_result, _route) = classification_engine.classify_authoritative(
    graph_builder.graph(), &patterns, &correlation_result.evidence,
    0, // ← BUG: 硬编码为 0
);
```

**v8 代码**:
```rust
// 从 persistence reader 读取的 graph 中统计重复的 PBC edges
let repetition_count = count_max_repeated_pbc_edges(graph_builder.graph());
let (classification_result, _route) = classification_engine.classify_authoritative(
    graph_builder.graph(), &patterns, &correlation_result.evidence,
    repetition_count, // ← 修复: 基于实际图统计
);
```

**`count_max_repeated_pbc_edges` 函数** (`executor.rs` 新增):
```rust
/// 统计图中相同 (source, target) 的 PBC edge 最大重复次数。
/// 这模拟了旧版 delta-based compute_repetition_score 在 replay 中的行为。
fn count_max_repeated_pbc_edges(graph: &ResponsibilityGraph) -> u32 {
    use std::collections::HashMap;
    let mut counts: HashMap<(NodeId, NodeId), u32> = HashMap::new();
    for edge in graph.all_edges() {
        if edge.edge_type == ResponsibilityEdgeType::PolicyBoundaryCrossing {
            *counts.entry((edge.source.clone(), edge.target.clone())).or_default() += 1;
        }
    }
    counts.values().max().copied().unwrap_or(0)
}
```

**预期效果**: repetition_score 在至少部分场景中 > 0，消融关闭 repetition 时可测量退化。

### 3.2 Behavioral D7/D8 (D5)

**当前 `sub_score_behavioral` 的 6 个维度有 4 个依赖 File PBC edges**，导致 behavioral-only 攻击 behavioral=0。

**v8 新增两个不依赖 File PBC 的维度**:

```rust
// D7: Process fan-out — Agent 生成的子进程数量
let process_fan_out = graph.all_edges()
    .filter(|e| matches!(e.edge_type,
        ResponsibilityEdgeType::AgentDelegatesTo))
    .count();
let dim_process_fan_out = process_fan_out >= 5;
// 理由: 多 Agent 攻击通过大量子进程委派实现，不产生 File PBC

// D8: Edge saturation — 某种边类型的集中度
let edge_type_counts: HashMap<_, u32> = /* 统计每种边类型的数量 */;
let max_edge_type = edge_type_counts.values().max().copied().unwrap_or(1);
let total_non_overhead = /* 非 ToolCall + 非 IntentCausesAction 的边总数 */;
let saturation = max_edge_type as f32 / total_non_overhead.max(1) as f32;
let dim_edge_saturation = saturation > 0.50;
// 理由: 正常场景边类型分布均匀，攻击场景某类边（如 AgentDelegatesTo）占主导
```

**8 维门控**: 保持现有 6 维，增加 D7、D8。门控分数表扩展:
- 0-1 维激活 → 0.0，2→0.40，3→0.55，4→0.70，5→0.85，6→0.95，7-8→1.00

### 3.3 evidence 加权中位数 (D1 部分修复)

**当前**: `sub_score_evidence` 使用均值 — 低置信度 evidence 拉低均值。

**v8**: 改用加权中位数。证据按 confidence 排序，取第 50 百分位值。这保证少数高置信度 evidence 不会被大量低置信度 evidence 稀释。

```rust
fn sub_score_evidence(evidence_records: &[CorrelationEvidence]) -> f32 {
    let mut sorted: Vec<f32> = evidence_records.iter()
        .filter(|e| e.match_level != MatchLevel::L5Substring)
        .map(|e| e.confidence)
        .collect();
    if sorted.is_empty() { return 0.0; }
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let n = sorted.len();
    if n % 2 == 0 {
        (sorted[n/2 - 1] + sorted[n/2]) / 2.0
    } else {
        sorted[n/2]
    }
}
```

### 3.4 heuristic_only 证据过滤 (消融 3 F1 不变修复)

**当前**: `heuristic_only` 只归零 behavioral/pattern/evidence/repetition 分数，但 PBC edges 和 evidence 记录仍完整传入——分类引擎仍有完整数据。

**v8**: 当 `skip_ai_pipeline=true` 时，同步过滤 evidence 和 PBC edges:
- 过滤 content-boundary PBC edges（SSE scanner 产生）
- 过滤仅被 content-boundary edges 引用的 evidence 记录
- 这样 classification engine 真正只能访问硬编码规则信号

此逻辑已存在于 `disable_content_boundary_signals` 的过滤中，只需在 `skip_ai_pipeline` 时也触发。

### 3.5 ProgressiveScorer (D6 — 核心新增)

#### 3.5.1 组件结构

```rust
// 位置: collector/src/responsibility/scoring/progressive.rs

/// 每个被监控 Agent 的渐进评分状态
pub struct AgentScoreState {
    pub agent_id: AgentId,
    pub event_scores: VecDeque<(u64, f64)>,  // (timestamp_ns, single_score)
    pub ewma_mean: f64,                       // EWMA 均值估计
    pub cusum: f64,                           // CUSUM 累积统计量
    pub consecutive_violations: u32,          // 连续违规计数
    pub last_update_ns: u64,                  // 上次更新时间
    pub cumulative_pts: f64,                  // 渐进威胁分数 (0-1)
}

/// 渐进评分配置
pub struct ProgressiveConfig {
    pub half_life_ns: u64,       // 半衰期 (默认 300s = 300_000_000_000ns)
    pub ewma_alpha: f64,         // EWMA 平滑因子 (默认 0.1)
    pub cusum_delta: f64,        // CUSUM 偏移参数 (默认 0.5)
    pub cusum_h: f64,            // CUSUM 告警阈值 (默认 3.0)
    pub consecutive_k: u32,      // 触发告警的最小连续违规数 (默认 2)
    pub max_event_window: usize, // 最大存储事件数 (默认 200)
}

/// 渐进评分结果
pub struct ProgressiveResult {
    pub pts: f64,                 // 渐进威胁分数
    pub cusum_alarm: bool,        // CUSUM 是否触发
    pub ewma: f64,                // 当前 EWMA 均值
    pub event_count: u64,        // 累计事件数
}
```

#### 3.5.2 核心算法

```rust
impl ProgressiveScorer {
    /// 摄入一个新事件，返回更新后的 PTS
    pub fn ingest(
        &mut self,
        agent_id: &AgentId,
        single_score: f64,
        timestamp_ns: u64,
    ) -> ProgressiveResult {
        let state = self.states.entry(agent_id.clone())
            .or_insert_with(|| AgentScoreState::new(agent_id.clone(), timestamp_ns));

        // Step 1: 时序衰减
        self.apply_decay(state, timestamp_ns);

        // Step 2: EWMA 更新
        state.ewma_mean = self.config.ewma_alpha * single_score
            + (1.0 - self.config.ewma_alpha) * state.ewma_mean;

        // Step 3: CUSUM: S_{t+1} = max(0, S_t + x_t - (μ_t + δ))
        let deviation = single_score - (state.ewma_mean + self.config.cusum_delta);
        state.cusum = (state.cusum + deviation).max(0.0);

        // Step 4: 连续违规计数
        if state.cusum > 0.0 {
            state.consecutive_violations += 1;
        } else {
            state.consecutive_violations = 0;
        }

        // Step 5: 记录事件
        state.event_scores.push_back((timestamp_ns, single_score));
        while state.event_scores.len() > self.config.max_event_window {
            state.event_scores.pop_front();
        }

        // Step 6: 渐进威胁分数 (PTS)
        // PTS = Σ(s_i × e^{-λ(t-t_i)}) / (1 + Σ(s_i × e^{-λ(t-t_i)}))
        let decay_sum: f64 = state.event_scores.iter()
            .map(|(ts, score)| {
                let dt = timestamp_ns.saturating_sub(*ts) as f64;
                let lambda = (2.0f64.ln()) / self.config.half_life_ns as f64;
                score * (-lambda * dt).exp()
            })
            .sum();
        state.cumulative_pts = decay_sum / (1.0 + decay_sum);
        state.last_update_ns = timestamp_ns;

        ProgressiveResult {
            pts: state.cumulative_pts,
            cusum_alarm: state.cusum > self.config.cusum_h
                && state.consecutive_violations >= self.config.consecutive_k,
            ewma: state.ewma_mean,
            event_count: state.event_scores.len() as u64,
        }
    }

    fn apply_decay(&self, state: &mut AgentScoreState, now_ns: u64) {
        let lambda = (2.0f64.ln()) / self.config.half_life_ns as f64;
        // 移除权重 < 1% 的旧事件 (dt > 6.6 × half_life)
        let cutoff = 7.0 * self.config.half_life_ns as f64;
        while let Some(&(ts, _)) = state.event_scores.front() {
            if (now_ns.saturating_sub(ts)) as f64 > cutoff {
                state.event_scores.pop_front();
            } else {
                break;
            }
        }
    }
}
```

#### 3.5.3 在 replay 中的接入

replay 模式下没有真实的 streaming 事件，但可以通过以下方式模拟渐进:

```rust
// executor.rs: 按时间戳排序 PBC edges，逐个 "ingest" 给 ProgressiveScorer
let sorted_edges = /* PBC edges sorted by timestamp_ns */;
let mut scorer = ProgressiveScorer::new(ProgressiveConfig::replay_mode());
for edge_batch in sorted_edges.chunks(5) {
    let single_score = compute_single_event_score(&graph_slice, &evidence_slice);
    let result = scorer.ingest(&agent_id, single_score, edge_batch[0].timestamp_ns);
}
let final_pts = scorer.get_pts(&agent_id);
```

### 3.6 ScoreBreakdownV8

```rust
/// 每个维度对总分的贡献分解
pub struct ScoreBreakdownV8 {
    pub dim_contributions: HashMap<String, f64>,  // 各维度贡献
    pub progressive: ProgressiveBreakdown,         // 渐进评分状态
    pub route: String,                             // 路由决策
    pub total_score: f64,                          // 最终分数
}

pub struct ProgressiveBreakdown {
    pub pts: f64,              // 渐进威胁分数
    pub ewma: f64,             // EWMA 均值
    pub cusum: f64,            // CUSUM 统计量
    pub cusum_alarm: bool,     // CUSUM 是否触发
    pub event_count: u64,      // 累计事件数
    pub decay_weight: f64,     // 当前时点的事件权重
}
```

## 4. 实施计划

### Phase 1: 基础修复 (D1-D5)

| 步骤 | 文件 | 内容 | 预计行数 |
|------|------|------|---------|
| P1.1 | `replay/executor.rs` | `count_max_repeated_pbc_edges()` + 修复调用点 | +30 |
| P1.2 | `classification/scoring.rs` | D7/D8 维度 | +40 |
| P1.3 | `classification/scoring.rs` | evidence 加权中位数 | +10/-10 |
| P1.4 | `replay/executor.rs` | heuristic_only 触发 evidence 过滤 | +15 |

### Phase 2: ProgressiveScorer (D6)

| 步骤 | 文件 | 内容 | 预计行数 |
|------|------|------|---------|
| P2.1 | `scoring/progressive.rs` | ProgressiveScorer 完整实现 | +300 |
| P2.2 | `scoring/mod.rs` | 模块入口 | +10 |
| P2.3 | `types/v8_types.rs` | ScoreBreakdownV8 | +50 |
| P2.4 | `classification/scoring.rs` | compute_score 接入 ProgressiveScorer | +20 |
| P2.5 | `replay/executor.rs` | replay 模拟渐进摄入 | +40 |

### Phase 3: 验证

| 步骤 | 内容 |
|------|------|
| P3.1 | `cargo test --lib` — 全部 878 测试通过 |
| P3.2 | 消融 6 变体 × 67 场景重跑 |
| P3.3 | 消融 3 (heuristic_only) F1 变化验证 |
| P3.4 | 消融表重新生成 |

## 5. 验收标准

| 标准 | 当前 | 目标 |
|------|------|------|
| repetition_score 中位数 | 0.00 | > 0.05 |
| heuristic_only F1 变化 | 0.00 (无效应) | ΔF1 < -0.05 (可测量退化) |
| behavioral 覆盖 behavioral-only 攻击 | 0/3 | ≥ 1/3 |
| evidence 加权中位数 attack vs benign Δ | 0.71 | 保持或提升 |
| 全部 Rust 测试 | 878 passed | 878 passed |
| 消融表逻辑一致性 | 部分矛盾 | 全部层次合理 |

## 6. 不变部分（向后兼容保证）

以下接口和行为**不变**：
- `ClassificationEngine::classify_provisional()` 签名
- `ClassificationEngine::classify_authoritative()` 签名
- `compute_score()` 签名（内部增强，外部调用方无感知）
- `route_score()` 签名和阈值逻辑
- `ClassificationConfig` 所有现有字段
- `RuleEngine`、`PatternMatcher`、`ClaimBuilder` 全部不变
