# 单 Schema 迁移完成 — 交接（2026-10-18）

> 这是单 schema 主线（`docs/superpowers/archive/plans/2026-10-18-single-schema.md`）
> T1–T10 完成后的**新会话唯一入口**。上一版 `HANDOFF-2026-10-18.md` 保留
> P0.5–P3 的历史取证；冲突时以本文件为准。

## 0. 60 秒自检

```bash
cd /home/lzk/MtAgentLens
git submodule update --init --recursive   # libbpf / bpftool
./scripts/verify.sh        # 期望 13 条全 PASS（clean clone 实测通过）
cd collector && cargo test # 期望 1052 passed / 0 failed / 3 ignored
```

产物路径：Rust 在 `collector/build/`（不是 `target/`），eBPF 在
`bpftools/.output/bin/`（`make .output/bin/<name>`）。

## 1. 已完成（T1–T10，全部有门）

| 任务 | 结果 | 证明 |
|---|---|---|
| T1 版本脊梁 | `APPLICATION_ID=0x4D54_4C31`、`PRAGMA user_version`、事务迁移、只读分类、共享校验 | `schema.rs` 23 个测试：fresh 盖章 / v0/v1 adoption / causal/unknown/future 零写入 / hot-WAL 零 checkpoint / 回滚重开恢复 |
| T2 旧库 fixture | `collector/tests/fixtures/schema/live-v1.db` 入库；`.gitignore` 例外；`git ls-files` 门 | `tests/schema_upgrade.rs`（列集合 = 冻结 DDL、adoption 不增删行、身份多重集不变） |
| T3 v2 身份信念 | `identity_process_beliefs` 追加式 + 3 个兼容视图；writer/runtime 传 belief 级别；`compact_db.py` 修复 | 视图强度优先测试、append 幂等测试、compact_db unittest（第 13 条门）、前端 SQL 契约测试（`frontend/__tests__/schema-v2-contract.test.ts`，未改前端源码） |
| T4 摘要与告警 | `/alerts` 读 live；summary `artifact_availability` 只认真实可服务的数据；删除 11 个 web ignored | web 测试 65/0 |
| T5–T8 replay + 因果面 | replay 只读 live DB、报告只含真实比较段；删除 `causal_schema`/`sqlite_writer`/`jsonl_writer`/`responsibility_schema`/`GraphStore`/`replay_executor`/`replay/traceback`/`claims/persistence` | ignored 25→3；新 replay 3 测试 |
| T9 路由收口 | `/snapshots` 读 `graph_snapshots` 返回前端契约数组；删除 correlation-evidence/graph-analysis/diff/snapshot-* 路由；`collector/README.md` 更新 | 37 个 web 测试；退役路由 404 钉 |
| T10 ADR + 预算 + sweep | `docs/adr/0005-single-schema.md`（22 行处置表）；预算校准；20/301 真实旧库 adoption | `docs/evaluation/schema-adoption-sweep.json`（列指纹一致、身份 1:1） |

当前基线：

```
门禁        13/13 PASS
测试        1052 passed / 0 failed / 3 ignored
clippy      257（预算 259）
ignored     3（预算 3，全部需要真实二进制/OpenClaw）
hardcoded /home  0（预算 0）
allow sites 56（预算 56）
```

## 2. 关键坐标

- 唯一 DDL/迁移/打开器：`collector/src/responsibility/persistence/schema.rs`
  - `classify_schema`、`run_migrations`、`open_responsibility_db`、
    `verify_readable_schema`、`backup_before_v2`、`SCHEMA_V2_*_DDL`
- 写入：`persistence/writer.rs::append_identity_belief`（追加规则）
- 读取校验：`persistence/reader.rs::PersistenceReader::open`（共享）
- replay：`collector/src/bin/replay.rs`（live-only，报告段
  `causal_links_comparison` / `alerts_comparison`）
- 路由：`collector/src/server/web.rs::match_run_route`（只剩 Summary /
  Evaluation / Alerts / Events / Snapshots / Db）
- 旧库 sweep：`scripts/schema/sweep_legacy_dbs.py` + `collector/examples/schema_adopt.rs`
- clean clone 复现：`scripts/check_fresh_clone.sh`（临时仓库 + clone + 完整门禁）

## 3. 新陷阱（本轮实际踩过）

1. **不要把会写文件的评审 agent 放在同一工作树里**：只读评审 agent 会创建
   `tests/zz_*.rs` 探针甚至回写源文件。评审用独立 clone/worktree，或明确只给
   Read/Grep 工具。
2. **测试会改进程 cwd**（`server::web` 的 `RepoEntryGuard`），任何生产代码里的
   `current_dir()` 读取都可能与之竞态。需要 cwd 的规则改为可注入
   （`RuleEngine::with_workspace_prefix`），测试用显式前缀。
3. **hot WAL 的零写入**：对 causal/未知库即使只 `Connection::open` 后立刻关闭，
   也可能在关闭时 checkpoint `-wal` 改写主库。必须先只读探针分类、拒绝即返回。
4. **未来版本可能只藏在 `schema_version` 表**：`user_version=0` +
   `MAX(schema_version)=99` 必须同样拒绝；`classify_schema` 已内置。
5. **foreign `application_id` 是正向反证**：表名撞车（runs/process_events/intents）
   但 `application_id` 非 0 且非 MTL1 → Unknown，不 adoption。
6. **不要编辑正在运行的 bash 脚本**：bash 按字节偏移继续读取，编辑会让注释片段
   被当作命令执行（实测 `these: 未找到命令`）。改脚本前先停掉它。
7. **fresh clone 有三道隐藏前提**（已修）：
   - `libbpf/`、`bpftool/` 是 git submodule，必须
     `git submodule update --init --recursive`；
   - `frontend/out/` 被忽略且缺失时 rust-embed 无法派生
     `FrontendDist::iter`；现由 `frontend/out/.gitkeep` 占位；
   - 内嵌二进制（`include_bytes!` 的 `.output/bin/*`）必须在**任何编译型门
     之前**构建——clippy 比 tests 先编译 lib，只修 tests 的门会在 clean clone
     的 gate 2 失败；现为 unnumbered **preflight**；
   - `.gitattributes` 的 `* text=auto` 会把 transport fixture 的 CRLF 改成 LF，
     clone 后 manifest size/sha256 全部失配；fixtures 已标 `-text`/`binary`。
8. **`normalized_args_json` 是 `Vec<NormalizedArg>` 的 JSON**（如
   `[{"Command":"..."}]`），不是 `{"command":...}`；写错会静默匹配 0 条。
9. **replay 的旧 ablation 旗标是空操作**（executor 忽略 `ReplayConfig`），已删除；
   不要重新加回未实现的旗标。
10. **门禁的两个假绿机制**：
    - 只数 warning：旧 clippy 门只 `grep -c ': warning:'`，deny 级 lint
      （`proc_stat.rs` 的 `pgid <= u32::MAX`）长期以 `error: could not compile`
      存在却全绿；clean clone 的「19 warnings」正是 3 个
      `couldn't read .output/bin/*` 错误 + 少量 warning，被误读为低预算；
    - **`pipefail` + `grep -q`**：`echo "$OUT" | grep -q FAILED` 中 grep 命中即
      退出，echo 收到 SIGPIPE，pipeline 返回 141，`if` 反而走 else —— 测试失败
      被报成 PASS（本轮实测：1 failed 却 “all gates passed”）。
      全部改为 here-string：`grep -q ... <<< "$OUT"`。
11. 前端本轮冻结：`/diff` 路由已删，但 `frontend/lib/api.ts::getDiff` 未删
   （无调用者，留待前端轮次）。

## 4. P4 通用化（进行中）

**已完成（2026-10-18）**

- **`/home/lzk` 66 → 0**：测试夹具改为 `fixture()`（由 `CARGO_MANIFEST_DIR`
  派生绝对路径）；生产分类规则 `safe.project_read` 改为运行期
  `workspace_prefix()`（cwd 即工作区）；`graph_builder` 的 Claude 缓存前缀改为
  通用后缀、cargo 构建产物前缀改为 cwd 派生；删除
  `classification/rules.rs` 中 166 行零调用重复规则表；C offset 测试改为必须显式
  `MTAGENTLENS_CLAUDE_BINARY`（无默认家目录）。预算 HOMEPATH = **0**。
- **OpenClaw `exec` 的 `{title, code}`**：`evidence/args.rs` 的通用字面量回退已覆盖
  （未知字段扫描显式路径/URL），3 个单测钉住；handoff 旧描述已过时，crosscheck
  注释同步更新。

- **场景词表外置（ADR 0006）**：6 条 shared-artifact 模式表原本在
  `handoff_injection` 与 `multi_agent_anomaly` 各有一份逐字相同的副本，
  `security_signoff` 另有角色默认值。现统一为
  `collector/patterns/scenarios.toml` + `scenario_vocabulary` 单一加载器：
  `include_str!` 默认、`MTAGENTLENS_SCENARIO_PATTERNS` 覆盖、启动时安装、
  坏文件启动失败。4 个测试钉住「两个分析器看到同一张表」与覆盖语义。

**待做：`fileaccess` 决策（已有量化证据）**

一手事实（301 个真实归档库，只读 SQL 聚合）：

| action_type | 行数 |
|---|---|
| FileRead | 93,530 |
| ProcessExec | 18,555 |
| FileOverwrite | 3,386 |
| FileWrite | 3,288 |
| **FileDelete / FileRename / FileTruncate** | **0** |

- `bpftools/process/process.bpf.c` 只挂 `sys_enter_open(at)` 并发 `FILE_OPEN`；
  写/覆盖 action 来自 open flags 的推导，不是独立事件。
- `bpftools/fileaccess` 是独立程序，**未接线**到 collector；即使接线也只覆盖
  open/openat。
- Rust 侧 `routing.rs` 已路由 `FILE_DELETE/FILE_WRITE/FILE_TRUNCATE/FILE_RENAME`，
  但没有生产者——这就是幽灵事件。
- 内核下限已是 **5.8**（capabilities.json），fentry（≥5.5）可用。

**决策（ADR 0007，已实施）**：删除未接线 `fileaccess`；在 `process` 中新增
`sys_enter_unlinkat` / `sys_enter_truncate` → `FILE_DELETE` / `FILE_TRUNCATE`
（`file_op.op` 字段，JSON 由 `process_json.h` 统一打印）。verifier 加载通过，
JSON 契约测试 + 路由契约测试已加。`FILE_RENAME`（双路径 `filepath2`）也已补齐；仅剩 `NETWORK_CONNECT`/`NETWORK_SEND`
为幽灵事件，记录在 capabilities。

环境限制：本 harness 会拦截自身 spawn 进程的文件 syscall（bpftrace 交叉验证
同样为 0），E2E 需在非沙箱环境跑 `scripts/spikes/file-mutation-hooks.sh`。

**（历史）Spike 计划：
1. 同一真实 agent 动作序列（`rm`/`mv`/`truncate`，含 io_uring 路径）分别测
   A) syscall tracepoints（unlinkat/renameat2/truncate）与 B) VFS fentry
   （`vfs_unlink`/`vfs_rename`/`vfs_truncate`，dentry→path）的覆盖率与 verifier 成本；
2. 用 301 库的 intent 侧（Bash 命令含 rm/mv）建立期望集，算漏报；
3. 覆盖率达到产品要求则收编为 VFS 生产者（同一事件 schema 接入 `process` →
   action layer），否则按计划彻底删除 `fileaccess` 并在 capabilities 记录限制。

## 5. P5 出口与治理（已完成）

补充（本轮末）：`verify.sh` 现在在 gate 3 之前先构建三个内嵌 eBPF 二进制
（fresh clone 没有 `.output/bin/`，否则 `cargo test` 的 `include_bytes!` 直接失败）；
CI 依赖补 `libssl-dev`/`libzstd-dev`；新增 `make verify`、`CHANGELOG.md`；
`FILE_RENAME`（双路径 `filepath2`）也已生产，只剩 `NETWORK_*` 为幽灵事件。

- **GenAI 导出**：`collector/src/export/genai.rs` + `mtagentlens export-genai
  --db <db> --out <jsonl> [--include-content]`。scope `io.mtagentlens`、span
  `mtl.gen_ai.alert`；默认只导出 id/枚举/计数/时间戳，自由文本需显式开启且仍脱敏；
  真实库已验证输出 1 span、0 content。
- **原始 SSL 脱敏**：`framework/analyzers/raw_secret_redactor.rs`，在两条 SSL 管线
  输出前重写 HTTP 头中的 Authorization/Proxy-Authorization/Cookie/Set-Cookie/
  X-Api-Key；保留原 `buf_size`，事件带 `redaction` 标记；捕获字节不变。
- **安全默认值**：Web 默认仅绑 `127.0.0.1`（`MTAGENTLENS_BIND` 覆盖）；
  删除全部通配 CORS；运行库可写打开收紧为 `0600`。
- **治理文档**：`SECURITY.md`、`docs/security/threat-model.md`（STRIDE + 代码坐标）、
  `docs/security/openSSF-self-assessment.md`、`CONTRIBUTING.md`；
  ADR 0008。
- **SPDX**：`scripts/` 22 个首方脚本补头；门 7 扩展到 scripts 与首方 eBPF
  （`ecapture/`、`tests/bpf/` 为上游 vendored，frontend 由 package.json 覆盖）。

**仍未做（记录在 openSSF 自评）**：CI runner 接入、`cargo audit`/`npm audit` 门、
release/CHANGELOG、`NETWORK_*` 生产者、前端
`api.getDiff` 删除（前端冻结）。

## 6. 三句话

1. 先跑 `./scripts/verify.sh`，它绿才是真实状态。
2. 任何 PRAGMA 之前先只读分类；拒绝路径绝不读写打开。
3. 每加一条能力加一条门；每删一条能力降一条预算。
