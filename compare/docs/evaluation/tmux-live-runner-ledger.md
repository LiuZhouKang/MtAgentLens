# tmux-live 多 agent runner ledger（离线实现，待 campaign 验证）

> 背景：`tmux-live` 模式的 run 目录只有 session 与 per-agent 状态文件，没有"谁执行了
> 什么、何时、结果如何"的账本；未写终态 status 的 agent 无法与"失败"区分。审稿意见 2/7
> 需要多 agent 归因证据，因此补 per-agent ledger。

## 1. 实现

- 新模块 `env/src/mtagentlens_benchmark/engine/runner_ledger.py`
  （schema `mtagentlens.runner-ledger/1`）：每个 agent 记录
  `task / pane_mode / log_file / launched_at_ns / completed_at_ns / status / detail`；
  - 状态：`succeeded / failed / timeout / error / **unobserved**`；
  - `unobserved` 是明确事实（status 文件缺失或从未进入终态），**既不是成功也不是失败**；
  - 原子写：`runner-ledger.json.tmp` → rename；重复 launch/completion、时间倒流直接报错。
- `executor._run_tmux_mode` 已接线：launch 前记录、轮询结束后按 status 文件记录完成，
  写 `run_dir/runner-ledger.json`，并在 summary 里给出路径与计数
  （`runner_ledger` / `runner_ledger_summary`）。
- 映射函数 `ledger_status(status_file)` 独立可测：`None/pending/running → unobserved`、
  `timeout/error` 原样、`done + success/exit_code` → succeeded/failed。

## 2. 测试

`env/tests/test_runner_ledger.py`（6 项）：launch/completion 记录、缺完成保持
`unobserved`、重复/倒流/未知状态被拒、原子写与 schema、status 文件映射。

## 3. 未验证（诚实声明）

- 没有真实 `tmux + claude` 运行：接线路径尚未端到端跑过（需要模型额度恢复后的 campaign）；
- "终结化预算"仍由 `MTAGENTLENS_COLLECTOR_STOP_TIMEOUT` 控制，本次未改其默认值；
- 只有在 campaign 里跑过 tmux-live，才能把 ledger 与 collector 的最终化对齐、并据此
  把 tmux-live 纳入指标卡（当前仍排除）。
