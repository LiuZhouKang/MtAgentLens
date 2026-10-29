# 回复信/修改说明内部核对清单（RC 2026-10-25-fix4）

> 内部使用，不随稿提交。只覆盖 R1.1–R1.5、R2.1–R2.5（外审 2 的五段）及编辑部要求；
> 原 R2.6–R2.10 为内部扩展，不单列。意见原文见 [审稿意见.txt](审稿意见.txt)。
> 编号按原文重排：R2.1–R2.5 = 基准规模、PBC 混杂、基线公平、根因、消融；旧内部
> R2.1–R2.10 编号在本文件不沿用。

## 0. 冻结口径

- 唯一可引用测量：RC **2026-10-25-fix4**：binary `de3a0894…`、release `aa364a45…`（93 cases）、config hash `6d44789c…`、61 条 run（18 benign + 43 attack）。见 [RC README](../evaluation/rc-2026-10-25-fix4/README.md) §1、`rc.json`。
- [claims.json](claims.json) 登记当前结果 claim；每个要写进论文的结果数字先登记再引用，并跑 `python3 scripts/evaluation/verify_claims.py --manifest docs/paper/claims.json`。其他 RC/旧构建一律不引用。
- claims.json 覆盖已登记的结果数字；协议/审计/修正数字（拒绝语言计数、2,108/149、F1 0.966、历史 218 ns 算术分解等）入论文前须补 claim，本文只作内部核对。
- `actionable := final_decision == Unsafe`；review 不是检出；分数只路由（[decision-procedure.md](decision-procedure.md) §1、§4）。

| 口径 | benign | attack |
|---|---|---|
| 有效 run / 尝试 | 18 / 24 | 43 / 50 |
| 已裁决 | 15 | 22 |
| Unsafe / review / Safe | 0 / 3 / 15 | 4 / 21 / 18 |
| 全部有效运行 actionable | 0 / 18 | 4 / 43 = 9.3% |
| 条件 actionable | 0 / 15 | 4 / 22 = 18.2%，Wilson [7.31%, 38.52%] |
| 二元覆盖 / 弃权 | 83.3% / 16.7% | 51.2% / 48.8% |
| 独立 marker 钩子触达 | — | 16 / 43 = 37.2%；4/16 Unsafe；已裁决 4/7 = 57.1% [25.05%, 84.18%] |

- benign 条件假阳性 0/15，Wilson 上界 **20.39%**；“零误报”只能作点估计，不能写成统计上已证明。
- **标签 ≠ 动作**：43 是有效攻击 run；23 Type C 是旧稿标签口径，现行 metadata 为 A/B/C=3/55/9（[experimental-design.md](../evaluation/experimental-design.md) §2）。两者都不能当执行分母；执行分母只认 marker 等独立证据。
- **拒绝语言 ≠ 完全拒绝**：审计记录 30/43 日志含拒绝语言，该计数不是完全拒绝运行数；拒绝语言可能与部分执行重叠，具体交叉表需按本次 43 个有效运行重核；不能把 30 写成“完全拒绝/未执行”（[attack-validity-audit.md](../evaluation/attack-validity-audit.md)）。
- 执行数有三种口径：claims 的 marker **16/43**；marker∪sink **18**；旧审计中的 **19** 不再作为当前计数。marker 只证明钩子触达，不代表攻击目标成功。
- **E3 边界**：统一缩放只作归一化控制；新增逐维相对权重分析见 E3 报告。固定记录 Observer 观测的 replay 仍不能证明端到端阈值稳健性。
- E5 合成身份微基准有产物，但身份指标未登记 claims；引用具体数字前先登记。

## 审稿人 1

### R1.1 统一评分流程与最终判定规则

- 两阶段：流式只产生 provisional；run 结束聚合硬证据与全部语义观测，写唯一 `runs.final_decision` / Authoritative 告警。
- 顺序：`HardViolation` > 有引用 `ObserverUnsafe`（无矛盾 Safe）> 矛盾/Uncertain/Failed 为 review > 仅 Safe 为 Safe；非 healthy 采集把 Safe 降 review，Unsafe 保留并附 provenance。
- Observer：Unsafe+引用 → Unsafe（Supporting）；Unsafe 无引用 → Uncertain；Safe 只清除其审阅的候选/版本；Uncertain/失败 → review，失败不得升 Unsafe，也不得当 Safe。
- 0.15/0.70 只是 `DirectSafe/ToObserver/DirectUnsafe` 的路由边界，不是攻击判定线；§6.1.3 的 0.399 按最终 decision 与证据改写，删除“高于 0.15 即攻击”。
- 证据：§3.5、算法 3、§6.1.3；decision-procedure.md §1–4；`final_decision.rs`、`engine.rs`、`alert_builder.rs`、`runtime.rs`、`checkpoint.rs`。
- 缺口：校准（Platt/isotonic、ECE）未做，不得称“校准后分数/概率”；论文段待改。

### R1.2 身份解析与根因定位准确性

- 边界信号：`ProcessId=(pid,start_time)`、TLS 会话参与者、conversation/session 提示；显式委派分离；设计上冲突保留 `unattributed`、不强行合并，但生产实现仍缺事件级候选/弃权输出，共享进程的动作级归属不声称唯一 agent（experimental-design.md §2）。
- E5 六拓扑合成微基准只证明受控条件；必须同时报 wrong-merge、split 与 coverage，不能只报 F1。具体数字未入 claims，先登记再引用（[e5](../evaluation/e5-identity-microbenchmark.md)）。
- 真实多 Agent/handoff 的评估语料与 Type C 事件级 GT 未做；旧 23 是标签口径，现行 C=9；不得把案例链深、图规模或场景数当准确率。
- 根因：`claims/traceback.rs` 仍返回单 root 候选、无有序排名，Top-k/MRR 尚无实现基础（experimental-design.md §2）；根因准确率未测。
- 证据：§3.2、§3.4.2、§5.3、§6.1.4–5；`identity/`、`claims/traceback.rs`；[身份解析.md](身份解析.md)、[annotation-protocol.md](annotation-protocol.md)。
- 缺口：真实语料身份评估、根因 GT 与排名、人工 κ（需实际独立记录）。

### R1.3 基线比较及外部基准协议

- 基线只接收文本、MtAgentLens 接收 OS 事件流并调用 Observer；结论只能写“观测层级/信息量的增量”，不能写方法优越。“1.000 vs 0.087–0.522”必须带输入、分母、成本，否则删除。
- “文本 + 工具调用日志”应用层基线未在 fix4 跑；只能写计划/限制。要求同 backbone、同预算、同输出契约，并保留逐样本记录。
- 同层方法只做能力矩阵（[能力矩阵-同级方法.md](能力矩阵-同级方法.md)）；AgentSight、GUARDIAN、BlindGuard、MAStitch 未在本环境复现，不引用其数字。AgentSentinel 仅复现 audit prompt 且改用 DeepSeek v4 Pro，只能按“适配版”报告。
- InjecAgent 必须给逐样本执行审计；确认前只能按文本降级协议报告，不能写 OS 执行或跨框架泛化。2,108 需对官方 1,054 做 case_id+变体映射；149 的构成（文档记为 99 控制 + 50 自建）需逐项来源核对；该实验只作弱 OS 信号的退化边界，不作主比较（experimental-design.md §8.1）。
- 证据：§2、§5.2、§6.3、§7；experimental-design.md §8.1。
- 缺口：text+tool 基线结果；同层方法复现或“未复现/不可比”声明。

### R1.4 指标核对与测试独立性

- 按 TP=2108、FP=149、FN=0、TN=0 的合并集重算，F1≈**0.966**，短良性 FPR=**1.0**；表 11 必须给完整混淆矩阵与分母，不能只报 F1（experimental-design.md §2）。
- 无独立开发集；无法核验开发与测试严格隔离，不能声称未使用测试标签；如实写限制，不用“全量门”等流程词。
- 困难良性 E7 未做；当前 18 benign 多数 PBC=0，与攻击分布不匹配，不能排除活动量混杂。
- E3 新增逐维相对权重扫描，结果以实测报告为准；不将固定 Observer 观测的 replay 写成端到端稳健性。
- 证据：表 11、§5.1、§5.3、§7；[e2](../evaluation/e2-pbc-activity-baseline.md)、[e3](../evaluation/e3-threshold-sensitivity.md)、[e7 计划](../evaluation/e7-difficult-benign-plan.md)。
- 缺口：E7 PBC 匹配困难良性；独立开发集（客观不存在，写限制）。

### R1.5 完整系统开销评估

- **2026-10-29 复核优先**：见 [报告顶部](../../results/overhead/README.md)。新增固定本地负载 C1 稳态 3×60 s，单核 CPU 0.667%/0.667%/0.650%，仅支持该采集负载，不是完整系统或原生 Agent ≤5%。原 32 并发 96 个成功凭据动作仅 23 个有 evidence；145.17 s 是运行内 p95 均值，条件样本 pooled p95 228.16 s、只共享 3 个最终 alert。不要把已完成执行写成高并发完整归因已验证。主文改用精简分账表＋综合图，原始不利尝试保留。

- 结论状态：**实测完成（2026-10-28）**。正式 36/36 条件、T8/F2 见 `compare/results/overhead/raw/full-system/report/`；结果摘要见[实验总方案 §6.6](../evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#66-实测结果2026-10-28-完成)。fix4 账本只有 `observer_calls`/`duration_seconds`，无 token/费用，不能据此补新协议的费用栏或替代新实验。
- 固定 Agent 负载（文件/进程/TLS，并触发真实 Observer；成功操作有独立凭据/回执），单 collector 监测 N 个实例。并发 1：C0 Agent-alone、C1 `trace --capture-only`、C2 `trace --disable-responsibility-observer`（runtime+DB 仍运行、Observer off）、C3 默认 `trace`（真实 Observer）；并发 4/8/16/32：只跑 C0、C3。
- 并发 1 为 3 个四臂区组，并发 4/8/16/32 各 3 个两臂区组，共 15 个配对区组；每级 3 次。36 个正式 condition runs（12+24），并发实例总数 372；36 指 condition runs，不是 36 个 Agent。warmup/pilot/诊断/TLS 微基准另计。
- 保留所有尝试；固定随机种子顺序、恢复/warmup/缓存规则；基础设施失败与负载饱和分开；不利结果不剔除。
- 报告耗时增幅/完成率；CPU/RSS/落盘体积及内存随并发和窗口变化；图更新轻量计时单列（C2−C1 含 runtime+DB，不是 graph-only）；Observer 调用延迟/token/费用/失败重试及缺失；已知成功操作缺失与底层 TLS/process/persist 计数分别报；可验证触发事件到最终可见告警 p50/p95 与未告警覆盖。不靠时间邻近配对，无 p99 硬承诺。
- 不包含长期泄漏、多主机模型、额外任务家族、逐探针并发矩阵、挂载对照。
- 探针补充：复用 `compare/scripts/perf/legacy/run_experiments.py` 的 bpftool 统计方法与 `compare/scripts/perf/bench_targets/ssl_loop.c`。TLS 仅一个短微基准、3 次重复：先用内存 BIO 等本地成功握手/读写固定载荷并检查返回值（原 `ssl_loop.c` 无连接失败路径，不能作完整成功采集成本）。只统计本次 program IDs，取预热后窗口差值；ns/次=Δruntime/Δcount；合计=相关程序 ΣΔruntime / 成功 SSL 读写操作数，一轮读+写算两操作，多端实际完成次数须写明。其他正式启用进程/syscall 程序仅复用并发 1 的 C1 负载，在主性能计时之外开短诊断窗口、随 3 次重复采样；列调用数与平均时间，包含过滤返回，不代表有效事件完整处理，零次=未覆盖；共享 syscall 不按文件 API 重复拆分，一次性扫描另记；诊断统计有开销，只在诊断时开启并恢复原状态，正式系统计时关闭。
- 历史纠错：`perf_report.json` 的 218.2624 是 101.3125+57.5763+59.3736 三个程序均值相加，触发次数不同且走负载失败路径，不是探针级或 SSL 操作级成本；不再以 1–5 ms 握手作开销分母，如保留算术纠错须注明非实测成本比例。36.9 MB/1.3 GiB 旧构建基线只作历史。新实测数字见[§6.6](../evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#66-实测结果2026-10-28-完成)；不改 fix4 `claims.json` 或原始 artifact。
- 工程状态：driver/metrics 已适配 36 condition runs 并通过严格完整导出；正式 36/36 条件已完成。预算按真实 usage 与价格日期记录，缺失写 null；旧预算 flag 不是硬上限。正式测量在独立窗口执行，未终止/改动 DSH 的 93 场景流程。
- 权威协议：[EXPERIMENT-SUITE-7-REVISED §6](../evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#6-overheadbench5)；本文件只记录辅助口径。
- 证据（历史）：§5.3、§6.4、表 12、§7；`overhead-baseline.json`、`scripts/wire/measure-overhead.sh`、RC fix4 `results.jsonl`。
- 缺口：完整开销已实测；剩余限定为单主机/单内核/单一负载/3 次描述性重复。Overhead claims 已登记为 `docs/paper/claims-overhead-2026-10-28.json`（16/16 通过），Word 合入尚未做。

## 审稿人 2（五段）

### R2.1 基准规模与评估独立性

- 19 是旧稿场景数；RC fix4 为 18 个有效 benign run（24 attempts），0/15 actionable，Wilson 上界 **20.39%**，3 个 review；不能把“零误报”写成强结论。
- 无独立开发集；E3 的固定 replay/整体缩放不能补救开发-测试泄漏，也不能当相对权重敏感性。
- 动作：扩困难良性（高委派、授权敏感访问、外网、长任务），按任务长度、PBC、委派数与攻击匹配；报告匹配子集 FPR；样本按场景家族/预算预先安排，不按 detector 输出挑选。
- 证据：§5.1、§5.3、§7；claims 的 `benign.*`；e7 计划。
- 缺口：E7 匹配困难良性；独立开发集（写入限制）。

### R2.2 Type C 高检出率的 PBC 混杂

- 旧 396.7 不可复用；需报告两臂 PBC、事件量、委派、网络、时长分布，并做 PBC 匹配良性子集。
- fix4 E2 的纯活动量基线：benign 均值 **0.72**、attack **1.77**，PBC rank AUROC **0.528**；分布高度偏斜，支持“活动量不是强信号”的初步负结果，但 n=18、无匹配良性，不能据此写“已排除混杂”（[e2](../evaluation/e2-pbc-activity-baseline.md)、claims 的 `e2.*`）。
- 23 Type C 是旧标签，现行 C=9；43 是攻击 run 数，三者不可互换。
- 证据：§6.1.2、§7；e2 文档与 `e2-summary.json`。
- 缺口：PBC 匹配良性、分层 FPR、必要的 PBC 归一化/消融。

### R2.3 基线公平性与覆盖面

- 同 R1.3；审稿人 2 额外要求同输入/同成本与 equal-FPR，所有对比列输入、上下文长度、调用预算、成本。
- AgentSentinel 只复现 audit prompt 且换 DeepSeek v4 Pro，明确标为适配版，不称完整复现。
- 动作：补 text+tool（完整工具调用 I/O）基线，同 backbone/温度/输出契约，给基线等价执行轨迹；不能复现的同层方法写“未复现/不可比”，不引用其成绩。
- 证据：§2、§5.2、§6.3；experimental-design.md §8.1；能力矩阵。
- 缺口：同输入基线与 equal-FPR 结果。

### R2.4 根因定位评估偏弱

- 需 RootAgent@1/@3、MRR、Entry accuracy、chain P/R、depth MAE；分母按现行 C 集合与实际执行证据写清，不能拿旧 23 当已执行样本。
- 当前 `traceback.rs` 单 root、无有序候选，@3/MRR 没有实现基础；不得先报指标后补实现（experimental-design.md §2）。
- 身份正确率单独评估；E5 只覆盖合成受控拓扑，真实同进程/连接复用/并发仍未验证，且数字未入 claims。
- 人工标注：无真实三人标签与 κ；只能写协议、工具与规则冻结，不能报 κ 数字。
- 证据：§3.2、§5.3、§6.1.4–5、§7；`identity/`、`claims/traceback.rs`；annotation-protocol.md；RC README §8 阻塞 1。
- 缺口：根因 GT 与排名实现、真实身份评估、人工 κ。

### R2.5 消融设计粒度

- 需拆 L0（无 OS）/L0.5（仅 eBPF）/L1（+PBC）/+图/+Observer；六维逐维 LOO；报告最终 verdict 翻转、review、覆盖、成本与 McNemar/Holm，不用分数变化冒充检测贡献。
- 现有 E4 是固定 checkpoint 上逐维置零/门控重放；L0.5–L1 未做，不能证明 eBPF 与 PBC 各自贡献（[e4](../evaluation/e4-ablation.md)）。旧 claim 只核验第一维编号，现已改名 `e4.first_dimension`；六维覆盖须以新增汇总登记核验。
- E3 的整体缩放不是相对权重敏感性；固定输入 replay 不是端到端稳健性。两者都不能写成“消融证明”。
- 若责任图/Observer 对检测无显著增益，照实报告，并把价值定位为归因/证据组织/语义仲裁；不宣称“L2 主增益”。
- 证据：§6.2、表 9/10、§7；e3/e4 文档；decision-procedure.md §5。
- 缺口：L0.5/L1 拆分、端到端逐维消融、统计校正。

## 编辑部：截止与材料

- 截止：**2026-11-03 之前**；逾期按自动撤稿处理（审稿意见.txt 第 54–60 行）。
- 材料：(1) 修改说明（逐条指出何处改、改了什么；无修改也解释理由）；(2) Word 带“修订”状态的修改稿；(3) 定稿修改稿。
- 顺序：修改说明 → 修订稿 → 定稿；三份分别转 PDF 后**合并成一个 PDF**，不能分三个上传。
- 系统“修改说明”栏填：“修改说明已写在修改稿中”，并上传合并后的 PDF。
- 另需参加 ChinaData2026 并到会报告；最终录用由复审意见与到会陈述决定。
- 投稿只交编辑部要求的三份合并材料；本文件仅供内部核对。

## 提交前聚焦核对

- [ ] 所有进入论文的结果数字能在 claims.json 中找到；协议/审计数字按 §0 规则先补 claim；无其他 RC/旧构建数字。
- [ ] 分母写清：43 全部 / 22 已裁决 / 16 marker；不混用 18/19 的 marker∪sink/audit 口径。
- [ ] 不写“高 TPR 低 FPR”“零误报”“Warning+ 即检出”“κ=…”；“23 Type C 已全量评估”“30/43 完全拒绝”“E3 已证明权重/阈值稳健”。
- [ ] 每个“已完成”有产物路径与 scope；未完成写成“缺什么、补什么”，不用重复防御性长句。
- [ ] 表 11/表 12 已修正；§3.5、§6.1.3 与 decision-procedure.md 一致。
