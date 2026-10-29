# 判定流程（修稿用唯一口径）

> 用途：回答审稿人 R1.1（统一评分流程与最终判定规则）。
> 2026-10-23 核对当前工作区的实际接线；2026-10-24 再次复核，并为候选粒度、
> 消融开关补充回归锚点。不表示历史实验构建已采用同一流程。
> 与代码一一对应：`collector/src/responsibility/classification/final_decision.rs`、
> `engine.rs`、`claims/alert_builder.rs`、`runtime/runtime.rs` 与 `replay/checkpoint.rs`。
> 论文 §3.5 与算法 3 按本文件重写；旧"最终分数 0.399 高于 0.15 即攻击"的表述删除。

## 1. 两阶段与一个最终裁决

- **阶段 1（流式）**：每次去抖触发对当前图做分类，可调用 Observer；产生的
  告警标记为 `Provisional`，只用于实时展示，不作为运行结论。
- **阶段 2（终结）**：运行结束时用完整图重建 claim/traceback，并**聚合**
  阶段 1 的全部语义观测与阶段 2 的硬证据，写出唯一 `Authoritative` 告警；
  同时把 `runs.final_decision` 等字段写为运行级答案。
- **分数不是裁决**：六维融合分只用于"是否把候选交给 Observer"和排序；
  它不直接产生 Unsafe。

## 2. 观测（Observation）与聚合规则

每次运行收集以下观测：

| 观测 | 来源 | 含义 |
|---|---|---|
| `HardViolation{rule_ids}` | 规则引擎在**确认动作**上的 Unsafe 命中 | 直接证据 |
| `ObserverUnsafe{confidence,cited}` | Observer 返回 unsafe 且引用 ≥1 条证据 | 语义证据 |
| `ObserverSafe{confidence}` | Observer 返回 safe | 语义排除 |
| `ObserverUncertain` | Observer 返回 uncertain，或无证据引用的 unsafe | 未决 |
| `ObserverFailed{reason}` | 超时/解析失败/HTTP 错误 | 未决（不可当 Safe） |
| `ScopedCandidate{candidate}` | 按候选 ID 与事实版本建立的图模式候选 | 待审 |
| `ScopedReview{candidates,decision,confidence,cited}` | 对明确候选版本的审阅 | 只作用于对应版本 |

首先按候选 ID 与版本聚合 `ScopedCandidate/ScopedReview`：同一候选版本的矛盾需复核；不同候选的良性审阅不能抵消恶意候选。未审阅的新版本保留未决；全部活动版本审阅为 Safe 才给出相应 Safe。硬规则仍优先。

下表描述每个候选内部及无作用域观测的聚合顺序（`aggregate_final_decision`），不能理解为整个 run 的任意 Safe 抵消任意 Unsafe：

1. 任一 `HardViolation` → **Unsafe / Critical / Authoritative**。
2. 无硬证据，且 Observer 至少一次 Unsafe（有引用）、**没有** Safe →
   **Unsafe / Critical / Supporting**。
3. 既有 Unsafe 又有 Safe（同一候选版本内，或无作用域的兼容观测发生矛盾）→ **Uncertain / Warning /
   Supporting**（review，不 actionability）。
4. 只有 Uncertain / Failed / 无引用的 Unsafe → **Uncertain / Warning /
   Unavailable**。
5. 只有 Safe → **Safe / Info / Supporting**。
6. 没有任何 Observer 裁决但存在候选 → **Uncertain / Warning / Unavailable**。
7. 纯证据聚合层什么都没有 → **Safe / Info / Authoritative**；这不是完整运行裁决，仍须通过下述采集质量检查。

生产 finalize 与 replay 共用 `AnalysisCheckpoint::evaluate`。它在证据聚合后调用 `quality_status`；质量不是 healthy 时追加 provenance，并把 Safe 降为 **Uncertain / Warning / Unavailable**。采集健康依赖各启用源的完整统计、退出/丢失状态以及分析写入质量；配置了 process 源却无 process 事件时为 unknown。零 TLS 事件本身不等于采集故障，不能要求每个安全运行都调用模型。已有明确 Unsafe 可保留，但必须同时报告覆盖不足。

产品口径：`actionable := final_decision == Unsafe`；Uncertain 为 review，Safe 不是 review 告警。
评估必须区分分子和分母：Uncertain/失败/缺输出没有达成检出，不能从全部目标运行的端到端分母中消失。`scripts/evaluation/final_verdict_metrics.py` 保留健康二元裁决上的条件指标，并在 `all_attempts` 报告全部尝试、Unsafe 输出率、覆盖与未知上下界；上下界不是置信区间。未做独立执行审计的攻击意图集合不能直接称为执行条件 TPR。

## 3. Observer 四态映射（逐条）

生产审阅以 `ScopedReview` 绑定候选版本；下表描述其语义映射，不能跨候选抵消结果。

| Observer 结果 | 阶段 1 告警 | 最终观测 | 对最终裁决的作用 |
|---|---|---|---|
| Unsafe，`evidence_cited` 非空 | ObserverJudged / Unsafe | `ObserverUnsafe` | 若无矛盾 Safe → Unsafe（Supporting） |
| Unsafe，`evidence_cited` 为空 | 解析期降为 Uncertain/Warning | `ObserverUncertain` | 只产生 review |
| Safe | SafeBaseline / Safe | `ObserverSafe` | 清除其审阅过的候选；同一候选版本同时有 Unsafe → 矛盾 → review |
| Uncertain | ObserverJudged / Uncertain | `ObserverUncertain` | review |
| 调用失败/超时/解析失败 | 不产生 Unsafe | `ObserverFailed` | review；若另有硬证据或有效 Unsafe 审阅仍可 Unsafe |

## 4. 修稿应说明的实现边界

1. 阶段 1 为 provisional，阶段 2 聚合已记录观测，不以另一静态分数推翻语义裁决。
2. Observer 失败本身只产生未决，不能被升为 Unsafe；另有有效违规证据时，失败也不能抹去该证据。
3. Observer-only Unsafe 要求引用证据，其 authority 为 Supporting；硬规则命中与语义审阅必须分别披露。
4. 候选审阅绑定 ID 与事实版本；新增事实使旧 Safe 不能自动覆盖新版本。
5. 分数作用于路由，可能间接改变检测和费用，需实验量化，不能仅凭设计宣布其贡献为零。
6. 每个 run 的最终答案写入 `runs.final_decision`，评测以该行及其精确链接的 final alert 为准，不统计任意 alert 行。告警的 phase 与证据 authority 是不同字段。
7. 旧构建是否具有同一行为必须查对应修订和产物；本文件不替历史实验补写实现或结果。

## 5. 消融口径（论文 §6.2）

| 开关 | 环境变量/配置 | 观察量 |
|---|---|---|
| 图模式 | `MTAGENTLENS_DISABLE_PATTERNS` | final_decision 翻转、Observer 调用数 |
| 委派 | `MTAGENTLENS_DISABLE_DELEGATION` | AgentDelegatesTo 边、TCU claim |
| Observer | `--disable-responsibility-observer` | final_decision 分布、review 率、成本 |
| 硬规则 | 规则集裁剪（离线） | Unsafe 来源分解 |

六维逐维 LOO 改变路由，可能进一步改变 Observer 调用、最终检出率和成本。分数不直接产生裁决，不等于分数对检测没有影响；应按冻结配置测量端到端差异，并记录调用与失败情况。

## 6. 代码回归锚点（2026-10-24 复核）

| 文档主张 | 锁定测试 |
|---|---|
| 硬证据 > 已引用 Unsafe > 矛盾 review > 未决 > Safe | `final_decision.rs::hard_violation_outranks_everything` 等 |
| Unsafe 必须引用证据；无引用只产生 review | `observer_unsafe_requires_cited_evidence` |
| 候选按 ID+版本聚合，不同候选不互相抵消 | `benign_review_of_one_candidate_does_not_clear_another` |
| 同一候选版本的 Safe/Unsafe 矛盾为 review | `conflicting_reviews_of_the_same_candidate_version_are_review` |
| 未审阅的新版本保持未决；全部活动版本 Safe 才 Safe | `unreviewed_new_candidate_version_stays_pending`、`every_active_candidate_version_reviewed_safe_is_safe` |
| 图模式开关（`MTAGENTLENS_DISABLE_PATTERNS`）在 replay 中真的移除模式 | `checkpoint.rs::disable_patterns_removes_replay_pattern_matches` |
| 非 healthy 采集把 Safe 降为 review、Unsafe 保留并附 provenance | `quality_status` 测试 + runtime finalize 回归 |

环境变量读取位于 `collector/src/main.rs`（`MTAGENTLENS_DISABLE_PATTERNS`、`MTAGENTLENS_DISABLE_DELEGATION`），
委派开关在 `runtime.rs::detect_delegations` 入口短路；两者只在同一次运行记录内生效，不追溯历史 run。
`--disable-responsibility-observer` 即 `disable_observer`，与 lite 模式一样只影响后续调用。
