---
status: accepted
date: 2026-10-19
---

# 0013 — 架构收口：单一类型真源、删除死模块、静默失败可观测

## Context

2026-10-19 的四维只读审计（`archive/HANDOFF-2026-10-18-p0p2.md` §5）确认了 37 条
架构债：13 组重复/影子类型、约 29 处零消费者死模块、6 类静默失败。它们的共同
形态不是"代码写得差"，而是**同一事实存在两个真源**或**观测到的事实被静默丢弃**：

- 根导出 `responsibility::NormalizedResource` 指向无人使用的 legacy 枚举，
  而 `types::NormalizedResource` 才是全部生产代码使用的那一份；
- `claims/alert.rs` 整套告警类型与 `types/alert_types.rs` 并存，前者零消费者；
- `replay/` 有三套 `RunDiff`、两套 `EvaluationMetrics`，其中 `evaluation.rs`
  的 555 行只有自己的测试在调用；
- `judgment/context_trust.rs`(389)、`graph/causal_chain.rs`+`repository.rs`(528)、
  `claims/projection.rs`(270)、`types/signal_types.rs`(310)、
  `framework/replay.rs`(187)、`api_contracts/`(108) 均为零非测试消费者；
- 非 `claude` comm 的 action 在 `routing.rs` 被静默丢弃（OpenClaw `MainThread`），
  >2 MiB 请求体静默产生 0 intent，reader 对损坏 JSON 返回空结构，writer 的
  `TrySendError::Closed` 不计入丢失，`SslConfig`/`ProcessConfig`/`ReplayConfig`
  的字段与 6 个 ablation 开关从未被读取。

## Decision

1. **每个概念只保留一个类型真源。** 删除影子定义，re-export 指向存活实现：
   - `NormalizedResource` → `types/resource.rs`；删除 `types/observation_types.rs`。
   - `RunDiff/OpDiff/DiffChange` → `replay/run_diff.rs`（HTTP route 的唯一实现）；
     删除 `replay/diff.rs` 与 `types/replay_types.rs` 中的副本，保留 `RunRecord`。
   - `EvaluationMetrics` → `replay/executor.rs`；删除 `replay/evaluation.rs`。
   - `PolicyJudgment`/`PolicyRule` → `types/policy_types.rs`；删除 `judgment/` 下两份影子。
   - `GraphPatternTemplate/Match` → `graph/pattern_matcher.rs`（ADR-0012）；删除
     `types/graph_types.rs` 中的旧副本。
   - `AlertAuthority`/`DisplayProfile`/`RuleVerdict`/`PatternVerdict` →
     `types/alert_types.rs`（**持久化与前端契约**）；observer 只 re-export。
   - `ClaimSeverity` → `claims/severity.rs`；删除 `accountability_claim.rs` 中的
     `AccountabilityClaim` 系列（零消费者）。
2. **零消费者模块纯删除，不做"留着以后接线"。** 删除 `context_trust`、
   `replay/{audit,evaluation,diff}`、`framework/replay`、`api_contracts`、
   `graph/{causal_chain,repository}`、`claims/{projection,responsibility_evidence,alert}`、
   `types/{signal_types,observation_types}`、`judgment/{judgment_record,policy_rule}`、
   `evidence/normalizer`、`bpftools/process_ext/` 的 11 个零 include 文件与
   Makefile 假依赖、env 的断链脚本与测试。`ResponsibilityEdge.signal` 字段与其
   18 处 `None` 一并移除。
3. **静默失败先可观测，再谈语义。**
   - 新增 `DIAG_ACTIONS_DROPPED_NO_AGENT`、`DIAG_BODY_TOO_LARGE`，与既有
     `DIAG_EVIDENCE_*`/`DIAG_ACTIONS_PRODUCED` 一起在 run 结束时输出
     `[diagnostics:json]`；
   - agent comm 边界可由 `MTAGENTLENS_AGENT_COMMS=a,b` 扩展；
   - reader 的损坏 JSON 列改为传播错误，不再返回空结构；
   - writer 的 `Closed` 计数并计入 DEGRADED 输出；
   - observer 配置文件存在但解析失败时终止启动，不再谎报 "config not found"；
   - `FileAccessAuditConfig` 的 `enabled`/`monitored_paths`/`excluded_paths`/
     `min_risk_level` 全部接线（默认值保持接线前的行为：全量审计、low 起报）；
   - 无法接线的配置（`SslConfig.tls_version`、`ProcessConfig.{pid,memory_threshold}`、
     `ReplayConfig` 全部字段）删除，而不是保留空操作。

### 追加发现：持久化通道的二次放大与 FK 级联

在重测开销时（活跃会话），日志暴露了 `FOREIGN KEY constraint failed`：
正常 FPR 语料中 3 个 run 分别出现 2,082 / 2,249 / 198 次写失败。一手证据定位到
两个独立缺陷：

1. **每事件重写全部临时身份映射**：`dispatch_event` 对
   `identity_resolver.provisional_identities()` 的每个进程写一条
   `IdentityMapping`，复杂度 O(进程数 × 事件数)。20 秒内产生 49,963 条被丢弃
   记录。修复：按 `(agent, pid, start, belief)` 去重；临时身份行按进程集合变化
   重写（`persisted_provisional_sets`）。
2. **父记录被丢弃后子记录仍写入**：`correlation_evidence` 对
   `intents`/`actions` 有外键，父记录因通道满被丢弃后，子记录必然 FK 失败。
   修复：记录成功入队的父 ID（`persisted_intent_ids`/`persisted_action_ids`），
   父缺失时跳过子记录（丢弃已被计数，不再伪装成第二个缺陷）；`trigger`/
   `finalize` 等批处理路径改用 `write_blocking`（等待容量），实时逐事件路径
   保持非阻塞，避免把内核事件丢掉。

修复后同一 20 秒负载：丢弃 49,963 → 0，写失败 0，`flushed cleanly`。
新增测试：`writer::tests::blocking_write_counts_closed_channel`、
`silent_failure_contract::evidence_is_only_written_when_its_parents_reached_the_writer`。

## Options considered

- **保留影子类型并加 `#[deprecated]`**：不减少真源数量，读者仍会选错；
  且两套 serde 契约（PascalCase vs snake_case）会让"合并"变成隐性 wire 变更。
- **把 `responsibility/server/**` 一并删除**：`serve_request` 零调用，但
  `frontend/lib/api.ts` 的 `getGraph`/`getEvidence` 仍指向它的 `/graph`、
  `/evidence` 路由；删除需要先改前端。本轮保留并在 capabilities 记录为开放缺口。
- **保留 `ReplayConfig` 但文档标注 no-op**：与 ADR-0011 已确立的原则冲突——
  "no-op 开关"会被当成实验能力；删除是唯一诚实的选项。

## Outcome

- 删除 5,718 行死代码，随删除移除 57 条只测死代码的测试；测试总数
  1050 → 1026（其中新增 33 条门/契约测试）。
- clippy 警告预算 251 → 167（实测 167）；`#[allow]` 预算 55 → 49（实测 49）。
- `types/mod.rs`、`claims/mod.rs`、`replay/mod.rs`、`graph/mod.rs`、
  `judgment/mod.rs` 的 re-export 同步为单一真源。
- 新增门/契约测试：
  - `reader::tests::corrupt_json_columns_surface_as_errors`
  - `writer::tests::closed_channel_writes_are_counted`
  - `intent_extractor::tests::oversized_body_increments_diagnostic_counter`
  - `file_access_audit` 的 4 条配置过滤测试
  - `union_find::agent_comm_tests`（默认边界 + 解析）
- **Observer 序列化契约变化（有意且已记录）**：合并前 observer 私有的
  `AlertAuthority`/`DisplayProfile` 带 `rename_all = "snake_case"`，
  `RuleVerdict`/`PatternVerdict` 的 `llm_comment` 带 `skip_serializing_if`；
  合并到 `types/alert_types.rs` 后统一为 PascalCase 与显式 `null`。仓库内
  `ObserverOutput` 从不被序列化（只序列化 `ObserverInput`），因此这是潜在
  公共 API 变化；`alert_types` 的 wire 测试钉住了持久化/前端契约。
- 残余（legacy server、路径规范化三语义、两套 FilterExpression、两套
  `json_to_event`）写入 `docs/capabilities.json` 的 `known_limitations`。

## Residual risk recorded by this round

`judgment/context_trust.rs` (deleted here for having zero callers) was the
intended authorization gate for cross-agent artifact handoffs. Its absence is
measurable: in the July 2026 FuxiBench corpus, `TaintedContextUse` fires in
15/19 benign scenarios (FPR 0.789, Wilson 0.567–0.915) — 9/9 in the
agent_teams subset and 6/10 in single_agent — and 59/67 attacks. The claim type must therefore be treated as an investigative signal
until an authorization gate is designed and wired; the measurement lives in
`compare/results/paper/standard_evaluation_v5.json` (`rq3_attribution`).

## Confirmation

- `./scripts/verify.sh` 14 门全绿（含 clippy/ignored/home/allow 四条只降预算、
  "every .rs is reachable from lib.rs/main.rs"、审计预算）。
- 删除后的模块若被重新引用会直接编译失败；`single_source_constants.rs` 与
  `graph_pattern_contract.rs` 继续守护单源常量与模板生产者。
- 静默失败的可观测性由 `[diagnostics:json]` 行保证：任何一次 run 都必须能
  区分"没有发生"与"发生了但被丢弃"。
