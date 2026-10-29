# E5 身份归属微基准（合成拓扑，离线完成）

> 回应审稿意见 2 前半："不同智能体共用进程、连接复用或并发执行时可能发生错误合并……
> 建议明确子智能体边界的识别条件，并量化身份归属准确率。"
>
> 状态：**已完成**（不依赖模型/额度）。产物：
> `local/workpackages/2026-10-25-e5-identity/`
> - `traces.jsonl` sha256 `78ed44fd8f001a0497e466460083291fac4e1aa2307803cdaa8be4be36e1fcfa`
> - `summary.json` sha256 `3deb585b0626f12f71b76495eda7a8c11e8f7c8379077cedf024824962733f57`
>
> **注**：T4 合并缺陷、T2/T3 的会话级归属均已修复（见 §3），上面的 hash 是**全部修复后**重跑产物。

## 1. 方法

- **生成器**：`collector/examples/identity_microbenchmark.rs`，按
  [`docs/paper/身份解析.md`](../paper/身份解析.md) 的 T1–T6 定义构造合成信号图
  （进程树、TLS 参与者、conversation、显式委派），每个拓扑 **30 条轨迹**，共 180 条。
- **被测实现**：真实 `IdentityResolver`（`resolve_provisional` + `resolve_authoritative_with_delegations`）。
- **真值（GT）**：由规格表**预先声明**（不是从输出反推）：
  T1 全部合并；T2 在会话切换处分裂；T3 分裂或标记 unattributed；
  T4 连接复用不得合并；T5 在 exec+独立 TLS 处分裂；T6 委派保持两个 agent。
- **评估器**：`scripts/evaluation/e5_identity_microbenchmark.py`，复用人工一致性同一套
  pairwise/B³ 定义（`compare/tools/identity_metrics.py`）；**unattributed 事件按单例计分**，
  所以"拒绝合并"不会被误算为错误合并。
- 单测：`compare/tests/test_e5_identity_microbenchmark.py`（7 项）。

## 2. 结果（180 轨迹 / 720 事件）

| 拓扑 | 规格期望 | pairwise F1 | 错误合并簇 | split 簇 | unattributed | 判定 |
|---|---|---:|---:|---:|---:|---|
| T1 单智能体 | 全部合并 | **1.000** | 0 | 0 | 0% | ✅ |
| T2 同进程顺序 | 切换点分裂 | **1.000**（修复前 0.500 / 弃权） | 0 | 0 | 0% | ✅ 会话级分裂 |
| T3 同进程并发 | 分裂或 unattributed | **1.000**（修复前 0.500 / 弃权） | 0 | 0 | 0% | ✅ 会话级分裂 |
| T4 连接复用 | 不得合并 | **1.000**（修复前 0.500） | 0 | 0 | 0% | ✅ 修复 |
| T5 子智能体分叉 | exec+独立 TLS 分裂 | **1.000** | 0 | 0 | 0% | ✅ |
| T6 委派交接 | 保持两个 agent | **1.000** | 0 | 0 | 0% | ✅ |

整体：180 条轨迹 / 720 个事件，**unattributed 0%**，**所有拓扑 wrong-merge = 0、split = 0**，
六个拓扑 pairwise F1 全部 **1.000**。

**指标读法（重要）**：F1 必须与 **wrong-merge 率与覆盖（unattributed 率）**一起报告，
不能只报 F1。修复前 T2/T3 的 pairwise F1 = 0 是**弃权**（预测把同一会话流的所有事件留作
unattributed 单例，没有"预测同簇"正样本），不是"更差"；当前修复后六拓扑 F1=1.000、
unattributed 0%，两者不矛盾。若未来改用更细事件粒度重新出现弃权，仍按此读法解释。

## 3. 结论（诚实、可直接写进论文）

1. **边界判据有效的两类**：子智能体分叉（独立 TLS + agent comm）与显式委派（禁止合并）
   都达到了规格要求（F1 = 1.000）；单智能体的工具子进程也能正确合并。
2. **一个已修复的缺陷**：
   - **T4（连接复用）**：原 S2 规则"共享 TLS 即合并"把两个不同 agent 合并（30/30）。
     修复：共享 TLS 仅在**会话证据不冲突**时合并——双方各有非空且互不相交的
     conversation id 时禁止合并（`union_find.rs` S2；单测
     `shared_tls_with_disjoint_conversations_does_not_merge_agents` 与
     `processes_without_conversations_still_merge_on_a_shared_tls_session`）。
     修复后 T4 pairwise F1 **0.500 → 1.000**，错误合并 **30/30 → 0/30**，
     既有 973 个 lib 测试无回归。
3. **原缺陷（已修复）**：修复前 T2/T3（同进程多会话）把两个会话合并成同一 agent
   （30/30 轨迹，错误合并率 100%，pairwise F1 0.500/弃权）。该缺陷由下面第 4、5 项
   修复关闭；当前 artifact（`local/workpackages/2026-10-25-e5-identity/summary.json`，
   sha256 `3deb585b0626f12f71b76495eda7a8c11e8f7c8379077cedf024824962733f57`）
   重跑结果为六拓扑 pairwise F1 = **1.000**、wrong-merge = 0、unattributed = 0%，
   与 §2 一致。修复前的失败值只用于说明前后差异，**不得写成当前结果**，也不得再写
   "仍未解决 / 错误合并 100%"。
4. 本轮第二个修复：**单进程多会话不再被静默合并**。`union_find.rs` 在组件分配后检测
   "同一进程携带 >1 个 conversation" 的组件，将其整体退出 assignments 并以
   `UnresolvedReason::AmbiguousMultipleSessions` 记入 unresolved（下游 provider 返回
   None → 失败关闭）。单测
   `one_process_with_two_conversations_is_ambiguous_not_merged`、
   `one_process_with_one_conversation_stays_assigned`。效果：T2/T3 错误合并
   **30/30 → 0/30**，代价是整体 unattributed 从 0% 升到 **33.3%**（只出现在 T2/T3）。
5. 第三个修复：**会话级身份**。多会话进程不再只是"整体弃权"：
   `AuthoritativeResolution.conversation_agents` 为每个 `(process, conversation)`
   派生独立 agent id（`derive_conversation_agent_id`），消息/工具调用事件按
   conversation 归属；**动作事件仍把该进程视为不可归属（失败关闭）**——动作没有
   conversation 字段，无法安全地二选一。
   单测：`multi_session_process_gets_one_conversation_agent_per_session`、
   `single_session_process_has_no_conversation_agents`。效果：T2/T3 的 pairwise F1
   **0.000（弃权）→ 1.000**，整体 unattributed **33.3% → 0%**。
6. 因此论文可写："六类边界条件下事件级归属 F1 = 1.000；其中**同进程多会话通过会话级
   身份分裂**，但其**动作级**归属保持未指定（失败关闭），不声称动作可归因。"

## 4. 效度边界（必须同时报告）

- 合成拓扑只保留身份相关信号（进程树/TLS/conversation/委派），不含真实 agent 的全部噪声；
  它测的是**判据实现是否符合规格**，不是端到端身份准确率。
- 事件抽象为"每个 actor 2 个事件"（720 事件），用于让 pairwise 指标有正样本；
  不同事件粒度会改变指标绝对值，但不会改变"合并/分裂"的方向。
- E6（23 个真实 Type C 轨迹上的跨机归属）仍需模型额度恢复后执行；
  本微基准不能替代 E6。
