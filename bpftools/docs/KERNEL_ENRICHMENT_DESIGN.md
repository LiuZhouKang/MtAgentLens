# 内核侧事件增强设计文档

> 状态：草案  
> 日期：2026-06-03  
> 目标：为 AgentSight 因果追溯系统设计完备的内核侧事件增强方案

---

## 1. 当前状态分析

### 1.1 现有架构

```
eBPF tracepoint/kprobe handlers
  ├── 旧 handlers (exec, exit, open, openat, bash_readline)
  │     → ring buffer (event struct with enrichment fields)
  │     → 通过 enable_enrichment flag 控制
  │
  └── 新 modules (bpf_fs, bpf_write, bpf_net, bpf_signals, bpf_mem, bpf_cow)
        → event_agg_map (聚合 map, 无 enrichment 字段)
        → 仅聚合 count/bytes, 不产生实时事件
```

### 1.2 已实现的增强字段

在 `process.h` 的 `struct event` 中已有：

| 字段 | 来源 | 用途 | 状态 |
|------|------|------|------|
| `cgroup_id` | `bpf_get_current_cgroup_id()` | 容器/team 边界 | ✅ 已实现 |
| `uid` / `gid` | `bpf_get_current_uid_gid()` | 用户身份 | ✅ 已实现 |
| `seq_number` | `seq_counter_map` | 系统调用序列号 | ✅ 已实现 |
| `parent_uid` | `capture_process_hierarchy()` | 父进程 UID | ✅ 已实现 |
| `parent_cgroup_id` | `capture_process_hierarchy()` | 父进程 cgroup | ✅ 已实现 |
| `start_time_ns` | `task_struct.start_time` | 防止 PID 重用 | ✅ 已实现 |
| `parent_start_time_ns` | `real_parent.start_time` | 进程亲缘关系 | ✅ 已实现 |
| `pgid` | `group_leader.pid` | 进程组统一 | ✅ 已实现 |

### 1.3 识别到的 Gap

| Gap | 严重程度 | 说明 |
|-----|---------|------|
| **G1: 新模块无 enrichment** | 高 | `bpf_fs/network/signals/mem/cow` 模块只做聚合，不产生带 enriched 数据的实时事件 |
| **G2: 重复 enrichment 头文件** | 中 | `bpftools/enrichment.h` 和 `process_ext/enrichment.h` 定义重叠但不同 — 只用前者 |
| **G3: sock 上下文未接入** | 中 | `capture_socket_context()` 已定义但从未在 handler 中调用 |
| **G4: 无 fd-inode 映射** | 高 | 无法将 fd 操作与具体文件关联（导致后续用户空间解析昂贵） |
| **G5: 无 namespace 信息** | 中 | 无 PID namespace、mount namespace 信息 — 容器内 PID 冲突风险 |
| **G6: 无 socket-to-fd 关联** | 中 | `bind/connect` 返回的 fd 未记录，后续网络操作无法知道操作哪个 socket |
| **G7: enriched_event struct 未使用** | 低 | 定义了但从未接入 ring buffer — 死代码 |
| **G8: 逐守护 enrichment 无选择性** | 低 | `enable_enrichment` 全有全无 — 无法选择性开启进程层级而不开启 socket 追踪 |

---

## 2. Enrichment 的目标和原则

### 2.1 目标

1. **因果链完整性** — 每条事件都应该携带足够的上下文信息来回答"谁干的、怎么干的、在什么环境干的"
2. **零解析开销** — 内核侧尽可能地解析和关联，减少用户空间昂贵的 /proc 查询
3. **向后兼容** — 现有的 `struct event` 布局必须保持不变，新字段只能追加到尾部
4. **选择性开销** — 每种增强类型可独立开关，按需收集避免不必要的 BPF verifier 复杂度和运行时开销

### 2.2 设计原则

- **在产生事件的点做增强** — 不要让后续 analyzer 去 /proc 查父进程，内核里就有
- **用 BPF map 做缓存** — 对频繁查询的信息（进程上下文、fd→inode 映射）用 per-CPU 或 hash map 缓存
- **ring buffer 事件自包含** — 用户空间收到事件后不需要再做额外的内核查询
- **增强字段紧凑** — 不盲目增加字段大小，避免 ring buffer 膨胀

---

## 3. Enrichment 维度设计

### 3.1 维度一：进程身份（Process Identity）

每个事件的"发出者"元数据。

```
进程身份上下文:
  - pid + start_time_ns   ← 已有的唯一进程标识
  - comm                   ← 进程名（16 字节）
  - uid / gid              ← 已有
  - cgroup_id              ← 已有
  - [NEW] pid_namespace_id ← PID namespace（容器识别）
  - [NEW] mnt_namespace_id ← mount namespace（文件路径解析上下文）
```

**实现方式：**
- `pid_namespace_id` — 从 `task->nsproxy->pid_ns_for_children->ns.inum` 读取
- `mnt_namespace_id` — 从 `task->nsproxy->mnt_ns->ns.inum` 读取
- 这两个字段在 exec 时缓存到 `proc_context_map`，后续事件从 map 读取

### 3.2 维度二：进程亲缘（Process Lineage）

进程树的父子关系，关键的 causal 链接。

```
进程亲缘:
  - ppid + parent_start_time_ns  ← 已有
  - pgid (进程组)                 ← 已有
  - parent_uid                   ← 已有
  - parent_cgroup_id             ← 已有
  - [NEW] grandparent_pid        ← 祖父进程（判断进程树深度）
  - [NEW] is_direct_child        ← 是否直接 fork 自 parent
```

**为什么需要祖父进程 PID？**
- 在 agent 场景中常见：`claude` → `bash` → `tool-process`，只知道 parent 是 bash 无法追溯到原始 agent
- 简化因果追溯链：用户空间可以直接获得 3 代关系而无需维护进程树

### 3.3 维度三：文件描述符解析（FD Resolution）

在文件操作时解析 fd → 实际文件路径。

```
fd 上下文:
  - [NEW] resolved_path[MAX_FILENAME_LEN] ← 文件实际路径（从 dentry 解析）
  - [NEW] inode                          ← 文件 inode（持久标识）
  - [NEW] dev                            ← 设备号（区分不同文件系统）
  - fd / flags / is_open                 ← 已有（部分）
```

**实现方式（两阶段）：**

**阶段 1 — fd→inode 映射表：**
- 在 `sys_enter_openat/close` 时维护 `fd_map: (pid, fd) → (inode, dev)`
- 在 `write/read` 等通过 fd 操作的事件中从 `fd_map` 查询 inode

**阶段 2 — inode→path 缓存：**
- 在 open 时尝试从 `dentry` 路径栈解析文件路径（`bpf_d_path` 需要 kernel 5.10+，使用 `dentry->d_name` + 父节点回溯代替）
- 对常规文件：最多回溯 8 层目录名
- 对于匿名文件（pipe、socket）：标记为特殊 inode

**这个 fd 解析方案的关键约束：**
- `bpf_d_path` 需要 `BPF_F_KPROBE_MULTI_RETURN` 和足够高的内核版本，兼容性差
- 替代方案：只记录 `(inode, dev)`，由用户空间通过 `/proc/pid/fd/` 或 inode 反查路径
- **推荐方案**：内核侧记录 `(inode, dev)`，用户空间查找路径（因为：
  - dentry 回溯在高并发下有 RCU 安全问题
  - BPF verifier 对深度循环限制严格
  - `/proc/pid/fd/` 查询成本远低于完全的 /proc 遍历）

### 3.4 维度四：Socket 关联（Socket Correlation）

网络操作的套接字属性和关联。

```
socket 上下文:
  - saddr / daddr / sport / dport  ← 已有 socket_context struct
  - [NEW] socket_inode             ← socket inode（关联到进程 fd）
  - [NEW] socket_family            ← AF_INET/AF_INET6/AF_UNIX
  - [NEW] protocol                 ← TCP/UDP/UNIX
```

**实现方式：**
- 在 `sys_enter_bind/connect` 时，除了聚合外还产生 ring buffer 事件（带 sockaddr 解析）
- 在 `capture_socket_context()` 中增加 inode 获取：`sock->sk_socket->file->f_inode`
- 新增 tracepoint: `sys_exit_accept/accept4` — 记录新的 client socket inode

### 3.5 维度五：系统调用链（Syscall Sequencing）

每个进程的系统调用序列号，用于 temporal ordering。

```
序列信息:
  - seq_number        ← 已有（per-process counter）
  - [NEW] chain_depth ← 从 parent 继承的调用深度
```

**chain_depth 用途举例：**
- 如果 process A 调用 tool B，B 做了 syscall → B 的 chain_depth = A 的 chain_depth + 1
- 可以快速判断调用嵌套层级，便于时序分析

### 3.6 维度六：资源上下文（Resource Context）

与事件关联的资源（文件、socket、内存映射等）的持久标识。

```
资源标识:
  - [NEW] resource_type    ← FILE/INODE/SOCKET/PIPE/MEMORY
  - [NEW] resource_id_hi   ← 高阶标识（inode, socket inode 等）
  - [NEW] resource_id_lo   ← 低阶标识（dev, protocol 等）
```

**设计意图：**
- 提供一个统一的双 64 位资源标识字段，覆盖所有资源类型
- 用户空间可以按 resource_id 聚合，不需解析每种事件类型的特定字段
- 为后续的 taint tracking 提供统一接口

---

## 4. 推荐的事件结构演进

### 4.1 当前 struct event（基类保持不变）

```c
struct event {
    enum event_type type;          // 不变
    int pid;                        // 不变
    int ppid;                       // 不变
    unsigned exit_code;             // 不变
    unsigned long long duration_ns; // 不变
    unsigned long long timestamp_ns;// 不变
    unsigned long long start_time_ns;         // 不变
    unsigned long long parent_start_time_ns;  // 不变
    int pgid;                       // 不变
    char comm[TASK_COMM_LEN];       // 不变
    char full_command[MAX_COMMAND_LEN]; // 不变

    // Enrichment fields (existing, keep as-is)
    unsigned long long cgroup_id;
    unsigned int uid;
    unsigned int gid;
    unsigned long long seq_number;
    unsigned int parent_uid;
    unsigned long long parent_cgroup_id;

    union {                         // 不变
        char filename[MAX_FILENAME_LEN];
        char command[MAX_COMMAND_LEN];
        struct { ... } file_op;
    };
    bool exit_event;                // 不变
};
```

### 4.2 新增字段（追加在 struct 尾部）

```c
    // ─── v2 Enrichment: namespace + extended lineage ───
    unsigned int pid_namespace_id;      // PID namespace inode number
    unsigned int mnt_namespace_id;      // Mount namespace inode number
    unsigned int grandparent_pid;       // GPID for quick 3-gen trace
    unsigned char is_direct_child;      // 1 = direct fork, 0 = inherited session

    // ─── v2 Enrichment: fd resolution ───
    unsigned long long file_inode;      // inode of the file being operated on
    unsigned long long file_dev;        // device ID of the filesystem

    // ─── v2 Enrichment: socket identity ───
    unsigned long long socket_inode;    // socket inode for correlation
    unsigned short socket_family;       // AF_INET/AF_INET6/AF_UNIX
    unsigned short socket_protocol;     // IPPROTO_TCP/UDP/...
    unsigned char socket_type;          // SOCK_STREAM/SOCK_DGRAM

    // ─── v2 Enrichment: unified resource identifier ───
    unsigned char resource_type;        // RESOURCE_FILE/SOCKET/PIPE/MEMORY
    unsigned long long resource_id_hi;  // inode or equivalent
    unsigned long long resource_id_lo;  // dev or protocol
```

---

## 5. 新模块（fs/net/signal/mem/cow）的 Ring Buffer 输出

### 5.1 当前问题

当前 bpf_fs.h, bpf_net.h, bpf_signals.h, bpf_mem.h, bpf_cow.h 只做 aggregation 到 `event_agg_map`，**不产生 ring buffer 事件**。这导致：
- 时间线中缺失这些事件
- 无法在事件级别做 enrichment
- 聚合数据缺少进程上下文（cgroup, uid 等）

### 5.2 解决方案：双输出模式

每个 handler 在聚合的同时，可选地产生 ring buffer 事件：

```c
// 模式控制 flag
const volatile bool enable_ringbuf_fs    = false;  // 产生 ring buf 文件事件
const volatile bool enable_ringbuf_net   = false;  // 产生 ring buf 网络事件
const volatile bool enable_ringbuf_signal = false; // 产生 ring buf 信号事件
const volatile bool enable_ringbuf_mem   = false;  // 产生 ring buf 内存事件
const volatile bool enable_ringbuf_cow   = false;  // 产生 ring buf COW 事件
```

**优点**：
- 低开销场景（仅聚合）和高精度场景（聚合+实时事件）都支持
- 每个 flag 独立控制，按需开销
- 与现有 `enable_enrichment` 正交

### 5.3 事件模板（新模块）

新模块产生的事件复用 `struct event`，按 `event_type` 区分子类型：

```
event.type = EVENT_TYPE_FILE_DELETE (10)  → file_op filled
event.type = EVENT_TYPE_FILE_RENAME (11)  → file_op filled  
event.type = EVENT_TYPE_DIR_CREATE (12)   → file_op filled
event.type = EVENT_TYPE_NET_CONNECT (22)  → enrichment fields with socket info
event.type = EVENT_TYPE_SIGNAL_SEND (32)  → command[] has "sig=N,target=PID"
event.type = EVENT_TYPE_COW_FAULT (41)    → enrichment field with mem info
```

---

## 6. 实施优先级

### P0 — 立即实施（核心因果依赖）

1. **G4: fd→inode 映射** — 建立 `fd_map`，在 `openat/open/close` 时更新，在 `write/read` 时查询
2. **G5: namespace 信息** — 在 exec handler 中捕获 PID 和 mount namespace
3. **G1: 新模块 ring buffer 输出** — 至少 `bpf_net.h` 和 `bpf_fs.h` 产生实时事件

### P1 — 短期实施（完善 causal 链）

4. **G3: sock 上下文接入** — 在 `trace_connect` handler 中调用 `capture_socket_context`
5. **统一 enrichment 头文件** — 移除重复，选定一个 `enrichment.h`
6. **事件 v2 字段追加** — 在 `struct event` 尾部追加 v2 字段

### P2 — 中期实施（高级追溯）

7. **G8: 逐特性开关** — 拆分 `enable_enrichment` 为 bitfield
8. **chain_depth 和 grandparent_pid** — 进程树深度信息
9. **unified resource identifier** — 统一资源标识

### P3 — 长期优化

10. **BPF iterator 批量导出** — 对于聚合数据，使用 BPF iterator 而非每次产生 ring buf 事件
11. **taint tracking 内核层** — 在 eBPF 中追踪 fd 继承关系

---

## 7. 关键实现细节

### 7.1 fd_map 设计

```c
struct fd_key {
    u32 pid;
    u64 start_time_ns;  // 预防 PID 重用
    u32 fd;
} __attribute__((packed));

struct fd_val {
    u64 inode;
    u64 dev;
    u32 flags;
    u8 fd_type;  // REG/CHAR/DIR/SOCKET/PIPE
};
```

- Map 类型：`BPF_MAP_TYPE_LRU_HASH`（自动驱逐最久未用，防止 map 膨胀）
- Max entries：8192
- 更新点：`sys_enter_openat`, `sys_enter_open`, `sys_enter_socket`, `sys_enter_close`
- 查询点：`sys_enter_write`, `sys_enter_read`, `sys_enter_sendto`, `sys_enter_recvfrom` 等

### 7.2 proc_context_map 扩展

当前 `proc_context_map` (在 `enrichment.bpf.c` 中) 仅存储 `process_context`。需要扩展到包含 namespace 信息：

```c
struct process_context {
    u32 ppid;
    u64 parent_start_time_ns;
    u64 cgroup_id;
    u32 uid;
    u32 gid;
    u64 seq_counter;
    char argv0[64];
    // v2 扩展
    u32 pid_namespace_id;
    u32 mnt_namespace_id;
    u32 grandparent_pid;
};
```

### 7.3 用户空间对应变更

Rust collector 侧无需改动 `process.rs` runner（它只是透传 JSON），但后续 analyzer 需要：
- `TimestampNormalizer` 或新 analyzer 中解析 `file_inode`/`file_dev` 字段
- `FileLogger` 或 persistence 中写入 v2 字段
- 可选：新增 `FdResolver` analyzer 做 inode→path 解析（通过 /proc 查询）

---

## 8. 验证策略

### 8.1 BPF 验证器

- 每次新增 `bpf_probe_read` 或 map lookup 后必须在目标内核版本上验证
- 新增的 dentry 回溯逻辑需要特别注意 verifier 复杂度（循环深度限制）

### 8.2 集成测试

```
1. 单进程测试：启动已知进程 → 确认 exec/exit event 包含所有 enrichment 字段
2. 文件操作测试：open + write + close → 确认 fd_map 正确解析 inode
3. 容器测试：docker run → 确认 namespace_id 与宿主机不同
4. 网络测试：curl example.com → 确认 connect event 包含 socket 信息
5. 聚合+实时双模式测试：同时开启 aggregation 和 ringbuf → 确认事件不丢失
```

### 8.3 性能回归

- ring buffer 事件大小增加 ≈ 64 字节，在 100k eps 下额外带宽为 6.4 MB/s（可接受）
- fd_map LRU lookup 为 O(1)，每次写入额外开销 < 100ns
- namespace 读取仅在 exec 时执行一次，开销忽略不计

---

## 附录 A: 文件清理计划

以下文件建议合并/移除：

| 文件 | 行动 | 原因 |
|------|------|------|
| `bpftools/enrichment.h` | 保留并扩展 | 主 enrichment 定义 |
| `bpftools/process_ext/enrichment.h` | 移除 | 与上面重复 |
| `bpftools/process_ext/enrichment.bpf.c` | 移除 | 逻辑已内联到 process.bpf.c |
| `bpftools/process_ext/enrichment_helpers.h` | 保留并扩展 | 工具函数 |

## 附录 B: 相关 Rust 模块映射

| BPF 增强字段 | Rust 消费位置 |
|-------------|--------------|
| `cgroup_id` | `responsibility_runtime`, `identity_resolver` |
| `uid`/`gid` | `policy_evaluator` (capability check) |
| `seq_number` | 时序分析（尚未实现） |
| `file_inode`/`file_dev` | `file_access_audit` (inode→path 解析) |
| `socket_inode` | `network_correlator` (尚未实现) |
| `pid_namespace_id` | `identity_resolver` (容器边界) |
