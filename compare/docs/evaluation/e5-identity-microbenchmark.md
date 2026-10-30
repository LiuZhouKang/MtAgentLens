# E5 合成身份微基准：历史报告

> 回应审稿意见 2 前半："不同智能体共用进程、连接复用或并发执行时可能发生错误合并……
> 建议明确子智能体边界的识别条件，并量化身份归属准确率。"
>
> 本报告记录 2026-10-25 的合成规格检查。原工作包 `local/workpackages/2026-10-25-e5-identity/` 已不存在；以下哈希仅标识历史产物，本次未重新验证其字节或重跑微基准。
> - `traces.jsonl` sha256 `78ed44fd8f001a0497e466460083291fac4e1aa2307803cdaa8be4be36e1fcfa`
> - `summary.json` sha256 `3deb585b0626f12f71b76495eda7a8c11e8f7c8379077cedf024824962733f57`
>
> 当前真实身份评价见 [IdentityBench](../../results/identity/README.md)；下表保留原测量结果，不作为当前新构建的重新测量。

## 1. 方法

- **生成器**：`collector/examples/identity_microbenchmark.rs`，按本页 T1–T6 规格表构造合成信号图
  （进程树、TLS 参与者、conversation、显式委派），每个拓扑 **30 条轨迹**，共 180 条。
- **被测实现**：真实 `IdentityResolver`（`resolve_provisional` + `resolve_authoritative_with_delegations`）。
- **真值（GT）**：由规格表**预先声明**（不是从输出反推）：
  T1 全部合并；T2 在会话切换处分裂；T3 分裂或标记 unattributed；
  T4 连接复用不得合并；T5 在 exec+独立 TLS 处分裂；T6 委派保持两个 agent。
- **评估器**：`scripts/evaluation/e5_identity_microbenchmark.py`，复用人工一致性同一套
  pairwise/B³ 定义（`compare/tools/identity_metrics.py`）；**unattributed 事件按单例计分**，
  所以"拒绝合并"不会被误算为错误合并。
- 单测：`compare/tests/test_e5_identity_microbenchmark.py`（7 项）。

## 2. 历史结果（180 轨迹 / 720 事件）

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

## 3. 实现规则与复核入口

该实验检查预声明拓扑中的合并/分离规则：单 Agent 的工具子进程可以合并；不相交会话约束共享连接的身份融合；明确委派保持主体区分。同进程多会话的消息可有会话级身份，缺少 conversation 的动作仍保留未赋值。

生成器位于 [identity_microbenchmark.rs](../../../collector/examples/identity_microbenchmark.rs)，评分入口为 [e5_identity_microbenchmark.py](../../../scripts/evaluation/e5_identity_microbenchmark.py)，指标由 [identity_metrics.py](../../tools/identity_metrics.py)定义。实现回归覆盖见 `compare/tests/test_e5_identity_microbenchmark.py` 及 identity 模块测试。重新生成时应另存输入、配置与构建身份，不覆盖本报告冒充同一批结果。

## 4. 解释范围

- 合成拓扑只保留身份相关信号（进程树/TLS/conversation/委派），不含真实 agent 的全部噪声；
  它测的是**判据实现是否符合规格**，不是端到端身份准确率。
- 事件抽象为"每个 actor 2 个事件"（720 事件），用于让 pairwise 指标有正样本；
  不同事件粒度会改变指标绝对值，但不会改变"合并/分裂"的方向。
- 当前 IdentityBench 已交付 30 个多 Agent 场景的选择性赋值评价；[AttributionBench](../../results/attribution/README.md)另标记为 `not_estimable`。两者与本合成规格检查分别报告。
