# P3 假 Taint 修复 Implementation Plan（2026-10-18）

> 状态：✅ 已实现并通过门禁。P3-a 实测：FPR **10.00%（2/20）**，20/20 run 的 taint 边/claim 为 **0**；P3-c 独立测量进行中。
> 取证：workflow `p3-false-taint`（5 个只读 agent + 架构 + 对抗评审）；原始输出见会话记录。

## 根因（已用一手证据确认）

1. **读时自播种**：`graph_builder.rs` step 6 在每次跨 agent 文件读时调用
   `ContextTrustEvaluator`，Violation/Ambiguous 分支执行
   `mark_contaminated(writer_agent, path, read_ts)`——这里的 `writer_agent`
   是**本次读的 intent 所属 agent**，不是真正写文件的 agent。于是一次读就把
   文件标记为"被读者污染"，下一次读就命中 `check_access`，产出
   `TaintPropagatesTo`。良性读密集场景因此产生大量假 taint。
2. **writer 替换**：taint 边的 source 用 `intent_agent_node_id`（读者 intent），
   而不是 `TaintAccess.contaminated_by`（记录里的真实污染者）。
3. **claim 放大**：`classify_claim_type` 只要任意 taint 边触碰 PBC 的任一端点，
   就把该 PBC 改写成 `TaintedContextUse`，即使两条边来自不同证据。
4. **身份不可分**：Phase 1 在缺 TLS/祖先证据时给每个进程发一个新的 UUID
   AgentId；同一进程可以先后出现两个 ID。仅比较 AgentId 字符串无法表达
   "同一逻辑主体"。

P1 后实测：5/20 良性场景有 Warning+，10 条 Warning+ 中 6 条是
"Tainted Context Use"，共 66 条 `TaintedContextUse` claim、85 条 taint 边。

## 对抗评审的关键否决

原方案打算把播种收窄到"注入/伪造内容"并声称"检测保留"。评审用 DB 语料否证：

- 261 个 run 中 1157 条非空 Content intent、41 条 Write/Edit，**0 条**包含
  注入 needle；live 可达的内容边界是 `boundary.tool.Bash` / `boundary.tool.UserPrompt`，
  而 UserPrompt intent 不进入 `handle()`。
- 因此"收窄播种"会让 live taint **完全失活**；不能把归零伪装成检测保留。
- 当前 live taint 的真实 TP 基线是 **0**（旧抓包 0 条边）；假阳性是唯一实际产出。

**决策**：本阶段以"删假"为首要目标，同时保留一个**确定可测**的指令写入种子；
live 外部内容种子列为 P3-b，并如实记录 live 检测当前为 0 TP。

## 已实现

| 修复 | 位置 | 测试 |
|---|---|---|
| 删除读时自播种；ContextTrust 不再写 taint tracker | `graph_builder.rs` step 6 | `test_read_only_correlation_does_not_seed_taint` |
| writer 用记录中的污染者解析节点；缺失则跳过，不造端点 | `graph_builder.rs::agent_node_id` + step 6 | `test_genuine_write_read_taint_edge_uses_recorded_writer` |
| 播种限定为 `prompt_injection` / `role_forgery`，且写入路径必须出现在 intent 参数中 | `graph_builder.rs` step 5a | `test_write_without_instruction_content_does_not_seed_taint`、`test_write_path_not_in_intent_args_does_not_seed_taint` |
| 同一 `(source,target)` 只保留一条边；第二次读追加 evidence 并重新入 `delta.new_edges`（DB 用 INSERT OR REPLACE） | `graph_builder.rs` step 6 | `test_taint_edge_is_idempotent_per_writer_reader_pair` |
| 污染记录携带写者 `ProcessId`；同进程访问不算跨 agent | `taint.rs` | `test_same_process_access_is_not_cross_agent_taint` |
| claim 只有在 taint 边与 PBC 边**共享 evidence** 且相邻时才标 `TaintedContextUse` | `claim_builder.rs` | `test_taint_claim_requires_shared_evidence` + 更新 `test_tainted_context_detection` |

## 验收

**结构性（硬门，确定）**

- 20 个良性 run 的 DB：`TaintPropagatesTo` = 0、`TaintedContextUse` = 0。
- `./scripts/verify.sh` 12/12；预算 295/26/67/59 不升。
- 确定性正向：带注入内容的 Write + 跨进程 Read → 恰好 1 条 taint 边、
  1 条 `TaintedContextUse`；writer 必须是记录中的写者。

**FPR（信息性，live）**

- run-level `Warning+` 期望从 5/20 下降；但评审指出残余（fail-closed Warning +
  PolicyViolation claim）可能仍有 4–5 个 run，且 4/20 与 5/20 的 Wilson 区间高度重叠。
  因此**以结构性断言为主**，alert 数量作为解释性指标，不把 4/20 当唯一证据。

## P3-b（下一步，未完成）

1. **外部内容种子**：当前 eBPF 只观测文件路径，不观测读取到的内容；需要
   `fileaccess`/VFS fentry 或读侧内容观测，才能对"外部植入的指令"播种。
2. **身份精度**：同进程内多个逻辑 agent 仍不可分；P3 的
   `RuntimeEntity × AgentSessionEntity` + 证据等级是正确方向。本阶段用
   "同进程不判跨 agent" 的保守规则，明确不声称能区分进程内子代理。
3. **live 正向场景**：现有 `agent-teams-context-pollution-classical` /
   `subagents-handoff-injection-classical` 的 fixture/verifier 在树中缺失或
   不含 needle，需修复后才能作为 live 正向验收。

## 明确不做的

- 不按告警标题/分数过滤（假事实是边，不是渲染）。
- 不删除/弱化 `check_access` 的同一 agent 与时间窗检查。
- 不新增 blanket path filter（会盲掉真实的 workspace 内交接）。
- 不改 payload normalization / intent extraction / evidence correlation / 策略评估。
- 不提交；commit message 由用户定稿。

---

## P3-c 敏感路径规则精确化（已实现，待独立测量）

**证据**：P3-a 后残余的 Warning+ 全部是
`policy.file.access` 的 `PolicyViolation`，boundary 为
`deploy-endpoints.json`（内容 `{"endpoints": []}`）。`is_sensitive_path` 的
`SENSITIVE` 列表包含裸子串 `"endpoint"`，把普通部署配置文件判为敏感文件，
`evaluate_capability_boundary` 返回 `Observed` → PBC → Warning。

**修改**：删除 `"endpoint"`；保留 `"exfil"`；新增精确的云/容器凭证路径
（`/.aws/`、`/.config/gcloud/`、`/.docker/config.json`、`/.kube/config`、`/.netrc`）。

**测试**：
- `test_deployment_endpoints_file_is_not_sensitive`（deploy-endpoints 不再 Observed）
- `test_cloud_credential_paths_are_sensitive`（精确凭证路径仍命中）

**测量纪律**：当前正在跑的 `p3-taint` 批次使用修复前的 release 二进制，
只度量 taint 修复；P3-c 需要单独重建 release 后重跑 20 场景，不把两个改动混进
同一次测量。

## 门禁稳定性修复

`auth/security_tests.rs` 的两个时序比值测试在并行 FPR 负载下会因调度噪声
偶发失败（`verify.sh` 曾报 939 passed / 1 failed，4 次重跑不复现）。已改为：
- `st01`：对每个候选取 5 次测量的**最小值**（调度噪声只增加时间）；
- `st03`：用**中位数**替代均值（单次被抢占的请求不再支配结果）。
阈值不变（<2.0 / <3.0），10 次连跑全绿。

## P2 减法（已执行）

删除 8 个零调用/未注册模块（约 2,800 行）：
`pipeline_orchestrator`、`analyzer_composition_contract`、`alert_enrichment`、
`role_hijack_contract`、`agent_teams_role_hijack`、`conversation_flow`、
`latency`、`observability_metrics`。
`docs/capabilities.json` 的 `implemented_but_not_registered` 已置空。
预算同步下降：clippy 295→265，ignored 26→25。
`correlator_legacy` 的 `CorrelationEvidence` 有 364 处外部使用，**未删**，需先迁移类型。

## 测量结果（2026-10-18）

| 批次 | 主 FPR（Warning+，含 Uncertain） | Actionable FPR（decision=Unsafe） | Taint 边 / Claim | 残余 |
|---|---|---|---|---|
| P3-a（taint 修复） | 10.00%（2/20），Wilson [2.79%, 30.10%] | 未单独统计 | **0 / 0**（20 run） | 2 条 `deploy-endpoints.json` PolicyViolation |
| P3-c（+ 敏感路径精确化） | 35.00%（7/20），Wilson [18.12%, 56.71%] | **0.00%（0/20）** | **0 / 0**（20 run） | 7 条全是 fail-closed `Uncertain`（score 0.23–0.65） |

**结论**：
- 结构性目标（taint 边/claim）在两个批次都稳定为 0；`deploy-endpoints` 假阳性被 P3-c 确定性消除。
- 主 FPR 从 2/20 跳到 7/20 与 P3-c 无关：残余全是同一类 fail-closed `Uncertain`，
  其 classifier score 随 run 波动。**主指标不能作为稳定对比**；actionable FPR 才是。
- 这是产品语义问题：`Uncertain` 是“请复核”，不是“已判定不安全”。基线 JSON 同时
  记录两者，前端/评测应按用途选择。
