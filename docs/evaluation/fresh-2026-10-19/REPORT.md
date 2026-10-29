# 冻结二进制小样本报告（2026-10-19）

> 单一冻结二进制 `2dfe82dd05ff7e4b131d39e346ed9d824245eba9f3da087fe0b9c0970ecc6865`
> （release，含 P1.1/P1.2 + 实时链路/评测修复）。本报告是**小样本**，不是最终
> 论文表；全量扩展前需用户确认预算与时间。

## 1. 协议

- 二进制：`collector/build/release/mtagentlens`，测量 SHA 见 `binary.sha256`
  （`2dfe82dd…`）。测量后仅合入一处路径穿越防护（`match_run_route` 拒绝
  `.`/`..`），当前源码二进制为 `ca197ef0…`（`current-source.sha256`）；
  详见 `BINARY-NOTE.md`。**最终论文数字必须用最终冻结二进制重测。**
- 隔离：`fpr_runner` 的 clean profile + inode 独立 agent 副本 + 唯一 comm。
- 良性集：`env/scenarios/benign/*.json`（20 个）。
- 观察口径：`observed=true`（有 intents/actions/evidence）才进入分母。
- actionable（ADR-0015）：`decision=Unsafe`；Warning+ 只作 review 信号。
- 每个 run 的 DB/scenario SHA 见 `fpr/provenance.json`。

## 2. 结果

### 2.1 良性 FPR（20 场景）

| 指标 | 值 |
|---|---|
| 完成 / 观察 | 20 / 20 |
| Warning+（review） | 0 / 20 |
| **Actionable（Unsafe）** | **0 / 20** |
| Wilson 95% CI | 0.00% – 16.11% |
| 告警 decision 分布 | `{Safe: 20}` |
| 全部 display profile | `SafeBaseline` |
| 每场景 claims | 0 |

证据：`fpr/fpr-frozen.md`、`fpr/fpr-frozen.json`、`fpr/runs/results.jsonl`、
`fpr/provenance.json`。

### 2.2 活动量混杂（R2-b 关键负结果）

| 集合 | PBC n | min | median | max |
|---|---:|---:|---:|---:|
| 历史攻击（1.1.0 语料） | 67 | 0 | 220 | 2124 |
| 历史良性（1.1.0 语料） | 19 | 0 | 4 | 9 |
| **本次冻结良性（2dfe）** | 20 | 0 | **0** | **0** |

- 历史攻击 IQR = 78–419；**没有任何良性 run 落在该区间**。
- 本次良性即使有 470 个 actions，PBC 仍为 0。
- **结论**：当前语料无法排除“检测到的是 PBC 存在/活动结构，而非恶意”。
  必须新建 ≥10 个产生真实边界穿越（敏感路径、跨 Agent 共享、委派）的困难
  良性任务，并在 PBC 匹配子集上报告 actionable FPR；否则论文不得声称区分
  恶意与良性。
- 证据：`fpr/benign-pbc.json`、`docs/evaluation/pbc-distribution-2026-10-19.json`。

### 2.3 live 延迟（3 个真实会话）

| 阶段 | p50 | p95 | p99 |
|---|---:|---:|---:|
| `route_event`（单事件路由） | 220 µs | 397 µs | 620 µs |
| `trigger`（去抖分类/图/claim） | 595 ms | 1,421 ms | 1,421 ms |
| `event_to_alert` | — | — | —（0 样本：良性无告警） |

- **修复后测量**（二进制 `5aaff2d7…`，去抖 `trigger` 边界持久化修复）：
  `/home/lzk/MtAgentLens/docs/evaluation/fresh-2026-10-19/live-latency-fixed.json`。
- 修复前（`2dfe…`）为 route p50 253µs / p95 676µs / p99 1,702µs；
  `live-latency.json` 保留为对照。
- trigger 每 50 事件或 2s 去抖触发一次。
- **告警时延上界**（本次可写）：去抖（≤2s）+ trigger（p95 ~1.42s）+ SSE。
- 局限：良性运行不产生告警，`event_to_alert` 无样本；需 attack/handoff 场景。
- 3 次修复后运行均 `flushed cleanly (0 dropped)`。

### 2.4 持久化背压（P0，已定位并修复）

- 修复前：live `trace --process true` 约 2 分钟丢弃 **404,734–1,895,737** 条
  记录；首批 20 条均为 `provisional_identity`。
- 根因：`route_event` 每个事件都重写 provisional identity / identity mapping。
- 修复：移到去抖 `trigger` 边界（每 2s 或 50 事件）。
- 修复后：20s live 复测 **0 dropped**；3 次 latency run 均
  `flushed cleanly (0 dropped, 0 failures, 0 closed)`。
- 影响：修复前的 live DB 在事件洪峰下不完整；FPR 的 20 次运行中仅
  `benign-heavy-build` 出现 2,785 条 provisional-identity 丢弃，告警/decision
  行未受影响（丢弃类型为 identity 表）。

### 2.5 构念效度（action trace，历史语料）

- 124 个 ground-truth 场景中：86 有 trace，68 可检查；**严格口径 29 个**有
  预期动作证据（宽松 52），**25 个未执行**，32 个不适用（良性/行为型）。
  严格口径只认敏感路径或复合模式；`env/cat/ls/curl` 这类裸命令不算执行证据。
  这意味着旧语料的攻击执行率本身不足，召回分母必须按“实际执行”收缩。
- 证据：`action-trace-audit.json`。

## 3. 这些结果能证明什么

- 冻结二进制在 20 个良性场景上 **actionable FPR = 0/20**，与 c53d 基线一致；
  统计上界仍宽（16.11%）。
- 实时链路在真实 `record/trace` 进程上可被前端消费（REST + SSE + 内嵌页面），
  见 `scripts/e2e/live-server-smoke.sh`；浏览器实拍见
  `screenshots/home.png`、`screenshots/console-live.png`
  （live 徽标、实时告警流、原始事件入口；无 404/409）。
- live 持久化在修复后 0 丢弃（20s 与 3 次 20–40s run）。
- 单事件路由开销在亚毫秒级；分类/图管线是主要延迟来源（~0.6–1.1s/次）。

## 4. 不能证明什么（不得写进论文）

- 不能证明当前二进制对攻击的 actionable 召回（未重采 ≥50 attack）。
- 不能证明检测区分“恶意 vs 活动量”（PBC 混杂，见 §2.2）。
- 不能报告 `event_to_alert` 分位（0 样本）。
- 不能报告 lite 开销/CPU/RSS（未跑 5+5）。
- 不能把历史 60/67 当成 2dfe 的结果。
