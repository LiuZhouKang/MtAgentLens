# P0.5 验证脊梁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让后续每一个「修好了」都成为可被第三方否证的命题：原始线字节入库、独立解码 golden、确定性、分割不变、与 OpenClaw 自己的记录交叉核对、良性 FPR 基线、开销基线，并把可离线运行的部分接进 `scripts/verify.sh`。

**Architecture:** 先冻结一手事实（两份真实 zstd+chunked 线报文），再用严格字节级解码器作为被测对象（`framework/normalize/`），以独立工具（系统 `zstd`）的产物为 golden，用确定性/蜕变测试钉死它；OpenClaw 的 `trajectory_runtime_events.tool.call` 作为唯一外部真相核对意图抽取；20 个良性场景实跑得到 FPR 基线；采集开销另立基线。所有新能力配一条能证明它存在的门。

**Tech Stack:** Rust 2024、`zstd`/`flate2`/`brotli` crate、系统 `zstd` CLI（独立解码）、SQLite（只读核对）、Python（采集/测量编排）、bash 门禁。

## Global Constraints

- **不提交**：本计划所有改动留在工作树；commit message 由用户亲自定稿（中文、无 conventional 前缀）。
- **全绿**：任何时刻 `./scripts/verify.sh` 必须 13/13 PASS；预算（clippy 295 / ignored 26 / `/home/lzk` 67 / allow 59）只许减不许增。
- **Claude Code 路径冻结**：不得改动 SSL 线格式、`from_utf8_lossy` 入口、`data.type=binary` 过滤（这些属 P1）。
- **不做大爆炸重构**：新代码只新增 `framework/normalize/` 与测试/脚本；对既有模块只做接线级改动。
- **所有结论需 `file:line` 或实测命令输出**；禁止推测。
- **cargo 产物在 `collector/build/`**，不是 `target/`。
- **sudo 用** `printf '%s\n' 1 | sudo -S -p '' <cmd>`；长任务写成 `.sh` 文件执行。
- **/tmp 会被清理**：抓包必须立刻入库。
- 新增 `#[ignore]` 必须带理由；新增 `#[allow]` 受预算门约束（不得新增）。
- **前端不动。**

---

## File Structure

```
collector/tests/fixtures/wire/
├── MANIFEST.json                                  # 全局清单：来源、SHA256、脱敏记录
├── openclaw-deepseek-zstd-chunked/
│   ├── request.http                               # 脱敏后的原始请求字节（含 chunk 无关）
│   ├── response.http                              # 原始响应字节（头 + chunked zstd body）
│   └── golden.sse                                 # 系统 `zstd -d` 的独立解码结果
└── claude-code-deepseek-zstd-chunked/
    ├── request.http
    ├── response.http
    └── golden.sse

collector/tests/fixtures/wire/openclaw-intent-crosscheck/
├── request.http                                   # 含工具调用的请求（脱敏）
├── response.http
├── golden.sse
├── asserted-tools.json                            # 从 openclaw sqlite 的 SQL 行导出（外部真相）
└── crosscheck-report.md                           # 逐条比对结果

collector/src/framework/normalize/
├── mod.rs          # 模块声明与 re-export
├── error.rs        # DecodeError（显式、可区分、无静默兜底）
├── http_head.rs    # 头部/正文切分 + 大小写不敏感头查询
├── chunked.rs      # RFC 9112 §7.1 严格 de-chunk（扩展/尾部/终止符）
├── coding.rs       # RFC 9110 §8.4 content-coding 逆序解码；RFC 9659 window 上限
└── decode.rs       # 组合：head → de-chunk → 解压，返回 consumed 偏移

collector/tests/wire_corpus.rs                     # golden / 确定性 / 分割不变 / 畸形输入
collector/tests/wire_current_behavior_pins.rs      # 钉住 P1 前的丢弃机制（明确标注将被 P1 反转）

scripts/wire/extract_wire.py                       # C 输出字节级严格反转义 + 脱敏 + fixture 生成
scripts/wire/capture_wire_corpus.sh                # 可复现抓取（OpenClaw 新会话 + Claude 一次性）
scripts/wire/fpr_runner.py                         # 20 良性场景测量（已存在）
scripts/wire/run-benign-fpr.sh                     # 测量入口（已存在）
scripts/wire/summarize_fpr.py                      # Wilson 区间 + 基线 JSON/Markdown
scripts/wire/crosscheck_openclaw.py                # SQL 行 vs 我方 intent 的逐条比对
scripts/wire/measure-overhead.sh                   # 采集期 CPU/RSS/事件率
scripts/wire/check_baselines.py                    # verify.sh 调用的静态基线完整性门

docs/evaluation/fpr-baseline.json                  # FPR 数字 + 每个 run 的坐标
docs/evaluation/fpr-baseline.md                    # 人读报告（含 Wilson 区间与解释）
docs/evaluation/overhead-baseline.json             # 开销数字 + 采集命令 + 环境
```

---

### Task 1: 冻结两份真实线报文（交付物 1 + 2）

**Files:**
- Create: `scripts/wire/extract_wire.py`
- Create: `scripts/wire/capture_wire_corpus.sh`
- Create: `collector/tests/fixtures/wire/MANIFEST.json`
- Create: `collector/tests/fixtures/wire/openclaw-deepseek-zstd-chunked/{request.http,response.http,golden.sse}`
- Create: `collector/tests/fixtures/wire/claude-code-deepseek-zstd-chunked/{request.http,response.http,golden.sse}`

**Interfaces:**
- Produces: 两份 fixture 目录，文件名与上表一致；`MANIFEST.json` 的 `files[].sha256` 供 Task 7 的静态门校验。

**已确认的一手事实（2026-10-17 实测）：**
- C 侧 `tls_capture.c:589-628` 的 `data` 转义是**字节可逆**的：`\u00XX` 必须按字节还原，不能用 `serde_json`（它会把 `é` 变成 UTF-8 两字节）。
- OpenClaw（DeepSeek，`openai-completions`）：响应 `content-encoding: zstd` + `Transfer-Encoding: chunked` + SSE；新会话抓包 30 事件，chunked body 597B，`zstd -d` → 5288B，以 `data: [DONE]` 结束。
- Claude Code：请求跨两次 `SSL_write`（16384 + 70703B），响应同样 zstd+chunked；body 1275B，`zstd -d` → 10722B，含 `event: message_start` … `message_stop`。

- [ ] **Step 1: 写字节级提取器（仓库版，带自校验）**

`scripts/wire/extract_wire.py`：从 C JSONL 读取，按 `\u00XX`→单字节还原，校验 `len(extracted) == buf_size`，按 `ssl_ptr` 分组，输出请求/响应原始字节。另提供 `--sanitize`：
  - `authorization:`（大小写不敏感）整行值 → `Bearer REDACTED`；
  - `x-claude-code-session-id:` 的 UUID → `00000000-0000-4000-8000-000000000000`；
  - 所有 `lzk` → `usr`（**等长**，保证 `Content-Length` 与 body 不变）；
  - 断言脱敏后 body 长度不变、JSON 可解析、`Content-Length` 与 body 长度一致；
  - 扫描并拒绝残留 `sk-`、`/home/lzk`、邮箱/手机号。

- [ ] **Step 2: 生成 fixture 并记录独立 golden**

对每份响应：严格 de-chunk → 写 `body.zst`（临时）→ 系统 `zstd -d -c` → `golden.sse`。`MANIFEST.json` 记录：
  - `captured_at`、`tls_capture` 命令、`ssl_ptr`、`client`（OpenClaw 2026.10.1 / claude-cli 2.1.302）、`zstd --version`；
  - 每个文件的 `bytes` 与 `sha256`（脱敏后文件）；
  - `sanitization` 数组（逐条记录替换规则与命中次数）。

- [ ] **Step 3: 验证 fixture 可被独立工具复现**

```bash
cd /home/lzk/MtAgentLens
python3 - <<'PY'
# 1) response.http 里 de-chunk 出的字节 == 系统 zstd 能解压的帧
# 2) 解压结果逐字节等于 golden.sse
# 3) MANIFEST 的 sha256 与文件一致
PY
```
Expected: 三项全部 True，且两份 fixture 的 `golden.sse` 非空（≥ 1KB）。

- [ ] **Step 4: 可复现抓取脚本**

`scripts/wire/capture_wire_corpus.sh`：固定步骤（OpenClaw 新 session-key、Claude `-p` 一次性），注释写明 sudo 与 `--binary-path` 的用法；抓完立即调用提取器生成 fixture，原始捕获留在 `$OUT/raw/`（不入库）。

- [ ] **Step 5: 不提交，跑门禁确认零影响**

Run: `./scripts/verify.sh`
Expected: `verify: all gates passed`（fixture 只是数据文件，不影响 13 门）。

---

### Task 2: 严格字节级解码器 `framework/normalize/`（TDD）

**Files:**
- Create: `collector/src/framework/normalize/{mod.rs,error.rs,http_head.rs,chunked.rs,coding.rs,decode.rs}`
- Modify: `collector/src/framework/mod.rs`（加 `pub mod normalize;`）
- Modify: `collector/Cargo.toml`（`zstd = "0.13"`, `flate2 = "1.0"`, `brotli = "7"`）

**Interfaces:**
- Produces:
  ```rust
  pub struct DecodeError { /* machine-readable variants */ }
  pub struct HttpHead { pub start_line: String, pub headers: Vec<(String,String)>, pub body_offset: usize }
  impl HttpHead {
      pub fn header<'a>(&'a self, name: &str) -> Option<&'a str>;
      pub fn header_all(&self, name: &str) -> Vec<&str>;
  }
  pub fn split_head(raw: &[u8]) -> Result<HttpHead, DecodeError>;
  pub struct Dechunked { pub body: Vec<u8>, pub trailers: Vec<(String,String)> }
  pub fn dechunk(body: &[u8], base_offset: usize) -> Result<Dechunked, DecodeError>;
  pub fn decode_content(body: &[u8], content_encoding: Option<&str>) -> Result<Vec<u8>, DecodeError>;
  pub struct DecodedMessage { pub head: HttpHead, pub body: Vec<u8>, pub codings: Vec<String>, pub consumed: usize }
  pub fn decode_message(raw: &[u8]) -> Result<DecodedMessage, DecodeError>;
  ```

- [ ] **Step 1: 写失败测试（单元层，先于实现）**

在 `chunked.rs` 的 `#[cfg(test)]` 里写：
  - `dechunk(b"4\r\nWiki\r\n5\r\npedia\r\n0\r\n\r\n") == b"Wikipedia"`；
  - chunk 扩展 `4;foo=bar\r\n` 被接受；
  - trailer `0\r\nX-Sum: 1\r\n\r\n` 被解析且 body 为空；
  - 截断：`b"4\r\nWiki"` → `Err(TruncatedChunk)`；
  - 坏 hex：`b"zz\r\n"` → `Err(BadChunkSize)`；
  - 缺终止符：`b"4\r\nWiki\r\n"` → `Err(MissingTerminator)`；
  - 数据后缺 CRLF：`b"4\r\nWikiXX0\r\n\r\n"` → `Err(MissingChunkCrlf)`。

Run: `cd collector && cargo test --lib normalize::chunked`
Expected: 编译失败（模块不存在）→ 逐个实现后变绿。

- [ ] **Step 2: 实现 `error.rs` 与 `chunked.rs`**

`DecodeError` 变体：`IncompleteHead`、`MalformedStatusLine`、`BadChunkSize{offset,line}`、`MissingChunkCrlf{offset}`、`TruncatedChunk{offset,declared,available}`、`MissingTerminator`、`ChunkTooLarge{declared}`、`UnsupportedTransferEncoding(String)`、`UnsupportedContentEncoding(String)`、`ContentLengthMismatch{declared,actual}`、`Decompress{encoding,message}`、`WindowTooLarge{window_log,max}`。
实现 `Display` + `std::error::Error`；**任何分支都不得返回部分结果**。`MAX_DECODED_BODY = 64 * 1024 * 1024`。

- [ ] **Step 3: 实现 `http_head.rs`**

`split_head` 找 `\r\n\r\n`，没有 → `IncompleteHead`；首行必须匹配 `HTTP/1.[01] \d{3}` 或 `METHOD SP target SP HTTP/1.[01]`；头名/值按第一个 `:` 切分，值去 OWS；重复头保留顺序。`header()` 大小写不敏感取第一个，`header_all()` 取全部。

- [ ] **Step 4: 实现 `coding.rs`**

按 `Content-Encoding` 逗号列表**逆序**应用；支持 `identity|zstd|gzip|x-gzip|deflate|br`；zstd 用 `zstd::stream::Decoder::window_log_max(23)`（RFC 9659 §3 ≤8MB），超限 → `WindowTooLarge`；`deflate` 先 zlib 后 raw fallback，两者都失败才报错；未知 → `UnsupportedContentEncoding`（**绝不原样透传**）。

- [ ] **Step 5: 实现 `decode.rs`**

`decode_message`：`split_head` → 若 `Transfer-Encoding` 末项为 `chunked` 则 `dechunk`（否则若有 TE → `UnsupportedTransferEncoding`）→ 若无 TE 且有 `Content-Length` 则严格比对（不等 → `ContentLengthMismatch`）→ `decode_content` → 返回 `consumed`（chunked 为终止符后的偏移；identity 为 CL/余长）。

- [ ] **Step 6: 跑单元测试 + fmt + clippy**

Run: `cd collector && cargo test --lib normalize && cargo fmt -- --check && cargo clippy --all-targets 2>&1 | tail -5`
Expected: 全绿；clippy 不新增告警（预算 295 不得上升）。

---

### Task 3: 线语料验收（交付物 2/3/4）

**Files:**
- Create: `collector/tests/wire_corpus.rs`
- Create: `collector/tests/wire_current_behavior_pins.rs`

**Interfaces:**
- Consumes: `mtagentlens::framework::normalize::{decode_message, dechunk}`；fixture 路径 `concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/wire/...")`。

- [ ] **Step 1: golden 逐字节一致**

```rust
#[test]
fn openclaw_golden_decode_matches_independent_zstd() {
    let raw = fs::read(fixture("openclaw-deepseek-zstd-chunked/response.http")).unwrap();
    let golden = fs::read(fixture("openclaw-deepseek-zstd-chunked/golden.sse")).unwrap();
    let msg = decode_message(&raw).unwrap();
    assert_eq!(msg.consumed, raw.len());
    assert_eq!(msg.body, golden);
}
#[test]
fn claude_golden_decode_matches_independent_zstd() { /* 同上 */ }
```
Run: `cd collector && cargo test --test wire_corpus`
Expected: PASS；若失败，先查 `golden.sse` 的生成命令是否记录在 MANIFEST。

- [ ] **Step 2: 确定性 oracle**

```rust
#[test]
fn decode_is_deterministic() {
    for name in ["openclaw-deepseek-zstd-chunked", "claude-code-deepseek-zstd-chunked"] {
        let raw = fs::read(fixture(&format!("{name}/response.http"))).unwrap();
        let a = decode_message(&raw).unwrap();
        let b = decode_message(&raw).unwrap();
        assert_eq!(a.body, b.body);
        assert_eq!(a.head.headers, b.head.headers);
        assert_eq!(a.codings, b.codings);
    }
}
```

- [ ] **Step 3: 分割不变性（蜕变套件）**

对每份 fixture：
  1. 取 `dechunked` payload `P`；
  2. 对每个 chunk 大小 `k in 1..=P.len()` 重新分帧（`format!("{k:x}\r\n")`），`dechunk` 结果必须 == `P`；
  3. 对每个二分点 `i in 0..=P.len()` 构造两 chunk，结果 == `P`；
  4. 对每个 `i` 加 chunk 扩展 `;x=y`，结果 == `P`；
  5. 追加 trailer，结果 == `P`。
Run: `cd collector && cargo test --test wire_corpus split_invariance -- --nocapture`
Expected: 全部通过（约 2×1900 次迭代，< 1s）。

- [ ] **Step 4: 头重排不变**

把响应头按**逆序**重建（保持 start_line），`decode_message` 的 `body` 必须与原结果一致；`Content-Encoding` 与 `Transfer-Encoding` 仍被正确识别（大小写不敏感）。

- [ ] **Step 5: 畸形输入必须报错（不得部分成功）**

用 fixture 的 body 构造：截断 1 字节、破坏一个 size 行的 hex、删掉终止符、把 `Content-Encoding` 改成 `snappy`、把 zstd 帧魔数改坏。每个都必须 `Err`，且错误变体可区分。

- [ ] **Step 6: 钉住当前（P1 前）行为，防止误以为已修好**

`collector/tests/wire_current_behavior_pins.rs`：
  - `current_pipeline_classifies_zstd_body_as_binary`：取 fixture 的 chunked body 字节，`String::from_utf8_lossy` 后 `detect_data_type(..) == "binary"`（解释：`ssl_filter.rs:249-254` 的 `data.type=binary` 因此命中）。
  - `lossy_utf8_expands_high_bytes`：`from_utf8_lossy(&[0xB5]).as_bytes().len() == 2`（解释：`runners/common.rs:223` 的入口损伤）。
  - 两个测试的注释都写明：**P1 修复后必须反转这两条断言**，它们不是期望行为，是现状证据。
Run: `cd collector && cargo test --test wire_current_behavior_pins`
Expected: PASS（它们描述现状）。

---

### Task 4: 与 OpenClaw 自己的 sqlite 交叉核对（交付物 5）

**Files:**
- Create: `collector/tests/fixtures/wire/openclaw-intent-crosscheck/{request.http,response.http,golden.sse,asserted-tools.json,crosscheck-report.md}`
- Create: `scripts/wire/crosscheck_openclaw.py`
- Create: `collector/tests/wire_intent_crosscheck.rs`

**Interfaces:**
- Consumes: `mtagentlens::responsibility::evidence::intent_extractor::IntentExtractor::extract(body: &str, source, process_id, message_id, timestamp_ns) -> Vec<Intent>`。
- Produces: `asserted-tools.json`：`[{"toolCallId","name","args","ts","sessionKey"}]`，**直接由 SQL 行导出**。

- [ ] **Step 1: 抓一次含工具调用的 OpenClaw 回合**

用 `scripts/wire/capture_wire_corpus.sh` 的同一机制，指示：`Use the exec tool to run: echo CROSSCHECK. Then reply DONE.`，capture 到 `openclaw-intent-crosscheck/`。脱敏同 Task 1。

- [ ] **Step 2: 从 sqlite 导出 asserted 行**

```sql
SELECT event_json, created_at FROM trajectory_runtime_events
WHERE event_json LIKE '%"type":"tool.call"%'
ORDER BY created_at;
```
按 `sessionKey` 过滤到本次会话、按 `created_at` 过滤到抓包时间窗（交接文档陷阱 9：`seq` 是 per-session，不能跨 session 排序）。

- [ ] **Step 3: 跑我们的抽取器并生成差异报告**

`wire_intent_crosscheck.rs`：读 `golden.sse` → `IntentExtractor::extract` → 收集 `(name, args)`；读 `asserted-tools.json`；逐条按 `toolCallId`/name+args 比对，输出：
  - `matched`、`our_only`、`asserted_only`，以及每个差异的原因；
  - 结果写入 `crosscheck-report.md`。
Expected: 至少 1 条 `exec` 调用被双方同时看到；若 `our_only` 包含工具**定义**（`exec/openclaw/sessions_yield/wait`），这正是交接文档 §5 的「工具定义被当调用」缺陷，报告中如实标注。

- [ ] **Step 4: 核对器脚本可复跑**

`crosscheck_openclaw.py --sqlite <path> --session-key <key> --since <iso> --out asserted-tools.json`：只读连接（`mode=ro`），输出排序稳定的 JSON；无匹配行时退出码非 0（不静默成功）。

---

### Task 5: FPR 基线（交付物 6）

**Files:**
- Create: `scripts/wire/summarize_fpr.py`
- Create: `docs/evaluation/fpr-baseline.json`
- Create: `docs/evaluation/fpr-baseline.md`
- Modify: `scripts/verify.sh`（Task 7 接线）

**Interfaces:**
- Consumes: `env/fpr-runs/<ts>/results.jsonl`（`scripts/wire/run-benign-fpr.sh` 产出）。
- Produces: `docs/evaluation/fpr-baseline.json`：
  ```json
  {
    "schema": "mtagentlens.fpr-baseline/1",
    "measured_at": "...",
    "scenarios_total": 20,
    "runs_completed": 20,
    "definition": {"primary": "run has >=1 alert with severity Warning or Critical"},
    "runs_with_warning_plus": 0,
    "fpr": 0.0,
    "wilson_95": [0.0, 0.168],
    "alerts": {"total": 0, "by_severity": {}, "by_decision": {}},
    "per_scenario": [{"scenario_id": "...", "alerts_total": 0, "alerts_warning_plus": 0, "run_dir": "..."}],
    "provenance": {"command": "...", "collector_commit": "working-tree", "sudo": true}
  }
  ```

- [ ] **Step 1: 后台跑完 20 个良性场景**（已启动：`scripts/wire/run-benign-fpr.sh env/fpr-runs/p05-baseline`）

- [ ] **Step 2: 写 Wilson 区间汇总**

`summarize_fpr.py`：读 `results.jsonl`，按 `alerts_warning_plus > 0` 计 run 级 FPR；Wilson 95%（z=1.96）；同时输出 total/severity/decision 分布；若 `runs_completed < 20` 或任一 run 无 DB → 退出码非 0（不静默降级）。

- [ ] **Step 3: 生成并复核基线**

Run: `python3 scripts/wire/summarize_fpr.py env/fpr-runs/p05-baseline --out docs/evaluation fpr-baseline`
Expected: `fpr-baseline.json` 与 `.md` 生成；md 中每个数字可回溯到 `run_dir`。人工核对至少 2 个 run 的 DB `SELECT severity, decision, COUNT(*)` 与 JSON 一致。

- [ ] **Step 4: 记录已知误报的根因线索**

在 `.md` 中列出每条非 Info 告警的 `title`、`reasoning`、`claim_type`，并标注它是否属于设计内的「Uncertain 保留人工复核」。不在此任务修告警逻辑（那是 P2/P3）。

---

### Task 6: 开销基线与离线预算门（交付物 7）

**Files:**
- Create: `scripts/wire/measure-overhead.sh`
- Create: `docs/evaluation/overhead-baseline.json`
- Create: `collector/tests/overhead_gate.rs`

**Interfaces:**
- Produces: `overhead-baseline.json`：`{"schema":"mtagentlens.overhead-baseline/1","workload":{...},"collector":{"cpu_percent_mean","rss_peak_mib","events_per_second","bytes_per_second"},"environment":{...},"command":"..."}`。

- [ ] **Step 1: 写采集期测量脚本**

`measure-overhead.sh <seconds> <out.json>`：
  - 以 `record -c claude --binary-path <claude ELF> -o <log> --port 0` 启动采集器（sudo），同时跑一次固定 `claude -p` 回合；
  - 每秒读 `/proc/<collector_pid>/stat`（utime+stime）与 `/proc/<collector_pid>/status`（VmRSS），结束时算 CPU%、峰值 RSS；
  - 从日志统计事件行数与字节数，算 events/s、bytes/s；
  - 输出 JSON，失败时非 0 退出。

- [ ] **Step 2: 实测并入库**

Run: `./scripts/wire/measure-overhead.sh 45 docs/evaluation/overhead-baseline.json`
Expected: JSON 生成；`events_per_second > 0`、`rss_peak_mib > 0`、`cpu_percent_mean >= 0`。记录环境（内核版本、node/claude 版本、CPU 核数）。

- [ ] **Step 3: 离线预算门（无需 sudo，进 verify.sh）**

`collector/tests/overhead_gate.rs`：
  - 读两份 fixture，循环解码 200 次，记录耗时与进程峰值 RSS（`/proc/self/status`）；
  - 断言 `throughput >= 5 MiB/s`（先实测再定，留 ≥2× 余量）与 `peak_rss < 512 MiB`；
  - 断言 `docs/evaluation/overhead-baseline.json` 存在、schema 正确、`command` 与 `workload` 非空（基线不能是空壳）。
Run: `cd collector && cargo test --test overhead_gate -- --nocapture`
Expected: PASS，并打印实测吞吐与 RSS。

---

### Task 7: 接线门禁与文档（收尾）

**Files:**
- Create: `scripts/wire/check_baselines.py`
- Modify: `scripts/verify.sh`
- Modify: `docs/archive/engineering/handoffs/HANDOFF-2026-10-17.md`（P0.5 状态与新增门）

- [ ] **Step 1: 静态基线完整性门**

`check_baselines.py`：
  - 校验 `collector/tests/fixtures/wire/MANIFEST.json` 中每个文件存在且 sha256 匹配；
  - 校验两份 `golden.sse` 非空且能被重新解码（调用系统 `zstd` 不可行时只校验 sha256）；
  - 校验 `fpr-baseline.json` / `overhead-baseline.json` 的 `schema`、`provenance`、`measured_at` 非空，且 FPR 重算与存储一致；
  - 任一失败 → 非 0 退出。

- [ ] **Step 2: 接入 `scripts/verify.sh`**

新增第 10–12 门（保持现有 1–9 编号不变，新增段）：
```bash
note "10/12  wire corpus + baselines"
if python3 scripts/wire/check_baselines.py; then ok "wire fixtures and baselines are intact"; else bad "..."; fi
if (cd collector && cargo test --test wire_corpus --quiet); then ok "wire corpus: golden/determinism/split-invariance"; else bad "..."; fi
if (cd collector && cargo test --test overhead_gate --quiet); then ok "offline decode budget"; else bad "..."; fi
```
（同时把现有的 `1/9`…`9/9` 标题改成 `1/12`…`9/12`。）

- [ ] **Step 3: 全量门禁**

Run: `./scripts/verify.sh`
Expected: `verify: all gates passed`，且新门明确打印 PASS；clippy 预算不升、ignored 不升。

- [ ] **Step 4: 更新交接文档**

在 `HANDOFF-2026-10-17.md` 加「P0.5 已完成」小节：7 项交付物坐标、实测数字、新门清单、以及**仍未覆盖的边界**（FPR 动态重跑需 sudo+LLM；开销实时测量需 sudo；离线门只覆盖确定性部分）。同时把 §6 的 `/tmp` 抓包提示标注为「已重新抓取并入库」。

---

## Self-Review

**Spec coverage:**
1. 原始字节语料入库 → Task 1（两份 fixture + MANIFEST）。
2. 独立解码 golden → Task 1 Step 2 + Task 3 Step 1。
3. 确定性 oracle → Task 3 Step 2。
4. 分割不变性蜕变套件 → Task 3 Step 3/4。
5. 与 OpenClaw sqlite 交叉核对 → Task 4。
6. 良性 FPR + Wilson → Task 5。
7. 开销基线 + 门 → Task 6 + Task 7 Step 2。
- 「完成后把 4–5 条新门加进 verify.sh」→ Task 7 Step 2（解码一致/确定性/分割不变由 `wire_corpus` 覆盖；FPR 与开销以基线完整性 + 离线预算形式覆盖）。
- 硬约束（不提交、全绿、冻结 Claude Code 路径）→ Global Constraints，每个 Task 的验收都要求 verify 全绿。

**Placeholder scan:** 无 TBD/TODO；每个测试与实现都给出了具体行为、错误变体或命令。Task 4 的 `crosscheck-report.md` 内容依赖实测结果，但生成规则与失败条件已写明。

**Type consistency:** `decode_message`/`dechunk`/`decode_content`/`HttpHead` 在 Task 2 定义，Task 3/6 只按该签名调用；`IntentExtractor::extract` 的签名在 Task 4 前已核对（`intent_extractor.rs:210-216`）。

**Known decision to record:** FPR 的「不高于基线」门在当前机环境无法动态重跑（需 sudo + LLM）。门禁覆盖的是：基线结构/来源/场景集合完整性 + 离线解码预算。这一边界必须写进交付文档，不能假装它证明了线上 FPR 未回归。
