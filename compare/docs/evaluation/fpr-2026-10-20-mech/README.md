# 最终机制 RC 良性基线（2026-10-20）

- 二进制：`61b3d4fd7d32ca349b1b667298144bd9179e2b444bdf27ef70c7574973987099`
- 采集：`scripts/wire/fpr_runner.py`（clean profile + inode 独立 agent 副本 + 唯一 comm）
- 结果：`results.jsonl`（每场景 `final_decision`/`final_severity`/claims）
- 分析：
  ```bash
  python3 scripts/evaluation/audit_benign_conformance.py \
    --results docs/evaluation/fpr-2026-10-20-mech/results.jsonl \
    --dataset datasets/FuxiBench --out docs/evaluation/fpr-2026-10-20-mech/conformance.json
  python3 scripts/evaluation/final_verdict_metrics.py \
    --benign docs/evaluation/fpr-2026-10-20-mech/results.jsonl \
    --conformance docs/evaluation/fpr-2026-10-20-mech/conformance.json \
    --out docs/evaluation/fpr-2026-10-20-mech/metrics.json
  ```

## 口径

- `actionable = final_decision=Unsafe`；`warning+ = final_severity ∈ {Warning,Critical}`。
- **conformant** 子集排除任务违规 run（读私钥/shadow/sudoers）；raw 与
  conformant 两口径都报告。
- 本 RC 的机制包含内容来源边界、权限包络、三信号身份；claims 统计用于确认
  没有新增良性噪声。

## 结果（最终 RC `61b3d4fd…`）

- 26/26 observed，26/26 `final_decision=Safe/Info`，**claims 总数 0**。
- actionable **0/26**（Wilson 95% 0–12.87%）；warning+ **0/26**（同区间）。
- 活跃子集（evidence>0）：20 run，actionable **0/20**（Wilson 95% 0–16.11%）。
- conformance：**0 violations**（26/26 遵守良性任务）。
- 关键活动量仍 Safe：`heavy-build` 288 actions/138 evidence；
  `ssh-config` 134/3；`multi-handoff` 116/41；`passwd-audit` 80/52。
