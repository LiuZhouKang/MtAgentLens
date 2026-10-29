# AttributionBench（审稿意见 2b）：当前 not_estimable

**结论：本 revision 不做 2b 的 3-reviewer 标注。** 不是标注预算问题，而是 detector 侧没有可打分的预测。

## 证据（93 条 trace，27,175 claims）

| 字段 | 非空条数 |
|---|---:|
| `root_groups` | 0 |
| `unique_entry` | 0 |
| `ranked_agents` | 0 |
| `source_entry_candidates` | 1 |
| `executors` | 917 |
| `edges` / `occurrences` | 10,921 |

`AttributionResult` 结构只有 correlation edges / occurrences / executors，没有 root step、ordered chain 字段。即使 3 reviewers 标出 root/entry/chain，也没有预测可计算 Root@1 / Entry / Chain；只能报 coverage=0。

## 为什么重跑场景不能修复

root 需要“observed file producer/writer”证据；campaign 的 claim 大多是 intent→action correlation，没有 writer 证据。重跑同一 fixture 不会产生新的语义输出，只会重复同样的空 root。这是 detector 能力缺口（W5），不是运行/标注问题。

## 建议的 limitation 原文

> “AttributionBench is not estimable in this revision: across 27,175 claims the detector emitted 0 root groups, 0 unique entries and 0 ranked agents, and the result schema has no root-step/ordered-chain field. Re-running scenarios cannot create these missing semantic outputs, so we report attribution as a capability gap and future work rather than an accuracy result.”

## 未来工作

1. 实现 W5：从 evidence path / handoff / intent 链产出 root candidate、root step、entry、ordered partial DAG。
2. 在 30 multi 上离线验证非空（不需要重跑场景）。
3. 再用同一套 3 fresh reviewers + adjudicator 标注并计算 T6。

## 交接

- 交付清单：[`MANIFEST.md`](MANIFEST.md)
- 原始数据：[`data/`](data/)（93 JSON，58 MB）；说明见 [`data/README.md`](data/README.md)。
