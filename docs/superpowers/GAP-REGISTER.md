# MtAgentLens Gap Register（登记始于 2026-10-19）

> 本文件登记本次审计与审稿意见暴露的**全部**缺口。用途：
> 1) 修稿与工程收口共用一张表；2) 每条都有证据、行动、优先级、状态；
> 3) 不允许“看起来修了”而没有证据。
>
> 优先级：**P0** = 修稿/发布阻断；**P1** = 顶级质量必须；**P2** = 可接受债务/
> 文档化残余；**NONGOAL** = 刻意不做，写进论文限制。
> 状态：`DONE`（本次已修并有门）、`TODO`、`DOC`（文档化残余）、`N/A`。
> 本文件不提交给审稿人；论文只写最终保留的主张与证据。

## 0. 使用范围与当前复核

本表混合工程缺口、历史修稿事项与待补实验。优先级不代表这些工作都属于当前
产品工程阶段；处理前须复核代码和证据。当时的工程执行顺序见
[归档工程收口审查](../archive/ai-2026-10-30/superpowers/ENGINEERING-REVIEW-2026-10-22-CODEX.md#8-w2w5-接续实施与验收)。
以下登记项和 DONE/TODO 描述各自记录时的状态，不代表当前版本已经重新测量；
不再维护容易漂移的手工总数。当前实验状态与证据以
[CURRENT-EVIDENCE.md](../evaluation/CURRENT-EVIDENCE.md) 为准。

2026-10-22 接续：采集计数与审计准入、数据库 SQL 错误态、多 Agent 日志标识和
隔离检出机制已补实现及回归；验证范围和剩余边界见工程审查 §8。
正式效果、消融、开销、人工标注与跨内核兼容性仍需独立证据。

2026-10-24 复核：EFF-11 仍未完成（无真实人工标注/κ），但工具面已补齐：标识符
脱敏预览、三名标注者工作台、`pilot/1` 定稿 codebook（2026-10-24）、一致性脚本与 κ 门禁、
codebook 版本强制；这些都不等于真实标注。事件级对齐由
[ADR-0016](../adr/0016-no-event-level-alignment-without-shared-anchor.md) 明确为
不可用（缺少共享可核验锚点），禁止用时间相近或字符串相似替代。运行绑定、发布
源链、启动时钟见证与内核进程快照见实验设计 §21–§29；旧实验数字仍全部按作废处理。

---

## EFF — 检测与测量

| ID | 缺口 | 一手证据 | 行动 | 优先级 | 状态 |
|---|---|---|---|---|---|
| EFF-01 | 当前二进制攻击召回未证 | P1.2 小样本 Unsafe 0/6；旧 0.8955 是 2026-07 构建；历史 60/67 仅代表 1.1.0 语料 | 用冻结二进制 `2dfe82dd…` 重采 ≥50 attack；报 actionable TPR/FPR+CI | P0 | TODO（下一步；需用户确认全量预算） |
| EFF-02 | Observer 路径无干净证据 | 旧 86 DB 中 59/63 observer 路由失败 | observer on/off 消融；明确主口径 | P0 | TODO |
| EFF-03 | fail-closed 语义矛盾 | 引擎 0.15–0.70 返回 DirectUnsafe，告警落 Uncertain/Warning；仅 >0.70 actionable | 产品决策 + 校准（Platt/isotonic）+ 写清两口径 | P0 | TODO |
| EFF-04 | benign FPR 样本小/波动 | 20/20 observed，actionable 0/20；Warning+ 一次 0/20、一次 1/20 | benign 扩 ≥50；报 Wilson/BCa；披露单次波动 | P1 | **部分 DONE**：冻结二进制 `2dfe82dd…` 重测 20/20 observed，Warning+ 0/20、actionable 0/20（Wilson 0–16.11%）；输入 SHA 见 `fresh-2026-10-19/fpr/provenance.json`；扩到 ≥50 与 PBC 匹配困难良性待做 |
| EFF-05 | TCU 多 Agent 未测 | 旧语料 delegation_records=0；P1.2 TCU 0/12 | ≥10 真实 handoff；测授权门 precision/recall | P0 | TODO |
| EFF-06 | 图模式窄/按边类型 | 收紧后攻击 2/6；`min_matches` 数边类型不数次数 | occurrence-based 匹配或明确边界 | P1 | TODO |
| EFF-07 | Credential producer 未触发 | 0/12；场景未访问真实凭据路径 | ≥10 真正访问 `.ssh/.env/.aws` 的场景 | P1 | TODO |
| EFF-08 | 消融与叙事冲突 | `ablation_summary_v16.json` L2 ΔAUROC=0.0 | 重跑逐组件消融 + CI；否则删“主增益” | P0 | TODO |
| EFF-09 | 基线不公平 | LLM Guard 内容级 vs 系统级；输入/口径/成本不一致 | 同输入 + equal-FPR + 成本列 + 不可比声明 | P0 | TODO |
| EFF-10 | 无统计管线 | 无 McNemar/Holm/ECE 脚本 | `scripts/evaluation/stats.py` + 单测 | P0 | **DONE**：`scripts/evaluation/stats.py`（Wilson/BCa/McNemar exact/Holm/Cliff's delta/AP/ECE），`--selftest` 通过 |
| EFF-11 | 责任归属无 ground truth | 无人工标注；R2.2/R2.8 | codebook + 三名标注者独立标注 + 第四方裁决 + κ + 三层指标 | P0 | **部分 DONE（仅工具）**：脱敏工作台、`pilot/1` 定稿 codebook（含授权包络/`succeeded` 判据/空根因/裁决门槛/盲化范围五项裁决与单调链强制）、一致性/版本门禁、codebook 迁移工具已就绪；真实三人标注未执行，无 κ，`paper_metrics_available=false` |
| EFF-12 | 无在线延迟 | 旧实验全离线 | 分段戳 + live 会话 P50/P95/P99 | P0 | **部分 DONE**：3 次 live（修复后 5aaff2d7）route_event p50 220µs / p95 397µs / p99 620µs；trigger p50 595ms / p95 1.42s；`event_to_alert` 0 样本（良性无告警）；证据 `fresh-2026-10-19/live-latency-fixed.json`；需 attack/handoff 场景补 event-to-alert |
| EFF-13 | 开销/lite 缺失 | 旧 6.24% CPU/1,424.8 MiB；ONNX 93% | lite 模式 + 空载复测 + 预算 | P0 | TODO |
| EFF-14 | 场景构念效度未审计 | credential-theft 只读 fixture；攻击可能未执行 | 逐场景 action trace 审计；未执行单列 | P0 | **部分 DONE**：`compare/scripts/audit/audit_action_traces.py`；历史语料 86 observed → 68 checkable → **29 strict executed**（宽松 52）、**25 not_executed**、32 not_applicable；严格口径只认 per-event 敏感路径/复合模式，排除 UserPrompt、目录名与裸命令（env/cat/ls/curl/journalctl）；证据 `docs/evaluation/action-trace-audit-2026-10-19.json`；待冻结二进制重采并先修场景执行率 |
| EFF-15 | 语料不可分发/哈希不符 | traces gitignored；prompt 哈希 0/86 与磁盘一致 | 校验和清单 + 生成脚本/分发包 | P1 | TODO |
| EFF-16 | 旧数字跨构建混用 | README/论文中 0.8955/0.8947/0.9925 | 只标历史；最终表用冻结二进制 | P0 | 部分 DONE（文档已标注，论文待改） |
| EFF-17 | **PBC 数量混杂**（R2-b）：检测可能只在区分“活动多/少” | Type C 平均 PBC 396.7 远高于 A/B；D4/D6 除以 50、N_PBC=10 都与行为量相关；良性 PBC 分布未报告 | 攻击/良性 PBC 分布对比 + PBC 规模匹配的良性多智能体对照 + 匹配子集 FPR + 归一化/消融 | P0 | **DONE（结论已反转）**：历史 78/220/419 vs 3/4/4 是**重复边**造成的（trigger 每轮重复计数）。修复 `upsert_pbc_edge` 后，同一 RC 的 distinct pair：攻击 c2 0–3、credential 0–1；困难良性 0–3；26 场景 FPR 中 4 个良性有 3–6 个 pair，仍 **0/26 actionable**。PBC 数量不再区分攻击/良性，检测来自 rule/Observer 证据。旧 396.7/220 不得引用；需在最终语料重算分布。 |
| EFF-18 | **缺同层/同信息量基线**（R1.3/R2-c） | 三个基线只读提示词；无 AgentSight/GUARDIAN/BlindGuard/MAStitch 对比；“1.000 vs 0.087~0.522”易误读 | 增加“文本+工具调用日志”同轨迹基线；同层方法做定位对比或明确“未复现/不可比”；对比表加输入/信息量/成本列 | P0 | TODO |
| EFF-19 | **根因定位与身份解析未定量**（R1.2/R2-d） | 只有 3 个 Type C 案例定性；无 23 个 Type C 的根因 Agent/入口准确率与 Top-k；并查集正确率未单独评估 | 23 个 Type C 全量定量（root-cause agent / entry point / Top-k=1,3,5）+ 身份合并/拆分 precision/recall | P0 | TODO |
| EFF-20 | **开发/测试隔离与困难良性缺失**（R1.4/R2-a） | 良性仅 19（CI 上界 0.176）；阈值/权重来源未说明；无与攻击同任务长度、同 PBC 数量的困难良性集 | 说明开发集规模与冻结测试隔离；构造 PBC/委派/敏感访问匹配的困难良性 ≥10；阈值敏感性分析 | P0 | TODO |

## PROD — 生产者与语义

| ID | 缺口 | 一手证据 | 行动 | 优先级 | 状态 |
|---|---|---|---|---|---|
| PROD-01 | TCU 仅靠 taint 边 | 旧 15/19 良性 | 四条件门（ADR-0015） | P0 | **DONE**（待新语料实测） |
| PROD-02 | Warning+ 被当 actionable | 三处相反定义 | actionable=Unsafe；warning_plus 独立 | P0 | **DONE** |
| PROD-03 | claim severity 与 alert 不一致 | Info 告警标题曾写 Critical | 标题用组合 severity | P1 | **DONE** |
| PROD-04 | Safe 告警标 UnsafeByEngine | 20/20 Safe/Info | Safe→SafeBaseline | P1 | **DONE** |
| PROD-05 | 同 Agent 读取造 PBC | 旧 TaintedContextUse FP | step 5 用 check_access_from | P1 | **DONE** |
| PROD-06 | 非文件工件成污点源 | `/` 在 15/19 良性 | is_concrete_file_artifact | P1 | **DONE** |
| PROD-07 | replay 委派 ID 不匹配 | provisional UUID vs agent-<sha> | 映射 + delegations_mapped | P1 | **DONE** |
| PROD-08 | matcher verdict 不持久化 | engine_section pattern_verdicts 恒 0 | ClassificationResult 持久化 | P0 | **DONE**（待重采） |
| PROD-09 | reconnaissance 过宽 | 良性 5/6 命中 | 要求 PBC + min_matches=2 | P0 | **DONE**（待重采） |
| PROD-10 | Credential 计数误匹配 | fixture 路径含 “credential” | 匹配 JSON 键 `"Credential"` | P1 | **DONE** |
| PROD-11 | taint 无过期 | 4096 FIFO，仅测试清理 | 文档化；长 run 实测 | P2 | DOC |
| PROD-12 | path_in_intent_args 子串包含 | 可能绑定 `.bak`/目录前缀 | 收紧为路径段/相等；补测试 | P1 | TODO |

## PERF — 在线与开销

| ID | 缺口 | 一手证据 | 行动 | 优先级 | 状态 |
|---|---|---|---|---|---|
| PERF-01 | 无 pipeline 分段延迟 | 无时间戳 | eBPF→intent→evidence→graph→claim→alert 分段戳 | P0 | **部分 DONE**：runtime 新增 `LatencySamples`（route_event/trigger/event_to_alert，µs，P50/P95/P99，20k 上限）并写入 `[diagnostics:json].latency`；3 次 live 数字见 EFF-12；eBPF/intent/evidence 细分与 SSE 实时 metrics 仍缺 |
| PERF-02 | ONNX 占 RSS 93% | 旧 1,424.8 MiB | lite 模式（observer/ONNX off）+ 预算 | P0 | **已作废**：ONNX Layer 3 已从产品移除（2026-10-27），`--lite` / `--disable-classifier` 均被 CLI 显式拒绝；当前对照组用 `--disable-responsibility-observer`。旧 1,424.8 MiB 是历史数字，不得作为当前开销引用 |
| PERF-03 | 无吞吐/背压指标 | 只有丢弃计数 | events/s、runs/min、队列水位 | P1 | TODO |
| PERF-04 | 无空载主机开销 | 本机被会话污染 | live 小样本 + 空载主机 | P0 | TODO |
| PERF-05 | DB 写放大/丢弃 | 长 run 曾 49,963 丢弃（已修）；delegation `let _` | 计数暴露 + 压测 | P1 | **部分 DONE**：provisional identity 每事件写的根因已修（见 DATA-01）；delegation 失败计数待核；修复后 live 20s 0 dropped |
| PERF-06 | **完整系统开销未分项**（R1.5） | 历史218 ns为三个程序均值之和，非单次探针/SSL操作成本；旧RSS与ONNX不代表当前完整成本 | 已按[实验总方案§6](../evaluation/EXPERIMENT-SUITE-7-REVISED-2026-10-26.md#6-overheadbench5)完成：36/36条件、TLS成功路径微基准、3个C1短诊断窗口；T8分账＋F2并发图；driver/metrics已支持36条件与严格完整导出 | P0 | **DONE（2026-10-28，有限定）**：T8/F2在`compare/results/overhead/raw/full-system/report/`；单主机/单内核/单一负载/3次描述性重复，不报CI/p99、不做跨环境或长期泄漏推断 |

## ARCH — 架构与代码

| ID | 缺口 | 一手证据 | 行动 | 优先级 | 状态 |
|---|---|---|---|---|---|
| ARCH-01 | legacy server 零调用 + 端点 404 | `responsibility/server/**`；`/graph` `/evidence` 404 | 实现或删除；前端改动先确认 | P0 | **DONE**：删除 14 文件/2,525 行零调用树；生产端实现 `/graph`（最新 graph_snapshots）与 `/evidence`（evidence/intents/actions/non_links），测试 `graph_api_returns_latest_snapshot_nodes_and_edges`、`evidence_api_returns_evidence_intents_actions_and_non_links` |
| ARCH-02 | Runtime 零单元测试 | `ResponsibilityRuntime` 0 test | 覆盖 alert 门/refinement/诊断 | P0 | **部分 DONE**：新增 `alert_gate_matrix`（Unsafe 无 claim 可告警 / 无 Agent 的 claim 不告警）、`tls_events_increment_the_tls_diagnostic_counter`、`empty_stream_writes_run_start_and_run_end`；refinement 幂等仍缺 |
| ARCH-03 | clippy/allow 债 | 当前门禁 99/49（2026-10-22）；167 为历史值 | 只降不升；逐条清 | P2 | DOC |
| ARCH-04 | 大模块/死代码 | graph_builder ~2000 行；dead_code 警告 | 拆分/删除；不搞大爆炸 | P2 | TODO |
| ARCH-05 | 前后端类型漂移 | TS 无 `actionable`；claim state 3 vs 5 | 加类型/契约测试；前端确认 | P1 | **部分 DONE**：`ResponsibilityAlert.actionable`、`ClaimState` 6 变体、`RunSummary.state/started_at_ns/artifact_availability`、`behavioral_score`、`cusum_dimensions`；type-check 绿 |
| ARCH-06 | `/api/runs/:id/alerts` 忽略 run_id | `get_alerts_all()` 返回全库 | 按 run_id 过滤 | P0 | **DONE**：`serve_alerts_from_db` 先解析 DB `runs` 表（301/301 单 run），多 run 且无法匹配时 409 `ambiguous_run`（不混合）；`get_alerts_desc` 保持 DESC 契约；5 个回归测试含真实「目录名→DB UUID」路径 |
| ARCH-07 | `?lean=1` 被忽略 | 路由只看 path | 实现或删除参数 | P2 | TODO |
| ARCH-08 | overview 告警排序不一致 | DB import ASC vs severity sort | 统一排序 | P1 | **部分 DONE**：`/api/runs` 改为最新优先（测试 `runs_index_lists_newest_first`）；DB import 内部排序待核 |
| ARCH-09 | monitoring 健康忽略 Info | openCount 含 Uncertain | 统一语义 | P1 | TODO |
| ARCH-10 | severityFilter 死配置 | 无消费者 | 删除或接线 | P2 | TODO |
| ARCH-11 | replay 诊断未暴露 | ReplayResult 有；API 无 | 暴露到 REST/报告 | P2 | TODO |
| ARCH-12 | `build_claims` 误用风险 | 已 cfg(test) | — | P1 | **DONE** |
| ARCH-13 | 图模板语义浅 | 仅类型集合 + 连通性 | 与 EFF-06 合并 | P1 | TODO |

## INT — 集成/API/前端

| ID | 缺口 | 一手证据 | 行动 | 优先级 | 状态 |
|---|---|---|---|---|---|
| INT-01 | 前端无 `actionable` | `frontend/lib/types.ts` | 加字段/徽章/筛选；需确认 | P1 | **DONE**：类型加 `actionable?`；`openCount` 改用 `actionable ?? decision==='Unsafe'`（Uncertain 不再计入待处置） |
| INT-02 | `/graph` `/evidence` 404 回退 | api helpers 404 → DB | 实现端点或只走 DB | P0 | **DONE**：生产 server 两端口已实现；`live_run_smoke` 实测 `/graph`、`/evidence` 200 |
| INT-09 | `/api/runs/active` 把归档 run 当活跃 | 只按字典序选 summary，不看 state | 仅 `state=running` 或本进程 live 产物 | P0 | **DONE**：新增 `live` run（`<log>.responsibility.db`）；归档仅接受 running；测试 `completed_archive_is_not_reported_as_active`、`live_run_is_active_and_serves_its_alerts`；真实冒烟通过 |
| INT-10 | live 产物路径与解析器不一致 | DB 在 `<log>.responsibility.db`，API 只找 `collector/…` | 统一 live 路径 | P0 | **DONE**：`live_db_path`/`live_events_path` + `serve_live_resource`；`scripts/e2e/live-server-smoke.sh` 实测 alerts/snapshots/evidence/graph/db/SSE 全通 |
| INT-11 | appState 竞态：initData 把 live 打回 replay | `loadRun` 无条件置 replay；initData 选最旧归档 | live 优先 + 同 run 保持 live | P1 | **DONE**：initData 先取 active；`loadRun` 对当前 live run 保留 live；测试两条（store-live） |
| INT-12 | RunSummary/ClaimState 形状漂移 | 前端声明 started_at_ns/state 但后端不返；ClaimState 3 vs 6 | 后端补字段 + 前端补类型 | P1 | **DONE**：后端 summary 返回 `state`/`started_at_ns`（DB runs）；前端类型与标签补全 |
| INT-13 | SSE id 跨连接非单调 | 每连接从 1 开始，前端只当 key | 文档化或全局序列 | P2 | DOC |
| INT-03 | DB import 默认值掩盖未知枚举 | decision=Uncertain/severity=Warning | 显式未知态/计数 | P1 | TODO |
| INT-04 | overview 用 raw slice(0,5) | ASC 导入显示最旧 | 用排序后的 alertModels | P1 | TODO |
| INT-05 | OpenClaw 仅 fixture E2E | `scripts/e2e/openclaw-outofband.sh` | 真实 gateway 触发 | P0 | TODO |
| INT-06 | asserted agent_key 未入图 | asserted → OutOfBandAsserted，但非 AgentId | 映射到图 AgentId | P1 | TODO |
| INT-07 | 浏览器视觉回归缺失 | 无 | P2 | P2 | TODO |
| INT-08 | 非沙箱文件 E2E 缺失 | harness 看不到文件 syscall | 普通主机 REQUIRE_FILES=1 | P2 | TODO |

## ID — 身份/带外

| ID | 缺口 | 一手证据 | 行动 | 优先级 | 状态 |
|---|---|---|---|---|---|
| ID-01 | 单进程多逻辑 Agent 无法唯一区分 | 产品承诺 candidate set | 论文限制；不硬解 | NONGOAL | DOC |
| ID-02 | comm=MainThread 致 `-c` 失效 | OpenClaw Node | cgroup/exe 边界；文档 | P1 | 部分 DONE |
| ID-03 | 委派父身份未解析 | 新增 `delegation_parent_unresolved` | 计数已加；重试/回填可选 | P1 | 部分 DONE |
| ID-04 | phase2 图 ID 与 phase1 委派 | replay 已映射；live 待核 | 核 live phase2 claim 的 ID 一致性 | P1 | TODO |
| ID-05 | 带外只读边界 | 不改 agent/不插桩 | 文档化 | NONGOAL | DOC |

## DATA — 数据/持久化

| ID | 缺口 | 一手证据 | 行动 | 优先级 | 状态 |
|---|---|---|---|---|---|
| DATA-01 | 背压下丢弃无逐条记录 | lost/failed/closed 计数 | 计数已暴露；压测 + 文档 | P1 | **DONE（根因修复）**：实测 live 2 分钟丢弃 404k–1.9M，首批 20 条均为 `provisional_identity`；根因是每个事件都重写 provisional identity。已移到去抖 `trigger` 边界（每 2s/50 事件），20s 复测 **0 dropped / flushed cleanly**；旧日志另有 5,531 条 FOREIGN KEY 失败，随丢弃修复待复测 |
| DATA-02 | delegation 持久化忽略失败 | `let _ = write` | 计数/重试 | P1 | TODO |
| DATA-03 | DB root 0600/单写 | 设计 | 文档；评测用 sudo 副本 | P2 | DOC |
| DATA-04 | 单 schema | ADR-0005 | 保持 | — | DONE |

## REPRO — 复现/发布/安全

| ID | 缺口 | 一手证据 | 行动 | 优先级 | 状态 |
|---|---|---|---|---|---|
| REPRO-01 | 无 Docker/`make repro` | 只有脚本 | artifact：一键构建+跑小样本+出表 | P0 | TODO |
| REPRO-02 | 二进制/语料 SHA 未全链 | FPR/p12 有；论文表无 | 每个表记录输入 SHA | P0 | 部分 DONE |
| REPRO-03 | 场景哈希不符 | manifest prompt 哈希 0/86（根因：改名后未重算，反向替换可复现 86/86） | 重算 scenario JSON 哈希并记录 | P0 | **DONE**：`compare/scripts/build/refresh_prompt_input_hashes.py --write/--check`；86/86 匹配；新增 scenario raw+canonical SHA、legacy 字段；篡改测试退出 1 |
| REPRO-09 | fixture 清单漂移 | 769 条中 521 一致 / 204 不一致 / 44 文件缺失（18 场景） | 重算 fixture 清单并绑定字节哈希 | P1 | TODO |
| REPRO-10 | 官方校验入口不可运行 | audit/report import 缺失的 `env/scripts/evaluate_frozen.py`；硬编码 67 vs 86 | 修复入口或改为 manifest 驱动 | P0 | **DONE**：按 `compare.tools` 契约重写 `env/scripts/evaluate_frozen.py`（actionable 口径）；`FrozenDatasetAdapter` 增加 traces 分类目录发现与 GT 过滤；三个入口 `--help` 正常；历史语料复现 60/67 recall、0/19 FP（`docs/evaluation/historical-evaluation-2026-10-19.json`） |
| REPRO-04 | 旧构建不可考 | 2026-07 DB 无 SHA | 只作历史；不写“交付二进制” | P1 | DOC |
| REPRO-05 | `.git` 5.7 GB/旧 key | SECURITY.md | 轮换 key；必要时 filter-repo（先确认） | P1 | TODO |
| REPRO-06 | 无 CI | ADR-0014 | 本地门禁；发布前跑全门 | NONGOAL | DOC |
| REPRO-07 | 3 ignored tests | 需真机/真实 OpenClaw | 保留理由；逐步消除 | P2 | DOC |
| REPRO-08 | 隔离源码重建 | 旧脚本会复制子模块构建产物；缺固定提交模式 | 已增加 `--commit REF`、独立子模块检出和临时依赖环境 | P1 | 部分 DONE：10 项契约通过；整仓候选重建结果与锁定边界见工程审查 §8 |

## DOC — 文档/科研

| ID | 缺口 | 一手证据 | 行动 | 优先级 | 状态 |
|---|---|---|---|---|---|
| DOC-01 | 决赛冻结文档写 FPR=0 无 caveat | `docs/决赛文档/**` | 加勘误/说明；不改冻结正文 | P1 | TODO |
| DOC-02 | experiment-design v5 与修稿脱节 | `docs/design/experiment-design.md` | 指向新 experimental-design.md | P1 | 部分 DONE |
| DOC-03 | 历史数字的适用范围 | 0.8955/0.8947/0.9925 不能替代新构建实测 | 根 README 受用户保护，不主动重写；新结果在评估材料中绑定版本、输入与证据 | P0 | 部分 DONE；新版本效果未证 |
| DOC-04 | capabilities 限制清单 | 已加 P1.1/P1.2 | 持续同步 | P1 | 部分 DONE |
| DOC-05 | 论文 claim 与证据 | matrix C1–C15 | 逐条删/降级 | P0 | TODO |
| DOC-06 | **表 11/表 12 数值自洽性**（R1.4/R1.5） | 表 11 Meta Prompt Guard TPR/FPR_short/F1 同时 1.000；表 12 218 ns vs 1–5 ms 占比与 1 ms 端 0.0218% 口径不明 | 重算两表；F1 由 P/R 推导并写分母；表 12 注明分母/分位数/同机条件 | P1 | TODO |

## NONGOAL — 刻意不做（写进论文限制）

| ID | 项 | 理由 |
|---|---|---|
| NONGOAL-01 | HTTP/2、HTTP/3/QUIC、AF_UNIX | 结构不可达/成本；已有记录 |
| NONGOAL-02 | 原始 capture 通用 body 脱敏 | 破坏字节保真；出口脱敏已覆盖 |
| NONGOAL-03 | P3-b 外部内容种子 | 前置条件未满足 |
| NONGOAL-04 | CI/CD | ADR-0014（实例限制） |
| NONGOAL-05 | 单进程内多逻辑 Agent 唯一区分 | 观测边界；承诺 candidate set |
| NONGOAL-06 | 零缺陷/无逃逸 | 不可能；写清看不到什么 |

---

## 与审稿意见的映射

- R1.1/R2.7 → EFF-12/PERF-01；R1.2 → EFF-13/PERF-02/04；
- R1.3/R2.8 → EFF-11/EFF-05；R1.4/R2.3/R2.6 → EFF-01/04/09/10/EFF-16；
- R1.5 → EFF-08/PERF-03；R2.1 → DOC-05/相关工作；
- R2.2 → EFF-11；R2.4 → PROD-01/02/EFF-05；R2.5 → EFF-09；
- R2.9 → EFF-08；R2.10 → REPRO-01/02/03；
- 工程收口：ARCH/INT/ID/DATA/REPRO 全表。

## 使用方式

1. 新会话先按 P0 排序做；P1 与修稿并行；P2/NONGOAL 写进限制。
2. 每条完成后：补证据（测试/产物/文档），把状态改 DONE；**不允许只改状态不加证据**。
3. 修稿/发布前逐条过一遍；未完成的 P0 不得声称“完成”。

---

## 2026-10-20 顶级就绪审计新增（12 条，未并入上方分类表）

> 来源：`docs/superpowers/TOP-TIER-AUDIT-2026-10-20.md`（19 个只读 agent）。
> 合计口径变为 **89 + 12 = 101 条**；本节不改变上方表的历史状态。

| ID | 缺口 | 证据 | 优先级 | 状态 |
|---|---|---|---|---|
| EFF-21 | PBC 边按 trigger 轮次重复累加 | c2 23 条→4 个 pair；data-exfil 34 条→1 个 pair | P0 | **DONE**：`upsert_pbc_edge` 按 (source,target) 去重，evidence refs 合并、confidence 取 max；测试 `pbc_upsert_is_idempotent_per_source_target_pair`。注意：去重后 c2 仅 4 个 PBC、data-exfil 1 个，gate≈0，原两个 Uncertain 很可能是重复计数假检出 |
| EFF-22 | 最小证据门使单事件攻击永远不可行动 | `scoring.rs` PBC<10 → ≤0.3 < 0.70 | P0 | **DONE**：`evidence_gate(pbc, has_unsafe_rules)`；unsafe rule 命中不再被 PBC 压制；pattern-only 仍连续门控；测试两枚 |
| EFF-23 | fail-closed 永不映射为 Unsafe | `alert_builder.rs`；c2 0.617→Uncertain | P0 | **DONE（语义锁定）**：ADR-0015 修订——Observer 确认 Unsafe + evidence 才 actionable；Observer 不可用/失败 = Uncertain review，不再声称 fail-closed 保留召回；代码路径已符合（observer branch） |
| EFF-24 | 多 Agent 委派链被并查集合并，AgentDelegatesTo=0 | `delegation-chain.db` 4 条委派→0 图边 | P0 | **DONE**：`record_delegation` 创建去重边并持久化；`run_authoritative_resolution_with_delegations` 让委派子进程保持独立 component；测试 `process_delegation_creates_one_agent_edge`、`delegated_child_without_tls_stays_a_separate_agent`；真实 delegation run snapshot 9 条 AgentDelegatesTo、11 agents |
| EFF-25 | 凭据盲区：HOME `.ssh/.env`、workspace 豁免 | `policy.rs:457-510`；12 样本 Credential=0 | P1 | **DONE**：移除 HOME `.ssh/.env` 豁免；测试 `test_home_ssh_and_env_are_sensitive`；区分走证据/严重度而非路径豁免 |
| EFF-26 | Action→intent 关联仅 11–42%；Content 不匹配 action | `graph_builder.rs`；p12 evidence | P1 | TODO |
| DATA-05 | GT provenance 与“独立人工标注 κ=0.87”冲突 | GT 生成器为规则程序化；metadata 自述按 collector 调整 37 条 | P0 | TODO（需用户澄清） |
| REPRO-11 | 前端假数据：追踪页 demo alerts、仪表盘 44.0K/s | `traces-page-client.tsx`、dashboard | P0 | **DONE**：移除 demoAlerts 回退；事件率改用 live.stats.eventCount（无数据显示 —）；错误态可见；引擎告警标题中文化；前端 lint/type-check/vitest 已入门禁 |
| REPRO-12 | `frontend/out` 构建早于最后前端改动 | out mtime vs source mtime | P0 | TODO（重 build） |
| SEC-20 | 静态资源路径穿越（任意文件读取） | `GET /../../etc/passwd` 实测 200 | P0 | **DONE**（assets.rs 规范化 + 测试 + live 404） |
| SEC-21 | TLS 明文 `data` 字段凭据泄漏 | 脱敏只改 `data_b64` | P0 | **DONE**（两字段都脱敏 + 测试） |
| OPS-01 | verify 不覆盖前端；无冻结 RC；历史 key 未轮换 | `verify.sh`；git history | P0/P1 | **部分 DONE**：annotator + stats + 前端 lint/type-check/vitest 已接入 gate 3；RC 已冻结 `e3881d87…`；历史 key 轮换待办 |

## 2026-10-20 追加修复（本轮）

- ONNX Layer 3 首次管道错误后不再静默：`degraded_reason()` + `[diagnostics:json].classifier_degraded`。
- `/ready` 无 health producer 时返回 200 + `pipeline_metrics:"unavailable"`；`/health` baseline 标 Degraded 并给 warning，不再伪造 Healthy/idle。
- 日志文件与 SQLite `-wal/-shm` 强制 0600；SIGTERM 与 SIGINT 同等优雅关闭；Host 头默认只允许回环（防 DNS rebinding）；HTML `Cache-Control: no-cache`。
- summary.json 的 artifact 路径拒绝 `/etc /proc /sys /root /boot /dev /var/log`。
- 静态资源路径穿越、TLS 明文凭据、PBC 重复计数已修（见上）。

### Observer 失效修复（2026-10-20）

| ID | 缺口 | 证据 | 优先级 | 状态 |
|---|---|---|---|---|
| OBS-01 | Observer reasoning 引用注入串被误判为被劫持 | `Observer LLM call failed: InjectionDetected` on c2/prompt | P0 | **DONE**：不再因 reasoning 出现 `IGNORE PREVIOUS/SYSTEM:` 丢弃裁决 |
| OBS-02 | `attacked_flag=true` 被映射为 InjectionDetected | provider.rs 235-236 | P0 | **DONE**：attacked_flag 仅作 advisory；结构化 decision/evidence 才是裁决 |
| OBS-03 | Observer 失败回退 heuristic Safe | `engine.rs` Err 分支 | P0 | **DONE**：失败/不可用一律 Uncertain（review），不再把失败当 Safe |
| OBS-04 | `UserPrompt` 场景名触发 forced observer | p12 12/12 强制调用 | P1 | **DONE**：`intent_is_suspicious` 排除 UserPrompt |

### 2026-10-20 追加（规则与 glob）

| ID | 缺口 | 证据 | 状态 |
|---|---|---|---|
| EFF-27 | glob 规则只支持首尾 `**` | `**.ssh/id_*`、`**/target/**` 从不匹配 | **DONE**：真实 glob + 测试 `glob_matches_internal_wildcards` |
| EFF-28 | 宽泛 Unsafe 规则造成良性假阳 | benign-passwd-audit `/etc/passwd` → Unsafe | **DONE**：Unsafe 只保留 shadow/sudoers/私钥/外泄端点；`/etc/passwd` 走 review；测试 `passwd_read_is_review_not_unsafe` |

### 2026-10-20 追加（图模板）

| ID | 缺口 | 证据 | 状态 |
|---|---|---|---|
| EFF-29 | 图模板只数边类型，语义过宽 | known_hosts 良性被 privilege_escalation+recursive 判 Unsafe | **DONE**：删除两个无约束模板；保留要求 File+Network/Credential+Delegation 的模板 |

### 2026-10-20 追加（pattern gating）

| ID | 缺口 | 证据 | 状态 |
|---|---|---|---|
| EFF-30 | 纯图模板可绕过 Observer 直接 Unsafe | 合法凭据轮换被 3 模板判 Unsafe | **DONE**：`pattern_requires_observer`；Observer 不可用时降灰区；单测覆盖 |

### 2026-10-20 机制贡献审计新增（EFF-31…39）

| ID | 缺口 | 证据 | 优先级 | 状态 |
|---|---|---|---|---|
| EFF-31 | 六维评分/OWA/Platt 无拟合与 ECE | 旧 DB 仅 47% 可复现；A=7/B=-3 硬编码 | P0 | **部分 DONE**：降级为路由启发式；新增 raw/calibrated/gate/版本/参数 provenance；不再称校准 |
| EFF-32 | CUSUM/Progressive 触发通道 | D4 从未 update；L0 AUROC≈0.5 | P1 | **DONE**：从 Observer 触发中移除，保留为诊断 |
| EFF-33 | Observer 只收到 intent 文本 | 无 Path/网络/凭据/rule ID | P0 | **DONE**：结构化 ContentSignals + prompt 行 + 测试 |
| EFF-34 | Observer authority 由 cache_ttl 自报 | 可无证据抬升 Authoritative | P1 | **DONE**：按 evidence_cited 派生 |
| EFF-35 | L2 verdict prior 自反馈 | 早先裁决影响后续 prompt | P1 | **DONE**：删除 L2，仅 L1 exact dedup；会话边界清理 |
| EFF-36 | Traceback 1-hop 星形 | 把所有 outgoing action 串成链 | P0 | **DONE**：多跳委派回溯 + evidence 过滤 + 上限；测试 |
| EFF-37 | EvaluationSummary phase1 计数恒 0 | drain 后取 len | P1 | **DONE**：drain 前快照 |
| EFF-38 | benchmark capture-health 解析错误 | `"event":"SSL"` 不存在 | P1 | **DONE**：真实 source/diagnostics/RINGBUF_STATS |
| EFF-39 | PBC 计数门把 Observer 挡在门外 | PBC≤6 → score<0.15 → DirectSafe | P0 | **DONE**：有边界/模式/rule → 强制 Observer |

## 2026-10-20 v2 收口（机制审计后）

> 证据：`docs/evaluation/mechanism-audit-2026-10-20-v2.md`；
> 判定口径：`docs/paper/decision-procedure.md`。

| 缺口 | 状态 | 证据 |
|---|---|---|
| 无运行级最终裁决（phase1/phase2 互相覆盖） | 已修 | `classification/final_decision.rs`；schema v3 `runs.final_decision` |
| 告警 timestamp=0 / phase 恒 Provisional | 已修 | `AlertBuilder::compose_with_context`；回归测试 |
| 灰区 → Unsafe；Observer 失败 → Unsafe | 已修 | `ambiguous_review`；失败只 review |
| Observer 缓存命中丢裁决 | 已修 | `CachedObserver` |
| 未转义 prompt / SAFE 先验 / 温度未固定 | 已修 | 新 system prompt；`temperature=0` |
| 权威告警 rule_verdicts 恒空 | 已修 | runtime 从 route output 回填 |
| phase 2 claim 恒空、phase1 claim 被 Dismissed | 已修 | 重建全部 claim；refinement 改 Changed/Confirmed |
| PBC 每轮 trigger 重复建边 | 已修 | `upsert_provisional_pbc` |
| PBC 指向 `..`/`/`；`exfil` 标签泄漏 | 已修 | `is_concrete_path`；敏感路径重标定 |
| 网络规则不可达（0 Network 节点） | 已修 | Command↔Url 匹配 |
| 委派边 0（节点未落库） | 已修 | `record_delegation_with_nodes` |
| 占位 Agent 污染计数 | 已修 | `UNATTRIBUTED_AGENT_ID` |
| traceback 星形 / TCU 根因指错 | 已修 | 只沿证据动作；污点源回溯 |
| TLS ringbuf 仅 ~31 槽 | 已修 | 常量预留 + ring 64 MiB（变量预留被 verifier 拒绝） |
| sudo 产物 root 0600 不可读 | 已修 | chown 给 SUDO_UID/GID |
| RunEnd 丢弃 → ended_at NULL | 已修 | 阻塞写 |
| SSE 绕过 Host 校验；跨 run 混合；artifact 任意路径 | 已修 | web.rs |
| /ready 假报就绪 | 已修 | 无生产者 → 503 |
| 前端 live 告警截断 200 | 已修 | store.ts |
| 契约门禁覆盖窄 | 已修 | check_ts_contract.py 全字段 |
| GT provenance / κ=0.87 | **未解决** | 无产物；论文必须改写（见审计 §5） |
| 旧 TPR/FPR/PBC/消融数字 | **未解决** | 必须用最终 RC 重测 |
| 人工 GT / κ / 多 Agent handoff 语料 | **未解决** | 需团队标注与采集 |
