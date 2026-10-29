# E7 困难良性扩展集：规格就绪，**场景与数字都还没有**

> 回应审稿意见 7："需要排除检测只是在区分活动量；补 PBC 规模相当的良性任务上的误报"。
> 本文件替代"完成 E7"的说法：**只有规格**。在 campaign 跑起来、场景真正执行之前，
> 任何 FPR 数字都不得出现。

## 1. 现在有什么

- 机器可读规格：`env/scenarios/_drafts/e7-difficult-benign/spec.json`
  （schema `mtagentlens.e7-difficult-benign/1`，`status: spec_only`）：
  **4 类 × 5 = 20** 个场景的 id、任务概述、良性论证与 `expected_pbc_min`；
- 结构测试：`env/tests/test_e7_difficult_benign_spec.py`（2 项）——数量/唯一性/字段/
  以及"该目录下除 spec.json 外没有场景文件"（防止把规格误当成可运行语料）。

四类：大量委派、敏感路径正常访问、正常外部网络、长任务高 PBC（各自目标 PBC 与
Type C 同量级；`expected_pbc_min` 10/1/1/10）。

## 2. 现在没有什么（诚实边界）

- **没有场景 JSON**（`agents/task/fixture_dir/hook/GT` 未写）；
- **没有 fixtures**；
- **没有执行**，因此**没有** TPR/FPR、没有 PBC 分布、没有与 Type C 的对照；
- 因此论文/回复信**不能**写"困难良性上误报为 0"或任何数字。

## 3. 解锁后的执行顺序（写进 campaign 计划）

1. 按 spec 写 20 个场景 JSON + fixtures（每个场景要有可判定的 `execution_probe` 与 GT）；
2. 先跑 2 个场景验证链路，再跑全量 20；
3. 用 `scripts/evaluation/final_verdict_metrics.py`（恶/良合并口径）与
   `e2_pbc_activity.py`（PBC 分层）产出 FPR 与 PBC 对照；
4. 把结果并入"独立扩展集"章节，**不修改**冻结基准与 fix2 数字。

## 4. 验收条件（写死，防止事后调参）

- 20 个场景全部有执行证据（marker/sink）或明确记为未执行并单列；
- FPR 报告必须带 Wilson 区间与分母（未执行/无裁决的单列，不进分母）；
- 与 Type C 的 PBC 分布对比要同时给 benign 与 attack 两侧；
- 若 FPR 不是 0，如实报告并把原因分类（活动量 vs 语义违规）。
