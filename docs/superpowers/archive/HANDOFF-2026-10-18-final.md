# MtAgentLens 后端整治 — 交接（最终状态，2026-10-18）

> **⚠️ 本文件的 P0–P2 已在同日晚间全部执行完毕。最新状态见
> [`HANDOFF-2026-10-18-p0p2.md`](HANDOFF-2026-10-18-p0p2.md)（14 门 / 1023 tests /
> 审计 0 漏洞 / 新增 ADR 0009–0012）。本文件保留为问题取证与决策依据。**

> **这是新会话的唯一入口。** 它自包含、不假设任何先前对话。
> 每条结论都有命令或 `file:line` 支撑；读不懂就先跑命令，不要相信文字。
> 历史版本：`HANDOFF-2026-10-18-single-schema-done.md`（T1–T10 细节）与
> `HANDOFF-2026-10-18.md`（P0.5–P3 取证）。冲突时以本文件为准。

---

## 0. 60 秒自检（进仓库第一件事）

```bash
cd /home/lzk/MtAgentLens
git submodule update --init --recursive     # libbpf / bpftool（必须）
./scripts/verify.sh                         # 期望 "verify: all gates passed"（13 门）
cd collector && cargo test                  # 期望 1052 passed / 0 failed / 3 ignored
```

第三方可复现性（会建临时 git 仓库并 clone 到 /tmp，约 8–10 分钟）：

```bash
./scripts/check_fresh_clone.sh               # 期望 FRESH_CLONE_VERIFY=ok
```

**当前已验证状态（2026-10-18，主仓库与 clean clone 各自实测）**

```
门禁             13/13 PASS（含 preflight）
测试             1052 passed / 0 failed / 3 ignored
clippy           257（预算 259）
ignored          3（预算 3：2 个需真实二进制 + 1 个需 live OpenClaw）
hardcoded /home  0（预算 0）
allow sites      56（预算 56）
clean clone      FRESH_CLONE_VERIFY=ok（2707 文件）
HEAD             8e37ac8（未提交；工作树 430 项改动）
```

**产物路径陷阱**：`collector/.cargo/config.toml` 设 `target-dir = "build"`，
Rust 产物在 **`collector/build/{debug,release}/`**，`collector/target/` 是旧码。
eBPF 产物在 `bpftools/.output/bin/`，构建用 `make .output/bin/<name>`。

---

## 1. 工作方法（比任何结论都重要）

**信条：先可信，再通用；内核自持，边界生成。**
唯一不可妥协：内核观测到的事实不能静默损坏、静默丢弃、静默假装存在。

**「更深入，不是更大」**：先用不可辩驳的一手证据把系统钉死，再以「证明」为单位
删改。每加一条能力，同时加一条能证明它存在的门；每删一条能力，同时把预算降下来。

**动手前四问**：
1. 该领域顶尖专业人士会做、而用户没要求的事是什么？
2. 他们会从哪些最原始材料开始？
3. 他们会用什么具体方法与工具？（差分/蜕变/性质测试、确定性 oracle、独立解码、
   FPR+Wilson、开销契约）
4. 除了我和用户，什么能证明结果是对的？（内核 verifier → agent 自己的记录 →
   独立解码器 → 确定性 → 第三方 clone 跑门禁 → 留出集 → FPR）

**原始材料优先级**：内核 tracepoint/BTF → 原始 wire 字节 → `/proc`/`bpftool` →
agent 自己落盘的记录 → DB 行本身（SQL，而非 reader 代码）→ 上游规范 → git 历史 →
构建产物。**禁止**用二手结论代替一手证据。

---

## 2. 代码地图（当前形态）

```
bpftools/                 eBPF（3 个程序，最低内核 5.8）
├── tls_capture/          SSL_read/write uprobe → data_b64（字节无损）
├── process/              exec/exit + 文件 open/unlinkat/truncate/rename(at2)
│   ├── process.h         struct event（单一真源；不要再复制到 include/）
│   ├── process_json.h    FILE_OPEN/DELETE/TRUNCATE/RENAME 的 JSON 行
│   └── process.bpf.c     tracepoints（含 3 个文件变更生产者）
├── stdiocap/             MCP stdio read/write（copy_failed/dropped_events 诚实化）
└── vmlinux/x86/vmlinux.h 已入库

collector/src/
├── framework/
│   ├── runners/          Ssl/Process/Stdio/System/Agent
│   ├── analyzers/
│   │   ├── scenario_vocabulary.rs  patterns/scenarios.toml 的单一加载器
│   │   ├── raw_secret_redactor.rs  SSL 头值脱敏（含 obs-fold）
│   │   ├── handoff_injection.rs    shared-artifact 传播检测（词表驱动）
│   │   ├── multi_agent_anomaly.rs  多 agent 异常（词表驱动）
│   │   ├── security_signoff.rs     签核检测（角色默认值来自词表，**但识别逻辑仍硬编码角色字面量**）
│   │   └── presets.rs              record 预设过滤器
│   └── normalize/transport.rs      data_b64 解码断言（decoded_len == buf_size）
├── export/genai.rs       content-free GenAI span 导出（默认无内容）
├── responsibility/
│   ├── persistence/schema.rs       唯一 DDL/迁移/打开器（APPLICATION_ID + user_version + v2 信念）
│   ├── persistence/reader.rs       共享只读校验 + 查询
│   ├── persistence/writer.rs       append_identity_belief（追加式）
│   ├── runtime/routing.rs          event → intent/action 路由（FILE_* 全映射）
│   ├── identity/union_find.rs      AGENT_COMMS（与 signal_snapshot.rs 有重复副本）
│   ├── evidence/                   相关器/分类器/参数提取
│   └── replay/                     live-only 回放（executor/diff/evaluation）
├── server/web.rs                   路由只剩 Summary/Evaluation/Alerts/Events/Snapshots/Db
└── main.rs                         record/trace/ssl/process/stdio/system/export-genai
```

**关键决策文档**（改动前必读）：
- ADR 0005 单 schema（版本脊梁/身份信念/因果面退役）
- ADR 0006 场景词表是数据
- ADR 0007 文件变更生产者（删 `fileaccess`）
- ADR 0008 数据出口安全默认值（无内容导出/脱敏/仅回环/0600）
- `docs/security/threat-model.md`、`docs/security/openSSF-self-assessment.md`

---

## 3. 已完成的全部工作（都有门）

### 3.1 单 schema（T1–T10）

- `APPLICATION_ID=0x4D54_4C31`、`PRAGMA user_version`、事务迁移梯；
  任何 PRAGMA 前只读分类：causal/unknown/未来版本**零写入**拒绝；hot-WAL 不
  checkpoint；迁移失败回滚 + 重开恢复；v2 `identity_process_beliefs` + 三个兼容视图。
- 30 个老库 adoption：301 个真实归档库抽 20 个副本，全部成功、身份 1:1、
  列指纹一致（`docs/evaluation/schema-adoption-sweep.json`）。
- 因果面退役：`replay` 只读 live DB、报告按语义键做多重集比较；
  22 个 causal-era 测试处置见 ADR 0005。

### 3.2 P4 通用化

- `/home/<user>` 66 → **0**；场景词表外置（ADR 0006）。
- 删除未接线 `fileaccess`；`process` 新增 `FILE_DELETE`/`FILE_TRUNCATE`/
  `FILE_RENAME`（双路径）。verifier 真实加载通过；JSON 契约 + 路由/分类契约测试。
- 301 个真实库中 delete/rename/truncate action 由 0 变为“有生产者可用”。

### 3.3 P5 出口与治理

- `mtagentlens export-genai`（默认 content-free，ADR 0008）。
- `RawSecretRedactor`：输出前重写 Authorization/Cookie 等头值（含折行），
  捕获字节不变，事件带 `redaction` 元数据。
- 默认仅绑 `127.0.0.1`；无通配 CORS；运行库 `0600`。
- `SECURITY.md`、威胁模型、OpenSSF 自评、`CONTRIBUTING.md`、`CHANGELOG.md`；
  SPDX 门覆盖 Rust/scripts/首方 eBPF（vendored 上游豁免）。

### 3.4 可复现性与门禁诚实性（本轮最重要的意外收获）

`scripts/check_fresh_clone.sh` 会从工作树建临时 git 仓库、clone、跑完整门禁。
它挖出并修复了：

1. `libbpf`/`bpftool` 是 **submodule**，未初始化时 eBPF 构建立即失败；
2. `frontend/out/` 缺失时 rust-embed 无法派生 `FrontendDist::iter`
   → 提交 `frontend/out/.gitkeep` 占位；
3. `.gitattributes` 的 `text=auto` 改写 transport fixture 行尾，clone 后 manifest
   size/sha256 全失配 → fixtures 标 `-text`、schema DB 标 `binary`；
4. 内嵌 eBPF 二进制必须在**任何编译型门之前**构建（clippy 先于 tests 编译 lib）
   → 门禁新增 unnumbered **preflight**；
5. 两个门禁假绿机制：clippy 只数 warning（deny lint 以 error 存在却全绿）；
   `set -o pipefail` + `grep -q` 的 SIGPIPE 把失败反转成 PASS → 全部修复，
   现在 `python`/shell 判定一律用 here-string。

---

## 4. 陷阱清单（全部实际踩过）

1. **不要把会写文件的评审 agent 放在同一工作树**：它们会创建探针文件、回写源文件。
   用独立 clone 或只给 Read/Grep。
2. **测试会改进程 cwd**（`server::web` 的 `RepoEntryGuard`）；生产代码里的
   `current_dir()` 读取可能与之竞态。需要 cwd 的规则改为可注入。
3. **hot WAL 的零写入**：对 causal/未知库即使只 open 后关闭也可能 checkpoint
   `-wal`；必须只读探针分类、拒绝即返回。
4. **未来版本可能只藏在 `schema_version` 表**（`user_version=0` + max=99）。
5. **foreign `application_id` 是正向反证**：表名撞车也不 adoption。
6. **不要编辑正在运行的 bash 脚本**：bash 按字节偏移继续读，注释片段会被执行。
7. **clean clone 的四个前提**：submodule、frontend 占位、fixture 字节、
   内嵌二进制 preflight（见 §3.4）。
8. **`normalized_args_json` 是 `Vec<NormalizedArg>` 的 JSON**（`[{"Command":"…"}]`）。
9. **replay 的旧 ablation 旗标是空操作**，已删除；不要加回未实现旗标。
10. **门禁判定不要用 `echo | grep -q`**：pipefail + SIGPIPE 会反转结果。
11. **本 harness 会拦截自身进程的文件类 syscall**（bpftrace 交叉验证同为 0）；
    E2E 文件/网络观测必须在非沙箱环境验证，别把“捕获不到”误判为代码缺陷。
12. **Claude Code 路径冻结**；前端本轮不动。

---

## 5. 未完成工作（按优先级，含验收标准）

> 前一项没有完成并有门之前，不要开始后一项。每项都遵守「加能力同时加门」。

### P0 — 正确性/安全债

**5.1 `security_signoff` 的角色硬编码**
- 事实：`authorized_roles` 已来自词表，但 `extract_grantor_role()`
  （`security_signoff.rs:449-459`）仍只认
  `security-performance-auditor`/`security-auditor` 字面量。
- 目标：从配置的 `authorized_roles` 生成匹配（含 acting 后缀），未知角色不猜。
- 验收：单测覆盖自定义角色（词表替换后仍生效）；现有 8 个 signoff 测试不变。

**5.2 隐私残余**
- 现状：只脱敏 HTTP 头；请求/响应 body、工具参数、stdio 明文不脱敏。
- 目标：至少 (a) stdio 事件在输出前的凭据形状脱敏（复用 `export::genai::redact`）；
  (b) 在 `capabilities.json`/威胁模型里把残余写死。
- 验收：新单测 + `SECURITY.md` 同步；raw 捕获仍是字节保真。

**5.3 CI runner 与依赖审计**
- 现状：`.gitlab-ci.yml` 已就绪（含 submodule/libssl/libzstd），但无 runner；
  `cargo audit`/`npm audit` 未接入。
- 目标：接 runner；加依赖审计门（允许有理由的预算）；更新 OpenSSF 自评状态。
- 验收：CI 跑通 `./scripts/verify.sh`；审计门有预算与理由。

### P1 — 能力完整

**5.4 `NETWORK_CONNECT`/`NETWORK_SEND` 生产者**
- 事实：Rust 侧已路由+分类（`connect`/`sendto`），但无生产者；3 类幽灵事件只剩它们。
- 设计：`sys_enter_connect`/`sys_enter_sendto`；AF_INET 解析 `sockaddr_in`
  （`bpf_ntohs` + 4 字节地址 → 点分十进制）；AF_INET6 可先显式跳过并在事件标
  `unsupported_family`（不伪造）。事件 schema 新增 `net_op`（host/port/family）。
- 验收：verifier 真实加载；JSON 契约测试；routing/分类测试；非沙箱环境 E2E
  脚本（`scripts/spikes/`）；噪声预算（当前无网络事件体积基线，需先测）。

**5.5 `ActionCandidateType::FileRename`**
- 现状：rename 归类为 `FileOverwrite`，动作资源取源路径；目标路径只存在于事件。
- 目标：加类型（DB 值、前端类型、分类器、测试、迁移说明），或把“无 rename 类型”
  写进 ADR 并长期接受。二选一，不许悬空。

### P2 — 架构统一（先审计后动手）

**5.6 `AGENT_COMMS` 双副本**
- `signals/signal_snapshot.rs:231` 私有副本 vs `identity/union_find.rs:32` 公共常量。
- 目标：一个真源；加门禁止再出现第二份。

**5.7 `MatchLevel` 双份 + 序数约定**
- `types/evidence_types.rs:166`（L1Exact/L2Normalized/L3Containment/L4Basename/L5Substring）
  与 `evidence/correlator_legacy.rs:115`（L1Exact/L2Basename/L3Host/L4Argv0/L5Substring）
  语义不同，序列化也不同。被 `graph_pattern_matcher`/`root_cause`/`evidence_record`
  使用。**必须先设计映射与数据迁移**，不可直接删。

**5.8 `graph/pattern_matcher.rs` 的 19 处 `required_edge_types`**
- 补生产者，或显式标 `unbacked`（`CapabilityUse` 在 `graph/` 全目录零构造）。
- 目标：每条 required edge 要么有生产者测试，要么 `unbacked` 清单进 capabilities。

**5.9 `routing.rs` 的 authority 硬编码**
- 旧交接提到 `routing.rs:239`；当前 grep 未找到字面量，**先重新取证**再决定是否已消除。

### P3 — 前端（需用户解冻）

**5.10 `api.getDiff`**
- `/api/runs/:id/diff` 已退役；`frontend/lib/api.ts::getDiff` 无调用者。
- 目标：删除该方法（一行）；前端 type-check/lint 通过。

**5.11 `/snapshots` 契约**
- 后端已返回 `GraphSnapshot[]`（`{snapshot_id,timestamp_ns,nodes,edges,metrics}`）；
  用真实 run 做一次浏览器验证（live 帧推进 + 回放时间轴）。

### 刻意不做

- **P3-b 外部内容种子**：前置条件（可靠指令门 + 负例语料）未满足，继续停用；
  计划见 `docs/superpowers/archive/plans/2026-10-18-p3b-external-content-seed.md`。
- 原始 capture 的通用 body 脱敏：破坏字节保真，出口脱敏已覆盖头；残余写进威胁模型。

---

## 6. 授权与约束（用户已明确）

| 项 | 结论 |
|---|---|
| 自主性 | 全权交给 AI，不需逐步确认；以顶级开源项目标准为准 |
| commit | **未提交**；commit message 由用户亲自定稿（中文、无 conventional 前缀、不提 agentsight）；大任务才提交 |
| 前端 | 本轮冻结；只有 §5.10/5.11 需要用户先解冻 |
| 工作树隔离 | **不可用**（Agent worktree 找不到 Cargo.toml/网络不稳），一律主会话内联执行 |
| 预算 | clippy/ignored//home/allow **只许降不许升** |
| sudo | eBPF 验证：`printf '%s\n' 1 \| sudo -S -p '' <cmd>` |
| 长任务 | 写成 `.sh` 再执行（`/tmp` 或 `scripts/`） |
| 不做大爆炸 | 纯删除可即做；合并/抽象先设计；与已有门协调 |

---

## 7. 常用命令

```bash
# 门禁 / 测试
./scripts/verify.sh
./scripts/check_fresh_clone.sh
cd collector && cargo test
cd collector && cargo clippy --all-targets --message-format=short

# eBPF
cd bpftools && make test
cd bpftools && make .output/bin/{tls_capture,stdiocap,process}
printf '%s\n' 1 | sudo -S -p '' timeout 3 .output/bin/process   # verifier/attach

# 真实旧库 adoption sweep
python3 scripts/schema/sweep_legacy_dbs.py

# GenAI 导出（真实库）
cd collector && cargo run --quiet --bin mtagentlens -- \
  export-genai --db ../env/runs/<id>/collector/collector.responsibility.db \
  --out /tmp/genai.jsonl
```

---

## 8. 提交建议（仅供参考；message 由用户定稿）

工作树未提交，逻辑上可分成四组，便于审阅与 `git bisect`：

1. **单 schema**：`collector/src/responsibility/persistence/*`、`collector/tests/schema_upgrade.rs`、
   `collector/tests/fixtures/schema/live-v1.db`（已 staged）、`scripts/schema/*`、
   `docs/adr/0005*`、ADR 0004 的处置引用、`docs/evaluation/schema-adoption-sweep.json`。
2. **P4 通用化**：`bpftools/process/*`（op/filepath2/process_json.h、测试）、
   `collector/patterns/scenarios.toml` + `scenario_vocabulary.rs`、
   `collector/src/framework/analyzers/{handoff_injection,multi_agent_anomaly,security_signoff}.rs`、
   `classification/rules.rs`（删死表）、`graph_builder.rs`、`bin/replay.rs` 的 live-only 重写、
   已删除的 9 个因果模块与 `bpftools/fileaccess/`。
3. **P5 出口与治理**：`collector/src/export/`、`raw_secret_redactor.rs`、`main.rs`（bind/export 子命令）、
   `server/web.rs`（CORS/快照路由）、`persistence/schema.rs`（0600）、
   `SECURITY.md`、`CONTRIBUTING.md`、`CHANGELOG.md`、`docs/security/*`、ADR 0008。
4. **可复现性与门禁**：`scripts/verify.sh`、`scripts/check_fresh_clone.sh`、`.gitattributes`、
   `frontend/out/.gitkeep` + `frontend/.gitignore`、`Makefile`（verify/clean）、
   `.gitlab-ci.yml`、ADR 0006/0007 与相关文档。

## 9. 给新会话的三句话

1. **先跑 `./scripts/verify.sh` 与 `cargo test`**：绿，才是当前真实状态。
2. **先读 §5 的 P0**：`security_signoff` 角色硬编码与隐私残余是已知的真实缺陷，
   比新增网络生产者更优先。
3. **每加一条能力，同时加一条能证明它存在的门；每删一条能力，同时把预算降下来。**
   拿不到一手证据时，宁可停下取证，也不要写“看起来对”的代码。
