# P1 字节无损 + 响应链复活 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 让 TLS 载荷从内核到分析器不再被静默损坏：C 侧加法式 `data_b64`、Rust 入口按字节读取、`PayloadNormalizer` 解出 zstd/chunked 响应，并且 `record` 不再丢弃响应。Claude Code 冻结路径每一步都必须保持 `verify.sh` 全绿。

**Architecture:** 加性 `data_b64`（`data` 降级为显示字段）；入口把新旧两种线格式统一归一化为 `data_b64`；`PayloadNormalizer` 在 `SSLFilter` **之前**按连接缓冲完整 HTTP 消息，复用 P0.5 的 `normalize::decode_message`，标记原始分片为 `partial`（下游解析器跳过），完成时发出唯一一条解码后的合成事件。

**Tech Stack:** eBPF C、Rust 2024、`base64 0.22`、P0.5 `framework/normalize`、tokio。

## Global Constraints

- 不提交；commit message 由用户定稿。
- `./scripts/verify.sh` 必须始终 12/12 全绿；预算 295 / 26 / 67 / 59 只许降。
- 不改 `probe_SSL_data_t` / 任何 BPF 代码；base64 只在用户态 `print_event` 流式完成。
- 不把 base64 写进 `data`；不删除 `data`；`data_b64` 只做加法。
- 不在 `ConnectionBuffer` / `HTTPParser` 之后读 `data_b64`。
- 不用 `make tls_capture`，用 `make .profile/bin` … 准确说：`make -C bpftools .output/bin/tls_capture`。
- 不做 worktree；全部内联。
- **P1 边界**：TLS 字节无损；stdio 仍是 `json_print_escaped` 有损（`stdiocap.c:219`），本阶段只记录不修。
- **不在 P1 做 outofband**（评审 12）：需要改共享枚举/身份模型，且不推进 P1 验收；归入 P3。
- **设计文档 P1 的“drop 计数 / coverage_contract / PROCESS_EXEC”不在本计划**：归入 P2/P3，本文件不静默宣称覆盖。

## 已完成的 T0（实测，2026-10-17）

`docs/superpowers/archive/spikes/2026-10-17-p1-base64-size.md`；脚本 `scripts/wire/measure_payload_sizes.py`。

- 源码事实：`MAX_BUF_SIZE=262144`（`tls_capture.h:9`）、`RING_BUFFER_SIZE=8MiB`（`:10`）；旧文档的 32KB/512KB 是腐化说法。
- 727 个真实事件：p50 **4B**、p99 **16,384B**、max **70,703B**；当前最大行 72,751B。
- 加性双字段投影（真实下界）：16,384→38,246B；70,703→164,989B；最坏逃逸上界 518,504B；262,144 理论上界 1,922,406B。
- **GO**：字母表 RFC 4648 STANDARD + `=`；用户态流式编码；不截断 `data_b64`。

---

### T1: C 侧 base64 + 共享 FILE* 转义器（纯机械，行为逐字节不变）

**Files:** `bpftools/include/b64.h`（新）、`bpftools/include/json_escape.h`、`bpftools/tests/test_b64.c`（新）、`bpftools/tests/test_json_escape.c`、`bpftools/Makefile`

- [ ] 写 `test_b64.c`（先红）：RFC 4648 向量、0..255 全字节、非单射三元组 `B5`/`E9`/`C2 B5`、262144 长度、字母表与 padding 位置。
- [ ] `b64.h` 实现 `b64_encoded_len` / `b64_encode` / `json_fprint_b64(FILE*, ...)`；每 3 字节写 4 字符，不堆分配（现有 `event_buf` 262145 字节不动）。
- [ ] `json_escape.h`：把 `json_print_escaped` 拆为 `json_fprint_escaped(FILE*, const char*, size_t)` + 一行 wrapper；规则逐行不变，所有调用点不动。
- [ ] `test_json_escape.c` 增加 fprint/print 全字节等价测试。
- [ ] Makefile `TEST_APPS` 加 `test_b64`。
- [ ] 验收：`make -C bpftools test` 全绿。

### T2: tls_capture 加法式 `data_b64`（流式）+ 截断诚实化

**Files:** `bpftools/include/payload_json.h`（新）、`bpftools/tls_capture/tls_capture.c`、`bpftools/tests/test_payload_json.c`（新）、`bpftools/Makefile`

- [ ] 写 `test_payload_json.c`（先红）：空载荷、ASCII、引号/反斜杠、`0x00/0x0A/0x1F/0x7F`、`0xB5`、合法 `C2 B5`、`é`；断言 `data` 段与今天逐字节一致，`data_b64` 正确。
- [ ] `payload_json.h`：`json_print_payload_fields(FILE*, const unsigned char*, size_t buf_size, unsigned int len)`；原 `tls_capture.c:589-625` 循环搬入（printf→fprintf），`data` 后输出 `"data_b64":"..."`，再接 `truncated`/`bytes_lost`；空载荷输出 `"data":null,"data_b64":null,"truncated":false`。
- [ ] `tls_capture.c:587-637` 改为一次调用；`:567` 用本地 clamp 后的 `buf_size`（修分叉）；`:631` `%d`→`%u`。
- [ ] **独立行为修复**：`buf_filled==0 && len>0` 从 `truncated:false` 改为 `truncated:true, bytes_lost=len`（`:636`）。
- [ ] 验收：`make -C bpftools test` + `make -C bpftools .output/bin/tls_capture`。

### T3: Rust 传输层 —— 新旧线格式统一为 `data_b64`

**Files:** `collector/src/framework/normalize/transport.rs`（新）、`normalize/mod.rs`、`collector/tests/wire_transport_roundtrip.rs`（新）、`collector/tests/fixtures/wire/transport/{payload.bin,capture-base64.jsonl,capture-escaped.jsonl}`（新）、`scripts/wire/make_transport_fixture.py`（新）、`MANIFEST.json`

**关键修正（评审 3）：** 旧格式的字节级恢复必须作用于**原始行字节**，不能作用于 `serde_json::Value`（`µ` 已在 Value 层变成 `C2 B5`）。

- [ ] 写测试（先红）：同一 payload 的 base64 行与旧 `\u00XX` 行经入口归一化后得到相同字节；`decoded_len == buf_size`；`null` = 无载荷；缺键 = legacy；非法 base64 = Err；truncated 记账。
- [ ] `transport.rs`：
  ```rust
  pub enum PayloadSource { Base64, LegacyUnescape }
  pub struct WirePayload { pub bytes: Vec<u8>, pub source: PayloadSource, pub declared_buf_size: u32, pub truncated: bool }
  pub fn legacy_data_bytes(line: &[u8]) -> Result<Option<Vec<u8>>, TransportError>;
  pub fn ensure_data_b64(value: &mut serde_json::Value, raw_line: &[u8]) -> Result<(), TransportError>;
  pub fn payload_bytes(value: &serde_json::Value) -> Result<Option<WirePayload>, TransportError>;
  ```
  `legacy_data_bytes` 复刻 `scripts/wire/extract_wire.py:57-88`（`\u00XX`→单字节），断言长度。
- [ ] fixture 生成器从 `claude-code-deepseek-zstd-chunked/response.http` 派生 `payload.bin`，生成新旧两种行；`MANIFEST` 标 `derived_from`（不冒充 live）。
- [ ] 验收：`cargo test --test wire_transport_roundtrip` + 门 10。

### T4: stdout 入口按字节读取 + 归一化

**Files:** `collector/src/framework/runners/common.rs`、`wire_current_behavior_pins.rs`

**关键修正（评审 10）：** 先抽出可测的 `pub(crate) async fn json_lines<R: AsyncBufRead + Unpin>(reader, source_name)`，再替换 `read_line(String)`。

- [ ] 写红测试：`BufReader<&[u8]>` 喂 `{"a":1}\n<0xff 0xff>\n{"b":2}\n`，断言 2 个 JSON、不终止、无 U+FFFD。
- [ ] `json_lines` 用 `read_until(b'\n', Vec<u8>)` + ASCII trim + `serde_json::from_slice`；保留行号日志、非 JSON 跳过、EOF 半行处理。
- [ ] 对解析出的对象调用 `transport::ensure_data_b64(&mut value, &line)`（只对含 `buf_size` 的 SSL 行；错误 → 记诊断并跳过该行，不 panic）。
- [ ] 删除 `common.rs:209-263` 的 InvalidData 恢复块（含 `:223` 的 `from_utf8_lossy`）。
- [ ] 反转 `wire_current_behavior_pins.rs` 的 lossy pin 为 `byte_reader_never_produces_replacement_characters`。
- [ ] 验收：`cargo test --lib runners::common` + `git grep from_utf8_lossy -- collector/src/framework/runners` 无输出。

### T5: 抽出 record 预设并删除两个丢弃规则

**Files:** `collector/src/framework/analyzers/presets.rs`（新）、`analyzers/mod.rs`、`collector/src/main.rs`

- [ ] 写 `presets.rs` 测试（先红）：用真实的 `HTTPFilter`/`SSLFilter` 构造并断言行为。
- [ ] 把 `main.rs:529/531` 的预设搬到 `presets.rs`；删除 HTTP 预设尾部 ` | response.body=` 与 SSL 预设 ` | data.type=binary`；保留 `data=0\r\n\r\n`（终止符去重；若 T6 证明它影响组装则在本任务内一并删除）。
- [ ] 更新 `main.rs:295` 的 clap 文档注释。
- [ ] 验收：`cargo test presets`；zstd 响应事件能穿过 `SSLFilter`；请求侧规则仍生效。

### T6: 响应组装器 + `PayloadNormalizer`（P1 核心）

**Files:** `normalize/assembler.rs`（新）、`analyzers/payload_normalizer.rs`（新）、`normalize/mod.rs`、`analyzers/mod.rs`、`sse_processor.rs`、`http_parser.rs`、`routing.rs`、`connection_buffer.rs`、`main.rs`、`collector/tests/response_chain.rs`（新）

**关键修正（评审 1/2/4/5/6/15）：**

- **注册点**：`main.rs:1117-1119`（`TimestampNormalizer` 之后、`SSLFilter` **之前**）。否则终止符事件 `0\r\n\r\n` 被 `SSLFilter` 精确匹配丢弃，chunked 永远 `MissingTerminator`。
- **数据源**：汇编器只读 `transport::payload_bytes(event.data)`；不读 `data["data"]`。加一条“只有 `data_b64`、没有 `data` 也能重组”的测试。
- **partial 标记**：一旦判定该响应可定帧，头事件与后续分片写入 `data["partial"]=true` 后再 yield；`SSEProcessor`、`HTTPParser`、`routing` 对 `partial==true` 直接原样放行/跳过，**防止双解析与假意图**。`FileLogger`/`OutputAnalyzer` 不跳过（原始字节保留）。
- **合成事件字段**：`source="ssl"`、`data["function"]="READ/RECV"`、`pid`、`tid`、`ssl_ptr`、`truncated=false`、`decode_status="ok"`、`codings`、`framing`、`source_sha256`/`source_len`（原始字节指纹，不复制压缩块）、`data_b64`=重组后的明文；若任一组成事件 `truncated=true` → `decode_status="lossy"` 且不得标 ok。
- **非 UTF-8 明文**：不发 `data`；发诊断事件 `decode_status="binary_non_utf8"` + `data_b64`（字节不丢）。
- **不可定帧**：无 TE/CL 的响应不合成（`decode.rs:71-73` 会把部分字节当完整 body），原样放行。
- **可重试错误**：仅 `IncompleteHead`/`IncompleteBody`/`TruncatedChunk`/`MissingTerminator`；其余一次诊断后 poison 该键。
- **连接键**：`(pid, ssl_ptr)`；缺 `ssl_ptr` 时回退 `(pid, tid)`。
- **边界**：idle 30s、每连接 64MiB、最多 64 连接、总缓冲 128MiB；新请求（WRITE）到达时把旧响应按 incomplete 收尾；流结束 flush。
- **ConnectionBuffer（评审 5）**：它克隆 `event.data` 后只替换 `data`，会留下陈旧 `data_b64`；重写 `data` 时同步删除 `data_b64`（或重算），并加测试。
- **SSEProcessor**：`decode_status=="ok"` 时跳过 `clean_chunked_content`（`sse_processor.rs:160-197` 会把十六进制行当 chunk size）。
- **测试**：对 `response.http` 的每个切分点重组 → `body == golden.sse`；pipelined、CL、unframed、malformed、truncated、idle、cap、data_b64-only、partial 不产生 intent。
- **链路测试**：用真实 record 预设（含 `SSLFilter`）搭 `PayloadNormalizer → SSLFilter → SSEProcessor → HTTPParser → HTTPFilter`，断言**恰好一条**响应且 body 非空。
- [ ] 验收：`cargo test --lib assembler` + `cargo test --test response_chain`。

### T7: 工具、文档真值与新鲜度门

**Files:** `scripts/wire/extract_wire.py`、`scripts/wire/check_baselines.py`、`bpftools/tests/test_tls_capture_header.c`、`docs/capabilities.json`、`bpftools/README.md`、`bpftools/README.zh-CN.md`、`README.zh-CN.md`、`docs/superpowers/archive/HANDOFF-2026-10-17.md`、`docs/superpowers/archive/specs/2026-10-17-world-class-architecture-design.md`、`collector/tests/wire_current_behavior_pins.rs`、`collector/tests/embedded_binary_freshness.rs`（新）

- [ ] `extract_wire.py` 增加整行解码：有 `data_b64` 严格解码，否则走现有字节解析；两条路都断言 `len==buf_size`。
- [ ] `check_baselines.py` 增加传输线检查：base64 行与旧行解码字节相同且 sha 等于 `payload.bin`。
- [ ] `test_tls_capture_header.c` 增加 `MAX_BUF_SIZE == 256*1024` 与 ring 容量断言（评审 8）。
- [ ] **新鲜度门（评审 7）**：`embedded_binary_freshness.rs` 比较 `include_bytes!("../../bpftools/.output/bin/tls_capture")` 与磁盘文件；不一致即红。不要用 `strings | grep data_b64`。
- [ ] 文档：`data`=有损显示、`data_b64`=权威、STANDARD+padding、null/缺键、truncated 按原始字节；把 32KB/512KB 统一改 256KB，并保留日期注记；同步 `docs/capabilities.json:79`、`main.rs:295`、README 的 `524288` 示例。
- [ ] 反转 `wire_current_behavior_pins.rs` 的“record 丢弃压缩响应” pin。
- [ ] 验收：`python3 scripts/wire/check_baselines.py` + `cargo test --test embedded_binary_freshness` + 门 11。

### T8: 真实路径验证（sudo + 真实触发；不在 verify.sh）

- [ ] `make -C bpftools .output/bin/tls_capture`；`cd collector && cargo build --release`。
- [ ] `./scripts/wire/capture_wire_corpus.sh claude /tmp/p1-claude`；raw jsonl 含 `"data_b64":"`；extract 解码字节与旧格式一致。
- [ ] 真实终端触发 `claude -p`，`mtagentlens record` 日志/DB 至少 1 条响应侧消息（`source=sse_processor` 或解码后的 `http_parser` response），body 非空。
- [ ] OpenClaw 同样跑一次（`MainThread`，`-c` 不可靠）。
- [ ] 量测：线体积、trace.log/DB 体积、RSS、事件率、intents/evidence 数量（确认 partial 不产生假意图）。
- [ ] **B2/B5 是硬性通过条件**，0 条响应 = 失败；诊断信息只用于解释失败。

---

## Acceptance

**无需 sudo（机械判定）**

- A1 `./scripts/verify.sh` → `all gates passed`，12/12，预算 295/26/67/59。
- A2 `make -C bpftools test` 全绿，含 `test_b64`、`test_payload_json`，且 `MAX_BUF_SIZE==262144` 断言存在。
- A3 `cargo test --test wire_transport_roundtrip`：base64 行与旧行 → 相同字节；`decoded_len==buf_size`；非法 base64 → Err。
- A4 `cargo test --lib assembler`：每个切分点 `body==golden.sse`；pipelined/CL/unframed/malformed/truncated/idle/cap/data_b64-only 全覆盖。
- A5 `cargo test --test response_chain`：真实 record 预设链恰好 1 条响应，body 非空；原始 ssl 事件仍输出。
- A6 `cargo test presets`：不再含 `response.body=` / `data.type=binary`；终止符规则仍生效。
- A7 `cargo test --lib runners::common`：非 UTF-8 行不终止流、不产生 U+FFFD。
- A8 `python3 scripts/wire/check_baselines.py` 退出 0；transport fixture 哈希一致。
- A9 `cargo test --test wire_current_behavior_pins`：record 丢响应的 pin 已反转；serde 非单射 pin 保留并注明 `data` 仅显示。
- A10 `python3 -I scripts/wire/measure_payload_sizes.py --self-check` 退出 0；spike 文件含实测分布。
- A11 `git grep -n from_utf8_lossy -- collector/src/framework/runners` 无输出。
- A12 `cargo test --test embedded_binary_freshness`：嵌入的 C 二进制与磁盘一致。

**需要 sudo / 真实抓包（不假装 CI 覆盖）**

- B1 `capture_wire_corpus.sh claude` 的 raw jsonl 含 `data_b64`；解码字节与旧格式一致。
- B2 真实 `claude -p` 的 record 日志/DB 至少 1 条响应侧明文消息，body 非空（**硬性**）。
- B3 record 不再整条丢弃压缩响应；请求侧事件数相对 P0.5 无回归。
- B4 线体积约 +33%（加 `data` 渲染）；RSS 相对 P0.5 基线无 >2× 回归；记录 DB/日志增长。
- B5 OpenClaw（node，`MainThread`）至少解码 1 条 zstd+chunked SSE（**硬性**）。

---

## 执行状态（2026-10-18）

| 任务 | 状态 | 证据 |
|---|---|---|
| T0 测量 spike | ✅ | `docs/superpowers/archive/spikes/2026-10-17-p1-base64-size.md`：p50 1,369B / p99 16,384B / max 70,591B；真实双字段最大行 166,776B |
| T1 C base64 | ✅ | `bpftools/include/b64.h`；`test_b64`（RFC 向量/256 字节往返/非单射三元组/长度算术） |
| T2 加法式 `data_b64` | ✅ | `bpftools/include/payload_json.h`；`test_payload_json`（字段契约/截断记账） |
| T3 传输层 | ✅ | `normalize/transport.rs`；`wire_transport_roundtrip` 8 个（新旧行同字节） |
| T4 字节入口 | ✅ | `runners/common.rs`；5 个单测；`git grep from_utf8_lossy -- runners` 无输出 |
| T5 record 预设 | ✅ | `analyzers/presets.rs`；5 个测试；P0.5 pin 反转 |
| T6 组装器 + Normalizer | ✅ | `normalize/assembler.rs` + `analyzers/payload_normalizer.rs`；`response_chain` 4 个 |
| T7 工具/文档/门 | ✅ | `extract_wire.py` base64；`check_baselines.py` 传输线检查；`embedded_binary_freshness`；32KB/512KB 腐化说法已改 256KiB |
| T8 真机验证 | ✅ | Claude Code：1 条 `sse_processor`；OpenClaw：1 条 `sse_processor`；真机抓到 chunk 尾部 CRLF 的 partial-CRLF 缺陷并修复 |

**P1 期间额外修复**：`dechunk` 在"chunk 数据已到、尾部 CRLF 未到"时返回致命的
`MissingChunkCrlf`（真机验证发现）→ 改为可重试的 `MissingTerminator`；
`PayloadNormalizer` 对 malformed 流 poison 到下一个请求；
`verify.sh` 的 clippy 计数改为确定性（先 touch 所有 `.rs`，避免热缓存少报）。

**待补测量**：post-P1 的 FPR 与采集开销基线（P1 解码响应后会改变两者）。
