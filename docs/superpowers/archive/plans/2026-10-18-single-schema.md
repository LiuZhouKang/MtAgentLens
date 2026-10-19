# 单 Schema 与 22 个 legacy 测试：修正后的执行计划（2026-10-18）

> **状态**：设计完成（workflow `p3-schema-unification` 的 5 面取证 + 架构 + 对抗评审），
> **迁移尚未执行**。本阶段唯一已落地的有界修复是 **live `/alerts`**（见 §0）。
> 迁移与测试重定向必须按本文件的修正执行，原 10 任务方案中有 18 个缺陷。

## 0. 已落地：live `/alerts`（真实产品断链）

- 根因 1：`resolve_db_for_alerts` 只找 `summary.artifacts.alerts_db` / `trace.log.db` / `*alerts*.db`，
  从不找真实采集产物 `<run>/collector/collector.responsibility.db` → 真实 run 返回 404。
- 根因 2：SQL 选 `agent_id`，live 列是 `primary_agent_id`；prepare 失败后返回 **200 []**，
  把损坏的 artifact 伪装成"没有告警"。
- 修复：`resolve_db_for_alerts` 先认 summary 显式 artifact，再认 live DB；
  用 `PersistenceReader::get_alerts_all()` 读取 live 19 列，序列化为与 SSE 相同的
  `ResponsibilityAlert`（`primary_agent`、类型化枚举、`phase`）；schema 不匹配返回
  **500 `alerts_schema_error`**，空表才返回 200 []。
- 测试：69 个 web 测试全绿；新增 `get_run_alerts_returns_500_on_live_schema_mismatch`；
  fixture 改为写入 live schema。
- 影响：`run_summary_value` 的 `artifact_availability.alerts` / `correlation_evidence`
  也改为 live DB 优先；summary 测试的期望值同步更新。

## 1. 一手事实（workflow 实测）

- 301 个 `env/runs/**/collector.responsibility.db`：`user_version=0` 全部，
  `schema_version max=1` 全部，28–30 张表；与 v1 DDL 的列集合 0 drift。
- 仓库内 `*.causal.db` = **0**；`*alerts*.db` = 0。real run 的 `summary.json` 只有
  `database_path`，没有 `artifacts`。
- 两套 DDL：live 27 表；causal 15 表；同名 5 张但列/语义不同
  （`correlation_evidence`、`non_link_diagnostics`、`delegation_records`、
  `context_pollution_events`、`schema_version`）。两套都写 version 1。
- 22 个 ignored：`bin/replay.rs` 11 + `server/web.rs` 11。
  9 个 replay 测试在 fixture 的 `INSERT INTO agent_instances` 处失败（no such table）。
- `/api/runs/:id/alerts` 的 200 [] 与 `/snapshots` 的契约错配已确认（前者已修）。

## 2. 对抗评审的 18 条修正（绑定）

| # | 修正 |
|---|---|
| D1 | `.gitignore:118` 的 `*.db` 会拦住 DB fixture；必须加 `!collector/tests/fixtures/schema/*.db`，并断言 `git ls-files` 已跟踪，而非只断言存在 |
| D2 | gate 13 的"退役标识符 0 命中"与 schema.rs 必须出现的 causal marker 冲突；对 marker 常量做行级 allowlist，或只检测 `CREATE TABLE` |
| D3 | 验收"缺 identity_process_map 的 v1 库在 v2 失败"与 adoption 规则 (c) 冲突；fixture 必须是 `user_version=1`，或规则 (b)/(c) 在已有用户表时不重跑 v1 |
| D4 | v2 会改 `schema_version` 行数与 `identity_process_map` 的主键；"每表行数/PK 摘要不变"不可满足。排除 schema_version，identity 改用信念多重集断言 |
| D5 | `VACUUM INTO` 第二次会因文件已存在失败，失败库永久打不开；备份前存在则跳过/删除，或用唯一临时名 + rename；加"失败后重开可恢复"测试 |
| D6 | 只拒绝三个 causal marker 不够；adoption 必须**正向识别 live 库**（`runs`/`process_events`/`intents` 存在）才允许；否则返回 `UnknownSchema` 零写入；补 `causal_links`/`agent_processes`/`action_events` 标记 |
| D7 | "当前 live opener 会给因果库加 27 张表"是反的：它跳过 DDL 后静默空写；反向才成立（causal opener 会给 live 库加 10 张）。改写测试依据 |
| D8 | 拒绝路径的"零写入"与先设 WAL 冲突；分类/版本检查必须在任何 PRAGMA 之前（或只读连接），验收加文件头/journal_mode 断言 |
| D9 | `verify_readable_schema` 只挂在 `open_responsibility_db_read_only`；`PersistenceReader::open` 直接 open_with_flags，绕过校验。提取共享校验并覆盖所有直接打开点 |
| D10 | `/api/runs/:id` summary 仍接退役 resolver/loader（`accountability_audit_summaries` 不在任何 schema）；T9 删除后会编译失败且 `artifact_availability` 继续撒谎。summary 必须随 T4/T9 一起改 |
| D11 | REST `/alerts` 的 `phase` 来自 `format!("{:?}", ...)` Debug 串，SSE 是 serde 对象；必须用同一 `ResponsibilityAlert` 序列化（本次已修） |
| D12 | T2 的 v2 断言在 T1（LATEST 仍为 1）时是空转或失败；v2 函数必须与 T1 同批，或把升级断言移到 T3 之后 |
| D13 | replay report 没有 `snapshot_comparison` 消费者；若重写 snapshot loader 而不比较，就是死代码；二选一并写明；env verifier 依赖该 section 的现状要记录 |
| D14 | 删除 replayed causal artifacts 与 env verifier 的 REQUIRED 清单未对齐；把两个 verifier 加入 T5/T10 或显式记录不修 |
| D15 | `compact_db.py` 不含 beliefs，`type='table'` 枚举会漏掉视图；加测试，并纳入 `freeze_10.py` |
| D16 | `collector/README.md` 仍写 `/correlation-evidence`，文档清单遗漏 |
| D17 | 旧二进制在 v2 视图上 `INSERT OR REPLACE` 会静默丢映射（writer 只记 write_failure）；ADR 必须记录单向迁移残余 |
| D18 | `identity_process_map_by_run` 创建后唯一按 run 过滤的读者（`compare/scripts/vis/legacy/generate_case_studies.py`）没有切换；要么切到该视图，要么删除并在文档说明单 run 假设 |

## 3. 修正后的迁移机制

- **唯一 DDL 权威**：`persistence/schema.rs`。
  - `SCHEMA_V1_DDL` = 今天的 27 表 DDL **冻结逐字**；`pub const SCHEMA_DDL = SCHEMA_V1_DDL`。
  - `APPLICATION_ID = 0x4D544C31`、`LATEST_SCHEMA_VERSION = 2`、
    `MIGRATIONS: &[(i64, fn(&Connection) -> Result<()>)]`。
- **打开顺序**（任何 PRAGMA 之前）：
  1. 只读分类：causal markers（含 `agent_instances/intent_events/action_candidates/agent_processes/action_events/causal_links`）→ `LegacyCausalSchema` 零写入；
     非 live 形态（无 `runs/process_events/intents`）→ `UnknownSchema` 零写入；
     `user_version > LATEST` → `UnsupportedSchemaVersion` 零写入。
  2. 之后才设置 WAL/synchronous。
  3. `effective = max(user_version, COALESCE(MAX(schema_version),0))`。
  4. 每个迁移 `BEGIN IMMEDIATE`；DDL + `PRAGMA user_version` + `application_id` +
     `INSERT OR IGNORE schema_version` 同一事务；失败 ROLLBACK。
  5. 非空文件库在 v2 前备份：`<db>.pre-v2.bak`，**已存在则跳过**（D5）。
- **v2**：`identity_process_map` 表 → `identity_process_beliefs` 追加式
  （`belief_id`、`belief_level CHECK(Unknown|Provisional|Inferred|Authoritative)`、
  `observed_at_ns`、`source`）；迁移按 `agent_identities`/`provisional_identities`
  分类，绝不编造；创建兼容视图 `identity_process_map`（每进程一行）、
  `identity_process_map_by_run`、`identity_process_belief_windows`。
  写入只进 beliefs，且只在最新信念不同时追加。
- **读取**：只读打开不迁移；共享 `verify_readable_schema` 覆盖
  `open_responsibility_db_read_only` 与 `PersistenceReader::open`。
- **计数口径**：明确是否含 `sqlite_*`；验收排除 `schema_version` 行数与 identity 主键。

## 4. 任务顺序（修正版）

1. **T1** 版本脊梁：应用 id + user_version + 事务迁移 + 正向 live 识别 + 共享只读校验。
   测试：fresh 盖章、v0/v1 adoption、future/causal/unknown 零写入、失败回滚 + **重开可恢复**。
2. **T2** 旧库 fixture（`.gitignore` 例外 + `git ls-files` 断言）+ 升级集成证明
   （v1 用 `user_version=1` 的 fixture；计数排除 schema_version）。
3. **T3** v2 身份信念 + 视图 + writer/runtime belief 级别 + `compact_db` 修复 + 前端 SQL 契约测试。
4. **T4** live `/alerts`（**已提前完成**）+ summary artifact 字段 + 删除零调用 enrichment/11 个 web ignored。
5. **T5** replay `--run-dir` 指向 live DB；`replay.report.json` 只比较真实比较的 section。
6. **T6** replay loaders 只读 live 表；删除 legacy 分支。
7. **T7** 处置 11 个 replay ignored：5/6/8 重定向，其余逐条带证据退役；IGNORED 25→3。
8. **T8** 删除 causal_schema/sqlite_writer/jsonl_writer/responsibility_schema/GraphStore/
   replay_executor/traceback/claims-persistence；gate 13 预算归零。
9. **T9** `/snapshots` 读 `graph_snapshots`；删除 causal-only 路由与前端 `api.getDiff`；
   更新 `collector/README.md`。
10. **T10** ADR 0005（22 行处置表）+ 预算校准 + 真实旧库 sweep（20 个副本，含列集合指纹）。

## 5. 不变量

- 不改 live 表/列（v1 加法 only）；v2 只重建 identity。
- 不改 `collector.responsibility.db` 文件名、raw DB 流、SSE 帧、CORS。
- 不复活 `violation_pattern_matches`/`evidence_records` 来迁就旧测试。
- 不为退役端点做双 schema 读者。
- 旧二进制不可写 v2 库（视图）——单向迁移，ADR 记录。
