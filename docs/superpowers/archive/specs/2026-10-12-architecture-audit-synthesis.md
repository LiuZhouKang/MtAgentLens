# 全项目架构审计综合报告与整治方案

- 日期：2026-10-12
- 来源：三份独立审计（collector 死代码/多管线、collector 抽象质量、frontend 架构与一致性）
- 原则：**所有结论均有 `file:line` 依据**；审计员的「未能确定」项原样保留，不臆断

---

## 第 0 部分：三个结构性根因

绝大多数表象问题都源自这三个根因。**先修根因，表象会自行暴露**（因为编译器开始说话）。

### 根因 A：整个 crate 被编译两遍

```
src/lib.rs:2-5      mod framework; mod responsibility; mod server;
src/main.rs:15-17   mod framework; mod responsibility; mod server;   ← 重复声明
```

- `main.rs` 全文 **0 处** `mtagentlens::` 引用（用 `crate::`）；而 `bin/replay.rs:10-20` 用 `mtagentlens::`
- 后果：**两个互不兼容的类型宇宙**（`crate::…::AlertId` ≠ `mtagentlens::…::AlertId`，跨边界必须序列化）；**全局 static 各一份**（`SHUTDOWN_REQUESTED`、`RUNTIME_SHUTDOWN`、两组 `*_GLOBAL_METRICS`、`DIAG_*`）；85,918 行编译两次
- **它是 `dead_code = "allow"` 被迫全局打开的根因**

### 根因 B：编译器被禁言，死代码因此隐身

```
src/main.rs:1     #![allow(dead_code, unused_imports, unused_variables, unused_mut)]
Cargo.toml:52     [lints.rust] dead_code = "allow"
```

- 后果：**13 个文件（~5,092 行）从未被任何 `mod` 声明，根本不参与编译**，多年无人发现
- 审计员只能靠人工交叉引用逐条验证死代码

### 根因 C：前后端契约零护栏

```
frontend/lib/types.ts:1-2
  // Mirrors backend types from collector/src/accountability/types/
  // Verified against actual Rust serde serialization format (2026-05-23)
```

- 441 行手工镜像；**最后一次人工核对是 2026-05-23**，而 Rust 侧正在被我们的工作持续改动
- 无 codegen、无 schema 导出、无运行时校验
- 前端还有 `as any` 强转后端响应（`lib/store.ts:526,531`）——契约未被类型化的直接证据

---

## 第 1 部分：必须最先做（阻碍一切后续重构）

| # | 项目 | 依据 | 为什么阻塞 |
|---|------|------|-----------|
| **1** | **消除双编译**：`main.rs:15-17` 改用 `mtagentlens::`，恢复 `dead_code` 告警 | 抽象审计 §6.1 | 不解决它，任何"统一 static/类型"的尝试都会先在这里失败；且无法恢复编译器护栏 |
| **2** | **修 `persistence/writer.rs` 静默失败链** | 抽象审计 §4.1 | **唯一已在生产路径静默丢数据的问题**；`commit_batch` COMMIT 失败后 `pending` 不复位 ⇒ 事务状态未定义 ⇒ 连锁污染其后所有写入。整个责任追溯/回放/证据链都建立在它之上 |
| **3** | **消除意图提取双路径** | 死代码审计 §4.1 | 两条路径无条件接线、写同一 buffer；`IntentId` 是随机 UUID ⇒ 去重键永不命中 ⇒ **证据链/告警成倍重复**。任何评测都建立在重复输入上 |
| **4** | **统一 agent-id 方案** | 死代码审计 §4.2 | `MultiAgentAnomalyAnalyzer` 用 `agent-{root_pid}`，`IdentityResolver` 用 `agent-{sha256}`，**互不知晓** ⇒ 同一 agent 分裂成两个 id |

**3 与 4 正是 L2（身份）与 P2（意图）的地基。** 不先消歧，后续工作就是在歧义上盖楼。

---

## 第 2 部分：纯删除（零风险，可立即做）

### collector

| 项 | 规模 | 依据 |
|---|------|------|
| 13 个未编译文件 | ~5,092 行 | 抽象审计附录 |
| `responsibility/server/` 整树未挂载（`serve_request` 零调用） | 2,357 行 | 死代码审计 §4.3 |
| 未接线的 pipeline 契约/编排框架 | ~3,300 行 | 死代码审计 §3 |
| `claims/alert.rs` + `persistence.rs` + `projection.rs` | ~607 行 | 死代码审计 §7 |
| `jsonl_writer.rs`（CLAUDE.md 声称的"双写"实际早已不写 JSONL） | — | 死代码审计 §4.4 |
| `CausalTraceAnalyzer` 悬空注释（**4 处**，用户清单只列了 3） | — | 死代码审计 §5 |

### frontend

| 项 | 规模 | 依据 |
|---|------|------|
| `world.json` | **-440 KB 发布体积** | 前端审计 §1.1 |
| `public/@vite/`（Storybook 产物**被 git 跟踪**并打进 rust-embed） | — | 同上 |
| `bento-grid.tsx` + `spotlight.tsx`（互相唯一引用） | ~200 行 | 同上 |
| 第二个命令面板（动作全是 no-op） | 73 行 | 前端审计 §1.2 |
| 整套 Storybook（`stories` glob 匹配 0 文件） | — | 前端审计 §1.4 |
| **19 个未使用 npm 依赖**（逐个 grep 验证） | — | 前端审计 §1.3 |

### ⚠️ 删除前需产品决策（不能机械删）

- `context_pollution.rs` / `credential_leak.rs` —— **CLAUDE.md 与 `pipeline_contract.rs:280` 都宣称它们是核心检测能力**，但实际未编译。需先定口径：**实现它** 还是 **下架它**
- `responsibility/server/**` —— 代码完整度像"已完成待接线"而非废弃；且其 `/api/handoff/*` 在活的 web.rs 中不存在。需确认是否为未完成特性

---

## 第 3 部分：语义已腐化（必须先定标，不能机械合并）

| 项 | 腐化表现 | 依据 |
|---|---------|------|
| **`MatchLevel` 双份 + 三张矛盾分值表** | L4Basename = **0.50 / 0.50 / 0.15**（3.3 倍差），无人知道哪张权威 | 抽象审计 §2.3 §3.3 |
| **`resolve_alerts_db_path` 演化出相反语义** | `web.rs:2691` 选 `.alerts.db`；`bin/replay.rs:274` 选**非** alerts 库 | 抽象审计 §2.4 |
| **`rule_engine.rs` fail-open** | `_ => OperationCategory::FileRead` 使 `Context`/`ToolCall`/`LlmConversation` 资源的越权被归为**最轻类别** | 抽象审计 §3.4 |
| **`HandoffInjection` 按场景名特判** | `handoff_injection.rs:488 if pattern.scenario_id != "subagents-identity-confusion"` | 抽象审计 §6.3 |
| **场景语义编译进"通用"检测器** | 6 个 analyzer 的生产判定分支硬编码 Benchmark 场景 id/文件名/标记串 | 抽象审计 §6.3 |

> 这一部分是**泛化能力的天花板**：对定位"世界顶级**通用** agent 监测产品"的项目，检测逻辑与自建 Benchmark 强耦合意味着换个数据集就失灵。

---

## 第 4 部分：架构统一（需设计，排期做）

| 项 | 现状 | 依据 |
|---|------|------|
| **`Analyzer` trait 状态管理 5 种写法** | A 全量 collect（破坏流式）／B `Arc<Mutex>`／C `Arc<RwLock>`／D `Arc<Atomic>`／E 全局 static。且**同一文件作者已注释承认锁是多余的**，而另一文件用 collect 绕开 `&mut self`，两者结论直接矛盾 | 抽象审计 §2.1 |
| **`Arc<Mutex>` 全是过度同步** | `AgentRunner::run`（`agent.rs:66-72`）是顺序 for 循环 ⇒ analyzer 间无并发。55 处 `Arc<Mutex>` + 6 处 `Arc<RwLock>` 保护的并发**不存在**，代价是 49 处中毒 panic 面 | 抽象审计 §5.2 |
| **`SharedStateStore` trait 写成后被注释掉** | `analyzers/mod.rs:45` —— 正是为消除上一条而生，20+ analyzer 全部绕过 | 抽象审计 §2.2 |
| **`server/web.rs` god-file** | 8,563 行 / ~50 个 handler 平铺：路由匹配 + 静态资源 + SSE + REST + 反序列化 + 4 个错误类型 | 抽象审计 §1 |
| **`runtime.rs::trigger()` 717 行上帝函数** | 内含 0~6 共七个阶段 | 抽象审计 §1 |
| **`globals.css` 28,673 行 / 1,049 处 `!important` / 7 组同名覆盖块** | 层叠被反复对抗而非收敛 | 前端审计 §3.3 |
| **重复实现（前端）** | 时间格式化 3~5 套、路由解析 **4 份逐字复制**、severity→tone 4 套、severity→color 4 套（半硬编码 hex）、`TraceEventKind` 表 2 套（注释谎报"已统一"）、排版常量 7 份 | 前端审计 §2 |
| **newtype 体系一半被绕过** | `types/ids.rs` 有 `newtype_id!` 宏，但 `support_types.rs`(25)/`web.rs`(24)/`data_contract.rs`(13) 仍写裸 `String` ⇒ 把 alert_id 传进 run_id 不报错 | 抽象审计 §3.1 |
| **错误处理四套体系混用** | `Box<dyn Error>` / `anyhow` / 自定义 enum / 裸 `String`；`thiserror` 未引入 | 抽象审计 §2.5 |
| **CORS 自相矛盾** | `web.rs:219-221` 显式说明"实时流含明文，禁止 `*`"，而**同一文件**的 `json_response:3593` 等四处的 `*` | 抽象审计 §6.5 |
| **硬编码绝对路径** | `main.rs:1350` `PathBuf::from("/home/lzk/MtAgentLens")` —— PropertyAccessAudit 的 workspace root，**换机器即失效** | 抽象审计 §6.2 |
| **配置 schema 与 example 不符** | `observer_llm.toml.example` 是 `[llm]` 表格格式，而 `ObserverProviderConfig` 要求顶层扁平 ⇒ **照抄 example 必失败** | 抽象审计 §6.4 |
| **`lib/types.ts` 契约护栏** | 见根因 C | 前端审计 §5 |

---

## 第 5 部分：与进行中工作的关系

我们正在做的是：**P0 已完成**（数据地基）、**P1 已完成**（带外读取）、下一步 P2（意图注入）/ L2（身份改造）。

**第 1 部分的第 3、4 项与本工作直接重叠**：
- 意图双路径 ⇒ 影响 P2
- agent-id 双方案 ⇒ 影响 L2

**建议顺序**：

```
第 1 部分（1→2→3→4）   ← 先修根因与消歧
      ↓
P2 意图注入 / L2 身份改造   ← 在干净地基上做
      ↓
第 2 部分纯删除            ← 缩小爆炸半径
      ↓
第 3 部分定标 + 第 4 部分统一
```

> 注：第 2 部分的纯删除**技术上不阻塞**任何事，可在任意时机插入；但**越早做，后续重构的爆炸半径越小**。

---

## 附：审计员明确标注的「未能确定」项（不得当作事实）

1. 意图双路径的**实际重复率**（需运行时抓包统计）
2. `persistence/mod.rs:25-27` 关于旧 `schema.rs` 遮蔽的注释是否仍准确
3. 未注册 Analyzer 是「待接线」还是「已废弃」——需产品确认
4. `Arc<Mutex>` 是否在某处依赖真实并发（各 Runner 内部与 analyzer 链是否并发消费同一流，未逐一确认）
5. `responsibility/server/**` 是否为近期计划挂载
6. 三张 `MatchLevel` 分值表中哪张是权威
7. `app/globals.css` 中哪些 `!important` 是当代偿债、哪些仍必要（需运行时逐条验证）
8. `lib/types.ts` 中 9 个零外部引用导出是「仅内部使用」还是「真死」
9. `public/data/db-sample.sqlite` 等回退数据是否该存在于发布产物
