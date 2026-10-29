# 告警延迟（fix2，运行级证据）

> **2026-10-28 更新**：本文是 fix2 的运行级历史报告，不是事件级对齐；当前开销实验已用真实 action→evidence→claim→alert 引用链报告链接延迟，见[实验总方案 §6.6](EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#66-实测结果2026-10-28-完成)。
>
> 回应审稿意见 5 中的"告警延迟（事件→告警）"。**先声明定义边界**：本仓库不提供
> 事件级对齐（ADR-0016），所以这里报告的是**运行级**延迟，而不是"某个事件到某个告警"
> 的逐事件对齐。

## 1. 定义（写进论文时必须照抄）

- **decision latency** = 最终告警时间戳 − 该告警之前最新的相关证据时间戳；
  含义："最后一条相关证据出现后，多久给出该 run 的权威裁决"。
- **finalization latency** = `runs.ended_at_ns` − 最终告警时间戳；
  含义："裁决之后多久完成终结化/落库"。
- 证据时间戳取 `correlation_evidence` 与 `actions` 的并集（都是 run 级记录）。
- 无裁决（`final_decision=null` 或 `state=failed`）、无 DB、无告警、无证据的 run
  **全部排除并计数**，不以 0 计入。

## 2. 结果（RC fix2，process 模式）

| arm | 可测 run | 排除 | decision p50 | decision p95 | finalization p50 | finalization p95 |
|---|---:|---|---:|---:|---:|---:|
| benign | 19 | 0 | **7.48 s** | 59.67 s | 0.025 s | 0.198 s |
| attack | 43 | 1（no_verdict） | **8.48 s** | 34.91 s | 0.035 s | 0.123 s |

（单位：秒；p50/p95 为 nearest-rank，可复算。）

## 3. 解读与限制

1. 裁决不是实时的：中位数在最后证据后约 **7.5–8.5 s** 给出，p95 可达 35–60 s。
   这符合"阶段 1 流式 + 阶段 2 终结化"的设计，但**不能**写成"亚秒级检测"。
2. 终结化本身很快（p50 25–35 ms，p95 < 0.2 s），延迟主要来自"最后证据 → 裁决"
   这一段（Observer 审查/批量复核）。
3. 该数字**不是**端到端首告警延迟：阶段 1 的即时告警（若有）与最终裁决是不同事件；
   本报告的 `alerts` 计数与告警类型见 DB；不要把它与 `observer-cost` 报告的 run 时长混用。
4. 只覆盖 fix2 口径的测量；**RC fix4 未重测延迟**（其 campaign 未记录该项），因此延迟数字
   只能标注为历史 profile 证据，不得写成 fix4 的性能；tmux-live 未进指标。

## 4. 产物

```
400e4fb41ee2e515713ff67f18d0b3c1302471a2b6f4df4993b9e98a35ef6b04  report.json
```

## 5. 复现

```bash
env/.venv/bin/python scripts/evaluation/alert_latency_report.py \
  --results benign=local/workpackages/2026-10-25-rc-fix2/benign/results.jsonl \
  --results attack=local/workpackages/2026-10-25-rc-fix2/attack/results.jsonl \
  --out local/workpackages/2026-10-25-alert-latency/report.json
```
