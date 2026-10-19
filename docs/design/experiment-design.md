# MtAgentLens 实验设计 v5 — RQ驱动 · 标准指标 · 正式设计文档
**状态**: 正式设计文档 · **日期**: 2026-06-14 · **目标会议**: USENIX Security / CCS / NDSS

> **当前实现口径（2026-10-19）**：本文件保留 v5 实验设计思路。FuxiBench 86 场景
> （67 attack + 19 benign；Type A=10、B=34、C=23）。指标必须分口径：
>
> - **交付二进制裁决**（`responsibility_alerts`，`compare/results/paper/standard_evaluation_v5.json`）：
>   `decision=Unsafe` TPR=0.8955（Wilson 0.7997–0.9485）、FPR=0.000（0–0.168）、
>   F1=0.9449、MCC=0.809；Warning+ TPR=0.9851 但 FPR=0.8947（不可作为产品口径）。
> - **评估脚本复合分**（`main_evaluation.json` 的 `full_scores`）：
>   TPR=0.9851、F1=0.9925；这是脚本对 DB 信号的重打分，**不是**二进制自身裁决。
>
> 旧文中的 TPR=0.896 / F1=0.945 是历史采集，不再作为头条数字。

> v5 取代 v4。核心变更：
> 1. 定义 4 个 Research Questions，每个实验严格对齐 RQ
> 2. 全面采用领域标准指标（TPR/FPR/F1/MCC/Bootstrap CI/Ablation Gap）
> 3. "TLS OFF" → "eBPF Signal Layer OFF"：正确表述内核级监控的结构性优势

---

## 0. Research Questions

### RQ1: Detection Superiority
**MtAgentLens 能否检测 prompt 级工具结构性漏报的 LLM Agent 攻击？**

- 核心假设：多 Agent 攻击（Type C）的恶意行为仅通过跨 Agent **行为关联**可见，单个 prompt 文本合法 → prompt 工具结构性失明
- 实验：Frozen Main Evaluation（当前主评估为 86 场景，覆盖 Type A/B/C + Benign）
- 主要指标：TPR, FPR, F1, MCC（per attack type）
- 统计验证：McNemar 配对检验 + Bootstrap 95% CI

### RQ2: Defense-in-Depth
**多层防御架构中的每一层是否独立贡献检测能力？**

- 核心假设：6 层架构中每一层有独立、可定量测量的边际贡献，不存在单点故障
- 实验：Controlled Feature Ablation（反事实分析，在已持久化 SQLite 图上执行）
- 主要指标：Ablation Gap (Δ), Relative Importance (Δ/Baseline), Cohen's d
- 关键叙事：Co-necessity pattern — eBPF 信号是必要条件，Observer 是增强层

### RQ3: Attribution Quality
**MtAgentLens 能否超越二元分类，提供结构化攻击归因？**

- 核心假设：所有对比工具只输出 binary label，MtAgentLens 输出结构化 claims + tracebacks + agent-level 责任归属
- 实验：Attribution Analysis（Claim Coverage, Evidence Level 分布）
- 主要指标：Claim Coverage (by boundary type, by attack category), Evidence Level L1-L5 分布
- 关键叙事：Detection→Attribution 的结构性质变

### RQ4: Runtime Efficiency
**内核级监控的性能开销是否可接受？**

- 核心假设：eBPF probe 开销 <100μs/event，吞吐 >500 ev/s，RSS <50MB
- 实验：Microbenchmarks（probe latency, throughput, memory）
- 主要指标：Per-event overhead (μs), Throughput (ev/s), RSS (MB), Startup latency (ms)

---

## 1. 标准指标定义

### 1.1 检测指标（RQ1）

采用安全/ML 领域通用标准：

| 指标 | 公式 | 用途 |
|------|------|------|
| **TPR** (True Positive Rate / Recall) | TP/(TP+FN) | 攻击检出率 |
| **FPR** (False Positive Rate) | FP/(FP+TN) | 误报率 |
| **Precision** | TP/(TP+FP) | 告警准确性 |
| **F1-score** | 2·P·R/(P+R) | Precision-Recall 调和均值 |
| **MCC** (Matthews Correlation Coefficient) | (TP·TN−FP·FN)/√((TP+FP)(TP+FN)(TN+FP)(TN+FN)) | 不平衡数据综合评价（−1到+1） |
| **Detection Rate** | TP/Total_Attacks | 场景级检出率 |

**Per-attack-type 分解**：所有检测指标按 Type A (direct injection) / Type B (indirect/fixture) / Type C (multi-agent) 分别报告。

**为什么 MCC 优于 F1**：当前主评估 86 场景中 67 attack vs 19 benign（不平衡），MCC 不会被 majority class 主导。MCC > 0.7 表示强检测能力，≈0 表示随机猜测。

### 1.2 消融指标（RQ2）

采用 ML 消融文献标准（Hanna et al., "Ablation Study": How Much Does Your "Ablated" Component Contribute?）：

| 指标 | 公式 | 用途 |
|------|------|------|
| **Ablation Gap (Δ)** | Baseline − Ablated | 绝对性能下降 |
| **Relative Importance** | Δ / Baseline × 100% | 相对贡献度 |
| **Effect Size (Cohen's d)** | (μ_baseline − μ_ablated) / σ_pooled | 标准化效应量 |
| **Co-necessity Score** | min(Importance_i, Importance_j) / max(Importance_i, Importance_j) | 组件互补性 |

**注**：之前使用的 MRR (Marginal Recall Reduction) = Relative Importance for Recall；MCR (Marginal Claim Reduction) = Relative Importance for Claims。v5 统一为标准术语。

### 1.3 归因指标（RQ3）

| 指标 | 公式 | 用途 |
|------|------|------|
| **Claim Coverage** | Matched_Claims / Expected_Claims | 归因完整性 |
| **Attribution Precision** | Correctly_Attributed / Total_Claims | 归因准确性 |
| **Evidence Level Score** | Weighted sum of L1-L5 evidence | 证据质量 |
| **Traceback Completeness** | Traced_Edges / Total_Attack_Edges | 溯源完整性 |

### 1.4 性能指标（RQ4）

| 指标 | 目标 | 测量方法 |
|------|------|---------|
| **Probe Latency** | <100μs | `criterion` benchmark (process exec/file access uprobe) |
| **Throughput** | >500 ev/s | 1000/3000 event pair burst test |
| **Memory (RSS)** | <50MB | `/proc/<pid>/status` peak |
| **Startup Latency** | <10ms | First observation timestamp |

### 1.5 统计方法

| 方法 | 用途 | 标准来源 |
|------|------|---------|
| **McNemar's test** (Yates correction) | 配对二元结果比较 | Dietterich (1998), "Approximate Statistical Tests for Comparing Supervised Classification Learning Algorithms" |
| **Bootstrap 95% CI** (10,000 resamples, BCa) | 交付二进制的 TPR/FPR 不确定性量化（当前仅两种裁决口径；见 §Bootstrap 95% CI 的范围说明） | Efron & Tibshirani (1994) |
| **Cohen's κ** | 标注者间一致性 | 盲标注质量保证 |
| **Fisher's exact test** | 小样本 2×2 列联表 | 替代 χ²（小 n 时更准确） |

---

## 2. 系统架构与消融映射

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 0: eBPF Signal Acquisition                             │
│   KERNEL-LEVEL monitoring (not application-level)            │
│   TLS uprobe (BoringSSL multi-version) + Process + Stdio     │
│   → Ablation A0: --ssl=false + --process=false              │
│   → RQ1, RQ2: 对比 "有/无内核级监控" 的结构性差异           │
├──────────────────────────────────────────────────────────────┤
│ Layer 1: Rule Engine (23 hand-crafted rules)                 │
│   → Ablation A1: Skip rule engine in classification          │
│   → RQ2: 规则引擎的独立边际贡献                             │
├──────────────────────────────────────────────────────────────┤
│ Layer 2: Graph Pattern Matching                              │
│   → Ablation A2: Skip pattern matcher                        │
│   → RQ2: 图拓扑异常检测的独立贡献                           │
├──────────────────────────────────────────────────────────────┤
│ Layer 3: Progressive Scoring (6-dim + CUSUM)                 │
│   → Ablation A3: Static scoring (no EWMA/CUSUM)             │
│   → RQ2: 时序行为评分的独立贡献                             │
├──────────────────────────────────────────────────────────────┤
│ Layer 4: Observer LLM (semantic verification)                │
│   → Ablation A4: --disable-responsibility-observer           │
│   → RQ2: LLM 语义验证的边际贡献                             │
├──────────────────────────────────────────────────────────────┤
│ Layer 5: Identity Resolution (Union-Find)                    │
│   → Ablation A5: Single-agent identity                       │
│   → RQ2, RQ3: 身份解析对归因的影响                          │
├──────────────────────────────────────────────────────────────┤
│ Layer 6: Attribution Pipeline (Claims + Tracebacks)          │
│   → Ablation A6: Alerts only (no claims/tracebacks)          │
│   → RQ3: 结构化归因 vs 二元分类的质变                       │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. 数据集

### 3.1 Frozen v1.0.0

- **当前主评估 86 场景**：67 attack (10 Type A, 34 Type B, 23 Type C) + 19 benign
- **全部 86 场景有 ground truth**（三标注者交叉验证 + 共识裁判）
- **全部 86 场景有 prompt input bundle**（用于 LLM Guard、Meta Prompt Guard、Agent Sentinel 文本基线）
- `compare/Dataset/FuxiBench/traces/` 中还包含主评估外的扩展/灰区/历史运行归档，不应直接等同于 86 场景主评估规模。

### 3.2 攻击类型定义

| 类型 | 定义 | 场景数 | 为何 prompt 工具可能漏检 |
|------|------|--------|------------------------|
| **Type A** (Direct Injection) | 单 Agent，直接恶意 prompt | 10 | prompt 文本包含攻击信号 → prompt 工具可检测 |
| **Type B** (Indirect/Fixture) | 单 Agent，通过文件/环境间接攻击 | 34 | 攻击信号在文件系统，不在 prompt 文本中 |
| **Type C** (Multi-Agent) | 多 Agent，跨 Agent 委派/传播攻击 | 23 | 每个单独 prompt 合法，仅跨 Agent 行为关联可见 |

---

## 4. 实验矩阵

```
┌─────────────────────────────────────────────────────────────┐
│  RQ1: Detection     → Experiment 1: Frozen Main Evaluation  │
│  RQ2: Defense-in-   → Experiment 2: Controlled Ablation     │
│        Depth                                                 │
│  RQ3: Attribution   → Experiment 3: Attribution Analysis    │
│  RQ4: Performance   → Experiment 4: Microbenchmarks         │
│  Annotation Quality → Experiment 5: Blind Annotation        │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Experiment 1: Frozen Main Evaluation（RQ1）

### 设计

比较 5 种配置在 frozen 数据集上的检测能力（当前主评估为 86 场景）：

| # | 配置 | 工具族 | 输入 |
|---|------|--------|------|
| 1 | **MtAgentLens** (eBPF ON) | runtime-causal-observability | responsibility.db artifacts |
| 2 | LLM Guard | prompt-baseline | prompt_inputs manifest |
| 3 | Vigil | prompt-baseline | prompt_inputs manifest |
| 4 | Meta Prompt Guard | prompt-baseline | prompt_inputs manifest |
| 5 | **MtAgentLens** (eBPF Signal OFF) | runtime-causal-observability | responsibility.db (no TLS) |

### Table 1a: Overall Detection

| Tool | TPR | FPR | Precision | F1 | MCC | Detected/Total |
|------|-----|-----|-----------|----|-----|----------------|
| **MtAgentLens (delivered binary, decision=Unsafe)** | 0.8955 | 0.000 | 1.000 | 0.9449 | 0.8090 | 60/67 |
| LLM Guard | 详见 `compare/results/paper/EXPERIMENTS.md` | — | — | — | — | — |
| Agent Sentinel | 详见 `compare/results/paper/EXPERIMENTS.md` | — | — | — | — | — |
| Meta Prompt Guard | 详见 `compare/results/paper/EXPERIMENTS.md` | — | — | — | — | — |
| MtAgentLens (eBPF Signal OFF) | 详见消融结果 | — | — | — | — | — |

### 预期 Table 1b: Per-Attack-Type Detection

| Tool | Type A TPR | Type B TPR | Type C TPR |
|------|-----------|-----------|-----------|
| **MtAgentLens (eBPF ON)** | — | — | — |
| LLM Guard | — | — | — |
| Vigil | — | — | — |
| Meta Prompt Guard | — | — | — |
| MtAgentLens (eBPF OFF) | 0.000 | 0.000 | 0.000 |

**关键发现**：Type C 是结构性分化点。Prompt 工具 Type C TPR ≈ 0（不是"表现差"，而是每个单独 prompt 都合法）。

### 预期 Table 1c: McNemar Pairwise Tests

| Comparison | b | c | χ² (Yates) | p | Interpretation |
|------------|---|---|------------|-----|----------------|
| MtAgentLens vs LLM Guard | TBD | TBD | TBD | TBD | TBD |
| MtAgentLens vs Vigil | TBD | TBD | TBD | TBD | TBD |
| MtAgentLens vs Meta Prompt | TBD | TBD | TBD | TBD | TBD |
| MtAgentLens vs eBPF OFF | TBD | TBD | TBD | TBD | TBD |

### Bootstrap 95% CI

交付二进制的检测指标报告 Bootstrap 95% CI（BCa，10,000 resamples，固定种子
20261019）：`compare/results/paper/standard_evaluation_v5.json` 的
`rq1_detection.strict` / `rq1_detection.actionable`。当每次重采样结果完全一致
（例如 FPR 0/19）时 BCa 区间退化为 [0, 0]，该文件同时报告 Wilson 95% 区间。

**范围说明（原承诺为“所有 TPR/FPR/F1/MCC”）**：本仓库目前只对从
`responsibility_alerts` 直接读取的二进制裁决（`decision=Unsafe` 与
`severity∈{Warning,Critical}` 两种口径）计算了 CI。评估脚本的 `full_scores`
复合分、以及与其他工具的 McNemar 对比尚未产出 CI 产物；在补产之前，正文
不得声称这些指标已有区间估计。

---

## 6. Experiment 2: Controlled Ablation（RQ2）

### 方法

反事实分析（counterfactual analysis）在已持久化的 SQLite 图数据上执行：

```
Baseline (eBPF ON, 全系统) → 选择性禁用 Layer N 的信号 → 重新计算检测指标
```

**不重跑 collector，不重编译代码。** 通过 DB 查询模拟信号移除。

### 消融矩阵

| ID | 消融层 | 移除的信号 | RQ2 子问题 |
|:---:|--------|-----------|-----------|
| **A0** | eBPF Signal Acquisition | 内核级 TLS + Process 信号 | 内核监控是否是必要条件？ |
| **A1** | Rule Engine | unsafe.* 规则命中 | 规则引擎独立贡献多大？ |
| **A2** | Pattern Matching | GraphPatternMatch | 图拓扑异常检测独立贡献？ |
| **A3** | Progressive Scoring | EWMA + CUSUM drift | 时序评分独立贡献？ |
| **A4** | Observer LLM | LLM 语义验证 → heuristic | Observer 边际贡献？ |
| **A5** | Identity Resolution | Union-Find → single agent | 身份解析对归因的影响？ |
| **A6** | Attribution Pipeline | Claims/Tracebacks removed | 归因层独立价值？ |

### 预期 Table 2a: Ablation Matrix

| Ablation | TPR | Δ TPR | Relative Importance | F1 | Δ F1 | Cohen's d |
|----------|-----|-------|--------------------|----|------|-----------|
| BASELINE | — | — | — | — | — | — |
| A0: eBPF Signal OFF | 0.000 | — | 100% | 0.000 | — | — |
| A1: Rules OFF | — | — | — | — | — | — |
| A2: Patterns OFF | — | — | — | — | — | — |
| A3: Scoring OFF | — | — | — | — | — | — |
| A4: Observer OFF | — | — | — | — | — | — |
| A5: Identity OFF | — | — | — | — | — | — |
| A6: Attribution OFF | — | — | — | — | — | — |

### 预期 Table 2b: Ablation Waterfall（per scenario）

每个场景 × 每个消融的 Unsafe alerts 变化，展示逐层降级路径。

### 关键叙事

**Co-necessity pattern**：
- A0 (eBPF Signal OFF) = 必要条件：无内核信号 → 检测崩溃（Relative Importance = 100%）
- A4 (Observer OFF) = 增强层：Observer 关闭 → Phase 2 fail-closed + DirectUnsafe 保留 ~83% 召回
- A5/A6 = 归因层：检测不受影响（Δ TPR = 0），但归因能力完全丧失

---

## 7. Experiment 3: Attribution Analysis（RQ3）

### 设计

对比 MtAgentLens 的结构化归因输出与所有 baseline 的二元分类输出。

### 预期 Table 3a: Claim Coverage

| Tool | PolicyViolation | TaintedContextUse | Total Claims | Tracebacks |
|------|----------------|-------------------|-------------|------------|
| **MtAgentLens** | X | X | X | X |
| LLM Guard | 0 | 0 | 0 | 0 |
| Vigil | 0 | 0 | 0 | 0 |
| Meta Prompt Guard | 0 | 0 | 0 | 0 |

### 预期 Table 3b: Evidence Level Distribution

| Evidence Level | Count | % | Example |
|---------------|-------|-----|---------|
| L1 (Exact Match) | X | X% | PBC edge with exact file path |
| L2 (Process Tree) | X | X% | Parent-child PID relationship |
| L3 (Temporal) | X | X% | Time-window correlation |
| L4 (TLS Session) | X | X% | Shared TLS session chain |
| L5 (Cross-Layer) | X | X% | Combined behavioral signal |

### 预期 Table 3c: Per-Attack-Type Attribution

| Attack Type | PBC Edges | Tainted Edges | Claims | Agents Identified |
|-------------|----------|---------------|--------|-------------------|
| Type A | X | X | X | X |
| Type B | X | X | X | X |
| Type C | X | X | X | X |

---

## 8. Experiment 4: Performance Benchmarks（RQ4）

### 预期 Table 4: Performance

| Benchmark | Value | Target | Status |
|-----------|-------|--------|--------|
| Process exec overhead | ~68μs | <100μs | ✅ |
| File access overhead | ~3μs | <10μs | ✅ |
| Throughput (1000 pairs) | ~843 ev/s | >500 | ✅ |
| Throughput (3000 pairs) | ~830 ev/s | >500 | ✅ |
| Peak RSS | ~15 MB | <50MB | ✅ |
| First observation | ~6 ms | <10ms | ✅ |
| Observer cost per call | <$0.001 | <$0.01 | ✅ |

---

## 9. Experiment 5: Blind Annotation Quality

### 设计

三名独立标注者（刘周康、唐浩钺、刘锦样）对当前主评估 86 场景进行盲标注。

### 预期 Table 5: Annotation Quality

| Metric | Value | Standard |
|--------|-------|----------|
| Binary label agreement | — | >0.85 |
| Attack type agreement | — | >0.70 |
| Cohen's κ (binary) | — | >0.80 (substantial) |
| Cohen's κ (type) | — | >0.60 (moderate) |
| Adjudicated disagreements | — | <10% |

---

## 10. 工程贡献

### 10.1 Multi-Version BoringSSL Byte-Pattern Matching

解决 Claude Code 自动更新导致 TLS uprobe 周期性失效的问题。

### 10.2 Cost-Optimized LLM Integration

DeepSeek v4-flash + thinking disabled + compact prompt + JSON truncation recovery。每次调用 <$0.001。

### 10.3 eBPF Ring Buffer Optimization

变长事件 + 32MB buffer + 内核侧 comm 过滤。Drops: 850K→100K (8.5x)。

---

## 11. 声明边界

### 支持的主张
- 86 场景 frozen MtAgentLens 离线评估（RQ1）
- Prompt baseline 在全部 86 场景 prompt_inputs 上的结果（RQ1）
- 反事实特征消融矩阵（RQ2）
- 结构化归因 vs 二元分类的质变（RQ3）
- 本地性能微基准（RQ4）
- 3 个工程贡献

### 不支持的主张
- 外部基准泛化
- 实时/online 端到端稳定性
- 生产环境延迟
- 超出主评估 86 场景的扩展/灰区归档泛化结论

---

## 12. 复现命令

```bash
# 构建
make build-bpf && cd collector && cargo build --release --bin mtagentlens

# 单场景采集（eBPF ON）
echo "1" | sudo -S bash -c 'MTAGENTLENS_SUDO_PASSWORD=1 \
  ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic \
  ANTHROPIC_AUTH_TOKEN=<token> MODEL=deepseek-v4-flash \
  PYTHONPATH=env/src:. python3 -m mtagentlens_benchmark.cli run \
  --scenario <id> --mode process --collector'

# 单场景采集（eBPF Signal OFF — 消融对照）
echo "1" | sudo -S bash -c 'MTAGENTLENS_SUDO_PASSWORD=1 \
  PYTHONPATH=env/src:. python3 -m mtagentlens_benchmark.cli run \
  --scenario <id> --mode process --collector -- --ssl=false --process=false'

# Prompt baseline 评估
PYTHONPATH=env:env/src:. python3 compare/scripts/run/run_llm_guards_direct.py

# 完整评估（所有消融在 DB 数据上执行）
PYTHONPATH=env:env/src:. python3 compare/scripts/build/build_paper_evaluation_report.py

# 性能
MTAGENTLENS_SUDO_PASSWORD=1 PYTHONDONTWRITEBYTECODE=1 \
  python3 compare/scripts/perf/build_performance_results.py --skip-build
```

---

*文档路径: `docs/design/experiment-design.md`*
*取代: v4 (2026-06-13), v3 (2026-06-13), v2 (2026-06-10), v1 (2026-06-04)*
