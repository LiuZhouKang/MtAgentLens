# E4 消融：逐维 leave-one-out 与门控消融（记录输入 replay，离线完成）

> 回应审稿意见"消融粒度"："L0→L1 同时引入 eBPF 与 PBC，无法区分两者各自贡献；
> 建议拆分 eBPF 与 PBC，并对六维评分做逐维 leave-one-out 消融。"
>
> **完成范围（如实声明）**：逐维 LOO（D1–D6）与证据门控消融在**记录输入**上已完成；
> "仅 eBPF（L0.5）vs eBPF+PBC（L1）"的层级拆分**未做**——它需要重放原始事件、
> 重新构图，而不是 replay 冻结 checkpoint，属额度恢复后的 campaign 工作。
>
> 产物（**RC 2026-10-25-fix4 重算版**）：`local/workpackages/2026-10-25-e3-fix4/`
> （`attack.json` / `benign.json` 内含 `ablation_points`；说明见同目录 `README.md`；
> 旧 fix2 版本保留为历史）。

## 1. 方法

- 命令：`mtagentlens replay-sensitivity ... --loo-dims 1,2,3,4,5,6`
  （`replay/sensitivity.rs::sweep_ablation_with`）。
- 对 RC fix4 的 61 份 frozen checkpoint（18 benign + 43 attack，`runs_without_checkpoint=0`）
  依次把某一维融合权重置零（`ScoringConfig.weights[i] = 0`），其余保持默认，
  重算分支与最终裁决，与记录配置比较。
- 维度对应：D1 boundary、D2 pattern、D3 role、D4 repetition、D5 evidence、D6 behavioral。

## 2. 结果（RC fix4：每维 61 run）

| 移除维度 | benign 裁决翻转 | attack 裁决翻转 | 分支翻转（benign/attack） |
|---|---:|---:|---|
| D1 boundary | 0 | 0 | 0 / 1 |
| D2 pattern | 0 | 0 | 0 / 1 |
| D3 role | 0 | 0 | 0 / 1 |
| D4 repetition | 0 | 0 | 0 / 1 |
| D5 evidence | 0 | 0 | 0 / 0 |
| D6 behavioral | 0 | 0 | 0 / 0 |

门控消融（E3 的同一批数据，门控中心 5/10/20）：**裁决翻转 0**（见 E3 §5）。

## 3. 结论（可直接写进论文）

1. **固定记录 Observer 观测时，最终裁决未随逐维移除改变**：把 D1–D6 任一维权重置零，61/61 裁决不变。
   该结果是固定观测条件下的稳定性；分诊改变可能影响真实调用，不能推断端到端检测不依赖评分。
2. 分数层面对路由有影响的维度是 D1–D4（attack 分支最多 1 个 run 变化），D5/D6 在该
   样本上没有改变分支；这支持把 D5/D6 定位为"补充信号"而非主判据。
3. **仍然不能宣称**"eBPF 与 PBC 各自贡献已拆分"：那需要重新采集/重放原始事件
   （L0.5 只有 eBPF、L1 加 PBC），当前离线工具不具备该能力。

## 4. 论文写法与限制

> 在冻结检查点上做逐维 leave-one-out：移除 D1–D6 任一维，最终裁决均不变（0/61 翻转），
> 分支最多变化 1 个 run；证据门控中心在 5–20 之间同样不改变裁决。这不等于真实调用策略不受单维
> 或门控常数影响。层级拆分（仅 eBPF vs eBPF+PBC）仍需新的采集实验，列为限制。

限制：
- 与 E3 相同，是**记录输入 replay**，不是 campaign 重跑；live 流式分数可能与 replay 不同。
- 权重置零是"去掉该维贡献"的最强扰动，不等价于"该维无效"；它只说明裁决不由单维决定。
- 61 个 run 均有 checkpoint，无缺失；不把旧报告的缺失数带入 fix4。
