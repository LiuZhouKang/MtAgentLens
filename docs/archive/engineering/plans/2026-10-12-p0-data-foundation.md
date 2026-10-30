# Phase 0 数据地基 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让采集层在 OpenClaw（单进程多 agent、并发 LLM 调用）场景下产出**不发生串包、且带运行时身份信号**的干净数据。

**Architecture:** 两件事。① 给 `tls_capture` 的 SSL 事件补上**连接标识 `ssl_ptr`**（BPF uprobe 的第一个参数就是 `void *ssl`，当前只是没存），再让 `ConnectionBuffer` 按连接而非 tid 分键——这消除并发串包。② 把 `full_command`/`filename`（EXEC JSON 里已有）贯通到 `ProcessMetadata`，并新增用户态 `/proc/<pid>/cgroup` 采集，为 L1 运行时身份提供信号；全部零新增 eBPF 采集点，仅新增一个 map 存取。

**Tech Stack:** eBPF C (libbpf + clang 18)、Rust (edition 2024)、cargo test、SQLite（仅验收用）

## Global Constraints

- Rust 遵循 `cargo fmt` + `cargo clippy`；C 代码遵循内核代码风格（Tab 缩进）
- **禁止**在 Rust 中引入 `unsafe` 块而不加 `SAFETY` 注释
- eBPF 修改必须考虑 verifier 复杂度；不得引入无界循环
- 所有 eBPF 改动必须在 4.1+ / 5.0+ / 5.8+ 内核矩阵上验证
- Claude Code 路径行为必须冻结：`comm ∈ {claude, claude-code}` 的既有行为零变化
- commit 信息用中文、无 conventional 前缀、不提 agentsight
- 每个任务结束必须 `cd collector && cargo test` 全绿且无回归

---

## 关键既有事实（实现者必读）

| 事实 | 位置 |
|------|------|
| BPF uprobe 第一个参数已是 `void *ssl` | `bpftools/tls_capture/tls_capture.bpf.c:82,176,196,322` |
| 事件结构体 `probe_SSL_data_t` 当前**无** ssl 字段 | `bpftools/tls_capture/tls_capture.h:13-26` |
| enter 只存了 `buf`，没存 `ssl` | `tls_capture.bpf.c:95-96` |
| `SSL_exit` 统一填充事件体 | `tls_capture.bpf.c:104-160` |
| JSON 序列化在 `print_event()` | `tls_capture.c:552-570` |
| `ConnectionBuffer` 按 `tid` 分键 | `collector/src/framework/analyzers/connection_buffer.rs:73-131` |
| EXEC JSON 已含 `full_command`/`filename` | `bpftools/process/process.c`（EXEC 分支） |
| `parse_exec_event` 只提取 pid/ppid/comm/pgid | `collector/src/responsibility/runtime/routing.rs:387-420` |
| `observe_exec` 签名只收 comm + pgid | `collector/src/responsibility/signals/signal_graph.rs:186-193` |
| `ProcessMetadata` 无 cmdline/cgroup 字段 | `collector/src/responsibility/signals/signal_snapshot.rs:13-32` |
| `record` 的 `-c` 是必填 String，无默认值 | `collector/src/main.rs:296-298` |

---

## File Structure

| 文件 | 职责 | 动作 |
|------|------|------|
| `bpftools/tls_capture/tls_capture.h` | 事件结构体定义 | Modify：加 `ssl_ptr` 字段 |
| `bpftools/tls_capture/tls_capture.bpf.c` | eBPF 探针 | Modify：加 `ssls` map，各 enter 存 / exit 取 |
| `bpftools/tls_capture/tls_capture.c` | 用户态加载器 + JSON 序列化 + attach | Modify：序列化 `ssl_ptr`；attach 改 best-effort |
| `bpftools/tests/test_tls_capture_header.c` | 结构体布局契约测试 | Create |
| `collector/src/framework/analyzers/connection_buffer.rs` | SSL_write 分片重组 | Modify：改按连接分键 |
| `collector/src/responsibility/signals/signal_snapshot.rs` | `ProcessMetadata` | Modify：加 `cmdline`/`cgroup_unit` |
| `collector/src/responsibility/signals/signal_graph.rs` | `observe_exec` | Modify：加 `ProcessExecInfo` 参数 |
| `collector/src/responsibility/runtime/routing.rs` | `parse_exec_event` | Modify：提取 cmdline/filename + 读 `/proc/<pid>/cgroup` |
| `collector/src/responsibility/runtime/cgroup.rs` | 用户态 cgroup 读取 + 缓存 | Create |
| `collector/src/main.rs` | CLI | Modify：`record` 的 `-c` 给默认值 |

---

## Task 1: 事件结构体加 `ssl_ptr` + 契约测试

**Files:**
- Modify: `bpftools/tls_capture/tls_capture.h:13-26`
- Create: `bpftools/tests/test_tls_capture_header.c`
- Modify: `bpftools/Makefile`（加测试目标）

**Interfaces:**
- Produces: `struct probe_SSL_data_t` 新增字段 `__u64 ssl_ptr;`（置于 `is_handshake` 之前，保持既有字段偏移不变——**不要**插在中间，否则破坏 BPF↔用户态 ABI）

- [ ] **Step 1: 写失败测试**

创建 `bpftools/tests/test_tls_capture_header.c`：

```c
// SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
/* 契约测试：probe_SSL_data_t 必须携带 ssl_ptr，且既有字段偏移不被破坏。 */
#include <stdio.h>
#include <stddef.h>
#include <assert.h>
#include "tls_capture.h"

#define CHECK(cond, msg) do { \
    if (!(cond)) { fprintf(stderr, "FAIL: %s\n", msg); return 1; } \
    printf("  ok: %s\n", msg); \
} while (0)

int main(void)
{
    struct probe_SSL_data_t e;

    /* 既有字段偏移必须保持不变（BPF 与用户态共享该结构）
     * 实测布局：timestamp_ns=0 delta_ns=8 pid=16 tid=20 uid=24 len=28
     *          buf_size=32 buf_filled=36 rw=40 comm=44 buf=60 */
    CHECK(offsetof(struct probe_SSL_data_t, timestamp_ns) == 0,
          "timestamp_ns 仍在偏移 0");
    CHECK(offsetof(struct probe_SSL_data_t, pid) == 16,
          "pid 仍在偏移 16");
    CHECK(offsetof(struct probe_SSL_data_t, comm) == 44,
          "comm 仍在偏移 44");
    CHECK(offsetof(struct probe_SSL_data_t, buf) == 60,
          "buf 仍在偏移 60（数据区布局不变）");

    /* 新字段必须存在且可写 */
    e.ssl_ptr = 0xdeadbeefcafeULL;
    CHECK(e.ssl_ptr == 0xdeadbeefcafeULL, "ssl_ptr 字段可读写");

    /* ssl_ptr 必须位于 buf 之后，避免移动既有的变长数据区 */
    CHECK(offsetof(struct probe_SSL_data_t, buf) <
          offsetof(struct probe_SSL_data_t, ssl_ptr),
          "ssl_ptr 位于 buf 之后（不破坏数据区布局）");

    printf("PASS: 全部契约满足\n");
    return 0;
}
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd /home/lzk/MtAgentLens/bpftools
gcc -I tls_capture -I include -o /tmp/test_hdr tests/test_tls_capture_header.c 2>&1 | head -20
```

Expected: 编译错误 `struct probe_SSL_data_t has no member named 'ssl_ptr'`

- [ ] **Step 3: 加字段**

修改 `bpftools/tls_capture/tls_capture.h`，在 `int is_handshake;` **之前**插入：

```c
    __u64 ssl_ptr;          // SSL* 连接对象指针（同一 TLS 连接内稳定）
    int is_handshake;
```

改完后结构末尾为：

```c
struct probe_SSL_data_t {
    __u64 timestamp_ns;
    __u64 delta_ns;
    __u32 pid;
    __u32 tid;
    __u32 uid;
    __u32 len;
    __u32 buf_size;         // Actual bytes copied to buf
    int buf_filled;
    int rw;
    char comm[TASK_COMM_LEN];
    __u8 buf[MAX_BUF_SIZE];
    __u64 ssl_ptr;          // SSL* 连接对象指针（同一 TLS 连接内稳定）
    int is_handshake;
};
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
cd /home/lzk/MtAgentLens/bpftools
gcc -I tls_capture -I include -o /tmp/test_hdr tests/test_tls_capture_header.c && /tmp/test_hdr
```

Expected: `PASS: 全部契约满足`

- [ ] **Step 5: 在 Makefile 注册该测试**

在 `bpftools/Makefile` 的 `test:` 目标列表中追加 `test_tls_capture_header`，并仿照既有 `$(BIN_DIR)/test_process_header` 规则加一条编译规则（依赖 `$(OUTPUT)/tls_capture.skel.h`）。

- [ ] **Step 6: Commit**

```bash
git add bpftools/tls_capture/tls_capture.h bpftools/tests/test_tls_capture_header.c bpftools/Makefile
git commit -m "为 tls_capture 事件结构加入 ssl_ptr 连接标识字段，并加结构布局契约测试"
```

---

## Task 2: BPF 侧采集 `ssl_ptr`

**Files:**
- Modify: `bpftools/tls_capture/tls_capture.bpf.c`

**Interfaces:**
- Consumes: Task 1 的 `struct probe_SSL_data_t.ssl_ptr`
- Produces: 每个 SSL 事件携带产生它的 `SSL*` 指针值

- [ ] **Step 1: 加 `ssls` map**

在 `tls_capture.bpf.c` 中 `bufs` / `start_ns` map 定义旁，仿照其写法追加：

```c
/* tid → SSL* 连接对象指针，enter 存、exit 取 */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 10240);
    __type(key, u32);
    __type(value, u64);
} ssls SEC(".maps");
```

> 注意：必须与 `bufs`/`start_ns` 使用**相同**的 map 类型与 max_entries，并在同处定义，保持文件风格一致。

- [ ] **Step 2: 所有 enter 探针存 ssl**

在 `probe_SSL_rw_enter`（`:82`）、`probe_SSL_write_ex_enter`（`:176`）、`probe_SSL_read_ex_enter`（`:196`）中，紧跟既有 `bpf_map_update_elem(&bufs, ...)` 之后各加一行：

```c
    bpf_map_update_elem(&ssls, &tid, &ssl, BPF_ANY);
```

`probe_SSL_rw_enter` 改后为：

```c
    /* store arg info for later lookup */
    bpf_map_update_elem(&bufs, &tid, &buf, BPF_ANY);
    bpf_map_update_elem(&start_ns, &tid, &ts, BPF_ANY);
    bpf_map_update_elem(&ssls, &tid, &ssl, BPF_ANY);
    return 0;
```

- [ ] **Step 3: `SSL_exit` 取出并写入事件**

在 `SSL_exit`（`:104`）中，紧邻既有 `bufp` 查找之后加：

```c
    u64 *sslp = bpf_map_lookup_elem(&ssls, &tid);
```

在填充字段处（`data->is_handshake = false;` 之前）加：

```c
    data->ssl_ptr = sslp ? *sslp : 0;
```

在既有 `bpf_map_delete_elem(&bufs, &tid);` 旁加：

```c
    bpf_map_delete_elem(&ssls, &tid);
```

- [ ] **Step 4: 对 `SSL_write_ex` / `SSL_read_ex` 的 exit 探针做同样处理**

`probe_SSL_write_ex_exit`（`:286`）、`probe_SSL_read_ex_exit`（`:304`）以及 `probe_SSL_do_handshake_exit`（`:339`）各自独立填充事件体（不经过 `SSL_exit`），需在各自的事件填充处同样加 `data->ssl_ptr = sslp ? *sslp : 0;`，并在其 enter 探针存 ssl。先完整阅读这三处的既有实现再改，保持与 `SSL_exit` 一致。

- [ ] **Step 5: 编译验证**

```bash
cd /home/lzk/MtAgentLens/bpftools && make tls_capture 2>&1 | tail -20
```

Expected: 编译成功，无 verifier 相关报错

- [ ] **Step 6: 真实环境验证 ssl_ptr 出现**

```bash
# 终端 A
sudo ./bpftools/.output/bin/tls_capture --binary-path /home/lzk/.nvm/versions/node/v24.19.0/bin/node -p $(pgrep -f "openclaw/dist/index.js gateway" | head -1) > /tmp/ssl_ptr_check.jsonl
# 终端 B：触发一次真实调用
/home/lzk/.nvm/versions/node/v24.19.0/bin/openclaw agent --message "Say only: OK"
# 停止 A 后检查
python3 -c "
import json
rows=[json.loads(l) for l in open('/tmp/ssl_ptr_check.jsonl') if l.strip()]
ptrs={r.get('ssl_ptr') for r in rows}
print('事件数:', len(rows))
print('不同 ssl_ptr 数:', len(ptrs))
print('样例:', list(ptrs)[:3])
assert all('ssl_ptr' in r for r in rows), '有事件缺 ssl_ptr'
assert len(ptrs) > 0 and 0 not in ptrs, 'ssl_ptr 全为 0 ⇒ 采集失败'
print('PASS')
"
```

Expected: 每个事件都有非零 `ssl_ptr`；同一连接的多个事件共享同一指针

- [ ] **Step 7: 回归验证（Claude Code 路径不受影响）**

```bash
cd /home/lzk/MtAgentLens/collector && cargo test 2>&1 | tail -8
```

Expected: 全绿（本任务未改 Rust，应无变化）

- [ ] **Step 8: Commit**

```bash
git add bpftools/tls_capture/tls_capture.bpf.c
git commit -m "tls_capture 采集 SSL* 连接指针，用于区分同一线程上的并发连接"
```

---

## Task 3: JSON 序列化 `ssl_ptr` + 符号挂载改 best-effort

**Files:**
- Modify: `bpftools/tls_capture/tls_capture.c:552-570`（序列化）
- Modify: `bpftools/tls_capture/tls_capture.c:38-73`（attach 宏）、`:353-369`（attach_openssl）

**Interfaces:**
- Consumes: Task 2 采集到的 `event->ssl_ptr`
- Produces: JSON 每行含 `"ssl_ptr":<u64>` 字段；符号挂载失败时打印逐符号告警且不中断其余符号

- [ ] **Step 1: 序列化 ssl_ptr**

在 `print_event()` 中，`printf("\"tid\":%d,", event->tid);` 之后加：

```c
	printf("\"ssl_ptr\":%llu,", (unsigned long long)event->ssl_ptr);
```

- [ ] **Step 2: 手动验证 JSON 合法**

```bash
sudo ./bpftools/.output/bin/tls_capture --binary-path /home/lzk/.nvm/versions/node/v24.19.0/bin/node -p $(pgrep -f "openclaw/dist/index.js gateway" | head -1) > /tmp/ssl_json.jsonl &
sleep 2
/home/lzk/.nvm/versions/node/v24.19.0/bin/openclaw agent --message "Say only: J"
sleep 4; sudo pkill -f tls_capture
python3 -c "
import json
for i,l in enumerate(open('/tmp/ssl_json.jsonl')):
    l=l.strip()
    if l: json.loads(l)   # 必须全部合法
print('全部 JSON 合法')
"
```

Expected: `全部 JSON 合法`

- [ ] **Step 3: 写 best-effort attach 的失败测试（先失败）**

在 `bpftools/tests/` 下新建 `test_attach_best_effort.sh`：

```bash
#!/bin/bash
# 验证：符号挂载改为 best-effort 后，缺失 SSL_write_ex2 不影响其他符号挂载。
set -u
BIN=/home/lzk/MtAgentLens/bpftools/.output/bin/tls_capture
OUT=$(mktemp)
# 用一个绝不存在的符号名触发"部分失败"路径
sudo "$BIN" --binary-path /home/lzk/.nvm/versions/node/v24.19.0/bin/node 2>&1 | head -40 > "$OUT" &
P=$!; sleep 3; sudo kill -INT $P 2>/dev/null; wait $P 2>/dev/null
if grep -q "attached" "$OUT"; then
  echo "PASS: 部分符号仍成功挂载"
else
  echo "FAIL: 全部挂载被中断"; cat "$OUT"; exit 1
fi
```

- [ ] **Step 4: 运行确认失败**

```bash
bash bpftools/tests/test_attach_best_effort.sh
```

Expected: FAIL（当前 `ATTACH_UPROBE_CHECKED` 遇缺失符号即 `return -errno`，后续符号全部不挂）

- [ ] **Step 5: 实现 best-effort attach**

在 `tls_capture.c` 中，把 `attach_openssl()` 内部的 `ATTACH_UPROBE_CHECKED` / `ATTACH_URETPROBE_CHECKED` 换成**不中断**的版本：新增宏

```c
/* best-effort 挂载：失败只记录，不中断后续符号 */
#define __ATTACH_UPROBE_SOFT(skel, binary_path, sym_name, prog_name, is_retprobe) \
	({                                                                        \
		int __rc = 0;                                                         \
		(skel)->links.prog_name =                                             \
			bpf_program__attach_uprobe_opts((skel)->progs.prog_name,          \
				-1, binary_path, 0, sym_name,                                 \
				&(struct bpf_uprobe_opts){ .retprobe = is_retprobe });        \
		if (!(skel)->links.prog_name) {                                       \
			fprintf(stderr, "警告: 符号 %s 挂载失败，继续其余符号\n", sym_name); \
			__rc = -1;                                                        \
		}                                                                     \
		__rc;                                                                 \
	})
```

并在 `attach_openssl()` 中改为收集这些返回值，最后汇总：

```c
	int failed = 0;
	failed += (__ATTACH_UPROBE_SOFT(skel, lib, SSL_write,    probe_SSL_rw_enter, false)   != 0);
	failed += (__ATTACH_UPROBE_SOFT(skel, lib, SSL_read,     probe_SSL_rw_enter, false)   != 0);
	/* …其余符号同理… */

	/* 追加当前缺失的符号 */
	failed += (__ATTACH_UPROBE_SOFT(skel, lib, SSL_write_ex2, probe_SSL_write_ex_enter, false) != 0);

	if (failed)
		fprintf(stderr, "tls_capture: %d 个 SSL 符号挂载失败（其余符号已生效）\n", failed);
```

> 保持 `attach_openssl` 的返回值语义，并在 `tls_capture.c:687-691` 调用处**不再丢弃返回值**——改为按失败数决定是否降级打印。

- [ ] **Step 6: 运行验证通过**

```bash
cd /home/lzk/MtAgentLens/bpftools && make tls_capture 2>&1 | tail -5
bash tests/test_attach_best_effort.sh
```

Expected: `PASS`

- [ ] **Step 7: Commit**

```bash
git add bpftools/tls_capture/tls_capture.c bpftools/tests/test_attach_best_effort.sh
git commit -m "tls_capture 输出 ssl_ptr，并把符号挂载改为逐符号 best-effort 且失败可见"
```

---

## Task 4: ConnectionBuffer 改按连接分键

**Files:**
- Modify: `collector/src/framework/analyzers/connection_buffer.rs`
- Test: 同文件 `#[cfg(test)]` 模块

**Interfaces:**
- Consumes: SSL 事件 `data["ssl_ptr"]: u64`、`data["tid"]: u64`
- Produces: `ConnectionBuffer` 在并发场景下不再串包；`ssl_ptr` 缺失时回退 `tid`（保持既有行为）

- [ ] **Step 1: 写失败测试**

在 `connection_buffer.rs` 的测试模块中加入：

```rust
#[tokio::test]
async fn concurrent_connections_do_not_mix() {
    let mut cb = ConnectionBuffer::new();
    let stream = futures::stream::iter(vec![
        // 连接 A 的请求头（未结束）
        ssl_event(1, 0xAAA, "POST /a HTTP/1.1\r\nHost: x\r\nContent-Length: 7\r\n\r\n{\"m"),
        // 连接 B 的请求（同一 tid！）
        ssl_event(1, 0xBBB, "POST /b HTTP/1.1\r\nHost: y\r\nContent-Length: 7\r\n\r\n{\"n"),
        // 连接 A 的响应到达 → 应吐出仅含 A 的 body
        ssl_event(1, 0xAAA, "HTTP/1.1 200 OK\r\n\r\n"),
    ]);
    let out: Vec<_> = cb.process(Box::pin(stream)).await.unwrap().collect().await;
    let bodies: Vec<String> = out
        .iter()
        .filter_map(|e| e.data.get("data").and_then(|v| v.as_str()).map(String::from))
        .filter(|d| d.contains("POST"))
        .collect();
    assert_eq!(bodies.len(), 1, "应恰好吐出 1 个合成请求");
    assert!(bodies[0].contains("/a"), "吐出的应是连接 A 的请求");
    assert!(!bodies[0].contains("/b"), "不得混入连接 B 的请求");
}

fn ssl_event(tid: u64, ssl_ptr: u64, data: &str) -> Event {
    Event::new_with_timestamp(
        1,
        "ssl".to_string(),
        42,
        "node".to_string(),
        serde_json::json!({ "tid": tid, "ssl_ptr": ssl_ptr, "data": data }),
    )
}
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /home/lzk/MtAgentLens/collector
cargo test --lib connection_buffer::tests::concurrent_connections_do_not_mix 2>&1 | tail -20
```

Expected: FAIL —— 两个请求被按 tid 累积成一个 buffer，断言 `!contains("/b")` 失败

- [ ] **Step 3: 改分键逻辑**

在 `connection_buffer.rs` 中，把 buffer key 从 `u64`（tid）改为 `ConnKey`，并在读事件时优先用 `ssl_ptr`：

```rust
/// 连接键：优先 SSL* 指针；缺失时回退 tid（保持既有行为）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum ConnKey {
    SslPtr(u64),
    Tid(u64),
}

fn conn_key(data: &serde_json::Value, tid: u64) -> ConnKey {
    match data.get("ssl_ptr").and_then(|v| v.as_u64()) {
        Some(p) if p != 0 => ConnKey::SslPtr(p),
        _ => ConnKey::Tid(tid),
    }
}
```

把 `type SessionBuffers = Arc<Mutex<HashMap<u64, Vec<u8>>>>;` 改为 `HashMap<ConnKey, Vec<u8>>`，`buffers` / `pending` 的类型同步调整（`pending` 保持 `Vec<Event>` 不变）。

在 `process()` 内，把 `let tid = ...` 之后改为：

```rust
                let key = conn_key(&event.data, tid);
```

并把后续所有 `bufs.entry(tid)` / `bufs.remove(&tid)` 改为使用 `key`。**逐处替换，不要遗漏**。

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /home/lzk/MtAgentLens/collector
cargo test --lib connection_buffer 2>&1 | tail -10
```

Expected: PASS（含既有测试）

- [ ] **Step 5: 全量回归**

```bash
cd /home/lzk/MtAgentLens/collector && cargo test 2>&1 | tail -6
cd /home/lzk/MtAgentLens/collector && cargo clippy 2>&1 | tail -6
```

Expected: 全绿 + 无 clippy 告警

- [ ] **Step 6: 真实并发端到端验收（本 Task 的核心验收）**

```bash
cd /tmp && rm -f oc_verify.jsonl
sudo /home/lzk/MtAgentLens/bpftools/.output/bin/tls_capture \
  --binary-path /home/lzk/.nvm/versions/node/v24.19.0/bin/node \
  -p $(pgrep -f "openclaw/dist/index.js gateway" | head -1) > /tmp/oc_verify.jsonl &
sleep 3
for i in 1 2 3; do
  /home/lzk/.nvm/versions/node/v24.19.0/bin/openclaw agent --message "Concurrent #$i. Reply: C$i" >/dev/null 2>&1 &
  /home/lzk/.nvm/versions/node/v24.19.0/bin/openclaw agent --agent probe --message "Concurrent probe #$i. Reply: P$i" >/dev/null 2>&1 &
done
wait; sleep 5; sudo pkill -f tls_capture
```

然后用与设计文档 §5 L0b 相同的模拟脚本（按连接分键版）统计：**全部合成请求事件必须可解析为合法单一 JSON**。

Expected: 合成事件数 ≥ 6，且**合法率 100%**（改前为 4/17）

- [ ] **Step 7: Commit**

```bash
git add collector/src/framework/analyzers/connection_buffer.rs
git commit -m "连接缓冲改为按 SSL 连接分键，修复并发下请求体串包"
```

---

## Task 5: cmdline / cgroup 贯通到 ProcessMetadata

**Files:**
- Modify: `collector/src/responsibility/signals/signal_snapshot.rs:13-32`
- Modify: `collector/src/responsibility/signals/signal_graph.rs:186-193`
- Modify: `collector/src/responsibility/runtime/routing.rs:387-420`
- Create: `collector/src/responsibility/runtime/cgroup.rs`
- Test: 各文件 `#[cfg(test)]`

**Interfaces:**
- Produces:
  - `ProcessExecInfo { comm: String, cmdline: Option<String>, exe_path: Option<String>, cgroup_unit: Option<String> }`
  - `ProcessMetadata` 新增 `pub cmdline: Option<String>`, `pub exe_path: Option<String>`, `pub cgroup_unit: Option<String>`
  - `SignalGraph::observe_exec(&mut self, child: ProcessId, parent: ProcessId, info: ProcessExecInfo, pgid: Option<u32>, ts: u64)`
  - `cgroup::read_unit(pid: u32) -> Option<String>`

- [ ] **Step 1: 写 cgroup 读取的失败测试**

创建 `collector/src/responsibility/runtime/cgroup.rs`：

```rust
//! 用户态 cgroup 读取。
//!
//! eBPF 层并未采集 cgroup（`process.h` 里的 `cgroup_id` 只是声明，
//! `process.bpf.c` 从未填充），因此改由用户态在 EXEC 事件到达时读取
//! `/proc/<pid>/cgroup`，并缓存 `pid → unit`。

use std::collections::HashMap;

/// 从 `/proc/<pid>/cgroup` 内容中解析 systemd unit 名。
///
/// 输入形如 `0::/user.slice/user-1000.slice/user@1000.service/app.slice/openclaw-gateway.service`
/// 输出 `Some("openclaw-gateway.service")`；无法解析时返回 `None`。
pub fn parse_unit(content: &str) -> Option<String> {
    let path = content.lines().next()?.rsplit(':').next()?;
    let last = path.rsplit('/').next()?;
    if last.is_empty() {
        return None;
    }
    Some(last.to_string())
}

/// 读取进程的 cgroup unit（进程已退出时返回 `None`）。
pub fn read_unit(pid: u32) -> Option<String> {
    let content = std::fs::read_to_string(format!("/proc/{pid}/cgroup")).ok()?;
    parse_unit(&content)
}

/// pid → cgroup unit 的缓存。进程退出后 `/proc` 消失，故需在 EXEC 时即读取。
#[derive(Debug, Default)]
pub struct CgroupCache {
    units: HashMap<u32, String>,
}

impl CgroupCache {
    pub fn observe(&mut self, pid: u32) {
        if self.units.contains_key(&pid) {
            return;
        }
        if let Some(u) = read_unit(pid) {
            self.units.insert(pid, u);
        }
    }

    pub fn get(&self, pid: u32) -> Option<&str> {
        self.units.get(&pid).map(String::as_str)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_systemd_service_unit() {
        let c = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/openclaw-gateway.service\n";
        assert_eq!(parse_unit(c).as_deref(), Some("openclaw-gateway.service"));
    }

    #[test]
    fn parses_terminal_scope_unit() {
        let c = "0::/user.slice/user-1000.slice/app.slice/app-org.gnome.Terminal.slice/vte-spawn-abc.scope\n";
        assert_eq!(parse_unit(c).as_deref(), Some("vte-spawn-abc.scope"));
    }

    #[test]
    fn returns_none_for_root_cgroup() {
        assert_eq!(parse_unit("0::/\n"), None);
    }

    #[test]
    fn returns_none_for_empty() {
        assert_eq!(parse_unit(""), None);
    }

    #[test]
    fn caches_and_reuses() {
        let mut c = CgroupCache::default();
        c.observe(1); // pid 1 几乎必然存在
        let first = c.get(1).map(String::from);
        c.observe(1);
        assert_eq!(c.get(1).map(String::from), first);
    }
}
```

在 `collector/src/responsibility/runtime/mod.rs` 中加 `pub mod cgroup;`。

- [ ] **Step 2: 运行确认通过（该模块应立即可测）**

```bash
cd /home/lzk/MtAgentLens/collector
cargo test --lib cgroup 2>&1 | tail -12
```

Expected: 5 个测试通过（纯函数解析，无外部依赖）

- [ ] **Step 3: 写 `observe_exec` 新签名的失败测试**

在 `signal_graph.rs` 测试模块加：

```rust
#[test]
fn exec_records_cmdline_and_cgroup() {
    let mut g = SignalGraph::new();
    let child = pid(100, 1);
    g.observe_exec(
        child,
        pid(50, 0),
        ProcessExecInfo {
            comm: "node".to_string(),
            cmdline: Some("node /opt/openclaw/dist/index.js gateway".to_string()),
            exe_path: Some("/usr/bin/node".to_string()),
            cgroup_unit: Some("openclaw-gateway.service".to_string()),
        },
        None,
        1,
    );
    let snap = g.snapshot();
    let meta = snap.get_process(100, 1).expect("进程应存在");
    assert_eq!(meta.comm, "node");
    assert_eq!(meta.cmdline.as_deref(), Some("node /opt/openclaw/dist/index.js gateway"));
    assert_eq!(meta.cgroup_unit.as_deref(), Some("openclaw-gateway.service"));
}
```

- [ ] **Step 4: 运行确认失败**

```bash
cd /home/lzk/MtAgentLens/collector
cargo test --lib exec_records_cmdline_and_cgroup 2>&1 | tail -20
```

Expected: 编译错误 `cannot find type ProcessExecInfo`

- [ ] **Step 5: 定义 `ProcessExecInfo` 并扩展 `ProcessMetadata`**

在 `signal_snapshot.rs` 中 `ProcessMetadata` 加入三个字段：

```rust
    /// 完整命令行（来自 EXEC 事件的 full_command）。
    pub cmdline: Option<String>,
    /// 可执行文件路径（来自 EXEC 事件的 filename）。
    pub exe_path: Option<String>,
    /// cgroup unit 名（用户态读 /proc/<pid>/cgroup 所得）。
    pub cgroup_unit: Option<String>,
```

在同一文件定义：

```rust
/// EXEC 事件携带的运行时身份信息。
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ProcessExecInfo {
    pub comm: String,
    pub cmdline: Option<String>,
    pub exe_path: Option<String>,
    pub cgroup_unit: Option<String>,
}
```

> 找到 `ProcessMetadata` 的所有构造点（`observe_exec` 内），为三个新字段补 `None` 或实际值。编译器会逐个报错，按提示补全。

- [ ] **Step 6: 改 `observe_exec` 签名**

把 `signal_graph.rs:186-193` 改为：

```rust
    pub fn observe_exec(
        &mut self,
        child: ProcessId,
        parent: ProcessId,
        info: ProcessExecInfo,
        pgid: Option<u32>,
        ts: u64,
    ) {
```

函数体内原 `comm` 的用法改为 `&info.comm`，并把 `info.cmdline` / `info.exe_path` / `info.cgroup_unit` 写入 `ProcessMetadata`。

- [ ] **Step 7: 改 `parse_exec_event` 提取新字段**

把 `routing.rs:387` 的返回类型改为
`Option<(ProcessId, ProcessId, ProcessExecInfo, Option<u32>)>`，并在函数体内追加：

```rust
    let cmdline = event
        .data
        .get("full_command")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let exe_path = event
        .data
        .get("filename")
        .and_then(|v| v.as_str())
        .map(str::to_string);
    let cgroup_unit = crate::responsibility::runtime::cgroup::read_unit(pid);
```

返回时组装 `ProcessExecInfo { comm, cmdline, exe_path, cgroup_unit }`。

> `full_command` / `filename` 已在 EXEC 的 JSON 中（`bpftools/process/process.c`），**无需改 eBPF**。

- [ ] **Step 8: 更新 `routing.rs:86` 调用点**

```rust
                if let Some((child, parent, info, pgid)) = parse_exec_event(event) {
                    signal_graph.observe_exec(child, parent, info, pgid, event.timestamp);
                }
```

- [ ] **Step 9: 修掉所有编译错误（含测试）**

```bash
cd /home/lzk/MtAgentLens/collector && cargo test 2>&1 | grep -E "^error|^warning: unused" | head -30
```

逐个修正：`correlator.rs` 测试里的 `observe_exec(*child, *parent, "test", None, ...)` 等调用需改为传 `ProcessExecInfo { comm: "test".into(), ..Default::default() }`。

- [ ] **Step 10: 全量测试 + clippy**

```bash
cd /home/lzk/MtAgentLens/collector && cargo test 2>&1 | tail -6 && cargo clippy 2>&1 | tail -6
```

Expected: 全绿

- [ ] **Step 11: 真实环境验收**

跑一次 `trace --process=true`，然后查 DB：

```bash
python3 -c "
import sqlite3
con=sqlite3.connect('/tmp/oc-trace/trace.log.responsibility.db')
# 用实际生成的 db 路径
"
```

需确认 Gateway 的 `ProcessMetadata` 中 `cgroup_unit == 'openclaw-gateway.service'` 且 `cmdline` 含 `openclaw/dist/index.js`。

> 若 `process_events` 表未持久化这三列，本步改为在 collector 日志中确认（`--verbose`），字段持久化留待 L1 任务。

- [ ] **Step 12: Commit**

```bash
git add collector/src/responsibility/
git commit -m "为进程身份贯通 cmdline 与 cgroup unit，供运行时身份解析使用"
```

---

## Task 6: `record` 的 `-c` 给默认值

**Files:**
- Modify: `collector/src/main.rs:296-298`
- Test: `collector/src/main.rs` 或 CLI 集成测试

**Interfaces:**
- Produces: `mtagentlens record` 不带 `-c` 时不再报错，默认 `claude`

- [ ] **Step 1: 写失败测试**

在 `main.rs` 的测试模块加：

```rust
#[test]
fn record_comm_defaults_to_claude() {
    use clap::Parser;
    let cli = Cli::try_parse_from(["mtagentlens", "record"]).expect("record 应可无 -c 解析");
    match cli.command {
        Commands::Record { comm, .. } => assert_eq!(comm, "claude"),
        _ => panic!("应为 Record"),
    }
}
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /home/lzk/MtAgentLens/collector
cargo test --lib record_comm_defaults_to_claude 2>&1 | tail -15
```

Expected: FAIL —— `-c` 缺失导致解析错误

- [ ] **Step 3: 加默认值**

把 `main.rs:296-298` 改为：

```rust
        /// Process command filter
        #[arg(short = 'c', long, default_value = "claude")]
        comm: String,
```

- [ ] **Step 4: 运行确认通过 + 全量回归**

```bash
cd /home/lzk/MtAgentLens/collector && cargo test 2>&1 | tail -6
```

Expected: 全绿

- [ ] **Step 5: Commit**

```bash
git add collector/src/main.rs
git commit -m "record 子命令的进程过滤提供默认值，修正注释与实现不符"
```

---

## 验收总表（P0 完成判据）

| 判据 | 命令 | 期望 |
|------|------|------|
| ssl_ptr 被采集 | Task 2 Step 6 | 每个 SSL 事件含非零 `ssl_ptr` |
| JSON 合法 | Task 3 Step 2 | 100% 可 `json.loads` |
| 符号挂载不中断 | Task 3 Step 4 | 缺失符号时仅告警，其余生效 |
| **并发不串包** | Task 4 Step 6 | 合成请求事件合法率 **100%**（改前 4/17） |
| cgroup/cmdline 可得 | Task 5 Step 11 | Gateway 的 `cgroup_unit == openclaw-gateway.service` |
| 无回归 | 各 Task | `cargo test` 全绿、clippy 无告警 |
| Claude Code 冻结 | Task 4 Step 6 | 用 `-c claude` 抓 Claude Code 行为与改前一致 |

---

## 风险与回退

| 风险 | 回退 |
|------|------|
| eBPF 加 map 触发 verifier 失败 | Task 2 Step 5 会立即暴露；回退为不加 map，改用 `(pid, fd)` 组合（需另开任务） |
| `ssl_ptr` 在某些 OpenSSL 版本非首参 | Task 2 Step 6 的断言会捕获（全 0）；届时改用 `PT_REGS_PARM1` 显式读取 |
| `ConnectionBuffer` 分键改动影响 Claude Code | `ssl_ptr` 缺失时回退 `tid`，行为与改前完全一致；Task 4 Step 6 做对照 |
| `observe_exec` 签名变更波及面大 | 编译器穷尽报错，逐个修正；`ProcessExecInfo` 实现 `Default` 便于测试构造 |

---

## Self-Review 记录

- **Spec 覆盖**：本文覆盖 spec §5 L0a（Task 3/5/6）、§5 L0b（Task 1/2/3/4）。L1/L2/L3/L4 不在 P0 范围。
- **占位符扫描**：无 TBD/TODO；每个代码步骤含可粘贴代码。
- **类型一致性**：`ProcessExecInfo`（Task 5 Step 5 定义）与 `observe_exec` 签名（Step 6）、`parse_exec_event` 返回（Step 7）、调用点（Step 8）一致；`ConnKey`（Task 4 Step 3）在测试（Step 1）与实现中同名。
- **已知缺口**：Task 5 Step 11 的 DB 持久化验证依赖 `process_events` 表是否含新列——该表结构未在本次勘察中确认，故给出日志回退方案，字段持久化顺延至 L1 任务。
