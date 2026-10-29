# MtAgentLens 最终交接：数据、代码、实验与执行（Codex 版）

> 日期：2026-10-26（2026-10-27 G0 更新）。  
> 这是**唯一权威交接**；§11 是接手后复核合同，冲突时以 §11 为准。旧的实验设计/审计草稿不再作为执行口径；证据审计保留在 `local/audit-2026-10-26/`。  
> 接手时仅完成设计/审查；后续代码实施与实测证据见 §11。未跑最终 campaign，数据修复由 DSH/Lead 负责。凡未跑项均为 pending，不写结果。

> **2026-10-28 开销协议覆盖项（已实测完成）**：意见 5 以[实验总方案 §6](../evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#6-overheadbench5)与[§6.6 实测结果](../evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#66-实测结果2026-10-28-完成)为准，覆盖本交接中旧 60 条件、探针/告警分解与工期安排。36/36 正式条件、15 配对区组、372 并发实例已完成；TLS 三次短微基准与其他探针 C1 短诊断已实测。T8/F2 在 `compare/results/overhead/raw/full-system/report/`；旧 driver/metrics 的 60 条件硬编码已替换并支持完整批次校验。历史验收记录保留，不是本协议的完成证据。DSH 的 93 场景流程不因本次测量停止或重启。

> **2026-10-27 G0 状态**：后续 agent 已完成机械修复并通过独立验收：release `9c7099c1d36d21af025f5050d462b273cc04aef0c69d3ff72e902779372c6ea3`；93=58 attack+35 benign、0 excluded、93 paper_ready；30 multi=22+8；A1/A2/A3/A4/A7/A8 通过；**A6 `/tmp` blind prepare 已修（`repo_rel` 支持外部 work，回归测试通过）；A5 env 声明 491 但实际 298（全量 870）**。`identity-confusion` 的 paper_ready 是机械判据，notes 仍有 A/C 歧义/registry 不可达/needs_human_review。C0 未跑。

---

## 0. 先读结论

- 当前冻结包（G0 后）：**93 输入 / 58 attack（A47/B11）+35 benign / 0 excluded / 93 检测计分**；multi 30=22 attack+8 benign。旧 84/9 口径仅历史。
- G0 已机械完成：93/58/35；但 `identity-confusion` notes 仍有未决科学问题（A/C 歧义/registry 不可达/needs_human_review），不能把机械 `paper_ready` 当完整裁决。
- 主 campaign：**93×1**；身份/归因/基线等复用 DB/checkpoint/transcript。Overhead 独立做 15 个配对区组（3 个四臂 +12 个两臂）、36 condition runs；warmup/校准/诊断/TLS 微基准另计，不用主 campaign 替代开销对照。
- 当前**不能直接跑**：30 multi 有真实双-pane测试但未做 30-run smoke；G0 已机械通过，A6 `/tmp` blind prepare 已修；Identity/Attribution 内核已有但未做真实参考/对齐；Overhead/Ablation/trace 未完成。
- 7 项分派任务 =7 个实验；论文只放 **5 张结果表**。
- 安全/E11/ONNX 不做；旧 v1 trace 只读；fix4 只历史；AI 参考按 3-AI+裁决，不写 human κ/gold。

---

## 1. 当前事实（可复核）

| 项 | 当前值 |
|---|---|
| HEAD | `610f726`（脏工作区） |
| 包 | `datasets/FuxiBench`，release `9c7099c1d36d21af025f5050d462b273cc04aef0c69d3ff72e902779372c6ea3`（G0 后） |
| GT | 93 =58 attack（A47/B11）+35 benign；0 excluded；93/93 paper_ready（机械） |
| 检测 | 93=58+35；multi 30=22+8（全部 included） |
| benign | 35=27 single+8 multi；7 difficult 均计分；handoff-unicode 为第8 multi benign |
| fix4 | 61 runs（43 attack+18 benign，全 process，0 multi）；仅历史 |
| traces | 87 DB，schema v1；`traces_replayable=false`/review-only；无 checkpoint/final_decision |
| 门禁 | 记录 18/18；claims 54/54；包 clean-room 93/58 |
| 关键缺口 | G0 49；tmux runner-events；IdentityView；AttributionResult；Overhead/Ablation/trace |

> 所有数字以 release.json/GT/DB 为准；本文不替代 artifact。

---

## 2. 不可更改的决定

1. **93 目标，G0 已机械冻结**；旧 84 只作历史；不混分母。
2. **每场景 1 run**；不重复；CI 只到 scenario；Overhead 独立重复。
3. **统一 RuleCatalog**：Generic 与 FuxiBench 启发的规则都写成通用规则；不做 dual/generic-only 两套指标，不设独立 adapter 检测器。
4. **ONNX 从 runtime 移除**；离线 Deberta 只作研究基线，不随包发布。
5. **旧 v1 traces review-only**；replay 只对新 v4/checkpoint；新 `FuxiBench-Traces/1.0` 93 DB。
6. **不做 E11/安全/前端产品化**；能力矩阵由他组负责。
7. **AI 参考问题不再讨论**：3 fresh AI + 裁决，只作 reference；不写 accuracy/human κ/gold。
8. **fix4 不混主表**；只用历史/回归。

---

## 3. 数据：G0 完成情况（2026-10-27）

### 3.1 当前实际
- 包 release `9c7099c1d36d21af025f5050d462b273cc04aef0c69d3ff72e902779372c6ea3`；93 输入=58 attack（A47/B11）+35 benign；**0 `detection_excluded`**；93 全计分。
- multi 30=22 attack+8 benign；`handoff-unicode` 已由 3 路盲标（27/27 benign）+裁决计入，成为第 8 个 multi benign。旧 23 Type C =22 attack multi + handoff（现 benign）；不要写成 23 attack。
- 0 hash drift；93/93 `paper_ready`（机械判据）；`identity-confusion` 的 notes 仍有 A/C 歧义/registry 不可达/needs_human_review，不能把机械 `paper_ready` 当完整科学裁决。
- 旧 84=58+26 只作历史；不再用 9 excluded 口径。

### 3.2 G0 独立验收（2026-10-27）
- A1 verify 18/18；A2 health 18/18；A3 package valid 93/58/35/0；A4 GT/scenario problems=[]；A7 code P1 通过；A8 reverse 0/93/0。
- **A6 已修复**：`gt_rebuild.py prepare --smoke 3 --work /tmp/dsh-gt` 原报 `repo_rel` 路径错误；DSH 修改 `repo_rel` 支持外部 work，`prepare` exit 0，新增回归测试通过。
- A5 env 声明 491 但列出的 4 个文件实际 298；`env/tests` 全量 870 通过；属期望/文件集不一致，不是数据坏。
- 验收报告：`local/audit-2026-10-27/G0-VERIFICATION.md`。

### 3.3 仍需要修复的科学/数据问题
- `identity-confusion` 的机械 `paper_ready` 与未决 notes 冲突；若要论文可用，需 3-AI 重审/裁决或明确 unknown。
- `recon-user-enumeration` v5 2:1 benign 但保留 v4 attack；已在 `label_annotation.v5_disagreement` 披露，不能静默当 unanimous。
- `handoff-unicode` 已 benign；fixture 的 ASCII gate 仍存在，但作为困难 benign 的设计证据已足够；不得再把它当 attack。
- 旧 v1 traces 87 个：`traces_replayable=false`、`traces_collected=87`、review-only；不再声称 replay。
- 新 93 v4 trace/checkpoint 尚未由 C0 生成；旧 v1 不迁移。

### 3.4 G0 验收命令
```bash
python3 -I local/readiness-2026-10-26/team/scenario-repair/check_dataset_health.py
PYTHONPATH=env:env/src env/.venv/bin/python scripts/evaluation/validate_fuxibench_package.py --clean-room
env/.venv/bin/python scripts/evaluation/validate_gt.py --dataset datasets/FuxiBench
env/.venv/bin/python scripts/evaluation/check_scenario_integrity.py --dataset datasets/FuxiBench --strict
```
当前均通过（见 `G0-VERIFICATION.md`）；A6 已修复，剩余 `identity-confusion` notes 与 A5 计数口径。

## 4. 代码：问题 → 解决方案（按域）

### 4.1 事件/数据流
- 问题：`Event.data: serde_json::Value` 无 schema/ID；analyzer 生成 benchmark 事件；无 finish 尾部丢失。
- 方案：`ObservationEnvelope {schema_version, obs_id, source, ts_ns, pid, process_start_ns, payload}` + `DerivedFact`；raw/derived 分离；`Analyzer::finish`；contract tests；benchmark fixtures 不进 runtime。

### 4.2 路径/授权
- 问题：raw path 不 canonicalize；`/dev/*`、`/proc/`、`/sys/` 整段 allowlist；loopback/private 默认。
- 方案：`CapturedPath {raw, normalized, binding=Known|Unknown}`；仅 Known 参与边界；精确 pseudo-FS 白名单；`..`/`~`/dirfd/危险设备负例；默认 egress 显式记录。

### 4.3 身份
- 问题：消息级/`conversation_agents` 未接生产；`ssl_ptr` 无 `start_time_ns`；Phase2 仍带 provisional ID。
- 方案：`IdentityView`；pre-checkpoint resolver 5 条件（PID/+PPID/+TLS/+conversation/full）；`AuthoritativeIdentityProvider`/persistence/export 消费 conversation；`tls_capture` 输出生代；Phase2 全链 remap；alert 不得 provisional；per-unit prediction。

### 4.4 根因/入口/链
- 问题：`traceback.rs` 单根 first-match；chain 是无序集合；无 entry/ranked；claim.traceback_ref NULL；metrics 无 producer。
- 方案：`AttributionResult {entry, root_candidates, ordered_partial_dag, abstain}`；`ranked_agents` 确定性排序（证据/时序，不称概率）；`claim.traceback_ref` 回填；`runtime→prediction` adapter；@3/MRR 仅在预测排序与明确相关根因参考具备后（参考无需自身有序）。
- 23 Type C 条件映射；22/23 按 G0。

### 4.5 导出/标准
- 问题：alert-only NDJSON；无 trace/span/parent/claim/root/chain；无 OpenInference。
- 方案：OTel/OpenInference 投影（LLM/AGENT/CHAIN/TOOL 只映射有证据的调用）；trace/span/parent/IDs；PROV-O DAG；内容 opt-in；不伪造 span。

### 4.6 持久化/replay/Phase2
- 问题：全图快照重复 53%；replay 只比较 evidence/alerts；假 refinement；O(I×A)；`let _` 写失败；`graph` 静默驱逐。
- 方案：内容寻址 snapshot + 引用；`FinalizeTransaction` 原子写；`ReviewBatch` 分批；replay parity 比较 verdict/claims/tracebacks；refinement 真实 ID；correlator 索引/cap；graph_quality；analyzer flush；失败显式。

### 4.7 评分/patterns/ONNX
- 问题：benchmark leakage（`hog.sh`/scenario_id/marker/host strings）；硬编码 Platt/gate；D1–D6 两层定义冲突；CUSUM 不可达；ONNX 1.3GiB/n_high=1。
- 方案：统一 RuleCatalog，所有规则通用化；删 `hog.sh`/scenario_id/marker/host strings；score 降级 triage/校准仅 dev；修 CUSUM；PBC 只计 evidence-backed；移除 ONNX；Layer 门禁。

### 4.8 运行/编排
- 问题：tmux 不写 `runner-events.jsonl`/input_reference，binding 拒绝；漏 `RUN_TAG`；无 93 编排；`e8_plan` 只 A2；system_load 仅 process。
- 方案：tmux v4 runner-events(session/pane/role_file)/RUN_TAG/binding；93 orchestration; AppBench A1/A2/A3 + valid-run; full-system overhead driver; ablation switches; trace packager.
- AppBench 每个 record 2 次 judge；当前 93=279 records/558 calls；旧 84=252/504 仅历史。

### 4.9 测试/门禁
- 新增：event contract、tmux 2-pane smoke、CapturedPath traversal、identity pre-checkpoint 5 区分、attribution ranked/schema、replay parity、package one-DB replay、ablation layer configs、AppBench transcript/coverage、overhead components。
- 不新增 `#[allow]`/`#[ignore]`/预算上调；18 门禁 + 新契约。

---

## 5. 七个实验（一项任务一个，5 张结果表）

| 任务 | 实验 | 数据 | primary | 表 |
|---|---|---|---|---|
| 2a 身份 | IdentityBench | 30 multi | pairwise/B³/false-merge/coverage | T5 |
| 2b+8 根因 | AttributionBench | 30 multi, N_A attack | RootSet/@1/Entry/Chain/abstain | T6 |
| 3a+8 基线 | AppBench | 93 transcript | AUROC / TPR-FPR / coverage | T4 |
| 5 开销 | OverheadBench | 15 配对区组，36 正式条件；探针补充另计，见实验总方案 §6 | C3/C0 耗时增幅；CPU/RSS/loss/latency/cost | T8/F2 |
| 6 独立性 | SensitivityBench | 93 checkpoint | flip rate / CI | T7C |
| 7 PBC | PBCBench | 93 | stratified TPR/FPR/AUROC | T7A |
| 8 消融 | AblationBench | 93 facts/checkpoint | ΔAUROC/TPR/FPR | T7B |

- 检测主 endpoint：`system_score` 的 AUROC/pAUC；TPR/FPR 次要，报 all/decided/executed+coverage；未实现 score 则只报 categorical，不造 AUROC。
- 身份/根因：AI reference；不写 accuracy；@3/MRR 仅预测 ranked 与明确根因参考具备后。
- AppBench：A1 task-only；A2 full trace（含 tool result，主基线）；A3 no result；M 只出现一次。
- PBC：原始 activity baseline；高 PBC benign 不足写不可估。
- Ablation：L0 raw / L0.5 +PBC / L1 +graph / L2 +Observer / L3 full；固定每层规则；hard_egress off。
- 统计：scenario bootstrap；1 run 不估 run 方差；Holm；分析口径开跑前固定；unknown/abstain 不记负。

### 复用（省钱核心）
- C0 93×1 产出全部 DB/checkpoint/transcript：身份/归因用 30 multi；AppBench 用 transcript+judge；PBC/Sensitivity/Ablation 用 checkpoint；trace 用 DB。
- Overhead 并发1：4×3=12条件；并发4/8/16/32：4×2×3=24条件，总36条件、372并发实例。warmup/校准/短诊断/TLS微基准另计，执行前由适配后的 driver 输出计划与预算。
- 93 场景主 campaign 不跑 3 repeats；Overhead 按上文独立重复。不新增场景、不做 E11、不复跑旧 v1。

---

## 6. 执行顺序与验收

```text
W0 口径固定/决策（0.5d）
W1 G0 数据（6–10d） + tmux runner-events（2–3d）   并行
W2 IdentityView/pre-checkpoint resolver（4–6d）
W3 AttributionResult/ranked roots（5–8d）
W4 system_score/AppBench transcript（1–2d）
W5 Overhead 适配与校准（按实验总方案§6；旧工期撤下，预算/排期校准后定）
W6 Ablation switches + sensitivity/PBC（5–8d）
W7 trace packager/replay validator（4–6d）
W8 E0 4-scenario pilot（0.5d）
W9 C0 93×1（5–7h）
W10 3-AI 参考 + judge + 离线分析 + 表/claims
```

- 总前置约 **48–74 人日**（2–3 人 6–9 周）。
- 门：G0 93 冻结；G1 tmux 2-pane + E0 4/4；G2 C0 healthy/v4/checkpoint；G3 参考 agreement/coverage；G4 表/claims。
- C0 前不得写结果。

---

## 7. Codex 任务（复制给 Codex）

```text
你接手 MtAgentLens 的代码/运行/实验基础设施实现。数据修复（93、9 excluded、49 paper_ready、重标）由 DSH/Lead 负责，你不要重复改数据集；你只按冻结后的 93 输入实现代码和实验接线。

先读：AGENTS.md、CLAUDE.md、docs/research/FINAL-CODEX-HANDOFF-2026-10-26.md、local/audit-2026-10-26/ 下 7 份审计。不要相信旧交接/旧实验设计。

先做（按顺序，TDD，不跳）：
1. tmux runner-events/v4 binding：session/pane/pane_pid/role_file、RUN_TAG/RUN_TAG_FILE、input_reference；2-pane fake smoke；multi 不再被判 failed。
2. IdentityView + pre-checkpoint resolver 5 条件（PID/+PPID/+TLS/+conversation/full）+ per-unit prediction；证明 5 套不同；不得从 checkpoint authoritative identities 读。
3. AttributionResult + ranked_agents（证据/时序，不称概率）+ entry/ordered partial DAG + claim.traceback_ref；@3/MRR 仅在预测排序与相关根因参考均已定义后；参考无需自身有序。
4. system_score/run_metrics/coverage/execution；AppBench transcript/A1/A2/A3/valid-run；A2 含 tool result，A3 不含；每 record 2 次 judge。
5. Overhead C0/C1/C2/C3 driver；Ablation L0/L0.5/L1/L2/L3 + gate + hard_egress off；trace packager/replay validator。
6. 每个包先失败测试→最小实现→窄测试→回归；不跨包顺手重构；不新增 allow/ignore/预算。

约束：1 run/scenario；不写 accuracy/human κ/gold；不伪造 93/58/35；不混 fix4/旧 v1；不跑安全/E11/ONNX；不 commit/sudo/付费；不把设计级 causal 当 runtime。
输出：每包 patch、失败→通过测试、验收命令、实测结果；仍未知/开放问题≤5；所有断言带 file:line/命令。
```

---

## 8. 红线

- C0 前不写任何检测/身份/根因结果。
- 不写 93=58+35 定稿（G0 前）；不写 23 Type C=23 attack。
- 不写 accuracy/human κ/gold；不写“1.000 真实准确率”。
- 不写 @3/MRR 未实现；不造排序。
- 不写 PBC 匹配/已排除混杂；不拿 PBC 当 activity。
- 不写独立测试集；family split 是回溯。
- 不写“公平基线/方法优越”超出数据；AppBench 是应用层信息对照。
- 不写“完整开销已测”用 218ns/process-only。
- 不混 fix4/旧 v1/旧 release；不高报 93 已计分。

---

## 9. 证据/权威文档

- 数据审计：`local/audit-2026-10-26/experiments-data.md`
- 核心检测：`core-detection.md`
- 身份/归因：`identity-attribution.md`
- 框架/持久化/replay：`framework-persistence-replay.md`
- 评分/图：`scoring-judgment-graph.md`
- 方法学审：`design-review-methodology.md`
- 论文/空间审：`design-review-paper.md`
- 可行性审：`design-review-feasibility.md`
- 外部标准：`external-design-research.md`
- 数据集卡/协议：`datasets/FuxiBench/DATASET-CARD.md`、`AI-ANNOTATION-PROTOCOL.md`
- 最终实验细节：`docs/evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md`、`EXPERIMENT-IDENTITY-ATTRIBUTION-2026-10-26.md`、`DATASET-93-REPAIR-PLAN-2026-10-26.md`

---

## 10. 接手时状态（当前实施见 §11）

**设计/审查完成；实现/数据未完成。**  
下一步：G0 盲重标 + tmux runner-events；Identity/Attribution 预测对象；Overhead/Ablation/trace；再 E0/C0。不要跳过。


## 11. Codex 接手复核与执行合同（2026-10-26，持续更新）

本节记录对当前方案的修正与实现证据，不改变 DSH/Lead 的数据修复所有权。第 0—10 节为接手时设计，不代表当前代码全部未改或全部已完成。数据集、GT、重标、49 个 paper_ready 由 DSH/Lead 处理；Codex 不并行写它们。当前实现阶段不 sudo、不调用付费模型、不跑 campaign、不 commit。

### 11.1 五组必须修正的验收语义

1. **身份消融与参考隔离**：C0—C4 是五种不同的信号可见性配置，不要求每条真实轨迹输出不同。用有区分能力的合成反例逐项证明屏蔽有效，简单轨迹允许相同输出。原始信号快照必须在最终 resolver 前冻结；禁止使用 checkpoint.identities、最终映射、detector graph 或 launch/role 参考回填预测。pane_pid 只证明 pane shell，不证明模型进程或该进程所有消息。缺锚点保留 unknown。
2. **根因排名与弃权**：@k/MRR 需要冻结的预测排序和明确的相关根因参考，不要求参考本身有顺序；单根参考即可评价。多根以 RootSet 为主，若报 MRR 必须预先定义“首个相关根”。同证据强度按稳定 ID 打破显示并列不代表因果强度；并列规则需披露。参考已知而预测弃权在对应指标中计 miss，并另报 coverage；参考 unknown 不补负例。时间先后不能单独生成 causal DAG；来源实体、引入者、执行者分开。根因是证据支持候选，不能仅凭观察声称反事实“必要贡献”。
3. **检测端点与资格**：先固定 task/scenario 标签、attack execution、capture、reference、prediction 的独立状态；为每项指标明确 eligibility。all-attempts 保留所有记录及失败原因；无 score 的记录不得补 0、Safe 或默默删掉。executed 攻击与 benign 的对照、scenario-level/all-attempts 描述分开报告，不能在 §0.2 的 executed-primary 与 §11 的 all-attempts-primary 间切换。分数若只是现有 triage 量可报告排序性质，不能宣称概率/校准；judge 只有 categorical 输出时不制造 AUROC。A2 含结果、A3 去结果，双方来自同一条有效转录，工具结果中嵌套的诱导指令也是不可信数据。
4. **预算与网格算术**：五个 threshold × 三个 gate center 是 **15 点**，不是 25；保留既定数值，不临时增加 center 凑数。Overhead 按 2026-10-28 精简协议：3 个四条件区组 +12 个两条件区组，共 **36 condition runs /372 并发实例**；预热、校准、诊断和 TLS 微基准另计。旧 60 条件矩阵不再执行，driver/metrics 必须先适配，不能手工删结果绕过原完整性检查。预算确认前仅生成显式 plan，不发付费调用；确认标志不等于硬费用上限。在线 Observer/gate 子样本不属于免费离线 replay，必须另列预算；冻结 Observer 回放只证明条件于已记录观测的结果。
5. **依赖与冻结**：tmux → 原始信号/IdentityView → AttributionResult → score/transcript → overhead/ablation → trace 验收，按包执行；第4节事件、路径、RuleCatalog、持久化缺陷仍需分配到对应包，不能被简短 prompt 的六步省略。路径/捕获不完整不得推断已授权；精确设备白名单不等于允许任意 fd/tty 写。C0 前锁定 analysis fingerprint/config/release；旧 v1 不迁移伪装新 trace。当前 package gate 的 PASS 不是 replay、真实效果或93全计分的证明。

规范核对：排序评测以相关性标签与预测分数为输入，参考无需先排一个总序（[scikit-learn ranking metrics](https://scikit-learn.org/stable/modules/model_evaluation.html#ranking-metrics)）；场景配对比较保持配对、不能把 unit 当独立重复（[SciPy permutation_test](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.permutation_test.html)）。OpenTelemetry GenAI 当前来源已迁到[独立规范仓库](https://github.com/open-telemetry/semantic-conventions-genai)，实现时固定版本，不把标准表示等同于因果证明。

### 11.2 本轮接手前后已验证的实现

- 最终图节点经既有 writer 更新到 authoritative/unattributed；最终告警不再回退 provisional ID。真实 runtime + SQLite reader 的两条失败→通过回归，lead `cargo test --lib finalize_` **7 passed**；phase1 历史 claims 保留原相位，不改写成最终事实。证据：`local/readiness-2026-10-26/team/identity-storage-lead.log`。
- `compare/tools/root_cause_metrics.py` schema **/2**：分支 DAG 的深度按最长有向路径的边数，拒绝环；/1 的总边数差不再称 DAG 深度。失败→通过记录及相关文档测试共 **17 passed**：`team/root-depth-{red,green}.log`。没有新增运行根因结果。
- 既有 W1/W2 路径截断、ActionOutcome、task-context/cache、actor 隔离、request framing 修复已合入当前构建；仅在不与新包冲突时保留，不重做已通过窄测试。
- 接手新限制前的最终发布构建成功；`./scripts/verify.sh` **18/18 PASS**，其中 comparison/annotation **1372 passed,1 skipped,23 subtests**、env **365 passed**；clippy **99/99**，无预算上调。日志 `team/verify-final.log`。这是该时点的基线，后续包另验收。
- 接手新限制前仅完成 benign-api-doc 真实验证：**Safe、0 claims、capture healthy**，9 intents/6112 actions/6103 evidence，Observer 0 calls；构建 `e4f9f2e9…`，输入仍为既有 release。它只验证一个良性通路，不是攻击效果/Observer质量证明。初次 sudo 未认证的失败 attempt 原样保留。新限制后不继续 paid/sudo 验证。
- 旧 GT 工具工作已停止：`team/gt-source-wiring.json` 为 timeout；lead随后仅重跑source/merge窄测试 **26 passed**（`team/gt-source-wiring-lead.log`），没有启动重标。局部代码的协议/全量prepare仍**未验收**，交由数据负责人审阅，不覆盖或删除其已有工作。

### 11.3 当前工作包（2026-10-27）

日志前缀统一为 `local/readiness-2026-10-26/team/`。以下是实现与聚焦验收状态，不能当作当前全仓门禁或真实实验结果。

- [x] **包1 tmux 绑定**：ProcessAudit/v4、runner-events、input_reference、RUN_TAG/RUN_TAG_FILE/FIXTURE_DIR/RUN_DIR、真实 argv/cwd 与双 pane 绑定；pane shell 不冒充 agent。lead 136 项聚焦测试通过，包含无付费模型的真实双 pane 和超时清理；env_reset 由假 sudo 验证，未实际提权。见 `tmux-binding-final-lead.log`、`run-dir-clean-green.log`。
- [x] **包2 身份实验内核**：PRE-resolver 原始快照、冻结 policy、五条件 IdentityView、逐消息/意图/动作的 assigned/unknown 输出及只读 identity CLI。TLS 声明须来自真实捕获 producer；同 PID 多会话依赖实际消息锚点，OS 动作不借消息归属。原生调用共指只在同进程/协议/唯一会话支持时派生，矛盾直接锚污染其关联键，空 ID 不提供信号。lead 当前 identity **78 passed**（`native-alias-owner-green.log`）；此前 identity CLI **14 passed**。这些测试证明实现边界，未证明真实身份有效性。
- [ ] **包3 归因**：原生 tool-call 引用/去重、SQLite v5、SSE 初始 input/损坏与缺 stop、request role 解析及 TextFallback 等级已修；解析器 lead **38 passed**、routing **30 passed**（`response-authority-final-lead.log`）。claim.traceback_ref 在 live/replay 使用同一构建入口。请求内来源见证仅记录较早 result→较晚 assistant 调用的文本回用，冻结实际 MessageId、pointer 与捕获表示哈希，lead **8 passed**（`context-witness-lead.log`）。AttributionResult 已接入分析内核、live 持久化与 replay：执行者、来源入口、发生项 DAG、写入者候选组、并列/弃权分开。lead 补充反例修复身份借用、伪造观测时序、截断悬空边、遗漏来源后虚假唯一排名、历史重现断链及 fallback 借用原生见证，当前 **16 passed**（`attribution-reference-green.log`）。严格只读 CLI 导出包括冻结图悬空引用拒绝，lead **27 passed**（`attribution-graph-closure-green.log`）。lead 进一步修复随机 traceback UUID 导致的引用不闭合，现在实际 SQLite→ReplayExecutor 与 checkpoint.evaluate 的结果包括引用完全一致；claims 聚焦回归 **76 passed**（`traceback-stability-green.log`）。独立运行参考对齐与评测 adapter 仍未完成；不把 legacy membership 称有序链，不虚报运行参考或排名指标。
- [ ] **包4 score/transcript**：A1/A2/A3 record 生成器与 campaign 三臂显式编排已闭环，A2 包含 tool result、A3 去除，同一录制共享输入，输出分臂隔离。重复场景记录在 limit 前拒绝，失败臂不生成付费命令；lead **63 passed**（`appbench-duplicate-green.log`）。`reason→label` 是一次判定的两阶段模型调用，不是两位独立 judge；三臂每 record 至多两次调用的既定计划不能悄悄翻倍。版本化 system_score/run_metrics 已由 authoritative classification 的实际分数生成，在 live 持久化与 replay 共用；lead replay **54 passed**（`run-metrics-lead.log`），缺值不补零，参考未审阅不准入主指标。summary `/2` 用 agreement_rate，不称独立真实准确率。独立运行参考的资格与评分汇总 adapter 尚待闭环，不给 categorical judge 合成 AUROC。
- [ ] **包5 overhead/ablation（历史阶段验收）**：当时提供真正跳过责任 runtime／DB／安全分析器的 `trace --capture-only`，以及当时的 `--disable-classifier`；后者现已随ONNX移除，不是当前有效开关，当前C2使用 `--disable-responsibility-observer`。实际工厂的无 DB／有 DB 和 CLI 冲突测试由 lead 重跑 **11 passed**（`overhead-profiles-lead.log`）。工厂抽取后，原词法接线门禁的 3 项失败已复现；补充显式返回类型→调用→注册追踪及删除反例后，lead 接线测试 **10 passed**（`profile-wiring-lead.log`），没有移除门禁。四条件 driver 和真实分层重建仍未完成；synthetic parser/system-load 与 post-filter 不能冒充完整测量或方法消融。该阶段的15 blocks/60 condition runs为旧矩阵记录；当前改为实验总方案§6的36条件，预热/校准/诊断另列，本轮不发付费实验调用。
- [ ] **包6 trace**：新增只读 `replay --validate-recording`：逐 run 校验双版本标记、checkpoint／fingerprint／图闭包、终结标记、canonical verdict、持久化 claims／tracebacks／attribution／run_metrics 及 evidence／alerts 回放；不写报告或改 DB，失败退出非零。lead 增补同数量替换 ID、负 SQLite 终结时间等反例后，**51 passed**（`recording-negative-end-green.log`）。它仅校验声明的输出范围：未证明持久化图／身份／全部原始采集的完整 parity、后续写入与最终 flush；严格离线 packager、构建／配置准入及独立参考对齐仍需完成。旧 v1 不迁移伪装当前实现。

### 11.4 核心检测修复与剩余限制

- 来源选择与动作授权分离：默认保守匹配；任务包含同命令（包括否定/引用）仅作 task_text_overlap 竞争解释；派生 authorization=Unknown，不单凭文本重现硬判 Unsafe。跨 run/actor 隔离、源 confidentiality、全部匹配替代来源已修；见 `provenance-negation-final.log`、`provenance-alternatives-lead.log`。
- 写路径组件边界、能力交集/子任务收缩已修；`..`、截断、设备/描述符目标不明返回 Unknown。补齐 thread-self/task fd 别名反例后 lead envelope **16 passed**（`envelope-fd-alias-green.log`），不读取宿主文件系统替代捕获目标证据。
- 既有敏感路径判定不再被 graph 的 `/proc`/`/sys` 或 build artifact 过滤覆盖；实际 Read→FileRead 回归验证 `/proc/self/environ`，保留 status 与 dev/null 负例；lead graph **36 passed**（`sensitive-path-lead.log`）。
- 默认场景词表中性化，不嵌入 benchmark 路径、marker 或权威角色；删除仅凭 hog.sh 文件名给出 Unsafe 的规则。共享读取保留拓扑，不由三个读取者直接报恶意/因果传播。lead framework analyzers **150 passed**（`shared-read-alert-green.log`）；自定义词表仍须显式披露，旧结果不可直接作为当前方法指标。
- 原始 Action 在身份判定前保全；未知 actor 只跳过需要主体的流式关联，最终冻结可重算，不伪造身份。这不是所有 syscall 的捕获覆盖保证。
- 未证实或未完成：当前二进制的真实攻击/Observer 验证、真实多 Agent 参考、TLS producer 的完整生命周期/进程 birth 信号、正式并发开销与完整消融。持久化失败仍须由后续发布校验拒收；不得把当前测试或旧 benign smoke 外推为无缺陷或效果理想。

各包由 lead 决定契约、独立检查返回 diff 并重跑验收。DSH 负责有边界的实现/测试，数据集修复仍归 DSH/Lead；本轮不改数据集，不 sudo、不 commit、不发正式付费实验。完整准备状态仍为 **未达到最终实验准入**。

### 11.5 分数与判定的冻结语义

`system_score` 固定取当前 checkpoint 在 authoritative classification 中计算的连续 triage score，并携带 source 名、当前 analysis implementation 和完整 classification config 哈希。它不是最终判定的数字编码，也不是已经校准的恶意概率；不得为了与标签一致而反向修分。非有限或越界分数标 unavailable，不补零。最终 Safe/Unsafe/Uncertain、捕获质量、逐事件身份 coverage 与动作系统调用 outcome 分别输出。

attack attempted/executed/succeeded 是独立运行参考要回答的问题，不能用“出现过一个 syscall／detector 报警”代替，产品导出保持 unreviewed。没有独立运行参考时，场景标签下的排序或响应率必须明确标为 design-conditioned，不能写成已执行攻击的 TPR/FPR；完整统计协议与运行参考准入闭环前不生成正式指标。AppBench 的两次调用固定为单一 judge 的 reason→label，未授权新增第二位 judge 或增加三臂之外的调用。

本轮完整 Rust lib 回归：**1223 passed / 3 既有 ignored**（`current-lib-regression.log`）；新增指标测试核对冻结 checkpoint 的捕获状态，不依赖其他并行测试改变的进程级健康注册表。首轮完整门禁暴露新增指标测试的环境断言，后续完整 Rust 集成检查又发现旧 roundtrip 测试仍固定 schema v4；两项均已修正并验证。最终 `./scripts/verify.sh` **18/18 全过，退出0**（`current-full-verify-complete.log`）：Rust lib 1223 passed / 3既有ignored，comparison/annotator/scripts 1394 passed / 1既有skipped / 23 subtests，env 378 passed，强制全目标 clippy 98 条（既定预算99，未上调）。

剩余准入工作不能由上述门禁代替：四条件开销 driver、从原始 facts 真正重建的层级消融、完整 trace packager／发布准入与参考对齐 adapter。显式外部词表现已冻结文件内容，pilot 实际使用该副本；Observer 模型与身份边界参数纳入配置哈希，哈希在应用隔离配置后生成，不能将这些局部绑定等同完整 pipeline 准入。当前根候选支持带完整路径、成功读写与内容匹配的 Read/Write 链，其他协议／工具／入口缺证据时弃权，不声称已覆盖所有根因。当前 release 二进制及四场景真实采集未在本轮重建/运行；独立运行参考与数据工作包最终准入也不能由代码测试替代。

### 11.6 继续审计的实查状态（2026-10-27）

- **数据 G0 已机械收口（2026-10-27）**：release `9c7099c1…c6ea3`；93 输入=58 attack+35 benign、0 excluded、93 scored；`paper_ready` 93（机械）。`identity-confusion` notes 仍有 A/C 歧义/registry 不可达/needs_human_review，`recon-user-enumeration` v5 2:1 但保留 v4，均已在 GT 披露。A6 `/tmp` blind prepare 已修复；A5 env 计数 298 vs 声明 491（全量 870）。
- **编排已补齐单 Agent**：`campaign` 复用同一 executor，动态列出全部 included 场景；G0 后应为 63 single+30 multi=93，0 excluded。当前尚未跑 30 multi 真实 smoke；没有发正式运行。
- **费用闸门**：直接 AppBench live 入口复用既有 `MTL_ALLOW_PAID=1` 和 real-mode 要求，在读取输入／创建输出／构造客户端前拒绝未授权路径；假客户端验证两阶段调用，没有额外新增 judge。
- **被测构建绑定**：隔离 agent 的 binary／comm 设置与可执行文件内容摘要现已纳入 runtime manifest；运行前后核对字节，变化时保留失败 attempt、清空 final_decision 并标为未观测。原临时副本删除不影响历史 manifest 校验；没有声明二进制的旧记录仍无内容证明，不补造摘要。lead 对 AppBench、全场景编排、runtime manifest 与真实 pilot 假执行入口联合复核 **133 passed**（`current-python-contracts-final-lead.log`）。
- **独立复核的开放项**：单独的 Python capture-health／real-run 接口尚未与冻结实验构建的 implementation 准入贯通；`healthy` 表示捕获状态，不能据此把旧实现录制当成当前方法。完整 packager／正式入口必须显式绑定并验证同一构建。首次全量检查的接线失败日志保留为 `readiness-contracts-full-verify.log`，不得以旧 18/18 结果覆盖本轮实际状态。
- **清理边界**：当前大量 staged／unstaged 删除及未跟踪数据来自既有工作，不在本轮批量删除或重新归档。当前只保留有用途的验收记录，不执行破坏性清理、commit、sudo 或付费实验。
- **最终回归**：修复接线扫描合同后，`./scripts/verify.sh` 再次完整执行并 **18/18 PASS、退出0**（`readiness-contracts-final-verify.log`）：Rust lib 1223 passed／3既有ignored，replay CLI 51 passed，comparison／annotation／scripts 1424 passed／1既有skipped／23 subtests，env 378 passed；强制全目标 clippy 98／预算99。数据包哈希与 clean-room 再验通过；release_id 未变。上述检查不消除本节列出的实验准入缺口，也不证明当前真实攻击效果。

---

## 12. Codex 接手后的复核状态（2026-10-27）

> 本节是 DSH 独立复核，不替代 `local/readiness-2026-10-26/team/` 日志。

### 12.1 已验证完成/部分完成

- `./scripts/verify.sh` 在 `PATH=/home/lzk/.cargo/bin:$PATH` 下 **18/18 PASS**（1223 Rust lib/3 ignored; 1429 comparison/annotation/scripts; 379 env; clippy 98/99）；DSH 在文档清理后复跑仍 18/18。默认 PATH 不含 cargo 时 verify 会误报失败，需在脚本/文档中固定 cargo 查找。
- ONNX runtime 移除：`collector/models` 空、classifier/Python server/CLI flags 删除；Rust/Python tests green。README/architecture/capabilities/collector README/final-rc 的 ONNX 旧文案已由 DSH 清理；overhead-baseline 已标 historical。
- tmux v4 runner-events/binding、IdentityView 5 条件、AttributionResult、system_score/run_metrics、AppBench A1/A2/A3、replay validate-recording 已实现并有聚焦测试；未做真实 campaign。
- `gt_rebuild.py` v3 / 31 tests；**未 full prepare**；A6 `/tmp` prepare 已修（回归测试通过），仍缺全量 prepare/重标。
- 数据：G0 已机械完成，release `9c7099…`；93=58+35、0 excluded、93 paper_ready；`identity-confusion` notes 仍科学未决；`handoff-unicode` 已 blind benign；A6 已修复。

### 12.2 新发现/仍开问题（2026-10-27 G0 验收后）

1. **A6 已修复（保留记录）**：`repo_rel` 已支持外部 `/tmp` work，`prepare --smoke 3` exit 0，新增回归测试；剩余是全量 prepare/重标，不是路径阻断。
2. **P1 科学 notes 未决**：`identity-confusion` 机械 `paper_ready=true`，但 notes 仍记 A/C 歧义、registry 不可达、needs_human_review；`recon-user-enumeration` v5 2:1 benign 但保留 v4 attack；必须作为披露/裁决，不写 unanimous。
3. **P1 @3/MRR 仍不完整**：`AttributionResult.ranked_agents` 只在 `actors.len()==1` 时给单元素；多 writer 直接 None。可以报 @1+RootSet，但 **不能报 @3/MRR**，除非实现多候选确定性排序与参考对齐。
4. **P1 真实 30 multi / C0 未跑**：tmux 双-pane 测试通过，但没有真实 30 multi smoke、没有 C0、没有新 v4 trace/参考。
5. **P1 Overhead/Ablation/trace 未完成**：四条件 driver、从原始 facts 重建的 L0–L3、严格 trace packager/replay 发布准入仍未完成。
6. **P2 A5 计数不一致**：4 个 env 文件实际 298 passed，不是声明 491；`env/tests` 全量 870 通过，属期望/文件集不一致。
7. **P2 工作区风险**：当前 1342 dirty 条目、765 files changed、94k deletions，未 commit；继续改前应做只读快照/明确提交策略。
8. **P2 文档**：README/architecture/capabilities/collector README/final-rc 的 ONNX 旧文案已清；overhead-baseline 已标 historical；历史 archive 仅保留作历史。

### 12.3 当前距实验准入

- 代码：tmux/identity/attribution/score/AppBench 已有实现；**overhead/ablation/trace/real multi** 未完成。
- 数据：G0 机械完成（93/58/35/0，93 paper_ready）；**A6 blind prepare 已修复**；`identity-confusion` 科学 notes 未决。
- 实验：未跑 C0；无新 93 v4 trace/参考；不能写结果。
- 独立验收：A1/A2/A3/A4/A7/A8 通过；A5 env 298 vs 声明 491（全量 870 通过）；A6 PASS（已修）。

