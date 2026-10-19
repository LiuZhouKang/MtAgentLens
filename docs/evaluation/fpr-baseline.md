# 良性 FPR 基线

机器可读：`fpr-baseline.json`（`mtagentlens.fpr-baseline/1`）。
生成：`./scripts/wire/run-benign-fpr.sh`（需要 sudo 与真实 LLM）。
门：`scripts/wire/check_baselines.py`（结构与内部一致性）。

## 2026-10-19 隔离测量

| 指标 | 值 |
|---|---|
| 场景 | 20/20 observed |
| Warning+ | **0/20**（Wilson 95% 0–0.161） |
| Unsafe | **0/20** |
| claims | 0 |

每个 run 使用 clean profile + **inode 独立副本** + ≤15 字节唯一 comm；
`process_comms` 中不含外层 `claude`。机制与验收：
[measurement-isolation.md](measurement-isolation.md)。

## 历史

旧基线（P0.5/P1/P3a/P3c、以及未隔离的 2026-10-19 采集）在
[archive/](archive/)。它们只用于追溯，不要引用为当前数字。
