# Replay 等价性检查行为（2026-10-25，离线实测）

> 背景：`collector/src/bin/replay.rs` 用于"重新分析已记录 run 的冻结证据"。
> 审计时问了一个具体问题：**当前构建能否重放 fix2 的 run 并声称等价？**

## 1. 实测

```bash
cd collector && cargo build --release --bin replay
./build/release/replay --run-dir <fix2 benign run dir>
# replay error: authoritative replay failed:
#   analysis implementation differs from the recording;
#   use recorded-result replay or an explicitly new analysis protocol
```

**它拒绝了**。原因：每个 `AnalysisCheckpoint` 记录录音时的
`implementation_fingerprint`（源码/配置指纹）；当前构建在 §75–§85 之后指纹已变，
于是 replay 拒绝把"旧记录 + 新内核"当作等价重放。

## 2. 这是正确行为，不是缺陷

- 防止把"改了检测逻辑之后再跑一遍"冒充"原样复现"；
- 与论文口径一致：当前 RC 是 **fix4**；本文件的 parity 检查基于 fix2 记录，
  fix4 的等价性证据见 `rc-2026-10-25-fix4/replay-ablation.json`（61/61 逐条复现）；
- 需要"新分析协议"时，必须显式选择（replay 的报错就是提示），并在文档里标注。

## 3. 已被单测锁定

`collector/src/responsibility/replay/checkpoint.rs` 新增 2 项测试：

- `replay_refuses_a_checkpoint_from_another_implementation`：篡改 `implementation` 后
  `validate()` 返回 `analysis implementation differs`；
- `replay_accepts_a_matching_checkpoint`：指纹匹配时通过；`run_id` 不匹配仍被拒。

## 4. 与 E3/E4 的关系（必须一起读）

E3/E4 的 replay 敏感性分析**不走** `validate()`：它们显式地在记录输入上用当前内核
`evaluate()`，并在文档中写明"这是记录输入上的分析内核 replay，不是 campaign 重跑"。
本检查说明：**如果要声称等价性**，那条路径会被指纹门挡住——这正是我们需要的诚实边界。

## 5. 后续

- 若审稿人要求"用当前构建重放旧记录并给出等价性"，唯一合法方式是重跑 campaign
  （额度恢复后），而不是绕过指纹；
- `replay --sqlite` 与 `--run-dir` 两种入口都走同一 `ReplayExecutor::reauthoritative`。
