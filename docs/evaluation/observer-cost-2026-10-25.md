# Observer 调用次数与运行时长（fix2，离线统计）

> **2026-10-28 更新**：本文是 fix2 历史离线统计，不包含逐调用 usage/费用，不再作为意见 5 的当前费用证据。当前完整开销实验（36/36 条件，含真实 Observer usage 与费用上界）见[实验总方案 §6.6](EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#66-实测结果2026-10-28-完成)。
>
> 回应审稿意见 5（"完整开销"）的**可测量部分**：Observer 调用次数、运行时长。
> 明确**不测**：每次调用的 token / 金额——campaign 账本没有逐调用 usage 记录，
> 因此本文**不给出费用数字**，也不把 Claude Code 自身 API 的 usage 日志当作 Observer 成本。

## 1. 方法

```bash
env/.venv/bin/python scripts/evaluation/observer_cost_report.py \
  --results benign=local/workpackages/2026-10-25-rc-fix2/benign/results.jsonl \
  --results attack=local/workpackages/2026-10-25-rc-fix2/attack/results.jsonl \
  --out local/workpackages/2026-10-25-observer-cost/report.json
```

- 输入：RC fix2 campaign 账本的 `observer_calls` 与 `duration_seconds`；
- **无裁决的运行被排除**（`final_decision=null` 或 `state=failed`），并单列
  `excluded_without_verdict`；不把它们计入分母；
- 百分位用 nearest-rank（p50/p95 可复算，函数有单测）。

## 2. 结果（RC fix2）

| arm | runs | Observer 调用总数 | mean | p50 | p95 | max | 零调用 run |
|---|---:|---:|---:|---:|---:|---:|---:|
| benign | 19 | 69 | 3.63 | 1 | 36 | 36 | 6 |
| attack | 43 | 143 | 3.33 | 1 | 14 | 16 | 12 |

| arm | 时长 mean | p50 | p95 | max |
|---|---:|---:|---:|---:|
| benign | 49.4 s | 40.3 s | 185.7 s | 185.7 s |
| attack | 47.2 s | 37.8 s | 94.8 s | 103.3 s |

- attack 臂 44 条账本中 **1 条无裁决**（`state=failed`, `evaluation_status=unknown_verdict`），
  已排除并单列；
- 账本字段名是 `observer_calls`，语义为"对该 run 发起的 Observer 调用次数"；不区分
  单次调用内的两段式 R-Judge/observer 协议步数。

## 3. 结论（可写进回复）

1. 在本机/本语料上，Observer 调用是**稀疏**的：p50 仅 1 次、且 6/19 与 12/43 的运行
   完全没有调用；调用集中在大批量候选的运行（benign p95=36）。
2. 运行墙钟时长中位数约 38–40 s，p95 低于 190 s；这是**端到端 run 时长**，不是单次
   Observer 延迟。
3. **不能写费用**：没有逐调用 token/价格记录；若要报成本，需要 Observer 侧记录 usage
   （下一轮改进项，不与本报告混用）。

## 4. 边界

- 只覆盖 process 模式（与 fix2 的指标口径一致）；tmux-live 未进指标。
- 数字来自 fix2 口径；**RC fix4 未重测**该项（fix4 的 Observer 调用数见其 metrics/RC README），
  论文引用这些延迟/调用统计时必须注明来源 campaign。
- 报告产物哈希：
  `07a0971612065aff651d5f32279ff20e600778c9bee51cbf44b289a47405840a  report.json`。

## 5. 后续（若审稿人坚持要"完整成本"）

1. 在 Observer 客户端记录每次调用的 `usage`（input/output tokens）到 campaign 账本；
2. 用同一脚本扩展出 token 统计与按当前价格换算的费用区间（价格是外部事实，需标注日期）；
3. 与并发实验（意见 5 另一部分）一起报告；该实验需要额度恢复后的 campaign。
