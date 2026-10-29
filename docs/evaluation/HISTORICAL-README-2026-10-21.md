# 历史 README 实验与性能记录（已作废）

以下为 2026-10-21 修复前 README 原文，仅保留证据追踪。**所有数字均不代表当前代码、场景或数据发布的效果，禁止作为新结果引用。** 新结果须绑定不可变 release、二进制及运行产物 hash，并完成独立执行和采集质量审计。

## FuxiBench

FuxiBench 是 MtAgentLens 的评估基准，用于覆盖多 Agent 安全观测中的主要场景。全部 86 个场景以 Claude Code 为 Agent 运行时采集，涵盖单 Agent、Agent Teams、Subagents 三种协作模式。

| 维度 | 数据 |
|------|------|
| 总场景 | **86**（67 攻击 + 19 良性） |
| 攻击类别 | **10**（manifest 实际分类数） |
| Type A（直接注入） | 10 — 攻击载荷在 task prompt 中 |
| Type B（间接注入） | 34 — 攻击载荷隐藏在 fixture 文件中 |
| Type C（多 Agent） | 23 — 跨进程协同攻击（**核心贡献**） |
| 标注 | 3 名独立标注者，交叉验证 + 共识裁判 |

**评估结果（按“测的是什么”分列，避免把脚本重打分当成二进制能力）**：

| 指标 | 值 | 口径与出处 |
|------|:--:|------|
| 攻击集 TPR / F1 | 0.9851 / 0.9925 | `compare/results/paper/main_evaluation.json` 的 `full_metrics`；由评估脚本的 `full_scores` 计算（引擎分 + 脚本重打分）。**不是**交付二进制的原始 `rust_score`。 |
| 二进制自身 `rust_score` | 11/67 攻击场景 < 0.70 | 同一 `main_evaluation.json` 的 `per_scenario`；因此“二进制召回 0.985”不成立，二者不可混用。 |
| 旧语料中的二进制裁决（FuxiBench 86 个 DB，2026-07 构建写入） | **可行动（actionable = `decision=Unsafe`）**: TPR 0.8955（Wilson 0.7997–0.9485）FPR 0.000（0–0.168）；**Review 信号（Warning+）**: 修复前 TPR 0.9851 **FPR 0.8947**，修复后（反事实重算）TPR 0.9403 **FPR 0.000** | `compare/results/paper/standard_evaluation_v5.json`；直接读取 `responsibility_alerts` 的 `decision`/`severity`，BCa 10,000 次 + Wilson 区间。修复后的 Warning+ 是**由旧 DB 中已存 `(has_claims, score)` 反事实重算**，不是二进制重跑；重采属于 P1.2。产品契约见 ADR-0015：只有 `Unsafe` 是可行动告警。 |
| 良性 FPR（Warning+ review 信号，隔离） | **0.00（0/26；Wilson 95% 0–0.129）** | `docs/evaluation/fpr-2026-10-20-mech/`（最终 RC `61b3d4fd…`）；26/26 观测到，final_decision 全为 Safe/Info，claims=0，conformance 0 violations。含 7 个困难良性；20 个 run 有意图-动作关联（活跃子集 0/20，Wilson 0–0.161）。每个场景用 clean profile + **inode 独立副本 + 唯一 comm**。 |
| 良性 FPR（actionable / Unsafe，隔离） | **0.00（0/26；Wilson 95% 0–0.129）** | 同一最终 RC；26 个 run 的 `runs.final_decision` 全为 Safe，0 个 actionable，claims=0。`heavy-build` 288 actions/138 evidence、`ssh-config` 134/3、`multi-handoff` 116/41 仍 Safe。 |
| TaintedContextUse 门控（旧语料反事实） | 置信度 ≥0.70：良性 **0/19**、攻击 **43/67** | 旧 DB 的 `responsibility_claims.confidence`；授权门需 `delegation_records`，旧语料该表为 0 行，故只可反事实验证置信度门。单元测试覆盖授权/未归因/方向判定。 |
| **P1.2 小样本**（12 场景，交付二进制，observer off） | 图模式：良性 **0/6**、攻击 **2/6**；Credential **0/12**；TCU **0/12**；Unsafe 攻击 **0/6** | `docs/evaluation/p12-small-sample/REPORT.md`；收紧 `reconnaissance` 模板前良性模式命中 5/6。全量 86 场景重采按用户要求暂缓（控成本）；旧语料已恢复，样本 DB 单独保存。**engine-only 小样本不复现旧 Unsafe TPR，正文不得无差别引用 0.8955。** |
| Observer 路由覆盖 | 63/86 场景出现 `observer_*` 路由，其中仅 6/86 得到非 `observer_failed` 裁决 | 从 86 个 DB 的 `engine_section_json.classification_route` 直接统计；59 个场景的 observer 调用失败（API 不可用），不能作为“Observer 有效性”的证据 |

> 隔离 FPR 的采集方式见 `docs/evaluation/measurement-isolation.md`：被测 Agent 以 inode 独立副本运行，collector 的 `-c` 与 TLS uprobe 只挂到该副本，外层交互会话被排除（已用内层/外层 PID 对照验证）。Warning+ 是单次实测且会波动：同一协议紧邻的一次重跑得到 1/20（`benign-heavy-build` 的 fail-closed Uncertain，0 claims），两次的 actionable 都是 0/20；产品口径以 actionable 为准。仓库内早期宣传的 `TPR 0.896 / FPR 0.000` 是 2026-06-25 旧采集，不再作为头条数字。

**Ground truth 标准程序**：`make annotate`（`tools/annotator/`）启动本地标注与裁决工作台——盲标、Agent/Action/Chain 标注、Cohen's/Fleiss' κ、可导出 JSON。规范见 `tools/annotator/CODEBOOK.md`。这不是脚本拼凑，而是论文归因指标（§3.2）的标注协议。

消融实验的结论以 `compare/results/paper/ablation_summary_v16.json` 为准：该文件中 L2 的 ΔAUROC 为 0.0（不显著），与早先“L2 是主要增益来源”的表述冲突；在补做一致的消融重跑之前，不应把 L2 写成主增益来源。

详见 [`datasets/FuxiBench/SCENARIOS.md`](../../datasets/FuxiBench/SCENARIOS.md) 和
[历史来源 `compare/results/paper/EXPERIMENTS.md`（已封存于 local）](../../local/final-v1/archive/compare-legacy-2026-10-30/paper/EXPERIMENTS.md)。

---

## 性能

微基准测试（Linux 6.17，本地 bpftool stat + pidstat）：

| 指标 | 数值 | 测量方法 |
|------|------|---------|
| SSL enter probe | **101.3 ns/op** | bpftool stat，2,000,000 次运行 |
| SSL read exit probe | **57.6 ns/op** | bpftool stat，1,000,000 次运行 |
| SSL write exit probe | **59.4 ns/op** | bpftool stat，1,000,000 次运行 |
| 单次 SSL read/write 总 probe 成本 | **218.3 ns/op** | enter + read_exit + write_exit |
| 空闲 RSS | **36.8 MB** | 60s pidstat 采样 |

端到端开销（`scripts/wire/measure-overhead.sh`，一次 `claude -p` 回合，45s）：
**平均单核 CPU 6.24%**、**峰值 RSS 1,424.8 MiB**（其中 ONNX 推理约 1,327.3 MiB）。
仓库中“<3% 吞吐开销”的数字来自合成 FakeRunner 管线，生成脚本的
`claim_boundary.not_supported` 明确声明它不支持生产 agent 的端到端延迟结论；
在没有对应 JSON 的端到端测量前，不应把它当作产品开销。

---

