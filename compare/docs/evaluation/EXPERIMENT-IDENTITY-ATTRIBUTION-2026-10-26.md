> **Status 2026-10-29（supersedes 下方 Revision 2 的 blocked 状态）**：  
> - **2a IdentityBench 已完成并 estimable**：30 multi、1,800 units、C0–C4、reference = runner 声明 process lineage（93.1%）+ 3 reviewers（68 consensus，0 conflict）；结果入口 `compare/results/identity/README.md`。  
> - **2b AttributionBench 仍 not_estimable**：27,175 claims 中 0 root / 0 entry / 0 ranked，schema 无 root step/ordered chain；这是 detector 能力缺口，不是标注缺口；见 `compare/results/attribution/README.md`。  
> - 以下正文是 2026-10-26 的设计与可行性审计，保留作为历史依据；当前状态以两个 README 为准。

> **Revision 2 必读（2026-10-26 22:00，吸收可行性审计）**：当前代码/数据**还不具备直接跑这两个实验**，以下都是前置，不是结果：  
> 1. **30 multi 目前会被 harness 判 failed**：tmux-live 不写 `runner-events.jsonl`（仅 process 模式有），`bind_run_artifacts` 硬性要求该文件，missing 即 `final_decision=None/state=failed`；tmux env 还漏 `RUN_TAG/RUN_TAG_FILE`。需先补 tmux v4 runner-events（含 session/pane/role_file）并让 binding 接受；约 2–3 人日。  
> 2. **G0 不是 21 个，是 49 个**：`paper_ready=false` 21 + 缺失 28；30 multi 只有 9 个 ready（5 attack+4 benign）；22 attack multi 中 17 个未 ready。必须先完成盲重标/修复，否则 93 无法冻结。  
> 3. **预测对象不存在**：无 `IdentityView`、无 5 个 resolver 条件（当前只有 full 解析）、`SignalSnapshot` 是 pub(crate) 且不可序列化；无 `AttributionResult`/`ranked_agents`/entry/ordered chain；`claim.traceback_ref` 仍恒 NULL；现有 3-AI 标注管线强制 `root_actor=null`，不能直接产出身份/根因参考。需 W2/W5 先实现，不能“先跑再补”。  
> 4. 因此本文所有“30 multi×1、5 条件、4 方法、3 AI 参考”都是**目标设计**；当前状态是 pending/blocked，不写“已就绪”。  
> 5. 参考只能写“与 AI 参考的 agreement”，不写 accuracy；不写 human κ/gold。

# 身份解析与根因/入口/链：两项顶级实验设计（每项一个实验）

> 日期：2026-10-26。  
> 范围：只覆盖分派给我们的**意见 2 前半（身份）**与**意见 2 后半 + 8 具体（根因/入口/链）**。  
> 约束：每个意见只设计**一个实验**（可含 within-study 条件/基线，不拆成多个实验）；93 场景、每场景 1 run；不做 E11/安全；数据先按 `DATASET-93-REPAIR-PLAN-2026-10-26.md` 修到 G0。  
> 本文件是设计文档，不是结果。

---

## 0. 先定两个“实验”的边界

| 意见 | 一个实验的名字 | 数据 | 产物 |
|---|---|---|---|
| 2a 身份 | **IdentityBench** | 30 个 multi 场景 ×1 run + 5 个 resolver 条件（同一批 trace 上离线计算） | pairwise/B³/false-merge/split/unattributed 表 |
| 2b 根因 | **AttributionBench** | 30 个 multi 场景 ×1 run（N_A attack + 30−N_A benign；N_A 由 G0 盲标决定，当前 22，可能 23），4 个方法/基线 + within-study 消融 | Root/Entry/Chain/abstain 表 |

**E5 合成拓扑不单独成为实验**：作为 IdentityBench 的**阳性控制**（positive control），同一篇实验内报告“受控拓扑上实现符合规格”，不单独声称真实准确率。  
**E7 不拆成多个实验**：full 方法、3 个基线、2 个 within-study 消融（去 entry、去 taint）都在同一个 AttributionBench 内比较。

---

## 1. 共同基础设施与证据边界

### 1.0 当前状态（feasibility 2026-10-26）

- 30 multi 中当前只有 **9 个 `paper_ready=true`（5 attack+4 benign）**；22 attack multi 中 17 个未 ready（盲标后数量可能变）；不能直接跑 30。
- tmux-live 当前不写 `runner-events.jsonl`，`bind_run_artifacts` 会拒绝；需先补 tmux v4 runner-events/session/pane/role_file，否则 multi run 会被判 failed。
- 无 `IdentityView`/5 resolver 条件；无 `AttributionResult`/ranked_agents/entry/ordered chain；`SignalSnapshot` 不可序列化；现有 3-AI 标注器强制 `root_actor=null`，不能产出这两项参考。需 W2/W5 先实现。
- 因此下面的“30 multi×1、5 条件、3 AI 参考”是目标设计；当前是 blocked/pending，不写“已就绪”。

### 1.1 运行集
- 93 场景修复/冻结后，从 93×1 主 campaign 取 **30 个 multi** run（N_A attack + 30−N_A benign；N_A 由 G0 盲标决定，当前 22，可能 23）。
- 每场景 1 run；**不重复**；因此所有 CI 是 scenario-level，不能声称 run 间稳定性。
- 每 run 有：`runner-events.jsonl`、agent logs、process/tmux session、`finalized.db`（v4/checkpoint）、summary、capture-quality、launch manifest。

### 1.2 参考隔离
- 3 个 fresh AI reviewer + 独立 adjudicator；输入是冻结轨迹/task/fixture/launch manifest，**不读 detector 的 identity/claim/root 输出**。
- AI 参考标 `method=model_proposal`；不写人工 κ/gold；报告 reviewer agreement（Fleiss/ pairwise）只作过程诊断。
- unknown/abstain 保留，不强行二分，不当负例。

### 1.3 外部标准边界
- 身份：`gen_ai.agent.id/name` 是**声明**，不是认证；物理/逻辑/声明身份分开；不把 transient PID 当稳定 agent id。[OTel GenAI](https://github.com/open-telemetry/semantic-conventions-genai)
- 因果：用 PROV-O 风格 DAG，不用 span 父子树冒充因果；允许多根/部分序/unknown。[PROV-O](https://www.w3.org/TR/prov-o/)
- 参考：Who&When 的经验——agent 级与 step 级难度差异巨大（最好 53.5% vs 14.2%），必须分开评测、报告 abstention/coverage。[Who&When](https://arxiv.org/abs/2505.00212)
- AgentSight 提供“系统边界 + 跨进程关联”的同路线参照；其 <3% 开销不能迁移到我们。[AgentSight](https://arxiv.org/abs/2508.02736)

---

## 2. IdentityBench（意见 2a）

### 2.1 研究问题
在共享进程、TLS/连接复用、并发子 Agent、委派交接下，多信号身份解析能否比单信号基线更准确地恢复 **agent-level 与 message/action-level** 归属，并诚实标出 unknown？

### 2.2 假设
- H-I1：full（process tree + TLS + conversation + delegation/subagent boundary）在 real multi 上比 PID-only / +PPID / +TLS / +conversation 的 pairwise F1、B³ 更高，false-merge 更低。
- H-I2：full 的 unattributed 不会因强行合并而降低；unknown 是 coverage 的一部分，不算错。
- H-I3：合成 6 拓扑上 full 实现符合规格（阳性控制），但不能替代 real 结果。

### 2.2.1 拓扑普查（G0 后、正式分析前）
逐 run 统计：进程数、每进程 conversation 数、是否 shared process、TLS 连接复用、并发 subagent、delegation。定义 `hard_subset`（shared process/连接复用/并发）。若 hard n<5，主表只报“真实语料不含该失败模式”，并加 controlled hard slice（同一进程 2–3 session/强制连接复用）作为 within-study 条件；不把 controlled 与真实 multi 混报。

### 2.3 设计（一个实验，5 个 within-study 条件）
- 数据：30 multi×1（N_A attack + 30−N_A benign；N_A 由 G0 盲标决定）。
- 条件（同一批冻结 trace 上离线跑 resolver，不新增 agent run）：
  - C0 PID-only
  - C1 PID+PPID
  - C2 C1+TLS participant
  - C3 C2+conversation/session
  - C4 C3+delegation/subagent boundary（full）
- 另加 **C4-deterministic**：同一 C4 重复计算两次，验证确定性（不是 run 重复）。
- 阳性控制：E5 6 拓扑×30=180 traces，C4 只做实现规格（不报真实准确率）。

### 2.4 参考怎么建（关键）
**两层参考，不把 one source 冒充 ground truth：**

1. **Orchestrator-declared anchor（强但有限）**：
   - 从 `runner-events.jsonl`/summary/launch manifest 取 `runner-agent-i`、role_file、session、agent logs、pane/process 归属。
   - 这是“benchmark 声明的 agent”，不是模型自述；只对顶层 agent 有效，不证明内部 subagent。
   - 标 `reference_tier=orchestrator_declared`。

2. **AI-adjudicated reference（覆盖 message/action + 模糊边界）**：
   - 3 个 fresh AI 对每个 run 的**分层样本**标注：所有 handoff/boundary 事件 + 每 run 60 个单元（20 条消息、20 个 tool call/intent、20 个 OS action），按拓扑/agent 数分层。
   - 输入：task、roles、launch manifest（只给 agent 声明，不给 detector 输出）、时间线（message/tool/action 的事件 ID、时间、pid/session/comm 的原始事实）。
   - 输出：每个单元 `agent_id` 或 `unknown`，证据 event id，confidence；不要求解释因果。
   - 裁决：3 个 AI 会话分歧 → 第 4 独立裁决；仍不一致 → `unknown`。
   - 标 `reference_tier=ai_adjudicated`；报告 agreement。

3. **合并规则**：
   - 顶层 agent 的明确 launch anchor 可作为强参考；
   - 内部 subagent/message 归属必须 AI 参考；
   - 两者冲突 → 不静默合并，标 `reference_conflict`，仅在敏感性分析中处理；
   - 主表用 `orchestrator_declared ∪ ai_adjudicated` 的参考，明确每层覆盖。

### 2.4.1 参考依赖与误差
- IdentityBench 先产出 unit→agent reference；AttributionBench 以该 reference 为条件做 scope/root/entry/chain；报告 cross-reference agreement，不把两套 3-AI 参考冒充独立证据。
- 参考只支持“与 AI 参考的 agreement”，不支持 accuracy；报告参考误差敏感性（假设 10%/20% 错误，给方法一致率上下界）。
- 不把 orchestrator-declared anchor 用于 message-level；anchor 只评顶层 agent 子集。

### 2.5 分析单位与指标
- 单位：`(run, unit)`，unit=message/tool/action；agent-level 用每个 run 的 partition。
- 主指标：
  - **pairwise P/R/F1**（micro + macro）
  - **B³ P/R/F1**（coreference/entity resolution 标准）
  - **false-merge rate**：不同参考 agent 被合并的比例
  - **false-split rate**：同一参考 agent 被拆开
  - **unattributed rate**：预测 unknown / 参考 known；单列，不当负例
  - **coverage**：有预测且非 unknown 的比例
- 次指标：按拓扑（shared process/concurrent/connection reuse/subagent/delegation）、按 agent 数、按 attack/benign 分层。
- 参考冲突/unknown 单列；主表用参考可判定子集，附表用全量。

### 2.6 统计
- scenario-level cluster bootstrap（10k）给 CI；pairwise/B³ 按 run 计算后按 scenario 汇总。
- C4 vs C0–C3：scenario-level paired permutation（不使用 unit 级 McNemar），Holm 校正。
- 30 multi 是 convenience sample，不是随机抽样；报告 CI 宽度，不外推总体。
- 1 run/scenario：不能估 run 方差；明确写“测量的是该 run 上的身份归属，不是 agent 运行分布”。
- 阳性控制 E5 只做实现规格，不纳入 real 主表。

### 2.7 预期审稿表（草表）
| 条件 | pairwise F1 [CI] | B³ F1 [CI] | false merge | false split | unattributed | coverage |
|---|---|---|---|---|---|---|
| PID-only | | | | | | |
| +PPID | | | | | | |
| +TLS | | | | | | |
| +conversation | | | | | | |
| full | | | | | | |

- 正文只放一张主表 + 一张按拓扑的分层图；E5 阳性控制放附录。
- 结论模板：full 相对基线在 real multi 上提高/未提高；unknown/coverage 如实报告；不写“准确率 1.000”；只写“与 AI 参考的一致率”。

### 2.8 效度威胁
- 参考是 AI 声明/裁决，不是人工；不能写 human gold/κ。
- 顶层 launch anchor 不证明内部 subagent；message-level 主要靠 AI。
- 30 multi 非随机；1 run；CI 宽。
- 样本分层加权；报告加权/未加权。
- 如果 C4 与 anchor 冲突：不强行合并，敏感性分析；不把冲突当负例。

---

## 3. AttributionBench（意见 2b + 8 具体）

### 3.0.1 23 Type C 映射
旧 23 条 multi attack trace =22 attack multi（A11+B11）+ `handoff-unicode`（当前顶层 benign/excluded，notes attack 3/3）。G0 盲标后若 handoff 回 attack，则 23 attack multi；若 benign，则 22+1。2.0 没有旧 Type C，只有 A/B；主表分 A/B，“Type C 等价集”只指 B 类（当前 B11，可能变）。回复必须写条件映射。

### 3.1 研究问题
在 multi attack run 中，evidence-bounded attribution 能否比“alert 的 primary agent / last action / nearest intent”更准确地定位 **root agent/step、entry point、ordered chain**，并给出可检验的 unknown/abstain？

### 3.2 假设
- H-A1：full（entry + evidence path + ordered partial DAG）在 RootAgent@1、Entry Accuracy、Chain edge F1 上优于 3 个基线。
- H-A2：step-level 指标显著低于 agent-level（与 Who&When 一致）；不把 agent 命中包装成 step 命中。
- H-A3：benign multi 上 full 的 false-root/abstention 可接受；不强行给根因。
- H-A4：去 entry/去 taint 的 within-study 消融会降低 Chain/Entry，验证组件贡献。

### 3.3 设计（一个实验，4 方法 + 2 消融）
- 数据：30 multi×1；主分析 **N_A attack multi**（攻击执行证据存在；N_A 由 G0 盲标决定）；30−N_A benign multi 用于 false-root/abstention。
- 执行门：先用 marker/sink/action/trace 判断 `attack_executed=yes/no/unknown`；**unknown 不进入 root 分母**，单独 coverage。
- 方法/条件（同一 run/checkpoint 上离线）：
  - B0 root = alert primary agent
  - B1 root = last action agent
  - B2 root = nearest-intent agent（时间/参数匹配）
  - M full = evidence-bounded（entry + evidence path + ordered partial DAG + abstain）
  - M-entry-off / M-taint-off：within-study 消融（不是新实验）
- 参考：3 AI fresh + 裁决；不读 detector 输出。

### 3.4 参考协议（scope → localize）
每个 attack run，3 个 AI 独立标注：

1. **Scope**：哪些 agent/step 在失败/攻击链的范围内；是否已执行；哪些事件是决定性。
2. **Root agent(s)**：可多根/unknown；不是“执行者”自动等于根因；给出证据。
3. **Root step/event**：decisive error/action；允许 unknown。
4. **Entry point**：未授权内容/交接/不可信动作第一次进入执行链的事件（定义见 §3.5）。
5. **Chain**：从 entry 到 harmful action 的**有序偏序边**；每条边引用事件；允许 alternatives/missing evidence。
6. **Abstain**：证据不足时明确 unknown，不猜。

输入：task、roles、fixture 摘要、launch manifest、冻结 timeline（message/tool/action/process/network 原始事件）、执行证据；**不含 detector claims/root/identity**。裁决：3 个 AI 会话分歧 → 第 4 独立裁决；仍不一致 → unknown。
参考 tier：`ai_adjudicated`；报告 agreement；不写 human κ/gold。

### 3.5 定义（必须先冻结）
- **Entry point**：第一个“未授权/不可信内容或动作”进入执行链的事件；不是“第一个进程”或“第一个 agent”。可按类型分：untrusted-content entry、handoff entry、privilege entry；主表用“首个 entry”，附表分类型。
- **Root agent**：其决策/动作对 harmful outcome 有证据支持的来源或贡献候选（纯观察不能证明反事实必要性）；执行者/被利用者不自动是根。
- **Root step**：从 entry 到 harmful action 的 decisive event；允许一个区间/多 step，主表用 first decisive。
- **Ordered chain**：由证据边组成的有向偏序 DAG；同一时间不强制全序；允许多根/替代路径。
- **Abstain**：没有足够证据 → unknown；参考 unknown 不补负例、不计深度 0；参考 known 时预测 abstain 计 miss，并单列 coverage。

### 3.6 指标
- **RootAgent@1/@3/MRR**：仅当预测有预先冻结的排序且参考有明确相关根因；参考本身不必有序。多根以 **RootSet P/R/F1** 为主，若报 MRR 采用首个相关根，须开跑前固定该口径并在正文写明。
- **RootStep@1/@3** + step distance（事件步数/时间差）。
- **Entry Accuracy**：agent/事件级；容忍窗口须开跑前固定（如 ±1 event/±500ms；必须冻结；不命中即错）。
- **Chain**：node P/R/F1、edge P/R/F1、ordered edge F1、Depth MAE（仅 known depth）、Boundary Jaccard。
- **Coverage/abstention**：分别报告预测 known/unknown 和参考 known/unknown；参考已知时预测弃权计 miss，参考 unknown 不补负例。
- **False-root on benign**：30−N_A benign multi 上给出 root 的比例。
- 分层：按拓扑（委派链/共谋环/影子 Agent/交接）、按 attack type A/B。

### 3.7 统计
- 单位：run（N_A attack）；scenario-level bootstrap；CI；paired permutation vs B0/B1/B2；Holm。
- N_A attack 样本小（当前 22）；报告 CI 宽度，不外推；子组仅探索。
- 1 run/scenario：不能估 run 方差；明确限制。
- 参考冲突/unknown 单列；主表在可判定子集，附表全量。

### 3.8 预期审稿表（草表）
| 方法 | RootAgent@1 | RootSet F1 | Entry Acc | Chain edge F1 | Depth MAE | abstain |
|---|---|---|---|---|---|---|
| alert agent | | | | | | |
| last action | | | | | | |
| nearest intent | | | | | | |
| full | | | | | | |
| full - entry | | | | | | |
| full - taint | | | | | | |

- 正文只放主表 + 一张错误类型图；不把 0.681 等无 artifact 数字写入。
- 结论模板：agent-level 较好、step-level 仍难（引用 Who&When）；full 相对基线的增益/无增益都报告；unknown/coverage 单列。

### 3.9 效度威胁
- AI 参考不是人类；root/step 本身有解释歧义；3+裁决只降低随机误差，不消除偏差。
- N_A attack 非随机；1 run；CI 宽。
- 执行门 unknown 会减少分母；必须披露。
- 不用 design-level causal proposal 当 runtime truth；不把 detector 的 identity 当参考。
- 不为了 Root@3 造排序；无排序只报 set/coverage。

---

## 4. 两项实验共用的数据/代码需求（反推 G0）

### 4.1 数据集/运行
- 93 全 scored；30 multi；handoff-unicode 修 benign；identity-confusion 修 actor/hash；9 个原 excluded 修到有明确 task/fixture/oracle。
- 每 multi run 必须有：
  - launch manifest（runner-agent-i、role_file、session/pane、process tree）；
  - agent logs；
  - `runner-events.jsonl`；
  - v4 DB + checkpoint；
  - execution evidence（marker/sink/action）用于 attack_executed。
- 如果某场景没有可观测交接/多进程，不能计入 multi 参考；在 G0 标出。

### 4.2 代码/分析
- Identity：`IdentityView` 输出 agent/message/conversation/unknown；5 个条件可配置；确定性；导出 per-unit prediction。
- Attribution：`AttributionResult` 输出 entry/root candidates/ordered partial DAG/abstain；claim.traceback_ref；per-run prediction JSON。
- 参考/预测分离：`reference/` 与 `prediction/` 不同目录；评分器不读 detector。
- Metrics：pairwise/B³、Root@k/MRR/RootSet、Entry、Chain edge P/R/F1、Depth/M boundary、coverage；无 reference 返回 None。
- 统计脚本：cluster bootstrap、paired permutation、Holm、agreement；分析计划开跑前固定并落成 JSON。

### 4.3 标注 artifact
- `identity/reference/<run_id>.json`：per-unit agent/unknown/evidence/tier。
- `attribution/reference/<run_id>.json`：scope/root/step/entry/chain/unknown/evidence。
- `reviewer_sessions/`：3 fresh 会话、prompt hash、model、时间；adjudication。
- agreement report；不写 human κ。

---

## 5. 执行顺序（只这两项）

```text
G0 数据 93 修复/冻结
  → E0 4-scenario pilot（含 1 multi）
  → E1 93×1（这两项复用 multi run）
  → 生成 v4 trace/checkpoint
  → IdentityBench: 30 multi ×5 条件（离线）+ 3 AI 参考
  → AttributionBench: 30 multi ×4 方法 + 2 消融（离线）+ 3 AI 参考
  → 统计/表/图/claims
```

**不先跑付费大 campaign 再设计**：这两个实验的 reference/annotation codebook、指标、预测 schema 必须先冻结；否则跑完也不能评分。

---

## 6. 开跑前固定的分析口径（两项共用）

- 主 cohort：30 multi（N_A attack +30−N_A benign；N_A 由 G0 盲标决定）；1 run；无 repeat。
- 主 endpoint：
  - Identity：full vs best baseline 的 pairwise F1、B³、false-merge；
  - Attribution：full vs best baseline 的 RootAgent@1、Entry、Chain edge F1。
- 次 endpoint：分层/消融/coverage。
- 排除：attack_executed=unknown 的 run 不进 root 分母；reference unknown 单列。
- 统计：cluster bootstrap 10k、paired permutation、Holm；CI；不做事后换指标。
- 停止：G0 失败/E0 capture 失败/参考协议未定稿 → 不跑主实验。
- 披露：AI 参考非人工；1 run；30 multi 非随机；无独立开发集；root/step 定义歧义。

---

## 7. 一句话给审稿人

> 我们把“身份”和“根因”分别做成一个可复现的实验：身份用 30 个真实多 Agent run、5 个 resolver 条件和两层参考（launch anchor + 3 AI 裁决）报告 pairwise/B³/false-merge/unknown（与 AI 参考的 agreement，不写 accuracy）；根因用同一批 run、4 个方法和 2 个消融、3 AI scope→localize 参考报告 Root/Entry/Chain/abstain。我们不把合成拓扑当真实准确率，不把 agent 命中当 step 命中，不用设计级因果提案冒充运行真值。
