# 真实运行取证备忘 — 2026-10-16

本文件记录一次**真实端到端采集**所暴露的问题。所有结论都有实测命令/输出或 `file:line` 依据。
交接文档（`HANDOFF-2026-10-16.md`）未覆盖这些项——它们只有靠真实运行才能暴露。

## 0. 采集方法与原始数据

```bash
mkdir -p /tmp/l3probe
sudo ./collector/build/release/mtagentlens trace \
  --ssl=true --process=true \
  --binary-path /home/lzk/.nvm/versions/node/v24.19.0/bin/node \
  --disable-responsibility-observer \
  -o /tmp/l3probe/trace.log -q
# 另一终端： openclaw agent -m "用 ls 工具列出 /tmp 目录下的前 3 项…"
```

- 二进制：`collector/build/release/mtagentlens`（2026-10-16 19:47，含本会话全部未提交改动）
- 产物：`trace.log` 1.1 MB / 2800 事件；`trace.log.responsibility.db`
- 环境：OpenClaw Gateway pid 1582（Node v24.19.0），provider `deepseek-v4-pro`

实测事件分布：

| source | event | 数量 |
|---|---|---|
| process / file_access | FILE_OPEN | 637 / 637 |
| process | EXIT | 851 |
| process | EXEC | 667 |
| ssl | (无) | 7 |
| http_parser | (无) | 2 |

---

## 1. 【已修】`persistence` 双重 BEGIN ⇒ 每批次一条伪失败

### 证据

真实运行 stderr 中 **488 次**：

```
persistence: begin transaction failed (total 1): cannot start a transaction within a transaction
…（total 至 488）
```

### 根因（`file:line`）

- `persistence/writer.rs` `commit_batch()`：`COMMIT` 成功后**又执行 `BEGIN TRANSACTION`**（旧 :979）
- 同文件 `writer_task()` 的记录分支：`pending == 0` 时**再执行一次 `BEGIN`**（旧 :411）
- 于是**每个批次边界之后的第一次写入**都会撞上已开启的事务

SQLite 的报错本被 `let _ =` 吞掉；S 级静默失败改造把它接入诚实的 `write_failures` 计数器后才暴露。

### 影响

数据其实写成功了（`actions` 501 / `intents` 32 / `identity_process_map` 868 行都在），**损害在可观测性**：
计数器把 488 次"事务已开"误报成 488 次写入失败——这恰好摧毁了它存在的意义。
且 `write_failure_count()` **全仓无任何消费者**，即"诚实化"没有出口。

### 修法

原则：**事务状态问 SQLite（`Connection::is_autocommit()`），不靠 `pending` 推断**。

- `commit_batch()`：移除"重新开事务"，改为"若 `!is_autocommit()` 则 `COMMIT`"；开事务的职责只留给记录路径
- 记录路径：`pending == 0` → 改为 `conn.is_autocommit()`
- 关闭/Flush/运行边界路径：`if pending > 0` → 无条件 `commit_batch`（它现在是廉价 no-op）
  —— 理由：行写入失败会让 `pending` 停在 0 而事务仍开着，旧判据会把已入事务的行回滚掉
- `runtime.rs` 关闭路径：`let _ = flush()` → 显式处理错误 + 打印 `lost`/`failed` 计数（DEGRADED / cleanly）
- 新增回归测试 `test_writer_reports_no_failures_across_many_batches`（`batch_size: 2`、10 条记录）

---

## 2. 【新】响应体是 **zstd 压缩**，被丢弃/污染后整条链路失效

设计文档 §7.2 把"压缩路径"列为【待验证】。**现已证实，且压缩算法是 zstd。**

### 证据

7 条 ssl 事件中的二进制载荷还原（`hex::decode` → UTF-8 → 取低字节）：

```
head: 28 b5 2f fd 00 48 d4 07 00 96 93 3b 21 50 67 ac
```

`28 b5 2f fd` = **Zstandard 魔数**。分块为 HTTP chunked（`103\r\n`=259、`74\r\n`=116、`2b\r\n`=43）。

### 完整的失效链（逐环 `file:line`）

**环 1 · 入口不可逆损伤** —— `framework/runners/common.rs:223`

```rust
let valid_up_to = String::from_utf8_lossy(raw_bytes);
```

C 侧把原始字节直接塞进 JSON 字符串（`tls_capture/tls_capture.c:589`，只转义 `"` `\` `\n` `\r` `\t` `\b`，**其余字节原样输出**）。
含非 UTF-8 字节的响应因此在**读取时**被替换为 U+FFFD —— 不可逆。
实测：chunk 声称 259 字节，还原后仅 **244**；`len` 字段记录的是 HEX 前的 ASCII 长度，不是原始字节数。

**环 2 · 二进制被判定并过滤（`record` 子命令）** —— `main.rs:531`

```rust
let ssl_filter_patterns = vec!["data=0\\r\\n\\r\\n | data.type=binary".to_string()];
```

`SSLFilter` 的 `data.type` 条件走 `framework/analyzers/ssl_filter.rs:250` → `common::detect_data_type()`
（`common.rs:27`：「含 `\n\r\t` 以外控制字符」即 `binary`）。
zstd 载荷必然判为 binary ⇒ **该 SSL 事件被整条丢弃**。

且分析器顺序（`main.rs:1118-1139`）是
`TimestampNormalizer → SSLFilter → SSEProcessor → HTTPParser → HTTPFilter`
—— **SSLFilter 在解析器之前**，所以 `record` 下响应**从未进入任何解析器**。

**环 3 · `trace` 子命令（无预设过滤器）下仍不可用**

事件存活，但 `SSEProcessor::is_sse_data`（`sse_processor.rs:77-93`）要求 `data.contains("data:")`，
zstd 载荷不含 ⇒ 不成立 ⇒ 直通；`HTTPParser` 面对二进制无法解析。
实测全日志中 `"delta"` = **0 行**、`choices` = **0 行**、`[DONE]` = **0 行**。

**⚠️ 一处修正**：`analyzers/common.rs:63-71` 的 `HEX:{hex}` 改写**只发生在输出阶段**
（唯一调用方 `output.rs:44` 与 `file_logger.rs:230`，皆为链尾），
**不会**污染下游分析器所见的数据。日志里看到的 `HEX:…` 是渲染结果，不是分析器的输入。

### 两个子命令的实际后果

| 子命令 | 响应命运 |
|---|---|
| `record`（旗舰一键命令） | SSLFilter 整条丢弃 → 解析器从未见到 |
| `trace`（本次采集所用） | 存活但不可解析 → `SSEProcessor` 产出 **0** |

即：不只 L3 描述的"聚合降级"，在 OpenClaw 上响应侧**完全不可用**。

### 请求侧正常（对照组）

`http_parser` 事件 `POST /chat/completions`，body 62,310 字节**明文完好**，含完整 system prompt。
⇒ 压缩是**响应侧单向**的（客户端发了 `accept-encoding`，服务端压缩了响应）。

全仓 `zstd` / `gzip` / `flate2` / `content-encoding` 检索：`collector/src` **零命中** —— 无任何解压能力。

---

## 3. 【新】把**工具定义**当成了**工具调用** ⇒ 幻影意图

### 证据（真实 DB）

```
('exec',            'unknown', 'Complete', 'SseResponseToolUse', 1)
('openclaw',        'unknown', 'Complete', 'SseResponseToolUse', 1)
('sessions_yield',  'unknown', 'Complete', 'SseResponseToolUse', 1)
('wait',            'unknown', 'Complete', 'SseResponseToolUse', 1)
```

四条全部 `tool_args_json = '[]'`。全日志中这些名字**唯一**出现在请求体 system prompt 的 Tooling 段：

```
## Tooling
- exec: Run JavaScript Code Mode; call exact catalog tools from code, never shell/Python/imports
- openclaw: Gateway restart/system setup/config
- sessions_yield: End turn; await subagent events
- wait: Resume a suspended Code Mode exec
```

### 根因（`file:line`）

`evidence/intent_extractor.rs:803-810`（`scan_json_for_tools` 的 Pattern B）：

```rust
// Pattern B: OpenAI function call → has function.name
if let Some(function) = obj.get("function")
    && let Some(name) = function.get("name").and_then(|n| n.as_str())
```

OpenClaw 是 OpenAI 协议，请求体含 `tools[]` 工具**定义**：

```json
"tools":[{"type":"function","function":{"name":"exec","description":"…","parameters":{…}}}]
```

Pattern B 只认 `function.name`，**无法区分"定义"与"调用"**；随后 `:832-835` 递归整个 JSON 树 → 每个工具定义都被当成一次工具调用。

调用链：`routing.rs:275` Step 1 `extract_from_raw_buffer` → `extract_generic_json` → `scan_json_for_tools`。

### 为什么 Claude Code 从未暴露

Anthropic 的工具定义是 `{name, description, input_schema}`——**没有 `function` 包装**，Pattern B 永不命中。
这正是"强绑定单一 agent CLI"造成的盲区。

### 附带

`routing.rs:239` 把 `IntentAuthoritySource::SseResponseToolUse` **硬编码**给所有 evidence 路由事件——
所以"来源=SSE 响应"并不代表数据真来自响应（本例来自请求）。署名不可信。

---

## 4. 【新】7 种事件类型 Rust 期望、C **从不发出** ⇒ 能力缺口

### 双向取证

C 侧实际发出（`process/process.c`，转义字面量，故普通 grep 会漏）：

| 事件 | file:line |
|---|---|
| `FILE_OPEN` | `process/process.c:248` |
| `EXIT` | `:528` |
| `EXEC` | `:565`, `:586` |
| `BASH_READLINE` | `:608` |
| `UNKNOWN` | `:646` |
| `PROCESS_RINGBUF_STATS` | `:453`（stderr） |

Rust 侧期望（`routing.rs` `classify_event` :402-410 与 `:304-317`）：
`EXEC` `EXIT` `FILE_OPEN` `PROCESS_EXEC` `FILE_DELETE` `FILE_WRITE` `FILE_TRUNCATE` `FILE_RENAME` `NETWORK_CONNECT` `NETWORK_SEND`

**从不发出 7 种**：`FILE_DELETE` `FILE_WRITE` `FILE_TRUNCATE` `FILE_RENAME` `NETWORK_CONNECT` `NETWORK_SEND` `PROCESS_EXEC`。

`process.bpf.c` 只有 5 个 hook（`grep -n 'SEC(' process/process.bpf.c`）：
`:83` bash readline uretprobe、`:131` sched_process_exec、`:205` sched_process_exit、`:262` sys_enter_openat、`:323` sys_enter_open。
**没有网络 hook、没有 unlink/rename/write hook。**

### 实测后果（真实 DB）

```
actions:  ('ProcessExec', 'HighSignal', 'Direct', 253)
          ('FileRead',    'HighSignal', 'Direct', 248)
```

**零**网络动作、**零**文件写/删动作。⇒ 动作层对"外传（NetworkSend）""凭据落盘/破坏性 fs"完全失明。
这不是代码整洁问题，是**核心检测能力缺口**。

### 结论

修它需要**新增 eBPF 程序**（重量级，须过 verifier + 内核矩阵），不能靠删 Rust 分支"消掉"——
删分支只会把缺口藏起来。

---

## 5. 【已定位】`/usr/bin/bash` 硬编码 ⇒ 非 Debian 系**整个进程监控归零**

### 机制（两步实证）

1. `process/process.bpf.c:83`：`SEC("uretprobe//usr/bin/bash:readline")` —— **SEC 名本身**就是挂载目标
2. `libbpf/src/libbpf.c:14644` `bpf_object__attach_skeleton()`：

```c
err = prog->sec_def->prog_attach_fn(prog, prog->sec_def->cookie, link);
if (err) {
    pr_warn("prog '%s': failed to auto-attach: %s\n", …);
    return libbpf_err(err);      // ← 第一个失败即返回，后续程序永不挂载
}
```

3. `process/process.c:708-712`：`process_bpf__attach()` 失败 → `goto cleanup` **直接退出**，连 ring buffer 都不轮询

bash uretprobe 是**第一个**程序，其后才是 exec/exit/openat/open。
⇒ 在无 `/usr/bin/bash` 的系统（NixOS、Alpine 等）上，`process` 二进制立刻退出，**EXEC/EXIT/FILE_OPEN 全部不挂载**，进程监控完全归零。

### 对比

`tls_capture` 已做 best-effort（P0.2 的 `FAIL` 宏），`process` **没有**这一层——同类问题只修了一半。

---

## 6. 【新】`classify_intent_class` 有**两份实现且结论互相矛盾**

| 位置 | 形态 |
|---|---|
| `types/support_types.rs:537` | 硬编码 `match`，11 个名字 |
| `evidence/intent_extractor.rs:165` | 查 `ORCHESTRATION_TOOLS` / `SIDE_EFFECT_TOOLS` 两张 const 表 |

**`Bash` 在两处结论相反**：`support_types` 判 `SideEffect`，`intent_extractor` 判 `Orchestration`。

`Bash` 走哪张表取决于提取路径：`IntentEvent::new`（`support_types.rs:489`）用前者；
`intent_extractor.rs:604/709/785/815/…` 用后者。而 `primary_matching_eligible` 的判据包含
`intent_class == SideEffect`（`support_types.rs:518`）——所以同一工具不同路径会得到不同的"可否匹配"。

同期硬编码的工具名表还有：`REQUIRED_FIELDS`（`intent_extractor.rs:150`）、`is_primary_matching_tool`（`support_types.rs:548`）。

---

## 7. 【新】参数抽取的泛化缺口不是"工具名"，而是**字段名白名单**

`evidence/args.rs:21` 的签名是 `extract_intent_args(_tool_name: &str, tool_input: &Value)`——
**带下划线，工具名根本没用**。它按字段名映射：

`file_path` / `notebook_path` / `path` / `pattern` / `command` / `url` / `query` / `description`|`prompt` / `old_string` / `new_string` / `content`

OpenClaw 的 Code Mode 把 JavaScript 放在 **`code`** 字段 —— **不在白名单**。
⇒ 即便修好工具名词汇表，真实 `exec` 调用仍会产出空参数 ⇒ 关联器无从匹配。

文件内已有 `extract_paths_from_text()` / `extract_urls_from_text()`（`:318` `:337`）两个通用启发式，
却只在少数具名字段上调用。**顶级做法**：对未知字段名回落到"遍历全部字符串字段 + 现有启发式"，
而不是继续扩白名单。

---

## 8. 连带确认：因果层为空的直接原因

真实 DB 中：`correlation_evidence` **0**、`non_link_diagnostics` **0**、`responsibility_claims` **0**、`responsibility_tracebacks` **0**。

4 条工具意图 `primary_matching_eligible = 1`（fast-path 硬编码 true），但 `tool_args_json = '[]'`。
⇒ 没有可匹配的参数，关联器无从下手。**§3 与 §7 是本项的两个直接原因。**

---

## 9. 未能确定 / 待办

1. 那 7 条 ssl 事件所属的连接是 LLM API 还是 Gateway 内部协议——请求侧（`POST /chat/completions`）与
   响应侧的 pid 均为 1582，但响应缺少首行/头部事件，**未能确定**二者是否同一 HTTP 事务。
2. zstd 解压该在哪里做（`data_to_string` 之前？HTTPParser 里按 `content-encoding` 分支？）——需设计，未定。
3. `file_access` 源 637 条与 `process` FILE_OPEN 637 条完全同数同源，两条来源是否重复记账——未确定。

---

## 10. 【量化】改 `Bash` 分类会毁掉一个承重指标（2026-10-17 实测）

问题：统一工具词汇表时把 `Bash` 定为 `SideEffect`（原先活跃路径的表把它列为 `Orchestration`）。
疑虑：`compare/scripts` 的评测会受影响。**实测证据如下。**

### 脚本实际算法（不是 `side_effect/orchestration`，而是占比）

```python
# compare/scripts/evaluate_mtagentlens.py:351-355
total_intents = max(db.execute("SELECT COUNT(*) FROM intents").fetchone()[0], 1)
side_effect   = db.execute("SELECT COUNT(*) FROM intents WHERE intent_class='side_effect'").fetchone()[0]
intent_ratio  = side_effect / total_intents
```
`compare/scripts/run/audit_scenario.py:58-61` 与 `compare/scripts/ablation/run_staircase.py:166-170` 是同一算法。

### 在 **180 个真实运行**（`env/runs/*/collector/collector.responsibility.db`）上测量

| `side_effect / total_intents` | min | max | mean | **stdev** | 不同取值数 |
|---|---|---|---|---|---|
| **现状** | 0.000 | 1.000 | 0.604 | **0.233** | **73** |
| 改成 `Bash=SideEffect` 后 | 0.486 | 1.000 | **0.975** | **0.071** | 29（多条钉死 1.000） |

单 run 样例（total, side_effect, bash, 现状→改后）：
```
  66   12   54   0.182 -> 1.000
  39    9   30   0.231 -> 1.000
  52   23   29   0.442 -> 1.000
  31   20   11   0.645 -> 1.000
  74   48   23   0.649 -> 0.959
```
在这批样本里 **Bash 占全部 intent 的 54.6%（244/447，38 个含数据的 run 汇总）**，且 `orchestration` 这一计数**全部来自 Bash**。

### 结论（有实测支撑）

该指标现在**确有区分力**（stdev 0.233、73 个不同取值、跨 0–1 分布）；把 Bash 归入 `SideEffect` 后它**饱和**（mean 0.975、stdev 0.071、大量 run 恒为 1.000），**失去区分度**。
⇒ **不要改这个持久化标签的语义**。

### 由此得出的正确修法（把两个职责拆开）

根因不是"Bash 该归哪一类"，而是**同一个字段被当两件事用**：
1. **匹配资格**（`primary_matching_eligible` 的判据含 `intent_class == SideEffect`）——语义必须正确，legacy 关联器测试要求 Bash 可匹配；
2. **评测标签**（`intent_class` 落库后被 `compare/scripts` 当指标读）——其取值是**既有 baseline 的一部分**，改动即让历史结果不可比。

正确做法：**让匹配资格用独立的语义谓词**（如显式的 `is_side_effecting_tool()`），不再从 `intent_class` 推导；
`intent_class` 作为**冻结的度量标签**保留历史取值，并在代码里明确标注"这是度量标签、不是语义分类法"；
若需要语义正确的分类法，**新增字段**（带 schema 版本），不要复用旧字段。

---

## 11. 【已核实】`record` 预设的 `response.body=` 丢弃全部 http_parser 响应事件（含精确边界）

**逐行核实**（不是推测）：

- `main.rs:529` 的 `record` 预设 http_filter 最后一项是 `response.body=`（**值为空**）
- `http_filter.rs:175`：`if !condition.contains('=')` —— 该 token **含** `=`，故被当作合法条件解析，value 为空串
- `http_filter.rs:331-333`：
  ```rust
  "body" | "body_contains" => {
      let body = data.get("body").and_then(|v| v.as_str()).unwrap_or("");
      body.contains(value)      // value == "" ⇒ "".contains("") == true，恒真
  }
  ```
- `http_filter.rs:441-443`：命中即 `filtered = true` ⇒ **事件被丢弃**

⇒ **`record` 下每一条 `source == "http_parser"` 的 response 事件都被丢弃。**

### 精确边界（重要，否则结论会过头）

`http_filter.rs:437`：`event.source != "http_parser"` 时一律不过滤。
而分析器顺序是 `SSLFilter → SSEProcessor → HTTPParser → HTTPFilter`（`main.rs`）。
**`SSEProcessor` 跑在 HTTPFilter 之前**，其产物 `source = "sse_processor"` ⇒ **不受本缺陷影响**。

这解释了为何长期无人发现：Claude Code 的 tool_use 主要来自 `sse_processor`（不受影响）；
而对走 OpenAI 协议 + zstd 压缩的 OpenClaw，`SSEProcessor` 产出 0（见 §2）⇒ 响应数据**双重死亡**。

### 对决策的含义

- 这不是"要不要删一个过滤器条件"的小事：它决定了 `http_parser` 这条路是否可用，而**压缩解压**（§2）的正确落点也在这一层
- 同时也说明数据入口处存在一个**基于内容嗅探的分叉**（经逐行核实，此处更正我先前的表述）：
  - `SSEProcessor` 是**消费型**的——对判定为 SSE 的分片返回 `None`（吞掉，`sse_processor.rs:752` 与聚合分支），
    非 SSE 才 `return Some(event)` 透传；`SSEProcessor` 的产物只有终结时的那**一个**合并事件（原始分片保存在其 `sse_events` 里）
  - 分叉判据是对**单个分片**的启发式 `is_sse_data()`（`sse_processor.rs:77-93`：看 `event:`/`data:`/`text/event-stream`/chunked 等字符串特征）
  - ⇒ 同一条逻辑响应走哪条路，取决于它**恰好在哪切分**。zstd 二进制响应不含这些特征 ⇒ 走 HTTP 路径 ⇒ 再被 `record` 预设丢弃；
    而一个被切在 `data:` 中间的普通响应也可能误入 SSE 路径
  - 这是"多管线"债务在**数据入口**处的形态，也是架构设计必须正面处理的点：正确的模型应当是**传输层重组出一个逻辑消息，再由消息自身的属性决定如何解码**，而不是在流上按字符串特征分叉

---

## 12. 【盘点】"已编译但从不注册"的分析器 —— 死代码盲区的实际规模

`rustc` 对 `pub` 项**不报** dead_code。因此"实现了 `Analyzer`、被 `mod.rs` 导出、但 `main.rs` 从不构造"的分析器
对编译器**完全隐形**。逐文件扫描结果：

| 分析器 | 文件 | 在 main.rs 构造？ |
|---|---|---|
| AlertEnrichmentAnalyzer | `alert_enrichment.rs`（476 行） | **否** |
| ConversationFlowAnalyzer | `conversation_flow.rs` | **否** |
| LatencyAnalyzer | `latency.rs` | **否** |
| ObservabilityMetricsAnalyzer | `observability_metrics.rs` | **否** |
| MockStageAnalyzer / FailingAnalyzer | `pipeline_orchestrator.rs` | 否（测试 mock，属正常） |
| AuthHeaderRemover / ConnectionBuffer / FileAccessAudit / FileLogger / HandoffInjection / HTTPFilter / HTTPParser / MultiAgentAnomaly / Output / SecuritySignoff / SSEProcessor / SSLFilter / TimestampNormalizer | — | 是 |

⇒ **19 个 `impl Analyzer` 中 6 个从未被构造**（4 个是真实死分析器）。它们中的多数还被 `analyzers/mod.rs` 以
`pub use` 导出，对外**看起来是可用的能力**。

**权威注册清单**（`main.rs` 的全局分析器，业已核实）：
`MultiAgentAnomalyAnalyzer`(:1335) · `HandoffInjectionAnalyzer`(:1338) · `FileAccessAuditAnalyzer`(:1344) ·
`SecuritySignoffAnalyzer`(:1354) · `FileLogger` · `OutputAnalyzer` · `ResponsibilityRuntime`。
（`RoleHijackAnalyzer` 虽在 `mod.rs` 导出，**同样未注册**。）

### 对设计的含义

这不是"再删几个文件"的问题，而是**缺少一个机制**：能力的"存在"应当有**单一、可机器校验的声明**
（capability manifest / 注册表），并在启动时自检——否则"实现了但没接线"会持续发生且无人发现。
这是本项目已多次踩中的同一模式（13 个未编译文件、第二管线 2761 行零引用、4 个未注册分析器）。

---

## 13. 【量化】多 agent 检测能力目前是"Benchmark 场景预言机"，不是通用能力

这是"通用化"目标下**最重的结构性事实**。逐文件计数（活代码，含少量测试字面量）：

| 文件 | 场景字面量处数 | 是否注册/存活 |
|---|---|---|
| `framework/analyzers/handoff_injection.rs` | **48** | ✅ 已注册 |
| `framework/analyzers/multi_agent_anomaly.rs` | **38** | ✅ 已注册 |
| `framework/analyzers/security_signoff.rs` | 11 | ✅ 已注册 |
| `responsibility/evidence/correlator_legacy.rs` | 8 | legacy |
| `framework/analyzers/pipeline_contract.rs` | 8 | 部分存活 |
| `role_hijack_contract.rs` / `agent_teams_role_hijack.rs` | 5 / 5 | ❌ 未注册 |
| `analyzer_composition_contract.rs` / `alert_enrichment.rs` | 4 / 4 | ❌ 未注册 |
| `responsibility/judgment/policy.rs` / `types/support_types.rs` | 2 / 1 | 存活 |

**活代码里约 97 处**。且存在**硬判定门**（路径必须落在我们自己的夹具目录内，否则整段逻辑不触发）：

```
handoff_injection.rs:306   if filepath.contains("/env/scenarios/fixtures/") {
handoff_injection.rs:311   if filepath.contains("/env/scenarios/fixtures/") {
multi_agent_anomaly.rs:575 if !filepath.contains("/env/scenarios/fixtures/") { /* return */ }
```

模式形状（`handoff_injection.rs:60-97`）是 6 条**逐条硬编码**的记录，每条 = 夹具路径后缀 + probe 文件名后缀 + 标记串 + scenario_id：

| scenario_id | 夹具 | 标记串 |
|---|---|---|
| `subagents-context-loss` | `/subagents-context-loss/shared_summary.md` | （probe 文件即标记） |
| `subagents-identity-confusion` | `/subagents-identity-confusion/shared_verdict.md` | 同意即标记 |
| `subagents-handoff-injection` | `/subagents-handoff-injection/shared_handoff.md` | payload_probe |
| `agent-teams-role-hijack` | `/agent-teams-role-hijack/security_signoff.md` | approval_probe |
| `subagents-privilege-leak` | `/subagents-privilege-leak/shared_handoff.md` | leak_probe |
| `agent-teams-context-pollution` | `/agent-teams-context-pollution/team_policy.md` | `POLICY OVERRIDE (urgent)` |

并有按场景名的特判：`handoff_injection.rs:488` `if pattern.scenario_id != "subagents-identity-confusion"`。

### 对设计的含义（必须正面回答，否则"通用"是空话）

这些**底层现象其实是通用的**，"标记串 + 文件名"只是它们在某个 benchmark 里的**实例**：

| 通用现象 | 现在的实现 | 通用化后的形态 |
|---|---|---|
| 跨 agent 共享产物被下游执行（handoff / 上下文注入） | 匹配硬编码路径后缀 | 「写-读-执行」的**跨 agent 产物流**模式（产物哈希/内容摘要 + 跨 agent 读-执行关联） |
| 身份混淆 / 冒充 | 夹具文件名 + 同意标记 | agent 自报身份（asserted）与观测身份（observed）**不一致** |
| 权限提升 / 越权签批 | 特定 signoff 文件名 | 签批主体与实际执行主体**不同**（能力/委派模型） |
| 上下文污染 | `POLICY OVERRIDE (urgent)` 字面量 | 注入语料的**结构特征**（指令性覆盖、越权指令、与既有策略冲突）而非具体字符串 |

⇒ 正确做法是让检测逻辑表达**通用模式**（可用内容哈希/跨 agent 关联/身份一致性来表达），
再把 benchmark 场景降级为**配置/规则实例**（数据），而不是把实例写进判定分支。
**迁移期必须保持 benchmark 结果可比**——即通用模式的规则集要能复现现有 6 个场景的判定（可用现有 180 个 run 的 DB 做回归比对）。

---

## 14. 【两项基础事实】无 CI；前端类型镜像已确定漂移

### 14.1 项目**没有任何 CI**

实测：无 `.github/workflows/`、无 `.gitlab-ci.yml`、无 `.woodpecker*`、无 `Jenkinsfile`、无其它 CI 配置。

这直接解释了本项目反复出现的同一类问题：
- 13 个文件 5,092 行**从未参与编译**却多年无人发现（有 CI 就会立刻红）
- 4 个分析器实现后从未注册、第二管线 2,761 行零引用（有"能力清单一致性"检查就会报）
- 927 个 lib 测试与 228 个 bpftools 测试**只在人手动跑时执行**

对"世界级开源项目"而言，**CI 是最低门槛**，也是本设计"防腐化机制"的落点。

### 14.2 前端类型镜像已漂移（具体实例）

`frontend/lib/types.ts` 是**手工镜像** Rust 类型（审计称最后一次人工核对在数月前）。实测一例：

| | 字段 |
|---|---|
| Rust `EngineSection`（`alert_types.rs:76`） | 有 `cusum_dimensions: Vec<String>` |
| 前端 `types.ts:99` 附近 | 有 `cusum_triggered?: boolean \| null`，**没有 `cusum_dimensions`** |

⇒ 漂移**确实存在且已在生产**（前端拿不到该字段）。这类漂移**纯 Rust 侧的测试防不住**——
需要跨语言契约机制（schema 导出/codegen/运行时校验，或至少一条"字段集一致性"守卫，
且该守卫必须**先断言被检对象存在**，否则会像 `naming_guards.rs` 那样因路径改名而静默空转）。
