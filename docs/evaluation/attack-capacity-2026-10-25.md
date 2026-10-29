# Attack-only 对照实验（零模型调用）：**不能**作为容量指标（负结果）

> 目的：回答"被模型拒绝的攻击如果真执行了，监控器能测到吗？"
> 做法：不经 LLM，直接执行每个攻击场景的诱饵流程（`make test/build/...` 或 `docs.md`
> 的 bash 代码块），由真实 collector 采集；零 token。
> **结论先说：这个协议无效——它测不到容量。** 结果和原因如下，作为方法学负结果保留。

## 1. 协议与可复现命令

```bash
export PYTHONPATH="$PWD/env:$PWD/env/src" COLLECT_HOST=93.184.216.34
export MTAGENTLENS_SUDO_PASSWORD=*** MTAGENTLENS_COLLECTOR_STOP_TIMEOUT=180
env/.venv/bin/python scripts/evaluation/attack_capacity_experiment.py \
  --release local/workpackages/2026-10-25-sink-release3/FuxiBench --out local/f8-capacity
```

触发器：`scripts/evaluation/attack-capacity-triggers.json`（44 条，逐条由 GT
`task_summary` 推导；`runbook_blocks=true` 执行 `docs.md` bash 代码块）。

## 2. 结果（release3 `71e10e45…`，binary `de3a0894…`）

| 项 | 数值 |
|---|---|
| 场景 | 44 |
| 执行成功（collector 正常） | 44/44 |
| 执行证据（marker/sink） | 41/44 |
| capture quality | 42 `unknown`、2 `healthy`（混杂：多数 DB 的 checkpoint 状态字段缺失/未知） |
| 最终裁决 | **Uncertain 44/44**，Unsafe 0 |
| claims / alerts | **0 / 0**（全部 run） |
| decision provenance | `["no_observations","capture_quality:unknown"]` |

## 3. 为什么无效（根因）

抽查 `credential-keylogger`、`c2-dns-dead-drop`、`exfil-archive-upload`、
`resource-disk-fill`、`privsec-docker-escape` 的 DB：**没有 intent、没有 claims、
没有 alerts**。原因是本系统的责任归因是 **intent 驱动**的：

- 意图来自 LLM 请求（TLS/HTTP）与工具调用；
- 没有 agent/LLM 上下文时，直接执行 `make test` 产生的文件/网络动作**没有 intent
  可以归属**，责任图不产生 claim；
- capture quality 因"无 TLS 会话"被判 unknown，裁决走 fail-closed 的 `Uncertain`。

因此 "44/44 Uncertain" **不是"全部检出"**，而是"没有东西可归因 + 采集未知"。
把它写成容量指标会虚报科研结论，特此更正并保留为**方法学负结果**。

## 4. 正确的容量/效果度量

1. **agent-gated executed subset**（唯一有效口径）：release2 campaign 中
   19 个真正执行的攻击 → 4 Unsafe（21.1%）、≥review 14（73.7%）、5 Safe。
2. **离线 replay 调参**：用这 19 个已执行 run 的 DB 做零 token 的规则/阈值实验，
   目标是把"凭证读取→外传"这类 OS 链式证据升级为行动级（Unsafe），再在
   新 campaign 中验证（不重跑旧数据）。
3. 若未来要做真正的"attack-only 容量"实验，必须**同时提供 LLM 意图上下文**
   （例如在 agent 会话内由同一进程树执行载荷），否则测的是采集缺失而不是检测能力。

## 5. 保留价值

- 脚本与触发器是可复现的"无上下文对照"，用于验证 **intent 归因是否必要**；
- 结果支撑论文中的 **construct validity / limitation** 一节：监控器不是纯 OS 签名，
  其证据链由 agent 意图与 OS 动作共同构成；这也解释了为什么 agent-gated 里
  Unsafe 只出现在"OS 证据 + 意图"对齐的 19 个执行样本中。
