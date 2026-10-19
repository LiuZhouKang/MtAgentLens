# OpenClaw ↔ MtAgentLens intent cross-check

日期：2026-10-17
固件：`collector/tests/fixtures/wire/openclaw-intent-crosscheck/`
可执行断言：`collector/tests/wire_intent_crosscheck.rs`（2 个测试，离线可跑）

## 结论

| 维度 | OpenClaw 自述 | 我们抽取 | 是否一致 |
|---|---|---|---|
| 工具调用次数 | 1 | 1 | ✅ |
| 工具名 | `exec` | `exec` | ✅ |
| 调用 ID | `call_00_BEeJQHspHn8me7IYOESg9530` | 不保留 | —（见下） |
| 参数 | `{"title":"Echo crosscheck","code":"text(\"CROSSCHECK\")"}` | `[]` | ❌ |

**判定：工具名一致，参数未抽取。** 这不是抽取器"看错了"，而是
`collector/src/responsibility/evidence/args.rs:21` 的字段映射只覆盖 Claude
工具形状（`file_path` / `command` / `url` / `path` / `pattern` / `query` /
`description` 等）。OpenClaw 的 `exec` 用 `{title, code}`，两个字段都不在表里，
因此归一化参数为空。P4「通用化」现在有了一个可复现的靶子，而不是一句判断。

## 一手材料与来源

- **观察侧**：`response.http` —— TLS uprobe 在 `pid 1488 / MainThread` 上抓到的
  原始字节；`content-encoding: zstd` + `Transfer-Encoding: chunked`；de-chunk
  后 4637 字节，系统 `zstd -d` 解出 171282 字节 SSE（`golden.sse`）。
- **自述侧**：`asserted-tools.json` —— 由
  `scripts/wire/crosscheck_openclaw.py` 只读导出自
  `~/.openclaw/agents/main/agent/openclaw-agent.sqlite`。

### 一个必须记录的坑

`trajectory_runtime_events.data.args` 里的参数被省略成
`{"title":"Echo crosscheck","code":"text(\"…CK\")"}`。完整的
`{"title":..., "code":"text(\"CROSSCHECK\")"}` 只存在于
`transcript_events`（该表用 `event_zstd` 压缩，`event_json` 为 NULL）。
**若直接信 trajectory 表，就会拿一个被截断的"真相"做核对。**
导出器因此以 `transcript_events` 为参数来源、以 `trajectory_runtime_events`
为身份来源，并在两者名字不一致时直接失败。

## 复现

```bash
# 1. 抓一次带工具调用的回合（需要 sudo 与运行中的 OpenClaw gateway）
#    见 scripts/wire/capture_wire_corpus.sh 的机制；指定 prompt：
#    "Use the exec tool to run exactly: echo CROSSCHECK. Then reply with exactly: DONE."

# 2. 选出含工具调用的 pair
python3 scripts/wire/extract_wire.py extract <capture.jsonl> --ssl 592283728 --pair 0 \
  --out-dir collector/tests/fixtures/wire/openclaw-intent-crosscheck --sanitize
python3 scripts/wire/extract_wire.py golden collector/tests/fixtures/wire/openclaw-intent-crosscheck

# 3. 导出自述侧（只读）
python3 scripts/wire/crosscheck_openclaw.py \
  --sqlite ~/.openclaw/agents/main/agent/openclaw-agent.sqlite \
  --session-key p05-crosscheck-20261017-230843 \
  --since 2026-10-17T23:08:40 --until 2026-10-17T23:09:10 \
  --out collector/tests/fixtures/wire/openclaw-intent-crosscheck/asserted-tools.json

# 4. 断言
cd collector && cargo test --test wire_intent_crosscheck -- --nocapture
```

预期输出中有：

```
tool=exec ours_args=[] asserted_args={"code":"text(\"CROSSCHECK\")","title":"Echo crosscheck"}
```

## 不作为结论的部分

- 本回合的响应是 `openai-completions` SSE；工具调用参数以
  `delta.tool_calls[].function.arguments` 分片到达。我们抽取到了完整的
  `exec` 调用（名字正确），说明 SSE 累积逻辑本身工作。
- 未验证多工具、并行工具、以及工具**定义**与工具**调用**同时出现在请求里的
  幻影意图场景（交接文档 §5 的另一条缺陷）——那属于 request 侧，
  本 fixture 只覆盖 response 侧。
