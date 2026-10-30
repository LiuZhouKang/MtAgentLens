# 分诊评分与裁决边界

本文说明当前 Rust 实现。分数是排序与路由信号，不是攻击概率，也不单独决定最终违规。过去的 v8 实施计划、86 案例结果和历史消融工具不作为此实现的验收依据。

## 从事实到分数

入口是 [classification/scoring.rs](../../collector/src/responsibility/classification/scoring.rs) 的 `compute_score_parts_with()`。它接收规则命中、图模式、责任图、关联证据、重复次数、意图数及运行时信号，并返回可审计的 `raw`、`calibrated`、`gate`、`final_score`。

| 维度 | 输入语义 | 默认权重 |
|---|---|---|
| D1 | `max(boundary, rule)`，边界与规则信号 | 0.28 |
| D2 | 图模式匹配；没有外部内容模型注入此维度 | 0.25 |
| D3 | 角色及行为放大信号 | 0.20 |
| D4 | 边界重复次数及饱和参数 | 0.10 |
| D5 | 关联证据质量 | 0.10 |
| D6 | 有运行时基线偏离值时使用该值，否则使用行为证据回退 | 0.07 |

`ScoreSignals` 还携带 CUSUM、渐进融合与触发原因等诊断。不能因为这些字段被保存，就推断它们全部直接参与上述六维公式；以 `compute_score_parts_with()` 的数据读取为准。

## 融合、压缩与证据门

[scoring/fusion.rs](../../collector/src/responsibility/scoring/fusion.rs) 的当前融合为：

1. 对六维值按声明权重求归一化线性组合。
2. 对值大于 `0.01` 的活跃维度求归一化加权几何平均；几何项使用 `EPS=0.01`。
3. 有活跃维度时，两项各占一半；无活跃维度时直接返回线性项。
4. 应用 `sigmoid(a × raw + b)`，默认 `a=7`、`b=-3`。
5. 乘以证据门并限制到 1 以内，得到最终分诊分数。

几何平均只覆盖活跃维度，不能描述成“必须六维同时出现”。`compute_owa()` 虽仍存在，但不在 `fuse_dimensions()` 的当前调用链。源码沿用 `platt`、`calibrated` 字段名；默认参数本身不能证明做过概率校准或在新数据上拟合。

证据门的输入是图中 `PolicyBoundaryCrossing` 边数。存在 Unsafe 规则命中时门值为 1；其余情况为：

```text
gate = sigmoid((pbc_count - gate_center) / gate_slope)
final_score = sigmoid(a × raw + b) × gate
```

默认 `gate_center=10`、`gate_slope=2`。它是连续门，不是“PBC 不足 10 就硬截为 0.1”。重复和行为回退的饱和分母默认均为 50。`ScoringConfig::validate()` 检查权重和门参数等约束；实验参数必须连同配置记录，不能把修改参数后的结果冒充原冻结结果。

## 路由不等于最终结论

`route_score()` 默认使用以下区间；阈值属于 `ClassificationConfig`：

| 分数 | 路由名称 |
|---|---|
| `< 0.15` | `DirectSafe` |
| `0.15..=0.70` | `ToObserver` |
| `> 0.70` | `DirectUnsafe` |

这些名称保留了早期接口。实际运行还要处理硬规则、候选、Observer 复核和证据作用域，不能从名称推导运行的最终 decision。

[final_decision.rs](../../collector/src/responsibility/classification/final_decision.rs) 负责最终证据聚合：

- 已确认动作上的硬违规优先于语义意见。
- Observer 的 Unsafe 需要引用证据；按候选 ID/version 绑定复核，避免不同对象的 Safe/Unsafe 相互抵消。
- 未复核候选、复核失败或矛盾可保留 `Uncertain`；图模式的 advisory 不确定性另有专门处理。
- `enforce_score_review()` 将高分但缺乏证据的 Safe 提升为复核，不会仅凭高分生成 Unsafe。
- `enforce_advisory_pattern_review()` 在分数和行为条件满足时保留 advisory 复核要求。

最终 `decision`、`authority`、`severity` 与分诊分数必须分别报告。可行动口径及上下文授权见 [ADR-0015](../adr/0015-alert-actionability-and-taint-authorization.md)。

## 在线、回放与修改入口

[ResponsibilityRuntime](../../collector/src/responsibility/runtime/runtime.rs) 在线维护信号并在结束时构造 [AnalysisCheckpoint](../../collector/src/responsibility/replay/checkpoint.rs)。最终分析与敏感性回放共享 checkpoint 的评价实现；checkpoint 保存身份策略和分析输入，并检查契约/实现指纹。

新增维度或修改融合时，应同时检查 `ScoringConfig`、`ScoreSignals`、持久化 breakdown、checkpoint 评价与敏感性参数传递。只改 Python 历史评分副本不能改变产品裁决。回归检查应覆盖区间端点、规则证据、未复核候选及在线/回放一致性；统计效果必须来自绑定输入和二进制的实验，见 [当前证据入口](../evaluation/CURRENT-EVIDENCE.md)。
