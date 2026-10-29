# RC 2026-10-25-fix3 —— **无效（campaign 中途模型额度耗尽，不可引用）**

> 状态：**INVALID / 不可发布**。本目录只保留一次失败 campaign 的证据与失效原因，
> 不冻结 `rc.json`、不算指标、不得引用任何数字。

## 1. 失效事件（2026-10-25 07:38 起）

- 攻击臂第 26 个场景开始，agent 日志出现
  `API Error: 402 Insufficient Balance`，`returncode=1`、agent 未执行任何动作；
- 19 / 44 个攻击 run 因此中止（`state=failed`，但 capture healthy、binding 正常，
  于是仍写出了 verdict——这些 verdict 只反映"什么都没发生"，不是检测结果）；
- 良性臂 19/19 在耗尽前完成（0 个 402、0 个非零返回码）；
- 证据：`local/f3/{benign,attack}/results.jsonl`、每个 run 的 `logs/agent_0.log`、
  `local/f3/*/runner.log`。**不删除。**

## 2. 为什么整份 RC 无效

- 召回必须只在"攻击确实被执行"的样本上测量；19 个 aborted run 连 agent 都没跑，
  把它们算进攻击臂会人为制造"漏报"或"正确 Safe"，两种都是错的；
- 只看前 25 个有效攻击 run 也不行：语料被截断成"字母序前半"，且 campaign 不是
  预先声明的子集，属事后挑选（post-hoc subsetting）；
- 因此本 campaign 只能作为**失效记录**，不能作为 RC。

## 3. 已修复的流程缺陷（防再次浪费）

`compare/scripts/run/pilot_observer.py` 现在会在每个 run 后检查 agent 日志中的
致命供应商错误（`Insufficient Balance` / `insufficient_quota` / `credit balance is too low`），
命中即：

1. 写入 `out_dir/campaign-aborted.json`（原因、最后场景、**未尝试**的场景清单）；
2. 以 exit code 3 立刻停止，不继续烧剩下的 run。

单测：`compare/tests/test_pilot_observer_abort.py`（4 项）。

## 4. 下一步（阻塞项）

- **阻塞**：模型 API 额度为 0（`402 Insufficient Balance`）。在额度恢复前不启动任何
  新的 agent campaign（良性/攻击/多 agent 都算）；离线工作（代码、测试、文档、
  标注工具、门禁）继续。
- 额度恢复后：用同一协议重跑 `local/f3/<arm>`（必须用短 campaign 根，见 §80），
  这次先跑 1 个场景确认额度与链路，再跑全量；每个 run 结束都检查致命错误。
- 修复前后对照：上一份**有效**测量仍是
  [RC 2026-10-25-fix2](../rc-2026-10-25-fix2/README.md)；当前构建相对它的默认行为差异见
  [实验设计 §81](../experimental-design.md)（复核提示上界、可读事实、phase-2 归因记忆）。
