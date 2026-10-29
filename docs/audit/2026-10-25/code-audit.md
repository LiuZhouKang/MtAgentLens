# MtAgentLens 代码与工程质量独立审计（2026-10-25）

审计者：code-audit（独立只读审计）· 工作区 `/home/lzk/MtAgentLens` · HEAD `610f726`
审计边界：**不改任何仓库文件**、不 commit/push、不审计实验数字（另有专人）。审计后 `git status --porcelain | wc -l` 仍为 **1286**，与审计前一致；所有探针只写 `/tmp`。

**一句话结论**：核心判定内核、静默失败防护、路径/鉴权/DB 权限、反作弊式门禁设计都属于专业水准，`verify.sh` 17/17（EXIT=0）是**真实**的；但存在 **1 个交付级阻断**（论文核心源码与 claims 清单不在版本库，HEAD 不可构建、第三方不可复现）、**4 个必须修**（subagent 边界 agent id 陈旧且前端直接展示、门禁中存在"0 测试也通过"的空检查、安全/构建自评高估、证据索引数字漂移）与 **12 项债务**。

统计：**P0 = 1 / P1 = 4 / P2 = 12**（共 17 条）。另核实 12 项"文档说已修且我实测为真"，见 §2。

---

## 0. 门禁真实性（先给结论，避免"文档自述"）

`/tmp/verify_gate_2.log`（19:14 完成）实测：**17/17 全 PASS，末行 `EXIT=0`**，不是"部分通过"。逐项与关键计数：

| 门 | 结果 | 我核到的事实 |
|---|---|---|
| preflight | PASS | 嵌入用 eBPF 二进制已构建且非 ASAN（`ldd` 检查真实） |
| 1 rustfmt | PASS | `cargo fmt -- --check` 干净 |
| 2 clippy | PASS | 99（budget 99）；**且先 `touch` 全部源文件**、`grep '^error'` 单独判失败——防"缓存低报"和"编译错误被当预算通过"，设计正确 |
| 3 tests | PASS | 995 passed / **3 ignored**（budget 3）；3 个 `#[ignore]` **都有理由**且理由成立（见下）；源级扫描不许裸 `#[ignore]` |
| 4 eBPF | PASS | `make -C bpftools test` 仅用户态 C 单测，**不加载内核**（见 P1-3） |
| 5 reachability | PASS | 162 个 `.rs` 全部可达；脚本逻辑真实（非空转），但**不校验文件数>0** |
| 6 build repro | PASS | 只校验 6 个硬编码路径被 git 跟踪，**不能发现新增未跟踪源码**（见 P1-2） |
| 7 licensing | PASS | SPDX 扫描真实（注意零文件时会静默通过） |
| 8 home path | PASS | 0 个 `/home/lzk` |
| 9 allow budget | PASS | 49/49 |
| 10 wire+baseline | PASS | 我另跑 `verify_claims.py`：**56/56**；fixture 逐文件 size+sha256 校验，非"只查存在" |
| 11 wire oracle | PASS | 真实跑 `wire_corpus` 等，但**不校验跑了几个测试**（见 P1-2） |
| 12 decode budget | PASS | 同上 |
| 13 compact_db | PASS | 同上（unittest 找不到测试也会 exit 0） |
| 14 audit budget | PASS | **全脚本最强守卫**：断言测试文件存在 + `Ran >= 28` 才通过 |
| 15 compare/annotator | PASS | 1092 passed + 16 subtests（pytest 0 用例会 exit 5 → 不可能空过） |
| 16 env | PASS | 365 passed |
| 17 GT/scenario | PASS | 两个校验器；空数据集会失败 |

3 个 ignored 测试的实测理由（全部成立，不是藏债）：
- `framework/runners/process.rs:187` `requires real binary and may need sudo privileges`
- `framework/runners/ssl.rs:168` 同上（须真实二进制 + 特权）
- `responsibility/outofband/openclaw.rs:409` `requires a live OpenClaw installation`
（`collector/tests/` 下没有 ignored 测试；不存在"ignored 数用预算糊住"。）

---

## 1. 问题清单

> 严重度：**P0** 阻断交付/正确性；**P1** 返修前必须修；**P2** 债务。
> "现状"列严格区分：**仍坏**（实测/代码证实）/ **文档高估**（文档说已解决但证据不支持）/ **已修**（实测为真，仅列在 §2）。

| ID | 严重度 | 问题 | 证据（file:line / 命令输出） | 现状 | 建议最小修复 | 成本 |
|---|---|---|---|---|---|---|
| P0-1 | P0 | **论文核心源码与新交付物不在版本库**：HEAD 无法构建，第三方无法复现；机器/误删即丢失 | `git ls-tree -r HEAD --name-only -- collector/build.rs collector/src/responsibility/classification/final_decision.rs docs/paper/claims.json \| wc -l` → **0**；HEAD 跟踪 2622 文件，工作区另有 **394 个未跟踪**；`checkpoint.rs:26` `include!(OUT_DIR/analysis_sources.rs)` 由**未跟踪**的 `collector/build.rs` 生成 → HEAD 构建必断；`compare/tests` HEAD 33 文件 vs 盘上 142、`env/tests` 23 vs 92、`env/scenarios` 984 vs 2149；`verify.sh:222-241` 只查 6 个硬编码路径，所以门禁假绿 | 仍坏（用户禁 commit，属策略阻断） | ①先 `git add -A` + review + 提交（**需用户授权**，本审计不执行）；②过渡期：`sha256sum` 清单 + 异地备份；③`verify.sh` gate 6 增加"编译必需文件必须在 `git ls-files` 中"（用 build.rs 的源清单交集），或 CI 跑 `check_fresh_clone.sh --commit HEAD` | S（策略）/ M（门禁） |
| P1-1 | P1 | **subagent 边界记录的 `parent_agent`/`child_agent` 是陈旧 id**：在向下传播中途用当时尚未完整的组件算 id，之后同组件再加入 tool 子进程 → 记录的 agent id 与最终 `agents[]` 不一致；DB `subagent_boundaries.parent_agent_id` 指向不存在的 agent，前端家族树直接画出幽灵父节点 | `collector/src/responsibility/identity/union_find.rs:552-553,607-608`（提前 `derive_agent_id`）vs `:779`（最终 id）；探针实测见 §4.1：`boundary parent=agent-c23117263a973ece`，最终 agent 为 `agent-ef43c099cd113db9`（= `[pid100,pid300]`）→ **parent_exists=false**；前端消费：`frontend/lib/identity-lineage.ts:137` `tree.get(b.parent_agent_id) ?? { agentId: b.parent_agent_id }`、`frontend/lib/db-import.ts:825` | 仍坏（用运行证据证实，测试未覆盖） | 把 Step 3 里的 `SubagentBoundary` 先只记录 `(parent_process, child_process, reason)`，在 **Step 4b 之后**统一 `derive_agent_id(&components[...])` 重算（照抄 4b 对 delegation 的做法）；回归测试断言每条 boundary 的 parent/child agent id 都 ∈ `agents[].agent_id` | S |
| P1-2 | P1 | **门禁存在"空检查/空目录静默通过"一类**：gate 11/12/13 不看执行了多少测试；gate 5 在 `collector/src` 为空时 `orph=[]` → PASS；gate 6 只查 6 个硬编码路径；gate 7/9 在 find/grep 命中 0 文件时静默 PASS。违反 verify.sh 自己写的规则 1（"guard 必须先断言目标存在且非空"） | 实测：`cd collector && cargo test --quiet --test wire_corpus -- --exact no_such_test_name` → `running 0 tests` / `test result: ok. 0 passed` / **EXIT=0**；`verify.sh:301-330` 无计数断言；`verify.sh:186-220` 无 `allrs` 非空断言；`verify.sh:222-241` hardcoded 6 路径 | 仍坏（当前恰好没有假绿，但守卫可被"删除测试体"绕过） | gate 11/12/13 解析 `test result: ok. N passed` 并断言 `N >= 已知基线`（如 wire 8、overhead 1、compact_db 用例数）；gate 5 断言 `len(allrs) > 100`；gate 6 用 build.rs 源清单校验；把 gate 14 的写法复制过去即可 | S |
| P1-3 | P1 | **OpenSSF 自评（tracked 文档）在安全/构建/第三方可测三项上高估**：①"eBPF verifier 真实加载检查"不在任何门禁（gate 4 只有用户态 C 单测；全仓库无 first-party verifier 加载脚本/测试）②"clean clone 实测 14/14"与现门禁 17 道不符 ③"全部测试在仓库内"对 HEAD 不成立（对比 tests/scenarios 数字见 P0-1） | `docs/security/openSSF-self-assessment.md:43`（#17）、`:45`（#19）、`:49`（#23）；`bpftools/Makefile:132`（`test:` 只跑 `$(BIN_DIR)/$$t`）；`grep -rn 'bpf_object__load\|BPF_PROG_LOAD' --include=*.c --include=*.sh --include=*.py` 仅命中 vendored `libbpf/bpftool/vmlinux/.output` | 文档高估 | 把 #23 改为"人工/实验实测已加载"并移出门禁证据栏（或补一个 `sudo` 真实加载脚本并明示"需特权、不在 verify.sh"）；#17 改成 17/17 且注明候选模式 vs `--commit` 模式；#19 注明"需先提交/快照，HEAD 不含本轮新增测试" | S |
| P1-4 | P1 | **证据索引数字漂移**：EVIDENCE-INDEX 写 claims 19 条 / `verify_claims` 19/19，实际 56/56；HANDOFF §1.1 的测试计数（compare 752、env 348）与 19:14 实测（1092、365）不符 | `docs/evaluation/EVIDENCE-INDEX.md:20`"19 条 / 19/19"；实测 `python3 scripts/evaluation/verify_claims.py --manifest docs/paper/claims.json \| tail -1` → **56/56 claims hold**；`/tmp/verify_gate_2.log`（1092+16 / 365）vs `docs/HANDOFF-NEXT-SESSION.md` §1.1 | 文档高估 | 修数字并注明复核时间；把"证据索引里引用的路径必须存在于 `git ls-files`"加进文档 CI（否则引用未跟踪的 `final_decision.rs` 本身仍是纸面证据） | S |
| P2-01 | P2 | `final_decision.rs` 文件头注释的优先级列表与实现/论文口径不完全一致：头注释把"Uncertain/调用失败"单列为 → Uncertain，未写"已引用 Unsafe 优先于 Uncertain/Failed"；而 `docs/paper/decision-procedure.md` §2 规则 2 与代码一致（实测 Unsafe+Uncertain → Unsafe/Supporting；Unsafe+Failed 同） | `collector/src/responsibility/classification/final_decision.rs:12-21` vs `:202-214`；探针输出 `unsafe+uncertain => Unsafe / Supporting`、`unsafe+failed => Unsafe / Supporting`；`final_decision.rs` 12 个测试**没有**混合观测用例 | 仍坏（注释误导，行为符合论文文档） | 头注释改写为与 `decision-procedure.md` §2 一致的 7 条；补 2 个混合观测回归测试（Unsafe+Uncertain、Unsafe+Failed） | S |
| P2-02 | P2 | capability envelope 的写路径仅做**纯字符串前缀**比较：用户任务里点名的文件路径会成为前缀，授权同前缀兄弟路径；`/dev/` 前缀整体放行（含 `/dev/shm`，可做跨进程外泄） | `collector/src/responsibility/provenance/envelope.rs:46-84`（`extract_paths` 结果直接进 `write_prefixes`）、`:177-222`（`starts_with(prefix)`）、`:179-181`（`path.starts_with("/dev/")` → Allowed） | 仍坏（影响是"少报 Expansion"，不是纵容攻击；但会给 Observer 错误的授权前提） | 对非 `/` 结尾前缀改为"精确相等或加 `/` 后前缀"；`/dev/` 白名单收窄到 `/dev/null\|/dev/stdout\|/dev/stderr\|/dev/tty` | S |
| P2-03 | P2 | `is_descriptor_indirection` 回落分支 `return !path.is_empty()` 恒真（能走到该分支时 path 必非空），逻辑噪声；同时把任意 `/proc/<任意>/fd/*` 当"已授权" | `collector/src/responsibility/provenance/envelope.rs:229-251`（`let ... else { ... return !path.is_empty(); }`） | 仍坏（语义噪声，非崩溃） | 改为 `return rest.is_some_and(\|r\| !r.is_empty())`，并限 `/proc/self/fd` 或已观测 pid | S |
| P2-04 | P2 | `runs` 表**没有任何 FK 引用**（28 张表 `PRAGMA foreign_key_list` → 0 指向 runs），且 `UPDATE runs ... WHERE run_id` **不检查影响行数**：若 `RunStart` 在通道满时被 `try_send` 丢弃，该 run 在 DB 里无声消失（final_decision/ended_at 全 NULL），`write_failure_count` 不增加 | 探针实测：`tables with FK -> runs: 0 of 28`；`collector/src/responsibility/persistence/writer.rs:483`（RunEnd）、`:510`（FinalVerdict）；`runtime.rs:426` RunStart 用可丢的 `write()` | 仍坏（概率低：仅初始化瞬间通道满；但后果是"样本从分母消失且无标记"） | 两处 `UPDATE` 取 `rows_affected`，`!= 1` 时计 `write_failure`；v4 迁移给证据表补 `FOREIGN KEY(run_id) REFERENCES runs(run_id)`（或至少对 runs 行缺失兜底 INSERT） | S/M |
| P2-05 | P2 | **源码级守卫测试用 `contains` 子串断言**：注释/死代码/字符串常量即可满足，不能证明接线；与"行为测试"混在同一门禁里会给人一种更强的错觉 | `collector/tests/silent_failure_contract.rs:27-47`（`runtime.contains("DIAG_...")`）、`:80-96`、`:120-140` 等；全文件都是读源码 + `contains` | 仍坏（弱断言类，非空转） | 关键 3 条改为行为断言（跑一次 `process()` 后读 `[diagnostics:json]` / capture_quality）；保留源级扫描只做防回退 | M |
| P2-06 | P2 | 预算全部顶在高水位，且 clippy 计数被跨 target 误报稀释：99 = lib 生产 12 + 其余主要来自 test/all-target 编译；其中 52 条 `never used/constructed`，含至少 1 个**真死码** `routing::classify_event_public`（全仓库 0 调用） | `CLIPPY_BUDGET=99` 实测 99；`cd collector && cargo clippy --lib --message-format=short` → **12 warnings**；`/tmp/verify_clippy.log` 中 52 条 never-used/constructed；`collector/src/responsibility/runtime/routing.rs:455` 零调用；ALLOW 49/49、IGNORED 3/3 | 仍坏（数字不再"只降"而是顶格；真实债务与误报混在一个数里） | 删除真死码（`classify_event_public`、`accountability_evidence.rs` 整块等），把 lib 目标单独设预算（如 lib ≤ 12 且只降），all-targets 预算只作防回退 | S |
| P2-07 | P2 | 其它文档漂移：HANDOFF §1.1 计数过期（compare 752→1092、env 348→365）；openSSF 生成日期 2026-10-18 未随门禁更新；`CLAUDE.md`/`AGENTS.md` 被 `.gitignore` 忽略（第三方的流程入口缺失，属有意但应在 README 说明） | `docs/HANDOFF-NEXT-SESSION.md` §1.1；`docs/security/openSSF-self-assessment.md:1-6`；`.gitignore`"AI agent instruction files (local only)" | 文档高估 | 修 §1.1 计数并加"以 verify.sh 输出为准"；README 注明流程文档仅本地 | S |
| P2-08 | P2 | 非回环 bind 时**主动关闭** Host allow-list 且无任何鉴权：`MTAGENTLENS_BIND=0.0.0.0` 后，同网段任何人可读解密后的 TLS 明文（`/api/live/events`、`/api/runs/<id>/events`）并下载 run DB；只有一行 stderr 提示 | `collector/src/server/web.rs:390-404`（bind≠loopback → `return true`）、`:2127-2131`（测试只覆盖 loopback 分支）；默认 `127.0.0.1` 是正确的 | 仍坏（默认安全，边界条件不安全） | 非回环时要求显式 token（`MTAGENTLENS_AUTH_TOKEN`）或要求 `--allow-remote` 二次确认；至少文档与 stderr 明示"无鉴权、等于公开明文" | M |
| P2-09 | P2 | 生产 finalize 路径每次派生 envelope 都 `eprintln!("[envelope] workspace=...")`：把本机绝对路径写进 run 日志（也是测量里 `log_bytes_per_second` 的噪声来源） | `collector/src/responsibility/provenance/envelope.rs:69-72`；调用点 `runtime/runtime.rs:900`（每 agent 一次） | 仍坏 | 降级为 `log::debug!` 或只在 `MTAGENTLENS_DEBUG` 打开时打印 | S |
| P2-10 | P2 | 未接线/死导入：`main.rs` 顶部 `#[allow(unused_imports)]` 掩盖了未使用的分析器导入；`FileAccessAuditConfig`、`doctors::has_failure`、`accountability_evidence` 整块等 52 个 never-used | `collector/src/main.rs:19-21`；`/tmp/verify_clippy.log:7-56` | 仍坏 | 删导入与死模块；`--all-targets` 里 lib 目标保留 `dead_code` 为 warn（不 allow） | S |
| P2-11 | P2 | claims 数值校验在 clean clone 中会 **pytest.skip**（artifact_root=`local/workpackages/...` 被 gitignore），文档把它当作门禁证据但未披露"第三方=skip" | `docs/paper/claims.json` `artifact_root=local/workpackages/2026-10-25-rc-fix4`；`compare/tests/test_paper_claims_artifact.py:44-53`（`pytest.skip`）；`.gitignore` `local/` | 文档高估（本地真、异地 skip） | 文档注明边界；把参与 claims 的 metrics.json 纳入版本库（脱敏后）或放进 release 资产 | S |
| P2-12 | P2 | eBPF 语义混用：`report_user_read_failed` 把**用户态指针**强转后塞进 `e->syscall_ret`（该字段在其它事件表示系统调用返回值），导出后是无意义地址 | `bpftools/process/process.bpf.c:178` `e->syscall_ret = (long long)(unsigned long long)src;` | 仍坏 | 改用专用字段（`capture_loss_reason`+`file_op.flags` 已有），或置 0 并把地址放进 `full_command` 的诊断文本 | S |

---

## 2. 文档说已修 vs 我实测已修（给"专业、彻底"的部分记账）

以下不是文档自述，是本次**运行/阅读证据**核实的：

1. **17 门全绿是真的**（`/tmp/verify_gate_2.log` 末行 `EXIT=0`），clippy 先 touch 源文件、单判 `^error`、ignored 测试要理由、审计门断言 `Ran>=28`——这些守卫质量高于多数工程仓。
2. **静默失败真的被治理了**（不是只改注释）：`PersistenceWriter` 同时统计 `lost_count / closed_count / write_failures`（`persistence/writer.rs:298-368`），runtime 把它们写进 `capture_quality.analysis`（`runtime.rs:2011-2020`），`quality_status` 任一非零即 `degraded`，且 Safe 会被降为 Uncertain（`replay/checkpoint.rs:170-260`）；finalize 结束打印"flushed cleanly / DEGRADED"。`tests/silent_failure_contract.rs` 还锁了接线。
3. **单一裁决内核是真的**：live finalize 与 replay 都走 `AnalysisCheckpoint::evaluate()`（`runtime.rs:2158`、`replay/executor.rs:125`）；replay 强制要求版本化 checkpoint，且 `build.rs` 把 **`src/**/*.rs` 全量 + Cargo.toml/lock** 做进指纹（不是手写清单），换实现即拒绝重放（`checkpoint.rs:26,69-88`）。这一条比文档写的更硬。
4. **eBPF 历史 ignore 坑已修**：`bpftools/tls_capture/tls_capture.bpf.c`、`process.bpf.c`、`stdiocap.bpf.c` 都在 `git ls-files` 中；`verify.sh:222-241` 对关键路径有跟踪断言（范围偏窄见 P1-2）。
5. **eBPF 边界处理是认真写的**：`read_user_path` 处理 `-ENAMETOOLONG`、按页边界退化为常量读、最后 1 字节兜底（`process.bpf.c:186-215`）；`full_command` 读入后做有界循环 + 显式 NUL（`:404-419`）；环形缓冲失败都有计数/`discard`。
6. **Rust 侧无 `unsafe`**：`collector/src` 中 `unsafe {` / `unsafe fn` / `unsafe impl` = **0**（99 处 `unsafe` 字面量全是 `"unsafe"` 字符串、`unsafe_grid`、`threshold_unsafe` 等标识符），因此不存在"缺 SAFETY 注释"的问题；`extern "C"` 也为 0。
7. **路径遍历/静态资源逃逸已修且有测试**：`server/assets.rs:61-76` 拒绝 `..`、`.`、含 `\` 段（注释记录了曾经 `GET /../../etc/passwd` 逃逸）；`server/web.rs:664-678` 对 `/api/runs/<id>` 任何段为 `..`/`.` 直接不匹配。
8. **DNS rebinding / Host 校验已修且 live SSE 也覆盖**（`web.rs:160-215,281-300,389-404`；测试 `:2127`）。
9. **DB 导出真的只含目标 run**：`export_run_snapshot` 用 SQLite backup API 冻结快照 → 按 `run_id` 删其它行 → `foreign_key_check` → VACUUM（`web.rs:1630-1712`）；新建库 `PRAGMA foreign_keys=ON`（`schema.rs:1291`），并有跨 run FK 拒绝测试（`schema.rs:610`）。
10. **凭据与产物权限到位**：DB/WAL 兜底 `0600` 且失败会报（`schema.rs:1364-1420`）；`.gitignore` 覆盖 `.env`、`env/config/vendors/*`、`observer.toml`；`*.db` 仅放行 schema fixture。
11. **测试体量与断言质量**：1138 个测试函数，`assert!(x.is_ok())` 这类弱断言仅 **4** 处；final_decision 12 例覆盖硬证据优先、无引用 Unsafe、候选版本粒度、确定性；无 `TODO/FIXME/HACK/unimplemented!/todo!`（唯一 "XXX" 命中是文档里的 `\uXXXX`）。
12. **无"生产路径 Mutex 跨 await"**：clippy 的 15 条 `MutexGuard held across await` **全部**位于 `web.rs` 测试模块（`#[cfg(test)]` 起于 `web.rs:2042`）；唯一 `unused Stream` 警告也在 runtime 测试（`:2808`）。

**文档高估（与"实测为真"分开列，便于返修时逐条改口）**：P1-3（verifier 加载/14 门/第三方可测）、P1-4（claims 19 vs 56、HANDOFF 计数）、P2-07/P2-11（clean clone skip 未披露）、P0-1（"仓库内可复现"与 HEAD 事实不符）。

---

## 3. 未提交大改风险（1286 项里的最危险 5 处）

构成：`702 M + 190 D + 394 ??`；staged 160 个删除。风险排序：

1. **论文核心模块整体未跟踪**（P0-1）：`final_decision.rs`、`provenance/{mod,envelope,labels,span_store}.rs`、`replay/checkpoint.rs`、`runtime/provenance_tracker.rs`、`classification/candidates.rs`、`claims/authorization.rs`、`collector/build.rs`（**没有它整个 crate 编译失败**）。这是"改了一半"的最危险形态——本地全绿，HEAD 全断。
2. **论文数字清单/证据索引未跟踪**：`docs/paper/claims.json`（56 条）、`docs/evaluation/EVIDENCE-INDEX.md`、`docs/HANDOFF-NEXT-SESSION.md`；`claims.json` 的 artifact_root 又指向 gitignored `local/` → 即使提交也不可第三方核对（P1-4/P2-11）。
3. **门禁用测试/场景未跟踪**：`compare/tests` +45 文件（HEAD 33→142）、`env/tests` +24（23→92）、`env/scenarios` +124（984→2149）。HEAD 的门禁覆盖面远小于当前报告所述。
4. **190 个 staged 删除**：`collector/src/auth/**`（TOTP/backup codes，HEAD 里 `lib.rs` 曾 `pub mod auth;`）与 `responsibility/server/**`（旧路由，现并入 `src/server/web.rs`）。编译通过说明没有悬空引用；但 auth 删除意味着**当前服务端完全无鉴权**（见 P2-08），若曾有文档/评审承诺"Web UI 鉴权"需核对。
5. **Cargo/前端清单一致性**：`cargo metadata --locked` **EXIT=0**（`Cargo.lock` 与 `Cargo.toml` 一致，不是"锁漂移"）；`frontend/package.json` 只改了 `dev` 脚本、`package-lock.json` 未改（无不一致）；`frontend/out/` 被 ignore 但 `rust-embed` 在**编译期**嵌入它——`head_asset_returns_headers_without_a_body`（`web.rs:2135-2142`）依赖 `/console.html`，因此"新 clone 直接 `cargo test`"会失败，必须先 `npm run build`（`check_fresh_clone.sh:176-177` 确实先 build，但 `verify.sh` 自己不保证）。

---

## 4. 关键问题的可复现证据

### 4.1 subagent 边界 agent id 陈旧（P1-1）

探针（不写仓库；用现成 `target/debug/deps/libmtagentlens-*.rlib` 链接）：

```rust
// /tmp/probe_identity3.rs — 场景：shell -> main(claude,TLS) -> sub(claude,TLS)，sub 先于 tool 进入 child_map
use std::collections::{HashMap, HashSet};
use mtagentlens::responsibility::signals::SignalGraph;
use mtagentlens::responsibility::signals::signal_snapshot::ProcessExecInfo;
use mtagentlens::responsibility::identity::union_find::run_authoritative_resolution_with_delegations;
use mtagentlens::responsibility::types::{ProcessId, TlsSessionId};
fn pid(id: u32, s: u64) -> ProcessId { ProcessId::new(id, s) }
fn main() {
    let (shell, main, sub, tool) = (pid(50,0), pid(100,1), pid(200,2), pid(300,3));
    let mut g = SignalGraph::new();
    g.observe_exec(main, shell, ProcessExecInfo{comm:"claude".into(),..Default::default()}, None, 1);
    g.observe_tls(main, TlsSessionId::from("sess-main"), 10);
    g.observe_exec(sub, main, ProcessExecInfo{comm:"claude".into(),..Default::default()}, None, 2);
    g.observe_tls(sub, TlsSessionId::from("sess-sub"), 20);
    g.observe_exec(tool, main, ProcessExecInfo{comm:"python3".into(),..Default::default()}, None, 3);
    let r = run_authoritative_resolution_with_delegations(&g.snapshot(), &HashMap::new(), &HashSet::new());
    for a in &r.agents { println!("agent {} = {:?}", a.agent_id, a.process_ids.iter().map(|p|p.pid).collect::<Vec<_>>()); }
    for b in &r.subagent_boundaries { println!("boundary parent={} child={} p={} c={}", b.parent_agent, b.child_agent, b.parent_process.pid, b.child_process.pid); }
}
```

```bash
export PATH="$HOME/.cargo/bin:$PATH"
cd collector
RLIB=$(ls -t target/debug/deps/libmtagentlens-*.rlib | head -1)
rustc --edition 2024 /tmp/probe_identity3.rs -L dependency=target/debug/deps --extern mtagentlens="$RLIB" -o /tmp/probe_identity3
/tmp/probe_identity3
```

实测输出：

```
agent agent-ef43c099cd113db9 = [100, 300]
agent agent-a8220833680747a3 = [200, 400]
boundary parent=agent-c23117263a973ece child=agent-a8220833680747a3 p=100 c=200
```

`parent=agent-c23117263a973ece` 不在 `agents[]`（它是 `derive_agent_id([100])` 的中间值）；前端 `identity-lineage.ts:137` 会用这个 id 造父节点 → 家族树出现幽灵父 agent。现有测试 `union_find.rs:1503 test_subagent_separate_from_main` 只断言 `parent_process/child_process`，不断言 agent id 一致，所以一直绿。

### 4.2 门禁"0 测试也通过"（P1-2）

```bash
export PATH="$HOME/.cargo/bin:$PATH"; cd collector
flock /tmp/mtagentlens-cargo.lock cargo test --quiet --test wire_corpus -- --exact no_such_test_name
# running 0 tests
# test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 8 filtered out
# EXIT=0   ← gate 11 的写法就是只看 exit code
```

对照 `verify.sh:349-356`（gate 14）就写了正确姿势：断言 `Ran >= 28`。把同一写法套到 11/12/13 即可。

### 4.3 runs 表隔离缺口（P2-04）

```
tables with FK -> runs: 0 of 28
```

（探针：`open_responsibility_db` 建库后对 28 张表逐表 `PRAGMA foreign_key_list`，无一条 `->runs(...)`；v4 的复合外键只在证据表之间建立，见 `schema.rs:892-895`。）

### 4.4 混合观测优先级（P2-01）

```
unsafe+uncertain => Unsafe / Supporting
unsafe+failed    => Unsafe / Supporting
single cited     => Unsafe / Supporting
```

与 `docs/paper/decision-procedure.md` §2 规则 2/§3 一致；与 `final_decision.rs:12-21` 头注释的隐含顺序不一致，且无回归测试。

---

## 5. 如果只能修 5 件事（按性价比排序）

1. **P0-1 让交付物进版本控制**（其余一切的前提）：先 `git add -A` → 人工 review diff → 提交（**需用户授权**；审计员不执行）。否则论文"代码可得/可复现"与 openSSF #17/#19 都无法成立，且一次 `git clean -fdx` 就抹掉论文核心实现。过渡动作：`sha256sum` 全量清单 + 异地备份，并把 `check_fresh_clone.sh --commit` 接入 CI。
2. **P1-1 修 subagent 边界的陈旧 agent id**：改成 Step 4b 之后统一 `derive_agent_id` 重算；加"boundary 的 agent id 必须 ∈ agents[]"回归测试。**前端家族树与 DB 审计直接可见的错误**，改动 ~20 行。
3. **P1-2 补门禁非空断言**：gate 11/12/13 断言 passed 数、gate 5 断言 `allrs>100`、gate 6 用 build.rs 源清单替代 6 个硬编码路径。这是"门禁可信度"的地基，改动 < 30 行，且沿用 gate 14 已验证的模板。
4. **P1-3 + P1-4 把对外文档改成可核验口径**：openSSF #23 改成"人工实测、不在门禁"、#17 写 17/17 并标注候选/固定提交模式、#19 标注 HEAD 不含新增测试；EVIDENCE-INDEX claims 19→56、HANDOFF §1.1 计数按 19:14 门禁输出更新。返修材料里数字写错比缺数字更伤。
5. **P2-02 收窄 capability envelope 的路径前缀判定**（安全相关且便宜）：`/dev/` 白名单化、非目录前缀要求精确匹配；顺带删掉 P2-03 的恒真返回。它直接影响"授权 vs 越权"这一论文核心判据的输入质量。

> 第 6 名（若还有余量）：**P2-04** 给两处 `UPDATE runs` 加 `rows_affected` 校验 + 补 `FOREIGN KEY(run_id) REFERENCES runs(run_id)`——它是"样本无声从分母消失"的唯一残余静默路径；以及 **P2-05** 把 3 条源级 `contains` 守卫换成行为断言。

---

## 6. 审计方法与限制

- 只读：所有探针源码与产物在 `/tmp`（`probe_identity*.rs`、`probe_decision.rs`、`probe_schema.rs`），通过既有 `target/debug/deps/libmtagentlens-*.rlib` 链接，不触发仓库写入；审计前后 `git status` 均 1286 项。
- 未重跑完整门禁（与 Lead 冲突）；只跑窄命令：`verify_claims.py`、`cargo test --test wire_corpus`（单测过滤）、`cargo metadata --locked`、`cargo clippy --lib`（均用 `flock /tmp/mtagentlens-cargo.lock` 与门禁互斥）。
- 未覆盖：实验数字/统计口径（另有人）；前端 E2E 真浏览器行为；真实内核加载（需特权，未执行，这也正是 P1-3 指出的门禁空白）；1286 项 diff 的逐一 review（只按风险抽样）。
