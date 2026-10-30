> **Status 2026-10-29**：T5 IdentityBench 已 **estimable**（30 multi，C0–C4；`compare/results/identity/README.md`）；T6 AttributionBench **not_estimable**（detector 0 root/0 entry，`compare/results/attribution/README.md`）；T7 PBC estimable；T8 overhead estimable；T4-app 已 **partial 完成**（M 与 R-Judge A2 binary，`compare/results/external/baselines/`）；T7 ablation 已 **estimable（two-endpoint：ranking + operating point，0 新增付费调用）**，见 `compare/results/ablation/README.md`；sensitivity 已 **estimable（110 点阈值 + 六维权重/gate OAT + 1000 次 LHS，fixed recorded Observer）**，见 `compare/results/sensitivity/README.md`。本文以下状态表保留为设计时记录。

> **文件组织更新（2026-10-29）**：开销实验统一存放于 [compare/results/overhead/](../../compare/results/overhead/README.md)。当前采用一张综合图与精简分账表；下文旧 F2、14 图／8 表名称仅指历史导出，旧图表已归档。原始数值与冻结证据保留。

# MtAgentLens 七项分派任务实验设计（Revision 2，已吸收对抗审）

> 日期：2026-10-26。  
> 本文件是被清理后的执行协议之一；总入口见 `docs/archive/engineering/handoffs/FINAL-CODEX-HANDOFF-2026-10-26.md`。旧草稿已清理，不再作为口径。接手复核合同见总入口 §11；冲突处以其为准。
> 关键修正：**7 项是分派子任务，不是 7 条原始审稿意见**；**当前分母尚未冻结**；**C0 不是第 8 个检测实验**；**检测主 endpoint 不再是 4–10 个 Unsafe 的 TPR**；**5 张结果表，不是 7 张**。

> **2026-10-28 意见 5 执行结果（已实测完成，非计划）**：§6 为用户确认的精简开销协议。正式批次 `compare/results/overhead/raw/full-system/` 已完成 **36/36 条件**、15 配对区组、372 个 Agent 实例；execution report `complete=true`，T8/F2 由 `overhead_report.py` 严格完整模式导出到 `compare/results/overhead/raw/full-system/report/`（`T8.md`/`T8.csv`/`F2.pdf|svg|png`）；论文写作版见 [开销实验统一报告](../../compare/results/overhead/README.md)（14 张独立图 + 8 张表）。最后一次 32 并发 C3 曾被 300 s harness 关闭上限截断；按固定恢复规则归档该尝试并用 600 s 上限重跑同一冻结条件，矩阵与测量口径未改，失败尝试的费用与部分数据保留。正式 36 条件峰时费用上界合计 **$0.6204**（含归档失败尝试 $0.0309）；本实验全部 cohort（含校准/pilot/丢弃批次/恢复）合计 **$1.6465**（观测/已知 usage 上界 $1.0265 + 未计量请求准备金 $0.62），预算 $5。其他实验不变。详细结果见 §6.6。

---

## 0. 先冻结三件事（G0）

### 0.1 分母（2026-10-27 G0 后）
- 当前包 release `9c7099c1d36d21af025f5050d462b273cc04aef0c69d3ff72e902779372c6ea3`；93 =58 attack（A47/B11）+35 benign；**0 `detection_excluded`**；93 全计分。
- multi 30=22 attack+8 benign；`handoff-unicode` 已经 3 路盲标（27/27 benign）+裁决，成为第 8 个 multi benign。旧 23 Type C =22 attack multi + handoff（现 benign），不是 23 attack。
- 旧 84=58+26 仅历史；不再混用。
- 93 全 `paper_ready` 是**机械判据**；`identity-confusion` notes 仍有 A/C 歧义/registry 不可达/needs_human_review，不能把机械 flag 当完整科学裁决。
- 独立验收：A2/A4/A7/A8 通过；A6 `/tmp` blind prepare 已修复（`repo_rel` 支持外部 work，回归测试通过）；A5 env 声明 491 但所列 4 文件实际 298（全量 870 通过）。见 `local/history/audits/audit-2026-10-27/G0-VERIFICATION.md`。
- AppBench 当前只保留 R-Judge A2：93 records/186 model calls；输入消融已删除。

### 0.2 执行状态
所有检测/基线/消融表都加 `attack_executed ∈ {yes,no,unknown}`：
- primary 只在 **executed=yes** 上；`all`/`no` 单列；unknown 不进分母。
- AppBench judge 明确“若 agent 拒绝/未执行，输出 not_executed，不按 FN 计”。
- 报告每臂 refusal/not-executed 率；跨 release 先对齐执行率。
- 依据：fix4 中 30/43 攻击被测模型拒绝执行；未执行攻击不是 benign，也不是 detector FN。

### 0.3 参考来源与诚信
- 3 fresh AI + 裁决；`method=model_proposal`；**不写 human κ/gold**。
- **更正**：原稿“3 位安全研究人员 + Fleiss κ=0.87”是错误表述；实际是 AI reviewer agents；修改稿和回复信必须显式更正，不能静默删除。
- 根因/入口/链参考只来自 3-AI 冻结轨迹标注；`_causal` 设计级 proposal 只作 `scenario_design`，**绝不作为 runtime 参考**。
- 旧 fix4 只作 historical/回归；不与 93 主表混。

---

## 0.4 当前硬前置（feasibility 审计，2026-10-26）

这些不是“实验设计的一部分”，而是**跑 C0 之前必须实现/修复**的阻塞项：

1. **30 multi 当前会被 harness 判 failed**：tmux-live 不写 `runner-events.jsonl`（仅 process 模式有），`bind_run_artifacts` 硬性要求该文件；tmux env 漏 `RUN_TAG/RUN_TAG_FILE`。需先补 tmux v4 runner-events（session/pane/role_file）并让 binding 接受；约 2–3 人日。否则 93×1 只有 63 single 可完成。
2. **G0 已机械完成**：93/58/35、0 excluded、93 paper_ready；`identity-confusion` notes 仍科学未决；A6 `/tmp` blind prepare 已修复；A5 env 计数 298 vs 声明 491。**不能把 G0 机械通过当实验准入**。
3. **Identity/Attribution 预测对象不存在**：无 IdentityView/5 条件；SignalSnapshot 不可序列化；无 AttributionResult/ranked_agents/entry/ordered chain；claim.traceback_ref 恒 NULL；现有 3-AI 标注器强制 root_actor=null。必须 W2/W5 先实现，不能先跑。
4. **AppBench 转录覆盖不足**：当前约 45/93 可解析；Type C/无转录 run 单列；不能直接承诺 93 records。A2 为 full trace（含 tool result）；每个 record judge 2 次调用，93 records=186 calls。
5. **旧 v1 trace 不能 replay**；新 93 DB 必须在 C0 后生成；fix4 不混主表。

**结论**：C0 之前有明确代码/数据工程；本设计是目标协议，不是“已就绪”。

## 0.5 可执行性判定（feasibility 审计，2026-10-26）

| 交付 | 现在能跑？ | 决定性缺口 | 估算 |
|---|---|---|---|
| G0 数据 | **机械完成** | 93/58/35/0、93 paper_ready；identity notes 未决；A6 已修复；A5 计数不一致 | 仅 A6 + 科学 notes 待收口 |
| C0 93×1 | 部分：63 single 可跑；30 multi 会被 harness 判 failed | tmux 无 runner-events/input_reference，binding 拒绝；漏 RUN_TAG；无 93 编排/finalize cap | 4–6 人日 + 6–12h |
| IdentityBench | 否 | 无 5 条件；SignalSnapshot 不可重建；无 per-unit 预测/false-merge 指标；3-AI 管线禁止身份 | 4–6 + 3–4 人日 + 3–5 天标注 |
| AttributionBench | 否 | 无 AttributionResult/entry/ranked/ordered chain；claim.traceback_ref NULL；metrics 无 producer | 5–8 + 4–6 人日 + 5–7 天标注 |
| AppBench | 部分（代码 ~70%，数据 0%） | e8 只出 A2；未接 select_valid_runs；A2 设计/代码相反；93 records=186 calls；multi transcript 未验证 | 1 人日 + 2–4h |
| OverheadBench | 已完成（2026-10-28） | 36/36 正式条件、15 配对区组、372 实例；driver/metrics 已适配 §6，成功 TLS 路径、真实 usage/告警关联与 C1 诊断均已实测；T8/F2 在 `compare/results/overhead/raw/full-system/report/` | 仅单主机/单内核/单一负载/3 次描述性重复；Word 合入与 claims 登记待做 |
| SensitivityBench | 是（有 93 checkpoint 后） | 缺 fingerprint/gate 模式/无门控/族划分 | 1–2 人日 |
| PBCBench | 部分（有 93 checkpoint 后） | e2 可用；缺 logistic/十分位/族 CV；PBC 口径未冻结 | 2–3 人日 |
| AblationBench | 否（~20%） | 只有六维 LOO；无 L0/L0.5/L1–L3 开关/ gate/ Observer/图/PBC 关闭 | 5–8 人日 |
| 新 trace | 否 | replay 指纹不匹配；sanitizer 改 workspace_prefix；无 validator/replay gate；旧 87 v1 仍宣称 replay | 4–6 人日 + 2–8GB |

**历史总前置量估算（2026-10-26）为 48–74 人日**（代码+数据），加 3-AI 标注日历时间；2–3 人并行约 6–9 周，单人约 10–14 周。此估算不是当前排期，尤其不适用于已收敛的 §6；不能据此安排开销实验。设计完成不等于实验已就绪。

## 1. 任务边界：7 项分派任务，不是 7 条意见

| # | 分派任务 | 实验 | 不回答 |
|---|---|---|---|
| 1 | 2a 身份 | IdentityBench | 根因、检测 |
| 2 | 2b+8 根因/入口/链 | AttributionBench | 身份准确率、检测 |
| 3 | 3a+8 基线公平性 | AppBench | PBC 机制、层级贡献 |
| 4 | 5 完整开销 | OverheadBench | 检测、归因 |
| 5 | 6 独立性/敏感性 | SensitivityBench | 层级贡献、独立测试（只能披露） |
| 6 | 7 PBC 混杂 | PBCBench | 参数稳定性、层级贡献 |
| 7 | 8 消融粒度 | AblationBench | 参数稳定性（只引用 T5C） |

**参考依赖**：IdentityBench 先产出 unit→agent reference；AttributionBench 以该 reference 为条件做 scope/root/entry/chain，并报告 cross-reference agreement（若独立重做）。不把两套 3-AI 参考冒充独立证据；成本按实际会话数报（30×3 +30×3 或共享）。

原始意见 1（判定流水线）、3b（InjecAgent 协议）、4（F1 算术）、同级方法（能力矩阵）属于文档/他组任务；本组不承诺“7 条意见全部闭环”。

### 1.1 不在 7 个实验内、但同一封回复必须覆盖的文档项

| 项 | 负责人/材料 | 本组动作 |
|---|---|---|
| 意见 1 判定流水线/四分数/四态 | 论文 §3.5/算法 3 + T3 | 不新实验；引用 T3 |
| 意见 3b InjecAgent 协议 | E10 文档/他组 | 不新实验；披露未 OS 执行、2,108/149 |
| 意见 4 F1 算术 | 论文表 11 + claims | 不新实验；重算/撤回无 artifact 数字 |
| 同级方法能力矩阵 | T1/他组 | 不跑 E11；不承诺性能比较 |
| 人工 κ/金标更正 | 回复信 + 根因指标定义 | 必做：原文“3 位安全研究人员/κ=0.87”更正为 AI reviewers；不写 human κ/gold |
| 编辑提交合规 | 11-03 PDF/修改说明 | 不在实验内 |

**注意**：不要把“7 个实验”写成“7 条原始意见全覆盖”；回复矩阵要单列这些文档项。

---

## 2. C0：共同 campaign（基础设施，不是实验）

- 93×1；每场景 1 run；约 5–7h；不重复；C0 没有独立 primary endpoint/表。
- 产出：v4 `finalized.db`（analysis_checkpoints、runs.final_decision）、runner-events、agent logs、launch manifest、execution evidence、capture-quality。
- 旧 v1 trace review-only；新 trace 93 DB 由 C0 生成。
- **检测系统结果只作为 AppBench 的 M 列出现一次**；PBC/Ablation 只报分层/Δ，不重复绝对值；不另立“E1 总检测表”。

### C0 资格
- E0 4-scenario pilot（1 A、1 B/multi、1 single benign、1 multi benign）：capture healthy、DB v4/checkpoint、export、replay、annotation prepare 全通过。
- 任一失败停止；pilot 不入分母。

---

## 3. IdentityBench（2a）

### 3.1 先做拓扑普查（P0 前置）
在 C0 后、分析前统计每个 multi run：
- 进程数、每进程 conversation 数；
- 是否有 shared process、TLS 连接复用、并发 subagent、delegation；
- `hard_subset` n（共享进程/连接复用/并发）。
若 hard n<5：主表只报“真实语料不含该失败模式”，并加 **controlled hard slice**（同一进程 2–3 session / 强制连接复用）作为 within-study 条件；不与真实 multi 混报。

### 3.2 条件（同一批 trace，pre-checkpoint）
必须绕过 `checkpoint.identities`（authoritative 会塌缩），用原始 SignalSnapshot 重算：
- C0 PID-only
- C1 PID+PPID
- C2 +TLS
- C3 +conversation
- C4 +delegation/subagent（full）
- C4 重算两次验证确定性
- E5 合成只作 **positive control**，不单独成实验/不进主表。

### 3.3 参考（不是“准确率”）
- Tier A `orchestrator_declared`：runner-agent-i/role/session/pane/process；只对顶层 agent，不评 message-level。
- Tier B `ai_adjudicated`：3 fresh AI 对分层样本（所有 handoff/boundary + 每 run 60 units：20 message/20 intent/20 action）标 agent/unknown + 证据；第 4 裁决；冲突单列。
- **2026-10-28 实现状态**：detector-blind native reference 已支持 conditional declared identity/root（仅 captured native anchor，PID/role 不作逻辑身份）并与 replay 预测导出按 source DB SHA256 绑定；但 per-run unit cohort 尚未按本节 60-unit 规则冻结，参考生产暂不启动。
- 报告 **agreement with ai_adjudicated reference**，不写 accuracy；加参考误差敏感性（假设 10%/20% 参考错误，给上下界）。
- 主表分层：anchor-verifiable / AI-only / conflict；不把设计角色当独立观测。

### 3.4 指标/统计
- pairwise P/R/F1、B³、false-merge/split、unattributed、coverage；按拓扑分层。
- 每个 run 先算指标，再 scenario-level paired permutation/bootstrap 10k；**不用 unit 级 McNemar**（伪重复）；unit 级只描述。
- 1 run：CI 条件于该 run；不写 run 方差。

### 3.5 能写/不能写
- 可写：“与 AI 参考的一致率、B³、false-merge、coverage”；不可写：“身份准确率”“1.000”“解决共享进程”。

---

## 4. AttributionBench（2b+8）

### 4.1 队列
- C0 30 multi×1；attack multi 数 =N_A（当前 22，盲标后可能 23）；benign multi =30−N_A。
- 先判 attack_executed；只有 executed=yes 进 root 分母；no/unknown 单列；N_A 由 G0 盲标决定。
- **23 Type C 映射**必须写条件：22 attack multi + handoff-unicode（当前 benign/excluded，notes attack 3/3）；G0 后按盲标更新。

### 4.2 方法/条件
- B0 root=alert primary agent
- B1 last action
- B2 nearest intent
- M full evidence-bounded（entry + evidence path + ordered partial DAG + abstain）
- M-entry-off / M-taint-off（within-study）
- **不做 8 个实验**；所有条件同一参考/同一 run。

### 4.3 参考
- 3 AI：先 scope（相关 agent/step/attack_executed），后 localize（root agent(s)、root step、entry、ordered chain、evidence、unknown）。
- **2026-10-28 实现状态**：当前 native reference 仍把 run 内每个 action 当作 target（真实 run 可达 5,440 action，单 packet 2.89 MB，超过 review batch 700 KB 上限），与本节“先 scope 后 localize”不一致；必须先实现 phase-1 scope 或开跑前固定的 cohort manifest 才能生产参考。
- root 可多根/unknown；执行者≠根因。
- **排序**：预测有冻结排序、参考有明确相关根因时可报 @1/@3/MRR，参考本身无需排序；多根以 RootSet P/R/F1 为主，MRR若采用首个相关根，须开跑前固定该口径。预测无排序时只报 RootSet/coverage，**不伪造 @3**。代码必须实现 `ranked_agents`（按证据强度/时序，非概率）并测试；未实现前在回复里明确“Top-k 未报”。
- Entry 定义：**图距离/边序**为主（entry→harmful action 的边序），时间窗只做次口径；开跑前固定两个口径并报敏感性，避免 ±500ms 在快慢 run 上不等价。

### 4.4 指标/统计
- RootAgent@1（预测有排序且参考根因已知）、RootSet P/R/F1、RootStep@1、Entry Acc、Chain node/edge P/R/F1、Depth MAE、Boundary Jaccard、coverage/abstain。
- N_A attack（当前 22，盲标后 22/23）：scenario bootstrap；Holm family 明确；样本功效低，主分析估计+CI，显著性标 exploratory；不承诺具体 18/22 显著。
- 30−N_A benign：false-root/abstain。

### 4.5 能写/不能写
- 可写：“与 3-AI 参考的 RootSet/Entry/Chain 估计 + abstain”；不可写：“根因准确率”“Type C 23 例全部”“@3 已实现”（除非预测排序与明确相关根因参考均具备）。

---

## 5. AppBench（3a+8）

### 5.1 定义（2026-10-29 收敛）
- **R-Judge A2 = full application trace（task + tool calls + tool results），唯一主基线**。
- M = 系统级结果，只出现一次。
- 93 场景 × 1 = 93 records；每 record 2 次调用（reason + label）= 186 calls；0 parse error。
- 不再保留 task-only / no-tool-result 输入臂；它们属于输入消融，已从论文表与实验代码路径移除。

### 5.2 数据/公平性
- 93 canonical runs；app transcript 从 runner/agent logs 归档；报告 transcript 覆盖率、截断与 multi-agent 拼接。
- 同一 judge/model/输出 schema；记录输入长度；不把“信息更多”说成“同信息”。
- M vs A2 是“系统级观测 vs 应用层轨迹”的观测边界比较，不能单独宣称同输入算法优越。

### 5.3 指标/统计
- 两个工具都输出 binary safe/unsafe；**AUROC/AUPRC 不可估计**，只报 TPR/FPR/coverage（PP 主口径）与 Wilson CI。
- 主表：M、Agent Sentinel-style、LLM Guard、Meta Prompt Guard、R-Judge A2；McNemar vs M + Holm。
- 不把 binary TPR 写成概率分数。

---

## 6. OverheadBench（5）

### 6.1 目标、边界与状态

只回应意见 5：给出完整部署成本，说明责任图和 Observer 是否计入，并报告并发下的捕获质量、告警延迟和内存变化；以轻量探针统计补充说明底层执行成本。低开销是待验证结论，不是筛选结果或停止实验的条件。

本实验独立于 DSH 的 **93×1 主 campaign**；其 DB 不能替代无监测对照。下表的 C0 仅表示开销基线，不是 §2 的公共 campaign。单主机、单内核、同一固定版本，**一个 collector 监测 N 个并发 Agent 实例**。同机正式测量等待 DSH 完成后的独占窗口，不停止或修改现有 93 场景流程；PID/cgroup 隔离不能消除 CPU/磁盘/API 配额争用。

仅用一套固定 Agent 负载：固定输入与工具操作流程，覆盖文件、进程、TLS 并能正常进入真实 Observer 路径；以独立操作凭据核查成功工作量。正式运行前固定负载、数据大小、每实例工作量、warmup、缓存初态、条件间恢复、超时、重试和费用预算。不按测得开销挑负载，不改判定阈值强行触发 Observer。只做必要的小规模校准，不新增任务家族、长期泄漏、多主机/多模型、逐探针并发扫描或挂载前后微基准矩阵。

### 6.2 最小运行矩阵

| 条件 | 运行内容 | 并发 | 每格重复 |
|---|---|---|---|
| C0 | Agent alone，无 collector | 1/4/8/16/32 | 3 |
| C1 | `trace --capture-only`，仅采集 | 1 | 3 |
| C2 | `trace --disable-responsibility-observer`，采集 + runtime + DB，Observer off | 1 | 3 |
| C3 | 默认 `trace`，完整系统 + 真实 Observer | 1/4/8/16/32 | 3 |

- 并发 1 做组件分账：4 条件 ×3 = **12 condition runs**；并发 4/8/16/32 做 C0/C3 扩展性：4×2×3 = **24 condition runs**。合计 **36 个正式运行**，不是 36 个 Agent 实例。
- 共 **15 个配对区组**：3 个四条件区组 + 12 个两条件区组。并发实例总数为 `3×4×1 + 3×2×(4+8+16+32) = 372`；warmup、校准、TLS 微基准及短诊断窗口另列，不混入 36 次或费用分母。
- 区组内条件顺序用固定随机种子生成并归档；各条件独立输出与状态恢复。C1–C3 的 TLS/process 等采集参数一致，只有分析边界不同。ONNX 已移除，不使用 `--lite` / `--disable-classifier`。
- C1−C0 表示采集路径增量；**C2−C1 是 runtime 与持久化组合增量，不是 graph-only**；C3−C2 辅助解释启用 Observer 的影响，组件差值仅在并发 1 有配对证据。
- C3 核验实际模型请求/响应及 usage；provider 配置标记不足以证明真实调用。完整在线条件不以 Mock 代替。记录 429、超时与重试，不把 API 限流默认为本地 collector 饱和。

### 6.3 最小指标与证据

| 审稿问题 | 报告内容 | 口径约束 |
|---|---|---|
| 完整系统成本 | 固定工作量耗时增幅、吞吐/完成率；collector 与工作负载各自 CPU 时间/利用率、峰值 RSS；trace/DB/WAL 体积 | C3 对 C0 配对比较；CPU 注明单核百分比，避免子进程重复计数；BPF 执行不等于 collector 用户态 CPU |
| 责任图成本 | 轻量图更新计时、次数与图规模，报告均值/可支持的分位数 | 不重建 tracing 平台；图更新与快照/写库边界明确，C2 总差值不能冒充图维护计时 |
| Observer | 实际调用数、缓存命中、请求延迟、失败/重试、输入/输出 token、费用 | Agent 费用与 Observer 增量分别记；usage 与模型价格日期可核查；未知不记零，失败尝试成本保留 |
| 捕获完整性 | 已成功受控操作在 raw/persisted 证据中的缺失比例；TLS/process 各来源丢弃、截断与 persist 丢弃/写失败 | 凭据缺失先判工作量未完成；成功操作数才是操作缺失分母，不能用原始事件计数代替。操作可能对应多个事件，不将不同层计数相加成一个 loss 率 |
| 告警延迟 | 可验证触发事件→最终可见告警 p50/p95、样本数、未关联/未告警/超时覆盖 | 沿真实 evidence/claim/alert 引用或共享锚点并核对时钟；不靠时间邻近匹配。不支持的条件记 N/A，缺失不记零；不强制 p99 或内部阶段分解 |
| 内存增长 | 各并发档 RSS 峰值及测量窗口内时间序列 | 区分随并发与随时间的变化；固定工作量窗口不能支持长期无泄漏结论 |

主要比较为各并发下 C3 相对 C0 的固定成功工作量耗时增幅；未完成时同时报告完成率、超时与失败原因，不把少做工作解释为加速。统计以独立运行/配对区组为单位，三次重复报告单次值、汇总和范围；事件数与采样点数不是独立重复数，不用 n=3 承诺精确尾部分位数或复杂显著性结论。告警 p50/p95 注明运行内样本数及重复间变化。所有尝试留痕，预先固定基础设施失败的重试规则；实际饱和、丢失和不利结果保留，不静默剔除。

### 6.4 探针补充：TLS 微基准 + 其他程序顺带采样

复用 `compare/scripts/perf/legacy/run_experiments.py` 的 `bpftool` 累计统计方法及 `compare/scripts/perf/bench_targets/ssl_loop.c` 的循环负载，不重构通用评测框架。**TLS 仅一个固定数据大小的短微基准，三次重复**；其他正式启用的进程/syscall 程序只复用并发 1 的 C1 负载，在主性能计时之外的短诊断窗口随三次重复采样，不建独立负载或额外并发矩阵。

最小必要修正：

1. 现有 `ssl_loop.c` 未建立连接、未检查读写成功，且注释残留冲突标记。使用内存 BIO 等本地连接完成握手后，预热并循环成功读写固定载荷；检查返回值与实际完成次数，覆盖数据复制/事件提交路径。读写与握手测量阶段分开，失败调用不能伪装成功采集成本。
2. 只绑定本次加载的 **program ID/挂载清单**，取预热后测量窗口的计数差值；不按系统全局 `SSL` 名称或既往累计值筛选。核对采集附着和实际事件产出；未触发程序标记未覆盖，不能记为 0 ns。
3. 每程序 `ns/次 = Δrun_time_ns / Δrun_cnt`；TLS 每成功操作成本为相关程序 `ΣΔrun_time_ns / 成功 SSL 读写操作数`。一读一写计两次；有多端调用时明确计入的端点及所有实际操作，不按循环次数猜测分母。不同程序平均值不能直接相加。
4. 其他程序报告触发次数与该负载下的平均执行时间，可能含快速过滤返回，不声称每个有效事件的完整处理成本。共享 syscall 程序不按文件 API 重复拆分；一次性启动扫描单列。累计统计不能生成单次调用 p95/p99。
5. 诊断统计自身有开销，仅在短诊断/TLS 测量窗口开启并恢复原状态；主系统正式计时关闭，不将两种测量混合。该统计仅是 BPF 程序执行成本，不涵盖全部 hook 触发、用户态消费或完整采集延迟。

**历史口径纠错**：`compare/results/overhead/archives/legacy-218ns-performance.tar.gz::perf/perf_report.json` 的 `218.2624` 来自三个程序均值 `101.3125 + 57.5763 + 59.3736` 相加，且触发次数不同，配套旧负载主要为未连接调用。撤回“单次探针/一次 SSL 操作约 218 ns”的解释；原始 artifact 保留追溯，不改写数据。`218 ns / 1–5 ms = 0.0044%–0.0218%` 仅是原稿算术纠错，不能证明读写或完整系统成本；新论文不再以握手时长作该开销分母。旧 36.9 MB 和含 ONNX 的 1.3 GiB 也不作当前系统结论。

### 6.5 执行前置与交付

1. 冻结上述负载参数、测量版本（源码/二进制/config/input 指纹）、恢复/随机化/失败规则与预算。DSH 93 场景继续原流程，正式开销占用独立窗口。用户已单独授权本次执行；不停止或修改主 campaign。
2. 已将 `overhead_driver.py` / `overhead_metrics.py` 适配为 **36 条件、15 区组、372 个 Agent 实例**。`overhead_workload.py` 提供原生 Claude Code + DeepSeek Flash 固定真实任务与每实例 16 个成功操作凭据；`overhead_evidence.py` 从真实 Observer attempt、图更新计时、DB 引用和最终消息 emission 构造补充证据，不能用 receipt 自报字段代替。`run_overhead_experiment.py` 负责冻结、独立预跑、费用控制和正式运行；`probe_overhead.py` / `overhead_diagnostics.py` 负责主计时之外的探针测量；`overhead_report.py` 严格检查完整正式批次后导出 T8/F2。
3. 小规模校准验证成功 TLS 路径、挂载、凭据、告警关联、真实 Observer usage、退出落盘与诊断开关；校准单独归档，正式前固定参数，不按好坏结果追加重复。预算确认标志不等于 provider 硬上限；缺失成本时停止继续付费并核查，记录实际账本。
4. 按冻结矩阵运行并独立核对原始证据。**T8 一张分账表**：A 探针统计（主文按功能组概括、逐程序明细附录），B 完整成本及组件增量；**F2 一张多面板图**：并发 vs loss/告警 p95/RSS/CPU。同步 §6.4/表12/图8 与意见5回复，必要明细进附录，不新建冗余报告。

验收：36 个正式条件均有尝试记录和状态；不完整 cohort 不冒充完整结论；关键指标来源可追溯、缺失可见、费用可核查。本批已按该标准完成（36/36，见 §6.6）；其他未完成实验仍只能称“协议已确认/实测 pending”，不得填入预期数值或升级 claims。

执行中已固定的必要口径：

- TLS 使用 256 B 载荷、256 次循环、16 次预热、三次重复；每循环四次成功 SSL 读写。核查本次程序所属 `dropped_events` map，只有确认零丢弃的重复进入成功采集成本汇总。高速过载校准原始记录保留，不能以丢弃快路径成本代表成功采集。
- collector 的最终 Observer 审查和落盘等待上限冻结为 300 秒；撤销 harness 隐藏的 30 秒上限。`study-v1` 在 16 并发被旧上限截断，整批不并入最终结果。正式批次最后一次 32 并发 C3（`c32-r3-C3`）在 300 s 处被截断后，按固定恢复规则以 **600 s harness 关闭上限**重跑同一冻结条件；关闭上限是运行保护，不是处理变量，矩阵、负载、测量口径和该条件的定义均未改变。两次 attempt 及其费用均保留。
- 原生客户端对未知模型的美元映射不适用于 DeepSeek。客户端限制设为 0.10 美元以避免错误提前结束；真实费用按会话累计 `modelUsage` 和 Observer 每次实际 usage，采用 [DeepSeek 官方峰时单价](https://api-docs.deepseek.com/quick_start/pricing/) 的保守上界，并保留失败尝试。5 美元为整个实验的软预算保护，不是 provider 硬上限。被 SIGKILL 中断的一次未计量请求另留 0.31 美元预算准备金，不冒充实测 usage 或账单。
- 数据库 flush、上游丢弃、语义过滤分别判断。上游报告丢弃不等于 DB flush 失败，也不能据此拒算受控操作缺失率；写库错误和未完成 drain 仍单独报告。
- 图更新计时覆盖在线 graph builder 和增量 buffer 写入，不含 phase-2 批量关联、pattern/scoring、快照与 DB 等待；不能称为所有责任图相关工作的总耗时。完整 runtime 成本仍由系统资源与 C1/C2/C3 条件共同呈现。
- 本机曾发生 suspend，`/proc` BOOTTIME 与 BPF MONOTONIC 存在偏移。进程出生时间用独立时钟见证转换；延迟关联使用精确 raw BPF 操作时间、凭据的 wall/monotonic 见证和真实 evidence/claim/alert 引用，并保留持久化时间偏移。未修改产品时间归一化器。终点是 collector 最终裁决消息的 broadcast attempt（含 safe 裁决），不是浏览器收到时间。

### 6.6 实测结果（2026-10-28 完成）

> 供论文写作成员使用的完整中文报告、14 张独立图与 8 张表见 [开销实验统一报告](../../compare/results/overhead/README.md)。

**验收状态**：`compare/results/overhead/raw/full-system/formal/execution-report.json` 为 `complete=true`、`conditions_completed=36/36`；36 个条件均为 `completed`，工作完成率 100%。`overhead_report.py` 在严格完整模式下导出 `T8.md`、`T8.csv`、`raw-per-run.json|csv`、`F2.pdf|svg|png`，无 warning。正式 plan SHA-256 为 `1f8a02e5…`；T8.md SHA-256 为 `7bd95ce4…`，T8.csv 为 `bbc538d5…`，F2.png 为 `cce37005…`。论文数字登记在 `docs/paper/claims-overhead-2026-10-28.json`，`verify_claims.py` 16/16 通过。

**执行修正（保留可审计性）**：`c32-r3-C3` 第一次 attempt 在 workload 512/512 完成后，phase-2 批量关联运行 233 s、尚未完成 458 个候选审查时被 300 s harness 上限 SIGKILL。该 attempt 连同 `result.json`、DB、trace 和费用归档到 `study-v4/formal-attempts/c32-r3-C3-20261028T103154Z/`；随后按固定恢复规则使用 600 s 上限重跑同一冻结条件，恢复运行通过全部校验（512/512 受控操作、attachment、final flush、真实 Observer usage、linked alert），条件墙钟 40.2 s。矩阵、负载、测量口径未改；失败尝试费用 $0.0309 计入账本，不静默剔除。

**完整成本（T8-a/b/e；均值 [min–max] 或单值）**

| 指标 | C0 并发 1 | C1 并发 1 | C2 并发 1 | C3 并发 1 | C3 并发 32 |
|---|---:|---:|---:|---:|---:|
| 条件墙钟（s） | 2.32 [2.27–2.37] | 4.50 [3.92–5.43] | 3.90 [3.83–3.98] | 9.27 [9.04–9.50] | 150.91 [40.21–234.74] |
| Agent 端到端（s） | 2.27 [2.22–2.32] | 2.97 [2.42–3.82] | 2.22 [2.12–2.32] | 2.49 [2.22–2.72] | 2.88 [2.75–3.15] |
| collector CPU 秒 | — | 0.75 [0.73–0.76] | 0.80 [0.79–0.81] | 0.85 [0.83–0.89] | 93.76 [24.94–149.53] |
| collector 峰值 RSS（MiB） | — | 373.6 [372.5–374.7] | 376.1 [375.8–376.4] | 376.0 [375.8–376.1] | 772.7 [461.7–1036.5] |
| DB+WAL 峰值（MiB） | — | — | 2.98 [2.65–3.63] | 3.59 [3.50–3.75] | 169.45 [84.92–232.27] |
| trace 终态（MiB） | — | 1.01 [0.93–1.12] | 0.76 [0.75–0.78] | 0.78 [0.77–0.79] | 47.86 [39.43–52.42] |
| 责任图节点/边（终态） | — | — | 123.7/160.7 | 125.7/163.3 | 823.7/36,254.7 |
| 图更新次数/总耗时 | — | — | 327.7/4.14 ms | 331.0/4.28 ms | 82,572/3.51 s |
| Observer 尝试/输入+输出 token | 0/— | 0/— | 0/— | 2.0/12,706+1,176 | 8.3/144,922+20,261 |
| Observer 费用上界（USD/条件） | 0 | 0 | 0 | 0.005232 | 0.067886 |
| 总费用上界（USD/条件） | 0.000558 | 0.000639 | 0.000501 | 0.005782 | 0.085738 |

- C2−C1 是 runtime+DB 组合增量，不是 graph-only；图更新计时只覆盖在线 graph builder 与增量 buffer 写入，不含 phase-2 批量关联、pattern/scoring、快照和 DB 等待。高并发 C3 的条件墙钟由 phase-2 批量关联主导，故呈重尾（40.2/177.8/234.7 s），不能用单项图维护计时代替完整 runtime 成本。
- Agent 任务耗时的配对差值为描述性：并发 1 为 −0.03%…+17.32%，并发 32 为 −4.05%…+20.70%（n=3，不报显著性）。固定短任务的差异落在运行间噪声内，不能据此宣称加速或精确减速。

**捕获完整性与告警（T8-c）**

- 所有 C1–C3 条件的受控操作 raw missing 与 persisted loss 均为 0.0%；32 并发为 0/1536 raw、0/1536 persisted。该分母是成功完成的受控文件操作，不是全局事件数。
- 32 并发进程采集源报告 ring-buffer 丢弃 13,439–16,442 次，TLS 源为 0；并发 1 的各源均为 0。上游丢弃、语义过滤和写库失败分层报告，不合并成单一 loss 率；“受控操作无缺失”不等于全局零事件丢失。
- 可关联最终告警/受控操作：并发 1 为 1/16（6.25%），p50=p95=7.63 s；并发 32 为 23/1536（1.5% [0.4–2.3]），p50=144.10 s、p95=145.17 s。覆盖率低是因为多数受控文件操作不产生可告警证据，不是漏报率；未关联样本不记零延迟。

**探针补充（T8 Probe supplement / Diagnostics）**

- TLS 成功读写微基准（256 B、256 次循环、16 次预热、3 次重复、3 次均零丢弃）：每成功 SSL 读写操作 **873.4 ns [386.7–1133.5]**（相关程序合计时间/成功读写操作数）；逐程序均值 `probe_SSL_rw_enter` 186.7 ns、`probe_SSL_read_exit` 564.6 ns、`probe_SSL_write_exit` 808.7 ns。
- 其他已启用程序的 3 个 C1 短诊断窗口：`enter_syscall` 66.3–70.9 ns/call、`complete_syscall` 85.6–90.7 ns/call、`handle_exec` 3.74–4.15 μs/call、`handle_exit` 1.47–1.75 μs/call。窗口内计数包含系统级触发与快速过滤返回，不能称为每个有效事件的完整处理成本；一次性扫描和未触发程序标记未覆盖，不记 0 ns。

**费用与账本**：36 个正式条件的 Agent+Observer 峰时费用上界合计 **$0.6204**（含归档失败尝试 $0.0309）；本实验全部 cohort（校准、pilot、丢弃批次、恢复）合计 **$1.6465**（观测/已知 usage 上界 $1.0265 + 未计量请求准备金 $0.62）；确认预算 $5，未达上限。费用基于真实 usage 与 DeepSeek 官方峰时单价计算，是保守上界而非账单；缺失 usage 不记零。

**F2 图**：四个面板只画完整系统 C3，横轴为并发 1/4/8/16/32：(a) 受控操作 raw missing 与 persisted loss（均为 0，标注 0/分母），(b) 可关联最终告警 p95（s；空白表示无关联样本，不记零），(c) collector 峰值 RSS（MiB），(d) collector CPU 均值（% 单核）。散点为三次重复，菱形为均值，竖线为 min–max；不画 CI/p99。C0 的端到端耗时保留在 T8-a，不作为第二个图。

**边界**：单主机、单内核（`7.0.0-38-generic`）、单一固定 Agent 负载、每格 3 次描述性重复；不做 CI、p99、长期泄漏或跨环境推断。`study-v1/v2/v3`、pilot 与失败 attempt 均单独归档，不并入正式 36 条件。结果只支持“该冻结构建与负载下的完整成本与并发趋势”。

## 7. SensitivityBench（6）

> **2026-10-30 执行更新**：正式 T7-S 为 **110 点阈值网格 + 六维权重/gate OAT + 1000 次 LHS**，固定已记录 Observer；baseline 93/93 精确重放。结果在 `compare/results/sensitivity/README.md`。

- 数据：93 checkpoints；只做 **threshold×gate-center 网格 + bootstrap**；**不做六维 LOO/门控变体**（移到 Ablation，避免重复）。
- 15 点：threshold 0.10–0.30（0.05）× gate center 5/10/20；bootstrap 1000 scenario cluster；verdict/route flips。
- **独立性**：当前 family split 是回溯划分，不是独立开发集；主文只能写“回溯 family split/敏感性”，不得写“独立测试验证”。若 G0 后能冻结新 family 划分，才可作次要分析；任何 family 泄漏要披露。
- 固定已记录 Observer 观测：结论限定“条件于冻结观测”；加 10–20 run 的 live Observer 子样本（3 gate center）估计重跑差异/token；否则不写“gate 不影响标签”。
- 主表只放 flip rate + CI；15 点全网格进附录。

## 8. PBCBench（7）

- **不要用 PBC 当 activity-only**；PBC 是 detector-derived。activity baseline 用 **原始账本**：syscall/文件读/网络连接/tool call 数量、图节点/边、持续时间。
- 93 C0；PBC/action/intent counts；按 attack A/B、benign single/multi、difficult；**7 difficult 是 benign 子集，不要重复加 9/16**。
- 分析：PBC 分布（说明零膨胀/并列）、tie-aware AUROC/AUPRC、分层 TPR/FPR（PBC=0 /1–9 /≥10，exact CI）、高 PBC benign FPR 上界（0/8→32%、0/16→19%，方法写清）。
- Type C 口径：N_A attack multi =A+B；主表分 A/B；“Type C 等价集”只指 B 类（当前 11，盲标后可能变），不把全部 N_A 叫 Type C；23→N_A 映射按 G0 写。
- 不声称“PBC 匹配对照”；样本不足就写“不可估”；不引用旧 396.7/0.528 当新结果。

## 9. AblationBench（8）

- 数据：C0 **原始 facts + checkpoint**；必须能够绕过 checkpoint 的 authoritative identities/graph，重新构造每层。
- 层次与**冻结规则**（开跑前固定的伪代码）：
  - L0：raw OS activity only（原始 syscall/file/network 计数，固定阈值/score）
  - L0.5：+PBC rules
  - L1：+graph/taint
  - L2：+Observer（缓存观测；另做 20-run live 子样本）
  - L3：+full RuleCatalog/identity
- 每层同一阈值/权重；加 `hard_sensitive_egress=off` 条件，报 hard rule 激活率；否则 hard rule 短路会掩盖层贡献。
- **L0 评分口径固定（2026-10-28）**：L0 的 per-run `ranking_score` 固定为原始 OS action occurrence 计数（单调排序值，不是校准概率，也不读 PBC/图/身份/Observer）；只报 L0 的 AUROC/AUPRC。L0.5–L3 使用同一冻结 `system_score`。**2026-10-29 正式 T7 改为 two-endpoint**：ranking 用分数（L2/L3 verdict partial 已披露，不冒充 Observer 完整重放）；operating point 用 L1@0.15 vs production L3；详见 `compare/results/ablation/README.md`。
- 六维 LOO + gate 变体（无/固定/sigmoid）**只在本实验**；Sensitivity 不再重复。
- 指标：score-based AUROC/TPR/FPR/coverage；Δ vs L0；paired scenario bootstrap；Holm family=各层对比；22/93 功效低，估计+CI 为主。
- 结论措辞：“feature-set ablation under frozen evidence”，不写“eBPF/PBC 各贡献 a/b”；责任图/Observer 若不提升检测，定位为归因/校准模块，用 AttributionBench 证明价值。

---

## 10. 论文表/图（5 结果表 + 3 框架 + 2 图）

| 编号 | 内容 | 对应 |
|---|---|---|
| T1 | 能力矩阵（他组/文档） | 同级方法 |
| T2 | 分母/标签/23 Type C 映射/84→93 漏斗 | G0 |
| T3 | 判定流水线/四分数/Observer 四态 | 意见1 |
| **T4** | 检测 + AppBench：M/A2，AUROC/TPR/FPR/coverage | AppBench（M 只出现一次） |
| **T5** | IdentityBench：5 条件 × pairwise/B³/false-merge/split/unattributed | 2a |
| **T6** | AttributionBench：4 方法+2 消融 × RootSet/Entry/Chain/abstain | 2b+8 |
| **T7** | 三联：A PBC 分层；B L0–L3 Δ；C threshold×gate flip + 六维 LOO | PBC+Ablation+Sensitivity |
| **T8** | 开销分账 | Overhead |
| F1 | 架构图 | — |
| F2 | 并发 vs loss/latency/RSS/CPU | Overhead |

7 个实验 ≠ 7 张表；T4/T7 合并，M 行只出现一次；附录放全网格/十分位/拓扑分层/错误类型。

---

## 11. 统计与开跑前固定（统一）

- 1 run/scenario：CI 条件于该 run；**不做 run 方差推断**；如需，10 scenario×3 repeat 子样本（可选，不能算主表重复）。
- 检测主 endpoint：**AUROC/AUPRC**；TPR/FPR secondary；报 `all/decided/executed` + coverage。
- 身份：agreement/B³/pairwise；不写 accuracy；参考误差敏感性；scenario cluster。
- 归因：RootSet/@1 primary；@3/MRR 仅预测排序与明确相关根因参考具备后；entry 边序为主；N_A attack 估计+CI。
- 多重性：每个实验开跑前固定 primary（1）+ secondary family；Holm；跨实验不合并，摘要不挑显著。
- 开跑前固定 cohort/endpoints/exclusions/stopping/analysis；G0 后不改分母。
- 披露：AI 非人工；无独立开发集；1 run；30 multi/N_A attack 非随机；参考误差。
- **capture quality**：C0 的 healthy/降级 run 开跑前固定；primary=all attempts，sensitivity=healthy-only；降级不得静默剔除。
- **顺序/漂移**：记录 run order；报告前 1/3 vs 后 1/3；长 campaign 模型/API 漂移作为协变量。
- **多重性**：每个实验只 1 个 primary；secondary family 开跑前固定；跨实验不做统一 Holm，摘要不挑显著。
- **模型族**：记录 GT/reference/judge/Observer 的模型/版本；报告单一模型族限制；可选 20-run 第二模型 robustness，不能当主表。

---

## 12. 数据/代码前置（G0 + W）

- G0：93 全 scored 或明确冻结口径；9 个 excluded 盲重标、label/notes 对齐；identity-confusion 修 actor/hash/回退；其他 paper_ready=false 修 prompt/fixture/GT；30 multi；3 AI/裁决。
- 运行：launch manifest、runner-events、agent logs、v4 DB/checkpoint、execution evidence；C0 stability sample optional。
- 代码：IdentityView/pre-checkpoint resolver；AttributionResult/ranked_agents；score-based metrics；AppBench transcript; PBC raw activity; ablation layer configs; overhead instrumentation; trace exporter.
- 每个实验的 prediction/reference 分离；metrics 无 reference 返回 None；claims/artifact.

---

## 13. 执行顺序

G0 数据冻结 → E0 pilot → C0 93×1 → 新 trace → IdentityBench/AttributionBench 3-AI 参考 → AppBench → Sensitivity/PBC/Ablation → Overhead → 统计/表/claims。

**G 门**：G0 分母/参考/协议；G1 pilot；G2 C0 健康；G3 参考 agreement；G4 表/claims。

## 14. 红线

- 不写 accuracy（AI 参考）；不写 human κ/gold；不写 93 已计分（G0 前）；不写 23 Type C=23 attack；不写 Root@3（未实现排序）；不写 TPR 显著（4–10 Unsafe）；不写“PBC 匹配/已排除混杂”；不写“独立测试集”；不把 fix4/旧 trace 混主表；不把设计级 causal 当 runtime。


## 2026-10-29 意见 5 补测与复核

用户确认精简方案后，复用原 36 条件，新增且已完成免费的 C1 固定本地稳态测量（3×60 s，40 SSL 操作/s、10 文件操作/s、1 子进程/s）。冻结 ELF 与原批次相同；三次单核 CPU 0.667%/0.667%/0.650%，均值 0.661%；平均 PSS 111.17 MiB。就绪后计时、包含全部三个采集进程，不插入额外静默时段，主计时关闭 BPF 统计。每次 600 个受控文件操作、60 个 exec 全部可见，来源丢弃计数 0。固定非 HTTP 载荷只支持限定采集流量结论；不能替代完整真实 Agent/Observer 开销，也不证明所有 TLS 操作原始载荷完整留存。启动与旧原生 Agent 大 ELF 扫描分开。

图表收敛为一张分账表、一张三面板图，见 [复核后的报告](../../compare/results/overhead/README.md)。该入口也纠正旧解释：32 并发 23/96 成功凭据动作有 evidence；145.17 s 是运行内 p95 的均值，条件样本 pooled p95 228.16 s、仅 3 个独立最终 alert；300 s 原失败尝试保留，未证实恢复规则预先登记。原全批次完成不等于所有科学结论已闭环。
