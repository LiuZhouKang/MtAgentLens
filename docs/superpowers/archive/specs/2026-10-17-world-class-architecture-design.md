# MtAgentLens 顶层架构设计：契约内核 + 声明外环

- 日期：2026-10-17
- 状态：**待评审**（本文含需用户拍板的 8 项产品决策，见 §8）
- 方法：8 臂 SOTA 一手来源调研（OTel GenAI / OBI / eBPF 工程 / provenance 学界线 / 多 agent 归因 / 顶级平台可扩展性 / Rust 工程标准 / 开源治理）→ 6 轴代码库审计 → **3 个不同哲学提案 + 3 名独立评委打分**（三票各选一，1:1:1）→ 合成 → **引用对抗核验（60+ 条实测抽验）**
- 所有 `file:line` 与 URL 均经实测；未复核处显式标注"未能确定"（§9）

---

## 0. 三项必须先纠正的输入事实

**如果后续排期建在错前提上，这份设计会比没有设计更糟。**

### 0.1 ⛔ P0：**项目当前无法从全新 clone 构建**（已实测复现）

| 环节 | 实测 |
|---|---|
| `bpftools/.gitignore:4-6` | `/browsertrace`、`/stdiocap`、`/process` —— 这些规则**原本指向构建产物**，但产物早已改到 `.output/bin/`（`Makefile:2` `OUTPUT := .output`）⇒ 现在**命中了同名源码目录** |
| 版本控制实况 | `git ls-files bpftools/process` = **0**、`stdiocap` = **0**、`browsertrace` = **0**；`bpftools/.output` 亦为 0（`.gitignore:1`） |
| 编译期依赖 | `collector/src/framework/binary_extractor.rs:13-18` 用 `include_bytes!("../../../bpftools/.output/bin/{process,tls_capture,stdiocap}")` —— **编译期**嵌入 |
| 全新 clone 模拟（`git worktree add --detach HEAD`） | `bpftools/` 下**没有** `process/`、`stdiocap/`、`browsertrace/`、`.output/` |
| **远端比赛产物** | `competition/main` 同样缺这 3 个源码目录、且无 `.output` ⇒ **已提交的比赛仓库不可构建** |

⇒ 全新 clone 下 `cargo build` 必然在 `include_bytes!` 处编译失败；`make -C bpftools` 也无从构建三者。
**修复成本：删 3 行 gitignore + `git add` 三个目录（约 1 分钟）。** 反悔成本为零。**必须排在一切之前。**

### 0.2 工作树已领先于审计文档（不要把已做的事再计入待办）

- 13 个文件处于**已暂存删除**状态（`context_pollution.rs`、`credential_leak.rs`、`event_dedup.rs`、`shared_state_store.rs`、`linked_navigation.rs` 等），合计下架 5,092 行
- `main.rs:21-42` 已改用 `use mtagentlens::…`（不再 `mod framework;` 双编译）⇒ 审计的"根因 A：crate 被编译两遍"**在工作树已修**
- `cargo test --lib` 实测 **927 passed / 0 failed / 14 ignored**（EXIT=0）

### 0.3 "图模式检测零产出"的根因是**生产者缺失**，不是边命名

`graph_types.rs:36-48` 定义 12 条边；`graph_builder.rs` + `runtime.rs` 实际只构造
`IntentCausesAction` / `PolicyBoundaryCrossing` / `TaintPropagatesTo`。
而 `graph/pattern_matcher.rs` 有 **19 处 `required_edge_types`**，其中 `CapabilityUse` 出现 11 次
—— 而 `CapabilityUse` 在 `graph/` 全目录**零构造**（27 处命中全是 claim type / evidence type / 枚举定义）。

⇒ **合并同义边不能让任何模板复活。** 必须补生产者，或把这些模板显式标为 `unbacked` 并在能力清单里可见。
这条直接决定 §6 的 P3 验收标准。

---

## 1. 设计立场

> **先可信，再通用；内核自持，边界生成。**

唯一不可妥协的是：**内核观测到的事实不被静默损坏、静默丢弃、静默假装存在**。

因此第一阶段不是抽象，而是先把**字节通道**修对（`tls_capture.c:591-624` 把字节值写成码点的**非单射**转义；
`runners/common.rs:223` 的 `from_utf8_lossy`）与**响应链**修对（`main.rs:531` 的 `data.type=binary`
在解析器**之前**整条丢弃压缩响应）。

在这个干净的地基上：**内核**（事件契约 / 身份 / 因果 / 证据 / 持久化）用**自有词汇**，
并把"关于某个 agent CLI 的事实"全部赶出去；**外环**（profile / 检测规则 / provider 归一化器 / gen_ai 出口）
是**数据**，其形态由内核导出的类型约束。

凡是"靠人记住两边一起改"的地方，一律改造成 **单一真源 → 生成 → CI 断言 `git diff` 为空**。

### 明确拒绝（含理由）

| 拒绝 | 理由（一手来源已核） |
|---|---|
| 把内部模型建在 `gen_ai.*` 上 | 该命名空间 **100% 处于 development**（`semantic-conventions-genai/model/manifest.yaml:7-8` 声明 `stability: development`、`schema_url: …/gen-ai-dev/1.42.0-dev`；`registry.yaml` 内 `stability: stable` 出现 **0** 次），且已于 semconv **v1.42.0 迁出主仓**（`CHANGELOG.md:118`，#3696） |
| 给 `gen_ai.conversation.id` / `gen_ai.agent.id` 填推导值 | 规范**明文禁止**：`registry.yaml:439-441` "SHOULD NOT be used as a fallback value"；`:458-466` agent.id 仅限 **hosted** 资源；核验另发现更硬的 `gen_ai.main_agent.id`（`:485-496`）"**MUST NOT** generate … from an in-memory or process-local identifier" |
| 自造 `gen_ai.inferred` 之类 provenance 属性 | 无标准、无后端消费方。provenance 走 **instrumentation scope name**（OBI 的官方做法：`pkg/export/otel/traces.go:29` `const reporterName = "go.opentelemetry.io/obi"`） |
| OTLP 出口默认携带 content | 规范默认**不采集**内容（`docs/gen-ai/gen-ai-spans.md` "Capturing instructions, inputs, and outputs"；`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` 显式 opt-in） |
| 运行时插件 / 热重载 / 动态规则加载 | OTel Collector（ocb 编译期清单）、Vector/Telegraf（编译期注册）、Tetragon（机制编进内核、只有策略是数据）**无一采用** |
| workspace 大爆炸拆分 | Cargo 官方只承诺共享 lock/产物/元数据，**不承诺收紧 pub 可见性**；"拆 workspace ⇒ 顺带解决 pub 死代码"因果不成立 |
| protobuf/Arrow 代码生成 | 只有两处跨边界、无第三方消费方 |
| whole-system provenance（CamFlow 式）与执行期强制 | 前者开销 56%–100% 且与单 agent 威胁模型不符；后者破坏"零插桩、不改 agent"边界 |
| 自造规则 DSL / 引入 CEL | Sigma 与 Falco 各自承认 condition 语言是规范里最复杂最易变的部分；我们没有外部规则作者 |
| Fellegi-Sunter 概率化身份引擎 | 无标注匹配对来估 m/u，拍出的分数比现在"更像科学"而**更不可信**（只借"双阈值 + 中间档送复核"结构） |
| CNCF Graduation / OpenSSF Scorecard 徽章 | 前者必填项是组织级（≥2 组织维护者、vendor-neutral）；后者多项只支持 GitHub，而远端是比赛 GitLab |
| Conventional Commits / 自动 changelog | 与用户自定的中文无前缀 commit 风格**硬冲突**；且 CII 本就禁止把 git log 当 changelog |
| 制度化"release 二进制 strings 探测"死代码 | 依赖 LTO 具体行为，只对含字符串常量的死函数有效（本次会话恰好用它抓到一个死 pub 方法，但不足以制度化） |
| 让"全部文档都与代码一致" | 不可达目标，且会催生大面积**静默空转**的守卫（`responsibility_naming_guards.rs` 就是现成反面教材：它检 `src/accountability/**`，该目录已不存在，取不到即 `continue`，恒空转） |

---

## 2. 目标架构

**单向依赖（下层不知上层）**；内核里出现任何 agent 专名（`claude`、`openclaw`、`/usr/bin/bash`、某个 fixture 文件名）**即为设计缺陷**。

```
L0 采集层   bpftools/**                              无损字节 + drop 计数 + best-effort attach（机制）
L1 传输契约 collector/src/wire/                       唯一线格式 + 生成 C 头 / Rust 类型 / TS 类型
L2 归一化解码 collector/src/framework/normalize/       de-chunk → 解压 → provider 归一
L3 语义内核 collector/src/{framework/core,          自有词汇
             responsibility/{identity,causal,evidence,persistence,pipeline}}
L4 外环     profiles/ rules/                         数据驱动
             collector/src/{detect,profiles,export,server} + frontend/
```

### 2.1 保留（不动或仅小改）

| 路径 | 理由 |
|---|---|
| `framework/runners/*` | `agent.rs:63` 的 `select_all` 合并多源、`agent.rs:207-236` 已立"不阻塞不间断流"的钉子；`runners/fake.rs` 是离线规则回归的现成基座 |
| `framework/analyzers/connection_buffer.rs` | 连接键 + `awaiting_request` 状态机是正确的；**但必须接进所有链**（现只在 `main.rs:752` 的 ssl 子命令 ⇒ record/trace 无请求重组） |
| `sse_processor.rs` 的 finalize 策略表 + `evicted_with_content` 淘汰计数 | 全仓**唯一**把"静默丢流"变成可见数字的地方；改造 trait 时必须保留该接缝 |
| `analyzers/event.rs` 的 `HTTPEvent`/`SSEProcessorEvent` | 已是"归一化事件"的现成范式，其余分析器照它改，**不是**把它降级成 `Value` |
| `pipeline_contract.rs` 的 `StageContract{consumes,produces}` + `EventSource` 枚举（`:105-203`） | 提升为唯一 registry 的种子 |
| `persistence/schema.rs` 的 `(pid, start_time_ns)` 复合键、每表 `run_id`、`correlation_evidence` 与 `non_link_diagnostics` 分表、`phase1_*_id → phase2_*_id` refinements、`open_*_db_read_only` 的 WAL 快照读 | 审计已认定正确，**不得合并/简化掉** |
| `responsibility/outofband/`（499 行） | 已按 profile 驱动 + schema 版本校验写好，**只是未接线**（P1 接线） |
| `include/json_escape.h` + 其单测 | 共享化的正确范式，推广之 |
| `Collector::resolve_bash_path` + `tls_capture.c` 的 `SOFT`/best-effort 宏族 | best-effort 挂载的正确实现，泛化之 |

### 2.2 改造

| 路径 | 改造内容 |
|---|---|
| `framework/core/events.rs:11-17` | `Event{timestamp,source,pid,comm,data:Value}` → 加 `id:EventId(ULID)`、`specversion`；`data` 加 `payload` 双写过渡；**删两条语义不同的序列化路径**（`runners/ssl.rs:246` 与 `runners/process.rs:272` 的 derive 直序列化），只留唯一 `to_wire()` |
| `framework/analyzers/mod.rs:14-18` | 唯一 `Analyzer` trait → 拆 `Transform`（流→流，惰性，`fn on_event(&mut self, ev, out)`）+ `Sink`（`start/shutdown/flush`）；`Analyzer` 保留为兼容别名，逐模块迁移后删 |
| `main.rs:742-1510` 的 5 份手写链 + 35 处 `add_analyzer` | 收敛为 registry 驱动的单次组装 |
| `identity/{resolver.rs,union_find.rs}` | seed 由 `ProcessId` 改 `SeedKey{runtime, session?}`；`AGENT_COMMS` 双副本（`union_find.rs:32` + `signal_snapshot.rs:231`）删除，改 profile 驱动 |
| `graph/graph_types.rs:36-48` | 12 边收敛为语义互斥的唯一集（消 `AgentDelegatesTo`/`Delegation`、`TaintPropagatesTo`/`TaintPropagation`、`ContextFlowsFrom`/`ContextInheritance` 三对同义边），加 `source_kind`/`asserted_by`/`grade` |
| `persistence/schema.rs:384-402` | `if version < 1` 单门 → `PRAGMA user_version` + 版本递增迁移数组 |
| `intent_extractor.rs:803-835` | Pattern B 只认 `obj.function.name` → 引入"**定义上下文 vs 调用上下文**"判别（先常量表，后 profile 化） |
| `analyzers/common.rs:27-36` + `main.rs:531` | `binary` 判定改在**解码后**的表示上做，或删除该规则 |
| `bpftools/.gitignore` | 删 3 条陈旧规则，恢复三程序源码入库（§0.1） |

### 2.3 删除（纯删除先行，零行为变更）

| 目标 | 依据 |
|---|---|
| `responsibility/server/**`（14 文件，2,524 行） | `serve_request` **零调用者**。**注意**：`frontend/lib/api.ts:80/96` 钉在这棵树的 `/api/runs/:id/graph`、`/evidence` ⇒ **须先改前端**再删 |
| 已编译但 `::new()` 只出现在自身测试的分析器：`alert_enrichment.rs`、`conversation_flow.rs`、`latency.rs`、`observability_metrics.rs`、`role_hijack_contract.rs`、`agent_teams_role_hijack.rs`（约 3,587 行） | 见 FINDINGS §12：19 个 `impl Analyzer` 中 **6 个从未被构造**，而 `rustc` 对 `pub` 项不报 |
| `framework/analyzers/{pipeline_orchestrator.rs(606), analyzer_composition_contract.rs(746)}` | 全仓零外部引用（其生命周期/Drainable 概念先搬进 `sink.rs`） |
| `evidence/correlator_legacy.rs` | **注意**：`evidence/mod.rs:23-24` 把 legacy 的 `CorrelationEvidence`/`MatchLevel` 以**裸名**再导出 ⇒ 新实现只能叫 `NewEvidenceCorrelator`；顺带统一 `match_level` 的**三种序数约定**（`evidence_types.rs:164-168`、`argument_matcher.rs:33`、`graph_pattern_matcher.rs:69`） |
| `server/web.rs:1-2` 与 `bin/replay.rs:1-2` 的整文件 `#![allow(dead_code)]` | 改精确到项 + 理由注释 |
| `main.rs:1` 的 `// TEMP: allow removed…` 标记 | — |
| `collector/tests/responsibility_naming_guards.rs` | 恒空转（检已不存在的 `src/accountability/**`）⇒ 重写为新守卫 |

### 2.4 新建

```
collector/src/wire/                        L1 传输契约
  mod.rs        Event 信封、EventId(ULID)、EventSource、to_wire/from_wire
  gen.rs        由 contracts/ 生成 C 头与 TS 类型
  generated/    提交入库；CI 重跑断言 git diff --exit-code
collector/src/framework/normalize/         L2
  chunked.rs            字节级 de-chunk（RFC 9112 §7.1.3）
  content_encoding.rs   gzip/deflate/br/zstd 逆序解码 + decode_state
  provider/{openai,anthropic,deepseek,mcp}.rs
collector/src/catalog.rs                   唯一能力注册表（StageContract 折叠而来）
collector/src/framework/pipeline/          Transform/Sink + 由 catalog 驱动的唯一组装点
collector/src/profile/{schema,extract,resolve}.rs
collector/src/rules/{schema,selectors,engine}.rs
collector/src/detect/                      只消费内核导出类型
collector/src/export/genai.rs              可选 feature
profiles/{claude-code,openclaw}.toml
rules/{general,sandbox}/*.rule.toml
vocabularies/*.toml
docs/capabilities.json + .md               生成物（含 known_limitations）
collector/xtask/                           cargo xtask verify / gen / validate
.gitlab-ci.yml
docs/adr/ + adr-log 生成的索引
CHANGELOG.md + RELEASES.md
bpftools/include/common/{mtl_drop.h,mtl_ringbuf.h,event_fields.h}
bpftools/common/{bpf_util.c,bpf_util.h}    5 份 libbpf_print_fn / 信号处理 / poll 的收敛点
collector/tests/{coverage_contract,capability_manifest,schema_roundtrip,orphans_guard}.rs
```

**不拆 workspace crate**：改用 `pub(crate)` 逐模块收窄 + 一个 `pub mod api;` 门面。
（`lib.rs` 现把 `framework/responsibility/server` 全 `pub mod` re-export，这正是 `unreachable_pub` 今天不报的原因；收窄后它成为守卫。）

---

## 3. 数据模型

### 3.1 事件信封

```rust
struct Event {
  id: EventId,             // ULID：去重、幂等重放、span-link 的前提（被删的 event_dedup.rs 缺的就是它）
  specversion: u16,        // 线格式版本门
  timestamp_ns: u64,       // 启动纳秒，唯一规范语义
  source: EventSource,     // 收敛为枚举（现 pipeline_contract.rs:105-203 零生产引用的那 20 个值）
  pid: u32, comm: String,
  payload: EventPayload,   // 按 source 分派的标签联合，含 Raw{source_key,value} 兜底臂并计数
}
```

- **迁移**：先加字段 + `Raw` 兜底臂**双写**，`.data.get` 继续工作；再按 source 逐个类型化（每 source 一提交）。
- 立刻修 `bin/perf_throughput.rs:45` 比较 `"http"` 而生产者发 `"http_parser"` 的**恒零计数**。
- `source` 补齐枚举外的 6 个漂移值（`http`/`chunk_merger`/`custom`/`latency`/`observability_metrics`/`conversation_flow`）。

### 3.2 身份 = 两个实体的关联 + 证据等级

```
RuntimeEntity      { pid, start_time_ns }   ← OTel 的 process 最小充分身份，与现有 PK 一致
AgentSessionEntity { session_key }          ← 逻辑/会话实体
Association(runtime, session, grade, corroboration, observed_at, recorded_at)
Grade ∈ { Observed, Asserted, Inferred, Unknown }
```

- **不把 `(pid, sessionKey)` 拍成单值主键**（学界已指出 `string equality does not make them process IDs`）。
- `AgentId` 派生改 `{runtime}/{session}`；**claude-code profile 下退化为旧值以零回归**。
- `asserted` 固定为最低保证级（NIST SP 800-63A IAL1：self-asserted, neither validated nor verified）；只在有独立足迹（eBPF 进程/TLS/文件路径，或 W3C `traceparent` 这类**非自报强键**）时才标"已佐证"。
- **冲突规则四步**：① latency check 优先（带外 sqlite 有写入延迟，未到齐不算冲突）→ ② 按**属性**而非按来源授权威（"逻辑 agent 是谁"asserted 权威；"进程/效果是什么"observed 权威）→ ③ 无权威可依则降置信、保留双方 → ④ 由**被观测主体自写**的分歧本身就是 finding，不消解。
- **同进程 N agent 的精确分离不做承诺**（截至 2026-10 无已发表工作解决）。产品承诺降为"**候选集 + 等级 + 事后可修正**"，前端展示判定依据。

### 3.3 因果图

- 边关系收敛为唯一集 + 一条 `Influence` 式泛化兜底（照 W3C PROV 的泛化关系）。
- 边加 `source_kind ∈ {Direct, Derived, Asserted}` + `asserted_by`（PROV `wasAttributedTo`：内核观测／启发式／observer LLM）+ `grade`；旧 `confidence: f32` 保留并与 STIX 0-100 对齐。
- **组合代数**：`⊕` 多来源/竞择解释，`⊗` 推导链取较弱者；**写成真值表 + 单测**。
  现状用 `c>=0.70→Observed / c>=0.50→Inferred`（`claims/claim_builder.rs:271-272`）冒充，
  把"我有多确定"与"我是怎么知道的"混为一谈 —— **拆开**。
- `ResponsibilityClaimState`（`alert_types.rs:160-168`，一个枚举同时编码认识论等级/生命周期/事件种类）**拆三字段**；
  `alert_types.rs:123` 的 `evidence_level: Option<String>`（全仓只有声明、无写入方，且实际装的是 `MatchLevel`）删除。
- **给 `pattern_matcher.rs` 的 19 处 `required_edge_types` 做裁决**（§0.3）：补生产者 **或** 显式标 `unbacked` 并在能力清单里可见。

### 3.4 持久化 schema

- **单一 DDL 权威**：保留 `schema.rs`；`causal_schema.rs` 与 `responsibility_schema.rs` 折叠进来；
  幽灵表 `causal_graph_snapshots`（`web.rs` 13 处引用、verifier 3 处引用，**无任何 DDL 创建**）补齐或显式删除。
- 迁移机制：`PRAGMA application_id` + `PRAGMA user_version`，迁移写成版本递增数组（expand-contract）；
  新增测试"**全新库 vs 逐版升级库 schema 完全相同**"。
- **枚举落盘统一**：删 `writer.rs` 的 `format!("{:?}")`（Debug 是 Rust 明确不承诺稳定的输出格式）
  + `reader.rs` 手写 `parse_*` 的 `_ => 某合法值` **静默兜底**（`reader.rs:140` 把未知 parse_state 当 `Complete`、
  `:185` 把未知 action_type 当 `FileRead`）；改显式 `Unknown` + 计数 + **全变体往返 property test**。
  > ⚠️ **唯一例外**：`intent_class` 小写写入侧是**评测承重契约**（见 §8 A），**不得改**。
- `PhaseIndicator`（`types/phase.rs:4-12`，带载荷枚举被 Debug 落盘、`reader.rs:43-53` 反解把 `timestamp_ns` 置 0 并丢 `overrides`，而 `frontend/lib/db-import.ts:228` 正按 `"Provisional { timestamp_ns: N }"` 反向正则解析）
  → 拆 `phase_kind TEXT + phase_at_ns INTEGER + overrides_json`。
- 补时间列：13 张无时间列的表统一 `observed_at_ns + recorded_at_ns`（`context_pollution_events` 用 `detected_at_ns` 改齐）。
- `identity_process_map` 的 `INSERT OR REPLACE`（`writer.rs:932`）→ **追加式 + belief 窗口**
  （`effective_from/effective_to` + `recorded_at`），修正**不覆盖**旧行；补 `Split`/`Reassigned` 产出
  （`union_find.rs:466-483` 现清一色 `Confirmed`）。
- `confidence` 一词两义两型（`schema.rs:117-123` REAL vs `:126-134` TEXT）统一为 REAL + 独立 `confidence_basis` 列。
- **run manifest**：落盘 `run.json`（run_id → 各产物路径 + schema_version + 行数）；
  所有消费方（`server/web.rs`、`bin/replay.rs`、`env/.../collector_manager.py`、前端 `db-import.ts`）
  **只读 manifest，禁止文件名猜测**（现状 4 处各自重建命名规则，任一改名让某消费方静默读空）。

### 3.5 向后兼容与既有 DB

- 既有约 220 个 run 的 `*.responsibility.db` 必须可读：新列可空或带默认；`user_version` 从 1 起单调递增；读取层**双写双读**。
- 前端切到新列后再删旧列（expand-contract 第四步）。
- 前端**直接读 SQLite 这一模式保留**（`store.ts:342-347` 是主路径），但其 26 张表的手写列名 SQL
  由 **Rust 类型生成 TS** 取代（工具选型须先 spike，见 §9.4），生成物入库 + CI diff 断言。

---

## 4. 扩展点（新人照做）

### 4.1 接一个新 agent CLI（目标：不改 Rust）

**前置判定**：若新 CLI 的明文能被 `SSL_read/SSL_write` uprobe 抓到（动态链接或静态但导出符号）⇒ 纯数据；
若需全新 hook 类型（如 QUIC）或全新内存布局 ⇒ **需内核变更**。**诚实承认：不是所有接入都是纯数据。**

1. 复制 `profiles/claude-code.toml` → `profiles/<new>.toml`，`maturity = "sandbox"`。
2. `[runtime].selectors` 按 `cgroup_unit > cmdline_contains > exe_basename > comm > 父链` 填。
   实测事实：Node 长驻 Gateway 的 `comm` 是 `MainThread`（`-c` 过滤全失效），**只有 cgroup 可靠**。
3. `[tls]`：动态/导出符号 ⇒ `attach = "exe_symbols"` + 符号表；BoringSSL 字节模式 ⇒ `attach = "byte_pattern"`。
   `fail_policy` 恒为 `warn_per_symbol`。
4. `[protocol]`：`accept` + `api_paths`。
5. `[[extract.context]]`：**硬性要求——工具定义与工具调用是两个互不相交的 context**
   （这正是幻影意图 `intent_extractor.rs:803-810` 的结构性解药）。
6. `[identity]`：`primary_key` 用**每请求都携带且指向具体 agent** 的信号（首选 system prompt 稳定区内的 workspace 路径；
   **不要整体 hash**——skills 可见性会变）；`corroborators` 填**非自报强键**（`traceparent` 一等）。
7. `[tools].vocabulary`：新建 `vocabularies/<new>.toml`；**未知工具名保留原名**进 `IntentClass::Unknown`。
8. `[curated]`：敏感子串、workspace 前缀、基础设施路径——把 `policy.rs:492-509` 的 `SENSITIVE`、
   `graph_builder.rs:947-953` 的 `WORKSPACE_PATH_PREFIXES`、`main.rs:1345-1348` 写死的 workspace root 都搬进来。
9. `cargo xtask validate --profile profiles/<new>.toml`（**不采集**；校验 schema + 探测权限与可挂符号；
   对齐 `otelcol validate` 与 Tetragon 的 policy status API）。
10. 若有新载荷形状，加 `framework/normalize/provider/<name>.rs`（照 OBI 的 `genai_normalize_*` 布局）。
11. `cargo xtask gen` 重生成 manifest → 跑一次真实采集 → `maturity` 提升到 `incubating`。

### 4.2 接一个新协议（HTTP/2）

h2 解析器**不改抓取层、不改解码层、不改语义层**——只在 L2 加一个协议解析器分支
（`SSL_read/SSL_write` 拿到的明文就是 h2 字节流；ALPN 由 `SSL_get0_alpn_selected` 标注为 `network.protocol.version`）。

**h3 结构性不可及**：BoringSSL 的 QUIC 走 `OSSL_FUNC_SSL_QUIC_*` 回调、与 `SSL_read/SSL_write` 分离
⇒ 只建立**检测信号**（观测到 UDP/443 而 SSL 侧无事件）并写进 `known_limitations`。

### 4.3 加一条检测规则

1. 现有 selector 能表达 ⇒ **只加一个 `[[rule]]` 块，不动 Rust**。
2. 三段分层：夹具路径/标记串/工具名 → `[[list]]`；可复用条件 → `[[macro]]`；判定与产出 → `[[rule]]`。
   **禁止把 fixture 绝对路径写进 `[[rule]]`**（现 `handoff_injection.rs:718/729` 写死 `/home/lzk/...`）。
3. 必须填 `maturity` / `enabled_by_default` / `falsepositives`（**Sigma 式一等字段**）/ `requires_engine`。
   Sigma 规范已核：`status ∈ {stable,test,experimental,deprecated,unsupported}`、`level` 五档。
   **benchmark/场景专用规则一律 `sandbox` + 默认关。**
4. 新 selector 变体属**内核评审**（`rules/selectors.rs` 加变体 + 对归一化事件求值 + 单测）。
5. 加 fixture 测试 + 在 **180 个真实 run**（`env/runs/*/collector/collector.responsibility.db`）上做**判定一致性回归**（迁移期硬门）。

### 4.4 新增一个事件域 / 内核 hook

改 `bpftools/include/common/event_fields.h`（三处手抄字段名的终点）+ `contracts/wire/sources/*` 登记 +
C 侧照 `tls_capture.c` 的 best-effort 宏族实现逐符号容忍 + 复用 `bpftools/common/bpf_util` 的 drop 计数。

**优先 `fentry` on VFS/网络内核函数（≥5.5），LSM（≥5.7，需 `CONFIG_BPF_LSM`）作为可选增强**——
syscall-entry 对 `openat2`(437)、`io_uring`(425-427) **天然失明**（已实测 syscall 号）。
`coverage_contract` 测试立刻把"Rust 期望 ⊄ C 实际 emit"变红。

### 4.5 新增出口

`collector/src/export/<target>.rs` 实现映射；`gen_ai-mapping` 是唯一映射表；
span 类型 `mtl.gen_ai.*`、属性 **ref 上游原名**、scope = `io.mtagentlens`、**content 默认不出口**、
`schema_url` 可配 + **Era1→Era3 迁移表**、推导 id 留自有命名空间。

---

## 5. 防腐化机制（可执行）

> **总原则：任何守卫必须先断言被检对象存在且非空**——否则重演 `responsibility_naming_guards.rs` 的静默空转。

### 5.1 门禁入口

`cargo xtask verify`（`collector/xtask/`，照 cargo-xtask；`.cargo/config.toml` 加 `[alias]`），
`.gitlab-ci.yml` 调**同一入口**。首批 8 条：

```bash
cargo fmt -- --check                                # 现状 exit 1（7 处 diff）
cargo clippy --all-targets -- -D warnings           # 现状 24 warnings → 先清零
cargo test --lib                                    # 927 passed
make -C bpftools test                               # 需先入库 test_json_escape.c / test_tls_capture_header.c
orphans 检查                                        # 任何 collector/src/**/*.rs 未被 mod 树引用即红
cargo xtask gen && git diff --exit-code             # 契约/文档/TS 类型/能力清单无漂移
unbacked-produces 检查                              # 声明 produces 却无生产者的 stage 进 known_limitations
allow 预算                                          # #![allow( 与 [lints] 条目数只许减不许增
```

### 5.2 编译器护栏

`collector/Cargo.toml:49` 的 `[lints.rust]` 现为**空表**，填入：

- `dead_code_pub_in_binary = "warn"`（官方 lint，**专治"release 二进制里 strings 消失"这类 bin 内 pub 死项**）
- `unreachable_pub = "deny"`（先"deny 新代码 + 存量清单显式 allow 带注释"，勿一刀切）
- `unused_crate_dependencies = "deny"`（注意 lib/bin 同包误报，用 `use x as _;` 处理；**不需要 cargo-udeps**，它要 nightly）
- `rust-version = "1.85"`（edition 2024 的实际下限；**现缺**）

### 5.3 能力清单 ↔ 真实接线

`catalog.rs` 是唯一注册表；`cargo xtask gen` 生成 `docs/capabilities.{json,md}`，含
`supported_agents` / `analyzers{id,stage,maturity,shipped_in,registered,consumes,produces}` / `rules` / `known_limitations`。

测试**双向断言**：声明为可用 ⇒ 真被编译、真被注册、真能产事件；在源码里但不在清单里 ⇒ **红**。

README（`README.md:138` 称 29 analyzers、`:467` 称 869+ 测试，**均陈旧**）与 `CLAUDE.md:328` 的 Event 字段表
改为指向生成物或删除。

`known_limitations` 首批登记：

- 7 种幽灵事件类型（`FILE_WRITE`/`DELETE`/`TRUNCATE`/`RENAME`、`NETWORK_CONNECT`/`SEND`、`PROCESS_EXEC`）**无 C 侧生产者**
- HTTP/2 不支持；HTTP/3 结构性不可及
- **内核下限 ≥5.8**（5 个程序全用 `BPF_MAP_TYPE_RINGBUF`；`tls_capture` 还用 `bpf_probe_read_user` ≥5.5）
  —— 而 `README.md:491`、`bpftools/README.md:547`、`CLAUDE.md:331` **三处仍宣称 4.1+**，
  必须只在一处声明 + load 时版本守卫给出可读错误

### 5.4 文档纪律（Diátaxis 的引用边界）

Diátaxis 的 reference 章**正面推荐**"参考文档由代码自动生成"。据此分域：

- **reference 域**（CLI 从 clap、配置从 struct、SQLite schema 从 DDL、事件类型从契约、能力表）
  **承诺与代码一致**，且由生成物驱动
- **explanation 域**（`docs/paper`、`docs/决赛文档`、`docs/superpowers/specs`）**显式声明"不承诺持续同步"**

给能力/阶段声明引入**机器可读枚举**（`proposed/active/degraded/removed/superseded`，照 KEP 的 `status/stage` 与 weaver 的 `stability`），
CI 校验合法值 + `supersedes` 路径存在。

同时清理 `collector/src` 内 **17 份零实现 analyzer design `.md`**（`event_deduplication_design.md:4-6` 仍指向已删模块）。

### 5.5 决策与发布

- `docs/adr/` 用 **MADR minimal**（YAML front matter `status` 枚举 + Context/Options/Outcome/**Confirmation**）；
  **只记不可逆决策，不迁移任何既有 specs**；索引由 `adr-log` 生成 + CI diff。
- `RELEASES.md`（CNCF 五要素模板 + Tokio 式版本政策）+ `CHANGELOG.md`（Keep a Changelog 结构，**手写**）。
- **许可证收敛（实测不一致）**：`LICENSE` 是 **Apache-2.0**、`README.md:358` 称 "Apache-2.0 + BPF GPL 例外"，
  但 `collector/src` 的 **136/195 个 `.rs` 写 `SPDX-License-Identifier: MIT`**、**58 个无 SPDX 头**、
  且 `collector/Cargo.toml` **无 `license` 字段** ⇒ 补字段 + 批量统一 SPDX + CI 校验每个 `.rs` 有 SPDX
  （顺带可用 `cargo-deny` 做 license/advisories/bans/sources 门）。**这是法律层面的歧义，不是风格问题。**

### 5.6 eBPF 层

- 5 个程序的** drop 计数统一**（现只有 process/tls_capture 有；`browsertrace.bpf.c:89-91`、`stdiocap.bpf.c:79-83`、
  `fileaccess.bpf.c:26-28` 的 reserve 失败即**静默 return**）
- ringbuf 尺寸改**单一常量 + `static_assert`**（2 的幂、页大小倍数）+ load 前 `bpf_map__set_max_entries`
- attach 统一 best-effort + 输出"**本次挂上了哪些探针**"的机器可读清单（对应 Tetragon 的 `missed_probes` 指标）
- SEC 名归一（`tls_capture.bpf.c:91` 与 `:345` 两个不同程序共用 `SEC("uprobe/do_handshake")`）
- 删 `include/process.h` 与 `process/process.h` 的**逐字节副本**之一（md5 均为 `5626366c192dd977033b0501216f0432`）+ `process_new.h`
- 清 `struct event` 的 **7 个幽灵字段**（`include/process.h:38-43` 声明、内核从不填充，而设计文档标"已实现"）
- 5 份 `libbpf_print_fn` + 5 份信号处理 + poll 收敛到 `bpftools/common/bpf_util.{c,h}`

---

## 6. 分期路线图

每期**独立可交付**；顺序由**数据依赖**决定，不可倒（在错误的数据上做正确的算法是最大浪费）。

### P0 · 门禁与可复现（1–2 天，零行为变更）

**范围**：修 `bpftools/.gitignore` 3 条规则恢复三程序源码 + 入库 `tests/test_json_escape.c`/`test_tls_capture_header.c`；
建 `xtask verify` + `.gitlab-ci.yml`（8 条门）；开编译器护栏（§5.2）；修 24 条 clippy + 7 处 fmt；
解 `collector/observer_llm.toml` 的 mode 120000 符号链接（指向 `/home/lzk/MtAgentPrism/...`）；
消 `main.rs:1` 的 TEMP；建 `docs/adr/`。

**验收（可验证）**：全新 clone → `make build` 成功；`cargo test`（**完整**，非仅 `--lib`）绿；`xtask verify` 本地与 CI 同绿。
**风险**：低。唯一风险是修 clippy 时误改行为——风格类与 API 形态类分开，后者先豁免登记。
**独立价值**：项目从"不可复现"变"clone 即可构建 + 测试绿"。
**没有这一期，后面所有机制都退化成又一层会腐烂的文档**——这正是本项目已经失败过的那一环。

### P1 · 字节无损 + 响应链复活（10–16 天，**最早的产品级收益**）

**范围**：

1. **C 侧载荷改 base64**（RFC 4648 §4，单射、可逆、恰 33% 膨胀 + `data_len`），替代 `tls_capture.c:591-624` 的 `\u%04x` 码点逃逸
2. **删入口 lossy**：`runners/common.rs:223` 的 `from_utf8_lossy` → 按字节读、base64 解码后再解 UTF-8
3. **新建 `framework/normalize/`**：字节级 **de-chunk**（RFC 9112 §7.1.3）→ **逆序解 Content-Encoding**（RFC 9110 §8.4）
   → 支持 zstd/gzip/deflate/br。zstd 按 **RFC 9659 §3**：window ≤8 MB，超限映射 `decode_failed` 而非 OOM，另设输出上限与膨胀比上限
4. **搬移/删除两个误导性判定**：`main.rs:531` 的 `data.type=binary` 移到**解码后**（或删除）；删 `record` 的 `response.body=`（§8 B2）
5. **接线 `responsibility/outofband/`**（现有 499 行**零调用**）
6. 5 个 BPF 程序补 drop 计数；`coverage_contract` 测试（7 种幽灵类型变红）；删 `PROCESS_EXEC` 冗余臂

**验收**：OpenClaw `record` 一次真实运行 → 响应侧 SSE 产出 **≥1 个 finalized message**（现状被整条丢弃）；
`correlation_evidence` 出现响应侧来源；base64 往返无损（`decode(encode(x))==x` property test）；
`non_link_diagnostics` 能区分"参数不匹配"与"置信度被砍"。

**风险**：中高。**直接触碰 SSL 采集路径 = Claude Code 冻结路径** ⇒ **必须先建"无损 Claude Code 录制 fixture 回归门"
（旧 decode ≡ 新 decode 语义）才允许合入**；wire 变更受 `specversion` 门控。

**独立价值**：目标部署环境（spec §0 明写是 **OpenClaw 单位环境**）**第一次能读出 LLM 响应**。

### P2 · 单一管线 + registry + 能力清单（15–22 天）

**范围**：纯删除先行（§2.3 全部）；`Transform`/`Sink` 双 trait + `catalog.rs` + `framework/pipeline/` 唯一组装点；
5 份手写链 + 35 处 `add_analyzer` 收敛；**`ConnectionBuffer` 接入所有链**；
每 stage 强制上报 `in/out/dropped-by-error/dropped-by-design/held`；
`pipeline_contract` 提升为接线真源并清幽灵 stage；
`xtask gen` 生成能力清单 + wire codegen（C 头 + TS 类型）替换手工镜像的 `frontend/lib/types.ts`；
`/health` 修掉或删除（`HealthState::new` **零调用者**、恒报 Healthy）。

**验收**：`rg 'add_analyzer' collector/src/main.rs` 只剩 registry 调用；5 个子命令共用同一 registry；
`cargo test --lib` 绿；`git diff --exit-code` 无契约漂移；`orphans` 零命中。

**风险**：中高。最大一块重构 ⇒ trait 变更**先在 3 个无状态分析器**（`auth_header_remover`/`timestamp_normalizer`/`ssl_filter`）验证行为一致，
再逐模块迁移；`ResponsibilityRuntime` 留 Sink 形态放最后且单独验证。

**独立价值**：两个主打命令恢复请求重组能力；**实时原始事件流在运行中可用**
（现状 `runtime.rs` 把整轮事件缓冲到输入结束 ⇒ `main.rs:1428` 的 `event_sender` 只在运行结束后跑一遍 ⇒ `/api/live/events` 原始事件流在运行中**恒为空**）；
内存峰值不再是整轮事件之和；约 6,100 行不可达代码消失。

### P3 · 身份与因果（25–38 天，**语义风险最集中**）

**范围**：身份两实体 + 证据等级 + 歧义状态（§3.2）；`AGENT_COMMS` 双副本改 profile；
边关系收敛 + 边证据等级 + 半环代数（§3.3）；**给 19 处 `required_edge_types` 补生产者或标 `unbacked`**；
修**假 taint**（`writer != reader` 在进程级唯一 id 下**恒真** ⇒ 大量 `TaintPropagatesTo` 是单 agent 会话误报）；
`correlator_legacy` 合并 + `MatchLevel` 统一；单 schema + `user_version` 迁移 + phase 拆列 + 时间列统一 + run manifest + 双时间轴；
**接线 `outofband` 已写好的 openclaw.rs schema 校验路径**（asserted 不覆盖 observed）。

**验收（真实 run，非单测）**：同 pid 的 `agent_identities` 出现 **2 行**；告警 `primary_agent_id` **100% 落在身份表**；
`graph_nodes` 的 distinct `agent_id` 不再等于节点数；`capability_use` 类模板要么产出、要么在 manifest 中标 `unbacked`；
`pattern_verdicts` 非空。

**风险**：高。改 `AgentId` 派生公式波及前端 `raw-event-browser.ts` 的 JOIN、`multi_agent_anomaly` 的 `pid_root`、
`security_signoff` 的 `root_pid`、评测脚本 ⇒ **先做穷尽消费者审计**。
`identity_refinements` 无真实 Split/Merged 样本 ⇒ 需**合成故障注入基准**。
**同进程 N agent 的精确分离不承诺解决**（学界公认开放问题）。

**独立价值**："责任追溯"跨 run 可指认同一个 agent；无落点告警有归属；最弱档匹配不再冒充确定因果。

### P4 · 通用化：profile + 规则数据化（18–28 天）

**范围**：`profiles/*.toml`（**用 TOML**——`toml=0.8` 已是运行时依赖，`serde_yaml` 只是 dev-dependency；
**不要混两种格式**）+ `xtask validate` 干跑 + `serde(deny_unknown_fields)` + 双向版本门；
检测规则外置 Falco/Sigma 三段 + `maturity`/`level`/`falsepositives`/`requires_engine`
（实测活代码中 **97 处**场景字面量：`handoff_injection` 48 + `multi_agent_anomaly` 38 + `security_signoff` 11，
以及 `filepath.contains("/env/scenarios/fixtures/")` 这类**硬判定门** → 全部外置，benchmark 场景降 `sandbox`）；
per-provider normalizer + **定义/调用双上下文**；`tool_vocabulary.rs` 的 const 表 → `vocabularies/*.toml`；
eBPF 共享库；`[curated]` 迁入所有硬编码路径。

**验收**：新 CLI 接入**不改 Rust**（除需新 hook 的场景）；general 规则在**非 fixture 目录**的流量上能触发
（现状 `multi_agent_anomaly.rs:575` 直接 `return Vec::new()`）；180-run 判定一致性回归通过。

**⚠️ 分阶段硬门**：先 **shadow 模式并行输出**，以"180-run 判定一致性"为合并门。**接受一次可解释的分数下降**——那是"不再靠自己造的夹具作弊"的代价。

**独立价值**：新 agent CLI 从"逆向 + 改代码 + 重编译"变"写一份 profile"；检测能力第一次在真实流量上可触发。

### P5 · 出口与治理（5–12 天，可选）

`export/genai.rs`（scope `io.mtagentlens`、`mtl.gen_ai.*` span、`schema_url` 可配、content 默认不出口）；
ADR 补齐 + `adr-log` 索引；`CHANGELOG.md` + `RELEASES.md`；reference 域生成；`cargo-deny` + SPDX 统一；
OpenSSF Best Practices passing 逐条 gap 分析。

**总量约 75–120 工程师-日**。**最小可交付集 = P0 + P1 ≈ 12–18 天**，即让"全新 clone 不可构建"
与"目标环境读不出响应"两个硬伤消失。

---

## 7. 路线图级的"不做"

除 §1 的拒绝清单外，路线图层面明确不做：

| 不做 | 原因 |
|---|---|
| 在 P1 之前做任何 P2/P3 的重构 | P1 是唯一能让**目标部署环境（OpenClaw）**可用的期；在坏数据上做正确算法是最大浪费 |
| 为"未来可能"预先抽象（插件系统、多后端、动态规则） | 三份提案的评委一致指出：本项目病史正是"预留了但从未接线"（`outofband/` 499 行、第二管线 2,761 行、6 个未注册分析器） |
| 用 perf buffer 回退换丢包计数 | 已硬依赖 5.8，ringbuf 更优；自计 reserve 失败即可 |
| 把 5 个 BPF 程序合并成一个 object | 违反"不做大爆炸"，且 fileaccess/browsertrace 存废待定 |
| 删不可达代码时顺带删其测试而不声明 | 927 的语义必须说清是"删不可达代码的测试"而非"回归" |
| 追 CNCF/Scorecard 徽章、改 commit 风格、自动 changelog | 见 §1 |

---

## 8. 决策裁决

### A. `Bash` 的工具分类 —— **解耦，而非二选一**

**背景**：统一工具词汇表时把 `Bash` 定为 `SideEffect`（语义上正确：bash 执行动作，不是"agent 协调"），
但 `intent_class` **落库列**同时被评测当指标读。

**实测代价（180 个真实 run）**：

| `intent_class='side_effect' / total_intents` | mean | stdev | 不同取值数 |
|---|---|---|---|
| 现状（Bash=Orchestration） | 0.604 | **0.233** | 73 |
| 改后（Bash=SideEffect） | **0.975** | **0.071** | 29（多条钉死 1.000） |

且该比率**不只是被上报**——`compare/scripts/evaluate_mtagentlens.py:239-240` 把它当**分数增强器**：

```python
if intent_side_effect_ratio > 0.3:
    score = min(score * (1.0 + intent_side_effect_ratio * 0.1), 1.0)
```

⇒ 饱和后它对所有 run 近乎一致，**从"区分项"退化成"统一通胀因子"**。这比"比率变了"更严重。

**裁决（三步，成本递增）**：

1. **语义层保留**：`tool_vocabulary::classify_intent_class` 保持 `Bash = SideEffect`（唯一权威，正确）。
2. **匹配层换谓词**：把 `support_types.rs:516-519` 的 `refresh_matching_eligibility` 判据
   从 `matches!(intent_class, SideEffect)` 改为**显式语义谓词** `is_side_effecting_tool(tool_name)`。
   **这是唯一的逻辑改点**——`primary_matching_eligible` 本就是独立列，落库的 `intent_class` 从不是它的必要输入。
3. **度量层改为可重算**：评测脚本不再读派生列，改读 `tool_name`（逐字持久化）+ **固定 vocabulary 版本**自行分类。
   ⇒ 指标从此**可重算、可复现**，且"语义表与落库值不一致"这个病根被消除。

> **比"冻结字段"更好**：(a) 冻结会让 `intent_class` 语义与实现永久不一致，正是本项目要消灭的病；
> (b) 把"不敢改字段"变成"版本可知的可重算投影"，是顶级项目的做法。

**反悔成本**：低（第 2 步一行判据 + 一个谓词；第 3 步改 3 个脚本，且口径已在 180 run 上验证）。
**阻塞**：**未提交的工作树已经把 Bash 翻成 SideEffect**（`tool_vocabulary.rs` 测试钉死）⇒ **必须在 commit 前定**。

### B1. `browsertrace` / `fileaccess` 存废

**事实**：两者都被 `bpftools/Makefile:26`（`BPF_PROGS`）构建；`binary_extractor.rs:13-17` **只嵌入** process/tls_capture/stdiocap；
全仓（排除 bpftools 自身）对二者**零引用**。

**裁决**：

- **`browsertrace` → 删除**。它挂的是浏览器专用符号（`browsertrace.bpf.c:190-237`：`PR_Write`、`SSL_ImportFD`、`chrome_plaintext_request_1` —— NSS/Chrome 栈），
  与目标部署（单机 CLI agent）无关；且与 `tls_capture` 有约 162 行逐字重复、各自维护**布局不同**的 `probe_SSL_data_t`（browsertrace 版**缺 `ssl_ptr`**）。
- **`fileaccess` → 不作为独立程序保留，收编进 C1**。它唯一产出 `FILE_OPEN`，与 `process` **完全重复**（实测同一次采集 637 = 637）。
  它是"多管线"债务在采集层的形态。若短期不动 process，**也别单独接线它**（会主动制造第二条 `FILE_OPEN` 来源）；
  正道是**把它的 open 观测升级为 fentry on VFS 并合并进 process**。
- **但先入库**（P0）：三个源码目录必须先 `git add`，否则"删除/收编"都无从谈起。

**反悔成本**：删 browsertrace 低（可从 git 恢复）；fileaccess 若已按 C1 改造则中。

### B2. `record` 预设的 `response.body=`

**裁决：删除该条件。不是改写，不是保留加注释。**

**逐环已复核**：`main.rs:529` 该 token 值为空 → `http_filter.rs:196-205` 解析为 `target="response"`、`field="body"`、`value=""`
→ `:397-400`（response 分支）`body.contains("")` **恒真** → `:437-443` 命中即丢弃（且只对 `source == "http_parser"`）

**为什么是删而不是改写成"丢弃无 body 的 response"**：空 body 本身不是威胁，丢弃它**没有安全或性能收益**，
反而丢失状态码/头等可分析信息；要表达这个语义需**新增操作符**（如 `response.body_empty=`）并显式命名——
不值得为一个来源不明的笔误扩 DSL。四项并列（`/v1/rgstr`、`202`、`HEAD`）指向"过滤 landing/遥测噪声"，
`response.body=` 显然是想续写却写空。

**对 Claude Code 冻结路径影响：零**——Claude Code 的 tool_use 主要来自 `sse_processor`（`source=="sse_processor"`），
而 http_filter 只过滤 `source=="http_parser"`。

**反悔成本**：低（一行）。与 C2 同批做。

### B3. "第二管线"整批存废 —— **部分保留 + 分期；净删约 2,761 行**

| 文件 | 行数 | 裁决 | 依据（已复核） |
|---|---|---|---|
| `pipeline_contract.rs` | 1,018 | **保留，改造为唯一 registry 种子** | `StageContract{consumes,produces}` 是对的；`StageMetrics` 被**活的** `server/web.rs` 与 `server/api_types.rs` 使用 |
| `pipeline_orchestrator.rs` | 606 | **删除** | `PipelineOrchestratorImpl` 全仓唯一构造点在**自身测试**；`mod.rs:72` 只 re-export |
| `analyzer_composition_contract.rs` | 746 | **删除** | 全仓唯一引用是 `mod.rs` 的模块声明，**零消费者** |
| `alert_enrichment.rs` | 476 | **删除** | 未注册；与已注册的 SecuritySignoff/HandoffInjection 告警产出重叠；其"去重/冷却"应在唯一 sink 层实现 |
| `role_hijack_contract.rs` + `agent_teams_role_hijack.rs` | 552+381 | **删除** | 全仓引用只有 `mod.rs` 的 `mod`/`pub use`，**未注册** |
| `SharedStateStore` trait | — | **删除** | 唯一 impl 已删，零实现者/调用者 |

**先纯删**（零行为变更，属 P2）；删 `alert_enrichment` 前确认 Signoff 是否已覆盖"告警冷却"语义。

### C1. 7 种事件类型 Rust 期望、C 从不发出

**裁决：不删 Rust 分支；先做两件小事，补 hook 放后期。**

- **硬事实**：`__NR_openat=257`、**`__NR_openat2=437`**、**`__NR_io_uring_{setup,enter,register}=425,426,427`**（已实测 syscall 号）。
  `process.bpf.c` 只有 5 个 hook，且 open 走 `tp/syscalls/sys_enter_open{,at}` ⇒ **对 `openat2` 与 `io_uring` 天然失明**。
- **hook 选型：不要用 syscall tracepoint 补**。应用 **`fentry` on VFS**（`vfs_open`/`vfs_unlink`/`vfs_rename`/`vfs_write`/`security_file_open`，≥5.5）
  ，BPF LSM 作**可选**增强（需 `CONFIG_BPF_LSM`）；网络用 `tcp_connect`/`tcp_sendmsg` 或 `security_socket_connect`。
- **顺手把 7 降到 6**：`PROCESS_EXEC` 是**冗余期望**——C 已发 `EXEC`，而 Rust 两条分支都同时认 `EXEC` 与 `PROCESS_EXEC`
  ⇒ 直接从期望集删掉 `PROCESS_EXEC` 臂，**不是"补 C 发射"**。
- **分阶段**：P1 建 `coverage_contract` 测试 + 删冗余臂（低成本）；P4/P5 补 file 与网络 hook（各过 verifier + 内核矩阵）。
- **反悔成本**：低（先建测试与裁剪）；补 hook 中高。

### C2. zstd 压缩 + `data.type=binary` + 入口 lossy

**裁决：在解码层（L2）解压，且必须整体修复整条链，顺序在 `SSLFilter/SSEProcessor/HTTPParser` 之前。** 四步见 §6 P1。

**诚实降级**：解压失败/未知 encoding → 产出带 `decode_state ∈ {ok, unsupported, failed, partial}` 的事件并**计数**，
**绝不静默丢弃**（对齐 `sse_processor` 的 `evicted_with_content` 范式）。

**阻塞项**：它阻塞 C3 的验证与目标环境响应侧可用性——**不解压，响应永远到不了解析器**（`SSEProcessor` 产出 0）。

### C3. 工具定义被当成工具调用（幻影意图）

**裁决：引入显式"定义上下文 vs 调用上下文"判别，停止按 JSON 形状猜。**

1. **结构性剪枝**：`tools[]`（OpenAI `tools[].function`、Anthropic 顶层 `tools[]` 各含 `input_schema`）/`tool_choice` 所在子树是**定义上下文**，整棵剪掉。
2. **只从调用位置取名**：`tool_calls[]`、`function_call`、`content[].type=="tool_use"`。
3. **未知协议默认保守**：只在"结构上被断言为调用"的位置取名。**正确性优先于召回**——定义误当调用会污染整条因果链。

**顺手修**：`routing.rs:239` 把 `IntentSource::SseResponseToolUse` **硬编码**给所有 evidence 路由事件 ⇒ 署名不可信；
应据实际来源（请求 vs 响应）标注。

**对 Claude Code：零回归**（Anthropic 无 `function` 包装，Pattern B 从不命中）。与 C2 同批（P1）。

### C4. harness 内 openat 不可观测 → 验收体系分层

**事实**：`strace` 可见、无 PID namespace、`Seccomp: 0`，但主机 eBPF 观测不到 harness 内进程的 `openat`
（`process` 576 行与 `fileaccess` 4288 行**同时漏掉**同一 marker）。**这是环境/沙箱边界，非产品缺陷。**

**裁决：接受为环境边界；把验收体系分层 + 声明可观测边界。**

| 层 | 内容 | 可信度 |
|---|---|---|
| 1 契约/单测 | `cargo test` + `make -C bpftools test` + `xtask verify` | **唯一**在本 harness 内可信执行的一层 |
| 2 离线回归 | 用已录制的**真实字节流** fixture（Claude Code + OpenClaw）驱动完整解码链 | **P1 敢碰冻结路径的前提** |
| 3 特权 E2E | 真机采集（sudo）。**必须记录环境指纹**（内核/cgroup/namespace/seccomp/comm）及其对可观测性的影响；本 harness 内的失败**不得**作为产品缺陷判据 | 人工或特权 runner |
| 4 产品自证 | drop/evict/decode_failed/attach 失败一律计入**机器可读清单** | 把"我没看到"变成"我没看到 N 条，原因是 X" |

---

### 8.2 需**用户拍板**的产品决策

| # | 决策点 | 推荐 | 反悔成本 |
|---|---|---|---|
| D1 | `fileaccess` / `browsertrace` 存废 | P0 先入库可复现；P4 时**保留 fileaccess 并扩为 VFS fentry 生产者**（补 7 种幽灵事件的一半），**删除 browsertrace** | 低 |
| D2 | 3 个已注册的场景耦合检测器 | **外置为 `sandbox` 规则保留、默认关闭**（Falco 模式），不删除——它们承载真实威胁面，只是判据错了层 | 中 |
| D3 | 内容出口策略 | **走规范第 3 档**（外部存储 + 引用）；内部存储保持默认可看（产品价值所在） | 中 |
| D4 | span 身份前缀 | **`mtl.`**（短、与项目名一致）——**现在定最省**，避免二次改名 | 低-中 |
| D5 | 未挂载 `responsibility/server/`（2,524 行）vs 活 `server/web.rs` | **保留挂载者为唯一服务端**；先改前端 `api.ts:80/96` 到活路由，再删未挂载树 | 中 |
| D6 | 带外 sqlite 是否信任为 asserted | **asserted 最低保证级 + 强制佐证**（有独立足迹才标"已佐证"） | 低 |
| D7 | 内核下限是否接受 ≥5.8 | **接受**，修三处文档 + 加 load 时守卫 | 低 |
| D8 | 是否先建身份 Split/Merged 的合成故障注入基准 | **是** | 低 |

---

## 9. 未能确定（**不得**作为设计依据）

1. **OpenClaw `sessionKey` 的判别力未实测**：是否与 `workspaceDir` 一一对应、同 key 是否被不同进程复用、会话滚动时是否变化 → 决定 P3 的 ambiguous→confirmed 收敛率。
2. **带外 sqlite 写入延迟量级未知** → 决定"缺席何时才算真缺席"。
3. **h2 真实优先级缺实测**（现判据只是语言默认值）→ 需一次覆盖目标 agent 集合的 ALPN 分布采样。
4. **Rust→TS 生成的最小工具链**（ts-rs / typeshare / 小脚本）与 Turbopack/vitest 的兼容性 **未 spike** → P2 前必须先 spike，**不得先承诺具体工具**。
5. **冷构建墙钟时间未测**（`target-dir=build`）→ 决定"拆 workspace 的编译并行收益"是否成立（结论仍是不以此为由拆分，但需数据）。
6. ~~PROBE 32 KB 上限 × base64 33% 膨胀的相互作用未实测~~ **已实测**：源码上限是 256 KiB（`tls_capture.h:9`），真实 p99 16 KiB / max 70,703B；base64 在用户态流式编码，BPF 结构不变。
7. **zstd 帧边界与 HTTP chunk 边界是否对齐未实测**（逐帧独立压缩 vs 单一连续流被切开，实现复杂度差别大）。
8. **`prepare` 决赛文档（68 文件）与 `docs/prepare`（23 文件）是否纳入"必须与代码一致"** → 推荐**排除**并显式声明为竞赛材料。
9. **CamFlow 类 whole-system provenance 的具体开销数字**（56%–100%）为转述，未复核论文原文。
10. **Falco 的 rule maturity 原文未取到**（Sigma 已核实）→ 不要引用 Falco 的具体字段名。
11. "6 个场景耦合 analyzer" **无法按审计原表述复现**——实测只核实到 3 个已注册者；P4 迁移前以实测为准。
12. 220 个真实 run 的部分统计数字（`correlation_evidence` 行数、Agent 节点数、告警落身份表比例等）为**转述**，未复跑。
13. `tls_capture.c:591-624` 的 `\u%04x` 细节、`reader.rs:43-53` 的 `timestamp_ns: 0` 为**转述**，未逐行复核。
14. npm/ecapture/`frontend/out/` 的 rust-embed 隐式依赖链为转述 → 任何 CI/构建改动前须复核。

---

## 附：给下一个接手工程师的三句话

1. **先把 P0 做完再谈重构**——没有 CI 时，这份文档本身就会变成下一份"宣称与代码不符"。
2. **P1 是唯一有机会在两周内让目标环境（OpenClaw）真正可用的期**，且它触碰 Claude Code 冻结路径
   ⇒ **必须先建"无损 Claude Code 录制 fixture 回归门"**。
3. **`CapabilityUse` 的零构造说明本项目最贵的病不是"写错了"，而是"没有任何机器能断言生产者存在"**——
   `xtask verify` 与 `catalog.rs` 是这份设计里唯一真正治本的两件东西，其余都是它们服务的对象。

## 附：本设计的产出方法（可追溯）

- 8 臂 SOTA 一手来源调研（每条含 URL，最优先规范原文/内核文档/论文）
- 6 轴代码库对照审计（每条含 `file:line`）
- 3 个不同哲学的独立架构提案（OTel-first / Profile-Registry-first / Trusted-Core）
- 3 名独立评委（SOTA 一致性 / 未来演进 / 交付与风险）——**三票各选一（1:1:1）**，说明三者各有真实价值，最终合成是**明确取舍**而非平均
- **引用对抗核验**：实测抽验 60+ 条 claim（curl + 行号），确认绝大多数；一处诚实标注（LangSmith 的引用指向 JS 壳页，结论真但 URL 弱）
- 全部原始 agent 产出（含完整 URL 清单、审计明细、三份提案全文、评委评语）保存在会话工作流记录中，可按需追溯
