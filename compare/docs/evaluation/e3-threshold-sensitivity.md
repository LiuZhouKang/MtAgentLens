> **Status 2026-10-30（supersedes this report）**：正式 T7-S 已改为 `compare/results/sensitivity/`：110 点阈值网格 + 六维权重/gate OAT + 1000 次联合 LHS，baseline 93/93 精确重放。本文的 25 点/旧 54 点/6 维 LOO 数字仅作历史记录，不再作为当前证据。

# E3：分诊参数敏感性

本报告回应 R1.4 / R2.1 的参数依赖问题。输入为 RC fix4 的 61 份冻结 checkpoint（18 benign、43 attack），固定已记录图、证据与 Observer 观测，不重新调用模型。它检验同一记录输入的分诊变化及裁决聚合，不估计新的语义调用、费用或端到端检出变化。

## 1. 修正原分析的问题

原实现将六个权重同时乘以 0.8/1.0/1.2。`fusion::fuse_dimensions` 的线性项和几何均值都按权重和归一化，因此整体缩放会抵消，不能检验相对权重。原九点数据保留为归一化控制，撤下“已证明对 ±20% 权重不敏感”的解释。

新增扫描每次仅改变一维：`w_i ← factor × w_i`，其余五维保持各自 checkpoint 的记录配置。六维 × factor {0.8,1.0,1.2} × gate_center {5,10,20}，每臂 54 个配置条目；factor=1 是重复的名义控制，不当作额外独立试验。25 点阈值网格及六维 LOO 另列，不把三类点数混用。

## 2. 新增相对权重实测

| 集合 | 运行数 | 配置条目 | 维度覆盖 | 最大分诊路径变化 | 最终裁决翻转 |
|---|---:|---:|---|---:|---:|
| benign | 18 | 54 | D1–D6 | 0 | 0 |
| attack | 43 | 54 | D1–D6 | 3 | 0 |

攻击臂最大三个路径变化出现在 gate_center=5、D1 或 D2 上调，或 D3/D4 下调时；在名义 gate_center=10 下，部分逐维扰动也会改变一个运行的分诊。这一结果表明相对权重确实影响路由，原整体缩放没有覆盖这一现象。零裁决翻转只在固定已记录语义观测的条件下成立，不能由此排除真实运行中新增/省略审阅所带来的标签变化。

25 点阈值扫描的攻击路径变化最多一个运行，良性为零；最终裁决均不变。逐维 LOO 见 [E4](e4-ablation.md)。这里的 ToObserver 计数是重算路径，不是实测 API 调用次数。

## 3. 复现与证据

新报告集中在 `local/workpackages/2026-10-25-codex-revision/`：

- `benign-relative-weights.json`、`attack-relative-weights.json`：生产 CLI 直接输出，含逐点结果、`relative_weight_points` 和固定观测策略；
- `sensitivity-summary.json`：现有汇总器生成，两臂都核验运行数、维度覆盖、配置条目与翻转；
- `replay-inputs.json`：分析二进制哈希和每份输入 DB 的路径/哈希，逐一与 fix4 的结果行核对；
- RC 的二进制、release、数据库、原始实验报告均保持原样。新增二进制仅用于记录输入上的分析。

```bash
# 对每臂 results.jsonl 中列出的 finalized.db 逐一追加 --db；不包含无效尝试。
collector/build/release/mtagentlens replay-sensitivity \
  --db <recorded-finalized.db> ... --out <arm-relative-weights.json>
python3 scripts/evaluation/e3_threshold_sensitivity.py \
  --report benign=<benign-relative-weights.json> \
  --report attack=<attack-relative-weights.json> --out <sensitivity-summary.json>
```

聚焦回归先复现缺少相对扫描/汇总的失败，再检验每维覆盖、因子/门控交叉与翻转计数；旧报告缺少新字段时标为未知，不伪造“已完成”的空扫描。

## 4. 正文可直接使用

> 为检验分诊对相对权重的依赖，我们在冻结检查点上逐次仅将一维权重乘以 0.8、1.0 或 1.2，并与门控中心 5、10、20 交叉。18 个良性运行的分诊路径未变化，43 个攻击运行中最多三个路径变化；在固定已记录 Observer 观测的条件下，最终裁决均未变化。该分析不能替代独立测试集或重新调用 Observer 的端到端参数实验。

没有独立开发集记录；不声称开发与测试严格隔离，也不声称“参数依赖的担忧已被排除”。新增实验要检验真实分诊策略时，应让各配置产生自己的语义观测并记录调用成本。
