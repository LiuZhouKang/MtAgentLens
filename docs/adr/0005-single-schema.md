---
status: accepted
date: 2026-10-18
---

# 0005 — 单一 schema：版本脊梁、身份信念、退役因果面

## Context

采集器曾同时维护两套 DDL：live 27 表（`persistence/schema.rs`）与已停产的
causal 15 表（`causal_schema.rs`）。两者都写 `schema_version=1`，同名表列语义
不同，且：

- 301 个真实归档库（`env/runs/**/collector.responsibility.db`）全部
  `user_version=0`、`application_id=0`，仅靠 `schema_version` 表记录版本；
- 仓库内 `*.causal.db` = 0，采集器只产 `collector.responsibility.db`；
- 22 个测试因面向因果 schema 而永久失败（ADR 0004 以预算记账）；
- 打开器对任意路径直接 `PRAGMA journal_mode=WAL` 后跑 `CREATE TABLE IF NOT
  EXISTS`，foreign DB 会被静默嫁接 27 张表；
- live `/alerts` 在真实 run 上 404 或 200 []，把损坏产物伪装成“没有告警”。

## Decision

**一个 schema、一个写入口、一个版本脊梁。**

1. `APPLICATION_ID = 0x4D54_4C31`（"MTL1"）写入每个采集器拥有的数据库；
   `PRAGMA user_version` 与 `schema_version` 表由同一事务维护。
2. `MIGRATIONS: &[(version, fn)]` 是唯一变更通道；`SCHEMA_V1_DDL` 冻结逐字。
   当前 v5 在 `intents` 增加 nullable 的 `native_tool_call_json` 与
   `declared_conversation_id`，保留捕获的原生调用引用与声明会话；旧行保持 NULL。
   reader 支持旧列缺失，但非空损坏 JSON 必须报错。引用不是身份认证或执行证明。
3. **任何写入前先只读分类**（`classify_schema`）：因果标记 / 未知形态 / 未来
   版本 → 零写入拒绝；只有正向识别为 live（`runs`+`process_events`+`intents`）
   或空文件才允许迁移。拒绝路径绝不以读写方式打开数据库（hot WAL 会因关闭时
   checkpoint 而改写文件）。
4. v2 把 `identity_process_map` 表替换为追加式 `identity_process_beliefs`
   （`belief_level ∈ Unknown/Provisional/Inferred/Authoritative` + `observed_at_ns`
   + `source`），并保留三个兼容视图：`identity_process_map`（每进程一行，最强
   信念优先）、`identity_process_map_by_run`、`identity_process_belief_windows`。
   写入只进 beliefs，且只在最新信念不同时追加。
5. 因果面整体退役：`causal_schema`、`sqlite_writer`、`jsonl_writer`、
   `responsibility_schema`、`GraphStore`、`replay/replay_executor`、
   `replay/traceback`、`claims/persistence` 全部删除；`replay` 只读 live DB。
6. `/api/runs/{id}/snapshots` 读 `graph_snapshots` 并返回前端契约的 **数组**
   （`{snapshot_id,timestamp_ns,nodes,edges,metrics}`）；因果专属路由
   （correlation-evidence、graph-analysis、diff、snapshot-* ）删除。

## 22 个 ignored 测试的逐条处置

| # | 测试 | 处置 |
|---|---|---|
| 1 | `web_alerts_surface_claim_state_and_policy_fields_without_frontend_invention` | 删除：写入 `trace.log.alerts.db` + causal links，路由已退役 |
| 2 | `web_alerts_load_authoritative_policy_and_traceback_rows_from_causal_db` | 删除：同上，读 `evidence_records`/traceback replay |
| 3 | `get_run_alerts_filters_malformed_causal_evidence_fail_closed` | 删除：causal evidence 列已不存在 |
| 4 | `get_run_alerts_returns_explicit_error_for_malformed_causal_evidence_ids_json` | 删除：`trace.log.alerts.db` 专属 |
| 5 | `get_run_alerts_returns_explicit_error_for_malformed_authoritative_causal_links_json` | 删除：causal links 专属 |
| 6 | `get_run_alerts_returns_explicit_error_for_malformed_evidence_json` | 删除：legacy alerts DB 专属 |
| 7 | `run_alerts_surface_replayable_accountability_traceback_when_present` | 删除：traceback replay 表不在任何 schema |
| 8 | `run_alerts_fail_closed_when_accountability_traceback_replay_is_malformed` | 删除：同上 |
| 9 | `run_alerts_fail_closed_when_accountability_traceback_replay_has_empty_chain_node_ids` | 删除：同上 |
| 10 | `run_alerts_fail_closed_when_accountability_traceback_replay_root_cause_is_outside_chain` | 删除：同上 |
| 11 | `get_run_alerts_reads_legacy_rows_without_evidence_source_column` | 删除：legacy `alerts` 表专属 |
| 12 | `test_replay_run_dir_preserves_live_candidate_before_upgraded_request_history_intent_ordering` | 删除：`action_candidates` 是 causal 表；replay 已重写 |
| 13 | `test_replay_run_dir_materializes_replayed_causal_links_with_intent_provenance` | 删除：产出 causal 工件的能力已退役 |
| 14 | `test_load_evidence_records_round_trips_accountability_fields` | 删除：`evidence_records` 表已删除 |
| 15 | `test_load_non_link_diagnostics_strict_rejects_invalid_parse_state_enum` | 删除：loader 已删除 |
| 16 | `test_replay_run_dir_reports_linked_intents_with_no_argument_match_noise` | 删除：causal link 报告段已移除 |
| 17 | `test_replay_run_dir_reports_link_comparison_section_and_parity_gap_when_links_differ` | 替换：新 `replay_reports_parity_when_persisted_evidence_matches` 在 live 证据上断言 parity |
| 18 | `test_replay_run_dir_preserves_conversation_only_original_agents` | 删除：依赖 `agent_instances` causal 表 |
| 19 | `test_replay_run_dir_writes_authoritative_report_for_valid_causal_db` | 替换：新 `replay_reads_the_live_database_and_writes_a_report` |
| 20 | `test_replay_run_dir_materializes_replayed_authoritative_rows_for_policy_grounded_links` | 删除：causal 物化已退役 |
| 21 | `test_replay_run_dir_reports_snapshot_comparison_section` | 删除：`snapshot_comparison` 比较的是 `accountability_audit_summaries`（不在任何 schema）；见下 |
| 22 | `test_replay_run_dir_materializes_violation_pattern_matches_for_shared_artifact_sequence` | 删除：`violation_pattern_matches` 表已删除 |

另新增 3 个 live replay 测试：读 live 库并写报告、persisted/replayed 证据
parity、无 live 库时报错；以及 4 个 `tests/schema_upgrade.rs` 升级契约测试。

## 已知残余（单向迁移）

- **旧二进制不可写 v2 库**：旧 writer 的 `INSERT OR REPLACE INTO
  identity_process_map` 在 v2 库上会命中视图，SQLite 报
  `cannot modify identity_process_map because it is a view`。旧 writer 把失败
  计入 `write_failures` 并打印，但该行的身份映射不会落盘。升级是单向的：
  用新二进制写过的 run 不能再用旧二进制补采。这是接受的代价，避免为退役
  schema 维护双写。
- **`snapshot_comparison` 已移除**：它比较的
  `accountability_audit_summaries`/`accountability_graph_snapshots` 不属于任何
  当前 schema；回放不产生可比较的快照。`env/scripts/verifiers/
  subagents-handoff-injection.py:594` 在缺该段时返回 `legacy_only`（非 fail），
  `subagents-security-response.py:792` 仅在存在时记录计数。两个 verifier 不改，
  此处显式记录。
- **前端 `api.getDiff` 未删**：本轮约束是前端不动；`/diff` 路由已退役，
  该客户端方法无调用者，作为已知死代码留待前端轮次删除。

## Confirmation

- `./scripts/verify.sh` 的 13 条门：新增 compact_db 契约门（v1 表与 v2
  视图/信念在压缩后均存活，header 保留）；ignored 预算 25 → **3**。
- `collector/src/responsibility/persistence/schema.rs` 中的门：
  fresh 盖章、v0/v1 adoption、future/causal/unknown **零写入**（含 hot WAL 与
  藏在 `schema_version` 表里的未来版本）、迁移失败回滚 + 重开恢复、备份
  只建一次且不覆盖、前端 SQL 契约（`SELECT agent_id, pid, start_time_ns FROM
  identity_process_map` 逐字不变）。
- 前端契约：`cd frontend && npx vitest run __tests__/schema-v2-contract.test.ts`
  （在真实 v1 fixture 上重建 v2 身份层，断言概览计数、身份全行、process JOIN
  标注逐字不变；`npm run type-check` 通过）。
- `collector/tests/schema_upgrade.rs` 对入库的 v1 fixture（
  `collector/tests/fixtures/schema/live-v1.db`，`git ls-files` 门保证可被第三方
  否证）断言列集合 = 冻结 DDL、adoption 不增删行、身份多重集不变、视图存在。
- `scripts/schema/sweep_legacy_dbs.py`：301 个真实归档库中确定性抽取 20 个副本，
  全部 adoption 成功、身份行数 1:1 保留、列集合指纹一致
  （`docs/evaluation/schema-adoption-sweep.json`）。
- 预算：clippy 265 → 259，ignored 25 → 3，allow 59 → 56，hardcoded `/home`
  67 → 66。
